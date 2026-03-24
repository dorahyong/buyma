# -*- coding: utf-8 -*-
"""
멀티소스 재고통합 공통 모듈

orchestrator(MERGE 단계)와 synchronizer 양쪽에서 import하여 사용.
같은 model_no를 가진 여러 수집처의 재고를 통합하고,
바이마 최저가 기준으로 마진이 남는 source의 재고는 구매가능,
마진이 안 남는 source의 재고는 품절로 처리.

사용처:
  - stock_merge.py (MERGE 단계, 미등록 상품)
  - stock_price_synchronizer_v2.py (등록 완료 상품)

작성일: 2026-03-10
"""

import json
import logging
from typing import List, Dict, Optional, Tuple
from decimal import Decimal

# =====================================================
# 상수 (buyma_lowest_price_collector.py와 동일)
# =====================================================
EXCHANGE_RATE = 9.2
SALES_FEE_RATE = 0.055
DEFAULT_SHIPPING_FEE = 15000
VAT_REFUND_RATE = 1 / 11.0

logger = logging.getLogger(__name__)


# =====================================================
# 마진 계산
# =====================================================

def calculate_margin(buyma_price_jpy: int, purchase_price_krw: float,
                     shipping_fee_krw: int = DEFAULT_SHIPPING_FEE) -> Tuple[Optional[float], Optional[float]]:
    """
    마진율 및 마진액 계산 (buyma_lowest_price_collector.py와 동일 공식)

    Returns:
        (마진율(%), 마진액(원)) or (None, None)
    """
    try:
        buyma_price_jpy = int(buyma_price_jpy) if buyma_price_jpy else 0
        purchase_price_krw = float(purchase_price_krw) if purchase_price_krw else 0
        shipping_fee_krw = int(shipping_fee_krw) if shipping_fee_krw else DEFAULT_SHIPPING_FEE
    except (ValueError, TypeError):
        return None, None

    if buyma_price_jpy <= 0 or purchase_price_krw <= 0:
        return None, None

    buyma_price_krw = float(buyma_price_jpy) * EXCHANGE_RATE
    sales_fee_krw = buyma_price_krw * SALES_FEE_RATE
    net_income_krw = buyma_price_krw - sales_fee_krw
    total_cost_krw = purchase_price_krw + float(shipping_fee_krw)
    margin_without_refund = net_income_krw - total_cost_krw
    vat_refund = purchase_price_krw / 11.0
    total_margin_krw = margin_without_refund + vat_refund
    margin_rate = (total_margin_krw / buyma_price_krw) * 100.0

    return round(margin_rate, 2), round(total_margin_krw, 0)


def calc_breakeven_purchase_price(selling_price_jpy: int,
                                  shipping_fee_krw: int = DEFAULT_SHIPPING_FEE) -> float:
    """
    바이마 판매가(JPY) → 마진 0%가 되는 최대 매입가(KRW) 역산

    공식 유도 (마진 = 0):
      실수령액 - 총원가 + 부가세환급 = 0
      buyma_krw × (1 - 수수료율) - (매입가 + 배송비) + 매입가/11 = 0
      buyma_krw × 0.945 - 매입가 - 배송비 + 매입가/11 = 0
      buyma_krw × 0.945 - 배송비 = 매입가 × (1 - 1/11)
      buyma_krw × 0.945 - 배송비 = 매입가 × (10/11)
      매입가 = (buyma_krw × 0.945 - 배송비) × 11/10

    Returns:
        손익분기 매입가 (KRW). 0 이하면 마진 불가.
    """
    if not selling_price_jpy or selling_price_jpy <= 0:
        return 0.0

    buyma_price_krw = float(selling_price_jpy) * EXCHANGE_RATE
    net_after_fee = buyma_price_krw * (1.0 - SALES_FEE_RATE)  # 0.945
    breakeven = (net_after_fee - float(shipping_fee_krw)) * (11.0 / 10.0)

    return breakeven


# =====================================================
# 수집처별 variants 파싱 (정규화)
# =====================================================

def extract_variants_from_raw(source_site: str, raw_json_data: dict) -> List[Dict]:
    """
    source별 raw_json_data에서 사이즈/재고 목록 추출 → 통일 형태 반환

    Returns:
        [{
            'color': str,           # 색상 원문
            'size_value': str,      # 사이즈 (예: "235", "240")
            'is_available': bool,   # 재고 있음 여부
            'option_code': str,     # 원본 옵션 코드
        }, ...]
    """
    if source_site == 'okmall':
        return _extract_okmall_variants(raw_json_data)
    elif source_site == 'kasina':
        return _extract_kasina_variants(raw_json_data)
    else:
        logger.warning(f"알 수 없는 source_site: {source_site}")
        return []


def _normalize_size(size_raw: str) -> str:
    """사이즈 정규화 (raw_to_converter_v2.py와 동일 로직)"""
    if not size_raw:
        return 'FREE'
    size_raw = size_raw.replace('품절 임박', '').replace('품절임박', '').strip()
    if size_raw in ['단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈']:
        return 'FREE'
    return size_raw


def _extract_okmall_variants(raw_json_data: dict) -> List[Dict]:
    """okmall raw_json_data에서 variants 추출"""
    variants = []
    options = raw_json_data.get('options', [])

    for opt in options:
        color = opt.get('color', 'FREE') or 'FREE'
        size_value = _normalize_size(opt.get('tag_size', ''))

        variants.append({
            'color': color,
            'size_value': size_value,
            'is_available': opt.get('status') == 'in_stock',
            'option_code': opt.get('option_code', ''),
        })

    return variants


def _extract_kasina_variants(raw_json_data: dict) -> List[Dict]:
    """
    kasina raw_json_data에서 variants 추출

    kasina는 color별 options 배열 구조:
    raw_json_data = {
        'color': 'STADIUM GREEN/BRT CRIMSON',
        'options': [
            {'color': '...', 'tag_size': '235', 'status': 'in_stock', 'option_code': '...', 'buy_price': 169000},
            ...
        ]
    }
    """
    variants = []
    options = raw_json_data.get('options', [])

    for opt in options:
        variants.append({
            'color': opt.get('color', ''),
            'size_value': opt.get('tag_size', ''),
            'is_available': opt.get('status') == 'in_stock',
            'option_code': opt.get('option_code', ''),
        })

    return variants


# =====================================================
# DB 조회
# =====================================================

def fetch_all_sources(conn, model_no: str) -> List[Dict]:
    """
    같은 model_no(=model_id)의 전체 raw_scraped_data 조회

    Returns:
        [{id, source_site, raw_price, stock_status, raw_json_data(parsed), product_url}, ...]
    """
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT id, source_site, raw_price, stock_status, raw_json_data, product_url
            FROM raw_scraped_data
            WHERE model_id = %s
              AND raw_price IS NOT NULL
              AND raw_price > 0
            ORDER BY source_site, raw_price ASC
        """, (model_no,))
        rows = cursor.fetchall()

    results = []
    for row in rows:
        # raw_json_data 파싱
        raw_json = row.get('raw_json_data')
        if isinstance(raw_json, str):
            try:
                raw_json = json.loads(raw_json)
            except (json.JSONDecodeError, TypeError):
                raw_json = {}
        elif raw_json is None:
            raw_json = {}

        results.append({
            'raw_data_id': row['id'],
            'source_site': row['source_site'],
            'raw_price': float(row['raw_price']),
            'stock_status': row['stock_status'],
            'raw_json_data': raw_json,
            'product_url': row.get('product_url', ''),
        })

    return results


def get_shipping_fee(conn, category_id: int) -> int:
    """카테고리별 예상 배송비 조회"""
    if not category_id:
        return DEFAULT_SHIPPING_FEE

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


# =====================================================
# 재고 통합 메인 로직
# =====================================================

def merge_stocks(conn, ace_product_id: int, model_no: str,
                 selling_price_jpy: int, category_id: int) -> Dict:
    """
    재고 통합 메인 함수

    1. 같은 model_no의 전체 source 조회
    2. 손익분기 매입가 계산
    3. 마진 남는 source → purchase_for_order, 안 남는 source → out_of_stock
    4. 전체 사이즈 union → ace_product_options + ace_product_variants UPSERT
    5. ace_products 가격 정보 UPDATE

    Args:
        conn: DB connection
        ace_product_id: ace_products.id
        model_no: 모델 번호 (= raw_scraped_data.model_id)
        selling_price_jpy: 바이마 판매가 (엔) — buyma_lowest_price - random 적용 후 값
        category_id: ace_products.category_id (배송비 조회용)

    Returns:
        {
            'total_sources': int,
            'profitable_sources': int,
            'merged_sizes': [str],
            'in_stock_sizes': [str],
            'out_of_stock_sizes': [str],
            'purchase_price_krw': float,  # worst case (마진 남는 중 최고가)
            'margin_rate': float,
            'margin_amount_krw': float,
        }
    """
    # 1. 전체 source 조회
    all_sources = fetch_all_sources(conn, model_no)
    if not all_sources:
        return {
            'total_sources': 0, 'profitable_sources': 0,
            'merged_sizes': [], 'in_stock_sizes': [], 'out_of_stock_sizes': [],
            'purchase_price_krw': 0, 'margin_rate': None, 'margin_amount_krw': None,
        }

    # 2. 손익분기 매입가 계산
    shipping_fee = get_shipping_fee(conn, category_id)
    breakeven_price = calc_breakeven_purchase_price(selling_price_jpy, shipping_fee)

    # 3. source별 variants 추출 + 마진 판단
    # size_value → {color, is_available, stock_type, source_site, source_raw_price, option_code}
    # 같은 사이즈가 여러 source에 있으면 마진 남는 + 더 싼 source 우선
    merged = {}  # key: (color, size_value)

    for src in all_sources:
        is_profitable = src['raw_price'] <= breakeven_price
        variants = extract_variants_from_raw(src['source_site'], src['raw_json_data'])

        for v in variants:
            key = (v['color'], v['size_value'])

            if not v['size_value']:
                continue

            new_entry = {
                'color': v['color'],
                'size_value': v['size_value'],
                'is_available': v['is_available'] and is_profitable,
                'source_site': src['source_site'],
                'source_raw_price': src['raw_price'],
                'option_code': v['option_code'],
                'raw_data_id': src['raw_data_id'],
            }

            if key not in merged:
                merged[key] = new_entry
            else:
                existing = merged[key]
                # 우선순위: 재고있음+마진남는 > 재고있음+마진안남는 > 품절
                # 같은 조건이면 더 싼 source
                new_available = new_entry['is_available']
                old_available = existing['is_available']

                if new_available and not old_available:
                    merged[key] = new_entry
                elif new_available == old_available and new_entry['source_raw_price'] < existing['source_raw_price']:
                    merged[key] = new_entry

    if not merged:
        return {
            'total_sources': len(all_sources), 'profitable_sources': 0,
            'merged_sizes': [], 'in_stock_sizes': [], 'out_of_stock_sizes': [],
            'purchase_price_krw': 0, 'margin_rate': None, 'margin_amount_krw': None,
        }

    # 4. ace_product_options + ace_product_variants UPSERT
    _upsert_merged_options_and_variants(conn, ace_product_id, merged)

    # 5. 통계 계산
    in_stock_sizes = [v['size_value'] for v in merged.values() if v['is_available']]
    out_of_stock_sizes = [v['size_value'] for v in merged.values() if not v['is_available']]
    all_sizes = [v['size_value'] for v in merged.values()]

    # purchase_price_krw = 마진 남는 source 중 최고가 (worst case)
    profitable_prices = [v['source_raw_price'] for v in merged.values() if v['is_available']]
    purchase_price_krw = max(profitable_prices) if profitable_prices else 0

    profitable_sources = len(set(
        v['source_site'] for v in merged.values() if v['is_available']
    ))

    # 마진 계산 (worst case 기준)
    margin_rate, margin_amount = None, None
    if purchase_price_krw > 0:
        margin_rate, margin_amount = calculate_margin(
            selling_price_jpy, purchase_price_krw, shipping_fee
        )

    # 6. ace_products UPDATE
    _update_ace_product_pricing(
        conn, ace_product_id, purchase_price_krw, selling_price_jpy,
        margin_rate, margin_amount, shipping_fee
    )

    return {
        'total_sources': len(all_sources),
        'profitable_sources': profitable_sources,
        'merged_sizes': all_sizes,
        'in_stock_sizes': in_stock_sizes,
        'out_of_stock_sizes': out_of_stock_sizes,
        'purchase_price_krw': purchase_price_krw,
        'margin_rate': margin_rate,
        'margin_amount_krw': margin_amount,
    }


# =====================================================
# DB UPSERT (내부 함수)
# =====================================================

def _upsert_merged_options_and_variants(conn, ace_product_id: int, merged: Dict):
    """
    통합된 재고를 ace_product_options + ace_product_variants에 UPSERT

    규칙:
    - options에 있는 size = variants에 있는 size (1:1 매칭 필수)
    - 기존에 없던 사이즈는 INSERT, 있던 사이즈는 UPDATE
    - 기존에 있었지만 더 이상 어느 source에도 없는 사이즈는 out_of_stock으로 UPDATE
    """
    with conn.cursor() as cursor:
        # 현재 ace_product_options (size) 조회
        cursor.execute("""
            SELECT id, value, position FROM ace_product_options
            WHERE ace_product_id = %s AND option_type = 'size'
            ORDER BY position
        """, (ace_product_id,))
        existing_options = {row['value']: row for row in cursor.fetchall()}

        # 현재 ace_product_variants 조회
        cursor.execute("""
            SELECT id, color_value, size_value, stock_type FROM ace_product_variants
            WHERE ace_product_id = %s
        """, (ace_product_id,))
        existing_variants = {(row['color_value'], row['size_value']): row for row in cursor.fetchall()}

        # 다음 position 번호
        max_position = max((v['position'] for v in existing_options.values()), default=0)

        # merged에 있는 사이즈 처리
        processed_keys = set()

        for (color, size_value), entry in merged.items():
            processed_keys.add((color, size_value))
            stock_type = 'purchase_for_order' if entry['is_available'] else 'out_of_stock'
            stocks = 1 if entry['is_available'] else 0

            # --- ace_product_options (size) ---
            if size_value not in existing_options:
                max_position += 1
                cursor.execute("""
                    INSERT INTO ace_product_options
                    (ace_product_id, option_type, value, master_id, position, source_option_value)
                    VALUES (%s, 'size', %s, 0, %s, %s)
                """, (ace_product_id, size_value, max_position, size_value))
                existing_options[size_value] = {'position': max_position}

            # --- ace_product_variants ---
            options_json = json.dumps([
                {"type": "color", "value": color},
                {"type": "size", "value": size_value}
            ], ensure_ascii=False) if color else json.dumps([
                {"type": "size", "value": size_value}
            ], ensure_ascii=False)

            if (color, size_value) in existing_variants:
                # UPDATE
                cursor.execute("""
                    UPDATE ace_product_variants
                    SET stock_type = %s, stocks = %s, options_json = %s,
                        source_option_code = %s, source_stock_status = %s,
                        source_site = %s, source_raw_price = %s
                    WHERE ace_product_id = %s AND color_value = %s AND size_value = %s
                """, (
                    stock_type, stocks, options_json,
                    entry['option_code'], 'in_stock' if entry['is_available'] else 'out_of_stock',
                    entry['source_site'], entry['source_raw_price'],
                    ace_product_id, color, size_value
                ))
            else:
                # INSERT
                cursor.execute("""
                    INSERT INTO ace_product_variants
                    (ace_product_id, color_value, size_value, options_json,
                     stock_type, stocks, source_option_code, source_stock_status,
                     source_site, source_raw_price)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ace_product_id, color, size_value, options_json,
                    stock_type, stocks,
                    entry['option_code'], 'in_stock' if entry['is_available'] else 'out_of_stock',
                    entry['source_site'], entry['source_raw_price']
                ))

        # 기존에 있었지만 merged에 없는 variants → out_of_stock
        for (color, size_value), existing in existing_variants.items():
            if (color, size_value) not in processed_keys:
                cursor.execute("""
                    UPDATE ace_product_variants
                    SET stock_type = 'out_of_stock', stocks = 0
                    WHERE ace_product_id = %s AND color_value = %s AND size_value = %s
                """, (ace_product_id, color, size_value))

    conn.commit()


def _update_ace_product_pricing(conn, ace_product_id: int,
                                purchase_price_krw: float,
                                selling_price_jpy: int,
                                margin_rate: Optional[float],
                                margin_amount_krw: Optional[float],
                                shipping_fee_krw: int):
    """ace_products 가격/마진 정보 UPDATE"""
    with conn.cursor() as cursor:
        purchase_price_jpy = int(purchase_price_krw / EXCHANGE_RATE) if purchase_price_krw else 0

        cursor.execute("""
            UPDATE ace_products
            SET purchase_price_krw = %s,
                purchase_price_jpy = %s,
                price = %s,
                expected_shipping_fee = %s,
                margin_rate = %s,
                margin_amount_krw = %s,
                margin_calculated_at = NOW()
            WHERE id = %s
        """, (
            purchase_price_krw,
            purchase_price_jpy,
            selling_price_jpy,
            shipping_fee_krw,
            margin_rate,
            margin_amount_krw,
            ace_product_id
        ))

    conn.commit()
