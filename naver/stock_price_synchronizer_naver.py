# -*- coding: utf-8 -*-
"""
재고 및 가격 동기화 스크립트 (네이버 11개 mall 공용)

대상 mall (smartstore + brandstore):
  - smartstore: premiumsneakers, fabstyle, loutique, t1global, vvano, veroshopmall,
                dmont, tuttobene, thefactor2
  - brandstore: carpi, joharistore

수집 방식: Playwright 단일 브라우저 + XHR 캡처
  - products API (상품 JSON): salePrice, optionCombinations, saleStatus
  - product-benefits API (쿠폰 적용가): optimalDiscount.totalDiscountResult.summary.totalPayAmount
  - URL prefix: smartstore는 /i/v2/, brandstore는 /n/v2/

실행 전제:
  - WARP OFF (네이버 DNS 차단 회피)
  - naver_cookies.json 존재 (없으면 premiumsneakers_collector.py --login 으로 갱신)
  - MAX_WORKERS=1 (Playwright 세션 1개 공유, 직렬 처리)

사용법:
    python stock_price_synchronizer_naver.py                         # 11개 mall 전부
    python stock_price_synchronizer_naver.py --source premiumsneakers
    python stock_price_synchronizer_naver.py --source carpi --dry-run
    python stock_price_synchronizer_naver.py --brand NIKE
    python stock_price_synchronizer_naver.py --id 121147

기반: kasina/stock_price_synchronizer_kasina.py (가격/마진/BUYMA API 로직 동일)
"""

import os
import sys
import io
import json
import time
import random
import re
import argparse
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import unicodedata
import requests
from bs4 import BeautifulSoup
import pymysql
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=True)


def _buyma_width(s: str) -> int:
    """바이마 반각 환산 길이 계산 (전각=2, 반각=1)"""
    w = 0
    for c in s:
        eaw = unicodedata.east_asian_width(c)
        w += 2 if eaw in ('F', 'W', 'A') else 1
    return w


def truncate_buying_shop_name(shop_name: str, max_limit: int = 30) -> str:
    """buying_shop_name 반각 30자 제한 처리
    1단계: 원본 그대로 (brand正規販売店)
    2단계: 正規販売店 → 正規店 으로 축약
    3단계: 'BRAND 正規販売店' 고정값
    """
    if not shop_name:
        return ""
    if _buyma_width(shop_name) <= max_limit:
        return shop_name
    if shop_name.endswith('正規販売店'):
        short = shop_name.replace('正規販売店', '正規店')
        if _buyma_width(short) <= max_limit:
            return short
    return 'BRAND 正規販売店'


# =====================================================
# 설정값 (buyma_product_register.py와 동일)
# =====================================================

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

# 바이마 API 설정
BUYMA_MODE = int(os.getenv('BUYMA_MODE', 1))
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')
BUYMA_BUYER_ID = os.getenv('BUYMA_BUYER_ID', '')  # 내 바이마 판매자 ID
API_BASE_URL = BUYMA_API_BASE_URL if BUYMA_MODE == 1 else BUYMA_SANDBOX_URL

# 바이마 API 고정값 (buyma_product_register.py와 동일)
BUYMA_FIXED_VALUES = {
    'buying_area_id': '2002003000',
    'shipping_area_id': '2002003000',
    'theme_id': 98,
    'duty': 'included',
    'shipping_methods': [1063035],
}

# =====================================================
# 네이버 설정
# =====================================================

# 11개 mall 분류
NAVER_MALLS = [
    'premiumsneakers', 'fabstyle', 'loutique', 't1global', 'vvano', 'veroshopmall',
    'dmont', 'tuttobene', 'thefactor2',
    'carpi', 'joharistore',
]
SMARTSTORE_MALLS = {
    'premiumsneakers', 'fabstyle', 'loutique', 't1global', 'vvano', 'veroshopmall',
    'dmont', 'tuttobene', 'thefactor2',
}
BRANDSTORE_MALLS = {'carpi', 'joharistore'}

# 쿠키 파일 (naver/ 디렉토리)
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'naver_cookies.json')

BUYMA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6',
    'Referer': 'https://www.buyma.com/',
}

BUYMA_SEARCH_URL = "https://www.buyma.com/r/-O3/{model_no}/"

# 딜레이 설정
REQUEST_DELAY_MIN = 0.3  # 네이버 상세 페이지 방문 간 최소 딜레이
REQUEST_DELAY_MAX = 0.8  # 네이버 상세 페이지 방문 간 최대 딜레이
API_CALL_DELAY = 0.2     # 바이마 API 호출 후 딜레이
DETAIL_PAGE_TIMEOUT = 30000
DETAIL_MAX_RETRIES = 2

# 병렬 처리 설정 (Playwright 단일 세션 공유 → 직렬)
MAX_WORKERS = 1

# 마진 계산 상수 (buyma_product_register.py와 동일)
EXCHANGE_RATE = 9.2
SALES_FEE_RATE = 0.055
DEFAULT_SHIPPING_FEE = 15000


# =====================================================
# 유틸리티 함수
# =====================================================

# 로그 출력용 Lock (병렬 처리 시 로그 섞임 방지)
_log_lock = threading.Lock()

def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)

def log_batch(messages: List[str]) -> None:
    """여러 로그 메시지를 한 번에 출력 (병렬 처리 시 섞임 방지)"""
    with _log_lock:
        for msg in messages:
            print(msg, flush=True)


# 카테고리별 허용 size_details 키 캐시
_category_size_keys_cache: Dict[int, List[str]] = {}


def load_category_size_keys() -> Dict[int, List[str]]:
    """
    BUYMA 마스터 데이터 size_details.csv에서 카테고리별 허용 키 로드
    Returns: {category_id: [허용 키 리스트]}
    """
    global _category_size_keys_cache
    if _category_size_keys_cache:
        return _category_size_keys_cache

    import csv
    # okmall 디렉토리의 마스터 데이터 참조
    csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'okmall', 'buyma_master_data_20260226', 'size_details.csv')
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat_id = row.get('category_id', '').strip()
                key_name = row.get('name', '').strip()
                if cat_id and cat_id.isdigit() and key_name:
                    cat_id = int(cat_id)
                    if cat_id not in _category_size_keys_cache:
                        _category_size_keys_cache[cat_id] = []
                    if key_name not in _category_size_keys_cache[cat_id]:
                        _category_size_keys_cache[cat_id].append(key_name)
        log(f"카테고리별 size_details 키 매핑 {len(_category_size_keys_cache)}개 카테고리 로드")
    except Exception as e:
        log(f"size_details.csv 로드 실패: {e}", "WARNING")

    return _category_size_keys_cache


def filter_details_by_category(details: List[Dict], category_id: int) -> List[Dict]:
    """category_id에 허용된 키만 남기고 나머지 제거"""
    keys_map = load_category_size_keys()
    allowed_keys = keys_map.get(category_id)
    if not allowed_keys:
        return []
    filtered = [d for d in details if d.get('key') in allowed_keys]
    if len(filtered) != len(details):
        removed = [d['key'] for d in details if d.get('key') not in allowed_keys]
        log(f"  - size_details 필터링: category_id={category_id}, 제거된 키={removed}")
    return filtered


def generate_model_no_variants(model_no: str) -> List[str]:
    """
    모델명을 여러 형태로 생성하여 리스트로 반환
    예: "WVBDK M25085 AAD" → ["WVBDK M25085 AAD", "WVBDKM25085AAD"]
    """
    if not model_no:
        return []

    model_no = re.sub(r'\s*\([^)]*\)', '', model_no).strip()
    variants = [model_no]  # 1. 원본

    # 2. 특수문자를 공백으로 바꾼 버전 (하이픈, 언더스코어 등)
    space_replaced = re.sub(r'[-_/\\.,]+', ' ', model_no)
    if space_replaced != model_no and space_replaced not in variants:
        variants.append(space_replaced)

    # 3. 모든 특수문자와 공백을 제거한 버전
    no_special = re.sub(r'[^A-Za-z0-9]', '', model_no)
    if no_special and no_special not in variants:
        variants.append(no_special)

    return variants  # 리스트 반환


def random_delay() -> None:
    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
    time.sleep(delay)


def decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def parse_price(price_text: str) -> Optional[int]:
    if not price_text:
        return None
    numbers = re.findall(r'[\d,]+', price_text)
    if not numbers:
        return None
    try:
        return int(numbers[0].replace(',', ''))
    except ValueError:
        return None

def truncate_buyma_name(text, max_limit=60):
    """
    Buyma 상품명 제한(반각 60자/전각 30자)에 맞춰 문자열을 자르는 함수
    - 전각(한글, 한자, 일본어 등): 2로 계산
    - 반각(영어, 숫자, 기호): 1로 계산
    """
    if not text:
        return ""

    current_length = 0
    result = ""

    for char in text:
        # 문자의 폭(width) 확인
        # 'F'(Fullwidth), 'W'(Wide), 'A'(Ambiguous)는 전각(2)으로 취급
        eaw = unicodedata.east_asian_width(char)
        if eaw in ('F', 'W', 'A'):
            char_width = 2
        else:
            char_width = 1

        # 제한 길이를 초과하면 중단
        if current_length + char_width > max_limit:
            break

        result += char
        current_length += char_width

    return result


def truncate_option_value(text, max_limit=26):
    """
    バイマ옵션명 제한(반각 26자/전각 13자)에 맞춰 자르는 함수
    1. + 구분자가 있으면 → 첫 번째 색상 + ' 外N色'
    2. 그래도 초과면 → '...' 포함하여 max_limit 이내로 truncate
    """
    if not text:
        return ""

    def buyma_width(s):
        w = 0
        for c in s:
            eaw = unicodedata.east_asian_width(c)
            w += 2 if eaw in ('F', 'W', 'A') else 1
        return w

    if buyma_width(text) <= max_limit:
        return text

    if '+' in text:
        parts = [p.strip() for p in text.split('+') if p.strip()]
        if len(parts) > 1:
            first = parts[0]
            suffix = f' 外{len(parts) - 1}色'
            combined = first + suffix
            if buyma_width(combined) <= max_limit:
                return combined

    result = ""
    current_length = 0
    dots = "..."
    dots_width = 3
    limit = max_limit - dots_width

    for char in text:
        eaw = unicodedata.east_asian_width(char)
        char_width = 2 if eaw in ('F', 'W', 'A') else 1
        if current_length + char_width > limit:
            break
        result += char
        current_length += char_width

    return result + dots


# =====================================================
# 마진 계산 (buyma_product_register.py와 동일)
# =====================================================

def calculate_margin(price_jpy: int, purchase_price_krw: float,
                     shipping_fee_krw: int = DEFAULT_SHIPPING_FEE) -> Dict:
    """
    등록 직전 마진 재계산 (buyma_product_register.py와 동일)
    """
    # 1. 바이마 판매가 (원화)
    sales_price_krw = price_jpy * EXCHANGE_RATE

    # 2. 판매수수료 (원화)
    sales_fee_krw = sales_price_krw * SALES_FEE_RATE

    # 3. 실수령액 (원화)
    net_income_krw = sales_price_krw - sales_fee_krw

    # 4. 총 원가 (원화)
    total_cost_krw = purchase_price_krw + shipping_fee_krw

    # 5. 마진 (부가세 환급 전)
    margin_before_vat = net_income_krw - total_cost_krw

    # 6. 부가세 환급액
    vat_refund = purchase_price_krw / 11

    # 7. 최종 마진 (부가세 환급 포함)
    final_margin_krw = margin_before_vat + vat_refund

    # 8. 마진율
    margin_rate = (final_margin_krw / sales_price_krw) * 100 if sales_price_krw > 0 else 0

    return {
        'is_profitable': final_margin_krw > 0,
        'margin_krw': round(final_margin_krw, 0),
        'margin_rate': round(margin_rate, 2),
        'sales_price_krw': round(sales_price_krw, 0),
        'net_income_krw': round(net_income_krw, 0),
        'total_cost_krw': round(total_cost_krw, 0),
    }


# =====================================================
# 네이버 상품 상세 fetch (Playwright XHR 가로채기)
# =====================================================

def _mall_type(source_site: str) -> str:
    """mall_name → 'smartstore' 또는 'brandstore'"""
    if source_site in BRANDSTORE_MALLS:
        return 'brandstore'
    return 'smartstore'


def _extract_product_no(product_url: str) -> Optional[str]:
    """네이버 상품 URL에서 product_no 추출
    예: https://smartstore.naver.com/<store>/products/12345 → '12345'
        https://brand.naver.com/<store>/products/12345 → '12345'
    """
    m = re.search(r'/products/(\d+)', product_url)
    return m.group(1) if m else None


def fetch_naver_detail(page, product_url: str, source_site: str) -> Tuple[Optional[Dict], Optional[Dict], Optional[str]]:
    """상품 상세 페이지 방문 → 두 XHR(products + product-benefits) 가로채기

    Returns:
        (product_json, benefits_json, error)
          - error == "NOT_FOUND": 404 (상품 삭제)
          - error == "CAPTCHA": 캡챠 감지
          - error == "TIMEOUT" / "LOAD_FAIL" 등: 일시적 오류
    """
    product_no = _extract_product_no(product_url)
    if not product_no:
        return None, None, f"URL에서 product_no 추출 실패: {product_url}"

    mall_type = _mall_type(source_site)
    prefix = '/n/v2' if mall_type == 'brandstore' else '/i/v2'

    product_re = re.compile(rf'{prefix}/channels/[^/]+/products/{product_no}(\?|$)')
    benefits_re = re.compile(rf'{prefix}/channels/[^/]+/product-benefits/{product_no}(\?|$)')

    captured = []

    def on_response(response):
        url = response.url
        if product_re.search(url) or benefits_re.search(url):
            captured.append(response)

    page.on('response', on_response)
    try:
        try:
            # 필요한 XHR(products + product-benefits)은 DOM 로드 시점에 이미 발사됨
            # networkidle은 네이버 광고/트래킹 스크립트 때문에 도달 불가 → 제거
            page.goto(product_url, timeout=DETAIL_PAGE_TIMEOUT, wait_until='domcontentloaded')
            # XHR 응답이 완료될 시간만 짧게 확보
            page.wait_for_timeout(500)
        except Exception as e:
            return None, None, f"LOAD_FAIL: {e}"

        # 캡챠 체크
        try:
            page_title = page.title()
        except Exception:
            page_title = ''
        if '보안' in page_title or 'captcha' in page_title.lower():
            return None, None, "CAPTCHA"

        product = None
        benefits = None
        product_status = None
        for resp in captured:
            url = resp.url
            try:
                if benefits_re.search(url):
                    if resp.status == 200:
                        benefits = resp.json()
                elif product_re.search(url):
                    product_status = resp.status
                    if resp.status == 200:
                        product = resp.json()
            except Exception:
                pass

        if product is None:
            if product_status == 404:
                return None, None, "NOT_FOUND"
            return None, None, "XHR_MISS"

        return product, benefits, None
    finally:
        page.remove_listener('response', on_response)


# =====================================================
# 재고/가격 동기화 클래스
# =====================================================

class StockPriceSynchronizer:

    def __init__(self):
        self.buyma_session = requests.Session()
        self.buyma_session.headers.update(BUYMA_HEADERS)

        # 403 차단 플래그 (스레드 간 공유 — Playwright 공용이지만 인터페이스 호환용)
        self.is_blocked = False
        self.block_lock = threading.Lock()

        # Playwright: sync 모드로 브라우저 1개 기동, 쿠키 로드, 페이지 재사용
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None

    def start_playwright(self):
        """Playwright 브라우저 기동 + 쿠키 로드 (run 직전 호출)"""
        if self.page is not None:
            return
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=False)
        self.context = self.browser.new_context(
            viewport={'width': 1280, 'height': 900},
            locale='ko-KR',
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/146.0.0.0 Safari/537.36'
            ),
        )
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                self.context.add_cookies(json.load(f))
            log(f"쿠키 로드: {COOKIE_FILE}")
        else:
            log(f"쿠키 없음 ({COOKIE_FILE}) — premiumsneakers_collector.py --login 필요할 수 있음", "WARNING")
        self.page = self.context.new_page()

    def stop_playwright(self):
        """Playwright 종료 (run 종료 후 호출)"""
        try:
            if self.browser is not None:
                self.browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None

    def get_connection(self) -> pymysql.Connection:
        return pymysql.connect(**DB_CONFIG)

    # -------------------------------------------------
    # 1. 동기화 대상 상품 조회
    # -------------------------------------------------
    def get_products_to_sync(self, limit: int = None, brand: str = None, product_id: int = None,
                              source: str = None) -> List[Dict]:
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                # source_site 필터: --source로 단일 mall, 미지정시 11개 전체
                if source:
                    if source not in NAVER_MALLS:
                        raise ValueError(f"지원하지 않는 source: {source} (지원: {NAVER_MALLS})")
                    source_list = [source]
                else:
                    source_list = NAVER_MALLS
                placeholders = ','.join(['%s'] * len(source_list))

                sql = f"""
                    SELECT
                        ap.id,
                        ap.buyma_product_id,
                        ap.reference_number,
                        ap.name,
                        ap.brand_name,
                        ap.model_no,
                        ap.category_id,
                        ap.source_site,
                        ap.source_product_url,
                        ap.original_price_krw,
                        ap.purchase_price_krw,
                        ap.original_price_jpy,
                        ap.price,
                        ap.buyma_lowest_price,
                        ap.expected_shipping_fee,
                        ap.buyma_lowest_price_checked_at
                    FROM ace_products ap
                    WHERE ap.is_published = 1
                      AND ap.buyma_product_id IS NOT NULL
                      AND ap.source_product_url IS NOT NULL
                      AND ap.is_active = 1
                      AND ap.source_site IN ({placeholders})
                """
                params = list(source_list)

                if product_id:
                    sql += " AND ap.id = %s"
                    params.append(product_id)

                if brand:
                    sql += " AND UPPER(ap.brand_name) LIKE %s"
                    params.append(f"%{brand.upper()}%")

                sql += " ORDER BY ap.buyma_lowest_price_checked_at ASC, ap.id ASC"

                if limit:
                    sql += " LIMIT %s"
                    params.append(limit)

                cursor.execute(sql, params)
                return cursor.fetchall()
        finally:
            conn.close()

    # -------------------------------------------------
    # 2. 네이버에서 가격/재고 수집 (Playwright XHR 캡처)
    # -------------------------------------------------
    def collect_from_naver(self, product_url: str, source_site: str) -> Tuple[Dict, Optional[str]]:
        """
        네이버 스마트/브랜드스토어 상품 상세 XHR 캡처로 가격/재고 수집

        - products XHR: salePrice, optionCombinations[].stockQuantity, saleStatus, statusType
        - product-benefits XHR: optimalDiscount.totalDiscountResult.summary.totalPayAmount (쿠폰 적용가)

        Returns:
            (result, error): result = {'original_price', 'sale_price', 'options'[]}
        """
        # 재시도 루프 (XHR_MISS / LOAD_FAIL 대응)
        product = None
        benefits = None
        last_err = None
        for attempt in range(DETAIL_MAX_RETRIES + 1):
            product, benefits, err = fetch_naver_detail(self.page, product_url, source_site)
            if product is not None:
                last_err = None
                break
            last_err = err
            if err == "NOT_FOUND":
                return {}, "상품 삭제됨 (404)"
            if err == "CAPTCHA":
                # 캡챠는 전체 세션 차단 — 바로 중단 (스레드 공유 차단 플래그 셋)
                with self.block_lock:
                    self.is_blocked = True
                return {}, f"캡챠 감지: {err}"
            if attempt < DETAIL_MAX_RETRIES:
                time.sleep(5 * (attempt + 1))

        if product is None:
            return {}, f"일시적 오류 (스킵): {last_err}"

        try:
            result = {'original_price': 0, 'sale_price': 0, 'options': []}

            # 가격
            original_price = int(product.get('salePrice') or 0)
            sale_price = original_price

            # 쿠폰 적용가 (1순위: benefits.optimalDiscount)
            if benefits:
                try:
                    pay = (((benefits.get('optimalDiscount') or {})
                            .get('totalDiscountResult') or {})
                            .get('summary') or {}).get('totalPayAmount')
                    if pay and pay > 0:
                        sale_price = int(pay)
                except Exception:
                    pass

            # fallback: product.benefitsView.discountedSalePrice
            if sale_price == original_price:
                bv = product.get('benefitsView') or {}
                d = bv.get('discountedSalePrice') or 0
                if d and 0 < d < original_price:
                    sale_price = int(d)

            result['original_price'] = original_price
            result['sale_price'] = sale_price

            # 판매 상태 체크
            # product.statusType: 'SALE'(판매중) 이외는 판매 종료 취급
            status_type = (product.get('statusType') or product.get('saleStatus') or '').upper()
            if status_type and status_type not in ('SALE', 'ONSALE', 'READY'):
                return {}, "판매 종료 상품"

            # 옵션별 재고 (optionCombinations)
            # group_types: product.options[].groupName 으로 색상/사이즈 분별
            opt_groups = product.get('options') or []
            group_types = []
            for g in opt_groups:
                gname = (g.get('groupName') or '').strip()
                gname_up = gname.upper()
                if '색상' in gname or '컬러' in gname or 'COLOR' in gname_up:
                    group_types.append('color')
                elif '모델' in gname or 'MODEL' in gname_up or '품번' in gname or '스타일' in gname:
                    group_types.append('skip')
                else:
                    group_types.append('size')

            def _normalize_size(s: str) -> str:
                s = (s or '').strip()
                if s.upper() in {'ONE SIZE', 'ONESIZE', '단일사이즈', '단일 사이즈', '단일',
                                 '원사이즈', '원 사이즈', 'UNI', 'FREE'}:
                    return 'FREE'
                return s

            combos = product.get('optionCombinations') or []
            for i, c in enumerate(combos):
                n1 = (c.get('optionName1') or '').strip()
                n2 = (c.get('optionName2') or '').strip()
                names = [n for n in [n1, n2] if n]

                color_val = ''
                size_val = ''
                for idx, name in enumerate(names):
                    gtype = group_types[idx] if idx < len(group_types) else 'size'
                    if gtype == 'color':
                        color_val = name
                    elif gtype == 'skip':
                        continue
                    else:
                        size_val = _normalize_size(name)

                if not size_val and not color_val:
                    size_val = 'FREE'
                if not size_val:
                    size_val = 'FREE'

                stock = int(c.get('stockQuantity') or 0)
                result['options'].append({
                    'color': color_val or '',
                    'size': size_val,
                    'option_code': str(c.get('id', i)),
                    'status': 'in_stock' if stock > 0 else 'out_of_stock',
                })

            # 옵션 없는 단일 상품
            if not result['options']:
                stock = int(product.get('stockQuantity') or 0)
                result['options'].append({
                    'color': '', 'size': 'FREE',
                    'option_code': '', 'status': 'in_stock' if stock > 0 else 'out_of_stock'
                })

            return result, None

        except Exception as e:
            return {}, f"파싱 오류: {str(e)}"

    # -------------------------------------------------
    # 3. 바이마 최저가 수집 (내 상품 제외 경쟁자 최저가)
    # -------------------------------------------------
    def get_buyma_lowest_price(self, model_no: str) -> Tuple[Optional[int], Optional[str]]:
        """
        바이마에서 경쟁자 최저가를 수집합니다.
        - 내 상품(BUYMA_BUYER_ID)은 제외하고 경쟁자 최저가를 반환
        - 내 상품만 있으면 None 반환 (경쟁자 없음)
        """
        if not model_no:
            return None, "모델번호 없음"

        encoded = urllib.parse.quote(model_no, safe='')
        url = BUYMA_SEARCH_URL.format(model_no=encoded)

        try:
            response = self.buyma_session.get(url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            products = soup.find_all('li', class_='product')
            if not products:
                return None, "검색 결과 없음"

            # 모든 상품을 순회하며 경쟁자(내 상품 제외, 중고 제외) 최저가 찾기
            for product in products:
                # 1. 중고 상품 제외
                used_tag = product.find('span', class_='product_used_tag')
                if used_tag:
                    continue

                # 2. 내 상품 제외
                buyer_elem = product.select_one('.product_Buyer a')
                if buyer_elem:
                    href = buyer_elem.get('href', '')
                    buyer_match = re.search(r'/buyer/(\d+)', href)
                    if buyer_match:
                        buyer_id = buyer_match.group(1)
                        if BUYMA_BUYER_ID and buyer_id == BUYMA_BUYER_ID:
                            continue

                # 3. 가격 추출 (경쟁자 상품)
                price_elem = product.find('span', class_='Price_Txt')
                if price_elem:
                    price = parse_price(price_elem.get_text(strip=True))
                    if price:
                        return price, None

            # 내 상품만 있거나 가격 추출 실패
            return None, "경쟁자 없음 (내 상품/중고만 존재)"

        except requests.exceptions.Timeout:
            return None, "요청 타임아웃"
        except requests.exceptions.RequestException as e:
            return None, f"요청 오류: {str(e)}"
        except Exception as e:
            return None, f"파싱 오류: {str(e)}"

    # -------------------------------------------------
    # 4. 배송비 조회
    # -------------------------------------------------
    def get_shipping_fee(self, category_id: int) -> int:
        if not category_id:
            return DEFAULT_SHIPPING_FEE
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT expected_shipping_fee
                    FROM buyma_master_categories_data
                    WHERE buyma_category_id = %s
                """, (category_id,))
                row = cursor.fetchone()
                if row and row.get('expected_shipping_fee'):
                    return int(row['expected_shipping_fee'])
                return DEFAULT_SHIPPING_FEE
        except:
            return DEFAULT_SHIPPING_FEE
        finally:
            conn.close()

    # -------------------------------------------------
    # 5. DB 조회/업데이트
    # -------------------------------------------------
    def get_current_variants(self, ace_product_id: int) -> List[Dict]:
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, color_value, size_value, stock_type
                    FROM ace_product_variants
                    WHERE ace_product_id = %s
                """, (ace_product_id,))
                return cursor.fetchall()
        finally:
            conn.close()

    def detect_stock_changes(self, db_variants: List[Dict], mall_options: List[Dict]) -> List[Dict]:
        changes = []

        # 단일 옵션 상품 처리: DB 1개, 카시나 1개이면 이름 상관없이 직접 매칭
        if len(db_variants) == 1 and len(mall_options) == 1:
            variant = db_variants[0]
            mall_opt = mall_options[0]
            db_status = variant.get('stock_type', 'purchase_for_order')
            db_is_available = db_status != 'out_of_stock'
            mall_is_available = mall_opt['status'] == 'in_stock'

            if db_is_available and not mall_is_available:
                changes.append({
                    'variant_id': variant['id'],
                    'color': variant.get('color_value'),
                    'size': variant.get('size_value'),
                    'old_status': db_status,
                    'new_status': 'out_of_stock',
                    'change_type': 'soldout'
                })
            elif not db_is_available and mall_is_available:
                changes.append({
                    'variant_id': variant['id'],
                    'color': variant.get('color_value'),
                    'size': variant.get('size_value'),
                    'old_status': db_status,
                    'new_status': 'purchase_for_order',
                    'change_type': 'restock'
                })
            return changes

        # 다중 옵션 상품: 기존 로직 (이름으로 매칭)
        mall_map = {}
        for item in mall_options:
            key = (item.get('color', '').strip().lower(), item.get('size', '').strip().lower())
            mall_map[key] = item['status']

        for variant in db_variants:
            db_color = (variant.get('color_value') or '').strip().lower()
            db_size = (variant.get('size_value') or '').strip().lower()
            db_status = variant.get('stock_type', 'purchase_for_order')
            db_is_available = db_status != 'out_of_stock'

            key = (db_color, db_size)
            if key in mall_map:
                mall_is_available = mall_map[key] == 'in_stock'
                if db_is_available and not mall_is_available:
                    changes.append({
                        'variant_id': variant['id'],
                        'color': variant.get('color_value'),
                        'size': variant.get('size_value'),
                        'old_status': db_status,
                        'new_status': 'out_of_stock',
                        'change_type': 'soldout'
                    })
                elif not db_is_available and mall_is_available:
                    changes.append({
                        'variant_id': variant['id'],
                        'color': variant.get('color_value'),
                        'size': variant.get('size_value'),
                        'old_status': db_status,
                        'new_status': 'purchase_for_order',
                        'change_type': 'restock'
                    })
            else:
                if db_is_available:
                    changes.append({
                        'variant_id': variant['id'],
                        'color': variant.get('color_value'),
                        'size': variant.get('size_value'),
                        'old_status': db_status,
                        'new_status': 'out_of_stock',
                        'change_type': 'not_found'
                    })
        return changes

    def update_ace_products_price(self, ace_product_id: int, original_price_krw: int,
                                   purchase_price_krw: int, price_jpy: int,
                                   original_price_jpy: int, buyma_lowest_price: int,
                                   margin_rate: float, margin_amount_krw: float = None,
                                   is_lowest_price: int = None, purchase_price_jpy: int = None) -> None:
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ace_products
                    SET original_price_krw = %s,
                        purchase_price_krw = %s,
                        price = %s,
                        original_price_jpy = %s,
                        buyma_lowest_price = %s,
                        margin_rate = %s,
                        margin_amount_krw = %s,
                        is_lowest_price = %s,
                        purchase_price_jpy = %s,
                        margin_calculated_at = NOW(),
                        buyma_lowest_price_checked_at = NOW()
                    WHERE id = %s
                """, (original_price_krw, purchase_price_krw, price_jpy,
                      original_price_jpy, buyma_lowest_price, margin_rate,
                      margin_amount_krw, is_lowest_price, purchase_price_jpy,
                      ace_product_id))
                conn.commit()
        finally:
            conn.close()

    def update_ace_variants_stock(self, stock_changes: List[Dict]) -> None:
        if not stock_changes:
            return
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                for change in stock_changes:
                    cursor.execute("""
                        UPDATE ace_product_variants
                        SET stock_type = %s,
                            source_stock_status = %s
                        WHERE id = %s
                    """, (change['new_status'], change['new_status'], change['variant_id']))
                conn.commit()
        finally:
            conn.close()

    def update_sync_time_only(self, ace_product_id: int) -> None:
        """변경 없을 때 체크 시간만 갱신"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ace_products
                    SET buyma_lowest_price_checked_at = NOW()
                    WHERE id = %s
                """, (ace_product_id,))
                conn.commit()
        finally:
            conn.close()

    def update_product_after_api_call(self, ace_product_id: int, request_data: Dict, response: Dict) -> None:
        """API 요청 후 상품 상태 업데이트 (buyma_product_register.py와 동일)"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                new_status = 'pending' if response.get('success') else 'api_error'
                cursor.execute("""
                    UPDATE ace_products SET status = %s WHERE id = %s
                """, (new_status, ace_product_id))
                cursor.execute("""
                    INSERT INTO ace_product_api_logs (ace_product_id, api_request_json, api_response_json, last_api_call_at)
                    VALUES (%s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE api_request_json = VALUES(api_request_json), api_response_json = VALUES(api_response_json), last_api_call_at = NOW()
                """, (
                    ace_product_id,
                    json.dumps(request_data, ensure_ascii=False, default=decimal_to_float),
                    json.dumps(response, ensure_ascii=False)
                ))
                conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------
    # 6. 바이마 API 호출 (buyma_product_register.py와 동일)
    # -------------------------------------------------
    def get_product_data_for_api(self, ace_product_id: int) -> Dict:
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, buyma_product_id, reference_number, name, brand_id, brand_name,
                           category_id, price, original_price_jpy, buying_shop_name,
                           buyma_model_id, colorsize_comments_jp, available_until,
                           expected_shipping_fee, purchase_price_krw, model_no,
                           source_product_url, source_site,
                           is_buyma_locked,
                           locked_name, locked_brand_id, locked_category_id, locked_reference_number
                    FROM ace_products WHERE id = %s
                """, (ace_product_id,))
                product = cursor.fetchone()

                cursor.execute("""
                    SELECT position, cloudflare_image_url
                    FROM ace_product_images
                    WHERE ace_product_id = %s AND cloudflare_image_url IS NOT NULL
                    ORDER BY position LIMIT 20
                """, (ace_product_id,))
                images = cursor.fetchall()

                cursor.execute("""
                    SELECT option_type, value, master_id, position, details_json
                    FROM ace_product_options
                    WHERE ace_product_id = %s
                    ORDER BY option_type DESC, position
                """, (ace_product_id,))
                options = cursor.fetchall()

                cursor.execute("""
                    SELECT color_value, size_value, stock_type, stocks
                    FROM ace_product_variants
                    WHERE ace_product_id = %s
                """, (ace_product_id,))
                variants = cursor.fetchall()

                return {'product': product, 'images': images, 'options': options, 'variants': variants}
        finally:
            conn.close()

    def build_buyma_request(self, data: Dict, is_delete: bool = False) -> Dict:
        """바이마 API 요청 JSON 구성 (buyma_product_register.py와 동일)"""
        product = data['product']
        images = data['images']
        options = data['options']
        variants = data['variants']

        # 삭제 요청
        if is_delete:
            return {
                "product": {
                    "control": "delete",
                    "reference_number": product['reference_number']
                }
            }

        # 전체 품절 → 삭제
        all_out_of_stock = all(v['stock_type'] == 'out_of_stock' for v in variants)
        if all_out_of_stock:
            return {
                "product": {
                    "control": "delete",
                    "reference_number": product['reference_number']
                }
            }

        # 1. 모델명 변형 생성
        model_no_list = generate_model_no_variants(product.get('model_no', ''))

        # 1-1. Comments용 텍스트 변환 (기존 로직 유지)
        model_no_text = '\n'.join(model_no_list)

        # 1-2. style_numbers 배열 생성 (신규 추가 요청사항)
        # [{"number": "...", "memo": ""}, ...] 형식
        style_numbers = [{"number": num, "memo": ""} for num in model_no_list]

        # 고정 공지사항 (buyma_product_register.py와 동일)
        fixed_comments = """☆☆☆ ご購入前にご確認ください ☆☆☆

◆商品は直営店をはじめ、 デパート、 公式オンラインショップ、ショッピングモールなどの正規品を取り扱う店舗にて買い付けております。100％正規品ですのでご安心ください。

◆「あんしんプラス」へご加入の場合、「サイズがあわない」「イメージと違う」場合に「返品補償制度」をご利用頂けます。
※「返品対象商品」に限ります。詳しくは右記URLをご参照ください。https://qa.buyma.com/trouble/5206.html

◆ご注文～お届けまで
手元在庫有：【ご注文確定】 →【梱包】 → 【発送】 → 【お届け】
手元在庫無し：【ご注文確定】 →【買付】 →【検品】 →【梱包】 →【発送】→【お届け】

◆配送方法/日数
通常国際便（OCS）：【商品準備2-5日 】+ 【発送～お届け5-9日】
※平常時の目安です。繁忙期/非常時はお届け日が前後する場合もございます。詳しくはお問合せください。
※当店では検品時に不良/不具合がある場合は良品に交換をしてお送りしております。当理由でお時間を頂戴する場合は都度ご報告させて頂いております。

◆「お荷物追跡番号あり」にて配送しますので、随時、配送状況をご確認いただけます。
◆土・日・祝日は発送は休務のため、休み明けに順次発送となります。

◆海外製品は「MADE IN JAPAN」の製品に比べて、若干見劣りする場合もございます。
返品・交換にあたる不具合の条件に関しては「お取引について」をご確認ください。

◆当店では、日本完売品、日本未入荷アイテム、限定品、
メンズ、レディース、キッズの シューズ（スニーカー等）や衣類をメインに取り扱っております。
(カップル,ファミリー、ペアルック、親子リンク)
韓国の最新トレンドや新作アイテムを順次出品しており、

◆交換・返品・キャンセル
返品と交換に関する規定は、バイマ規定によりお客様の理由による返品はお受けいたしかねますので、ご購入には慎重にお願いいたします。
不良品・誤配送は交換、または返品が可能です。
モニター環境による色違い、サイズ測定方法による1~3cm程度の誤差、糸くず、糸の始末などは欠陥でみなされません。
製品の大きさは測定方法によって1~3cm程度の誤差が生じることがありますが、欠陥ではございません。

◆不良品について
検品は行っておりますが、海外製品は日本商品よりも検品基準が低いです。
下記の理由は返品や交換の原因にはなりません。
- 縫製の粗さ
- 縫い終わり部分の糸が切れていないで残っている
- 生地の色ムラ
- ミリ段位の傷
- 若干の汚れ、シミ
- 製造過程での接着剤の付着など"""

        # colorsize_footer 섹션별 분리 (앞에서부터 제거 대상, 뒤가 더 중요)
        colorsize_footer_sections = [
            """

★最安値に挑戦中！★
本商品は、私たちKONNECT（コネクト）が
お客様に少しでもお安く提供できるよう、
最安値での出品に努めた商品です。
出品時の市場価格調査はもちろん、
定期的にも価格チェックを行っております。
（※ただし、価格はリアルタイムで変動するため、
タイミングによっては最安値ではなくなる場合もございます。
あらかじめご了承ください。）""",
            """

★追加料金は一切なし！★
BUYMAでの決済金額以外、追加費用は一切かかりませんのでご安心ください。
関税・消費税・送料はすべて商品価格に含まれております。お客様が追加で支払う必要はございません。""",
            """

★安心の追跡付き発送★
KONNECT（コネクト）では、すべて追跡可能な配送方法でお届けいたします。
商品発送後、1～2日ほどでBUYMA上にて追跡番号をご確認いただけます""",
            """

★ご購入前の在庫確認のお願い★
在庫状況はリアルタイムではなく、人気の商品は注文時す
でに《欠品》となっている可能性もございます。
確実でスピーディーなお取引と、注文確定後のキャンセル
によるお客様のご負担をなくすため、ご注文手続きの前に
【在庫確認】のご協力をお願いしております。
ご検討されている方も、お気軽にお問い合わせ欄からお声
掛け下さいませ。""",
            """

※ 上記参考価格は現地参考価格を10KRW ＝ 1.1円で換算したものです
※仕入れはデパートや公式オンラインショップなど、100％正規品のみ扱っております"""
        ]

        # available_until
        available_until = product.get('available_until')
        if available_until:
            if isinstance(available_until, str):
                available_until_str = available_until.replace('-', '/')
            else:
                available_until_str = available_until.strftime('%Y/%m/%d')
        else:
            available_until_str = (datetime.now() + timedelta(days=90)).strftime('%Y/%m/%d')

        # images
        images_arr = [{"path": row['cloudflare_image_url'], "position": row['position']} for row in images]

        # ★ variants에서 유효한 size/color 추출 (options 필터링용 - 방어 코드)
        valid_sizes = set()
        valid_colors = set()
        for v in variants:
            if v['color_value']:
                valid_colors.add(v['color_value'])
            if v['size_value']:
                valid_sizes.add(v['size_value'])

        # options (★ variants에 있는 size/color만 포함 + category_id별 size_details 키 필터링)
        options_arr = []
        for row in options:
            # ★ variants에 있는 것만 포함 (방어 코드)
            if row['option_type'] == 'size' and row['value'] not in valid_sizes:
                continue
            if row['option_type'] == 'color' and row['value'] not in valid_colors:
                continue
            opt = {
                "type": row['option_type'],
                "value": truncate_option_value(row['value']),
                "position": row['position'],
                "master_id": row['master_id'] or 0
            }
            if row['option_type'] == 'size' and row.get('details_json'):
                try:
                    details = json.loads(row['details_json'])
                    if details:
                        # ★ category_id 기준 허용 키 필터링
                        cat_id = product.get('locked_category_id') or product['category_id']
                        if cat_id:
                            details = filter_details_by_category(details, int(cat_id))
                        if details:
                            opt['details'] = details
                except:
                    pass
            options_arr.append(opt)

        # variants (buyma_product_register.py와 동일)
        variants_arr = []
        for v in variants:
            is_in_stock = v['stock_type'] != 'out_of_stock' and (v['stocks'] is None or v['stocks'] > 0)
            variant = {
                "options": [],
                "stock_type": "purchase_for_order" if is_in_stock else "out_of_stock"
            }
            if v['color_value']:
                variant["options"].append({"type": "color", "value": truncate_option_value(v['color_value'])})
            if v['size_value']:
                variant["options"].append({"type": "size", "value": truncate_option_value(v['size_value'])})
            variants_arr.append(variant)

        # 배송 방법
        shipping_methods = [{"shipping_method_id": sm_id} for sm_id in BUYMA_FIXED_VALUES['shipping_methods']]

        # 불변 필드 결정: 잠금 상태면 locked_* 값 사용 (바이마 API 오류 방지)
        if product.get('is_buyma_locked') == 1:
            api_name = product.get('locked_name') or product['name']
            api_brand_id = product.get('locked_brand_id') or product['brand_id']
            api_category_id = product.get('locked_category_id') or product['category_id']
            api_reference_number = product.get('locked_reference_number') or product['reference_number']
        else:
            api_name = product['name']
            api_brand_id = product['brand_id']
            api_category_id = product['category_id']
            api_reference_number = product['reference_number']

        request_data = {
            "control": "publish",
            "id": product['buyma_product_id'],
            "reference_number": api_reference_number,
            "name": truncate_buyma_name(api_name),
            "comments": f"{api_name}\n{model_no_text}\n\n{fixed_comments}" if model_no_text else f"{api_name}\n\n{fixed_comments}",
            "brand_id": int(api_brand_id) if api_brand_id else 0,
            "category_id": int(api_category_id),
            "price": int(product['price']),
            "available_until": available_until_str,
            "buying_area_id": BUYMA_FIXED_VALUES['buying_area_id'],
            "shipping_area_id": BUYMA_FIXED_VALUES['shipping_area_id'],
            "shipping_methods": shipping_methods,
            "images": images_arr,
            "options": options_arr,
            "variants": variants_arr,
            "order_quantity": random.randint(90, 100),
            "theme_id": BUYMA_FIXED_VALUES['theme_id'],
            "duty": BUYMA_FIXED_VALUES['duty'],
        }

        # brand_id=0인 경우 (바이마 미등록 브랜드) brand_name 추가, style_numbers 제외
        if not api_brand_id or api_brand_id == 0:
            if product.get('brand_name'):
                request_data['brand_name'] = product['brand_name']
        else:
            request_data['style_numbers'] = style_numbers

        if product.get('buying_shop_name'):
            request_data['buying_shop_name'] = truncate_buying_shop_name(product['buying_shop_name'])
        if product.get('original_price_jpy'):
            ref_price = int(product['original_price_jpy'])
            if ref_price > request_data.get('price', 0):
                request_data['reference_price'] = ref_price
        if product.get('buyma_model_id'):
            request_data['model_id'] = product['buyma_model_id']

        if product.get('source_product_url'):
            request_data['shop_urls'] = [{
                "url": product['source_product_url'],
                "label": product.get('source_site', ''),
                "description": ""
            }]

        # colorsize_comments 글자수 제한 처리 (1000자)
        COLORSIZE_LIMIT = 1000
        base_colorsize = product.get('colorsize_comments_jp') or ""

        # 앞에서부터 섹션 누적 길이 계산하여 끝 인덱스 결정 (뒤 섹션부터 제거)
        remaining = COLORSIZE_LIMIT - len(base_colorsize)
        end_idx = 0  # 기본값: 아무것도 안 붙임

        cumulative_len = 0
        for i in range(len(colorsize_footer_sections)):
            section_len = len(colorsize_footer_sections[i])
            if cumulative_len + section_len <= remaining:
                cumulative_len += section_len
                end_idx = i + 1
            else:
                break

        # 선택된 섹션들만 합쳐서 footer 생성
        colorsize_footer = ''.join(colorsize_footer_sections[:end_idx])
        request_data['colorsize_comments'] = base_colorsize + colorsize_footer

        return {"product": request_data}

    def call_buyma_api(self, request_data: Dict) -> Dict:
        url = f"{API_BASE_URL}api/v1/products"
        headers = {
            "Content-Type": "application/json",
            "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN
        }
        try:
            response = requests.post(url, headers=headers, json=request_data, timeout=30)
            if response.status_code in [200, 201, 202]:
                return {"success": True, "status_code": response.status_code}
            else:
                return {"success": False, "status_code": response.status_code, "error": response.text}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": str(e)}

    def _delete_from_db(self, ace_product_id: int, raw_data_id: int = None):
        """ace 테이블 및 raw_scraped_data 삭제"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM ace_product_variants WHERE ace_product_id = %s", (ace_product_id,))
                cursor.execute("DELETE FROM ace_product_options WHERE ace_product_id = %s", (ace_product_id,))
                cursor.execute("DELETE FROM ace_product_images WHERE ace_product_id = %s", (ace_product_id,))
                cursor.execute("DELETE FROM ace_products WHERE id = %s", (ace_product_id,))

                if raw_data_id:
                    cursor.execute("DELETE FROM raw_scraped_data WHERE id = %s", (raw_data_id,))

            conn.commit()
        except Exception as e:
            log(f"DB 삭제 실패: {e}", "ERROR")
            conn.rollback()
        finally:
            conn.close()

    # -------------------------------------------------
    # 단일 상품 처리 (병렬 처리용)
    # -------------------------------------------------
    def process_single_product(self, product: Dict, idx: int, total: int, dry_run: bool, force: bool,
                                stats: Dict, stats_lock: threading.Lock) -> None:
        """단일 상품 동기화 처리 (스레드에서 실행) - 로그를 모아서 한 번에 출력"""

        # ★ 차단 상태 확인 (다른 스레드에서 차단되었으면 즉시 종료)
        with self.block_lock:
            if self.is_blocked:
                return

        logs = []  # 로그 버퍼
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def add_log(message: str, level: str = "INFO"):
            logs.append(f"[{timestamp}] [{level}] {message}")

        add_log(f"\n[{idx}/{total}] {product['brand_name']} - {product['name'][:30]} ...(상품번호: {product['model_no']})")

        try:
            # 1. 네이버 XHR로 가격/재고 수집
            mall_data, error = self.collect_from_naver(
                product['source_product_url'], product['source_site']
            )
            if error:
                add_log(f"  [{product['source_site']}] 수집 실패: {error}", "WARNING")

                # 일시적 API 오류 → 삭제하지 않고 스킵
                if "일시적 오류" in error:
                    add_log(f"  → API 일시적 오류, 이번 회차 스킵")
                    with stats_lock:
                        stats['skipped'] += 1
                    log_batch(logs)
                    return

                # 상품 삭제(404) 또는 판매 종료 → 바이마에서도 삭제
                add_log(f"  → 수집처에서 상품 삭제/종료됨 → 바이마 삭제 요청")

                # 바이마 삭제 API 호출
                if not dry_run:
                    api_data = self.get_product_data_for_api(product['id'])
                    request_json = self.build_buyma_request(api_data, is_delete=True)

                    add_log(f"  바이마 API 호출 중... (삭제)")
                    result = self.call_buyma_api(request_json)
                    self.update_product_after_api_call(product['id'], request_json, result)

                    with stats_lock:
                        if result.get('success'):
                            add_log(f"  API 성공 (삭제)")
                            stats['api_called'] += 1
                            stats['deleted'] += 1
                        else:
                            add_log(f"  API 실패: {result.get('error', 'Unknown')}", "ERROR")
                            stats['failed'] += 1
                else:
                    add_log(f"  [DRY-RUN] 삭제 API 호출 예정")
                    with stats_lock:
                        stats['deleted'] += 1

                log_batch(logs)  # 로그 한 번에 출력
                random_delay()
                return

            new_original_price = mall_data.get('original_price', 0)
            new_sale_price = mall_data.get('sale_price', 0)
            mall_options = mall_data.get('options', [])

            # 2. 재고 변동 감지 + 바이마 최저가 수집 (★ 병렬 실행)
            with ThreadPoolExecutor(max_workers=2) as sub_executor:
                future_lowest = sub_executor.submit(self.get_buyma_lowest_price, product.get('model_no'))

                # 최저가 수집과 동시에 재고 감지 진행
                db_variants = self.get_current_variants(product['id'])
                stock_changes = self.detect_stock_changes(db_variants, mall_options)

                # 최저가 결과 대기
                competitor_lowest_price, lp_error = future_lowest.result()

            # 4. 새 가격 계산 (JPY)
            if lp_error:
                if "경쟁자 없음" in lp_error:
                    # 경쟁자 없음 → 매입가 기반 20% 마진 가격 재계산
                    new_purchase = new_sale_price if new_sale_price else float(product.get('purchase_price_krw') or 0)
                    if new_purchase > 0:
                        shipping_fee_for_calc = product.get('expected_shipping_fee') or self.get_shipping_fee(product.get('category_id'))
                        total_cost = new_purchase + float(shipping_fee_for_calc)
                        vat_refund = new_purchase / 11.0
                        denominator = (1.0 - SALES_FEE_RATE) - 0.20  # 0.745
                        if denominator > 0:
                            target_price_krw = (total_cost - vat_refund) / denominator
                            target_price_jpy = int(target_price_krw / EXCHANGE_RATE)
                            new_price_jpy = target_price_jpy
                            add_log(f"  - 경쟁자 없음 → 마진20% 가격 재계산: ¥{new_price_jpy:,}")
                        else:
                            new_price_jpy = product.get('price')
                            add_log(f"  - 경쟁자 없음, 가격 역산 실패 → 가격 유지")
                    else:
                        new_price_jpy = product.get('price')
                        add_log(f"  - 경쟁자 없음, 매입가 없어 가격 유지")
                    new_lowest_price = None
                else:
                    add_log(f"  - 최저가 수집 실패: {lp_error}")
                    new_price_jpy = product.get('price')
                    new_lowest_price = product.get('buyma_lowest_price')
            else:
                old_price = product.get('price') or 0
                price_range_min = competitor_lowest_price - 9
                price_range_max = competitor_lowest_price - 1

                if price_range_min <= old_price <= price_range_max:
                    add_log(f"  - 경쟁자 최저가: ¥{competitor_lowest_price:,} → 내 가격 ¥{old_price:,} (범위 ¥{price_range_min:,}~¥{price_range_max:,} 내) → 유지")
                    new_price_jpy = old_price
                    new_lowest_price = competitor_lowest_price
                else:
                    new_price_jpy = competitor_lowest_price - random.randint(1, 9)
                    new_lowest_price = competitor_lowest_price
                    add_log(f"  - 경쟁자 최저가: ¥{competitor_lowest_price:,} → 내 가격: ¥{new_price_jpy:,}")

            new_original_price_jpy = int(new_original_price / EXCHANGE_RATE) if new_original_price else product.get('original_price_jpy') or 0
            new_purchase_price_krw = new_sale_price if new_sale_price else float(product.get('purchase_price_krw') or 0)

            # 5. 마진 계산
            shipping_fee = product.get('expected_shipping_fee') or self.get_shipping_fee(product.get('category_id'))
            margin_info = calculate_margin(new_price_jpy, new_purchase_price_krw, shipping_fee)

            add_log(f"  - 판매가: ¥{new_price_jpy:,} (₩{margin_info['sales_price_krw']:,.0f})")
            add_log(f"  - 매입가: ₩{new_purchase_price_krw:,.0f}, 배송비: ₩{shipping_fee:,}")
            add_log(f"  - 마진: ₩{margin_info['margin_krw']:,.0f} ({margin_info['margin_rate']:.1f}%)")

            # 6. 변경 여부 판단
            old_price_jpy = product.get('price') or 0
            old_original_price_jpy = product.get('original_price_jpy') or 0
            old_lowest_price = product.get('buyma_lowest_price') or 0

            need_api_call = False
            is_delete = False

            if not margin_info['is_profitable']:
                add_log(f"  - 마진 부족 (손해) → 삭제 요청", "WARNING")
                need_api_call = True
                is_delete = True

            if stock_changes:
                add_log(f"  - [변경] 재고 변동 {len(stock_changes)}건")
                for change in stock_changes:
                    ct = "품절" if change['change_type'] in ['soldout', 'not_found'] else "재입고"
                    add_log(f"      [{ct}] {change.get('color', '')} / {change.get('size', '')}")
                need_api_call = True

            if old_price_jpy != new_price_jpy:
                add_log(f"  - [변경] 판매가 ¥{old_price_jpy:,} → ¥{new_price_jpy:,}")
                need_api_call = True

            if old_original_price_jpy != new_original_price_jpy:
                add_log(f"  - [변경] 참고정가 ¥{old_original_price_jpy:,} → ¥{new_original_price_jpy:,}")
                need_api_call = True

            if old_lowest_price != new_lowest_price:
                old_lp_str = f"¥{old_lowest_price:,}" if old_lowest_price else "なし"
                new_lp_str = f"¥{new_lowest_price:,}" if new_lowest_price else "なし"
                add_log(f"  - [변경] 최저가 {old_lp_str} → {new_lp_str}")
                need_api_call = True

            if force and not need_api_call:
                add_log(f"  - [FORCE] 강제 API 호출")
                need_api_call = True

            # DRY-RUN
            if dry_run:
                if need_api_call:
                    add_log(f"  [DRY-RUN] {'삭제' if is_delete else '수정'} API 호출 예정")
                else:
                    add_log(f"  [DRY-RUN] 변경 없음, API 호출 안함")
                with stats_lock:
                    stats['success'] += 1
                log_batch(logs)  # 로그 한 번에 출력
                if need_api_call:
                    random_delay()
                return

            # 7. DB 업데이트
            if not is_delete:
                # is_lowest_price: 경쟁자 없으면 1, 있으면 내 가격 <= 최저가일 때 1
                if not new_lowest_price:
                    calc_is_lowest = 1
                else:
                    calc_is_lowest = 1 if new_price_jpy <= new_lowest_price else 0
                # purchase_price_jpy: 매입가(원) → 엔화 변환
                calc_purchase_price_jpy = round(new_purchase_price_krw / EXCHANGE_RATE) if new_purchase_price_krw else None

                self.update_ace_products_price(
                    product['id'], new_original_price, int(new_purchase_price_krw),
                    new_price_jpy, new_original_price_jpy, new_lowest_price,
                    margin_info['margin_rate'],
                    margin_amount_krw=margin_info['margin_krw'],
                    is_lowest_price=calc_is_lowest,
                    purchase_price_jpy=calc_purchase_price_jpy
                )
                if stock_changes:
                    self.update_ace_variants_stock(stock_changes)

            # 8. API 호출 여부 결정
            if need_api_call:
                api_data = self.get_product_data_for_api(product['id'])
                request_json = self.build_buyma_request(api_data, is_delete=is_delete)

                add_log(f"  바이마 API 호출 중... ({'삭제' if is_delete else '수정'})")
                result = self.call_buyma_api(request_json)
                self.update_product_after_api_call(product['id'], request_json, result)

                with stats_lock:
                    if result.get('success'):
                        add_log(f"  API 성공")
                        stats['api_called'] += 1
                        if is_delete:
                            stats['deleted'] += 1
                    else:
                        add_log(f"  API 실패: {result.get('error', 'Unknown')}", "ERROR")
                        stats['failed'] += 1
                    stats['success'] += 1

                log_batch(logs)  # 로그 한 번에 출력
                time.sleep(API_CALL_DELAY)
                random_delay()
            else:
                self.update_sync_time_only(product['id'])
                add_log(f"  변경 없음, API 호출 생략")
                with stats_lock:
                    stats['skipped'] += 1
                log_batch(logs)  # 로그 한 번에 출력

        except Exception as e:
            add_log(f"  처리 오류: {e}", "ERROR")
            with stats_lock:
                stats['failed'] += 1
            log_batch(logs)  # 로그 한 번에 출력

    # -------------------------------------------------
    # 메인 실행 로직 (직렬 처리 — Playwright 단일 세션 공유)
    # -------------------------------------------------
    def run(self, limit: int = None, brand: str = None, product_id: int = None,
            dry_run: bool = False, force: bool = False, source: str = None) -> Dict:
        log("=" * 60)
        log("재고/가격 동기화 시작 (naver 11 malls)")
        log(f"  옵션: source={source or 'ALL'}, id={product_id}, brand={brand}, "
            f"limit={limit}, dry_run={dry_run}, force={force}")
        log(f"  처리 방식: 직렬 (Playwright 단일 세션)")
        log("=" * 60)

        if dry_run:
            log("*** DRY RUN 모드 - 실제 업데이트 안함 ***", "WARNING")

        products = self.get_products_to_sync(
            limit=limit, brand=brand, product_id=product_id, source=source
        )
        log(f"동기화 대상 상품: {len(products)}개")

        if not products:
            log("동기화할 상품이 없습니다.")
            return {'total': 0, 'success': 0, 'skipped': 0, 'failed': 0}

        stats = {
            'total': len(products),
            'success': 0,
            'skipped': 0,
            'failed': 0,
            'deleted': 0,
            'api_called': 0,
            'errors': 0,
            'blocked': 0
        }
        stats_lock = threading.Lock()

        # Playwright 브라우저 기동
        self.start_playwright()

        try:
            # 직렬 처리 (Playwright 단일 페이지 공유)
            for idx, product in enumerate(products):
                # 캡챠 감지 등 세션 차단 시 즉시 중단
                with self.block_lock:
                    if self.is_blocked:
                        log("차단 감지 — 동기화 중단", "WARNING")
                        with stats_lock:
                            stats['blocked'] = len(products) - idx
                        break
                try:
                    self.process_single_product(
                        product, idx + 1, len(products),
                        dry_run, force, stats, stats_lock
                    )
                except Exception as e:
                    log(f"상품 처리 오류 (id={product.get('id')}): {e}", "ERROR")
                    with stats_lock:
                        stats['errors'] += 1
        finally:
            self.stop_playwright()

        # 결과
        log("\n" + "=" * 60)
        log("재고/가격 동기화 완료!")
        log(f"  총 대상: {stats['total']}건")
        log(f"  성공: {stats['success']}건")
        log(f"  스킵 (변경없음): {stats['skipped']}건")
        log(f"  실패: {stats['failed']}건")
        log(f"  API 호출: {stats['api_called']}건")
        log(f"  삭제: {stats['deleted']}건")
        log(f"  오류: {stats['errors']}건")
        log(f"  차단(중단): {stats['blocked']}건")
        log("=" * 60)

        return stats


# =====================================================
# 메인 실행
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='바이마 재고/가격 동기화 (네이버 11 malls)')
    parser.add_argument('--source', type=str, default=None,
                        help=f'특정 mall만 처리 (지원: {", ".join(NAVER_MALLS)}). 미지정시 11개 전체')
    parser.add_argument('--id', type=int, default=None, help='특정 상품 ID (ace_products.id)')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만 처리')
    parser.add_argument('--dry-run', action='store_true', help='테스트 모드 (실제 업데이트 안함)')
    parser.add_argument('--force', action='store_true', help='변경 없어도 강제 API 호출')

    args = parser.parse_args()

    if args.source and args.source not in NAVER_MALLS:
        log(f"지원하지 않는 --source: {args.source} (지원: {NAVER_MALLS})", "ERROR")
        exit(1)

    if not BUYMA_ACCESS_TOKEN:
        log("BUYMA_ACCESS_TOKEN이 설정되지 않았습니다.", "ERROR")
        return

    try:
        synchronizer = StockPriceSynchronizer()
        synchronizer.run(
            limit=args.limit,
            brand=args.brand,
            product_id=args.id,
            dry_run=args.dry_run,
            force=args.force,
            source=args.source,
        )
    except Exception as e:
        log(f"실행 중 오류 발생: {str(e)}", "ERROR")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
