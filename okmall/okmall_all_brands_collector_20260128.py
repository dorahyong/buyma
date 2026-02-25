# -*- coding: utf-8 -*-
"""
오케이몰 모든 브랜드 상품 수집 스크립트 (최종 완성본)
- 정교한 ld+json 파싱 및 실측(measurements), 혼용률(composition) 수집 로직 통합
- mall_brands 테이블의 모든 활성 브랜드를 순회하며 수집
- 이미지 수집 로직 제외 (요청 사항)
- --dry-run 옵션 지원
"""

import os
import re
import json
import time
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ===========================================
# 환경 설정
# ===========================================

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# DB 연결 설정
DATABASE_URL = os.getenv('DATABASE_URL', 'mysql+pymysql://block:1234@54.180.248.182:3306/buyma')
engine = create_engine(DATABASE_URL, echo=False)

# 스크래핑 설정
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://www.okmall.com/',
}
SCRAPING_DELAY = 1.5

# ===========================================
# 데이터 추출 함수
# ===========================================

def extract_ld_json(soup: BeautifulSoup) -> Tuple[Dict, List]:
    """ld+json 파싱"""
    product_data = {}
    breadcrumb_list = []
    scripts = soup.find_all('script', type='application/ld+json')
    for script in scripts:
        try:
            content = json.loads(script.string)
            if isinstance(content, dict):
                if content.get('@type') == 'Product':
                    product_data = content
                elif content.get('@type') == 'BreadcrumbList':
                    breadcrumb_list = content.get('itemListElement', [])
        except:
            continue
    return product_data, breadcrumb_list

def extract_brand_info(product_data: Dict, soup: BeautifulSoup) -> Tuple[str, str]:
    """브랜드 정보 추출"""
    raw_brand = product_data.get('brand', {}).get('name', '')
    brand_kr = re.split(r'\(', raw_brand)[0].strip() if '(' in raw_brand else raw_brand
    brand_en = ''
    en_match = re.search(r'\(([^)]+)\)', raw_brand)
    if en_match:
        brand_en = en_match.group(1).strip()
    else:
        brand_elem = soup.select_one('.target_brand .prName_Brand')
        if brand_elem:
            brand_en = brand_elem.get_text(strip=True)
    return brand_en, brand_kr

def extract_product_name(soup: BeautifulSoup) -> Tuple[str, str, str, str]:
    """상품명 및 모델번호 추출"""
    name_area = soup.select_one('h3#ProductNameArea')
    full_name = name_area.get_text(' ', strip=True) if name_area else ''
    season_elem = soup.select_one('.prd_name_season')
    season = season_elem.get_text(strip=True) if season_elem else ''
    prd_name_elem = soup.select_one('.prd_name')
    prd_name_text = prd_name_elem.get_text(strip=True) if prd_name_elem else ''
    model_id = ''
    model_match = re.search(r'\(([^)]+)\)', prd_name_text)
    if model_match:
        model_id = model_match.group(1).strip()
    product_name = prd_name_text.split('(')[0].strip()
    return product_name, full_name, model_id, season

def extract_price_info(product_data: Dict, soup: BeautifulSoup) -> Tuple[int, int]:
    """가격 정보 추출"""
    original_price = 0
    origin_elem = soup.select_one('.value_price .price')
    if origin_elem:
        price_text = origin_elem.get_text()
        price_match = re.sub(r'[^0-9]', '', price_text)
        if price_match:
            original_price = int(price_match)
    sales_price = 0
    offers = product_data.get('offers', {})
    if offers.get('@type') == 'AggregateOffer':
        sales_price = int(offers.get('lowPrice', 0))
    elif offers.get('@type') == 'Offer':
        sales_price = int(offers.get('price', 0))
    return original_price, sales_price

def extract_category_path(breadcrumb_list: List) -> str:
    """카테고리 경로 추출"""
    categories = [item.get('name', '') for item in breadcrumb_list if item.get('name')]
    return ' > '.join(categories)

def extract_options(soup: BeautifulSoup, product_data: Dict) -> List[Dict]:
    """옵션 및 재고 상태 추출"""
    options = []
    opt_rows = soup.select('#ProductOPTList tbody tr[name="selectOption"]')
    for row in opt_rows:
        cols = row.select('td')
        if len(cols) >= 3:
            sinfo = row.get('sinfo', '')
            option_code = sinfo.split('|')[-1] if sinfo else ''
            option = {
                'color': cols[0].get_text(strip=True),
                'tag_size': cols[1].get_text(strip=True),
                'real_size': cols[2].get_text(strip=True),
                'option_code': option_code,
                'status': 'in_stock'
            }
            if '품절' in row.get_text():
                option['status'] = 'out_of_stock'
            options.append(option)
    
    offers = product_data.get('offers', {})
    if offers.get('@type') == 'AggregateOffer':
        offer_list = offers.get('offers', [])
        for offer in offer_list:
            sku = str(offer.get('sku', ''))
            is_out_of_stock = 'OutOfStock' in offer.get('availability', '')
            for opt in options:
                if opt.get('option_code') == sku:
                    opt['status'] = 'out_of_stock' if is_out_of_stock else 'in_stock'
    return options

def extract_measurements(soup: BeautifulSoup) -> Dict[str, Dict]:
    """실측 정보(measurements) 추출"""
    measurements = {}

    # 방법 1: item_size_detail 클래스로 찾기 (의류)
    detail_div = soup.find('div', class_='item_size_detail')
    # 방법 2: realSizeInfo_detail2 ID로 찾기 (가방/액세서리)
    if not detail_div:
        detail_div = soup.find('div', id='realSizeInfo_detail2')

    if not detail_div:
        return measurements

    # display:none이 아닌 첫 번째 ul만 사용
    visible_ul = None
    for ul in detail_div.find_all('ul'):
        style = ul.get('style', '')
        if 'display:none' not in style and 'display: none' not in style:
            visible_ul = ul
            break

    if not visible_ul:
        return measurements

    for li in visible_ul.find_all('li'):
        size_link = li.find('a')
        if not size_link: continue
        size_name = size_link.get_text(strip=True)

        summary_p = li.find('p')
        summary = summary_p.get_text(strip=True) if summary_p else ""

        size_data = {"summary": summary}

        # tbody 내의 모든 tr 찾기
        tbody = li.find('tbody')
        rows = tbody.find_all('tr') if tbody else li.find_all('tr')
        for row in rows:
            th = row.find('th')
            td = row.find('td')
            if th and td:
                label = th.get_text(strip=True)
                value = td.get_text(strip=True)
                if value == '-': continue

                # 의류 측정값
                if '허리' in label: size_data['waist'] = value
                elif '허벅지' in label: size_data['thigh'] = value
                elif '밑위' in label: size_data['rise'] = value
                elif '엉덩이' in label: size_data['hip'] = value
                elif '안기장' in label: size_data['inseam'] = value
                elif '밑단' in label: size_data['hem'] = value
                elif '바깥기장' in label: size_data['outseam'] = value
                elif '어깨' in label: size_data['shoulder'] = value
                elif '가슴' in label: size_data['chest'] = value
                elif '팔' in label: size_data['sleeve_length'] = value
                elif '소매' in label: size_data['sleeve_width'] = value
                elif '총장' in label: size_data['total_length'] = value
                # 가방/액세서리 측정값
                elif '가로' in label: size_data['width'] = value
                elif '세로' in label: size_data['depth'] = value
                elif '높이' in label and '숄더끈' not in label: size_data['height'] = value
                elif '숄더끈 높이(최대)' in label or '숄더끈높이(최대)' in label: size_data['Shoulder strap drop (max)'] = value
                elif '숄더끈 높이(최소)' in label or '숄더끈높이(최소)' in label: size_data['Shoulder strap drop (min)'] = value
                elif '중량' in label or '무게' in label: size_data['weight'] = value

        measurements[size_name] = size_data
    return measurements

def extract_composition(soup: BeautifulSoup) -> Dict[str, str]:
    """혼용률(composition) 추출"""
    composition = {}
    material_div = soup.find('div', id='realSizeInfo_material')
    if not material_div:
        return composition

    rows = material_div.find_all('tr')
    for row in rows:
        tds = row.find_all('td')
        if len(tds) >= 2:
            label = tds[0].get_text(strip=True)
            value = tds[1].get_text(strip=True)
            value = " ".join(value.split())
            if not value or value == '-':
                continue

            # 특정 라벨 매핑
            if '겉감' in label:
                composition['outer'] = value
            elif '안감' in label:
                composition['lining'] = value
            elif '충전재' in label:
                composition['padding'] = value
            elif '소재' in label:
                composition['material'] = value
            elif '혼용률' in label or '혼용율' in label:
                composition['blend_ratio'] = value
            else:
                # 기타 라벨은 원본 라벨명으로 저장
                clean_label = label.replace(':', '').strip()
                if clean_label:
                    composition[clean_label] = value

    # 만약 특정 라벨이 없고 단일 텍스트인 경우
    if not composition:
        # 테이블 외 직접 텍스트도 시도
        all_text = material_div.get_text(strip=True)
        all_text = " ".join(all_text.split())
        if all_text:
            composition['raw'] = all_text

    return composition

def extract_product_data(html: str, product_url: str) -> Optional[Dict[str, Any]]:
    """전체 상품 데이터 추출 및 JSON 구성"""
    soup = BeautifulSoup(html, 'html.parser')
    product_ld, breadcrumb_ld = extract_ld_json(soup)
    if not product_ld:
        return None

    brand_en, brand_kr = extract_brand_info(product_ld, soup)
    product_name, full_name, model_id, season = extract_product_name(soup)
    original_price, sales_price = extract_price_info(product_ld, soup)
    category_path = extract_category_path(breadcrumb_ld)
    options = extract_options(soup, product_ld)

    stock_status = 'out_of_stock'
    if any(opt.get('status') == 'in_stock' for opt in options):
        stock_status = 'in_stock'

    mall_product_id = str(product_ld.get('sku', ''))
    if not mall_product_id:
        match = re.search(r'no=(\d+)', product_url)
        if match:
            mall_product_id = match.group(1)

    # raw_json_data 구성 (정상 데이터 포맷 일치)
    raw_json_data = {
        'images': [], # 이미지 제외 요청 반영
        'options': options,
        'season': season,
        'measurements': extract_measurements(soup),
        'composition': extract_composition(soup),
        'ld_json_product': product_ld,
        'rating': product_ld.get('aggregateRating', {}),
        'scraped_at': datetime.now().isoformat()
    }

    return {
        'source_site': 'okmall',
        'mall_product_id': mall_product_id,
        'brand_name_en': brand_en,
        'brand_name_kr': brand_kr,
        'product_name': product_name,
        'p_name_full': full_name,
        'model_id': model_id,
        'category_path': category_path,
        'original_price': original_price,
        'raw_price': sales_price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json_data, ensure_ascii=False),
        'product_url': product_url
    }

# ===========================================
# 수집 및 저장 로직
# ===========================================

def get_brands_from_database(brand_filter: str = None) -> List[Dict]:
    with engine.connect() as conn:
        query = "SELECT mall_brand_name_en, mall_brand_url FROM mall_brands WHERE mall_name = 'okmall' AND is_active = 1"
        if brand_filter:
            query += f" AND UPPER(mall_brand_name_en) = :brand"
        result = conn.execute(text(query), {"brand": brand_filter.upper()} if brand_filter else {})
        return [{'name': r[0], 'url': r[1]} for r in result]

def get_product_urls_from_list(base_url: str, limit: int = None) -> List[str]:
    all_urls = []
    page = 1
    max_pages = 100

    while page <= max_pages:
        page_url = f"{base_url}&page={page}" if '?' in base_url else f"{base_url}?page={page}"
        try:
            response = requests.get(page_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
        except:
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        product_boxes = soup.select('.item_box[data-productno]')
        if not product_boxes:
            break

        for box in product_boxes:
            product_no = box.get('data-productno')
            if product_no:
                all_urls.append(f"https://www.okmall.com/products/view?no={product_no}")
        
        if limit and len(all_urls) >= limit:
            all_urls = all_urls[:limit]
            break
            
        page += 1
        time.sleep(0.5)

    return list(dict.fromkeys(all_urls))

def save_to_database(data_list: List[Dict]):
    if not data_list: return
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

def main():
    parser = argparse.ArgumentParser(description='오케이몰 통합 브랜드 수집기')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='브랜드당 최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"오케이몰 통합 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    logger.info("=" * 60)

    brands = get_brands_from_database(args.brand)
    logger.info(f"대상 브랜드: {len(brands)}개")

    for brand in brands:
        logger.info(f"\n>>> 브랜드 시작: {brand['name']}")
        product_urls = get_product_urls_from_list(brand['url'], limit=args.limit)
        logger.info(f"발견된 상품: {len(product_urls)}개")

        batch_data = []
        for idx, url in enumerate(product_urls, 1):
            try:
                response = requests.get(url, headers=HEADERS, timeout=30)
                if response.status_code == 200:
                    data = extract_product_data(response.text, url)
                    if data:
                        if args.dry_run:
                            logger.info(f"  [{idx}/{len(product_urls)}] [DRY-RUN] 추출 성공: {data['product_name']}")
                        else:
                            batch_data.append(data)
                            logger.info(f"  [{idx}/{len(product_urls)}] 추출 완료: {data['product_name']}")
                
                if len(batch_data) >= 10: # 10개 단위 저장
                    save_to_database(batch_data)
                    batch_data = []
                
                time.sleep(SCRAPING_DELAY)
            except Exception as e:
                logger.error(f"  [{idx}/{len(product_urls)}] 오류: {e}")

        if batch_data and not args.dry_run:
            save_to_database(batch_data)

    logger.info("\n" + "=" * 60)
    logger.info("모든 브랜드 수집 완료")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
