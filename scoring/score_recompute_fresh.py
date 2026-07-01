# -*- coding: utf-8 -*-
"""
scoring Phase 2 — 출품대기중(fresh) 예상일일마진액 계산 → score_index_fresh.

3.2 공식:
  점수 = prior(brand, category) × 마진 × 1.0
  prior = 계층적 Bayesian Shrinkage (출품중 코호트의 예상 일평균판매 추정)
    ① n_bc>=min_cohort   : shrink(n_bc, avg_bc, kappa_bc, brand_avg)
    ② n_b >=min_brand    : shrink(n_b,  brand_avg, kappa_b, category_avg)
    ③ n_c >=min_category : shrink(n_c,  category_avg, kappa_c, global_avg)
    ④ else               : global_avg
  shrink(n, avg, k, parent) = (n·avg + k·parent) / (n + k)

코호트 원신호(prior 기준) = fresh_prior_signal 파라미터:
  'sold'      = sold/t                                     ← 스펙 §3.2 기본
  'composite' = (w_찜·찜 + w_조회·조회 + w_장·장 + w_판·판)/t ← 나중 튜닝 전환용

- 코호트 모집단 = 출품중(is_published=1). t = NOW − first_listed_at (없으면 1).
- 대기풀(fresh) = is_active=1 AND is_published=0 AND winner_offering 있음 AND 마진>0 (품절/삭제 제외).
- category = buyma_listings.category_id (정수, 깨끗함 → 텍스트 정규화 불필요). brand = brand_name.upper().strip().
- 조인은 파이썬 메모리(타입 CAST 회피). 파라미터는 scoring_parameters(O1). buyma_listings 안 건드림.

사용: python score_recompute_fresh.py            # dry-run(분포·층별발동·샘플, 쓰기 0)
      python score_recompute_fresh.py --execute  # cohort_priors + score_index_fresh 갱신
"""
import os, sys, io, argparse
from datetime import datetime
from collections import defaultdict
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pymysql
from dotenv import load_dotenv
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT',3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)

EXCLUDE_STATUS = ('soldout', 'deleted')     # 대기풀에서 제외할 status


def rows(conn, sql, *a):
    with conn.cursor() as cur:
        cur.execute(sql, a); return cur.fetchall()


def load_params(conn):
    p = {r['param_key']: r['value'] for r in rows(conn, "SELECT param_key, value FROM scoring_parameters")}
    return dict(
        signal=p.get('fresh_prior_signal', 'sold'),
        k_bc=int(p['kappa_bc']), k_b=int(p['kappa_b']), k_c=int(p['kappa_c']),
        min_bc=int(p['min_cohort_samples']), min_b=int(p['min_brand_samples']), min_c=int(p['min_category_samples']),
        g_fallback=float(p['global_avg_daily_sales']),
        w_fav=float(p['w_favorite']), w_acc=float(p['w_access']), w_cart=float(p['w_cart']), w_sold=float(p['w_sold']),
    )


def shrink(n, avg, k, parent):
    return (n * avg + k * parent) / (n + k) if (n + k) else parent


def norm(b):
    return (b or '').upper().strip()


def pct(sv, q):
    if not sv: return 0
    return sv[min(len(sv) - 1, int(q * len(sv)))]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    conn = pymysql.connect(**cfg)
    P = load_params(conn)
    now = datetime.now()
    print(f"[파라미터] signal={P['signal']}  kappa(bc/b/c)={P['k_bc']}/{P['k_b']}/{P['k_c']}  "
          f"min(bc/b/c)={P['min_bc']}/{P['min_b']}/{P['min_c']}  global_fallback={P['g_fallback']}")

    # ---------- 1. 코호트 prior 재계산 (모집단 = 출품중) ----------
    print("\n[1] 코호트 prior 계산 — 출품중 모집단 로드 ...")
    pub = rows(conn, """SELECT id, buyma_product_id, brand_name, category_id
                        FROM buyma_listings
                        WHERE is_active=1 AND is_published=1 AND buyma_product_id IS NOT NULL""")
    stats = {}
    for r in rows(conn, "SELECT buyma_product_id, access_count, favorite_count, cart_count, sold_count FROM buyma_product_stats"):
        try: stats[int(r['buyma_product_id'])] = r
        except: pass
    days = {r['buyma_product_id']: r['first_listed_at'] for r in rows(conn, "SELECT buyma_product_id, first_listed_at FROM buyma_listing_days")}

    bc_n = defaultdict(int); bc_s = defaultdict(float)
    b_n = defaultdict(int);  b_s = defaultdict(float)
    c_n = defaultdict(int);  c_s = defaultdict(float)
    g_n = 0; g_s = 0.0
    for p in pub:
        b = norm(p['brand_name']); c = p['category_id']
        fl = days.get(p['buyma_product_id'])
        t = max(1, (now - fl).days) if fl else 1
        s = stats.get(p['buyma_product_id'])
        fav = (s['favorite_count'] or 0) if s else 0
        acc = (s['access_count'] or 0) if s else 0
        cart = (s['cart_count'] or 0) if s else 0
        sold = (s['sold_count'] or 0) if s else 0
        if P['signal'] == 'composite':
            sig = (P['w_fav']*fav + P['w_acc']*acc + P['w_cart']*cart + P['w_sold']*sold) / t
        else:  # 'sold' (스펙 기본)
            sig = sold / t
        bc_n[(b, c)] += 1; bc_s[(b, c)] += sig
        b_n[b] += 1;       b_s[b] += sig
        c_n[c] += 1;       c_s[c] += sig
        g_n += 1;          g_s += sig

    bc_avg = {k: bc_s[k] / bc_n[k] for k in bc_n}
    b_avg = {k: b_s[k] / b_n[k] for k in b_n}
    c_avg = {k: c_s[k] / c_n[k] for k in c_n}
    global_avg = (g_s / g_n) if g_n else 0.0
    if global_avg <= 0:
        global_avg = P['g_fallback']
    print(f"    출품중 {len(pub):,} / 브랜드 {len(b_n):,} / 카테고리 {len(c_n):,} / bc쌍 {len(bc_n):,}")
    print(f"    global_avg(일평균신호) = {global_avg:.10f}")

    def cascade(b, c):
        if bc_n.get((b, c), 0) >= P['min_bc']:
            return shrink(bc_n[(b, c)], bc_avg[(b, c)], P['k_bc'], b_avg.get(b, global_avg)), 'bc'
        if b_n.get(b, 0) >= P['min_b']:
            return shrink(b_n[b], b_avg[b], P['k_b'], c_avg.get(c, global_avg)), 'b'
        if c_n.get(c, 0) >= P['min_c']:
            return shrink(c_n[c], c_avg[c], P['k_c'], global_avg), 'c'
        return global_avg, 'global'

    # ---------- 2. 대기풀(fresh) 점수 ----------
    print("\n[2] 대기풀(fresh) 점수 계산 — 후보 로드 ...")
    fresh = rows(conn, f"""SELECT bl.id, bl.brand_name, bl.category_id, bl.winner_offering_id,
                                  LEFT(bl.name,28) nm, a.margin_amount_krw m
                           FROM buyma_listings bl
                           JOIN source_offerings so ON so.id = bl.winner_offering_id
                           JOIN ace_products a ON a.id = so.ace_product_id
                           WHERE bl.is_active=1 AND bl.is_published=0
                             AND bl.winner_offering_id IS NOT NULL
                             AND (bl.status IS NULL OR bl.status NOT IN {EXCLUDE_STATUS})""")
    results = []; layer_hit = defaultdict(int); excl = 0
    for r in fresh:
        m = r['m']
        if m is None or float(m) <= 0:      # P9 exclude
            excl += 1; continue
        prior, layer = cascade(norm(r['brand_name']), r['category_id'])
        score = prior * float(m)
        layer_hit[layer] += 1
        results.append((r['id'], round(score, 4), dict(prior=prior, m=float(m), layer=layer,
                        brand=r['brand_name'], nm=r['nm'])))

    scores = sorted(x[1] for x in results)
    print(f"    대기풀 후보 {len(fresh):,} / 계산됨 {len(results):,} / 음수·무마진 제외 {excl:,}")
    print(f"    prior 층별 발동: bc={layer_hit['bc']:,}  b={layer_hit['b']:,}  c={layer_hit['c']:,}  global={layer_hit['global']:,}")
    if scores:
        print(f"    점수(원/일) 분포:  min={scores[0]:.2f}  p10={pct(scores,.1):.2f}  p50={pct(scores,.5):.2f}  "
              f"p90={pct(scores,.9):.2f}  max={scores[-1]:.2f}  mean={sum(scores)/len(scores):.2f}")

    def show(title, items):
        print(f"\n  [{title}]")
        for lid, sc, d in items:
            print(f"    #{lid} score={sc:>12,.2f} | prior={d['prior']:.8f}({d['layer']}) 마진={d['m']:,.0f} | {d['brand']} {d['nm']}")
    show("점수 상위 10", sorted(results, key=lambda x: -x[1])[:10])
    show("점수 하위 10", sorted(results, key=lambda x: x[1])[:10])

    if not args.execute:
        print("\n(dry-run — cohort_priors / score_index_fresh 미기록. 실제는 --execute)"); conn.close(); return

    # ---------- 기록 ----------
    cur = conn.cursor()
    # cohort_priors (감사/투명성용: 원 avg + global 기준 standalone smoothed). category_id=0=미사용 sentinel.
    cp = [('_GLOBAL_', 0, 'global', g_n, global_avg, global_avg, now)]
    for b in b_n:
        cp.append((b[:128], 0, 'b', b_n[b], b_avg[b], shrink(b_n[b], b_avg[b], P['k_b'], global_avg), now))
    for c in c_n:
        cp.append(('_ALL_', c, 'c', c_n[c], c_avg[c], shrink(c_n[c], c_avg[c], P['k_c'], global_avg), now))
    for (b, c) in bc_n:
        cp.append((b[:128], c, 'bc', bc_n[(b, c)], bc_avg[(b, c)],
                   shrink(bc_n[(b, c)], bc_avg[(b, c)], P['k_bc'], b_avg.get(b, global_avg)), now))
    cur.execute("DELETE FROM cohort_priors")
    for i in range(0, len(cp), 2000):
        cur.executemany("""INSERT INTO cohort_priors
            (brand_normalized, category_id, cohort_level, sample_count, sample_avg, smoothed_prior, calculated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""", cp[i:i+2000]); conn.commit()
    print(f"\n[기록] cohort_priors {len(cp):,}건")

    rw = [(lid, sc, now) for lid, sc, _ in results]
    for i in range(0, len(rw), 2000):
        cur.executemany("""INSERT INTO score_index_fresh (listing_id, score, calculated_at)
            VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE score=VALUES(score), calculated_at=VALUES(calculated_at)""",
            rw[i:i+2000]); conn.commit()
    print(f"[기록] score_index_fresh {len(rw):,}건")
    conn.close()


if __name__ == '__main__':
    main()
