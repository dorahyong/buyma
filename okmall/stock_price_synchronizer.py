# -*- coding: utf-8 -*-
"""
재고 및 가격 동기화 스크립트

바이마에 등록된 상품의 재고와 가격을 오케이몰에서 재수집하여
ace 테이블을 업데이트하고 바이마 API로 상품을 수정합니다.

처리 흐름:
1. ace_products에서 바이마 등록 상품 조회 (is_published=1, buyma_product_id 있음)
2. 오케이몰 재방문 → 현재 가격/재고 수집
3. ace_products 가격 UPDATE
4. ace_product_variants 재고 UPDATE
5. 바이마 최저가 수집
6. 마진 계산 (buyma_product_register.py와 동일)
7. 변경 여부 판단 후 바이마 API 호출

API 호출 기준:
- 재고 변동 (품절/재입고)
- 가격 변동 (price, reference_price)
- 마진 <= 0 (손해) → 삭제 요청
- 전체 품절 → 삭제 요청

사용법:
    python stock_price_synchronizer.py                    # 전체 실행
    python stock_price_synchronizer.py --brand BURBERRY   # 특정 브랜드만
    python stock_price_synchronizer.py --limit 100        # 최대 100개만
    python stock_price_synchronizer.py --dry-run          # 테스트 (API 호출 안함)
    python stock_price_synchronizer.py --force            # 변경 없어도 강제 API 호출

★ 봇 감지 방지 기능 (v2 - 개선됨):
- 30개마다 세션 교체 + 메인 페이지 방문
- 세션 내에서는 쿠키 유지 (자연스러운 브라우징)
- 랜덤 브라우저 프로필 (전체 헤더 세트)
- 자연스러운 Referer 체인
- 타임아웃 연속 3회 시 차단 감지 및 중지

작성일: 2026-02-02
수정일: 2026-02-12 (세션 관리 개선, 타임아웃 차단 감지)
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

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=True)

# =====================================================
# 설정값 (buyma_product_register.py와 동일)
# =====================================================

DB_CONFIG = {
    'host': os.getenv('DB_HOST', '54.180.248.182'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'block'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': os.getenv('DB_NAME', 'buyma'),
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

BUYMA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6',
    'Referer': 'https://www.buyma.com/',
}

BUYMA_SEARCH_URL = "https://www.buyma.com/r/-O3/{model_no}/"

# 딜레이 설정
REQUEST_DELAY_MIN = 1.5  # 오케이몰 요청 간 최소 딜레이
REQUEST_DELAY_MAX = 2.5  # 오케이몰 요청 간 최대 딜레이
API_CALL_DELAY = 1.0     # 바이마 API 호출 후 딜레이

# 병렬 처리 설정
MAX_WORKERS = 1  # 동시 처리 스레드 수 (차단 방지를 위해 1개 권장)

# 세션 관리 설정
SESSION_REFRESH_INTERVAL = 30  # 30개마다 세션 교체 + 메인 페이지 방문
MAX_CONSECUTIVE_TIMEOUTS = 5   # 연속 타임아웃 5회 시 차단으로 판단

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


def generate_model_no_variants(model_no: str) -> List[str]:
    """
    모델명을 여러 형태로 생성하여 리스트로 반환
    예: "WVBDK M25085 AAD" → ["WVBDK M25085 AAD", "WVBDKM25085AAD"]
    """
    if not model_no:
        return []

    model_no = model_no.strip()
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
# 재고/가격 동기화 클래스
# =====================================================

class StockPriceSynchronizer:

    def __init__(self):
        self.buyma_session = requests.Session()
        self.buyma_session.headers.update(BUYMA_HEADERS)
        
        # 403 차단 플래그 (스레드 간 공유)
        self.is_blocked = False
        self.block_lock = threading.Lock()
        
        # ★★★ 오케이몰 세션 관리 (v2 추가) ★★★
        self.okmall_session = None           # 현재 오케이몰 세션
        self.okmall_profile = None           # 현재 브라우저 프로필
        self.okmall_request_count = 0        # 현재 세션에서의 요청 수
        self.consecutive_timeout_count = 0   # 연속 타임아웃 횟수
        self.session_lock = threading.Lock() # 세션 접근 동기화

    def get_connection(self) -> pymysql.Connection:
        return pymysql.connect(**DB_CONFIG)

    # -------------------------------------------------
    # ★★★ 오케이몰 세션 관리 (v2 추가) ★★★
    # -------------------------------------------------
    def _create_new_okmall_session(self) -> Tuple[bool, Optional[str]]:
        """
        새 오케이몰 세션 생성 + 메인 페이지 방문
        
        Returns:
            Tuple[bool, Optional[str]]: (성공 여부, 에러 메시지)
        """
        try:
            # 기존 세션 종료
            if self.okmall_session:
                self.okmall_session.close()
            
            # 새 세션 생성
            self.okmall_session = requests.Session()
            
            # 랜덤 브라우저 프로필 선택
            self.okmall_profile = random.choice(BROWSER_PROFILES).copy()
            
            # 메인 페이지 방문 헤더 설정
            main_headers = self.okmall_profile.copy()
            main_headers['Referer'] = 'https://www.google.com/'  # 구글에서 온 것처럼
            main_headers['Sec-Fetch-Site'] = 'cross-site'  # 외부에서 온 것
            
            self.okmall_session.headers.update(main_headers)
            
            # 메인 페이지 방문 (쿠키 획득)
            log(f"  [세션] 새 세션 시작 - 메인 페이지 방문 중...")
            main_response = self.okmall_session.get('https://www.okmall.com/', timeout=15)
            
            if main_response.status_code != 200:
                return False, f"메인 페이지 접속 실패: {main_response.status_code}"
            
            # 세션 내 이동용 헤더로 변경
            product_headers = self.okmall_profile.copy()
            product_headers['Referer'] = 'https://www.okmall.com/'  # 메인에서 온 것처럼
            product_headers['Sec-Fetch-Site'] = 'same-origin'  # 같은 사이트 내 이동
            self.okmall_session.headers.update(product_headers)
            
            # 카운터 초기화
            self.okmall_request_count = 0
            
            # 짧은 대기 (사람처럼)
            time.sleep(random.uniform(0.5, 1.5))
            
            log(f"  [세션] 새 세션 준비 완료 (쿠키 획득됨)")
            return True, None
            
        except requests.exceptions.Timeout:
            return False, "메인 페이지 타임아웃"
        except Exception as e:
            return False, f"세션 생성 오류: {str(e)}"

    def _fetch_product_page(self, product_url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        현재 세션으로 상품 페이지 접속
        
        Returns:
            Tuple[Optional[str], Optional[str]]: (HTML 내용, 에러 메시지)
        """
        try:
            response = self.okmall_session.get(product_url, timeout=30)
            response.raise_for_status()
            return response.text, None
            
        except requests.exceptions.Timeout:
            return None, "요청 타임아웃"
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if '403' in error_msg:
                return None, "접근 차단됨 (403)"
            return None, f"요청 오류: {error_msg}"
        except Exception as e:
            return None, f"오류: {str(e)}"

    # -------------------------------------------------
    # 1. 동기화 대상 상품 조회
    # -------------------------------------------------
    def get_products_to_sync(self, limit: int = None, brand: str = None, product_id: int = None) -> List[Dict]:
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                sql = """
                    SELECT
                        ap.id,
                        ap.buyma_product_id,
                        ap.reference_number,
                        ap.name,
                        ap.brand_name,
                        ap.model_no,
                        ap.category_id,
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
                """
                params = []

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
    # 2. 오케이몰에서 가격/재고 수집 (★ v2 개선됨)
    # -------------------------------------------------
    def collect_from_okmall(self, product_url: str) -> Tuple[Dict, Optional[str]]:
        """
        ★ 세션 관리 + 타임아웃 감지 적용된 오케이몰 수집
        
        - 30개마다 새 세션 + 메인 페이지 방문
        - 타임아웃 연속 3회 시 차단으로 판단
        """
        with self.session_lock:
            # ★ 세션 교체 필요 여부 확인 (30개마다 또는 세션 없을 때)
            if self.okmall_session is None or self.okmall_request_count >= SESSION_REFRESH_INTERVAL:
                success, error = self._create_new_okmall_session()
                if not success:
                    return {}, error
            
            # 상품 페이지 접속
            html, error = self._fetch_product_page(product_url)
            
            # 요청 카운터 증가
            self.okmall_request_count += 1
            
            # ★ 타임아웃 연속 감지
            if error and "타임아웃" in error:
                self.consecutive_timeout_count += 1
                log(f"  [타임아웃] 연속 {self.consecutive_timeout_count}회", "WARNING")
                
                if self.consecutive_timeout_count >= MAX_CONSECUTIVE_TIMEOUTS:
                    return {}, "타임아웃 차단 감지 (연속 3회)"
            else:
                # 성공 또는 다른 에러면 타임아웃 카운터 초기화
                self.consecutive_timeout_count = 0
        
        if error:
            return {}, error
        
        if not html:
            return {}, "빈 응답"
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 흠집 상품 체크
            if 'item_scratch' in html:
                return {}, "흠집 상품"
            
            result = {'original_price': 0, 'sale_price': 0, 'options': []}

            # 정가
            origin_elem = soup.select_one('.value_price .price')
            if origin_elem:
                price_text = origin_elem.get_text()
                price_match = re.sub(r'[^0-9]', '', price_text)
                if price_match:
                    result['original_price'] = int(price_match)

            # 판매가 (JSON-LD)
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                if script.string:
                    try:
                        ld_data = json.loads(script.string)
                        if ld_data.get('@type') == 'Product':
                            offers = ld_data.get('offers', {})
                            if offers.get('@type') == 'AggregateOffer':
                                result['sale_price'] = int(offers.get('lowPrice', 0))
                            else:
                                result['sale_price'] = int(offers.get('price', 0))
                            break
                    except (json.JSONDecodeError, ValueError):
                        pass

            # 옵션별 재고 (raw_to_ace_converter.py 로직 참고)
            opt_rows = soup.select('#ProductOPTList tbody tr[name="selectOption"]')
            for row in opt_rows:
                cols = row.select('td')
                if len(cols) >= 3:
                    sinfo = row.get('sinfo', '')
                    option_code = sinfo.split('|')[-1] if sinfo else ''

                    # 색상: 그대로 사용 (DB에 저장된 값과 동일)
                    color_raw = cols[0].get_text(strip=True)

                    # size_notice 태그 제거 후 사이즈 추출 (품절 임박 제외)
                    size_elem = cols[1]
                    for notice in size_elem.select('.size_notice'):
                        notice.decompose()
                    size_raw = size_elem.get_text(strip=True)

                    # 사이즈: 단일사이즈 → FREE 변환 (raw_to_ace_converter.py와 동일)
                    if size_raw in ['단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈']:
                        size = 'FREE'
                    else:
                        size = size_raw

                    option = {
                        'color': color_raw,
                        'size': size,
                        'option_code': option_code,
                        'status': 'in_stock'
                    }
                    # 품절 임박이 아닌 실제 품절만 확인
                    row_text = row.get_text()
                    if '품절' in row_text and '품절 임박' not in row_text:
                        option['status'] = 'out_of_stock'
                    result['options'].append(option)

            # JSON-LD로 재고 상태 보완
            for script in scripts:
                if script.string:
                    try:
                        ld_data = json.loads(script.string)
                        if ld_data.get('@type') == 'Product':
                            offers = ld_data.get('offers', {})
                            if offers.get('@type') == 'AggregateOffer':
                                for offer in offers.get('offers', []):
                                    sku = str(offer.get('sku', ''))
                                    is_out = 'OutOfStock' in offer.get('availability', '')
                                    for opt in result['options']:
                                        if opt.get('option_code') == sku:
                                            opt['status'] = 'out_of_stock' if is_out else 'in_stock'
                    except json.JSONDecodeError:
                        pass

            # 단일 상품
            if not result['options']:
                for script in scripts:
                    if script.string:
                        try:
                            ld_data = json.loads(script.string)
                            if ld_data.get('@type') == 'Product':
                                offers = ld_data.get('offers', {})
                                availability = offers.get('availability', '')
                                status = 'out_of_stock' if 'OutOfStock' in availability else 'in_stock'
                                result['options'].append({
                                    'color': '', 'size': 'ONE SIZE',
                                    'option_code': '', 'status': status
                                })
                                break
                        except json.JSONDecodeError:
                            pass

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

            # 모든 상품을 순회하며 경쟁자(내 상품 제외) 최저가 찾기
            for product in products:
                # buyer ID 추출: <div class="product_Buyer"><a href="/buyer/9757794.html">
                buyer_elem = product.select_one('.product_Buyer a')
                if buyer_elem:
                    href = buyer_elem.get('href', '')
                    # /buyer/9757794.html 에서 9757794 추출
                    buyer_match = re.search(r'/buyer/(\d+)', href)
                    if buyer_match:
                        buyer_id = buyer_match.group(1)
                        # 내 상품이면 스킵
                        if BUYMA_BUYER_ID and buyer_id == BUYMA_BUYER_ID:
                            continue

                # 가격 추출
                price_elem = product.find('span', class_='Price_Txt')
                if price_elem:
                    price = parse_price(price_elem.get_text(strip=True))
                    if price:
                        return price, None

            # 내 상품만 있거나 가격 추출 실패
            return None, "경쟁자 없음 (내 상품만 존재)"

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

        # 단일 옵션 상품 처리: DB 1개, 오케이몰 1개이면 이름 상관없이 직접 매칭
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
                                   margin_rate: float) -> None:
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
                        margin_calculated_at = NOW(),
                        buyma_lowest_price_checked_at = NOW()
                    WHERE id = %s
                """, (original_price_krw, purchase_price_krw, price_jpy,
                      original_price_jpy, buyma_lowest_price, margin_rate, ace_product_id))
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
                if response.get('success'):
                    cursor.execute("""
                        UPDATE ace_products
                        SET status = 'pending',
                            api_request_json = %s,
                            api_response_json = %s,
                            last_api_call_at = NOW()
                        WHERE id = %s
                    """, (
                        json.dumps(request_data, ensure_ascii=False, default=decimal_to_float),
                        json.dumps(response, ensure_ascii=False),
                        ace_product_id
                    ))
                else:
                    cursor.execute("""
                        UPDATE ace_products
                        SET status = 'api_error',
                            api_request_json = %s,
                            api_response_json = %s,
                            last_api_call_at = NOW()
                        WHERE id = %s
                    """, (
                        json.dumps(request_data, ensure_ascii=False, default=decimal_to_float),
                        json.dumps(response, ensure_ascii=False),
                        ace_product_id
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
                    SELECT id, buyma_product_id, reference_number, name, brand_id,
                           category_id, price, original_price_jpy, buying_shop_name,
                           buyma_model_id, colorsize_comments_jp, available_until,
                           expected_shipping_fee, purchase_price_krw, model_no,
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

◆「あんしんプラス」へご加入の場合、「サイズがあわない」、「イメージと違う」場合に「返品補償制度」をご利用頂けます。
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

◆海外製品は、「MADE IN JAPAN」の製品に比べて、若干見劣りする場合もございます。
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

確実でスピーディなお取引と、注文確定後のキャンセル
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
            available_until_str = (datetime.now() + timedelta(days=30)).strftime('%Y/%m/%d')

        # images
        images_arr = [{"path": row['cloudflare_image_url'], "position": row['position']} for row in images]

        # options
        options_arr = []
        for row in options:
            opt = {
                "type": row['option_type'],
                "value": row['value'],
                "position": row['position'],
                "master_id": row['master_id'] or 0
            }
            if row['option_type'] == 'size' and row.get('details_json'):
                try:
                    details = json.loads(row['details_json'])
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
                variant["options"].append({"type": "color", "value": v['color_value']})
            if v['size_value']:
                variant["options"].append({"type": "size", "value": v['size_value']})
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
            "comments": f"{model_no_text}\n\n{fixed_comments}" if model_no_text else fixed_comments,
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
            "style_numbers": style_numbers,
            "theme_id": BUYMA_FIXED_VALUES['theme_id'],
            "duty": BUYMA_FIXED_VALUES['duty'],
        }

        if product.get('buying_shop_name'):
            request_data['buying_shop_name'] = product['buying_shop_name']
        if product.get('original_price_jpy'):
            request_data['reference_price'] = int(product['original_price_jpy'])
        if product.get('buyma_model_id'):
            request_data['model_id'] = product['buyma_model_id']

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
            # 1. 오케이몰 가격/재고 수집 (★ v2 세션 관리 적용)
            mall_data, error = self.collect_from_okmall(product['source_product_url'])
            if error:
                add_log(f"  오케이몰 수집 실패: {error}", "WARNING")
                
                # ★ 403 차단 또는 타임아웃 차단 감지 시 즉시 중단
                if error == "접근 차단됨 (403)" or error == "타임아웃 차단 감지 (연속 3회)":
                    add_log(f"  → IP 차단됨! 비행기모드 토글 필요", "ERROR")
                    with self.block_lock:
                        self.is_blocked = True
                    with stats_lock:
                        stats['blocked'] += 1
                    log_batch(logs)
                    return
                
                # 흠집 상품이면 DB도 삭제
                if error == "흠집 상품":
                    add_log(f"  → 흠집 상품 발견 → 바이마 삭제 + DB 삭제")
                else:
                    add_log(f"  → 수집처에서 상품 삭제됨 → 바이마 삭제 요청")

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
                            
                            # 흠집 상품이면 DB에서도 삭제
                            if error == "흠집 상품":
                                self._delete_from_db(product['id'], product.get('raw_data_id'))
                                add_log(f"  DB 삭제 완료")
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

            # 2. 재고 변동 감지
            db_variants = self.get_current_variants(product['id'])
            stock_changes = self.detect_stock_changes(db_variants, mall_options)

            # 3. 바이마 최저가 수집 (내 상품 제외 경쟁자 최저가)
            competitor_lowest_price, lp_error = self.get_buyma_lowest_price(product.get('model_no'))

            # 4. 새 가격 계산 (JPY)
            if lp_error:
                if "경쟁자 없음" in lp_error:
                    add_log(f"  - 경쟁자 없음 (내 상품만 존재) → 가격 유지")
                    new_price_jpy = product.get('price')
                    new_lowest_price = product.get('buyma_lowest_price')
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
                add_log(f"  - [변경] 최저가 ¥{old_lowest_price:,} → ¥{new_lowest_price:,}")
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
                self.update_ace_products_price(
                    product['id'], new_original_price, int(new_purchase_price_krw),
                    new_price_jpy, new_original_price_jpy, new_lowest_price,
                    margin_info['margin_rate']
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
    # 메인 실행 로직 (병렬 처리)
    # -------------------------------------------------
    def run(self, limit: int = None, brand: str = None, product_id: int = None, dry_run: bool = False, force: bool = False) -> Dict:
        log("=" * 60)
        log("재고/가격 동기화 시작 (v2 - 세션 관리 개선)")
        log(f"  옵션: id={product_id}, brand={brand}, limit={limit}, dry_run={dry_run}, force={force}")
        log(f"  병렬 처리: {MAX_WORKERS}개 스레드")
        log(f"  세션 교체 주기: {SESSION_REFRESH_INTERVAL}개마다")
        log(f"  타임아웃 차단 감지: 연속 {MAX_CONSECUTIVE_TIMEOUTS}회")
        log("=" * 60)

        if dry_run:
            log("*** DRY RUN 모드 - 실제 업데이트 안함 ***", "WARNING")

        products = self.get_products_to_sync(limit=limit, brand=brand, product_id=product_id)
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
            'blocked': 0  # 차단 카운트
        }
        stats_lock = threading.Lock()

        # 스레드 풀로 병렬 처리
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []

            for idx, product in enumerate(products):
                future = executor.submit(
                    self.process_single_product,
                    product, idx + 1, len(products),
                    dry_run, force, stats, stats_lock
                )
                futures.append(future)

            # 모든 작업 완료 대기
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    log(f"스레드 오류: {e}", "ERROR")
                    with stats_lock:
                        stats['errors'] += 1

        # 오케이몰 세션 정리
        if self.okmall_session:
            self.okmall_session.close()

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
        if stats['blocked'] > 0:
            log(f"  차단됨: {stats['blocked']}건 (비행기모드 토글 필요)", "WARNING")
        log("=" * 60)

        return stats


# =====================================================
# 메인 실행
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='바이마 재고/가격 동기화')
    parser.add_argument('--id', type=int, default=None, help='특정 상품 ID (ace_products.id)')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만 처리')
    parser.add_argument('--dry-run', action='store_true', help='테스트 모드 (실제 업데이트 안함)')
    parser.add_argument('--force', action='store_true', help='변경 없어도 강제 API 호출')

    args = parser.parse_args()

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
            force=args.force
        )
    except Exception as e:
        log(f"실행 중 오류 발생: {str(e)}", "ERROR")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()