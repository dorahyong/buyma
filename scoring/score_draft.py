# -*- coding: utf-8 -*-
"""
3번 — draft 채점 → score_index_draft

선행: 1번 토큰 · 2번 market_demand_cells / market_mn_cells

  python scoring/score_draft.py            # dry-run
  python scoring/score_draft.py --execute
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from draft_common import (
    KAPPA,
    L6_DENSITY,
    WINDOW_DAYS,
    brand_key_from_listing,
    cell_key_l3,
    cell_key_l4,
    cell_key_l6,
    clip_adj,
    connect,
    is_strong_mn,
    listing_cat_key,
    price_band,
    shrink_density,
    tokenize_model_no,
)

CHUNK_WRITE = 2000


def load_demand(cur) -> dict:
    cur.execute(
        "SELECT level, cell_key, density, avg_fav FROM market_demand_cells"
    )
    out = {}
    for r in cur.fetchall():
        dens = float(r["density"])
        fav = r["avg_fav"]
        out[(r["level"], r["cell_key"])] = (
            dens,
            float(fav) if fav is not None else None,
        )
    return out


def load_mn(cur) -> dict:
    cur.execute(
        "SELECT mn_norm, brand_key, n_act, ord90, max_fav FROM market_mn_cells"
    )
    out = {}
    for r in cur.fetchall():
        out[(r["mn_norm"], r["brand_key"])] = (
            int(r["n_act"]),
            int(r["ord90"]),
            int(r["max_fav"] or 0),
        )
    return out


def load_targets(cur) -> list:
    cur.execute(
        """
        SELECT
          l.id,
          l.brand_name,
          l.price,
          l.model_no,
          c.buyma_paths,
          c.buyma_name
        FROM buyma_listings l
        JOIN source_offerings o ON o.id = l.winner_offering_id
        LEFT JOIN buyma_master_categories_data c
          ON c.buyma_category_id = l.category_id
        WHERE l.is_published = 0
          AND l.is_active = 1
          AND o.margin_amount_krw > 0
        ORDER BY l.id
        """
    )
    return list(cur.fetchall())


def load_tokens(cur, listing_ids: list) -> dict:
    by_id: dict[int, list] = defaultdict(list)
    if not listing_ids:
        return by_id
    # chunk IN list
    step = 5000
    for i in range(0, len(listing_ids), step):
        chunk = listing_ids[i : i + step]
        ph = ",".join(["%s"] * len(chunk))
        cur.execute(
            f"SELECT listing_id, code_norm FROM listing_model_tokens "
            f"WHERE listing_id IN ({ph})",
            chunk,
        )
        for r in cur.fetchall():
            by_id[int(r["listing_id"])].append(r["code_norm"])
    return by_id


def pick_l1(tokens: list, brand_key: str, mn_cells: dict):
    """
    L1 토큰 선택:
      1) 시장 셀에 매칭되는 토큰만
      2) 강한 토큰(len≥6)이 하나라도 있으면 약한 후보는 버림
      3) 강한 매칭이 전혀 없으면 L1 포기 (짧은 옵션코드만으로 붙이지 않음)
      4) 남은 후보 중 n_act DESC → ord90 DESC → mn_norm ASC
    """
    if not tokens or not brand_key:
        return None
    cands = []
    for tok in tokens:
        hit = mn_cells.get((tok, brand_key))
        if hit is None:
            continue
        n_act, ord90, max_fav = hit
        cands.append((n_act, ord90, tok, max_fav))
    if not cands:
        return None
    strong = [c for c in cands if is_strong_mn(c[2])]
    if not strong:
        return None
    strong.sort(key=lambda x: (-x[0], -x[1], x[2]))
    n_act, ord90, tok, max_fav = strong[0]
    return tok, n_act, ord90, max_fav


def score_one(row, tokens: list, demand: dict, mn_cells: dict):
    bk = brand_key_from_listing(row.get("brand_name"))
    ck = listing_cat_key(row.get("buyma_paths"), row.get("buyma_name"))
    band = price_band(row.get("price"))

    l6_key = cell_key_l6()
    l6 = demand.get(("L6", l6_key))
    d_l6 = l6[0] if l6 else L6_DENSITY

    d_l4 = None
    fav_l4 = None
    k4 = cell_key_l4(ck, band) if ck else None
    if k4:
        hit = demand.get(("L4", k4))
        if hit:
            d_l4, fav_l4 = hit

    d_l3 = None
    fav_l3 = None
    k3 = cell_key_l3(bk, ck, band) if (bk and ck) else None
    if k3:
        hit = demand.get(("L3", k3))
        if hit:
            d_l3, fav_l3 = hit

    parent_d = d_l3 if d_l3 is not None else (d_l4 if d_l4 is not None else d_l6)
    cell_avg_fav = fav_l3 if fav_l3 is not None else fav_l4

    l1 = pick_l1(tokens, bk, mn_cells)
    if l1 is not None:
        tok, n_act, ord90, max_fav = l1
        d = shrink_density(ord90, n_act, parent_d)
        if cell_avg_fav and cell_avg_fav > 0:
            adj = clip_adj(max_fav / cell_avg_fav)
        else:
            adj = 1.0
        level = "L1"
        cell_key = f"{tok}|{bk}"
    elif d_l3 is not None:
        d, adj, level, cell_key = d_l3, 1.0, "L3", k3
    elif d_l4 is not None:
        d, adj, level, cell_key = d_l4, 1.0, "L4", k4
    else:
        d, adj, level, cell_key = d_l6, 1.0, "L6", l6_key

    expected = KAPPA * d * adj / float(WINDOW_DAYS)
    return expected, level, cell_key, adj, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    conn = connect(read_timeout=3600, write_timeout=3600, autocommit=True)
    cur = conn.cursor()
    t0 = time.time()

    print("[load] demand cells ...")
    demand = load_demand(cur)
    print(f"[load] demand={len(demand):,}")

    print("[load] mn cells ...")
    mn_cells = load_mn(cur)
    print(f"[load] mn={len(mn_cells):,}")

    print("[load] draft targets ...")
    targets = load_targets(cur)
    print(f"[load] targets={len(targets):,}")

    ids = [int(r["id"]) for r in targets]
    print("[load] listing tokens ...")
    tokens_by_id = load_tokens(cur, ids)
    print(f"[load] listings_with_tokens={len(tokens_by_id):,}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_out = []
    levels = Counter()
    tok_fallback = 0
    sample = []

    for i, r in enumerate(targets, 1):
        lid = int(r["id"])
        toks = tokens_by_id.get(lid)
        if not toks:
            toks = tokenize_model_no(r.get("model_no"))
            if toks:
                tok_fallback += 1

        expected, level, cell_key, adj, d = score_one(r, toks or [], demand, mn_cells)
        levels[level] += 1
        rows_out.append(
            (lid, expected, level, cell_key, adj, KAPPA, now)
        )
        if len(sample) < 5:
            sample.append(
                {
                    "id": lid,
                    "level": level,
                    "D": round(d, 6),
                    "adj": adj,
                    "expected": expected,
                    "cell": cell_key,
                }
            )
        if i % 10000 == 0:
            print(f"  scored={i:,}/{len(targets):,}", flush=True)

    print(f"[score] done levels={dict(levels)} tok_fallback={tok_fallback}")
    for s in sample:
        print("  sample", s)

    if args.execute:
        print("[write] TRUNCATE score_index_draft ...")
        cur.execute("TRUNCATE TABLE score_index_draft")
        for i in range(0, len(rows_out), CHUNK_WRITE):
            cur.executemany(
                """
                INSERT INTO score_index_draft
                  (listing_id, expected_sales_daily, d_level, d_cell_key,
                   d_adj, kappa, calculated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                rows_out[i : i + CHUNK_WRITE],
            )
            if (i // CHUNK_WRITE) % 10 == 0:
                print(f"  wrote={min(i+CHUNK_WRITE, len(rows_out)):,}", flush=True)
        cur.execute("SELECT COUNT(*) n FROM score_index_draft")
        print(f"[write] OK n={cur.fetchone()['n']:,}")
    else:
        print("[dry-run] no write")

    conn.close()
    print(f"done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
