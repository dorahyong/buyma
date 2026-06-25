# -*- coding: utf-8 -*-
"""
MERGE reconcile 엔진 — 분류 + 영향 리포트 (읽기전용, 라이브 0)

각 출품가능 listing 을 모드로 분류:
  - CREATE  : 그룹 멤버 중 BUYMA 등록(buyma_product_id) 0개 → 신규 등록
  - EDIT    : 1개 → 기존 출품 수정 (소싱교정/가격/옵션/재고)
  - COLLAPSE: 2개+ → 같은 모델이 여러 출품으로 → 1개로 합쳐야 함 (수동검수)

EDIT 후보는 "소싱 교정"(현재 게시된 몰 ≠ merge winner = 더 싼 몰) 여부도 본다.

이 파일은 분류·집계만. 실제 edit/collapse 실행은 다음 단계.

Usage:
    python reconcile_engine.py            # 전체 분류 + 영향 집계
    python reconcile_engine.py --sample 8 # EDIT 소싱교정 샘플 상세
"""

import os
import argparse
from collections import defaultdict

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))


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


def load_classification(conn):
    """모든 출품가능 listing → 모드 + 소싱교정 판정 (set 기반, 1쿼리)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            l.id,
            w.source_site                         AS winner_site,
            w.purchase_price_krw                  AS winner_price,
            wa.is_published                       AS winner_is_pub,
            COUNT(DISTINCT CASE WHEN a.is_published=1 THEN a.buyma_product_id END) AS n_published
        FROM buyma_listings l
        JOIN source_offerings w  ON w.id = l.winner_offering_id
        LEFT JOIN ace_products  wa ON wa.id = w.ace_product_id
        LEFT JOIN source_offerings so ON so.listing_id = l.id
        LEFT JOIN ace_products  a  ON a.id = so.ace_product_id
        WHERE l.is_active = 1 AND l.winner_offering_id IS NOT NULL
        GROUP BY l.id, w.source_site, w.purchase_price_krw, wa.is_published
    """)
    return cur.fetchall()


def classify(rows):
    stats = {'create': 0, 'edit': 0, 'collapse': 0,
             'edit_sourcing_fix': 0, 'edit_same_winner': 0}
    by_mode = defaultdict(list)
    for r in rows:
        n = r['n_published']
        if n == 0:
            mode = 'create'
        elif n == 1:
            mode = 'edit'
        else:
            mode = 'collapse'
        stats[mode] += 1
        by_mode[mode].append(r)
        if mode == 'edit':
            # winner 의 ace 가 현재 게시본(is_published=1)이면 소싱 그대로, 아니면 더 싼 몰로 교정
            if r['winner_is_pub'] == 1:
                stats['edit_same_winner'] += 1
            else:
                stats['edit_sourcing_fix'] += 1
    return stats, by_mode


def report(conn):
    rows = load_classification(conn)
    stats, by_mode = classify(rows)
    total = len(rows)
    print("=" * 64)
    print("  reconcile 분류 (출품가능 listing 기준)")
    print("=" * 64)
    print(f"  총: {total}")
    print(f"  CREATE  (신규 등록):        {stats['create']}")
    print(f"  EDIT    (기존 1개 수정):     {stats['edit']}")
    print(f"     ├ 소싱 교정(더 싼 몰로):  {stats['edit_sourcing_fix']}")
    print(f"     └ winner 그대로:         {stats['edit_same_winner']}")
    print(f"  COLLAPSE(2+개 합치기):      {stats['collapse']}")
    print("=" * 64)
    return stats, by_mode


def sample_sourcing_fix(conn, n):
    """소싱 교정 EDIT 후보 상세: 현재 게시 몰/매입가 → merge winner 몰/매입가."""
    cur = conn.cursor()
    cur.execute("""
        SELECT l.id, l.name, l.price,
               w.source_site winner_site, w.purchase_price_krw winner_price
        FROM buyma_listings l
        JOIN source_offerings w ON w.id = l.winner_offering_id
        LEFT JOIN ace_products wa ON wa.id = w.ace_product_id
        WHERE l.is_active=1 AND l.winner_offering_id IS NOT NULL
          AND (wa.is_published IS NULL OR wa.is_published=0)
          AND (SELECT COUNT(DISTINCT a.buyma_product_id) FROM source_offerings so
               JOIN ace_products a ON a.id=so.ace_product_id AND a.is_published=1
               WHERE so.listing_id=l.id) = 1
        ORDER BY l.id LIMIT %s
    """, (n,))
    listings = cur.fetchall()
    print(f"\n[소싱 교정 EDIT 샘플 {len(listings)}건] (현재 게시 몰 → merge winner 몰)")
    for l in listings:
        cur.execute("""
            SELECT so.source_site, so.purchase_price_krw, a.is_published, so.is_margin_ok
            FROM source_offerings so JOIN ace_products a ON a.id=so.ace_product_id
            WHERE so.listing_id=%s ORDER BY so.purchase_price_krw
        """, (l['id'],))
        members = cur.fetchall()
        pub = [m for m in members if m['is_published'] == 1]
        cur_site = pub[0]['source_site'] if pub else '?'
        cur_price = pub[0]['purchase_price_krw'] if pub else None
        print(f"  #{l['id']} {l['name'][:42]}")
        print(f"     현재 게시: [{cur_site}] 매입 {cur_price}  →  winner: [{l['winner_site']}] 매입 {l['winner_price']}  "
              f"(절감 {float(cur_price)-float(l['winner_price']):.0f}원)" if cur_price else "")


def main():
    ap = argparse.ArgumentParser(description='reconcile 분류/영향 리포트 (읽기전용)')
    ap.add_argument('--sample', type=int, default=0, help='소싱교정 EDIT 샘플 N건 상세')
    args = ap.parse_args()
    conn = get_connection()
    try:
        report(conn)
        if args.sample:
            sample_sourcing_fix(conn, args.sample)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
