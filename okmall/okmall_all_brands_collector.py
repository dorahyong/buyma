# -*- coding: utf-8 -*-
"""
오케이몰 모든 브랜드 상품 수집 스크립트 (최종 완성본)
- 정교한 ld+json 파싱 및 실측(measurements), 혼용률(composition) 수집 로직 통합
- mall_brands 테이블의 모든 활성 브랜드를 순회하며 수집
- 이미지 수집 로직 제외 (요청 사항)
- --dry-run 옵션 지원

★ 봇 감지 방지 기능 (v2):
- 30개마다 세션 교체 + 메인 페이지 방문
- 세션 내에서는 쿠키 유지 (자연스러운 브라우징)
- 랜덤 브라우저 프로필 (전체 헤더 세트)
- 자연스러운 Referer 체인
- 타임아웃 연속 5회 시 차단 감지 및 중지
"""

import os
import re
import json
import time
import random
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
DATABASE_URL = os.getenv('DATABASE_URL', f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4")
engine = create_engine(DATABASE_URL, echo=False)

# =====================================================
# ★★★ 완전한 브라우저 프로필 (UA + 모든 헤더가 일치) ★★★
# =====================================================
BROWSER_PROFILES = [
    # Chrome 120 on Windows
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cache-Control': 'max-age=0',
    },
    # Chrome 121 on Windows
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cache-Control': 'max-age=0',
    },
    # Chrome 120 on Mac
    {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'Cache-Control': 'max-age=0',
    },
    # Firefox 121 on Windows
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.8,en-US;q=0.5,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    },
    # Edge 120 on Windows
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cache-Control': 'max-age=0',
    },
]

# 딜레이 설정
REQUEST_DELAY_MIN = 0.8  # 오케이몰 요청 간 최소 딜레이
REQUEST_DELAY_MAX = 1.8  # 오케이몰 요청 간 최대 딜레이

# 세션 관리 설정
SESSION_REFRESH_INTERVAL = 30  # 30개마다 세션 교체 + 메인 페이지 방문
MAX_CONSECUTIVE_TIMEOUTS = 5   # 연속 타임아웃 5회 시 차단으로 판단


# =====================================================
# ★★★ 오케이몰 세션 관리 클래스 ★★★
# =====================================================
class OkmallSessionManager:
    """오케이몰 봇 감지 방지 세션 관리자"""

    def __init__(self):
        self.session = None
        self.profile = None
        self.request_count = 0
        self.consecutive_timeout_count = 0
        self.is_blocked = False

    def create_new_session(self) -> Tuple[bool, Optional[str]]:
        """새 오케이몰 세션 생성 + 메인 페이지 방문"""
        try:
            if self.session:
                self.session.close()

            self.session = requests.Session()
            self.profile = random.choice(BROWSER_PROFILES).copy()

            # 메인 페이지 방문 헤더 (구글에서 온 것처럼)
            main_headers = self.profile.copy()
            main_headers['Referer'] = 'https://www.google.com/'
            main_headers['Sec-Fetch-Site'] = 'cross-site'
            self.session.headers.update(main_headers)

            logger.info("  [세션] 새 세션 시작 - 메인 페이지 방문 중...")
            main_response = self.session.get('https://www.okmall.com/', timeout=15)

            if main_response.status_code != 200:
                return False, f"메인 페이지 접속 실패: {main_response.status_code}"

            # 세션 내 이동용 헤더로 변경
            product_headers = self.profile.copy()
            product_headers['Referer'] = 'https://www.okmall.com/'
            product_headers['Sec-Fetch-Site'] = 'same-origin'
            self.session.headers.update(product_headers)

            self.request_count = 0
            time.sleep(random.uniform(0.5, 1.5))
            logger.info("  [세션] 새 세션 준비 완료 (쿠키 획득됨)")
            return True, None

        except requests.exceptions.Timeout:
            return False, "메인 페이지 타임아웃"
        except Exception as e:
            return False, f"세션 생성 오류: {str(e)}"

    def fetch_page(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        세션 관리가 적용된 페이지 접속
        - 30개마다 새 세션 + 메인 페이지 방문
        - 타임아웃 연속 5회 시 차단 감지
        """
        if self.is_blocked:
            return None, "차단됨"

        # 세션 교체 필요 여부 확인
        if self.session is None or self.request_count >= SESSION_REFRESH_INTERVAL:
            success, error = self.create_new_session()
            if not success:
                return None, error

        try:
            response = self.session.get(url, timeout=30)
            self.request_count += 1

            if response.status_code == 403:
                self.is_blocked = True
                return None, "접근 차단됨 (403)"

            response.raise_for_status()
            self.consecutive_timeout_count = 0  # 성공 시 초기화
            return response.text, None

        except requests.exceptions.Timeout:
            self.request_count += 1
            self.consecutive_timeout_count += 1
            logger.warning(f"  [타임아웃] 연속 {self.consecutive_timeout_count}회")

            if self.consecutive_timeout_count >= MAX_CONSECUTIVE_TIMEOUTS:
                self.is_blocked = True
                return None, f"타임아웃 차단 감지 (연속 {MAX_CONSECUTIVE_TIMEOUTS}회)"
            return None, "요청 타임아웃"

        except requests.exceptions.RequestException as e:
            self.request_count += 1
            self.consecutive_timeout_count = 0
            error_msg = str(e)
            if '403' in error_msg:
                self.is_blocked = True
                return None, "접근 차단됨 (403)"
            return None, f"요청 오류: {error_msg}"

    def close(self):
        if self.session:
            self.session.close()

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
            # size_notice 태그 제거 후 텍스트 추출 (품절 임박 제외)
            tag_size_elem = cols[1]
            for notice in tag_size_elem.select('.size_notice'):
                notice.decompose()
            tag_size = tag_size_elem.get_text(strip=True)

            option = {
                'color': cols[0].get_text(strip=True),
                'tag_size': tag_size,
                'real_size': cols[2].get_text(strip=True),
                'option_code': option_code,
                'status': 'in_stock'
            }
            # 품절 임박이 아닌 실제 품절만 확인
            row_text = row.get_text()
            if '품절' in row_text and '품절 임박' not in row_text:
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

                # 숫자 접두사 제거 (예: "① 가로" → "가로")
                label = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩\d\.\s]+', '', label).strip()
                if label and value:
                    size_data[label] = value

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

    # model_id 없으면 이후 단계(PRICE/IMAGE/REGISTER) 진행 불가 → 스킵
    if not model_id:
        return None

    mall_product_id = str(product_ld.get('sku', ''))
    if not mall_product_id:
        match = re.search(r'no=(\d+)', product_url)
        if match:
            mall_product_id = match.group(1)

    # raw_json_data 구성
    raw_json_data = {
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

def get_published_product_ids(brand_name: str = None) -> set:
    """등록 완료된 상품의 mall_product_id 목록 조회"""
    with engine.connect() as conn:
        query = """
            SELECT r.mall_product_id 
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = 'okmall' 
            AND a.is_published = 1
        """
        if brand_name:
            query += " AND (UPPER(r.brand_name_en) = :brand OR UPPER(r.brand_name_kr) = :brand)"
            result = conn.execute(text(query), {"brand": brand_name.upper()})
        else:
            result = conn.execute(text(query))
        return {str(r[0]) for r in result}

def get_product_urls_from_list(base_url: str, session_mgr: OkmallSessionManager, limit: int = None) -> List[str]:
    all_urls = []
    page = 1
    max_pages = 100

    while page <= max_pages:
        if session_mgr.is_blocked:
            logger.error("  차단 감지됨 — 목록 수집 중단")
            break

        page_url = f"{base_url}&page={page}" if '?' in base_url else f"{base_url}?page={page}"
        html, error = session_mgr.fetch_page(page_url)
        if error:
            logger.warning(f"  목록 페이지 {page} 수집 실패: {error}")
            if session_mgr.is_blocked:
                break
            page += 1
            continue
        if not html:
            break

        soup = BeautifulSoup(html, 'html.parser')
        product_boxes = soup.select('.item_box[data-productno]')
        if not product_boxes:
            break

        scratch_count = 0
        for box in product_boxes:
            # 흠집특가상품 제외 (item_scratch 클래스)
            if 'item_scratch' in box.get('class', []):
                scratch_count += 1
                continue
            product_no = box.get('data-productno')
            if product_no:
                all_urls.append(f"https://www.okmall.com/products/view?no={product_no}")
        if scratch_count > 0:
            logger.info(f"  흠집특가 제외: {scratch_count}개")

        if limit and len(all_urls) >= limit:
            all_urls = all_urls[:limit]
            break

        page += 1
        time.sleep(random.uniform(0.5, 1.0))

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
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품만 스킵 (신규+미등록 상품 수집)')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"오케이몰 통합 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    logger.info(f"  세션 교체 주기: {SESSION_REFRESH_INTERVAL}개마다")
    logger.info(f"  타임아웃 차단 감지: 연속 {MAX_CONSECUTIVE_TIMEOUTS}회")
    if args.skip_existing:
        logger.info("  신규+미등록 상품 수집 모드 (--skip-existing)")
    logger.info("=" * 60)

    # ★ 세션 매니저 생성
    session_mgr = OkmallSessionManager()

    brands = get_brands_from_database(args.brand)
    logger.info(f"대상 브랜드: {len(brands)}개")

    for brand in brands:
        # ★ 차단 감지 시 전체 중단
        if session_mgr.is_blocked:
            logger.error("IP 차단 감지됨! 비행기모드 토글 필요 — 수집 중단")
            break

        logger.info(f"\n>>> 브랜드 시작: {brand['name']}")
        product_urls = get_product_urls_from_list(brand['url'], session_mgr, limit=args.limit)
        logger.info(f"발견된 상품: {len(product_urls)}개")

        if session_mgr.is_blocked:
            logger.error("IP 차단 감지됨! 비행기모드 토글 필요 — 수집 중단")
            break

        # skip-existing 옵션이 활성화된 경우 등록 완료 상품만 필터링
        if args.skip_existing:
            published_ids = get_published_product_ids(brand['name'])
            logger.info(f"등록 완료 상품: {len(published_ids)}개 (스킵 대상)")

            # URL에서 mall_product_id 추출해서 필터링
            new_urls = []
            for url in product_urls:
                match = re.search(r'no=(\d+)', url)
                if match:
                    product_id = match.group(1)
                    if product_id not in published_ids:
                        new_urls.append(url)
                else:
                    new_urls.append(url)

            skipped_count = len(product_urls) - len(new_urls)
            product_urls = new_urls
            logger.info(f"수집 대상: {len(product_urls)}개 (신규+미등록), 스킵: {skipped_count}개 (등록완료)")

        batch_data = []
        for idx, url in enumerate(product_urls, 1):
            # ★ 차단 감지 시 즉시 중단
            if session_mgr.is_blocked:
                logger.error("IP 차단 감지됨! 비행기모드 토글 필요 — 수집 중단")
                break

            try:
                html, error = session_mgr.fetch_page(url)

                if error:
                    if session_mgr.is_blocked:
                        logger.error(f"  [{idx}/{len(product_urls)}] 차단됨: {error}")
                        break
                    logger.warning(f"  [{idx}/{len(product_urls)}] 수집 실패: {error}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                if html:
                    data = extract_product_data(html, url)
                    if data:
                        pid = data.get('mall_product_id', '?')
                        if args.dry_run:
                            logger.info(f"  [{idx}/{len(product_urls)}] [DRY-RUN] 추출 성공: pid={pid}, {data['product_name']}")
                        else:
                            batch_data.append(data)
                            logger.info(f"  [{idx}/{len(product_urls)}] 추출 완료: pid={pid}, {data['product_name']}")

                if len(batch_data) >= 10:  # 10개 단위 저장
                    save_to_database(batch_data)
                    batch_data = []

                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            except Exception as e:
                logger.error(f"  [{idx}/{len(product_urls)}] 오류: {e}")

        if batch_data and not args.dry_run:
            save_to_database(batch_data)

    # ★ 세션 정리
    session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info("모든 브랜드 수집 완료")
    if session_mgr.is_blocked:
        logger.warning("⚠ 차단으로 인해 일부 브랜드가 수집되지 않았을 수 있습니다")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
