# -*- coding: utf-8 -*-
"""
무신사 부티크(musinsa.com 부티크/category 105) 상품 수집 스크립트
- 무신사는 카페24가 아니라 자체 사이트 → HTML이 아니라 JSON API로 수집한다.
- 수집 기준: "전체상품" = 부티크 카테고리 105 전체.

세 가지 JSON API (전부 200 확인, 비로그인 가능):
  1) 목록  : GET https://api.musinsa.com/api2/dp/v1/plp/goods?gf=A&category=105&sortCode=NEW&page=N&size=60&caller=CATEGORY
             → data.list[] (goodsNo, brandName, brand, price ...), data.pagination(totalPages, hasNext)
  2) 상세  : GET https://goods-detail.musinsa.com/api2/goods/{goodsNo}
             → brandInfo(한글/영문), category(depth1~4), goodsImages, goodsPrice, isOutOfStock, styleNo ...
  3) 옵션  : GET https://goods-detail.musinsa.com/api2/goods/{goodsNo}/options
             → basic[] (컬러/사이즈 그룹), optionItems[] (판매 조합)

매핑:
  - raw_scraped_data : source_site='musinsa', mall_product_id=goodsNo, model_id=styleNo
  - mall_brands      : mall_brand_no=brand코드(johnsmedley)로 중복판별, en=영문명, raw_brand_name=한글명
  - mall_categories  : full_path(예 "상의 > 니트/스웨터")로 중복판별, gender='unisex' 관례, category_id=최하위 코드
  - 이미지는 raw_json_data.images(절대 URL 리스트) → ace_product_images 이관은 raw→ace 변환 단계에서

사용법:
    python musinsa_collector.py                  # 부티크 전체상품 raw 수집
    python musinsa_collector.py --limit 30       # 최대 30개 (테스트)
    python musinsa_collector.py --dry-run        # DB 저장 없이 테스트 (앞 몇 개 출력)
    python musinsa_collector.py --skip-existing  # 이미 등록 완료된 상품 스킵
    python musinsa_collector.py --start-page 5   # N페이지부터 (재개용)
"""

import os
import json
import time
import random
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Any

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'okmall'))
import authority_flag  # 단일권위 전환 스위치 (ace → buyma_listings)
from sqlalchemy.exc import OperationalError

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
# pool_pre_ping: 끊긴 연결 자동 감지·재연결 / pool_recycle: MySQL wait_timeout 전에 갱신
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=280)

# ===========================================
# 상수
# ===========================================

SOURCE_SITE = 'musinsa'
CATEGORY_CODE = '105'           # 부티크 = 전체상품
LIST_API = 'https://api.musinsa.com/api2/dp/v1/plp/goods'
DETAIL_API = 'https://goods-detail.musinsa.com/api2/goods/{}'
OPTIONS_API = 'https://goods-detail.musinsa.com/api2/goods/{}/options'
# 옵션 API에는 사이즈별 품절이 없다. 사이즈별 실시간 재고는 이 POST 엔드포인트에서만 나온다.
INVENTORY_API = 'https://goods-detail.musinsa.com/api2/goods/{}/options/v2/prioritized-inventories'
IMAGE_HOST = 'https://image.msscdn.net'   # 상세 imageUrl이 상대경로(/images/...)라 앞에 붙임
PRODUCT_URL = 'https://www.musinsa.com/products/{}'

PAGE_SIZE = 60
REQUEST_DELAY_MIN = 0.2
REQUEST_DELAY_MAX = 0.5
HTTP_TIMEOUT = 20
MAX_RETRY = 3

USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')


# ===========================================
# HTTP (JSON API)
# ===========================================

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': 'https://www.musinsa.com/',
    })
    return s


def fetch_json(session: requests.Session, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """JSON GET (429/5xx만 백오프 재시도). 실패 시 None."""
    for attempt in range(MAX_RETRY):
        try:
            resp = session.get(url, params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 * (attempt + 1)
                logger.warning(f"  [HTTP {resp.status_code}] 재시도({attempt+1}/{MAX_RETRY}) {wait}s — {url}")
                time.sleep(wait)
                continue
            # 404 등은 재시도 의미 없음
            return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            wait = 2 * (attempt + 1)
            logger.warning(f"  [HTTP 예외] 재시도({attempt+1}/{MAX_RETRY}) {wait}s — {str(e)[:60]}")
            time.sleep(wait)
        except ValueError:  # JSON 파싱 실패
            return None
    return None


# ===========================================
# API 호출
# ===========================================

def get_list_page(session: requests.Session, page: int, size: int = PAGE_SIZE) -> tuple:
    """목록 API 한 페이지 → (items, pagination). 실패 시 ([], None)."""
    params = {
        'gf': 'A',
        'category': CATEGORY_CODE,
        'sortCode': 'NEW',
        'page': page,
        'size': size,
        'caller': 'CATEGORY',
    }
    data = fetch_json(session, LIST_API, params)
    if not data or 'data' not in data:
        return [], None
    d = data['data']
    return d.get('list', []), d.get('pagination', {})


def get_detail(session: requests.Session, goods_no) -> Optional[Dict]:
    data = fetch_json(session, DETAIL_API.format(goods_no))
    if not data or not data.get('data'):
        return None
    return data['data']


def get_inventory(session: requests.Session, goods_no, option_value_nos: List[int]) -> Dict:
    """옵션값별 실시간 재고 조회 (POST). → {productVariantId: {'outOfStock': bool, 'remainQuantity': int|None}}

    무신사 옵션 API(basic/optionItems)에는 사이즈별 품절 정보가 없어서, 별도 재고 API를 호출해야 한다.
    productVariantId 는 optionItems[].no 와 동일하다."""
    if not option_value_nos:
        return {}
    url = INVENTORY_API.format(goods_no)
    for attempt in range(MAX_RETRY):
        try:
            resp = session.post(url, json={'optionValueNos': option_value_nos}, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                rows = (resp.json() or {}).get('data') or []
                return {
                    r.get('productVariantId'): {
                        'outOfStock': bool(r.get('outOfStock')),
                        'remainQuantity': r.get('remainQuantity'),
                    }
                    for r in rows if r.get('productVariantId') is not None
                }
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            return {}
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(2 * (attempt + 1))
        except ValueError:  # JSON 파싱 실패
            return {}
    return {}


def get_options(session: requests.Session, goods_no) -> Dict:
    data = fetch_json(session, OPTIONS_API.format(goods_no))
    if not data or not data.get('data'):
        return {}
    d = data['data']
    # optionItems에 사이즈별 실시간 재고(outOfStock/remainQuantity) 주입
    items = d.get('optionItems') or []
    if items:
        option_value_nos = sorted({n for it in items for n in (it.get('optionValueNos') or [])})
        inv = get_inventory(session, goods_no, option_value_nos)
        for it in items:
            info = inv.get(it.get('no'))
            if info:
                it['outOfStock'] = info['outOfStock']
                it['remainQuantity'] = info['remainQuantity']
    return d


# ===========================================
# 파싱 헬퍼
# ===========================================

def abs_image(url: str) -> str:
    if not url:
        return ''
    if url.startswith('http'):
        return url
    return IMAGE_HOST + url


def parse_options(options_data: Dict) -> List[Dict]:
    """옵션 그룹 정규화 → [{'name':'컬러','values':[...]}, {'name':'사이즈','values':[...]}]"""
    groups = []
    for grp in options_data.get('basic', []):
        values = [v.get('name', '') for v in grp.get('optionValues', []) if not v.get('isDeleted')]
        if values:
            groups.append({'name': grp.get('name', ''), 'values': values})
    return groups


def build_category(detail: Dict) -> tuple:
    """detail.category → (full_path, category_id, [depth1..4])  없으면 ('', '', [])"""
    cat = detail.get('category') or {}
    depths, codes = [], []
    for i in (1, 2, 3, 4):
        name = cat.get(f'categoryDepth{i}Name') or cat.get(f'categoryDepth{i}Title') or ''
        code = cat.get(f'categoryDepth{i}Code') or ''
        if name:
            depths.append(name)
            codes.append(code)
    if not depths:
        return '', '', []
    full_path = ' > '.join(depths)
    category_id = codes[-1] if codes and codes[-1] else (codes[0] if codes else '')
    return full_path, category_id, depths


# ===========================================
# DB 조회/저장
# ===========================================

def get_published_product_ids() -> set:
    """등록 완료된 상품의 mall_product_id 목록"""
    with engine.connect() as conn:
        _reg = authority_flag.registered_sql('a') if authority_flag.use_listing_authority() else "a.is_published = 1"
        result = conn.execute(text(f"""
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = :site AND {_reg}
        """), {'site': SOURCE_SITE})
        return {str(r[0]) for r in result}


def load_existing_brand_names() -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT mall_brand_name_en FROM mall_brands WHERE mall_name = :s AND mall_brand_name_en IS NOT NULL"),
            {'s': SOURCE_SITE})
        return {r[0] for r in rows}


def load_existing_category_paths() -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT full_path FROM mall_categories WHERE mall_name = :s AND full_path IS NOT NULL"),
            {'s': SOURCE_SITE})
        return {r[0] for r in rows}


def ensure_brand(conn, detail: Dict, seen: set) -> None:
    """mall_brands에 브랜드 자동 등록 (영문명으로 중복판별).
    무신사는 숫자 brand_no가 없고 슬러그 코드만 있어 mall_brand_no는 비운다(NULL).
    raw_brand_name = raw_scraped_data.brand_name_en 과 동일해야 조인됨(영문). 한글명은 저장 안 함."""
    binfo = detail.get('brandInfo') or {}
    code = binfo.get('brand') or detail.get('brand') or ''
    name_en = binfo.get('brandEnglishName') or code.upper()
    if not name_en or name_en in seen:
        return
    conn.execute(text("""
        INSERT INTO mall_brands
          (mall_name, mall_brand_name_en, raw_brand_name, mall_brand_no, mall_brand_url,
           buyma_brand_id, buyma_brand_name, mapping_level, is_mapped, is_active)
        VALUES
          (:m, :en, :raw, NULL, :url, NULL, NULL, 0, 0, NULL)
    """), {
        'm': SOURCE_SITE, 'en': name_en, 'raw': name_en,
        'url': f'https://www.musinsa.com/brand/{code}' if code else None,
    })
    seen.add(name_en)


def ensure_category(conn, full_path: str, category_id: str, depths: List[str], seen: set) -> None:
    """mall_categories에 카테고리 자동 등록 (full_path 유니크). gender='unisex' 관례."""
    if not full_path or full_path in seen:
        return
    d = (depths + [None, None, None, None])[:4]
    conn.execute(text("""
        INSERT INTO mall_categories
          (mall_name, category_id, gender, depth1, depth2, depth3, depth4,
           full_path, mall_category_url, is_active, created_at)
        VALUES
          (:m, :cid, 'unisex', :d1, :d2, :d3, :d4,
           :fp, :url, NULL, NOW())
        ON DUPLICATE KEY UPDATE
          category_id = VALUES(category_id),
          depth1 = VALUES(depth1), depth2 = VALUES(depth2),
          depth3 = VALUES(depth3), depth4 = VALUES(depth4)
    """), {
        'm': SOURCE_SITE, 'cid': category_id,
        'd1': d[0], 'd2': d[1], 'd3': d[2], 'd4': d[3],
        'fp': full_path,
        'url': f'https://www.musinsa.com/category/{category_id}' if category_id else None,
    })
    seen.add(full_path)


def convert_to_raw_data(detail: Dict, options_data: Dict, full_path: str) -> Optional[Dict]:
    """상세 + 옵션 → raw_scraped_data 형식"""
    goods_no = detail.get('goodsNo')
    if not goods_no:
        return None
    product_name = detail.get('goodsNm') or detail.get('goodsNmEng') or ''
    if not product_name:
        return None

    binfo = detail.get('brandInfo') or {}
    brand_en = binfo.get('brandEnglishName') or (detail.get('brand') or '').upper()
    model_id = detail.get('styleNo') or ''   # 무신사는 goodsNo가 유니크키라 styleNo 없어도 저장(스킵 안 함)

    gp = detail.get('goodsPrice') or {}
    raw_price = gp.get('salePrice') or detail.get('price') or 0
    original_price = gp.get('normalPrice') or raw_price

    stock_status = 'out_of_stock' if detail.get('isOutOfStock') else 'in_stock'

    # 캐러셀(Swiper/Pagination) 이미지 = 커버(thumbnailImageUrl) + goodsImages.
    # goodsImages엔 커버가 빠져있으므로 커버를 맨 앞에 붙인다(BUYMA 메인=첫 이미지).
    gallery = [abs_image(img.get('imageUrl', '')) for img in detail.get('goodsImages', [])]
    gallery = [u for u in gallery if u]
    thumbnail = abs_image(detail.get('thumbnailImageUrl', ''))
    images = ([thumbnail] if thumbnail else []) + [u for u in gallery if u != thumbnail]

    raw_json = {
        'brand_code': binfo.get('brand') or detail.get('brand') or '',
        'sex': detail.get('sex') or [],
        'style_no': detail.get('styleNo') or '',
        'season': detail.get('season') or '',
        'options': parse_options(options_data),
        'option_items': options_data.get('optionItems', []),
        'images': images,
        'thumbnail': thumbnail,
        'category_code': (detail.get('category') or {}).get('categoryDepth1Code', ''),
        'discount_rate': gp.get('discountRate'),
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': str(goods_no),
        'brand_name_en': brand_en,
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': full_path,
        'original_price': original_price,
        'raw_price': raw_price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': PRODUCT_URL.format(goods_no),
    }


def save_to_database(data_list: List[Dict]):
    if not data_list:
        return
    insert_sql = text("""
        INSERT INTO raw_scraped_data
        (source_site, mall_product_id, brand_name_en,
         product_name, p_name_full, model_id, category_path,
         original_price, raw_price, stock_status, raw_json_data, product_url)
        VALUES
        (:source_site, :mall_product_id, :brand_name_en,
         :product_name, :p_name_full, :model_id, :category_path,
         :original_price, :raw_price, :stock_status, :raw_json_data, :product_url)
        ON DUPLICATE KEY UPDATE
        brand_name_en = VALUES(brand_name_en),
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
    for attempt in range(3):
        try:
            with engine.connect() as conn:
                for data in data_list:
                    conn.execute(insert_sql, data)
                conn.commit()
            return
        except OperationalError as e:
            logger.warning(f"  [DB] 저장 실패({attempt+1}/3) — 재연결 후 재시도: {str(e)[:80]}")
            engine.dispose()
            time.sleep(2 * (attempt + 1))


# ===========================================
# 메인 수집
# ===========================================

def run_collection(args):
    """부티크 전체상품 스윕 → 상세/옵션 수집 → mall_brands/categories 등록 + raw 저장"""
    session = make_session()

    published_ids = get_published_product_ids() if args.skip_existing else set()
    if args.skip_existing:
        logger.info(f"  등록 완료 상품 {len(published_ids)}개 — 스킵 대상")

    seen_brands = load_existing_brand_names()
    seen_categories = load_existing_category_paths()
    logger.info(f"  기존 mall_brands: {len(seen_brands)}개 / mall_categories: {len(seen_categories)}개")

    seen_products = set()
    total_collected = 0
    total_skipped = 0       # 상세/옵션 실패 등
    new_brands0 = len(seen_brands)
    new_cats0 = len(seen_categories)

    page = args.start_page
    batch_data = []
    stop = False

    while not stop:
        items, pagination = get_list_page(session, page)
        if not items:
            logger.info(f"  [page {page}] 빈 응답 — 종료")
            break
        total_pages = (pagination or {}).get('totalPages')
        logger.info(f"\n>>> [page {page}/{total_pages}] 목록 {len(items)}개")

        for it in items:
            goods_no = str(it.get('goodsNo'))
            if not goods_no or goods_no in seen_products:
                continue
            seen_products.add(goods_no)
            if args.skip_existing and goods_no in published_ids:
                continue

            detail = get_detail(session, goods_no)
            if not detail:
                total_skipped += 1
                logger.warning(f"  상세 실패: {goods_no} | {it.get('goodsName','')[:30]}")
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                continue

            options_data = get_options(session, goods_no)
            full_path, category_id, depths = build_category(detail)
            data = convert_to_raw_data(detail, options_data, full_path)
            if not data:
                total_skipped += 1
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                continue

            total_collected += 1
            if total_collected <= 5 or total_collected % 100 == 0:
                logger.info(f"    [{total_collected}] {data['stock_status']:12} | {data['brand_name_en'][:18]:18} | "
                            f"{(data['model_id'] or '-'):14} | {int(data['raw_price']):>9,}원 | "
                            f"{full_path or '-'} | {data['product_name'][:28]}")

            if not args.dry_run:
                # 브랜드/카테고리 자동 등록 (자체 트랜잭션). --skip-mapping이면 mall_brands/mall_categories 건드리지 않음.
                if not args.skip_mapping:
                    try:
                        with engine.begin() as conn:
                            ensure_brand(conn, detail, seen_brands)
                            ensure_category(conn, full_path, category_id, depths, seen_categories)
                    except OperationalError as e:
                        logger.warning(f"  [DB] 브랜드/카테고리 등록 실패: {str(e)[:80]}")
                batch_data.append(data)
                if len(batch_data) >= 10:
                    save_to_database(batch_data)
                    batch_data = []

            if args.limit and total_collected >= args.limit:
                logger.info(f"  --limit {args.limit} 도달 — 종료")
                stop = True
                break

            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        if stop:
            break
        if pagination and not pagination.get('hasNext'):
            logger.info("  마지막 페이지 — 종료")
            break
        page += 1

    if batch_data and not args.dry_run:
        save_to_database(batch_data)

    logger.info("\n" + "=" * 60)
    logger.info("무신사 부티크 전체상품 수집 완료")
    logger.info(f"  총 수집: {total_collected}개 | 실패/스킵: {total_skipped}개")
    logger.info(f"  신규 브랜드: {len(seen_brands) - new_brands0}개 | 신규 카테고리: {len(seen_categories) - new_cats0}개")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :s"), {'s': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 musinsa raw: {count}개")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='무신사 부티크 전체상품 수집기')
    parser.add_argument('--limit', type=int, help='최대 수집 상품 수 (테스트용)')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    parser.add_argument('--start-page', type=int, default=1, help='시작 페이지 (재개용)')
    parser.add_argument('--skip-mapping', action='store_true',
                        help='mall_brands/mall_categories 자동 등록 건너뛰기 (raw_scraped_data만 갱신)')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"무신사 부티크 수집 시작 (category={CATEGORY_CODE}, dry_run={args.dry_run})")
    logger.info("=" * 60)
    run_collection(args)


if __name__ == '__main__':
    main()
