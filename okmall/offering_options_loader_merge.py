# -*- coding: utf-8 -*-
"""
MERGE 3단계 — source_offering_options 적재 (옵션·재고 투영)

offering(수집처별 상품)의 색/사이즈/재고/매입가를 채운다.
데이터는 이미 ace_product_variants에 있으므로 재수집/재변환 없이 DB→DB 투영만 한다.

흐름:
  source_offerings (2단계 적재)
    → (source_site, source_model_id)로 매칭되는 ace_products(active+duple)들의 id
    → 그 ace들의 ace_product_variants
    → source_offering_options 적재 (offering 단위 매입가 상속)

주의:
- offering 1개가 여러 색상 ace 행에 걸칠 수 있어 매칭 ace **전부**의 variants를 모음.
- ace_product_variants.source_raw_price 는 사실상 비어있음 → 옵션 매입가는
  source_offerings.purchase_price_krw (offering 단위)를 상속.
- is_margin_ok 는 0으로 둠 (4단계 RESOLVE에서 계산).

Usage:
    python offering_options_loader_merge.py             # DRY-RUN (기본, 변경 없음)
    python offering_options_loader_merge.py --execute   # 실제 적재
"""

import os
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


def load_ace_key_index(conn):
    """(source_site, model_no) -> [ace_product_id]  (active+duple)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, source_site, model_no
        FROM ace_products
        WHERE is_active = 1 OR status = 'duple'
    """)
    idx = defaultdict(list)
    n = 0
    for row in cur.fetchall():
        idx[(row['source_site'], row['model_no'])].append(row['id'])
        n += 1
    logger.info(f"ace 키 인덱스 로드: {n}건 / 키 {len(idx)}개")
    return idx


def load_variants_index(conn):
    """ace_product_id -> [variant rows]  (전체 1쿼리 프리로드)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ace_product_id, color_value, size_value,
               color_value_original, size_value_original,
               source_option_code, stock_type, stocks, source_stock_status
        FROM ace_product_variants
    """)
    idx = defaultdict(list)
    n = 0
    for row in cur.fetchall():
        idx[row['ace_product_id']].append(row)
        n += 1
    logger.info(f"variants 인덱스 로드: {n}건 / ace {len(idx)}개")
    return idx


def load_offerings(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, source_site, source_model_id, purchase_price_krw
        FROM source_offerings
        WHERE is_active = 1
    """)
    rows = cur.fetchall()
    logger.info(f"source_offerings 로드: {len(rows)}건")
    return rows


INSERT_SQL = """
    INSERT INTO source_offering_options
        (offering_id, color_value_original, size_value_original, source_option_code,
         color_value, size_value, stock_type, stocks, purchase_price_krw,
         is_margin_ok, source_stock_status)
    VALUES (%(offering_id)s, %(color_value_original)s, %(size_value_original)s, %(source_option_code)s,
            %(color_value)s, %(size_value)s, %(stock_type)s, %(stocks)s, %(purchase_price_krw)s,
            0, %(source_stock_status)s)
    ON DUPLICATE KEY UPDATE
        source_option_code = VALUES(source_option_code),
        color_value = VALUES(color_value),
        size_value = VALUES(size_value),
        stock_type = VALUES(stock_type),
        stocks = VALUES(stocks),
        purchase_price_krw = VALUES(purchase_price_krw),
        source_stock_status = VALUES(source_stock_status),
        updated_at = CURRENT_TIMESTAMP
"""


def load_options(conn, offerings, ace_key_idx, variants_idx, dry_run=True):
    cur = conn.cursor()
    stats = {
        'offerings_total': len(offerings),
        'offerings_with_options': 0,
        'offerings_no_ace': 0,
        'offerings_no_variant': 0,
        'options_loaded': 0,
        'by_stock_type': defaultdict(int),
    }

    pending = []
    BATCH = 1000
    processed = 0

    for off in offerings:
        ace_ids = ace_key_idx.get((off['source_site'], off['source_model_id']), [])
        if not ace_ids:
            stats['offerings_no_ace'] += 1
            continue

        seen = set()  # (color_orig, size_orig) 중복 방지 (여러 색상 ace 합칠 때)
        rows = []
        for aid in ace_ids:
            for v in variants_idx.get(aid, []):
                key = (v['color_value_original'], v['size_value_original'])
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    'offering_id': off['id'],
                    'color_value_original': v['color_value_original'],
                    'size_value_original': v['size_value_original'],
                    'source_option_code': v['source_option_code'],
                    'color_value': v['color_value'],
                    'size_value': v['size_value'],
                    'stock_type': v['stock_type'],
                    'stocks': v['stocks'],
                    'purchase_price_krw': off['purchase_price_krw'],  # offering 단위 상속
                    'source_stock_status': v['source_stock_status'],
                })
                stats['by_stock_type'][v['stock_type']] += 1

        if not rows:
            stats['offerings_no_variant'] += 1
            continue

        stats['offerings_with_options'] += 1
        stats['options_loaded'] += len(rows)
        pending.extend(rows)

        if not dry_run and len(pending) >= BATCH:
            cur.executemany(INSERT_SQL, pending)
            conn.commit()
            pending = []

        processed += 1
        if processed % 5000 == 0:
            logger.info(f"  진행: {processed}/{len(offerings)} offering, options={stats['options_loaded']}")

    if not dry_run and pending:
        cur.executemany(INSERT_SQL, pending)
        conn.commit()

    return stats


def print_report(stats, dry_run):
    mode = "DRY-RUN" if dry_run else "EXECUTED"
    logger.info("=" * 60)
    logger.info(f"  MERGE 옵션·재고 적재 결과 [{mode}]")
    logger.info("=" * 60)
    logger.info(f"  offering 총:                  {stats['offerings_total']}")
    logger.info(f"  옵션 적재된 offering:         {stats['offerings_with_options']}")
    logger.info(f"  매칭 ace 없는 offering:       {stats['offerings_no_ace']}")
    logger.info(f"  variants 없는 offering:       {stats['offerings_no_variant']}")
    logger.info(f"  source_offering_options 적재: {stats['options_loaded']}건")
    logger.info("-" * 60)
    logger.info("  재고 타입 분포:")
    for st, cnt in sorted(stats['by_stock_type'].items(), key=lambda x: -x[1]):
        logger.info(f"    {st:20} {cnt}건")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='MERGE 3단계 — source_offering_options 적재')
    parser.add_argument('--execute', action='store_true', help='실제 적재 (없으면 DRY-RUN)')
    args = parser.parse_args()
    dry_run = not args.execute

    logger.info(f"offering_options_loader_merge 시작 (mode: {'DRY-RUN' if dry_run else 'EXECUTE'})")

    conn = get_connection()
    try:
        ace_key_idx = load_ace_key_index(conn)
        variants_idx = load_variants_index(conn)
        offerings = load_offerings(conn)
        stats = load_options(conn, offerings, ace_key_idx, variants_idx, dry_run=dry_run)
        print_report(stats, dry_run)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
