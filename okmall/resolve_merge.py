# -*- coding: utf-8 -*-
"""
MERGE 4단계 — RESOLVE (winner 선정 + 출품 옵션 합집합)

각 listing(그룹)마다:
  1. 판매가 결정: 경쟁자 최저가(기존 ace_products.buyma_lowest_price 재사용) /
     경쟁자 없으면 30% 목표가
  2. 멤버별 마진 계산 (기존 calculate_margin_rate 동일) → is_margin_ok = 마진액>0
  3. winner = 마진O 멤버 중 매입가 최저 → buyma_listings.winner_offering_id
  4. 출품 옵션 = 마진O 멤버의 재고있는 옵션 합집합 → listing_options
     (옵션마다 그걸 가진 마진O 멤버 중 매입가 최저를 소싱 포인터로)

경쟁자 최저가는 새로 크롤하지 않고 기존 저장값 재사용(7단계 reconcile에서 신선도 갱신).
마진O 멤버가 없으면 그 listing은 출품 불가(control='draft' 유지 + winner NULL).
(suspend 플래그는 BUYMA가 거부하므로 절대 사용 안 함)

Usage:
    python resolve_merge.py             # DRY-RUN (기본)
    python resolve_merge.py --execute   # 실제 반영
"""

import os
import random
import argparse
import logging
from collections import defaultdict

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# === 마진 상수/함수: buyma_lowest_price_collector.py 와 동일 (동작 보존 위해 복제) ===
EXCHANGE_RATE = 9.2
SALES_FEE_RATE = 0.055
DEFAULT_SHIPPING_FEE = 15000


def calculate_margin_rate(buyma_price_jpy, purchase_price_krw, shipping_fee_krw=DEFAULT_SHIPPING_FEE):
    """마진율(%), 마진액(원) — 원본 collector와 동일 공식."""
    try:
        buyma_price_jpy = int(buyma_price_jpy) if buyma_price_jpy else 0
        purchase_price_krw = float(purchase_price_krw) if purchase_price_krw else 0
        shipping_fee_krw = int(shipping_fee_krw) if shipping_fee_krw else DEFAULT_SHIPPING_FEE
    except (ValueError, TypeError):
        return None, None
    if not buyma_price_jpy or buyma_price_jpy <= 0 or not purchase_price_krw or purchase_price_krw <= 0:
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


def calculate_target_price_jpy(purchase_price_krw, shipping_fee_krw=DEFAULT_SHIPPING_FEE, target_margin_rate=0.30):
    """목표 마진율이 되는 판매가(엔) 역산 — 원본 collector와 동일.
    무경쟁 목표마진 30% (커밋 10baa77, 2026-06-15 운영 일괄변경과 일치)."""
    try:
        purchase_price_krw = float(purchase_price_krw) if purchase_price_krw else 0
        shipping_fee_krw = int(shipping_fee_krw) if shipping_fee_krw else DEFAULT_SHIPPING_FEE
    except (ValueError, TypeError):
        return None
    if purchase_price_krw <= 0:
        return None
    total_cost = purchase_price_krw + float(shipping_fee_krw)
    vat_refund = purchase_price_krw / 11.0
    denominator = (1.0 - SALES_FEE_RATE) - target_margin_rate
    if denominator <= 0:
        return None
    buyma_price_krw = (total_cost - vat_refund) / denominator
    return int(buyma_price_krw / EXCHANGE_RATE)


def get_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST', '54.180.248.182'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER', 'block'),
        password=os.getenv('DB_PASSWORD', '1234'),
        database=os.getenv('DB_NAME', 'buyma'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


# ============================================================
# 데이터 프리로드
# ============================================================

def load_all(conn):
    cur = conn.cursor()

    cur.execute("SELECT id, category_id FROM buyma_listings WHERE is_active=1")
    listings = {r['id']: r for r in cur.fetchall()}
    logger.info(f"listings 로드: {len(listings)}")

    cur.execute("""
        SELECT id, listing_id, source_site, ace_product_id, purchase_price_krw
        FROM source_offerings WHERE is_active=1
    """)
    offerings_by_listing = defaultdict(list)
    for r in cur.fetchall():
        offerings_by_listing[r['listing_id']].append(r)
    logger.info(f"offerings 로드: {sum(len(v) for v in offerings_by_listing.values())}")

    cur.execute("""
        SELECT id, offering_id, color_value, size_value, stock_type, stocks
        FROM source_offering_options
    """)
    options_by_offering = defaultdict(list)
    for r in cur.fetchall():
        options_by_offering[r['offering_id']].append(r)
    logger.info(f"options 로드: {sum(len(v) for v in options_by_offering.values())}")

    # ace 가격/정보: buyma_lowest_price(경쟁자), 신선도, buying_shop_name
    cur.execute("""
        SELECT id, buyma_lowest_price, buyma_lowest_price_checked_at, buying_shop_name
        FROM ace_products WHERE is_active=1 OR status='duple'
    """)
    ace_info = {r['id']: r for r in cur.fetchall()}
    logger.info(f"ace 가격정보 로드: {len(ace_info)}")

    # 카테고리 배송비
    fee_map = {}
    try:
        cur.execute("SELECT buyma_category_id, expected_shipping_fee FROM buyma_master_categories_data")
        for r in cur.fetchall():
            if r['expected_shipping_fee'] is not None:
                fee_map[r['buyma_category_id']] = int(float(r['expected_shipping_fee']))
    except Exception as e:
        logger.warning(f"배송비 맵 로드 실패: {e}")
    logger.info(f"배송비 맵: {len(fee_map)}")

    return listings, offerings_by_listing, options_by_offering, ace_info, fee_map


# ============================================================
# RESOLVE 핵심
# ============================================================

def resolve_listing(listing, offerings, options_by_offering, ace_info, fee_map):
    """한 listing 해석 → 판단 결과 dict 반환 (DB 미반영)."""
    shipping = fee_map.get(listing['category_id'], DEFAULT_SHIPPING_FEE)

    # --- 경쟁자 최저가: 멤버 중 가장 최근 체크된 buyma_lowest_price>0 ---
    competitor = None
    best_at = None
    for off in offerings:
        ai = ace_info.get(off['ace_product_id'])
        if not ai:
            continue
        low = ai['buyma_lowest_price']
        if low and low > 0:
            at = ai['buyma_lowest_price_checked_at']
            if best_at is None or (at is not None and at > best_at):
                best_at = at
                competitor = int(low)

    # --- 판매가 결정 ---
    if competitor:
        # 운영 stock 과 동일: 현재가가 [경쟁자-9, 경쟁자-1] 범위면 유지(불필요 EDIT 방지),
        # 아니면 경쟁자 - random(1~9) 언더컷. 신규(현재가 0)는 자동으로 언더컷 = register 동일.
        cur_price = listing.get('price') or 0
        if competitor - 9 <= cur_price <= competitor - 1:
            selling = cur_price
        else:
            selling = competitor - random.randint(1, 9)
    else:
        # 경쟁자 없음 → 최저 매입가 기준 30% 목표가 (운영 11개 파일과 동일, 커밋 10baa77)
        purchases = [float(o['purchase_price_krw']) for o in offerings if o['purchase_price_krw']]
        if not purchases:
            return {'status': 'no_price', 'competitor': None, 'selling': None}
        selling = calculate_target_price_jpy(min(purchases), shipping, 0.30)
        if not selling:
            return {'status': 'no_price', 'competitor': None, 'selling': None}

    # --- 멤버별 마진 ---
    margins = {}  # offering_id -> (rate, amount, is_ok)
    for off in offerings:
        rate, amount = calculate_margin_rate(selling, off['purchase_price_krw'], shipping)
        is_ok = (amount is not None and amount > 0)
        margins[off['id']] = (rate, amount, is_ok)

    ok_offerings = [o for o in offerings if margins[o['id']][2]]
    if not ok_offerings:
        return {'status': 'no_margin', 'competitor': competitor, 'selling': selling, 'margins': margins}

    # --- winner = 마진O 중 매입가 최저 ---
    winner = min(ok_offerings, key=lambda o: float(o['purchase_price_krw'] or 1e18))

    # --- 옵션 합집합: 마진O 멤버의 재고있는 옵션, (color,size)별 매입가 최저 멤버 ---
    union = {}  # (color, size) -> (best_option_row, best_purchase, offering)
    for off in ok_offerings:
        pp = float(off['purchase_price_krw'] or 1e18)
        for opt in options_by_offering.get(off['id'], []):
            if opt['stock_type'] == 'out_of_stock':
                continue
            key = (opt['color_value'], opt['size_value'])
            if key not in union or pp < union[key][1]:
                union[key] = (opt, pp, off)

    listing_options = []
    for (color, size), (opt, pp, off) in union.items():
        listing_options.append({
            'color_value': color, 'size_value': size,
            'stock_type': opt['stock_type'], 'stocks': opt['stocks'],
            'sourced_offering_option_id': opt['id'],
        })

    return {
        'status': 'ok',
        'competitor': competitor,
        'selling': selling,
        'margins': margins,
        'winner': winner,
        'winner_shop': (ace_info.get(winner['ace_product_id']) or {}).get('buying_shop_name'),
        'listing_options': listing_options,
    }


def run(conn, dry_run=True):
    listings, offerings_by_listing, options_by_offering, ace_info, fee_map = load_all(conn)
    cur = conn.cursor()

    stats = {
        'total': len(listings), 'resolved': 0, 'no_margin': 0, 'no_price': 0, 'no_offering': 0,
        'competitor_used': 0, 'target_used': 0,
        'listing_options': 0, 'winner_by_source': defaultdict(int),
    }

    off_updates = []   # (margin_rate, margin_amount, is_ok, offering_id)
    lst_updates = []   # (price, buyma_low, is_lowest, winner_id, shop, control, listing_id)
    opt_rows = []      # listing_options insert dicts
    BATCH = 1000

    def flush():
        if dry_run:
            off_updates.clear(); lst_updates.clear(); opt_rows.clear(); return
        if off_updates:
            cur.executemany("""UPDATE source_offerings
                SET margin_rate=%s, margin_amount_krw=%s, is_margin_ok=%s, updated_at=CURRENT_TIMESTAMP
                WHERE id=%s""", off_updates)
            off_updates.clear()
        if lst_updates:
            cur.executemany("""UPDATE buyma_listings
                SET price=%s, buyma_lowest_price=%s, is_lowest_price=%s,
                    winner_offering_id=%s, buying_shop_name=%s, control=%s, updated_at=CURRENT_TIMESTAMP
                WHERE id=%s""", lst_updates)
            lst_updates.clear()
        if opt_rows:
            cur.executemany("""INSERT INTO listing_options
                (listing_id, color_value, size_value, stock_type, stocks, sourced_offering_option_id, is_active)
                VALUES (%(listing_id)s, %(color_value)s, %(size_value)s, %(stock_type)s, %(stocks)s,
                        %(sourced_offering_option_id)s, 1)
                ON DUPLICATE KEY UPDATE
                    stock_type=VALUES(stock_type), stocks=VALUES(stocks),
                    sourced_offering_option_id=VALUES(sourced_offering_option_id),
                    is_active=1, updated_at=CURRENT_TIMESTAMP""", opt_rows)
            opt_rows.clear()
        conn.commit()

    processed = 0
    for lid, listing in listings.items():
        offerings = offerings_by_listing.get(lid, [])
        if not offerings:
            stats['no_offering'] += 1
            continue

        r = resolve_listing(listing, offerings, options_by_offering, ace_info, fee_map)

        if r['status'] == 'ok':
            stats['resolved'] += 1
            if r['competitor']:
                stats['competitor_used'] += 1
            else:
                stats['target_used'] += 1
            stats['winner_by_source'][r['winner']['source_site']] += 1
            stats['listing_options'] += len(r['listing_options'])

            for off in offerings:
                rate, amount, is_ok = r['margins'][off['id']]
                off_updates.append((rate, amount, 1 if is_ok else 0, off['id']))
            lst_updates.append((r['selling'], r['competitor'], 1,
                                r['winner']['id'], r['winner_shop'], 'draft', lid))
            for o in r['listing_options']:
                opt_rows.append({'listing_id': lid, **o})
        elif r['status'] == 'no_margin':
            stats['no_margin'] += 1
            for off in offerings:
                rate, amount, is_ok = r['margins'][off['id']]
                off_updates.append((rate, amount, 0, off['id']))
            # 출품불가: suspend는 BUYMA가 거부하므로 사용 금지.
            # control='draft' 유지 + winner_offering_id=NULL 로 표시 (register가 winner 없는 건 제외)
            lst_updates.append((r['selling'], r['competitor'], 0, None, None, 'draft', lid))
        else:  # no_price
            stats['no_price'] += 1

        processed += 1
        if len(opt_rows) >= BATCH or len(off_updates) >= BATCH * 3:
            flush()
        if processed % 5000 == 0:
            logger.info(f"  진행: {processed}/{len(listings)}, resolved={stats['resolved']}")

    flush()
    return stats


def print_report(stats, dry_run):
    mode = "DRY-RUN" if dry_run else "EXECUTED"
    logger.info("=" * 60)
    logger.info(f"  MERGE RESOLVE 결과 [{mode}]")
    logger.info("=" * 60)
    logger.info(f"  listing 총:                   {stats['total']}")
    logger.info(f"  resolved (출품가능):          {stats['resolved']}")
    logger.info(f"    - 경쟁자가 기준:            {stats['competitor_used']}")
    logger.info(f"    - 30% 목표가 기준:          {stats['target_used']}")
    logger.info(f"  마진O 멤버 없음 (출품불가):   {stats['no_margin']}")
    logger.info(f"  가격 계산 불가:               {stats['no_price']}")
    logger.info(f"  offering 없음:                {stats['no_offering']}")
    logger.info(f"  listing_options 적재:         {stats['listing_options']}건")
    logger.info("-" * 60)
    logger.info("  winner 수집처 분포:")
    for src, cnt in sorted(stats['winner_by_source'].items(), key=lambda x: -x[1]):
        logger.info(f"    {src:18} {cnt}건")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='MERGE 4단계 RESOLVE')
    parser.add_argument('--execute', action='store_true', help='실제 반영 (없으면 DRY-RUN)')
    args = parser.parse_args()
    dry_run = not args.execute

    logger.info(f"resolve_merge 시작 (mode: {'DRY-RUN' if dry_run else 'EXECUTE'})")
    conn = get_connection()
    try:
        stats = run(conn, dry_run=dry_run)
        print_report(stats, dry_run)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
