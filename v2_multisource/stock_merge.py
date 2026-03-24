# -*- coding: utf-8 -*-
"""
MERGE 단계 스크립트 (orchestrator_v2에서 호출)

PRICE 단계 이후 실행되며, 바이마 최저가 기반으로
멀티소스 재고를 통합하여 ace_products에 반영.

대상: 미등록 상품 (is_published=0) 중 buyma_lowest_price가 있는 것

사용법:
    python stock_merge.py --brand NIKE
    python stock_merge.py                   # 전체 브랜드

작성일: 2026-03-10
"""

import os
import sys
import argparse
from datetime import datetime
from typing import List, Dict, Optional
import pymysql
from dotenv import load_dotenv

from stock_utils import merge_stocks

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# .env 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}


def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def get_target_products(conn, brand_name: Optional[str] = None) -> List[Dict]:
    """
    MERGE 대상 상품 조회

    조건:
    - is_active=1
    - is_published=0 (미등록)
    - buyma_lowest_price IS NOT NULL (PRICE 단계 완료)
    - model_no IS NOT NULL
    """
    sql = """
        SELECT ap.id, ap.model_no, ap.price, ap.category_id,
               ap.buyma_lowest_price, ap.brand_name, ap.name
        FROM ace_products ap
        WHERE ap.is_active = 1
          AND ap.is_published = 0
          AND ap.buyma_lowest_price IS NOT NULL
          AND ap.model_no IS NOT NULL
    """
    params = []

    if brand_name:
        sql += """
          AND (ap.brand_name = %s
               OR ap.brand_id IN (
                   SELECT DISTINCT buyma_brand_id FROM mall_brands
                   WHERE UPPER(mall_brand_name_en) = UPPER(%s)
                     OR UPPER(buyma_brand_name) = UPPER(%s)
               ))
        """
        params.extend([brand_name, brand_name, brand_name])

    sql += " ORDER BY ap.id"

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def main():
    parser = argparse.ArgumentParser(description='멀티소스 재고 통합 (MERGE 단계)')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 (예: NIKE)')
    parser.add_argument('--dry-run', action='store_true', help='DB 변경 없이 시뮬레이션')
    parser.add_argument('--limit', type=int, default=0, help='처리 상품 수 제한')
    args = parser.parse_args()

    log("=" * 60)
    log("멀티소스 재고 통합 (MERGE) 시작")
    if args.brand:
        log(f"  브랜드: {args.brand}")
    if args.dry_run:
        log(f"  모드: DRY-RUN (DB 변경 없음)")
    log("=" * 60)

    conn = pymysql.connect(**DB_CONFIG)

    try:
        products = get_target_products(conn, args.brand)
        total = len(products)

        if args.limit > 0:
            products = products[:args.limit]
            log(f"총 {total}개 중 {args.limit}개만 처리")
        else:
            log(f"총 {total}개 상품 처리 예정")

        stats = {'success': 0, 'skipped': 0, 'error': 0, 'no_source': 0}

        for idx, product in enumerate(products):
            ace_id = product['id']
            model_no = product['model_no']
            selling_price = product['price']
            category_id = product['category_id']

            try:
                log(f"[{idx+1}/{len(products)}] id={ace_id}, model_no={model_no}, price={selling_price}엔")

                if args.dry_run:
                    from stock_utils import fetch_all_sources, calc_breakeven_purchase_price, get_shipping_fee, extract_variants_from_raw
                    shipping_fee = get_shipping_fee(conn, category_id)
                    breakeven = calc_breakeven_purchase_price(selling_price, shipping_fee)
                    sources = fetch_all_sources(conn, model_no)
                    log(f"  손익분기 매입가: {breakeven:,.0f}원, source {len(sources)}건")
                    for src in sources:
                        profitable = "✓" if src['raw_price'] <= breakeven else "✗"
                        variants = extract_variants_from_raw(src['source_site'], src['raw_json_data'])
                        sizes = [v['size_value'] for v in variants if v['is_available']]
                        log(f"  {profitable} [{src['source_site']}] {src['raw_price']:,.0f}원 → 사이즈: {sizes}")
                    stats['success'] += 1
                    continue

                result = merge_stocks(conn, ace_id, model_no, selling_price, category_id)

                if result['total_sources'] == 0:
                    log(f"  → source 없음. 스킵.", "WARNING")
                    stats['no_source'] += 1
                    continue

                margin_str = f"{result['margin_rate']:.2f}%" if result['margin_rate'] is not None else "N/A"
                log(f"  → source {result['total_sources']}건 중 {result['profitable_sources']}건 마진 O")
                log(f"  → 재고: {len(result['in_stock_sizes'])}개, 품절: {len(result['out_of_stock_sizes'])}개, 마진: {margin_str}")
                stats['success'] += 1

            except Exception as e:
                log(f"  → 오류: {e}", "ERROR")
                stats['error'] += 1

        log("=" * 60)
        log(f"MERGE 완료: 성공 {stats['success']}, source없음 {stats['no_source']}, 오류 {stats['error']}")
        log("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
