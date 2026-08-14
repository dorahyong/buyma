# -*- coding: utf-8 -*-
"""
2번 — market_demand_cells (L3/L4/L6) + market_mn_cells 일배치.

선행: 1번 market_item_codes(field) 백필 완료

  python scoring/build_market_cells.py            # dry-run
  python scoring/build_market_cells.py --execute
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from draft_common import (
    K_SHRINK,
    L6_DENSITY,
    WINDOW_DAYS,
    brand_key_from_market,
    cell_key_l3,
    cell_key_l4,
    cell_key_l6,
    connect,
    market_cat_key,
    price_band,
    shrink_density,
)

CHUNK = 20000


def load_ord90(cur) -> dict:
    since = (datetime.now().date() - timedelta(days=WINDOW_DAYS)).strftime("%Y/%m/%d")
    print(f"[orders] sale_date >= {since}")
    t0 = time.time()
    cur.execute(
        """
        SELECT o.item_id, COUNT(*) AS cnt
        FROM market_orders o
        INNER JOIN market_items m ON m.item_id = o.item_id
        WHERE o.sale_date >= %s
        GROUP BY o.item_id
        """,
        (since,),
    )
    out = {r["item_id"]: int(r["cnt"]) for r in cur.fetchall()}
    print(
        f"[orders] items={len(out):,} ord_sum={sum(out.values()):,} "
        f"in {time.time()-t0:.1f}s"
    )
    return out


def load_codes_by_item(cur) -> dict:
    print("[codes] DISTINCT item_id, code_norm ...")
    t0 = time.time()
    cur.execute("SELECT DISTINCT item_id, code_norm FROM market_item_codes")
    by_item: dict[str, set] = defaultdict(set)
    n = 0
    for r in cur.fetchall():
        by_item[r["item_id"]].add(r["code_norm"])
        n += 1
    print(f"[codes] pairs={n:,} items={len(by_item):,} in {time.time()-t0:.1f}s")
    return by_item


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    conn = connect(read_timeout=7200, write_timeout=7200, autocommit=True)
    cur = conn.cursor()
    t0 = time.time()

    ord90_by_item = load_ord90(cur)
    codes_by_item = load_codes_by_item(cur)

    l3 = defaultdict(lambda: [0, 0, 0.0])
    l4 = defaultdict(lambda: [0, 0, 0.0])
    l6 = [0, 0, 0.0]
    mn = defaultdict(lambda: [0, 0, 0])

    print("[items] scanning market_items ...")
    last_id = ""
    scanned = 0
    while True:
        cur.execute(
            """
            SELECT item_id, brand, category_path, current_price, status, fav_count
            FROM market_items
            WHERE item_id > %s
            ORDER BY item_id
            LIMIT %s
            """,
            (last_id, CHUNK),
        )
        rows = cur.fetchall()
        if not rows:
            break

        for r in rows:
            last_id = r["item_id"]
            scanned += 1
            bk = brand_key_from_market(r.get("brand"))
            ck = market_cat_key(r.get("category_path"))
            band = price_band(r.get("current_price"))
            active = (r.get("status") or "").upper() == "ACTIVE"
            fav = int(r.get("fav_count") or 0)
            o90 = ord90_by_item.get(r["item_id"], 0)

            l6[1] += o90
            if active:
                l6[0] += 1
                l6[2] += fav

            if ck:
                k4 = cell_key_l4(ck, band)
                l4[k4][1] += o90
                if active:
                    l4[k4][0] += 1
                    l4[k4][2] += fav

            if bk and ck:
                k3 = cell_key_l3(bk, ck, band)
                l3[k3][1] += o90
                if active:
                    l3[k3][0] += 1
                    l3[k3][2] += fav

            if bk:
                for code in codes_by_item.get(r["item_id"], ()):
                    key = (code, bk)
                    mn[key][1] += o90
                    if active:
                        mn[key][0] += 1
                        if fav > mn[key][2]:
                            mn[key][2] = fav

        if scanned % 200000 == 0:
            print(
                f"  scanned={scanned:,} l3={len(l3):,} l4={len(l4):,} mn={len(mn):,}",
                flush=True,
            )

    print(
        f"[items] scanned={scanned:,} l3={len(l3):,} l4={len(l4):,} mn={len(mn):,} "
        f"l6_n_act={l6[0]:,} l6_ord={l6[1]:,}"
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    demand_rows = []

    demand_rows.append(
        (
            "L6",
            cell_key_l6(),
            l6[0],
            l6[1],
            L6_DENSITY,
            (l6[2] / l6[0]) if l6[0] else None,
            now,
        )
    )

    l4_density = {}
    for k, (n_act, ord90, fav_sum) in l4.items():
        d = shrink_density(ord90, n_act, L6_DENSITY, K_SHRINK)
        l4_density[k] = d
        demand_rows.append(
            ("L4", k, n_act, ord90, d, (fav_sum / n_act) if n_act else None, now)
        )

    for k, (n_act, ord90, fav_sum) in l3.items():
        parts = k.split("|", 2)
        parent_key = (
            cell_key_l4(parts[1], parts[2]) if len(parts) == 3 else cell_key_l6()
        )
        parent_d = l4_density.get(parent_key, L6_DENSITY)
        d = shrink_density(ord90, n_act, parent_d, K_SHRINK)
        demand_rows.append(
            ("L3", k, n_act, ord90, d, (fav_sum / n_act) if n_act else None, now)
        )

    mn_rows = []
    for (code, bk), (n_act, ord90, max_fav) in mn.items():
        if n_act <= 0 and ord90 <= 0:
            continue
        mn_rows.append((code, bk, n_act, ord90, max_fav, now))

    print(f"[write] demand={len(demand_rows):,} mn={len(mn_rows):,} execute={args.execute}")
    print(
        f"  L6 n_act={l6[0]:,} ord90={l6[1]:,} "
        f"raw={l6[1]/l6[0] if l6[0] else 0:.6f} stored={L6_DENSITY}"
    )

    if args.execute:
        cur.execute("TRUNCATE TABLE market_demand_cells")
        cur.execute("TRUNCATE TABLE market_mn_cells")
        for i in range(0, len(demand_rows), 2000):
            cur.executemany(
                """
                INSERT INTO market_demand_cells
                  (level, cell_key, n_act, ord90, density, avg_fav, refreshed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                demand_rows[i : i + 2000],
            )
        for i in range(0, len(mn_rows), 2000):
            cur.executemany(
                """
                INSERT INTO market_mn_cells
                  (mn_norm, brand_key, n_act, ord90, max_fav, refreshed_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                mn_rows[i : i + 2000],
            )
        print("[write] OK")
    else:
        print(" sample L6:", demand_rows[0])
        print(" sample L4:", next((r for r in demand_rows if r[0] == "L4"), None))
        print(" sample L3:", next((r for r in demand_rows if r[0] == "L3"), None))
        print(" sample mn:", mn_rows[0] if mn_rows else None)

    conn.close()
    print(f"done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
