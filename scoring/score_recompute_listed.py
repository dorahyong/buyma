# -*- coding: utf-8 -*-
"""
scoring — 출품중(Listed) 예상일일마진액 계산 → score_index_listed.

공식 (scoring-system/is_published_scoring_system_v1.md):
  점수(원/일) = 앵커 × 마진 × 찜승수 × 가격승수 × 카테고리승수
              = 건당 마진 × 판매확률  = 슬롯당 기대이익

  마진        source_offerings.margin_amount_krw (winner). 음수·NULL 제외
  찜승수      buyma_product_stats.favorite_count 구간별 (scoring_parameters.signal_bands)
  가격승수    buyma_listings.price(엔) 구간별      (scoring_parameters.price_bands)
  카테고리승수 그 카테고리 판매율 ÷ 전역 판매율, 베이지안 축소(k=cohort_shrink_k)
              판매는 sold_count>0 이진. 표본 적은 꼬리는 자동으로 1.0 수렴
  앵커        실제 하루 마진 ÷ Σ(마진×찜×가격×카테고리)
              → Σ(점수) = 실제 하루 마진. 랭킹은 앵커와 무관(전체에 같은 수를 곱함)

대상: buyma_listings.is_published=1 AND is_lowest_price=1 AND price>0, winner 마진>0

★ ace_products 를 읽지 않는다. 마진은 source_offerings, 가격·카테고리는 buyma_listings.
  (ace 의 마진·가격은 "이 몰 혼자였다면"의 값이라 병합 구조에서 의미가 없어 2026-08-10 제거됨)

기록: score_index_listed (점수 + 승수 내역) / score_cohort_mult / score_anchor_history

사용: python score_recompute_listed.py            # dry-run (분포·상위/하위, 쓰기 0)
      python score_recompute_listed.py --execute  # 실제 기록
"""
import os
import sys
import io
import json
import argparse
from datetime import datetime
from collections import defaultdict

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4',
           cursorclass=pymysql.cursors.DictCursor)


def rows(conn, sql, *a):
    with conn.cursor() as cur:
        cur.execute(sql, a)
        return cur.fetchall()


def load_params(conn):
    p = {r['param_key']: r['value'] for r in rows(conn, "SELECT param_key, value FROM scoring_parameters")}
    return dict(
        signal_bands=[(hi, float(m)) for hi, m in json.loads(p['signal_bands'])],
        price_bands=[(hi, float(m)) for hi, m in json.loads(p['price_bands'])],
        k=int(p['cohort_shrink_k']),
        window=int(p['anchor_window_days']),
    )


def band(value, bands):
    """구간표에서 승수 찾기. 상한 None = 그 이상 전부."""
    for hi, mult in bands:
        if hi is None or value <= hi:
            return mult
    return bands[-1][1]


def pct(sorted_desc, q):
    """상위 q 분위 값 (내림차순 리스트 기준)."""
    if not sorted_desc:
        return 0
    return sorted_desc[min(len(sorted_desc) - 1, int((1 - q) * len(sorted_desc)))]


def main():
    ap = argparse.ArgumentParser(description='출품중 예상일일마진액 계산')
    ap.add_argument('--execute', action='store_true', help='실제 기록 (없으면 dry-run)')
    args = ap.parse_args()

    conn = pymysql.connect(**cfg)
    P = load_params(conn)
    now = datetime.now()
    print(f"[파라미터] 찜밴드 {len(P['signal_bands'])}구간 / 가격밴드 {len(P['price_bands'])}구간 "
          f"/ 축소 k={P['k']} / 앵커창 {P['window']}일")

    # ---------- 1. 대상 ----------
    targets = rows(conn, """
        SELECT l.id, l.buyma_product_id, l.price, l.category_id,
               so.margin_amount_krw AS margin
          FROM buyma_listings l
          JOIN source_offerings so ON so.id = l.winner_offering_id
         WHERE l.is_published = 1
           AND l.is_lowest_price = 1
           AND l.price > 0
           AND so.margin_amount_krw > 0
    """)
    print(f"[로드] 대상 {len(targets):,}건 (출품중 + 최저가 + 마진>0)")

    stats = {}
    for r in rows(conn, "SELECT buyma_product_id, favorite_count, sold_count FROM buyma_product_stats"):
        try:
            stats[int(r['buyma_product_id'])] = (r['favorite_count'] or 0, r['sold_count'] or 0)
        except (TypeError, ValueError):
            pass          # 숫자가 아닌 상품번호는 버린다(구 데이터)
    print(f"[로드] 통계 {len(stats):,}건")

    # ---------- 2. 카테고리 승수 (베이지안 축소) ----------
    n_cat, sold_cat = defaultdict(int), defaultdict(int)
    sold_total = 0
    for t in targets:
        _, sold = stats.get(t['buyma_product_id'], (0, 0))
        n_cat[t['category_id']] += 1
        if sold > 0:
            sold_cat[t['category_id']] += 1
            sold_total += 1
    global_rate = sold_total / len(targets) if targets else 0.0
    if global_rate <= 0:
        print("[중단] 대상 집합에 판매이력이 하나도 없다 — 카테고리 승수를 못 만든다")
        conn.close()
        return
    cohort = {c: ((sold_cat[c] + P['k'] * global_rate) / (n_cat[c] + P['k'])) / global_rate for c in n_cat}
    print(f"[코호트] 전역 판매율 {global_rate:.5f} / 카테고리 {len(cohort)}종 "
          f"(승수 {min(cohort.values()):.2f}~{max(cohort.values()):.2f})")

    # ---------- 3. 승수 곱 ----------
    parts = []          # (listing_id, raw, margin, fav, price, s, p, c)
    raw_sum = 0.0
    for t in targets:
        fav, _ = stats.get(t['buyma_product_id'], (0, 0))
        s = band(fav, P['signal_bands'])
        p = band(int(t['price']), P['price_bands'])
        c = cohort.get(t['category_id'], 1.0)
        m = float(t['margin'])
        raw = m * s * p * c
        raw_sum += raw
        parts.append((t['id'], raw, m, fav, int(t['price']), s, p, c))

    # ---------- 4. 앵커 ----------
    a = rows(conn, f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(so.margin_amount_krw), 0) AS total
          FROM buyma_self_orders o
          JOIN buyma_listings l ON l.buyma_product_id = o.buyma_product_id
          JOIN source_offerings so ON so.id = l.winner_offering_id
         WHERE o.ordered_at >= NOW() - INTERVAL {P['window']} DAY
    """)[0]
    daily_actual = float(a['total']) / P['window']
    anchor = daily_actual / raw_sum if raw_sum else 0.0
    print(f"[앵커] 최근 {P['window']}일 주문 {a['n']:,}건 → 하루 마진 {daily_actual:,.0f}원 "
          f"/ 곱의 합 {raw_sum:,.0f} → 앵커 {anchor:.12f}")

    results = [(lid, raw * anchor, m, fav, price, s, p, c)
               for lid, raw, m, fav, price, s, p, c in parts]
    results.sort(key=lambda x: -x[1])
    vals = [r[1] for r in results]
    total = sum(vals)

    print("\n=== 결과 ===")
    print(f"  점수 합계 {total:,.0f}원/일  vs 실제 하루 마진 {daily_actual:,.0f}원  "
          f"→ {'검산 일치' if abs(total - daily_actual) < 1 else '★ 불일치'}")
    if vals:
        print(f"  분포(원/일): max {vals[0]:,.1f} / p90 {pct(vals, .9):,.2f} / p50 {pct(vals, .5):,.2f} "
              f"/ p10 {pct(vals, .1):,.3f} / min {vals[-1]:,.4f}")

    ids = [r[0] for r in results[:10]] + [r[0] for r in results[-10:]]
    name = {r['id']: r for r in rows(
        conn, "SELECT id, brand_name, LEFT(name,30) nm FROM buyma_listings WHERE id IN (%s)"
              % (','.join(str(i) for i in ids) or '0'))}

    def show(title, items):
        print(f"\n  [{title}]")
        for lid, sc, m, fav, price, s, p, c in items:
            nm = name.get(lid, {})
            print(f"    #{lid:<7} {sc:>10,.2f}원/일 | 마진 {m:>9,.0f} 찜{fav:>3} {price:>7,}엔 "
                  f"| 신호{s:>6} 가격{p:>5} 코호트{c:.2f} | {nm.get('brand_name','')} {nm.get('nm','')}")

    show("상위 10", results[:10])
    show("하위 10", results[-10:])

    if not args.execute:
        print("\n(dry-run — 기록하지 않음. 실제 반영은 --execute)")
        conn.close()
        return

    # ---------- 5. 기록 ----------
    cur = conn.cursor()
    cur.executemany("""
        INSERT INTO score_cohort_mult (category_id, n, sold, mult, calculated_at)
        VALUES (%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE n=VALUES(n), sold=VALUES(sold), mult=VALUES(mult),
                                calculated_at=VALUES(calculated_at)
    """, [(c, n_cat[c], sold_cat[c], round(cohort[c], 6), now) for c in cohort])
    conn.commit()
    print(f"\n[기록] score_cohort_mult {len(cohort):,}건")

    cur.execute("""
        INSERT INTO score_anchor_history
            (calculated_at, anchor, daily_actual_margin, raw_sum, target_count, orders_in_window, window_days)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (now, anchor, daily_actual, raw_sum, len(targets), a['n'], P['window']))
    conn.commit()
    print("[기록] score_anchor_history 1건")

    # 이번 대상이 아닌 행은 지운다 — 최저가에서 밀렸거나 마진이 사라졌거나 내려간 상품이다.
    #   남겨두면 옛 계산식·옛 시점의 점수가 섞여, 나중에 Swap 이 그걸 현재 점수로 오해한다.
    #   이 표는 항상 "지금 점수를 매길 수 있는 상품 전부"만 담는다.
    ids = [r[0] for r in results]
    with conn.cursor() as c0:
        c0.execute("SELECT COUNT(*) c FROM score_index_listed")
        before = c0.fetchone()['c']
        c0.execute("CREATE TEMPORARY TABLE _keep (listing_id INT PRIMARY KEY)")
        for i in range(0, len(ids), 5000):
            chunk = ids[i:i + 5000]
            c0.executemany("INSERT IGNORE INTO _keep VALUES (%s)", [(x,) for x in chunk])
        c0.execute("""DELETE s FROM score_index_listed s
                       LEFT JOIN _keep k ON k.listing_id = s.listing_id
                      WHERE k.listing_id IS NULL""")
        removed = c0.rowcount
        c0.execute("DROP TEMPORARY TABLE _keep")
    conn.commit()
    print(f"[정리] 대상에서 빠진 옛 점수 {removed:,}건 삭제 (기존 {before:,}건)")

    B = 2000
    rw = [(lid, round(sc, 4), now, m, s, p, round(c, 4))
          for lid, sc, m, fav, price, s, p, c in results]
    for i in range(0, len(rw), B):
        cur.executemany("""
            INSERT INTO score_index_listed
                (listing_id, score, calculated_at, margin_krw, signal_mult, price_mult, cohort_mult)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE score=VALUES(score), calculated_at=VALUES(calculated_at),
                                    margin_krw=VALUES(margin_krw), signal_mult=VALUES(signal_mult),
                                    price_mult=VALUES(price_mult), cohort_mult=VALUES(cohort_mult)
        """, rw[i:i + B])
        conn.commit()
    print(f"[기록] score_index_listed {len(rw):,}건")
    conn.close()


if __name__ == '__main__':
    main()
