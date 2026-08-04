# -*- coding: utf-8 -*-
"""
재고 및 가격 동기화 스크립트 (ABC마트 / 그랜드스테이지 버전) — [MERGE]

같은 플랫폼(a-rt.com) 위 채널 2개(abcmart / grandstage). --source 로 대상 채널을 지정한다.
바이마에 등록된 상품의 재고와 가격을 a-rt.com 에서 재수집하여
ace 테이블을 refresh 하고, BUYMA push(수정/삭제/옵션합침/싼몰)는 run 끝 reconcile 이 담당한다.

처리 흐름:
1. ace_products에서 바이마 등록 상품 조회 (is_published=1, buyma_product_id 있음, source_site=--source)
2. a-rt /product/info(JSON) 재방문 → 현재 가격/재고 수집
3. ace_products 가격 UPDATE
4. ace_product_variants 재고 UPDATE
5. 바이마 최저가 수집
6. 마진 계산 (buyma_product_register.py와 동일)
7. refresh 만 수행 (BUYMA 직접 호출 X)
8. run 끝 → reconcile 이 이번 회차 그룹만 BUYMA push

사용법:
    python stock_price_synchronizer_abc_merge.py --source abcmart              # abcmart 전체 실행
    python stock_price_synchronizer_abc_merge.py --source grandstage           # grandstage 전체 실행
    python stock_price_synchronizer_abc_merge.py --source abcmart --brand NIKE # 특정 브랜드만
    python stock_price_synchronizer_abc_merge.py --source abcmart --limit 100  # 최대 100개만
    python stock_price_synchronizer_abc_merge.py --source abcmart --dry-run    # 테스트 (reconcile 생략)

작성일: 2026-07-24
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

# [MERGE] reconcile 엔진(okmall/)을 여기서 import.
#   buyma_new_product_register 가 win32 stdout/stderr utf-8 wrap 까지 처리하므로,
#   여기서 또 감싸면 안 됨 — 이중 wrap → 버퍼 닫힘(I/O operation on closed file) 버그.
#   stdout wrap 은 bnpr 한 곳만, import 도 모듈 로드 시 한 번만.
#   reconcile 모듈들은 okmall/ 에 있으므로 sys.path 에 추가 후 import.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'okmall'))
import reconcile_runner  # noqa: E402  (stdout utf-8 wrap 부수효과 포함)
import authority_flag
# [공용] 재고동기화 공용 부품 — 상수·함수·메서드는 okmall/stock_common.py 한 곳에 있다.
from stock_common import (  # noqa: E402
    DB_CONFIG, EXCHANGE_RATE, SALES_FEE_RATE, DEFAULT_SHIPPING_FEE,
    BUYMA_BUYER_ID, BUYMA_SEARCH_URL, _log_lock,
    log, log_batch, parse_price, decimal_to_float, _buyma_width,
    truncate_buyma_name, truncate_option_value, truncate_buying_shop_name,
    generate_model_no_variants, calculate_margin,
    StockCommonMixin,
)
  # 단일권위 전환 스위치 (ace → buyma_listings)

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=True)


# =====================================================
# 설정값 (buyma_product_register.py와 동일)
# =====================================================


# 바이마 API 설정
BUYMA_MODE = int(os.getenv('BUYMA_MODE', 1))
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')
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
# a-rt.com (ABC마트/그랜드스테이지 공용 플랫폼) 설정
#   수집기(abc_collector.py)와 동일한 /product/info(JSON) 재조회로 가격·재고 확인.
#   도메인/prdtNo 는 ace_products.source_product_url 에서 그대로 추출(채널 무관).
# =====================================================
ART_INFO_PATH = '/product/info'
ART_SELL_STATUS_ON = '10001'   # sellStatCode: 10001=판매중 (10002=품절). abc_collector 와 동일.
ART_USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')

BUYMA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6',
    'Referer': 'https://www.buyma.com/',
}


# 딜레이 설정
REQUEST_DELAY_MIN = 0.5  # a-rt API 요청 간 최소 딜레이
REQUEST_DELAY_MAX = 1.0  # a-rt API 요청 간 최대 딜레이
API_CALL_DELAY = 0.2     # 바이마 API 호출 후 딜레이

# 병렬 처리 설정
MAX_WORKERS = 2  # 동시 처리 스레드 수

# 마진 계산 상수 (buyma_product_register.py와 동일)


# =====================================================
# 유틸리티 함수
# =====================================================

# 로그 출력용 Lock (병렬 처리 시 로그 섞임 방지)


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
    csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'buyma_master_data', 'size_details.csv')
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


def random_delay() -> None:
    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
    time.sleep(delay)


# =====================================================
# 마진 계산 (buyma_product_register.py와 동일)
# =====================================================


# =====================================================
# a-rt API 호출 헬퍼
# =====================================================

def art_info_get(product_url: str) -> Tuple[Optional[dict], Optional[str]]:
    """
    a-rt /product/info(JSON) 재조회. 도메인·prdtNo 는 product_url 에서 추출(채널 무관).

    Returns:
        (info, error): 성공 시 (info, None)
                       404 → (None, "상품 삭제됨 (404)")   → 삭제 분기
                       그 외 비정상 → (None, "일시적 오류 (스킵): ...") → 이번 회차 스킵
    """
    m = re.search(r'(https?://[^/]+).*prdtNo=(\d+)', product_url or '')
    if not m:
        return None, f"일시적 오류 (스킵): URL에서 prdtNo 추출 실패: {product_url}"
    domain, prdt_no = m.group(1), m.group(2)
    headers = {
        'User-Agent': ART_USER_AGENT,
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': f'{domain}/product/new?prdtNo={prdt_no}',
    }
    try:
        resp = requests.get(domain + ART_INFO_PATH, headers=headers,
                            params={'prdtNo': prdt_no, 'xramdom': random.random()}, timeout=30)
        if resp.status_code == 200:
            try:
                return resp.json(), None
            except ValueError:
                return None, "일시적 오류 (스킵): JSON 파싱 실패"
        elif resp.status_code == 404:
            return None, "상품 삭제됨 (404)"
        else:
            log(f"  a-rt API 오류 {resp.status_code}: prdtNo={prdt_no}", "WARNING")
            return None, f"일시적 오류 (스킵): HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        log(f"  a-rt API 타임아웃: prdtNo={prdt_no}", "WARNING")
        return None, "일시적 오류 (스킵): 타임아웃"
    except Exception as e:
        log(f"  a-rt API 요청 실패: prdtNo={prdt_no} - {e}", "ERROR")
        return None, f"일시적 오류 (스킵): {e}"


# =====================================================
# 재고/가격 동기화 클래스
# =====================================================

class StockPriceSynchronizer(StockCommonMixin):

    def __init__(self, source_site: str = 'abcmart'):
        # 대상 채널 (abcmart / grandstage). get_products_to_sync 의 source_site 필터에 사용.
        self.source_site = source_site
        self.buyma_session = requests.Session()
        self.buyma_session.headers.update(BUYMA_HEADERS)

        # 403 차단 플래그 (스레드 간 공유)
        self.is_blocked = False
        self.block_lock = threading.Lock()


    # -------------------------------------------------
    # 1. 동기화 대상 상품 조회
    # -------------------------------------------------
    def get_products_to_sync(self, limit: int = None, brand: str = None, product_id: int = None) -> List[Dict]:
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                # 대상 선정: 이 몰의 offering 중 등록된 listing 소속 전부(멤버 포함)
                _reg = authority_flag.registered_sql('ap')
                sql = f"""
                    SELECT
                        ap.id,
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
                    WHERE {_reg}
                      AND ap.source_product_url IS NOT NULL
                      AND ap.is_active = 1
                      AND ap.source_site = %s
                """
                params = [self.source_site]

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
    # 2. a-rt.com 에서 가격/재고 수집 (/product/info JSON)
    # -------------------------------------------------
    def collect_from_abc(self, product_url: str) -> Tuple[Dict, Optional[str]]:
        """
        a-rt /product/info(JSON)로 가격/재고 수집. abc_collector.py 와 동일한 파싱 규칙.

        - 가격: productPrice.normalAmt(정가) / sellAmt(판매가=매입가 기준)
        - 옵션: productOption[] 전부(품절 포함). sellStatCode==10001 이고 재고>0 → in_stock.
          차원은 addOptn2Text 유무로 판별(있으면 색상+사이즈, 없으면 사이즈만) — 수집기와 동일.
        - 반환 형식은 원본(kasina) 버전과 동일: {original_price, sale_price, options[{color,size,option_code,status}]}
        """
        info, err = art_info_get(product_url)
        if err:
            # "상품 삭제됨 (404)" → 삭제 분기, "일시적 오류 ..." → 스킵 (process_single_product 에서 분기)
            return {}, err

        try:
            # 미노출(dispYn=N) → 판매 종료로 간주 (삭제 분기)
            if str(info.get('dispYn') or 'Y') != 'Y':
                return {}, "판매 종료 상품"

            result = {'original_price': 0, 'sale_price': 0, 'options': []}

            # 가격 (productPrice)
            pp = info.get('productPrice') or {}
            sale_amt = int(pp.get('sellAmt') or pp.get('normalAmt') or 0)
            normal_amt = int(pp.get('normalAmt') or sale_amt or 0)
            result['original_price'] = normal_amt
            result['sale_price'] = sale_amt

            # 옵션별 재고 (품절 포함 — 재고변동 감지에 전 옵션 필요)
            for o in info.get('productOption') or []:
                name = (o.get('optnName') or '').strip()
                size2 = (o.get('addOptn2Text') or '').strip()   # 의류만 존재(사이즈)
                if size2:  # 2차원: 색상 + 사이즈
                    color, size = name, size2
                else:      # 1차원: 사이즈만
                    color, size = '', name
                stock = o.get('totalStockQty') or 0
                o_stat = str(o.get('sellStatCode') or '')
                status = 'in_stock' if (o_stat == ART_SELL_STATUS_ON and stock > 0) else 'out_of_stock'
                result['options'].append({
                    'color': color,
                    'size': size,
                    'option_code': str(o.get('prdtOptnNo') or ''),
                    'status': status,
                })

            # 옵션 없는 단일 상품
            if not result['options']:
                sell_stat = str(info.get('sellStatCode') or '')
                stock_status = 'in_stock' if sell_stat == ART_SELL_STATUS_ON else 'out_of_stock'
                result['options'].append({
                    'color': '', 'size': 'ONE SIZE',
                    'option_code': '', 'status': stock_status
                })

            return result, None

        except Exception as e:
            return {}, f"일시적 오류 (스킵): 파싱 오류 {str(e)}"

    # -------------------------------------------------
    # 3. 바이마 최저가 수집 (내 상품 제외 경쟁자 최저가)
    # -------------------------------------------------

    # -------------------------------------------------
    # 4. 배송비 조회
    # -------------------------------------------------

    # -------------------------------------------------
    # 5. DB 조회/업데이트
    # -------------------------------------------------

    def detect_stock_changes(self, db_variants: List[Dict], mall_options: List[Dict]) -> List[Dict]:
        changes = []

        # 단일 옵션 상품 처리: DB 1개, a-rt 1개이면 이름 상관없이 직접 매칭
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

        # 다중 옵션 상품 매칭 (2026-05-21 개편):
        #   1순위: source_option_code (mall이 부여한 옵션 고유 ID, 번역 영향 0)
        #   2순위: (color_value_original, size_value_original) 한글 원본 (mall raw 그대로 보존)
        #   매칭 실패 → skip. 일본어 fallback 제거.
        # 일본어 fallback이 5,320건+ false delete 사고의 원인이었음 (color_value가 번역으로 덮여 한글 mall raw와 매칭 불가).
        mall_by_code = {}
        mall_by_kr = {}
        for item in mall_options:
            code = (item.get('option_code') or '').strip()
            mc = (item.get('color', '') or '').strip().lower() or 'free'
            ms = (item.get('size', '') or '').strip().lower() or 'free'
            if code:
                mall_by_code[code] = item['status']
            mall_by_kr[(mc, ms)] = item['status']

        for variant in db_variants:
            db_code = (variant.get('source_option_code') or '').strip()
            db_color_kr = (variant.get('color_value_original') or '').strip().lower() or 'free'
            db_size_kr = (variant.get('size_value_original') or '').strip().lower() or 'free'
            db_status = variant.get('stock_type', 'purchase_for_order')
            db_is_available = db_status != 'out_of_stock'

            mall_status = None
            if db_code and db_code in mall_by_code:
                mall_status = mall_by_code[db_code]
            elif (db_color_kr, db_size_kr) in mall_by_kr:
                mall_status = mall_by_kr[(db_color_kr, db_size_kr)]

            if mall_status is None:
                continue

            mall_is_available = mall_status == 'in_stock'
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


    # ====================================================
    # [MERGE] 재고0 표시 + reconcile push (BUYMA 직접 건드리지 않음)
    # ====================================================
    def _mark_all_out_of_stock(self, ace_product_id: int) -> None:
        """abc(abcmart/grandstage) 품절/삭제/흠집 → 이 ace 의 옵션 전부 out_of_stock 표시.
        BUYMA 직접 삭제 대신(다른 몰 있으면 winner 이동) → reconcile 이 판단."""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ace_product_variants
                    SET stock_type='out_of_stock', source_stock_status='out_of_stock'
                    WHERE ace_product_id=%s
                """, (ace_product_id,))
                conn.commit()
        finally:
            conn.close()


    # -------------------------------------------------
    # 6. 바이마 API 호출 (buyma_product_register.py와 동일)
    # -------------------------------------------------


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
            # 1. a-rt.com 에서 가격/재고 수집
            mall_data, error = self.collect_from_abc(product['source_product_url'])
            if error:
                add_log(f"  a-rt 수집 실패: {error}", "WARNING")

                # 일시적 API 오류 → 삭제하지 않고 스킵
                if "일시적 오류" in error:
                    add_log(f"  → API 일시적 오류, 이번 회차 스킵")
                    with stats_lock:
                        stats['skipped'] += 1
                    log_batch(logs)
                    return

                # [MERGE] 바이마 직접 삭제 안 함 — abc 옵션만 재고0 표시.
                #   abc만 품절/삭제이어도 다른 몰 있으면 winner 이동, 없으면 reconcile 이 retire.
                add_log(f"  → 수집처 삭제/종료 → abc 재고0 표시 (BUYMA 반영은 reconcile)")
                if not dry_run:
                    self._mark_all_out_of_stock(product['id'])
                    self.update_sync_time_only(product['id'])
                else:
                    add_log(f"  [DRY-RUN] abc 재고0 표시 예정")
                with stats_lock:
                    stats['skipped'] += 1

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
                    # 경쟁자 없음 → 매입가 기반 30% 마진 가격 재계산
                    new_purchase = new_sale_price if new_sale_price else float(product.get('purchase_price_krw') or 0)
                    if new_purchase > 0:
                        shipping_fee_for_calc = product.get('expected_shipping_fee') or self.get_shipping_fee(product.get('category_id'))
                        total_cost = new_purchase + float(shipping_fee_for_calc)
                        vat_refund = new_purchase / 11.0
                        denominator = (1.0 - SALES_FEE_RATE) - 0.30  # 0.645
                        if denominator > 0:
                            target_price_krw = (total_cost - vat_refund) / denominator
                            target_price_jpy = int(target_price_krw / EXCHANGE_RATE)
                            new_price_jpy = target_price_jpy
                            add_log(f"  - 경쟁자 없음 → 마진30% 가격 재계산: ¥{new_price_jpy:,}")
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

            # 7. DB 업데이트 (refresh) — [MERGE] 항상 수행 (no-margin이어도 reconcile 이 판단하도록 최신화)
            if not new_lowest_price:
                calc_is_lowest = 1
            else:
                calc_is_lowest = 1 if new_price_jpy <= new_lowest_price else 0
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

            # 8. [MERGE] BUYMA push 생략 — refresh 만. push(수정/삭제/옵션합침/싼몰)는 run 끝 reconcile 담당.
            self.update_sync_time_only(product['id'])
            add_log(f"  refresh 완료 (BUYMA 반영은 reconcile)")
            with stats_lock:
                stats['success'] += 1
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
        log(f"재고/가격 동기화 시작 ({self.source_site})")
        log(f"  옵션: id={product_id}, brand={brand}, limit={limit}, dry_run={dry_run}, force={force}")
        log(f"  병렬 처리: {MAX_WORKERS}개 스레드")
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
            'blocked': 0
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
        log("=" * 60)

        # [MERGE] refresh 끝 → reconcile 이 BUYMA push (옵션합침+싼몰+수정/삭제 판단)
        #   이번 회차 synced 상품(products)의 그룹만 대상.
        if not dry_run:
            try:
                self._reconcile_published(products)
            except Exception as e:
                log(f"[MERGE] reconcile push 오류: {e}", "ERROR")
        else:
            log("[MERGE] [DRY-RUN] reconcile push 단계 생략")

        return stats


# =====================================================
# 메인 실행
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='바이마 재고/가격 동기화 (ABC마트 / 그랜드스테이지) [MERGE]')
    parser.add_argument('--source', type=str, default='abcmart', choices=['abcmart', 'grandstage'],
                        help='대상 채널 (abcmart / grandstage). 기본 abcmart')
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
        synchronizer = StockPriceSynchronizer(source_site=args.source)
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
