# -*- coding: utf-8 -*-
"""
밀라니즈(milaneez.com) 상품 수집 스크립트
- Cafe24 기반 쇼핑몰 → HTML 스크래핑
- 진입: 전체상품 리스트 `/product/list.html?cate_no=149&page=N` 단일 순회
- 상세: 리스트 카드의 슬러그 URL 그대로 fetch
- raw_scraped_data 테이블에 source_site='milaneez'으로 저장

사용법:
    python milaneez_collector.py                       # 전체 실행
    python milaneez_collector.py --limit 10            # 최대 10개만
    python milaneez_collector.py --max-pages 3         # 최대 3페이지만
    python milaneez_collector.py --dry-run             # DB 저장 없이 테스트
    python milaneez_collector.py --skip-existing       # 등록 완료 상품 스킵
    python milaneez_collector.py --map-categories      # 카테고리 페이지만 순회하여 raw.category_path + ace.category_id 채움
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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'okmall'))
import authority_flag  # 단일권위 전환 스위치 (ace → buyma_listings)

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
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=3600)

# ===========================================
# 상수
# ===========================================

BASE_URL = 'https://milaneez.com'
SOURCE_SITE = 'milaneez'
ALL_ITEMS_CATE_NO = '149'   # 전체상품 리스트
SESSION_REFRESH_INTERVAL = 30
MAX_CONSECUTIVE_TIMEOUTS = 5
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.8

BROWSER_PROFILES = [
    {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    },
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
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
            self.session.headers.update(main_headers)

            logger.info(f"  [세션] 새 세션 시작 - 메인 페이지 방문")
            r = self.session.get(f'{BASE_URL}/', timeout=15)
            if r.status_code != 200:
                return False, f"메인 페이지 접속 실패: {r.status_code}"

            prod_headers = self.profile.copy()
            prod_headers['Referer'] = f'{BASE_URL}/'
            self.session.headers.update(prod_headers)
            self.request_count = 0
            time.sleep(random.uniform(0.5, 1.2))
            return True, None
        except requests.exceptions.Timeout:
            return False, "메인 페이지 타임아웃"
        except Exception as e:
            return False, f"세션 생성 오류: {e}"

    def fetch_page(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        if self.is_blocked:
            return None, "차단됨"
        if self.session is None or self.request_count >= SESSION_REFRESH_INTERVAL:
            ok, err = self._create_new_session()
            if not ok:
                return None, err
        try:
            r = self.session.get(url, timeout=30)
            self.request_count += 1
            if r.status_code == 403:
                self.is_blocked = True
                return None, "접근 차단됨 (403)"
            r.raise_for_status()
            self.consecutive_timeout_count = 0
            return r.text, None
        except requests.exceptions.Timeout:
            self.consecutive_timeout_count += 1
            logger.warning(f"  [타임아웃] 연속 {self.consecutive_timeout_count}회")
            if self.consecutive_timeout_count >= MAX_CONSECUTIVE_TIMEOUTS:
                self.is_blocked = True
                return None, "타임아웃 차단 감지"
            return None, "요청 타임아웃"
        except requests.exceptions.RequestException as e:
            self.consecutive_timeout_count = 0
            return None, f"요청 오류: {e}"

    def close(self):
        if self.session:
            self.session.close()


# ===========================================
# 리스트 페이지 파싱
# ===========================================

def _parse_int_from(text_value: str) -> int:
    if not text_value:
        return 0
    digits = re.sub(r'[^0-9]', '', text_value)
    return int(digits) if digits else 0


def get_product_list_from_page(html: str) -> List[Dict]:
    """리스트 페이지 HTML에서 상품 기본 정보 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    products = []
    items = soup.select('li.df-prl__item[id^="anchorBoxId_"]')
    for item in items:
        try:
            item_id = item.get('id', '')
            product_no = item_id.replace('anchorBoxId_', '')
            if not product_no:
                continue

            # 상세 URL (슬러그 기반)
            thumb_link = item.select_one('a.df-prl__thumb-link')
            detail_path = thumb_link.get('href', '') if thumb_link else ''

            # cate_no 추출 (URL 내 /category/N/)
            cate_no = ''
            m = re.search(r'/category/(\d+)/', detail_path)
            if m:
                cate_no = m.group(1)

            # 상품명
            name_elem = item.select_one('a.df-prl__name span') or item.select_one('a.df-prl__name')
            product_name = name_elem.get_text(strip=True) if name_elem else ''

            # 정가 (line-through)
            original_price = 0
            custom_li = item.select_one('li.df-prl__desc-item.product_custom span[style*="line-through"]')
            if custom_li:
                original_price = _parse_int_from(custom_li.get_text(strip=True))

            # 판매가
            sale_price = 0
            price_li = item.select_one('li.df-prl__desc-item.product_price span[style*="font-weight"]')
            if price_li:
                sale_price = _parse_int_from(price_li.get_text(strip=True))

            # 이미지
            img = item.select_one('img.df-prl__thumb-image')
            image_url = img.get('src', '') if img else ''
            if image_url.startswith('//'):
                image_url = 'https:' + image_url

            products.append({
                'product_no': product_no,
                'product_name': product_name,
                'original_price': original_price,
                'sale_price': sale_price,
                'image_url': image_url,
                'cate_no': cate_no,
                'detail_path': detail_path,
            })
        except Exception as e:
            logger.warning(f"  리스트 아이템 파싱 오류: {e}")
            continue

    return products


def get_last_page(html: str) -> int:
    """페이지네이션에서 마지막 페이지 번호 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    max_page = 1
    # 'last' 클래스 우선
    last_a = soup.select_one('.xans-product-normalpaging a.last[href]')
    if last_a:
        m = re.search(r'page=(\d+)', last_a.get('href', ''))
        if m:
            return int(m.group(1))
    # fallback: 모든 ol li a
    for a in soup.select('.xans-product-normalpaging ol li a[href]'):
        m = re.search(r'page=(\d+)', a.get('href', ''))
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


# ===========================================
# 상세 페이지 파싱
# ===========================================

def extract_detail_info(html: str) -> Dict[str, Any]:
    """상세 페이지에서 모델번호, 색상, 옵션, 이미지, 카테고리 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    info = {
        'product_name': '',
        'brand_name': '',
        'model_id': '',
        'options': [],
        'images': [],
        'category_path_breadcrumb': '',
        'sale_price_dom': 0,
        'original_price_dom': 0,
        'is_soldout_product': False,   # 상품 전체 품절 플래그
    }

    # 상품 전체 품절: var is_soldout_icon = 'T'
    m_soldout = re.search(r"var\s+is_soldout_icon\s*=\s*'([TF])'", html)
    if m_soldout and m_soldout.group(1) == 'T':
        info['is_soldout_product'] = True

    # 상품명: div.infoArea span.df-prs__name-size
    name_elem = soup.select_one('div.infoArea span.df-prs__name-size') \
                or soup.select_one('h2.df-prs__name span.df-prs__name-size')
    if name_elem:
        info['product_name'] = name_elem.get_text(strip=True)

    # 브랜드명: 상품명에서 추출 (model_id와 동일 알고리즘)
    # 예: "24FW 프라다 ..." → "프라다", "미하라 야스히로 ..." → "미하라 야스히로"
    # milaneez는 명시적 brand 마크업 없음. collect.md의 h2#cicat은 잘못된 spec.
    if info.get('product_name'):
        cleaned = re.sub(r'^\[[^\]]*\]\s*', '', info['product_name']).strip()
        parts = cleaned.split()
        # 시즌 prefix(25FW 등) 건너뛰고 첫 한글 토큰 찾기
        i = 0
        while i < len(parts) and not re.search(r'[가-힣]', parts[i]):
            i += 1
        # 연속된 한글 토큰을 모두 brand로
        brand_parts = []
        while i < len(parts) and re.search(r'[가-힣]', parts[i]):
            brand_parts.append(parts[i])
            i += 1
        if brand_parts:
            info['brand_name'] = ' '.join(brand_parts)

    # 가격 (DOM)
    custom = soup.select_one('tr.product_custom_css #span_product_price_custom strike') \
             or soup.select_one('#span_product_price_custom strike')
    if custom:
        info['original_price_dom'] = _parse_int_from(custom.get_text(strip=True))

    price = soup.select_one('tr.product_price_css #span_product_price_text') \
            or soup.select_one('#span_product_price_text')
    if price:
        info['sale_price_dom'] = _parse_int_from(price.get_text(strip=True))

    # 옵션 (사이즈) — cafe24는 옵션별 재고 정보를 `var option_stock_data` JS 변수에 담음
    # 예: {"P0000ERJ000T":{"option_value":"36","stock_number":4,"is_selling":"T"}, ...}
    stock_match = re.search(r"var\s+option_stock_data\s*=\s*'(\{.*?\})'\s*;", html, re.DOTALL)
    if stock_match:
        try:
            js_str = stock_match.group(1)
            # JS 문자열 escape 해제: \\ → \ (임시 마커), \" → ", \uXXXX → 유니코드
            py_str = (js_str.replace('\\\\', '\x00').replace('\\"', '"')
                            .replace("\\'", "'").replace('\x00', '\\'))
            py_str = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), py_str)
            stock_data = json.loads(py_str)
            for opt_code, d in stock_data.items():
                size = d.get('option_value', '')
                if isinstance(size, list):
                    size = ' / '.join(str(s) for s in size)
                stock_num = int(d.get('stock_number', 0) or 0)
                is_selling = d.get('is_selling') == 'T'
                in_stock = is_selling and stock_num > 0
                if size in ['단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈', 'ONESIZE', 'FREE SIZE']:
                    size = 'FREE'
                info['options'].append({
                    'color': '',
                    'tag_size': size,
                    'option_code': opt_code,
                    'status': 'in_stock' if in_stock else 'out_of_stock',
                })
        except Exception:
            pass  # fallback으로 select 파싱

    # fallback: option_stock_data 없으면 select 태그 파싱 (재고 정보 없음 → 텍스트에 [품절]만 체크)
    if not info['options']:
        option_select = soup.select_one('select#product_option_id1')
        if option_select:
            for opt in option_select.select('option'):
                opt_value = opt.get('value', '')
                if not opt_value or opt_value in ('*', '**'):
                    continue
                opt_text = opt.get_text(strip=True)
                if re.match(r'^[-=]{3,}$', opt_text.strip()):
                    continue
                is_soldout = '품절' in opt_text or opt.get('disabled') is not None
                clean_size = re.sub(r'\s*\[품절\]\s*', '', opt_text).strip()
                if clean_size in ['단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈', 'ONESIZE', 'FREE SIZE']:
                    clean_size = 'FREE'
                info['options'].append({
                    'color': '',
                    'tag_size': clean_size,
                    'option_code': opt_value,
                    'status': 'out_of_stock' if is_soldout else 'in_stock',
                })

    # 두 번째 옵션 (색상x사이즈 매트릭스)
    option_select2 = soup.select_one('select#product_option_id2')
    if option_select2 and info['options']:
        first_options = []
        for opt in soup.select_one('select#product_option_id1').select('option'):
            v = opt.get('value', '')
            if not v or v in ('*', '**'):
                continue
            first_options.append(opt.get_text(strip=True))
        second_options = []
        for opt in option_select2.select('option'):
            v = opt.get('value', '')
            if not v or v in ('*', '**'):
                continue
            t = opt.get_text(strip=True)
            is_soldout = '품절' in t or opt.get('disabled') is not None
            second_options.append({
                'size': re.sub(r'\s*\[품절\]\s*', '', t).strip(),
                'status': 'out_of_stock' if is_soldout else 'in_stock',
            })
        if first_options and second_options:
            info['options'] = [
                {'color': c, 'tag_size': s['size'], 'option_code': '', 'status': s['status']}
                for c in first_options for s in second_options
            ]

    # 이미지: ThumbImage src (BigImage + slider)
    seen_img = set()
    for sel in ['div.thumbnail img.BigImage', 'div.df-slider ul li img.ThumbImage', 'img.ThumbImage']:
        for img in soup.select(sel):
            src = img.get('data-original-src') or img.get('src', '')
            if not src:
                continue
            if src.startswith('//'):
                src = 'https:' + src
            if src in seen_img:
                continue
            seen_img.add(src)
            info['images'].append(src)

    # 카테고리 breadcrumb (있으면)
    crumb = soup.select_one('div.location') or soup.select_one('.df-breadcrumb')
    if crumb:
        crumb_text = re.sub(r'\s+', ' ', crumb.get_text(' ', strip=True))
        info['category_path_breadcrumb'] = crumb_text

    return info


def extract_model_id(product_name: str, brand_name: str = '') -> str:
    """상품명에서 모델번호 추출 (collect.md spec: 브랜드명 다음 영문 숫자 조합)
    예: '생로랑 692061 BOW01 1000 금장 모노그램 멀티 폴드 반지갑' → '692061 BOW01 1000'
        '스톤아일랜드 771640131 V0029 6세 키즈 ...' → '771640131 V0029'
        '[에나멜부분 변색]프라다 1BC214 R789 F0PG7 삼각로고 ...' → '1BC214 R789 F0PG7'

    알고리즘 (토큰 단위):
      1) prefix `[...]` 태그 제거
      2) 첫 한글 단어(brand) 건너뛰기
      3) 나머지 토큰을 순서대로: 한글 포함 토큰을 만나면 중단
      4) 모은 토큰들을 공백으로 join → model_id
    """
    if not product_name:
        return ''
    cleaned = re.sub(r'^\[[^\]]*\]\s*', '', product_name).strip()
    parts = cleaned.split()
    # 1) 앞쪽 영숫자 prefix(시즌: 25FW/24SS 등) 건너뛰고 첫 한글 토큰 찾기
    i = 0
    while i < len(parts) and not re.search(r'[가-힣]', parts[i]):
        i += 1
    if i >= len(parts):
        return ''  # 한글 brand 토큰이 전혀 없음
    # 2) 연속된 한글 토큰은 모두 brand (예: "미하라 야스히로", "폴로 랄프로렌")
    while i < len(parts) and re.search(r'[가-힣]', parts[i]):
        i += 1
    if i >= len(parts):
        return ''  # brand 뒤에 토큰 없음
    # 3) 한글 토큰 만날 때까지 영숫자 토큰만 model_id로 수집
    tokens = []
    for tok in parts[i:]:
        if re.search(r'[가-힣]', tok):
            break  # 한글 포함 토큰 = description 시작
        tokens.append(tok)
    candidate = ' '.join(tokens)
    # 임시: 구찌의 'GG' 로고 토큰이 모델번호 뒤에 붙는 케이스 (상품 늘어나면 일반화)
    if brand_name == '구찌' and candidate.endswith(' GG'):
        candidate = candidate[:-3].strip()
    return candidate if len(candidate) >= 2 else ''


# ===========================================
# 변환 + DB 저장
# ===========================================

def convert_to_raw_data(list_item: Dict, detail: Dict, category_path: str = '') -> Optional[Dict]:
    product_no = list_item['product_no']
    product_name = detail.get('product_name') or list_item.get('product_name', '')
    brand_name = detail.get('brand_name', '')
    model_id = extract_model_id(product_name, brand_name)
    if not model_id:
        return None

    original_price = detail.get('original_price_dom') or list_item.get('original_price', 0)
    sale_price = detail.get('sale_price_dom') or list_item.get('sale_price', 0)
    if not sale_price:
        sale_price = original_price

    options = detail.get('options', [])
    if detail.get('is_soldout_product'):
        # 상품 전체 품절 플래그 우선 — 옵션 in_stock 여부와 무관
        stock_status = 'out_of_stock'
        # 모든 옵션도 out_of_stock으로 일관성 맞춤
        for o in options:
            o['status'] = 'out_of_stock'
    elif any(o.get('status') == 'in_stock' for o in options):
        stock_status = 'in_stock'
    elif options:
        stock_status = 'out_of_stock'
    else:
        stock_status = 'in_stock'  # 옵션 없으면 일단 in_stock

    raw_json = {
        'options': options,
        'images': detail.get('images', []),
        'cate_no': list_item.get('cate_no', ''),
        'brand_name_kr': brand_name,
        'category_breadcrumb': detail.get('category_path_breadcrumb', ''),
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    detail_path = list_item.get('detail_path', '')
    product_url = urljoin(BASE_URL, detail_path) if detail_path else f"{BASE_URL}/product/detail.html?product_no={product_no}"

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': brand_name,  # 일단 사이트 표기 그대로 저장. converter에서 정규화
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


def get_published_product_ids() -> set:
    """등록 완료된 상품의 mall_product_id 조회"""
    with engine.connect() as conn:
        _reg = authority_flag.registered_sql('a') if authority_flag.use_listing_authority() else "a.is_published = 1"
        rows = conn.execute(text(f"""
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = :src AND {_reg}
        """), {'src': SOURCE_SITE})
        return {str(r[0]) for r in rows}


def get_category_path_by_cate_no(cate_no: str) -> str:
    """mall_categories에서 cate_no에 매칭되는 full_path 조회 (있으면)"""
    if not cate_no:
        return ''
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT full_path FROM mall_categories
            WHERE mall_name = :src AND category_id = :cid
            LIMIT 1
        """), {'src': SOURCE_SITE, 'cid': cate_no}).fetchone()
        return row[0] if row else ''


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
# 메인
# ===========================================

def run_category_mapping(session: 'SessionManager', dry_run: bool = False):
    """카테고리 페이지만 순회하면서 product_no → category 매핑 dict 구축,
    raw_scraped_data.category_path + ace_products.category_id 업데이트.
    상품 상세는 fetch 안 함 (이미 수집된 raw 활용)."""
    from collections import defaultdict

    # 1) mall_categories에서 milaneez leaf 카테고리 로드
    with engine.connect() as conn:
        cats = conn.execute(text("""
            SELECT category_id, full_path, buyma_category_id
            FROM mall_categories
            WHERE mall_name = :s AND is_active = 1
            ORDER BY category_id
        """), {'s': SOURCE_SITE}).fetchall()
    logger.info(f"=== milaneez 카테고리 {len(cats)}개 순회 ===")

    pno_map: Dict[str, List[Tuple[str, str, Optional[int]]]] = defaultdict(list)

    # 2) 각 카테고리 페이지 순회
    for idx, (cate_no, full_path, buyma_id) in enumerate(cats, 1):
        if session.is_blocked:
            logger.error("  차단 감지 — 중단")
            break
        cate_no = str(cate_no)
        page = 1
        prev_pnos = None
        while True:
            url = f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}"
            html, err = session.fetch_page(url)
            if err or not html:
                break
            items = get_product_list_from_page(html)
            if not items:
                break
            cur_pnos = {it['product_no'] for it in items}
            if prev_pnos and cur_pnos == prev_pnos:
                break
            prev_pnos = cur_pnos
            for it in items:
                pno_map[it['product_no']].append((cate_no, full_path, buyma_id))
            # 마지막 페이지 감지
            last_p = get_last_page(html)
            if page >= last_p:
                break
            page += 1
            time.sleep(random.uniform(0.3, 0.7))
        logger.info(f"  [{idx}/{len(cats)}] cate={cate_no} ({full_path[:35]}) → 매핑 상품 {sum(1 for v in pno_map.values() if (cate_no, full_path, buyma_id) in v)}개 (누적 {len(pno_map)})")
        time.sleep(random.uniform(0.3, 0.7))

    n_multi = sum(1 for v in pno_map.values() if len(v) > 1)
    logger.info(f"\n  매핑 dict 구축 완료: 상품 {len(pno_map)}개 (다중 카테고리 {n_multi}건은 첫 매칭 사용)")

    if dry_run:
        logger.info("  [DRY-RUN] DB 갱신 안 함")
        return

    # 3) DB 업데이트
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, mall_product_id FROM raw_scraped_data WHERE source_site = :s
        """), {'s': SOURCE_SITE}).fetchall()
        raw_by_pno = {str(r[1]): r[0] for r in rows}

        updated_raw = 0
        updated_ace = 0
        unmatched = []
        for pno, raw_id in raw_by_pno.items():
            if pno not in pno_map:
                unmatched.append(pno)
                continue
            cate_no, path, buyma_id = pno_map[pno][0]
            conn.execute(text("UPDATE raw_scraped_data SET category_path = :p WHERE id = :id"),
                         {'p': path, 'id': raw_id})
            updated_raw += 1
            if buyma_id:
                res = conn.execute(text("""
                    UPDATE ace_products SET category_id = :cid
                    WHERE raw_data_id = :rid AND (category_id IS NULL OR category_id = 0)
                """), {'cid': int(buyma_id), 'rid': raw_id})
                updated_ace += res.rowcount
        conn.commit()

    logger.info(f"\n=== DB 업데이트 완료 ===")
    logger.info(f"  raw.category_path UPDATE: {updated_raw}건")
    logger.info(f"  ace.category_id UPDATE: {updated_ace}건")
    logger.info(f"  매칭 실패 상품: {len(unmatched)}건")
    if unmatched:
        logger.info(f"    sample: {unmatched[:10]}")


def main():
    parser = argparse.ArgumentParser(description='밀라니즈 상품 수집기')
    parser.add_argument('--limit', type=int, help='최대 수집 상품 수')
    parser.add_argument('--max-pages', type=int, help='최대 페이지 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    parser.add_argument('--map-categories', action='store_true',
                        help='카테고리 페이지만 순회하여 raw.category_path + ace.category_id 채움 (상세 fetch 안 함)')
    args = parser.parse_args()

    # 카테고리 매핑 모드: 별도 분기
    if args.map_categories:
        logger.info("=" * 60)
        logger.info(f"카테고리 매핑 모드 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
        logger.info("=" * 60)
        session = SessionManager()
        try:
            run_category_mapping(session, dry_run=args.dry_run)
        finally:
            session.close()
        return

    logger.info("=" * 60)
    logger.info(f"밀라니즈 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    if args.skip_existing:
        logger.info("  신규+미등록 상품만 수집 (--skip-existing)")
    logger.info("=" * 60)

    session = SessionManager()
    all_list_items: List[Dict] = []
    seen_pno = set()
    total_collected = 0
    skipped_no_model = 0

    try:
        # ───── Phase 1: 리스트 페이지 순회 ─────
        page = 1
        last_page = None
        prev_page_pnos: Optional[set] = None

        while True:
            if session.is_blocked:
                logger.error("  차단 감지 — 리스트 수집 중단")
                break

            url = f"{BASE_URL}/product/list.html?cate_no={ALL_ITEMS_CATE_NO}&page={page}"
            html, err = session.fetch_page(url)
            if err:
                logger.warning(f"  page={page} 수집 실패: {err}")
                break
            if not html:
                break

            if page == 1:
                last_page = get_last_page(html)
                logger.info(f"  총 페이지(추정): {last_page}")

            items = get_product_list_from_page(html)
            if not items:
                logger.info(f"  page={page} 카드 없음 → 종료")
                break

            cur_pnos = {it['product_no'] for it in items}
            if prev_page_pnos and cur_pnos == prev_page_pnos:
                logger.info(f"  page={page} 이전과 동일 → 종료")
                break
            prev_page_pnos = cur_pnos

            new_count = 0
            for it in items:
                if it['product_no'] in seen_pno:
                    continue
                seen_pno.add(it['product_no'])
                all_list_items.append(it)
                new_count += 1

            logger.info(f"  page={page} 수집 {len(items)}개 (신규 {new_count}, 누적 {len(all_list_items)})")

            if args.max_pages and page >= args.max_pages:
                logger.info(f"  --max-pages={args.max_pages} 도달")
                break
            if last_page and page >= last_page:
                logger.info(f"  마지막 페이지 도달")
                break

            page += 1
            time.sleep(random.uniform(0.4, 1.0))

        logger.info(f"\n  리스트 수집 완료: {len(all_list_items)}개")

        # ───── 필터링 ─────
        if args.limit and len(all_list_items) > args.limit:
            all_list_items = all_list_items[:args.limit]
            logger.info(f"  --limit={args.limit} 적용 → {len(all_list_items)}개")

        if args.skip_existing:
            published = get_published_product_ids()
            before = len(all_list_items)
            all_list_items = [it for it in all_list_items if it['product_no'] not in published]
            logger.info(f"  등록 완료 스킵: {before - len(all_list_items)}, 대상: {len(all_list_items)}")

        # ───── Phase 2: 상세 수집 + 저장 ─────
        batch: List[Dict] = []
        total = len(all_list_items)

        for idx, it in enumerate(all_list_items, 1):
            if session.is_blocked:
                logger.error("  차단 감지 — 상세 수집 중단")
                break

            detail_url = urljoin(BASE_URL, it['detail_path']) if it.get('detail_path') \
                         else f"{BASE_URL}/product/detail.html?product_no={it['product_no']}"
            html, err = session.fetch_page(detail_url)
            if err:
                logger.warning(f"  [{idx}/{total}] 상세 수집 실패: {err} | {it.get('product_name','')[:30]}")
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                continue
            if not html:
                continue

            detail = extract_detail_info(html)

            # category_path: mall_categories에서 cate_no로 조회 (있으면), 없으면 빈 문자열
            category_path = get_category_path_by_cate_no(it.get('cate_no', ''))

            data = convert_to_raw_data(it, detail, category_path)
            if not data:
                skipped_no_model += 1
                logger.info(f"  [{idx}/{total}] SKIP (no model_id) | {it.get('product_name','')[:50]}")
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                continue

            logger.info(f"  [{idx}/{total}] {data['model_id'][:30]} | {data['raw_price']:>10,}원 | {data['brand_name_en']} | {data['product_name'][:40]}")
            total_collected += 1

            if not args.dry_run:
                batch.append(data)
                if len(batch) >= 10:
                    save_to_database(batch)
                    logger.info(f"  DB 저장: {len(batch)}개")
                    batch = []

            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        if batch and not args.dry_run:
            save_to_database(batch)
            logger.info(f"  DB 저장(잔여): {len(batch)}개")

    finally:
        session.close()

    logger.info("\n" + "=" * 60)
    logger.info(f"밀라니즈 수집 완료")
    logger.info(f"  총 수집: {total_collected}개")
    logger.info(f"  model_id 없어서 스킵: {skipped_no_model}개")
    if not args.dry_run:
        with engine.connect() as conn:
            cnt = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :s"
            ), {'s': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 milaneez 상품: {cnt}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
