# -*- coding: utf-8 -*-
"""
트렌드메카(trendmecca.co.kr) 상품 수집 스크립트
- Cafe24 기반 쇼핑몰 → HTML 스크래핑
- 리스트 페이지: /product/list.html?cate_no={mall_brand_no}&page={n}
- 상세 페이지: /product/{slug}/{product_no}/category/{cate_no}/display/1/
- raw_scraped_data 테이블에 source_site='trendmecca'로 저장

사용법:
    python trendmecca_collector.py                        # 전체 실행
    python trendmecca_collector.py --brand "AMI"          # 특정 브랜드만
    python trendmecca_collector.py --limit 10             # 브랜드당 최대 10개
    python trendmecca_collector.py --dry-run              # DB 저장 없이 테스트
    python trendmecca_collector.py --skip-existing        # 등록 완료 상품 스킵

★ 봇 감지 방지:
- 30개마다 세션 교체 + 메인 페이지 방문
- 랜덤 브라우저 프로필
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
# 상수
# ===========================================

BASE_URL = 'https://trendmecca.co.kr'
SOURCE_SITE = 'trendmecca'
SESSION_REFRESH_INTERVAL = 30
MAX_CONSECUTIVE_TIMEOUTS = 5
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.8

BROWSER_PROFILES = [
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
]


# ===========================================
# 세션 관리
# ===========================================

class SessionManager:
    def __init__(self):
        self.session = None
        self.profile = None
        self.request_count = 0
        self.consecutive_timeout_count = 0
        self.is_blocked = False

    def _create_new_session(self) -> Tuple[bool, Optional[str]]:
        try:
            if self.session:
                self.session.close()

            self.session = requests.Session()
            self.profile = random.choice(BROWSER_PROFILES).copy()

            main_headers = self.profile.copy()
            main_headers['Referer'] = 'https://www.google.com/'
            main_headers['Sec-Fetch-Site'] = 'cross-site'
            self.session.headers.update(main_headers)

            logger.info(f"  [세션] 새 세션 시작 - 메인 페이지 방문 중...")
            response = self.session.get(f'{BASE_URL}/index_time.html', timeout=15)

            if response.status_code != 200:
                return False, f"메인 페이지 접속 실패: {response.status_code}"

            product_headers = self.profile.copy()
            product_headers['Referer'] = f'{BASE_URL}/'
            product_headers['Sec-Fetch-Site'] = 'same-origin'
            self.session.headers.update(product_headers)

            self.request_count = 0
            time.sleep(random.uniform(0.5, 1.5))

            logger.info(f"  [세션] 새 세션 준비 완료 (쿠키 획득됨)")
            return True, None

        except requests.exceptions.Timeout:
            return False, "메인 페이지 타임아웃"
        except Exception as e:
            return False, f"세션 생성 오류: {str(e)}"

    def fetch_page(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        if self.is_blocked:
            return None, "차단됨"

        if self.session is None or self.request_count >= SESSION_REFRESH_INTERVAL:
            success, error = self._create_new_session()
            if not success:
                return None, error

        try:
            response = self.session.get(url, timeout=30)
            self.request_count += 1

            if response.status_code == 403:
                self.is_blocked = True
                return None, "접근 차단됨 (403)"

            response.raise_for_status()
            self.consecutive_timeout_count = 0
            return response.text, None

        except requests.exceptions.Timeout:
            self.consecutive_timeout_count += 1
            logger.warning(f"  [타임아웃] 연속 {self.consecutive_timeout_count}회")
            if self.consecutive_timeout_count >= MAX_CONSECUTIVE_TIMEOUTS:
                self.is_blocked = True
                return None, "타임아웃 차단 감지"
            return None, "요청 타임아웃"
        except requests.exceptions.RequestException as e:
            self.consecutive_timeout_count = 0
            return None, f"요청 오류: {str(e)}"

    def close(self):
        if self.session:
            self.session.close()


# ===========================================
# 리스트 페이지 파싱
# ===========================================

def get_product_list_from_page(html: str, cate_no: str) -> List[Dict]:
    """리스트 페이지 HTML에서 상품 기본 정보 추출

    트렌드메카 리스트 구조:
    - <li id="anchorBoxId_{product_no}">
    - 상품명: div.name > a > span (displaynone 제거)
    - 가격: div.description 속성 ec-data-custom (소비자가), ec-data-price (판매가)
    - 이미지: div.thumbnail > a > img
    - 상품 URL: div.name > a href
    """
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    items = soup.select('ul.prdList li[id^="anchorBoxId_"]')
    for item in items:
        try:
            # product_no
            item_id = item.get('id', '')
            product_no = item_id.replace('anchorBoxId_', '')
            if not product_no:
                continue

            # 상품명
            name_elem = item.select_one('div.name a')
            raw_name = ''
            if name_elem:
                for hidden in name_elem.select('.displaynone'):
                    hidden.decompose()
                raw_name = name_elem.get_text(strip=True)

            # "타임메카" / "트렌드메카" 접미어 제거
            product_name = re.sub(r'\s*(타임메카|트렌드메카)\s*$', '', raw_name).strip()

            # 상품 상세 URL (slug 포함된 원본 href)
            detail_href = ''
            link_elem = item.select_one('div.name a')
            if link_elem:
                detail_href = link_elem.get('href', '')

            # 가격 — div.description 속성에서 추출
            desc_elem = item.select_one('div.description')
            original_price = 0
            sale_price = 0
            if desc_elem:
                custom = desc_elem.get('ec-data-custom', '')
                price = desc_elem.get('ec-data-price', '')
                if custom:
                    original_price = int(re.sub(r'[^0-9]', '', custom) or 0)
                if price:
                    sale_price = int(re.sub(r'[^0-9]', '', price) or 0)

            # fallback: li.price01 (판매가), li.price02 (정가)
            if not sale_price:
                price01 = item.select_one('li.price01')
                if price01:
                    p_text = re.sub(r'[^0-9]', '', price01.get_text(strip=True))
                    if p_text:
                        sale_price = int(p_text)
            if not original_price:
                price02 = item.select_one('li.price02')
                if price02:
                    p_text = re.sub(r'[^0-9]', '', price02.get_text(strip=True))
                    if p_text:
                        original_price = int(p_text)

            # 이미지
            img_elem = item.select_one('div.thumbnail a img')
            image_url = ''
            if img_elem:
                image_url = img_elem.get('src', '')
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url

            products.append({
                'product_no': product_no,
                'product_name': product_name,
                'original_price': original_price,
                'sale_price': sale_price,
                'image_url': image_url,
                'detail_href': detail_href,
                'cate_no': cate_no,
            })
        except Exception as e:
            logger.warning(f"  리스트 아이템 파싱 오류: {e}")
            continue

    return products


def get_last_page(html: str) -> int:
    """페이지네이션에서 마지막 페이지 번호 추출"""
    soup = BeautifulSoup(html, 'html.parser')

    # .last 링크에서 마지막 페이지 추출
    last_link = soup.select_one('.xans-product-normalpaging a.last')
    if last_link:
        href = last_link.get('href', '')
        match = re.search(r'page=(\d+)', href)
        if match:
            return int(match.group(1))

    # fallback: 모든 페이지 링크에서 최대값
    paging = soup.select('.xans-product-normalpaging ol li a')
    max_page = 1
    for a in paging:
        href = a.get('href', '')
        match = re.search(r'page=(\d+)', href)
        if match:
            page_num = int(match.group(1))
            if page_num > max_page:
                max_page = page_num
    return max_page


# ===========================================
# 상세 페이지 파싱
# ===========================================

def extract_size_from_option_value(option_value: str) -> str:
    """option_value에서 사이즈 추출
    "USW247 730 0951 (M)" → "M"
    "단일사이즈" → "FREE"
    """
    # 괄호 안의 값 추출
    match = re.search(r'\(([^)]+)\)\s*$', option_value)
    if match:
        size = match.group(1).strip()
    else:
        size = option_value.strip()

    # 단일사이즈 → FREE
    if size in ['단일사이즈', '단일 사이즈', 'ONE SIZE', 'ONESIZE', '원사이즈']:
        size = 'FREE'

    return size


def extract_detail_info(html: str) -> Dict[str, Any]:
    """상세 페이지에서 모델번호, 옵션, 이미지 등 추출

    트렌드메카 상세 페이지 특징:
    - 상품 정보 테이블: tr[rel="모델명"], tr[rel="브랜드"], tr[rel="제조국"]
    - 옵션: option_stock_data JS 변수 (JSON)
    - 이미지: xans-product-addimage 영역 ThumbImage
    - 실측정보/소재 없음
    """
    soup = BeautifulSoup(html, 'html.parser')
    info = {
        'model_id': '',
        'origin': '',
        'color': '',
        'options': [],
        'images': [],
    }

    # 상품 정보 테이블 파싱 (tr[rel="..."] 구조)
    # td > span 텍스트만 추출 (카드혜택 모달 등 하위 요소 오염 방지)
    for row in soup.select('.xans-product-detaildesign tr'):
        rel = row.get('rel', '')
        td = row.select_one('td')
        if not rel or not td:
            continue
        span = td.select_one('span')
        value = span.get_text(strip=True) if span else td.get_text(strip=True)

        if rel == '모델명' and value:
            info['model_id'] = value
        elif rel == '제조국' and value:
            info['origin'] = value

    # model_id fallback: 모든 tr에서 th 텍스트 기반 검색
    if not info['model_id']:
        for row in soup.select('tr'):
            th = row.select_one('th')
            td = row.select_one('td')
            if not th or not td:
                continue
            header = th.get_text(strip=True).replace(' ', '')
            if '모델명' in header or header == '모델':
                value = td.get_text(strip=True)
                if value:
                    info['model_id'] = value
                    break

    # option_stock_data JS 변수 파싱 (재고 정보)
    stock_match = re.search(r"option_stock_data\s*=\s*'(.*?)'", html, re.DOTALL)
    if stock_match:
        try:
            raw_stock = stock_match.group(1).replace('\\"', '"')
            stock_data = json.loads(raw_stock)

            for opt_code, opt_info in stock_data.items():
                opt_value = opt_info.get('option_value', '').strip()
                is_selling = opt_info.get('is_selling', 'F') == 'T'
                is_display = opt_info.get('is_display', 'F') == 'T'
                stock_num = opt_info.get('stock_number', 0)

                tag_size = extract_size_from_option_value(opt_value)
                in_stock = is_selling and is_display and stock_num > 0

                info['options'].append({
                    'color': '',
                    'tag_size': tag_size,
                    'option_code': opt_code,
                    'status': 'in_stock' if in_stock else 'out_of_stock',
                })
        except (json.JSONDecodeError, AttributeError):
            pass

    # fallback: select 옵션
    if not info['options']:
        option_select = soup.select_one('select[option_title]')
        if not option_select:
            option_select = soup.select_one('select#product_option_id1')
        if option_select:
            for opt in option_select.select('option'):
                opt_value = opt.get('value', '')
                if not opt_value or opt_value == '*':
                    continue
                opt_text = opt.get_text(strip=True)
                if re.match(r'^[-=]{3,}$', opt_text.strip()):
                    continue

                is_soldout = '품절' in opt_text or opt.get('disabled') is not None
                clean_size = re.sub(r'\s*\[품절\]\s*', '', opt_text).strip()
                clean_size = extract_size_from_option_value(clean_size)

                info['options'].append({
                    'color': '',
                    'tag_size': clean_size,
                    'option_code': opt_value,
                    'status': 'out_of_stock' if is_soldout else 'in_stock',
                })

    # 상품 이미지 — ThumbImage
    thumb_area = soup.select_one('.xans-product-addimage')
    if thumb_area:
        for img in thumb_area.select('img.ThumbImage'):
            src = img.get('src', '')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                if src not in info['images']:
                    info['images'].append(src)

    # JS 변수에서 가격 추출
    sale_match = re.search(r"product_sale_price\s*=\s*(\d+)", html)
    if sale_match:
        info['sale_price_js'] = int(sale_match.group(1))

    original_match = re.search(r"product_price\s*=\s*'(\d+)'", html)
    if original_match:
        info['original_price_js'] = int(original_match.group(1))

    return info


# ===========================================
# 데이터 변환
# ===========================================

def convert_to_raw_data(list_item: Dict, detail_info: Dict, brand_name_en: str, brand_name_ko: str, category_path: str = '') -> Optional[Dict]:
    """리스트 + 상세 데이터를 raw_scraped_data 형식으로 변환"""

    product_no = list_item['product_no']
    product_name = list_item.get('product_name', '')

    # 모델번호: 상세 페이지 테이블 우선
    model_id = detail_info.get('model_id', '')

    # model_id 없으면 스킵
    if not model_id:
        return None

    # 가격: 상세 페이지 JS 변수 우선, fallback으로 리스트 페이지 값
    original_price = detail_info.get('original_price_js', list_item.get('original_price', 0))
    sale_price = detail_info.get('sale_price_js', list_item.get('sale_price', 0))
    if not sale_price:
        sale_price = original_price

    # 재고 상태
    options = detail_info.get('options', [])
    stock_status = 'out_of_stock'
    if any(opt.get('status') == 'in_stock' for opt in options):
        stock_status = 'in_stock'
    elif not options:
        stock_status = 'in_stock'

    # raw_json_data
    raw_json = {
        'origin': detail_info.get('origin', ''),
        'options': options,
        'images': detail_info.get('images', []),
        'cate_no': list_item.get('cate_no', ''),
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    # product_url: 리스트에서 가져온 slug URL 우선, 없으면 detail.html 형식
    detail_href = list_item.get('detail_href', '')
    if detail_href:
        if detail_href.startswith('/'):
            product_url = f"{BASE_URL}{detail_href}"
        else:
            product_url = detail_href
    else:
        product_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={list_item.get('cate_no', '')}"

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': brand_name_en,
        'brand_name_kr': brand_name_ko,
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': category_path,
        'original_price': original_price,
        'raw_price': sale_price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': product_url,
    }


# ===========================================
# DB 조회/저장
# ===========================================

def get_brands_from_database(brand_filter: str = None) -> List[Dict]:
    with engine.connect() as conn:
        query = "SELECT mall_brand_name_en, mall_brand_name_ko, mall_brand_no FROM mall_brands WHERE mall_name = 'trendmecca' AND is_active = 1"
        params = {}
        if brand_filter:
            query += " AND UPPER(mall_brand_name_en) = :brand"
            params['brand'] = brand_filter.upper()
        result = conn.execute(text(query), params)
        return [{'name_en': r[0], 'name_ko': r[1], 'cate_no': r[2]} for r in result]


def get_categories_from_database() -> List[Dict]:
    """mall_categories에서 trendmecca 카테고리 목록 조회"""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT category_id, full_path FROM mall_categories WHERE mall_name = 'trendmecca' AND is_active = 1"
        ))
        return [{'cate_no': r[0], 'full_path': r[1]} for r in result]


def build_category_map(session_mgr: 'SessionManager') -> Dict[str, str]:
    """카테고리 페이지를 순회하여 {product_no: full_path} 매핑 구축"""
    categories = get_categories_from_database()
    logger.info(f"카테고리 매핑 시작: {len(categories)}개 카테고리")

    category_map = {}  # {product_no: full_path}

    for cat_idx, cat in enumerate(categories, 1):
        if session_mgr.is_blocked:
            logger.error("  차단 감지됨 — 카테고리 매핑 중단")
            break

        cate_no = cat['cate_no']
        full_path = cat['full_path']

        # 첫 페이지
        url = f"{BASE_URL}/product/list.html?cate_no={cate_no}&page=1"
        html, error = session_mgr.fetch_page(url)
        if error:
            logger.warning(f"  [{cat_idx}/{len(categories)}] {full_path} 수집 실패: {error}")
            continue
        if not html:
            continue

        last_page = get_last_page(html)
        items = get_product_list_from_page(html, cate_no)
        product_nos = [item['product_no'] for item in items]

        # 나머지 페이지
        for page in range(2, last_page + 1):
            if session_mgr.is_blocked:
                break
            page_url = f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}"
            page_html, error = session_mgr.fetch_page(page_url)
            if error:
                continue
            if not page_html:
                break
            items = get_product_list_from_page(page_html, cate_no)
            if not items:
                break
            product_nos.extend(item['product_no'] for item in items)
            time.sleep(random.uniform(0.2, 0.5))

        # 매핑 등록 (이미 있으면 덮어쓰지 않음 — 첫 매칭 우선)
        new_count = 0
        for pno in product_nos:
            if pno not in category_map:
                category_map[pno] = full_path
                new_count += 1

        logger.info(f"  [{cat_idx}/{len(categories)}] {full_path} | {len(product_nos)}개 상품, 신규 매핑 {new_count}개")
        time.sleep(random.uniform(0.2, 0.5))

    logger.info(f"카테고리 매핑 완료: 총 {len(category_map)}개 상품 매핑됨")
    return category_map


def update_categories_in_db(category_map: Dict[str, str]):
    """기존 raw_scraped_data의 category_path를 업데이트"""
    if not category_map:
        return

    updated = 0
    with engine.connect() as conn:
        for product_no, full_path in category_map.items():
            result = conn.execute(text("""
                UPDATE raw_scraped_data
                SET category_path = :path
                WHERE source_site = 'trendmecca' AND mall_product_id = :pid AND (category_path IS NULL OR category_path = '')
            """), {'path': full_path, 'pid': product_no})
            if result.rowcount > 0:
                updated += result.rowcount
        conn.commit()
    logger.info(f"기존 데이터 category_path 업데이트: {updated}건")


def get_published_product_ids(brand_name: str = None) -> set:
    """등록 완료된 상품의 mall_product_id 목록 조회"""
    with engine.connect() as conn:
        query = """
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = 'trendmecca'
            AND a.is_published = 1
        """
        if brand_name:
            query += " AND (UPPER(r.brand_name_en) = :brand OR UPPER(r.brand_name_kr) = :brand)"
            result = conn.execute(text(query), {"brand": brand_name.upper()})
        else:
            result = conn.execute(text(query))
        return {str(r[0]) for r in result}


def save_to_database(data_list: List[Dict]):
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

def main():
    parser = argparse.ArgumentParser(description='트렌드메카 상품 수집기')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='브랜드당 최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    parser.add_argument('--update-categories', action='store_true', help='기존 데이터의 category_path만 업데이트')
    args = parser.parse_args()

    logger.info("=" * 60)

    session_mgr = SessionManager()

    # --update-categories: 카테고리 매핑만 수행 후 종료
    if args.update_categories:
        logger.info("트렌드메카 카테고리 업데이트 모드")
        logger.info("=" * 60)
        try:
            category_map = build_category_map(session_mgr)
            update_categories_in_db(category_map)
        finally:
            session_mgr.close()
        logger.info("=" * 60)
        return

    logger.info(f"트렌드메카 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    if args.skip_existing:
        logger.info("  신규+미등록 상품 수집 모드 (--skip-existing)")
    logger.info("=" * 60)

    # 카테고리 맵 먼저 구축
    try:
        category_map = build_category_map(session_mgr)
    except Exception as e:
        logger.warning(f"카테고리 맵 구축 실패: {e} — category_path 빈 값으로 진행")
        category_map = {}

    brands = get_brands_from_database(args.brand)
    logger.info(f"대상 브랜드: {len(brands)}개")

    if not brands:
        logger.info("수집할 브랜드가 없습니다.")
        session_mgr.close()
        return

    total_collected = 0
    total_skipped_no_model = 0

    try:
        for brand_idx, brand in enumerate(brands, 1):
            brand_name_en = brand['name_en']
            brand_name_ko = brand['name_ko']
            cate_no = brand['cate_no']

            logger.info(f"\n>>> [{brand_idx}/{len(brands)}] 브랜드: {brand_name_en} (cate_no={cate_no})")

            if session_mgr.is_blocked:
                logger.error("  차단 감지됨 — 수집 중단")
                break

            # 1) 브랜드 리스트 첫 페이지
            brand_url = f"{BASE_URL}/product/list.html?cate_no={cate_no}&page=1"
            html, error = session_mgr.fetch_page(brand_url)
            if error:
                logger.warning(f"  브랜드 페이지 수집 실패: {error}")
                continue
            if not html:
                continue

            last_page = get_last_page(html)
            all_list_items = get_product_list_from_page(html, cate_no)

            # 나머지 페이지
            for page in range(2, last_page + 1):
                if session_mgr.is_blocked:
                    break
                page_url = f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}"
                page_html, error = session_mgr.fetch_page(page_url)
                if error:
                    continue
                if not page_html:
                    break
                items = get_product_list_from_page(page_html, cate_no)
                if not items:
                    break
                all_list_items.extend(items)
                time.sleep(random.uniform(0.3, 0.8))

            # product_no 기준 dedup
            seen = set()
            deduped = []
            for item in all_list_items:
                if item['product_no'] not in seen:
                    seen.add(item['product_no'])
                    deduped.append(item)
            all_list_items = deduped

            logger.info(f"  리스트 수집 완료: {len(all_list_items)}개 ({last_page}페이지)")

            if not all_list_items:
                continue

            # limit 적용
            if args.limit and len(all_list_items) > args.limit:
                all_list_items = all_list_items[:args.limit]

            # skip-existing
            if args.skip_existing:
                published_ids = get_published_product_ids(brand_name_en)
                before = len(all_list_items)
                all_list_items = [item for item in all_list_items if item['product_no'] not in published_ids]
                skipped = before - len(all_list_items)
                if skipped > 0:
                    logger.info(f"  등록 완료 스킵: {skipped}개, 수집 대상: {len(all_list_items)}개")

            # 2) 상세 페이지 수집 + 변환 + 저장
            batch_data = []
            skipped_no_model = 0
            total = len(all_list_items)

            for idx, list_item in enumerate(all_list_items, 1):
                if session_mgr.is_blocked:
                    logger.error("  차단 감지됨 — 상세 수집 중단")
                    break

                product_no = list_item['product_no']

                # 상세 URL: 리스트에서 가져온 slug URL 우선
                detail_href = list_item.get('detail_href', '')
                if detail_href:
                    if detail_href.startswith('/'):
                        detail_url = f"{BASE_URL}{detail_href}"
                    else:
                        detail_url = detail_href
                else:
                    detail_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={cate_no}&display_group=1"

                detail_html, error = session_mgr.fetch_page(detail_url)
                if error:
                    logger.warning(f"  [{idx}/{total}] 상세 수집 실패: {error} | {list_item['product_name'][:30]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                detail_info = extract_detail_info(detail_html) if detail_html else {}

                category_path = category_map.get(product_no, '')
                data = convert_to_raw_data(list_item, detail_info, brand_name_en, brand_name_ko, category_path)
                if not data:
                    skipped_no_model += 1
                    logger.info(f"  [{idx}/{total}] SKIP (no model_id) | {list_item['product_name'][:50]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                logger.info(f"  [{idx}/{total}] {data['model_id']} | {data['raw_price']:>12,}원 | {data['product_name'][:40]}")
                total_collected += 1

                if not args.dry_run:
                    batch_data.append(data)

                # 10개 단위 배치 저장
                if len(batch_data) >= 10:
                    save_to_database(batch_data)
                    logger.info(f"  DB 저장: {len(batch_data)}개")
                    batch_data = []

                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            # 잔여분 저장
            if batch_data and not args.dry_run:
                save_to_database(batch_data)
                logger.info(f"  DB 저장(잔여): {len(batch_data)}개")

            total_skipped_no_model += skipped_no_model
            logger.info(f"  {brand_name_en} 완료: model_id 없어서 스킵 {skipped_no_model}개")

    finally:
        session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info(f"트렌드메카 수집 완료")
    logger.info(f"  총 수집: {total_collected}개")
    logger.info(f"  model_id 없어서 스킵: {total_skipped_no_model}개")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :site"
            ), {'site': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 trendmecca 상품: {count}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
