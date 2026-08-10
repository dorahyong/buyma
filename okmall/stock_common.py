# -*- coding: utf-8 -*-
"""재고동기화 공용 부품 (몰별 stock_price_synchronizer_*_merge.py 가 함께 쓴다).

몰 14개 파일에 글자까지 똑같이 복제돼 있던 것을 여기로 모았다. 본문은 옮기기 전과 동일하다.
마진식·최저가 조회·DB 반영 방식을 바꾸려면 이제 이 파일 한 곳만 고치면 된다.

  - 상수      : 환율·수수료·기본배송비·DB접속·바이마 검색 설정
  - 모듈 함수 : 로그, 가격 파싱, 글자수 자르기, 모델명 변형, 마진 계산
  - StockCommonMixin : 몰별 동기화 클래스가 상속하는 공용 메서드
                       (DB 연결/갱신, 바이마 최저가 조회, reconcile 호출 등)

몰마다 달라야 하는 것은 여기 두지 않는다:
  - random_delay 와 REQUEST_DELAY_MIN/MAX (사이트마다 요청 간격이 다르다)
  - 사이트 긁기(collect_from_*), 옵션 대조(detect_stock_changes), 상품 처리(process_single_product)

2026-08-04 신설.
"""
import os
import json
import time
import random
import re
import threading
import unicodedata
import urllib.parse
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import requests
import pymysql
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)
# =====================================================
# 설정값
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
EXCHANGE_RATE = 9.2
SALES_FEE_RATE = 0.055
DEFAULT_SHIPPING_FEE = 15000
BUYMA_BUYER_ID = os.getenv('BUYMA_BUYER_ID', '')  # 내 바이마 판매자 ID
BUYMA_SEARCH_URL = "https://www.buyma.com/r/-O3/{model_no}/"
_log_lock = threading.Lock()

# =====================================================
# 공용 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)

def log_batch(messages: List[str]) -> None:
    """여러 로그 메시지를 한 번에 출력 (병렬 처리 시 섞임 방지)"""
    with _log_lock:
        for msg in messages:
            print(msg, flush=True)

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

def decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def _buyma_width(s: str) -> int:
    """바이마 반각 환산 길이 계산 (전각=2, 반각=1)"""
    w = 0
    for c in s:
        eaw = unicodedata.east_asian_width(c)
        w += 2 if eaw in ('F', 'W', 'A') else 1
    return w

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

    # 자른 자리가 공백이면 BUYMA 가 저장할 때 지운다 → 보낸 값과 BUYMA 값이 달라진다.
    #   name 은 게시 후 편집 불가라 값이 다르면 이후 수정이 거부될 수 있으므로 여기서 다듬는다.
    return result.rstrip()

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
# 공용 메서드 (몰별 동기화 클래스가 상속)
# =====================================================

class StockCommonMixin:
    """몰별 StockPriceSynchronizer 가 상속하는 공용 메서드 모음.
    본문은 각 몰 파일에 있던 것과 동일하다."""
    def get_connection(self) -> pymysql.Connection:
        return pymysql.connect(**DB_CONFIG)
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
    def get_buyma_lowest_price(self, model_no: str) -> Tuple[Optional[int], Optional[str]]:
        """
        바이마에서 경쟁자 최저가를 수집합니다.
        - 내 상품(BUYMA_BUYER_ID)은 제외하고 경쟁자 최저가를 반환
        - 내 상품만 있으면 None 반환 (경쟁자 없음)
        """
        if not model_no:
            return None, "모델번호 없음"

        encoded = urllib.parse.quote(model_no.replace('/', ' ').replace('#', ' ').replace('&', ' ').replace('?', ' ').strip(), safe='')
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
    def get_current_variants(self, ace_product_id: int) -> List[Dict]:
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, color_value, size_value, color_value_original, size_value_original, source_option_code, stock_type
                    FROM ace_product_variants
                    WHERE ace_product_id = %s
                """, (ace_product_id,))
                return cursor.fetchall()
        finally:
            conn.close()
    def update_ace_products_price(self, ace_product_id: int, original_price_krw: int,
                                   purchase_price_krw: int, price_jpy: int,
                                   original_price_jpy: int, buyma_lowest_price: int,
                                   margin_rate: float, margin_amount_krw: float = None,
                                   is_lowest_price: int = None, purchase_price_jpy: int = None) -> None:
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                # 마진(margin_rate·margin_amount_krw)은 ace 에 저장하지 않는다 — 판매가를
                # 목록이 정하므로 소싱처 혼자서는 마진을 확정할 수 없다. 진실은
                # source_offerings 에 있고 resolve_merge 가 목록 판매가로 매번 갱신한다.
                # 인자는 호출부 호환을 위해 그대로 받되 쓰지 않는다. (2026-08-10)
                cursor.execute("""
                    UPDATE ace_products
                    SET original_price_krw = %s,
                        purchase_price_krw = %s,
                        price = %s,
                        original_price_jpy = %s,
                        buyma_lowest_price = %s,
                        is_lowest_price = %s,
                        purchase_price_jpy = %s,
                        buyma_lowest_price_checked_at = NOW()
                    WHERE id = %s
                """, (original_price_krw, purchase_price_krw, price_jpy,
                      original_price_jpy, buyma_lowest_price,
                      is_lowest_price, purchase_price_jpy,
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
                # 출품 edit 성공 시 BUYMA에 보낸 available_until(today+90)을 DB에도 반영 (delete payload엔 없음)
                sent_available_until = request_data.get('product', {}).get('available_until')
                if response.get('success') and sent_available_until:
                    cursor.execute("""
                        UPDATE ace_products SET available_until = %s WHERE id = %s
                    """, (sent_available_until.replace('/', '-'), ace_product_id))
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
    def _reconcile_published(self, products: List[Dict]) -> None:
        """이번 회차에 refresh 한 상품들의 그룹만 reconcile 이 BUYMA push (옵션합침+싼몰).
        ★ 이번 synced 상품에 한정 (해당 몰 published 전체 아님) → --limit/--id 테스트 안전.
        그룹락으로 multi-PC 안전. push 결정(edit/retire)은 reconcile 이 담당."""
        import reconcile_runner as rr
        import reconcile_buyma_push as push
        from dedup_corrector_merge import canonicalize
        model_nos = [p['model_no'] for p in products if p.get('model_no')]
        if not model_nos:
            return
        conn = push.get_connection()
        try:
            with conn.cursor() as cur:
                fmt = ','.join(['%s'] * len(model_nos))
                cur.execute(f"SELECT DISTINCT model_no, brand_id FROM ace_products WHERE model_no IN ({fmt})",
                            model_nos)
                rows = cur.fetchall()
            seen, groups = set(), []
            for r in rows:
                key = (r['brand_id'], canonicalize(r['model_no']))
                if key in seen:
                    continue
                seen.add(key)
                groups.append((r['model_no'], r['brand_id']))
            log(f"[MERGE] reconcile push 대상(이번 refresh 그룹): {len(groups)}건")
            ok = err = skip = 0
            # 로그용 몰이름 — 이미 조회된 products 에서 꺼냄(추가 쿼리·JOIN 없음).
            _mall = (products[0].get('source_site') or '?') if products else '?'
            _total = len(groups)
            for _i, (model_no, brand_id) in enumerate(groups, 1):
                res = rr.process_one_group(conn, model_no, brand_id, dry_run=False, scope='published',
                                           tag=f"[{_mall} {_i}/{_total}] ")
                resp = res.get('response') or {}
                if res.get('skipped'):
                    skip += 1
                elif resp.get('success'):
                    ok += 1
                elif resp:
                    err += 1
                time.sleep(0.4)
            log(f"[MERGE] reconcile 완료: 성공 {ok} / 실패 {err} / 스킵 {skip}")
        finally:
            conn.close()
