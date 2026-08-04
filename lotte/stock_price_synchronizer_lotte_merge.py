# -*- coding: utf-8 -*-
"""
재고 및 가격 동기화 스크립트 (롯데온 버전)

바이마에 등록된 상품의 재고와 가격을 롯데온(lotteon.com 롯데백화점 mall_no=2)에서 재수집하여
ace 테이블을 업데이트하고 바이마 API로 상품을 수정합니다.

처리 흐름:
1. ace_products에서 바이마 등록 상품 조회 (is_published=1, buyma_product_id 있음)
2. 롯데온 API 재방문 → 현재 가격/재고 수집 (상세 + 즉시할인가 + 옵션 조합별 재고)
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

롯데온 수집 메모 (로그인/브라우저 불필요, 순수 HTTP GET/POST):
- 상세 API(sitmNo 기준): priceInfo.slPrc=정가, basicInfo.sitmSlStatCd=판매상태,
  optionInfo.optionList=옵션축, optionInfo.optionMappingInfo=조합별 재고(stkQty)/판매상태.
- ★ 상세 API의 slPrc 는 "정가"라서 즉시할인이 반영되지 않는다. 실매입가는 즉시할인 API
  (promotionMaxFavorInfoList → immdDcAplyTotAmt)로 따로 받아야 목록 노출가와 일치한다.
- 삭제된 상품은 HTTP 200 + returnCode=400 + data=null("상품정보가 없습니다") 로 온다 → 삭제 처리.
- option_code 는 lotte_collector.parse_options 와 동일한 "색상값/사이즈값" 포맷으로 만들어야
  ace_product_variants.source_option_code 매칭이 깨지지 않는다.
- 일시적 API 오류(타임아웃/429/5xx)는 삭제하지 않고 스킵(오삭제 방지).

사용법:
    python3 stock_price_synchronizer_lotte_merge.py                    # 전체 실행
    python3 stock_price_synchronizer_lotte_merge.py --brand BURBERRY   # 특정 브랜드만
    python3 stock_price_synchronizer_lotte_merge.py --limit 100        # 최대 100개만
    python3 stock_price_synchronizer_lotte_merge.py --dry-run          # 테스트 (API 호출 안함)
    python3 stock_price_synchronizer_lotte_merge.py --force            # 변경 없어도 강제 API 호출

작성일: 2026-07-28
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
# 롯데온 API 설정 (lotte_collector.py와 동일 소스)
# =====================================================
# 상세(기본정보 + 옵션축 + 조합별 재고). 상품이 사라지면 returnCode=400 / data=null.
LOTTE_DETAIL_SITM_API = 'https://pbf.lotteon.com/product/v2/detail/search/base/sitm/{sitm}?sitmNo={sitm}&mall_no=2'
# URL에 sitmNo가 없을 때(또는 sitmNo가 교체됐을 때) 폴백 — 상품번호(pdNo) 기준 상세
LOTTE_DETAIL_PD_API = 'https://pbf.lotteon.com/product/v2/detail/search/base/pd/{pid}?pdNo={pid}&mall_no=2'
# 즉시할인 적용가(=실매입가). 상세의 slPrc는 정가라서 할인가는 이 API로만 나온다.
LOTTE_MAX_FAVOR_API = 'https://pbf.lotteon.com/product/v1/detail/promotion/promotionMaxFavorInfoList'

LOTTE_HTTP_TIMEOUT = 25
LOTTE_MAX_RETRY = 3
LOTTE_USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')

LOTTE_API_HEADERS = {
    'User-Agent': LOTTE_USER_AGENT,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://www.lotteon.com/',
}

BUYMA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6',
    'Referer': 'https://www.buyma.com/',
}


# 딜레이 설정
REQUEST_DELAY_MIN = 0.3  # 롯데온 API 요청 간 최소 딜레이 (Imperva 속도저하 회피용)
REQUEST_DELAY_MAX = 0.8  # 롯데온 API 요청 간 최대 딜레이
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
# 롯데온 API 호출 헬퍼
# =====================================================
# NOT_FOUND = 상품 삭제(404 또는 returnCode=400 + data=null).
# TEMP = 일시적 오류(타임아웃/429/5xx) → 오삭제 방지용 스킵.


def lotte_api_get(url: str) -> Tuple[Optional[dict], Optional[str]]:
    """롯데온 JSON GET. (data, None) / (None, "NOT_FOUND") / (None, "TEMP")"""
    for attempt in range(LOTTE_MAX_RETRY):
        try:
            resp = requests.get(url, headers=LOTTE_API_HEADERS, timeout=LOTTE_HTTP_TIMEOUT)
            if resp.status_code == 200:
                body = resp.json() or {}
                data = body.get('data')
                if data:
                    return data, None
                # 삭제/미노출 상품: HTTP 200 인데 returnCode=400, data=null ("상품정보가 없습니다")
                if str(body.get('returnCode')) == '400':
                    return None, "NOT_FOUND"
                return None, "TEMP"   # 그 외 빈 응답은 안전하게 일시 오류로 (오삭제 방지)
            if resp.status_code == 404:
                return None, "NOT_FOUND"
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            return None, "TEMP"  # 기타 4xx도 안전하게 일시 오류로 취급(오삭제 방지)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(2 * (attempt + 1))
        except ValueError:  # JSON 파싱 실패
            return None, "TEMP"
    return None, "TEMP"


def lotte_get_sale_price(detail: Dict) -> Optional[int]:
    """즉시할인 적용가(=실매입가, 목록 노출가와 동일) 조회. 실패 시 None → 호출측이 정가로 폴백.

    상세의 priceInfo.slPrc 는 정가이고 할인은 반영돼 있지 않다. 롯데온 PDP 가 쓰는
    promotionMaxFavorInfoList(POST) 를 그대로 호출해 immdDcAplyTotAmt(즉시할인가)를 받는다.
    요청 파라미터는 전부 상세 응답에서 조립(PDP 의 pricePromotionParams 와 동일)."""
    basic = detail.get('basicInfo') or {}
    price = detail.get('priceInfo') or {}
    stck = detail.get('stckInfo') or {}
    dlv = detail.get('dlvInfo') or {}

    if not basic.get('spdNo') or not basic.get('sitmNo') or not price.get('slPrc'):
        return None

    body = {
        'spdNo': basic.get('spdNo'),
        'sitmNo': basic.get('sitmNo'),
        'trGrpCd': basic.get('trGrpCd'),
        'trNo': basic.get('trNo'),
        'lrtrNo': basic.get('lrtrNo') or '',
        'strCd': basic.get('strCd') or '',
        'ctrtTypCd': basic.get('ctrtTypCd'),
        'slPrc': price.get('slPrc'),
        'slQty': 1,
        'scatNo': basic.get('scatNo') or '',
        'brdNo': basic.get('brdNo') or '',
        'sfcoPdMrgnRt': price.get('sfcoPdMrgnRt'),
        'sfcoPdLwstMrgnRt': price.get('sfcoPdLwstMrgnRt'),
        'afflPdMrgnRt': price.get('afflMrgnRt'),
        'afflPdLwstMrgnRt': price.get('afflLwstMrgnRt'),
        'pcsLwstMrgnRt': price.get('pcsLwstMrgnRt'),
        'aplyStdDttm': datetime.now().strftime('%Y%m%d%H%M%S'),
        'thdyPdYn': basic.get('thdyPdYn'),
        'dvCst': dlv.get('dvCst'),
        'fprdDvPdYn': dlv.get('fprdDvPsbYn'),
        'discountApplyProductList': price.get('discountApplyProductList') or [],
        'maxPurQty': basic.get('maxPurQty', 1000000),
        'stkMgtYn': stck.get('stkMgtYn'),
        'screenType': 'PRODUCT',
        'dmstOvsDvDvsCd': basic.get('dmstOvsDvDvsCd'),
        'dvPdTypCd': dlv.get('dvPdTypCd'),
        'dvCstStdQty': dlv.get('dvCstStdQty'),
        'aplyBestPrcChk': price.get('aplyBestPrcChk') or 'Y',
        'pyMnsExcpLst': basic.get('pyMnsExcpLst'),
    }
    headers = dict(LOTTE_API_HEADERS)
    headers['Content-Type'] = 'application/json'

    for attempt in range(LOTTE_MAX_RETRY):
        try:
            resp = requests.post(LOTTE_MAX_FAVOR_API, headers=headers, json=body,
                                 timeout=LOTTE_HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = (resp.json() or {}).get('data') or {}
                # 즉시할인가 → 추가할인가 → 회원할인가 순. 쿠폰(중복쿠폰)은 반영 안 함(수집기와 동일).
                for key in ('immdDcAplyTotAmt', 'adtnDcAplyTotAmt', 'mbFvrOffrAmt'):
                    val = data.get(key)
                    if val and int(val) > 0:
                        return int(val)
                return None
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(2 * (attempt + 1))
        except ValueError:
            return None
    return None


# -----------------------------------------------------
# 롯데온 옵션 정규화 (lotte/lotte_collector.py parse_options 와 동일 규칙)
#   수집 때 쓴 분류·코드 규칙을 그대로 복제해야 source_option_code / 한글 color·size 가 일치해
#   detect_stock_changes 매칭이 깨지지 않는다. 재고만 조합별(optionMappingInfo)로 더 정밀하게 본다.
# -----------------------------------------------------
# raw_to_converter 가 FREE 로 바꾸는 값들과 동일하게 맞춘다(size_value_original 매칭용)
_LOTTE_FREE_SIZE_TOKENS = {'단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈', 'UNI'}


def _lotte_norm_size(label: str) -> str:
    """수집→변환 단계에서 저장된 size_value_original 과 같은 모양으로 정규화"""
    s = (label or '').strip()
    s = s.replace('품절 임박', '').replace('품절임박', '').strip()
    if s in _LOTTE_FREE_SIZE_TOKENS:
        return 'FREE'
    return s or 'FREE'


def _lotte_mapping_lookup(mapping: Dict, cval: str, sval: str) -> Optional[Dict]:
    """optionMappingInfo 에서 조합 찾기.

    키는 "옵션값_옵션값" 인데 ★순서가 optionList 에 나온 축 순서★ 라서
    상품에 따라 색상_사이즈 / 사이즈_색상 둘 다 나온다 (예: 사이즈 축이 먼저인 상품 존재).
    → 두 순서 다 시도. 축이 1개면 값 하나가 키."""
    if not mapping:
        return None
    keys = []
    if cval and sval:
        keys += [f"{cval}_{sval}", f"{sval}_{cval}"]
    elif cval:
        keys += [cval, f"{cval}_"]
    elif sval:
        keys += [sval, f"_{sval}"]
    for k in keys:
        if k in mapping:
            return mapping[k]
    return None


def _lotte_combo_in_stock(info: Dict, stk_mgt_yn: Optional[str]) -> bool:
    """조합 재고 판정: 판매상태 SALE + 재고수량 > 0 (재고관리 안 하는 상품이면 수량 무시)"""
    for key in ('sitmNoSlStatCd', 'spdNoSlStatCd'):
        st = (info.get(key) or '').upper()
        if st and st != 'SALE':
            return False
    if stk_mgt_yn == 'N':
        return True
    qty = info.get('stkQty')
    if qty is None:
        return True
    try:
        return int(qty) > 0
    except (TypeError, ValueError):
        return True


def normalize_lotte_options(option_info: Dict, stck_info: Dict) -> List[Dict]:
    """optionInfo → [{color, size, option_code, status}] (collect_from_* 공통 포맷).

    color/size 는 한글 원본(detect_stock_changes 의 color_value_original/size_value_original 매칭용),
    option_code 는 수집기와 동일한 "색상값/사이즈값" (source_option_code 매칭용),
    status 는 optionMappingInfo 의 조합별 재고 기준(매핑 없으면 축의 disabled 폴백)."""
    option_info = option_info or {}
    mapping = option_info.get('optionMappingInfo') or {}
    stk_mgt_yn = (stck_info or {}).get('stkMgtYn')

    colors, sizes = [], []
    for axis in (option_info.get('optionList') or []):
        title = (axis.get('title') or '')
        is_color = ('색상' in title) or ('컬러' in title) or ('color' in title.lower())
        for v in (axis.get('options') or []):
            label = (v.get('label') or '').strip()
            if not label:
                continue
            entry = {'label': label, 'value': v.get('value', ''), 'disabled': bool(v.get('disabled'))}
            (colors if is_color else sizes).append(entry)

    hits = [0]  # 매핑에서 실제로 찾아진 조합 수 (키 포맷이 바뀌면 0 → 축 폴백)

    def _status(cval: str, sval: str, axis_disabled: bool) -> str:
        info = _lotte_mapping_lookup(mapping, cval, sval)
        if info is not None:
            hits[0] += 1
            return 'in_stock' if _lotte_combo_in_stock(info, stk_mgt_yn) else 'out_of_stock'
        if mapping:
            return 'out_of_stock'   # 매핑에 없는 조합 = 판매 안 하는 조합
        return 'out_of_stock' if axis_disabled else 'in_stock'

    def _axis_status(axis_disabled: bool) -> str:
        return 'out_of_stock' if axis_disabled else 'in_stock'

    options = []
    if colors and sizes:
        for c in colors:
            for sz in sizes:
                options.append({
                    'color': c['label'],
                    'size': _lotte_norm_size(sz['label']),
                    'option_code': f"{c['value']}/{sz['value']}",
                    'status': _status(c['value'], sz['value'], c['disabled'] or sz['disabled']),
                    '_axis_disabled': c['disabled'] or sz['disabled'],
                })
    elif colors:
        for c in colors:
            options.append({
                'color': c['label'], 'size': 'FREE', 'option_code': c['value'],
                'status': _status(c['value'], '', c['disabled']),
                '_axis_disabled': c['disabled'],
            })
    elif sizes:
        for sz in sizes:
            options.append({
                'color': '', 'size': _lotte_norm_size(sz['label']), 'option_code': sz['value'],
                'status': _status('', sz['value'], sz['disabled']),
                '_axis_disabled': sz['disabled'],
            })

    # 매핑은 있는데 한 조합도 못 찾았으면 키 포맷이 바뀐 것 → 전건 품절 오판 방지, 축 상태로 폴백
    if options and mapping and hits[0] == 0:
        log("  [DIAG] 롯데 optionMappingInfo 키 매칭 0건 → 축(disabled) 기준으로 폴백", "WARNING")
        for opt in options:
            opt['status'] = _axis_status(opt['_axis_disabled'])

    for opt in options:
        opt.pop('_axis_disabled', None)
    return options


def extract_lotte_ids(product_url: str) -> Tuple[Optional[str], Optional[str]]:
    """상품 URL → (sitmNo, pdNo)
    예: https://www.lotteon.com/p/product/LE1220935356?sitmNo=LE1220935356_1326285784&mall_no=2
    """
    sitm = None
    pid = None
    m = re.search(r'[?&]sitmNo=([^&#]+)', product_url or '')
    if m:
        sitm = urllib.parse.unquote(m.group(1)).strip()
    m = re.search(r'/p/product/([^/?#]+)', product_url or '')
    if m:
        pid = urllib.parse.unquote(m.group(1)).strip()
    return sitm, pid


# =====================================================
# 재고/가격 동기화 클래스
# =====================================================

class StockPriceSynchronizer(StockCommonMixin):

    def __init__(self):
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
                      AND ap.source_site = 'lotte'
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
    # 2. 롯데온에서 가격/재고 수집 (롯데온 API)
    # -------------------------------------------------
    def collect_from_lotte(self, product_url: str) -> Tuple[Dict, Optional[str]]:
        """
        롯데온 API로 가격/재고 수집 (lotte_collector와 동일 소스)

        - 상세 API: priceInfo.slPrc=정가, basicInfo.sitmSlStatCd=판매상태,
                    optionInfo.optionList=옵션축 / optionMappingInfo=조합별 재고
        - 즉시할인 API: immdDcAplyTotAmt=실매입가(할인 적용가, 목록 노출가와 동일)
        반환: ({'original_price'(정가), 'sale_price'(매입가), 'options':[...]}, error)
        """
        sitm_no, pd_no = extract_lotte_ids(product_url)
        if not sitm_no and not pd_no:
            return {}, f"URL에서 sitmNo/pdNo 추출 실패: {product_url}"

        # 상세 API (returnCode=400/404=삭제, TEMP=일시오류는 오삭제 방지로 스킵)
        detail, api_err = (None, None)
        if sitm_no:
            detail, api_err = lotte_api_get(LOTTE_DETAIL_SITM_API.format(sitm=sitm_no))
        # sitmNo가 없거나(구형 URL) 아이템번호가 교체된 경우 → 상품번호(pdNo) 기준으로 재확인
        if detail is None and api_err != "TEMP" and pd_no:
            detail, api_err = lotte_api_get(LOTTE_DETAIL_PD_API.format(pid=pd_no))

        if not detail:
            if api_err == "NOT_FOUND":
                return {}, "상품 삭제됨 (상품정보 없음)"
            return {}, f"일시적 오류 (스킵): {api_err or 'no data'}"

        time.sleep(random.uniform(0.2, 0.4))

        try:
            result = {'original_price': 0, 'sale_price': 0, 'options': []}

            basic = detail.get('basicInfo') or {}
            price_info = detail.get('priceInfo') or {}
            stck_info = detail.get('stckInfo') or {}

            # 판매 상태 체크 (SALE 이외 = 품절/판매중지/판매종료)
            sale_status = (basic.get('sitmSlStatCd') or basic.get('spdSlStatCd') or '').upper()
            if sale_status and sale_status != 'SALE':
                status_nm = basic.get('sitmSlStatCdNm') or basic.get('spdSlStatCdNm') or sale_status
                return {}, f"판매 종료 상품 ({status_nm})"

            # 가격: slPrc=정가, 즉시할인 적용가=실매입가 (할인 없으면 정가와 동일)
            original_price = int(price_info.get('slPrc') or 0)
            sale_price = lotte_get_sale_price(detail) or original_price
            if original_price and sale_price > original_price:
                original_price = sale_price          # 방어: 정가 < 매입가 뒤집힘 방지
            result['original_price'] = original_price
            result['sale_price'] = sale_price

            # 옵션별 재고 (옵션축 + 조합별 재고)
            result['options'] = normalize_lotte_options(detail.get('optionInfo'), stck_info)

            # 옵션 없는 단일 상품 → 상품 전체 재고수량으로 처리
            if not result['options']:
                stk_qty = stck_info.get('stkQty')
                if stck_info.get('stkMgtYn') == 'N' or stk_qty is None:
                    in_stock = True
                else:
                    try:
                        in_stock = int(stk_qty) > 0
                    except (TypeError, ValueError):
                        in_stock = True
                result['options'].append({
                    'color': '', 'size': 'FREE',
                    'option_code': '', 'status': 'in_stock' if in_stock else 'out_of_stock'
                })

            return result, None

        except Exception as e:
            return {}, f"파싱 오류: {str(e)}"

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

        # 단일 옵션 상품 처리: DB 1개, 무신사 1개이면 이름 상관없이 직접 매칭
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
        """lotte 품절/삭제/흠집 → 이 ace 의 옵션 전부 out_of_stock 표시.
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
            # 1. 롯데온 API로 가격/재고 수집
            mall_data, error = self.collect_from_lotte(product['source_product_url'])
            if error:
                add_log(f"  롯데온 수집 실패: {error}", "WARNING")

                # 일시적 API 오류 → 삭제하지 않고 스킵
                if "일시적 오류" in error:
                    add_log(f"  → API 일시적 오류, 이번 회차 스킵")
                    with stats_lock:
                        stats['skipped'] += 1
                    log_batch(logs)
                    return

                # [MERGE] 바이마 직접 삭제 안 함 — lotte 옵션만 재고0 표시.
                #   lotte만 품절/삭제이어도 다른 몰 있으면 winner 이동, 없으면 reconcile 이 retire.
                add_log(f"  → 수집처 삭제/종료 → lotte 재고0 표시 (BUYMA 반영은 reconcile)")
                if not dry_run:
                    self._mark_all_out_of_stock(product['id'])
                    self.update_sync_time_only(product['id'])
                else:
                    add_log(f"  [DRY-RUN] lotte 재고0 표시 예정")
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
        log("재고/가격 동기화 시작 (lotte)")
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
    parser = argparse.ArgumentParser(description='바이마 재고/가격 동기화 (lotte)')
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
