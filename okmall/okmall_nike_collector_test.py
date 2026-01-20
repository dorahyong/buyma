"""
오케이몰 나이키 브랜드 상품 수집 스크립트
- 나이키 브랜드 상품 목록 페이지에서 전체 상품 URL 수집 (페이지네이션 지원)
- 각 상품 상세 페이지에서 바이마 API에 필요한 데이터 추출
- raw_scraped_data 테이블에 저장

실행 방법:
    python okmall_nike_collector_test.py

필요 패키지:
    pip install requests beautifulsoup4 python-dotenv sqlalchemy pymysql
"""

import os
import re
import json
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ===========================================
# 환경 설정
# ===========================================

# .env 파일 로드 (프로젝트 루트에서)
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
USER_AGENT = os.getenv('SCRAPING_USER_AGENT',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36')
SCRAPING_DELAY = float(os.getenv('SCRAPING_DELAY_SECONDS', '1'))

# 요청 헤더
HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Referer': 'https://www.okmall.com/',
}

# 나이키 브랜드 상품 목록 URL
NIKE_LIST_URL = 'https://www.okmall.com/products/list?brand=%EB%82%98%EC%9D%B4%ED%82%A4%28NIKE%29'


# ===========================================
# 데이터 추출 함수
# ===========================================

def extract_ld_json(soup: BeautifulSoup) -> tuple[Dict, List]:
    """
    페이지에서 ld+json 스크립트를 파싱하여 Product 정보와 BreadcrumbList 추출

    Args:
        soup: BeautifulSoup 객체

    Returns:
        tuple: (product_data, breadcrumb_list)
    """
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
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"ld+json 파싱 오류: {e}")
            continue

    return product_data, breadcrumb_list


def extract_brand_info(product_data: Dict, soup: BeautifulSoup) -> tuple[str, str]:
    """
    브랜드 영문명/한글명 추출
    예: "나이키(NIKE)" -> brand_kr="나이키", brand_en="NIKE"

    Args:
        product_data: ld+json Product 데이터
        soup: BeautifulSoup 객체

    Returns:
        tuple: (brand_en, brand_kr)
    """
    raw_brand = product_data.get('brand', {}).get('name', '')

    # 한글명: 괄호 앞 문자열
    brand_kr = re.split(r'\(', raw_brand)[0].strip() if '(' in raw_brand else raw_brand

    # 영문명: 괄호 안 문자열
    brand_en = ''
    en_match = re.search(r'\(([^)]+)\)', raw_brand)
    if en_match:
        brand_en = en_match.group(1).strip()
    else:
        # ld+json에 없으면 HTML에서 추출 시도
        brand_elem = soup.select_one('.target_brand .prName_Brand')
        if brand_elem:
            brand_en = brand_elem.get_text(strip=True)

    return brand_en, brand_kr


def extract_product_name(soup: BeautifulSoup) -> tuple[str, str, str, str]:
    """
    상품명, 전체 상품명, 모델ID, 시즌 정보 추출

    Args:
        soup: BeautifulSoup 객체

    Returns:
        tuple: (product_name, full_name, model_id, season)
    """
    # 전체 상품명 영역
    name_area = soup.select_one('h3#ProductNameArea')
    full_name = name_area.get_text(' ', strip=True) if name_area else ''

    # 시즌 정보 (예: 25FW, 26SS)
    season_elem = soup.select_one('.prd_name_season')
    season = season_elem.get_text(strip=True) if season_elem else ''

    # 순수 상품명
    prd_name_elem = soup.select_one('.prd_name')
    prd_name_text = prd_name_elem.get_text(strip=True) if prd_name_elem else ''

    # 모델ID 추출: 상품명의 첫 번째 괄호 안 문자열
    # 예: "남성 나이키 드라이 핏 페이서 하프 짚 (FQ2494-010)" -> "FQ2494-010"
    model_id = ''
    model_match = re.search(r'\(([^)]+)\)', prd_name_text)
    if model_match:
        model_id = model_match.group(1).strip()

    # 괄호 앞까지가 순수 상품명
    product_name = prd_name_text.split('(')[0].strip()

    return product_name, full_name, model_id, season


def extract_price_info(product_data: Dict, soup: BeautifulSoup) -> tuple[int, int]:
    """
    가격 정보 추출 (정가, 판매가)

    Args:
        product_data: ld+json Product 데이터
        soup: BeautifulSoup 객체

    Returns:
        tuple: (original_price, sales_price)
    """
    # 정가: .value_price .price 에서 추출
    original_price = 0
    origin_elem = soup.select_one('.value_price .price')
    if origin_elem:
        price_text = origin_elem.get_text()
        price_match = re.sub(r'[^0-9]', '', price_text)
        if price_match:
            original_price = int(price_match)

    # 판매가: ld+json offers에서 추출
    sales_price = 0
    offers = product_data.get('offers', {})

    # AggregateOffer인 경우 lowPrice 사용
    if offers.get('@type') == 'AggregateOffer':
        sales_price = int(offers.get('lowPrice', 0))
    # 단일 Offer인 경우 price 사용
    elif offers.get('@type') == 'Offer':
        sales_price = int(offers.get('price', 0))

    return original_price, sales_price


def extract_category_path(breadcrumb_list: List) -> str:
    """
    카테고리 경로 추출

    Args:
        breadcrumb_list: BreadcrumbList itemListElement

    Returns:
        str: 카테고리 경로 (예: "ACTIVITY·LIFE > 액티비티 > 러닝 > 상의")
    """
    categories = [item.get('name', '') for item in breadcrumb_list if item.get('name')]
    return ' > '.join(categories)


def extract_images(product_data: Dict) -> List[str]:
    """
    상품 이미지 URL 목록 추출

    Args:
        product_data: ld+json Product 데이터

    Returns:
        List[str]: 이미지 URL 목록
    """
    images = product_data.get('image', [])
    if isinstance(images, str):
        images = [images]
    return images


def extract_options(soup: BeautifulSoup, product_data: Dict) -> List[Dict]:
    """
    상품 옵션(색상/사이즈/재고상태) 추출

    Args:
        soup: BeautifulSoup 객체
        product_data: ld+json Product 데이터

    Returns:
        List[Dict]: 옵션 정보 목록
    """
    options = []

    # 방법 1: ProductOPTList 테이블에서 추출
    opt_rows = soup.select('#ProductOPTList tbody tr[name="selectOption"]')

    for row in opt_rows:
        cols = row.select('td')
        if len(cols) >= 3:
            # sinfo 속성에서 옵션 코드 추출 (예: "Black-S^1B:06|Black-S|2234429")
            sinfo = row.get('sinfo', '')
            option_code = sinfo.split('|')[-1] if sinfo else ''

            option = {
                'color': cols[0].get_text(strip=True),           # 색상
                'tag_size': cols[1].get_text(strip=True),        # 택 사이즈
                'real_size': cols[2].get_text(strip=True),       # 실측 사이즈
                'option_code': option_code,                       # 옵션 코드
                'status': 'in_stock'                              # 기본값
            }

            # 품절 여부 확인
            if '품절' in row.get_text():
                option['status'] = 'out_of_stock'

            options.append(option)

    # 방법 2: ld+json offers에서 재고 상태 보완
    offers = product_data.get('offers', {})
    if offers.get('@type') == 'AggregateOffer':
        offer_list = offers.get('offers', [])
        for offer in offer_list:
            sku = str(offer.get('sku', ''))
            availability = offer.get('availability', '')
            is_out_of_stock = 'OutOfStock' in availability

            # 매칭되는 옵션의 재고 상태 업데이트
            for opt in options:
                if opt.get('option_code') == sku:
                    opt['status'] = 'out_of_stock' if is_out_of_stock else 'in_stock'

    return options


def extract_product_data(html: str, product_url: str) -> Optional[Dict[str, Any]]:
    """
    상품 상세 페이지 HTML에서 모든 필요 데이터 추출

    Args:
        html: 상품 상세 페이지 HTML
        product_url: 상품 URL

    Returns:
        Dict: 추출된 상품 데이터 (raw_scraped_data 테이블 형식)
    """
    soup = BeautifulSoup(html, 'html.parser')

    # ld+json 파싱
    product_ld, breadcrumb_ld = extract_ld_json(soup)

    if not product_ld:
        logger.warning(f"ld+json Product 데이터 없음: {product_url}")
        return None

    # 브랜드 정보
    brand_en, brand_kr = extract_brand_info(product_ld, soup)

    # 상품명 정보
    product_name, full_name, model_id, season = extract_product_name(soup)

    # 가격 정보
    original_price, sales_price = extract_price_info(product_ld, soup)

    # 카테고리 경로
    category_path = extract_category_path(breadcrumb_ld)

    # 이미지 URL 목록
    images = extract_images(product_ld)

    # 옵션 정보
    options = extract_options(soup, product_ld)

    # 재고 상태 결정 (하나라도 재고가 있으면 in_stock)
    stock_status = 'out_of_stock'
    if any(opt.get('status') == 'in_stock' for opt in options):
        stock_status = 'in_stock'

    # mall_product_id 추출 (sku 또는 URL에서)
    mall_product_id = str(product_ld.get('sku', ''))
    if not mall_product_id:
        # URL에서 추출: ?no=753625
        match = re.search(r'no=(\d+)', product_url)
        if match:
            mall_product_id = match.group(1)

    # raw_json_data 구성 (바이마 API에 필요한 추가 정보)
    raw_json_data = {
        'images': images,
        'options': options,
        'season': season,
        'ld_json_product': product_ld,      # 원본 ld+json 보존
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
# 수집 함수
# ===========================================

def get_product_urls_from_list(base_url: str, max_pages: int = 100) -> List[str]:
    """
    브랜드 상품 목록 페이지에서 전체 상품 URL 목록 추출 (페이지네이션 지원)

    Args:
        base_url: 브랜드 상품 목록 기본 URL
        max_pages: 최대 페이지 수 (무한 루프 방지)

    Returns:
        List[str]: 상품 URL 목록
    """
    all_urls = []
    page = 1

    logger.info(f"상품 목록 수집 시작: {base_url}")

    while page <= max_pages:
        # 페이지 URL 구성
        page_url = f"{base_url}&page={page}" if '?' in base_url else f"{base_url}?page={page}"

        logger.info(f"페이지 {page} 요청 중...")

        try:
            response = requests.get(page_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"페이지 {page} 요청 실패: {e}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')

        # 상품 박스에서 data-productno 속성 추출
        product_boxes = soup.select('.item_box[data-productno]')

        # 상품이 없으면 마지막 페이지에 도달
        if not product_boxes:
            logger.info(f"페이지 {page}: 상품 없음 - 수집 완료")
            break

        # 상품 URL 추출
        page_urls = []
        for box in product_boxes:
            product_no = box.get('data-productno')
            if product_no:
                url = f"https://www.okmall.com/products/view?no={product_no}"
                page_urls.append(url)

        all_urls.extend(page_urls)
        logger.info(f"페이지 {page}: {len(page_urls)}개 상품 수집")

        page += 1

        # 페이지 간 요청 간격 (서버 부하 방지)
        time.sleep(0.5)

    # 중복 제거
    unique_urls = list(dict.fromkeys(all_urls))

    logger.info(f"총 수집된 상품 URL: {len(unique_urls)}개 (중복 제거 후)")
    return unique_urls


def scrape_product(product_url: str) -> Optional[Dict[str, Any]]:
    """
    개별 상품 상세 페이지 스크래핑

    Args:
        product_url: 상품 URL

    Returns:
        Dict: 추출된 상품 데이터
    """
    logger.info(f"상품 페이지 요청: {product_url}")

    try:
        response = requests.get(product_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"상품 페이지 요청 실패: {e}")
        return None

    return extract_product_data(response.text, product_url)


def save_to_database(data_list: List[Dict]) -> int:
    """
    수집된 데이터를 DB에 저장 (Upsert)

    Args:
        data_list: 저장할 상품 데이터 목록

    Returns:
        int: 저장된 레코드 수
    """
    if not data_list:
        logger.warning("저장할 데이터가 없습니다.")
        return 0

    saved_count = 0

    # SQL: INSERT ... ON DUPLICATE KEY UPDATE
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

    try:
        with engine.connect() as conn:
            for data in data_list:
                try:
                    conn.execute(insert_sql, data)
                    saved_count += 1
                    logger.debug(f"저장 완료: {data['mall_product_id']}")
                except Exception as e:
                    logger.error(f"개별 저장 실패 ({data['mall_product_id']}): {e}")

            conn.commit()
            logger.info(f"DB 커밋 완료: {saved_count}건")

    except Exception as e:
        logger.error(f"DB 연결/저장 오류: {e}")
        return 0

    return saved_count


# ===========================================
# 메인 실행
# ===========================================

def main():
    """
    메인 실행 함수
    1. 나이키 브랜드 상품 목록에서 전체 상품 URL 수집
    2. 각 상품 상세 페이지 스크래핑
    3. DB에 저장 (배치 단위)
    """
    logger.info("=" * 60)
    logger.info("오케이몰 나이키 브랜드 전체 상품 수집 시작")
    logger.info("=" * 60)

    # 1. 전체 상품 URL 수집
    product_urls = get_product_urls_from_list(NIKE_LIST_URL)

    if not product_urls:
        logger.error("수집된 상품 URL이 없습니다. 종료합니다.")
        return

    logger.info(f"수집 대상 상품: {len(product_urls)}개")
    logger.info("-" * 60)

    # 2. 각 상품 스크래핑 (배치 저장)
    collected_data = []
    success_count = 0
    fail_count = 0
    BATCH_SIZE = 50  # 50개마다 DB 저장

    for idx, url in enumerate(product_urls, 1):
        logger.info(f"[{idx}/{len(product_urls)}] 수집 중...")

        data = scrape_product(url)
        if data:
            collected_data.append(data)
            success_count += 1
            logger.info(f"  ✓ 성공: {data['brand_name_kr']} | {data['product_name'][:30]}... | "
                       f"모델: {data['model_id']} | 가격: {data['raw_price']:,}원")
        else:
            fail_count += 1
            logger.warning(f"  ✗ 실패: {url}")

        # 배치 저장 (50개마다)
        if len(collected_data) >= BATCH_SIZE:
            logger.info(f"--- 배치 저장: {len(collected_data)}건 ---")
            saved = save_to_database(collected_data)
            logger.info(f"--- 저장 완료: {saved}건 ---")
            collected_data = []  # 리스트 초기화

        # 요청 간격 조절 (서버 부하 방지)
        if idx < len(product_urls):
            time.sleep(SCRAPING_DELAY)

    # 3. 남은 데이터 DB 저장
    if collected_data:
        logger.info(f"--- 최종 배치 저장: {len(collected_data)}건 ---")
        saved = save_to_database(collected_data)
        logger.info(f"--- 저장 완료: {saved}건 ---")

    # 4. 결과 요약
    logger.info("=" * 60)
    logger.info("수집 완료 요약:")
    logger.info(f"  - 총 대상 상품: {len(product_urls)}개")
    logger.info(f"  - 수집 성공: {success_count}개")
    logger.info(f"  - 수집 실패: {fail_count}개")
    logger.info(f"  - 성공률: {success_count/len(product_urls)*100:.1f}%")
    logger.info("=" * 60)
    logger.info("전체 수집 완료")


if __name__ == "__main__":
    main()
