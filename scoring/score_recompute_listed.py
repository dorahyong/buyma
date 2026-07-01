# -*- coding: utf-8 -*-
"""
scoring Phase 1 — 출품중(Listed) 예상일일마진액 계산 → score_index_listed.

3.1 공식:
  점수 = 직접신호점수 × 마진 + α × 마진 × 시간감점(t)
  직접신호점수 = w_찜·(찜/t) + w_조회·(조회/t) + w_장바구니·(장바구니/t) + w_판매·(판매/t)
  t(등록기간, 일) = NOW − first_listed_at   (accumulated_seconds 는 아직 0이라 미사용)
  마진 = ace_products.margin_amount_krw (winner offering)
  음수/무마진 = 제외(P9 exclude)

조인은 파이썬 메모리에서(buyma_listings.buyma_product_id int ↔ stats varchar 타입불일치 CAST 회피).
파라미터는 scoring_parameters 에서 읽음(O1). buyma_listings 안 건드림(러너 돌아도 안전).

사용: python score_recompute_listed.py            # dry-run(분포·샘플, 쓰기 0)
      python score_recompute_listed.py --execute  # score_index_listed 갱신
"""
import os, sys, io, argparse
from datetime import datetime
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pymysql
from dotenv import load_dotenv
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT',3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)


def rows(conn, sql, *a):
    with conn.cursor() as cur:
        cur.execute(sql, a); return cur.fetchall()


def load_params(conn):
    p = {r['param_key']: r['value'] for r in rows(conn, "SELECT param_key, value FROM scoring_parameters")}
    return dict(
        grace=int(p['grace_period_days']), thr_days=int(p['decay_threshold_days']),
        thr_val=float(p['decay_threshold_value']), zero=int(p['decay_zero_days']),
        alpha=float(p['baseline_alpha']),
        w_fav=float(p['w_favorite']), w_acc=float(p['w_access']),
        w_cart=float(p['w_cart']), w_sold=float(p['w_sold']),
        floor=p['margin_floor_policy'],
    )


def time_decay(t, P):
    if t <= P['grace']: return 1.0
    if t <= P['thr_days']:
        return 1.0 - (1.0 - P['thr_val']) * (t - P['grace']) / (P['thr_days'] - P['grace'])
    if t <= P['zero']:
        return P['thr_val'] * (P['zero'] - t) / (P['zero'] - P['thr_days'])
    return 0.0


def pct(sorted_vals, q):
    if not sorted_vals: return 0
    i = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[i]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    conn = pymysql.connect(**cfg)
    P = load_params(conn)
    now = datetime.now()
    print(f"[파라미터] grace={P['grace']} thr={P['thr_days']}/{P['thr_val']} zero={P['zero']} "
          f"α={P['alpha']} w(찜/조회/장바구니/판매)={P['w_fav']}/{P['w_acc']}/{P['w_cart']}/{P['w_sold']}")

    print("[로드] 출품중 listing / stats / days / margin ...")
    pub = rows(conn, "SELECT id, buyma_product_id, winner_offering_id FROM buyma_listings WHERE is_active=1 AND is_published=1 AND buyma_product_id IS NOT NULL")
    stats = {}
    for r in rows(conn, "SELECT buyma_product_id, access_count, favorite_count, cart_count, sold_count FROM buyma_product_stats"):
        try: stats[int(r['buyma_product_id'])] = r
        except: pass
    days = {r['buyma_product_id']: r['first_listed_at'] for r in rows(conn, "SELECT buyma_product_id, first_listed_at FROM buyma_listing_days")}
    wids = [p['winner_offering_id'] for p in pub if p['winner_offering_id']]
    margin = {}
    for i in range(0, len(wids), 2000):
        ch = wids[i:i+2000]; ph = ','.join(['%s']*len(ch))
        for r in rows(conn, f"SELECT so.id, a.margin_amount_krw m FROM source_offerings so JOIN ace_products a ON a.id=so.ace_product_id WHERE so.id IN ({ph})", *ch):
            margin[r['id']] = r['m']
    print(f"  출품중 {len(pub):,} / stats {len(stats):,} / days {len(days):,} / margin {len(margin):,}")

    results = []            # (listing_id, score, dbg)
    st = {'calc':0, 'excl_margin':0, 'no_stats':0}
    for p in pub:
        m = margin.get(p['winner_offering_id'])
        m = float(m) if m is not None else None
        if m is None or m <= 0:      # P9 exclude
            st['excl_margin'] += 1; continue
        fl = days.get(p['buyma_product_id'])
        t = max(1, (now - fl).days) if fl else 1
        s = stats.get(p['buyma_product_id'])
        if s:
            fav = (s['favorite_count'] or 0); acc = (s['access_count'] or 0)
            cart = (s['cart_count'] or 0); sold = (s['sold_count'] or 0)
        else:
            fav = acc = cart = sold = 0; st['no_stats'] += 1
        direct = (P['w_fav']*fav/t + P['w_acc']*acc/t + P['w_cart']*cart/t + P['w_sold']*sold/t)
        decay = time_decay(t, P)
        score = direct * m + P['alpha'] * m * decay
        st['calc'] += 1
        results.append((p['id'], round(score, 4),
                        dict(m=m, t=t, fav=fav, acc=acc, cart=cart, sold=sold, direct=round(direct,6), decay=round(decay,3))))

    scores = sorted(r[1] for r in results)
    print("\n=== 결과 요약 ===")
    print(f"  계산됨: {st['calc']:,} / 음수·무마진 제외: {st['excl_margin']:,} / (그중 stats없음=baseline만: {st['no_stats']:,})")
    if scores:
        print(f"  점수(원/일) 분포:  min={scores[0]:.2f}  p10={pct(scores,.1):.2f}  p50={pct(scores,.5):.2f}  "
              f"p90={pct(scores,.9):.2f}  max={scores[-1]:.2f}  mean={sum(scores)/len(scores):.2f}")

    name = {r['id']: r for r in rows(conn, "SELECT id, brand_name, LEFT(name,28) name FROM buyma_listings WHERE id IN (%s)" %
            (','.join(str(r[0]) for r in (sorted(results,key=lambda x:x[1])[:10]+sorted(results,key=lambda x:-x[1])[:10])) or '0'))}
    def show(title, items):
        print(f"\n  [{title}]")
        for lid, sc, d in items:
            nm = name.get(lid, {})
            print(f"    #{lid} score={sc:>12,.2f} | 마진={d['m']:,.0f} t={d['t']}d decay={d['decay']} "
                  f"찜{d['fav']}/조회{d['acc']}/장{d['cart']}/판{d['sold']} | {nm.get('brand_name','')} {nm.get('name','')}")
    show("점수 상위 10", sorted(results, key=lambda x:-x[1])[:10])
    show("점수 하위 10", sorted(results, key=lambda x:x[1])[:10])

    if not args.execute:
        print("\n(dry-run — score_index_listed 미기록. 실제는 --execute)"); conn.close(); return

    cur = conn.cursor()
    B = 2000; rowsw = [(lid, sc, now) for lid, sc, _ in results]
    for i in range(0, len(rowsw), B):
        cur.executemany("""INSERT INTO score_index_listed (listing_id, score, calculated_at)
                           VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE score=VALUES(score), calculated_at=VALUES(calculated_at)""", rowsw[i:i+B])
        conn.commit()
    print(f"\n[기록 완료] score_index_listed {len(rowsw):,}건")
    conn.close()


if __name__ == '__main__':
    main()
