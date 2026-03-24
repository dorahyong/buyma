# -*- coding: utf-8 -*-
"""
카시나(kasina.co.kr) 상품 수집 스크립트
- 리스트: Shopby API로 productNo + 기본 데이터 수집
- 상세: Shopby API로 category_path 추가
- 옵션: Shopby API로 사이즈/재고 추가
- raw_scraped_data 테이블에 source_site='kasina'로 저장
"""

import os
import json
import time
import random
import argparse
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ===========================================
# 환경 설정
# ===========================================

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL', f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4")
engine = create_engine(DATABASE_URL, echo=False)

# ===========================================
# 카시나 API 설정
# ===========================================

# 리스트/상세 API (shop-api-secondary)
KASINA_LIST_API_BASE = "https://shop-api-secondary.shopby.co.kr"
# 옵션 API (shop-api.e-ncp.com)
KASINA_OPTIONS_API_BASE = "https://shop-api.e-ncp.com"

KASINA_API_HEADERS = {
    'clientId': '183SVEgDg5nHbILW//3jvg==',
    'company': 'Kasina/Request',
    'platform': 'PC',
    'content-type': 'application/json',
    'version': '1.0',
    'Accept': '*/*',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Origin': 'https://www.kasina.co.kr',
    'Referer': 'https://www.kasina.co.kr/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
}

SOURCE_SITE = 'kasina'
PAGE_SIZE = 100
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.3
EXCLUDE_CATEGORY_NOS = 1095823  # 카시나 전역 appConfig 설정


# ===========================================
# API 호출
# ===========================================

def api_get(base_url: str, endpoint: str, params: dict = None) -> Optional[dict]:
    """카시나 Shopby API GET 요청"""
    url = f"{base_url}{endpoint}"
    try:
        resp = requests.get(url, headers=KASINA_API_HEADERS, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            logger.debug(f"404: {endpoint}")
            return None
        else:
            logger.warning(f"API 오류 {resp.status_code}: {endpoint}")
            return None
    except requests.exceptions.Timeout:
        logger.warning(f"API 타임아웃: {endpoint}")
        return None
    except Exception as e:
        logger.error(f"API 요청 실패: {endpoint} - {e}")
        return None


# ===========================================
# 1) 리스트 API — productNo + 기본 데이터 수집
# ===========================================

def get_brand_no_from_db(brand_name: str) -> Optional[str]:
    """mall_brands에서 kasina 브랜드의 mall_brand_no 조회"""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT mall_brand_no FROM mall_brands WHERE mall_name = 'kasina' AND UPPER(mall_brand_name_en) = :brand AND is_active = 1"
        ), {'brand': brand_name.upper()}).fetchone()
        return result[0] if result else None


def get_product_list_from_api(brand_name: str, limit: int = None) -> List[dict]:
    """리스트 API로 전체 상품 수집 (페이지네이션)"""
    brand_no = get_brand_no_from_db(brand_name)
    if not brand_no:
        logger.error(f"브랜드 mall_brand_no 없음: {brand_name}")
        return []

    all_items = []
    page = 1

    while True:
        params = {
            'brandNos': brand_no,
            'excludeCategoryNos': EXCLUDE_CATEGORY_NOS,
            'pageSize': PAGE_SIZE,
            'pageNumber': page,
            'hasTotalCount': 'true',
            'filter.saleStatus': 'ALL_CONDITIONS',
            'order.by': 'MD_RECOMMEND',
            'order.direction': 'ASC',
            'order.soldoutPlaceEnd': 'true',
            'filter.soldout': 'true',
        }

        data = api_get(KASINA_LIST_API_BASE, '/products/search', params)
        if not data:
            logger.warning(f"  {page}페이지 조회 실패, 중단")
            break

        items = data.get('items', [])
        if not items:
            break

        if page == 1:
            total_count = data.get('totalCount', 0)
            page_count = data.get('pageCount', 0)
            logger.info(f"총 상품 수: {total_count}개, {page_count}페이지")

        all_items.extend(items)
        logger.info(f"  {page}페이지 수집: {len(items)}개 (누적: {len(all_items)}개)")

        if limit and len(all_items) >= limit:
            all_items = all_items[:limit]
            break

        if page >= data.get('pageCount', 1):
            break

        page += 1
        time.sleep(random.uniform(0.3, 0.8))

    return all_items


# ===========================================
# skip-existing — DB에서 등록 완료 상품 조회
# ===========================================

def get_published_product_ids(brand_name: str = None) -> set:
    """등록 완료된 상품의 mall_product_id 목록 조회"""
    with engine.connect() as conn:
        query = """
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = 'kasina'
            AND a.is_published = 1
        """
        if brand_name:
            query += " AND (UPPER(r.brand_name_en) = :brand OR UPPER(r.brand_name_kr) = :brand)"
            result = conn.execute(text(query), {"brand": brand_name.upper()})
        else:
            result = conn.execute(text(query))
        return {str(r[0]) for r in result}


# ===========================================
# 2) 상세 API — category_path 등 추가 수집
# 3) 옵션 API — 사이즈/재고 추가 수집
# ===========================================

def fetch_product_detail(product_no: int) -> Optional[dict]:
    """상품 상세 API 호출"""
    return api_get(KASINA_LIST_API_BASE, f'/products/{product_no}', {'useCache': 'false'})


def fetch_product_options(product_no: int) -> Optional[dict]:
    """상품 옵션(사이즈/색상) API 호출"""
    return api_get(KASINA_OPTIONS_API_BASE, f'/products/{product_no}/options', {'useCache': 'false'})


# ===========================================
# 데이터 변환
# ===========================================

def extract_category_path(detail: dict) -> str:
    """상세 데이터에서 첫 번째 카테고리 경로 추출"""
    categories = detail.get('categories', [])
    if categories:
        return categories[0].get('fullCategoryLabel', '').replace('>', ' > ')
    return ''


def parse_options(options_data: dict) -> List[dict]:
    """옵션 데이터에서 색상/사이즈/재고 파싱"""
    flat_options = options_data.get('flatOptions', [])
    parsed = []
    for opt in flat_options:
        value = opt.get('value', '')
        parts = value.split('|')
        if len(parts) >= 2:
            color = parts[0].strip()
            size = parts[1].strip()
        else:
            # '|' 구분자 없음 = 사이즈만 있는 상품
            color = ''
            size = parts[0].strip()
        parsed.append({
            'color': color,
            'tag_size': size,
            'option_code': opt.get('optionManagementCd', ''),
            'stock_count': opt.get('stockCnt', 0),
            'status': 'in_stock' if opt.get('saleType') != 'SOLDOUT' else 'out_of_stock',
            'buy_price': opt.get('buyPrice', 0),
        })
    return parsed


def extract_measurements(detail: dict) -> Dict[str, Dict]:
    """
    상세 API의 contentFooter에서 실측 사이즈 파싱

    contentFooter 형식: "가슴단면,소매길이,총 기장|85,48,69,78|90,50,71,80|..."
    → 파이프(|)로 행 분리, 쉼표(,)로 열 분리
    → 첫 행: 측정 키 (헤더), 이후 행: 첫 값=사이즈명, 나머지=측정값

    Returns:
        {"85": {"가슴단면": "48cm", "소매길이": "69cm", "총 기장": "78cm"}, ...}
    """
    measurements = {}

    base_info = detail.get('baseInfo', {}) if detail else {}
    content_footer = base_info.get('contentFooter', '')

    if not content_footer or '|' not in content_footer:
        return measurements

    segments = content_footer.split('|')
    if len(segments) < 2:
        return measurements

    # 첫 번째 세그먼트: 측정 키 (헤더)
    headers = [h.strip() for h in segments[0].split(',') if h.strip()]
    if not headers:
        return measurements

    # 이후 세그먼트: 사이즈별 측정값
    for segment in segments[1:]:
        values = [v.strip() for v in segment.split(',')]
        if len(values) < 2:
            continue

        size_name = values[0]  # 첫 번째 값 = 사이즈명
        size_data = {}

        for i, header in enumerate(headers):
            value_idx = i + 1  # 사이즈명 다음부터
            if value_idx < len(values) and values[value_idx]:
                size_data[header] = f"{values[value_idx]}cm"

        if size_data:
            measurements[size_name] = size_data

    return measurements


def convert_to_raw_data(item: dict, detail: dict, options_data: dict) -> Optional[dict]:
    """리스트 아이템 + 상세 + 옵션을 raw_scraped_data 형식으로 변환"""
    product_no = str(item.get('productNo', ''))
    model_id = item.get('productManagementCd', '') or ''

    # model_id 없으면 스킵
    if not model_id:
        return None

    # 가격 계산
    sale_price = item.get('salePrice', 0) or 0
    discount_amt = item.get('immediateDiscountAmt', 0) or 0
    raw_price = sale_price - discount_amt

    # 재고 상태
    sale_status = item.get('saleStatusType', '')
    stock_status = 'in_stock' if sale_status == 'ONSALE' else 'out_of_stock'

    # 카테고리 (상세에서)
    category_path = ''
    gender_prefix = ''
    if detail:
        category_path = extract_category_path(detail)
        # 성별정보로 gender prefix 결정 (공용/빈값 → MEN, women → WOMEN)
        base_info = detail.get('baseInfo', {})
        for prop in base_info.get('customPropertise', []):
            if prop.get('propName') == '성별정보':
                gender_value = prop.get('propValue', '').strip().lower()
                if gender_value in ('women', '여자'):
                    gender_prefix = 'WOMEN'
                else:
                    gender_prefix = 'MEN'
                break
        if not gender_prefix:
            gender_prefix = 'MEN'
        if category_path:
            category_path = f"{gender_prefix} > {category_path}"

    # 옵션 파싱
    options = []
    if options_data:
        options = parse_options(options_data)
        # 옵션에 color가 없으면 상품레벨 color 적용 (okmall과 동일 구조)
        product_color = item.get('hsCode', '') or 'Free'
        if options:
            for opt in options:
                if not opt['color']:
                    opt['color'] = product_color
            # 옵션 기준 재고 보정: 하나라도 in_stock이면 in_stock
            stock_status = 'in_stock' if any(o['status'] == 'in_stock' for o in options) else 'out_of_stock'

    # raw_json_data 구성
    raw_json = {
        'color': item.get('hsCode', ''),
        'images': item.get('imageUrls', []),
        'list_images': item.get('listImageUrls', []),
        'options': options,
        'kasina_brand_no': item.get('brandNo', ''),
        'register_date': item.get('registerYmdt', ''),
        'sale_start': item.get('saleStartYmdt', ''),
        'sale_end': item.get('saleEndYmdt', ''),
        'like_count': item.get('likeCount', 0),
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }
    if detail:
        base_info = detail.get('baseInfo', {})
        raw_json['duty_info'] = base_info.get('dutyInfo', '')
        raw_json['categories'] = detail.get('categories', [])
        raw_json['gender'] = ''
        for prop in base_info.get('customPropertise', []):
            if prop.get('propName') == '성별정보':
                raw_json['gender'] = prop.get('propValue', '')

        # contentFooter에서 실측 사이즈 파싱 (okmall의 measurements와 동일 형식)
        measurements = extract_measurements(detail)
        if measurements:
            raw_json['measurements'] = measurements

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': item.get('brandName', ''),
        'brand_name_kr': item.get('brandNameKo', ''),
        'product_name': item.get('productName', ''),
        'p_name_full': item.get('productNameEn', '') or item.get('productName', ''),
        'model_id': model_id,
        'category_path': category_path,
        'original_price': sale_price,
        'raw_price': raw_price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': f"https://www.kasina.co.kr/product-detail/{product_no}",
    }


# ===========================================
# 4) DB 저장
# ===========================================

def save_to_database(data_list: List[Dict]):
    """raw_scraped_data에 저장 (ON DUPLICATE KEY UPDATE)"""
    if not data_list:
        return
    insert_sql = text("""
        INSERT INTO raw_scraped_data
        (source_site, mall_product_id, brand_name_en, brand_name_kr,
         product_name, p_name_full, model_id, category_path,
         original_price, raw_price, stock_status, raw_json_data, product_url)
        VALUES
        (:source_site, :mall_product_id, :brand_name_en, :brand_name_kr,
         :product_name, :p_name_full, :model_id, :category_path,
         :original_price, :raw_price, :stock_status, :raw_json_data, :product_url)
        ON DUPLICATE KEY UPDATE
        brand_name_en = VALUES(brand_name_en),
        brand_name_kr = VALUES(brand_name_kr),
        product_name = VALUES(product_name),
        p_name_full = VALUES(p_name_full),
        model_id = VALUES(model_id),
        category_path = VALUES(category_path),
        original_price = VALUES(original_price),
        raw_price = VALUES(raw_price),
        stock_status = VALUES(stock_status),
        raw_json_data = VALUES(raw_json_data),
        product_url = VALUES(product_url),
        updated_at = NOW()
    """)
    with engine.connect() as conn:
        for data in data_list:
            conn.execute(insert_sql, data)
        conn.commit()


# ===========================================
# 메인 실행
# ===========================================

def get_brands_from_database(brand_filter: str = None):
    """mall_brands 테이블에서 kasina 브랜드 목록 조회"""
    with engine.connect() as conn:
        query = "SELECT mall_brand_name_en, mall_brand_url FROM mall_brands WHERE mall_name = 'kasina' AND is_active = 1"
        params = {}
        if brand_filter:
            query += " AND UPPER(mall_brand_name_en) = :brand"
            params['brand'] = brand_filter.upper()
        result = conn.execute(text(query), params)
        return [{'name': r[0], 'url': r[1]} for r in result]


def main():
    parser = argparse.ArgumentParser(description='카시나 상품 수집기')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품만 스킵 (신규+미등록 상품 수집)')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"카시나 수집 시작: {args.brand or '전체'} (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    if args.skip_existing:
        logger.info("  신규+미등록 상품 수집 모드 (--skip-existing)")
    logger.info("=" * 60)

    # 1) DB에서 브랜드 목록 조회
    brands = get_brands_from_database(args.brand)
    logger.info(f"대상 브랜드: {len(brands)}개")

    total_brands = len(brands)
    for brand_idx, brand in enumerate(brands, 1):
        brand_name = brand['name']
        logger.info(f"\n>>> [{brand_idx}/{total_brands}] 브랜드: {brand_name} ({brand['url']})")

        # 리스트 API로 상품 수집
        items = get_product_list_from_api(brand_name, limit=args.limit)
        logger.info(f"리스트 수집 완료: {len(items)}개")

        if not items:
            continue

        # skip-existing: 등록 완료 상품 필터링
        if args.skip_existing:
            published_ids = get_published_product_ids(brand_name)
            logger.info(f"등록 완료 상품: {len(published_ids)}개 (스킵 대상)")

            before = len(items)
            items = [i for i in items if str(i.get('productNo', '')) not in published_ids]
            logger.info(f"수집 대상: {len(items)}개 (신규+미등록), 스킵: {before - len(items)}개 (등록완료)")

        # 상세 + 옵션 수집 → 변환 → 저장
        batch_data = []
        skipped_no_model = 0
        total = len(items)

        for idx, item in enumerate(items, 1):
            product_no = item.get('productNo')
            product_name = item.get('productName', '')
            mgmt_cd = item.get('productManagementCd', '')

            # model_id 사전 체크
            if not mgmt_cd:
                skipped_no_model += 1
                logger.info(f"  [{idx}/{total}] SKIP (no model_id) | {product_name}")
                continue

            # 상세 API
            detail = fetch_product_detail(product_no)
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            # 옵션 API
            options_data = fetch_product_options(product_no)
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            # 변환
            data = convert_to_raw_data(item, detail, options_data)
            if not data:
                skipped_no_model += 1
                logger.info(f"  [{idx}/{total}] SKIP (convert fail) | {mgmt_cd} | {product_name}")
                continue

            opt_count = len(json.loads(data['raw_json_data']).get('options', []))
            logger.info(f"  [{idx}/{total}] {data['model_id']} | {data['raw_price']:>10,.0f}원 | opts={opt_count} | {data['stock_status']} | {data['category_path']} | {data['product_name']}")

            if args.dry_run:
                pass
            else:
                batch_data.append(data)

            # 10개 단위 배치 저장
            if len(batch_data) >= 10:
                save_to_database(batch_data)
                logger.info(f"  DB 저장: {len(batch_data)}개 (누적 ~{idx}개 처리)")
                batch_data = []

        # 잔여분 저장
        if batch_data and not args.dry_run:
            save_to_database(batch_data)
            logger.info(f"  DB 저장(잔여): {len(batch_data)}개")

        logger.info(f"\n  {brand_name} 수집 결과: 총 {total}개, model_id 없어서 스킵: {skipped_no_model}개")

    logger.info("\n" + "=" * 60)
    logger.info("카시나 수집 완료")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :site"
            ), {'site': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 kasina 상품: {count}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
