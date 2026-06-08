# -*- coding: utf-8 -*-
"""
로로모다(loromoda.net) 상품 수집 스크립트
- Cafe24 기반 쇼핑몰 → HTML 스크래핑 (9tems와 가장 가까운 계열)
- 브랜드별 순회 방식 (mall_brands.mall_brand_url = /category/{name}/{cate_no}/)
  · 리스트: {brand_url}?page={n}
  · 상세:   /product/{slug}/{product_no}/category/{cate_no}/display/1/  (리스트 a href 그대로)
  · 브랜드명은 순회 루프에서 확정 (영문)
- 상세 파싱은 인라인 JS 변수 우선 (var product_name / product_price / option_stock_data ...)
- raw_scraped_data 테이블에 source_site='loromoda'로 저장
- 이미지는 raw_json_data.images에 저장 → ace_product_images 이관은 raw→ace 변환 단계에서

사용법:
    python loromoda_collector.py                       # 전체 브랜드
    python loromoda_collector.py --brand "CELINE"      # 특정 브랜드만
    python loromoda_collector.py --limit 10            # 브랜드당 최대 10개
    python loromoda_collector.py --dry-run             # DB 저장 없이 테스트
    python loromoda_collector.py --skip-existing       # 등록 완료 상품 스킵

★ 봇 감지 방지: labellusso 수집기와 동일 (30개마다 세션 교체 + 메인 방문, 랜덤 프로필)
"""

import os
import re
import json
import time
import random
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from collections import Counter

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

BASE_URL = 'https://loromoda.net'
SOURCE_SITE = 'loromoda'
SESSION_REFRESH_INTERVAL = 30
MAX_CONSECUTIVE_TIMEOUTS = 5
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.8

# 상세 본문 이미지: 사방넷상품등록 경로(EUC-KR 인코딩)가 실제 상품사진
SABANG_MARKER = '%BB%E7%B9%E6%B3%DD%BB%F3%C7%B0%B5%EE%B7%CF'  # 사방넷상품등록

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
# 세션 관리 (labellusso 수집기와 동일)
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

            logger.info("  [세션] 새 세션 시작 - 메인 페이지 방문 중...")
            response = self.session.get(f'{BASE_URL}/index.html', timeout=15)

            if response.status_code != 200:
                return False, f"메인 페이지 접속 실패: {response.status_code}"

            product_headers = self.profile.copy()
            product_headers['Referer'] = f'{BASE_URL}/'
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
# 공통 유틸
# ===========================================

def _abs_url(src: str) -> str:
    if not src:
        return ''
    if src.startswith('//'):
        return 'https:' + src
    if src.startswith('/'):
        return BASE_URL + src
    return src


def _parse_price(text_value: str) -> int:
    if not text_value:
        return 0
    m = re.search(r'[\d,]{2,}', text_value)
    return int(m.group().replace(',', '')) if m else 0


def clean_product_name(name: str) -> str:
    """`[로로모다]` 접두어 제거 + 공백 정리"""
    name = re.sub(r'^\s*\[로로모다\]\s*', '', name or '').strip()
    return re.sub(r'\s+', ' ', name).strip()


def extract_model_id_from_name(product_name: str) -> str:
    """상품명 끝 ` / MODEL` 형식에서 모델번호 추출

    예) "생로랑 마틀라세 여성 클러치백 / 651030-BOW91-1000" → "651030-BOW91-1000"
        "몽클레르 티마 ... / TIMAH-1A00003-268"            → "TIMAH-1A00003-268"
    """
    if not product_name:
        return ''
    if '/' in product_name:
        tail = product_name.rsplit('/', 1)[-1].strip()
        if re.search(r'\d', tail) and re.fullmatch(r'[A-Za-z0-9\-_. ]+', tail):
            return tail
    # fallback: 뒤에서부터 한글 아닌 토큰 연속 수집
    tokens = product_name.strip().split()
    trailing = []
    for tok in reversed(tokens):
        if re.search(r'[가-힣]', tok):
            break
        trailing.append(tok)
    trailing.reverse()
    model_id = ' '.join(trailing).strip()
    return model_id if re.search(r'\d', model_id) else ''


# ===========================================
# 리스트 페이지 파싱
# ===========================================

def get_product_list_from_page(html: str) -> List[Dict]:
    """리스트 페이지 HTML에서 상품 카드 추출

    loromoda(Cafe24) 리스트 구조:
    - <ul class="prdList ..."> > <li id="anchorBoxId_{product_no}">
    - 상세 링크 & 상품명: div.name > a  (마지막 span 텍스트, [로로모다] 접두어 포함)
    - 제조사(한글): ul.spec li:first span:last
    - 가격: div[ec-data-custom], div[ec-data-price]
    - 이미지: a img[id^="eListPrdImage"]
    """
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    items = soup.select('ul.prdList li[id^="anchorBoxId_"]')
    for item in items:
        try:
            item_id = item.get('id', '')
            product_no = item_id.replace('anchorBoxId_', '')
            if not product_no:
                continue

            name_a = item.select_one('div.name a')
            detail_path = name_a.get('href', '') if name_a else ''
            # 쿼리스트링(?icid=...) 제거
            detail_path = detail_path.split('?')[0]
            detail_url = _abs_url(detail_path)

            product_name = ''
            if name_a:
                for hidden in name_a.select('.title, .displaynone'):
                    hidden.decompose()
                product_name = re.sub(r'\s+', ' ', name_a.get_text(strip=True)).strip()

            # 제조사(한글 브랜드) — 참고용
            brand_ko = ''
            spec_li = item.select_one('ul.spec li')
            if spec_li:
                for hidden in spec_li.select('.title, .displaynone'):
                    hidden.decompose()
                brand_ko = spec_li.get_text(strip=True)

            # 가격
            desc = item.select_one('[ec-data-price]')
            sale_price = _parse_price(desc.get('ec-data-price', '')) if desc else 0
            custom = item.select_one('[ec-data-custom]')
            original_price = _parse_price(custom.get('ec-data-custom', '')) if custom else 0

            # 이미지
            img_elem = item.select_one('a img[id^="eListPrdImage"]') or item.select_one('.thumbnail img')
            image_url = _abs_url(img_elem.get('src', '')) if img_elem else ''

            products.append({
                'product_no': product_no,
                'product_name': product_name,
                'brand_ko': brand_ko,
                'detail_url': detail_url,
                'list_image': image_url,
                'original_price': original_price,
                'sale_price': sale_price,
            })
        except Exception as e:
            logger.warning(f"  리스트 아이템 파싱 오류: {e}")
            continue

    return products


def get_last_page(html: str) -> int:
    soup = BeautifulSoup(html, 'html.parser')
    max_page = 1
    for a in soup.select('.xans-product-normalpaging a'):
        m = re.search(r'page=(\d+)', a.get('href', ''))
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


# ===========================================
# 상세 페이지 파싱 (인라인 JS 변수 우선)
# ===========================================

def _js_var(html: str, name: str) -> str:
    m = re.search(rf"var\s+{name}\s*=\s*'([^']*)'", html)
    return m.group(1) if m else ''


def extract_detail_info(html: str, model_id_hint: str = '') -> Dict[str, Any]:
    """상세 페이지에서 상품명/가격/옵션/이미지 추출

    loromoda(Cafe24) 상세 구조:
    - JS 변수: product_name / product_price(판매가) / is_soldout_icon / option_stock_data(JSON)
    - 소비자가: #span_product_price_custom strike
    - 이미지 3소스: detailArea BigImage + addimage ThumbImage(/small/→/big/) + #prdDetail 사방넷 CDN
    """
    soup = BeautifulSoup(html, 'html.parser')
    info = {
        'product_name': '',
        'price': 0,
        'original_price': 0,
        'options': [],
        'images': [],
        'sold_out': False,
    }

    # 상품명 (JS var 우선)
    info['product_name'] = _js_var(html, 'product_name')

    # 판매가 (JS var) / 소비자가 (DOM strike)
    price_var = _js_var(html, 'product_price')
    info['price'] = int(price_var) if price_var.isdigit() else 0
    if not info['price']:
        pe = soup.select_one('#span_product_price_text')
        if pe:
            info['price'] = _parse_price(pe.get_text())
    custom_elem = soup.select_one('#span_product_price_custom strike') or soup.select_one('#span_product_price_custom')
    if custom_elem:
        info['original_price'] = _parse_price(custom_elem.get_text())

    # 품절 여부
    info['sold_out'] = _js_var(html, 'is_soldout_icon') == 'T'

    # 옵션 — option_stock_data JSON (JS 문자열이라 \" 로 이스케이프됨 → 언이스케이프 후 파싱)
    m = re.search(r"var\s+option_stock_data\s*=\s*'([^']*)'", html)
    if m:
        try:
            stock_data = json.loads(m.group(1).replace('\\"', '"'))
            for opt_code, opt in stock_data.items():
                opt_value = (opt.get('option_value', '') or '').strip()
                stock_number = int(opt.get('stock_number', 0) or 0)
                is_selling = opt.get('is_selling', 'T') == 'T'
                in_stock = is_selling and stock_number > 0 and not info['sold_out']
                if opt_value in ('단일사이즈', '단일 사이즈', 'ONE', 'ONE SIZE', 'ONESIZE', '원사이즈'):
                    opt_value = 'FREE'
                info['options'].append({
                    'color': '',
                    'tag_size': opt_value,
                    'option_code': opt_code,
                    'status': 'in_stock' if in_stock else 'out_of_stock',
                })
        except (json.JSONDecodeError, AttributeError, ValueError):
            pass

    # ----- 이미지 3소스 -----
    seen = set()

    def _add(url):
        if url and url not in seen:
            seen.add(url)
            info['images'].append(url)

    # A. 메인 썸네일
    big = soup.select_one('div.detailArea img.BigImage')
    if big:
        _add(_abs_url(big.get('src', '')))
    # B. 추가 썸네일 (/small/ → /big/)
    for thumb in soup.select('div.xans-product-addimage img.ThumbImage'):
        src = thumb.get('src', '')
        if src:
            _add(_abs_url(src.replace('/small/', '/big/')))
    # C. 상세 본문 사방넷 상품사진 (ec-data-src 지연로딩, 마지막 1장 제외)
    #    - 상세 이미지는 src가 비어있고 ec-data-src에 실제 URL이 들어있음
    #    - 사방넷상품등록 경로(SABANG_MARKER)가 실제 상품사진, 상세페이지 템플릿은 제외됨
    sabang = []
    for img in soup.select('#prdDetail img'):
        src = img.get('ec-data-src', '') or img.get('src', '')
        if 'loromoda123.cafe24.com' in src and SABANG_MARKER in src:
            sabang.append(_abs_url(src))
    # model_id 일치하는 것만 우선, 없으면 사방넷 전체
    model_imgs = [u for u in sabang if model_id_hint and model_id_hint.upper() in u.upper()] or sabang
    if len(model_imgs) >= 2:
        model_imgs = model_imgs[:-1]  # 마지막 1장 제외 (수집처 메모)
    for url in model_imgs:
        _add(url)

    return info


# ===========================================
# 데이터 변환
# ===========================================

def convert_to_raw_data(list_item: Dict, detail_info: Dict, brand_name_en: str) -> Optional[Dict]:
    product_no = list_item['product_no']
    raw_name = detail_info.get('product_name') or list_item.get('product_name', '')
    product_name = clean_product_name(raw_name)
    if not product_name:
        return None

    model_id = extract_model_id_from_name(product_name)
    if not model_id:
        return None

    price = detail_info.get('price', 0) or list_item.get('sale_price', 0)
    original_price = detail_info.get('original_price', 0) or list_item.get('original_price', 0) or price

    options = detail_info.get('options', [])
    if detail_info.get('sold_out'):
        stock_status = 'out_of_stock'
    elif any(o.get('status') == 'in_stock' for o in options):
        stock_status = 'in_stock'
    elif not options:
        stock_status = 'in_stock'
    else:
        stock_status = 'out_of_stock'

    raw_json = {
        'brand_ko': list_item.get('brand_ko', ''),
        'options': options,
        'images': detail_info.get('images', []),
        'list_image': list_item.get('list_image', ''),
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': brand_name_en,
        'product_name': product_name,
        'p_name_full': raw_name,
        'model_id': model_id,
        'category_path': '',
        'original_price': original_price,
        'raw_price': price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': list_item.get('detail_url', ''),
    }


# ===========================================
# DB 조회/저장
# ===========================================

def get_brands_from_database(brand_filter: str = None) -> List[Dict]:
    with engine.connect() as conn:
        query = "SELECT mall_brand_name_en, mall_brand_no, mall_brand_url FROM mall_brands WHERE mall_name = :site AND is_active = 1"
        params = {'site': SOURCE_SITE}
        if brand_filter:
            query += " AND UPPER(mall_brand_name_en) = :brand"
            params['brand'] = brand_filter.upper()
        result = conn.execute(text(query), params)
        return [{'name_en': r[0], 'cate_no': r[1], 'brand_url': r[2]} for r in result]


def get_published_product_ids() -> set:
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = :site AND a.is_published = 1
        """), {'site': SOURCE_SITE})
        return {str(r[0]) for r in result}


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
    with engine.connect() as conn:
        for data in data_list:
            conn.execute(insert_sql, data)
        conn.commit()


# ===========================================
# 카테고리 채우기 (--categories 모드)
# ===========================================
# loromoda는 브랜드별로 수집해 category_path가 빈 채로 저장됨. index.html의 WOMEN/MEN/KIDS
# 성별 메뉴(3단계: 성별 > 그룹 > 리프)를 따로 크롤해 product_no→경로를 만들고
# raw_scraped_data.category_path만 UPDATE한다. (NEW ARRIVAL/BRAND/SALE 제외)

def _cate_of(href: str) -> Optional[str]:
    m = re.search(r'/category/[^/]+/(\d+)/', href or '') or re.search(r'cate_no=(\d+)', href or '')
    return m.group(1) if m else None


def parse_category_nodes(html: str) -> List[Tuple[str, str, int]]:
    """index.html → [(cate_no, path, spec)] (spec: 3=리프, 2=그룹, 1=성별)

    네비게이션 최상위 탭 중 WOMEN/MEN/KIDS만 사용 (NEW ARRIVAL/BRAND/SALE 제외).
    구조: 성별 > 그룹(의류/가방/슈즈…) > 리프(패딩/자켓…)
    """
    soup = BeautifulSoup(html, 'html.parser')
    cat = soup.select_one('.navigation-menu__category') or soup.select_one('div.top_category')
    if not cat:
        return []
    top_ul = cat.find('ul')
    if not top_ul:
        return []

    nodes = {}

    def add(c, p, s):
        if c and (c not in nodes or s > nodes[c][1]):
            nodes[c] = (p, s)

    for li in top_ul.find_all('li', recursive=False):
        a = li.find('a', recursive=False) or li.find('a')
        if not a:
            continue
        gender = a.get_text(strip=True)
        if gender not in ('WOMEN', 'MEN', 'KIDS'):
            continue
        add(_cate_of(a.get('href')), gender, 1)
        sub = li.find('ul')
        if not sub:
            continue
        for li2 in sub.find_all('li', recursive=False):
            a2 = li2.find('a', recursive=False) or li2.find('a')
            if not a2:
                continue
            group = a2.get_text(strip=True)
            add(_cate_of(a2.get('href')), f'{gender} > {group}', 2)
            ul3 = li2.find('ul')
            if ul3:
                for li3 in ul3.find_all('li', recursive=False):
                    a3 = li3.find('a')
                    if a3 and a3.get_text(strip=True):
                        add(_cate_of(a3.get('href')), f'{gender} > {group} > {a3.get_text(strip=True)}', 3)
    return [(c, p, s) for c, (p, s) in nodes.items()]


def get_collected_product_ids() -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT mall_product_id FROM raw_scraped_data WHERE source_site = :s"
        ), {'s': SOURCE_SITE})
        return {str(r[0]) for r in rows}


def _crawl_category_pnos(sm: 'SessionManager', cate_no: str) -> List[str]:
    pnos = []
    first, err = sm.fetch_page(f"{BASE_URL}/product/list.html?cate_no={cate_no}&page=1")
    if err or not first:
        return pnos
    last = get_last_page(first)
    for page in range(1, last + 1):
        html = first if page == 1 else sm.fetch_page(
            f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}")[0]
        if not html:
            continue
        items = get_product_list_from_page(html)
        if not items:
            break
        pnos.extend(it['product_no'] for it in items)
        if page > 1:
            time.sleep(random.uniform(0.2, 0.4))
    return pnos


def fill_categories(dry_run: bool = True):
    """index.html 성별 카테고리를 크롤해 category_path를 채운다 (리프→그룹→성별, 가장 구체적 우선)"""
    here = os.path.dirname(os.path.abspath(__file__))
    nodes = parse_category_nodes(open(os.path.join(here, 'index.html'), encoding='utf-8').read())
    nodes.sort(key=lambda x: -x[2])
    logger.info("=" * 60)
    logger.info(f"로로모다 카테고리 채우기 (Mode: {'DRY-RUN' if dry_run else 'APPLY'})")
    logger.info(f"카테고리 노드 {len(nodes)}개")

    collected = get_collected_product_ids()
    logger.info(f"raw_scraped_data loromoda 상품 {len(collected)}개")

    sm = SessionManager()
    product_path = {}
    try:
        for cate_no, path, spec in nodes:
            if sm.is_blocked:
                logger.error("차단 감지 — 중단")
                break
            pnos = _crawl_category_pnos(sm, cate_no)
            new = sum(1 for p in pnos if p not in product_path)
            for p in pnos:
                product_path.setdefault(p, path)
            logger.info(f"  [{path}] {len(pnos)}개 (신규배정 {new})")
            time.sleep(random.uniform(0.2, 0.4))
    finally:
        sm.close()

    matched = {p: path for p, path in product_path.items() if p in collected}
    unmatched = collected - set(matched)
    logger.info("\n" + "=" * 60)
    logger.info(f"category_path 배정: {len(matched)}/{len(collected)}개 (미배정 {len(unmatched)})")
    logger.info(f"성별 분포: {dict(Counter(p.split(' > ')[0] for p in matched.values()))}")
    for path, n in Counter(matched.values()).most_common(15):
        logger.info(f"  {n:4}  {path}")

    if dry_run:
        logger.info("\n[미리보기 모드] DB 변경 없음. 적용하려면 --categories (--dry-run 빼고)")
        return

    update_sql = text("""
        UPDATE raw_scraped_data SET category_path = :path, updated_at = NOW()
        WHERE source_site = :site AND mall_product_id = :pno
    """)
    engine.dispose()  # 15분 크롤 동안 잠든 풀 연결 폐기 → 저장은 새 연결로
    with engine.connect() as conn:
        cnt = 0
        for pno, path in matched.items():
            conn.execute(update_sql, {'path': path, 'site': SOURCE_SITE, 'pno': pno})
            cnt += 1
            if cnt % 200 == 0:
                conn.commit()
        conn.commit()
    logger.info(f"\n✅ category_path UPDATE 완료: {len(matched)}개")


# ===========================================
# 메인 실행
# ===========================================

def _build_list_url(brand: Dict, page: int) -> str:
    path = brand.get('brand_url') or f"/category/{brand['name_en']}/{brand['cate_no']}/"
    if not path.startswith('http'):
        path = f"{BASE_URL}{path}"
    sep = '&' if '?' in path else '?'
    return f"{path}{sep}page={page}"


def main():
    parser = argparse.ArgumentParser(description='로로모다 상품 수집기 (브랜드별 순회)')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='브랜드당 최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    parser.add_argument('--categories', action='store_true', help='상품 수집 대신 category_path 채우기')
    args = parser.parse_args()

    if args.categories:
        fill_categories(dry_run=args.dry_run)
        return

    logger.info("=" * 60)
    logger.info(f"로로모다 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    logger.info("=" * 60)

    brands = get_brands_from_database(args.brand)
    logger.info(f"대상 브랜드: {len(brands)}개")
    if not brands:
        logger.info("수집할 브랜드가 없습니다.")
        return

    published_ids = get_published_product_ids() if args.skip_existing else set()
    if args.skip_existing:
        logger.info(f"  등록 완료 상품 {len(published_ids)}개 — 스킵 대상")

    session_mgr = SessionManager()
    total_collected = 0
    total_skipped_no_model = 0

    try:
        for brand_idx, brand in enumerate(brands, 1):
            if session_mgr.is_blocked:
                logger.error("  차단 감지됨 — 수집 중단")
                break

            brand_name_en = brand['name_en']
            logger.info(f"\n>>> [{brand_idx}/{len(brands)}] 브랜드: {brand_name_en} (cate_no={brand['cate_no']})")

            first_url = _build_list_url(brand, 1)
            first_html, error = session_mgr.fetch_page(first_url)
            if error or not first_html:
                logger.warning(f"  리스트 수집 실패: {error}")
                continue

            last_page = get_last_page(first_html)
            all_items = []
            seen = set()

            for page in range(1, last_page + 1):
                if session_mgr.is_blocked:
                    break
                if page == 1:
                    page_html = first_html
                else:
                    page_html, error = session_mgr.fetch_page(_build_list_url(brand, page))
                    if error or not page_html:
                        continue
                items = get_product_list_from_page(page_html)
                if not items:
                    break
                new_items = [it for it in items if it['product_no'] not in seen]
                for it in new_items:
                    seen.add(it['product_no'])
                all_items.extend(new_items)
                if page > 1:
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            if args.limit and len(all_items) > args.limit:
                all_items = all_items[:args.limit]
            if args.skip_existing:
                all_items = [it for it in all_items if it['product_no'] not in published_ids]

            logger.info(f"  리스트 {len(all_items)}개 수집 대상")

            batch_data = []
            total = len(all_items)
            for idx, list_item in enumerate(all_items, 1):
                if session_mgr.is_blocked:
                    logger.error("  차단 감지됨 — 상세 수집 중단")
                    break

                detail_html, error = session_mgr.fetch_page(list_item['detail_url'])
                if error:
                    logger.warning(f"  [{idx}/{total}] 상세 실패: {error} | {list_item['product_name'][:30]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                model_hint = extract_model_id_from_name(clean_product_name(list_item.get('product_name', '')))
                detail_info = extract_detail_info(detail_html, model_hint) if detail_html else {}
                data = convert_to_raw_data(list_item, detail_info, brand_name_en)
                if not data:
                    total_skipped_no_model += 1
                    logger.info(f"  [{idx}/{total}] SKIP (no model_id) | {list_item['product_name'][:50]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                logger.info(f"  [{idx}/{total}] {data['model_id']} | {data['raw_price']:>12,}원 | {data['product_name'][:40]}")
                total_collected += 1

                if not args.dry_run:
                    batch_data.append(data)
                    if len(batch_data) >= 10:
                        save_to_database(batch_data)
                        logger.info(f"  DB 저장: {len(batch_data)}개")
                        batch_data = []

                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            if batch_data and not args.dry_run:
                save_to_database(batch_data)
                logger.info(f"  DB 저장(잔여): {len(batch_data)}개")

    finally:
        session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info("로로모다 수집 완료")
    logger.info(f"  총 수집: {total_collected}개")
    logger.info(f"  model_id 없어서 스킵: {total_skipped_no_model}개")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :site"
            ), {'site': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 loromoda 상품: {count}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
