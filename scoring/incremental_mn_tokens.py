# -*- coding: utf-8 -*-
"""
품번 토큰 증분 반영 (빠진 것만).

  - 우리: model_no 있는데 listing_model_tokens 없는 리스팅
  - 시장: brand_model_number 있는데 market_item_codes(field) 없는 아이템
           (+ mn_norm 캐시 갱신)

  python scoring/incremental_mn_tokens.py           # dry-run
  python scoring/incremental_mn_tokens.py --execute
"""
from __future__ import annotations

import argparse
import io
import sys
import time

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from draft_common import connect, representative_mn, tokenize_model_no

CHUNK = 2000


def incr_listings(cur, execute: bool) -> tuple[int, int]:
    print("[listings] finding gaps ...")
    cur.execute(
        """
        SELECT l.id, l.model_no
        FROM buyma_listings l
        WHERE l.model_no IS NOT NULL
          AND TRIM(l.model_no) <> ''
          AND NOT EXISTS (
            SELECT 1 FROM listing_model_tokens t WHERE t.listing_id = l.id
          )
        """
    )
    rows = cur.fetchall()
    print(f"[listings] gap={len(rows):,}")
    batch = []
    tok_n = 0
    for r in rows:
        for tok in tokenize_model_no(r.get("model_no")):
            batch.append((int(r["id"]), tok))
            tok_n += 1
    if execute and batch:
        for i in range(0, len(batch), 2000):
            cur.executemany(
                """
                INSERT INTO listing_model_tokens (listing_id, code_norm)
                VALUES (%s,%s)
                ON DUPLICATE KEY UPDATE code_norm=VALUES(code_norm)
                """,
                batch[i : i + 2000],
            )
    print(f"[listings] tokens_to_write={tok_n:,} execute={execute}")
    return len(rows), tok_n


def incr_market(cur, execute: bool) -> tuple[int, int, int]:
    """brand_model_number 있는 행을 훑되, 이미 field 코드 있는 item은 건너뜀."""
    print("[market] scanning items with brand_model_number ...")
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM market_items
        WHERE brand_model_number IS NOT NULL
          AND brand_model_number <> ''
        """
    )
    total = int(cur.fetchone()["n"])
    print(f"[market] with_model_no={total:,}")

    last_id = ""
    scanned = 0
    gap_items = 0
    codes_n = 0
    mn_n = 0
    t0 = time.time()

    while True:
        cur.execute(
            """
            SELECT item_id, brand_model_number, mn_norm
            FROM market_items
            WHERE item_id > %s
              AND brand_model_number IS NOT NULL
              AND brand_model_number <> ''
            ORDER BY item_id
            LIMIT %s
            """,
            (last_id, CHUNK),
        )
        rows = cur.fetchall()
        if not rows:
            break

        ids = [r["item_id"] for r in rows]
        last_id = ids[-1]
        scanned += len(rows)

        ph = ",".join(["%s"] * len(ids))
        cur.execute(
            f"""
            SELECT DISTINCT item_id FROM market_item_codes
            WHERE src='field' AND item_id IN ({ph})
            """,
            ids,
        )
        has_code = {r["item_id"] for r in cur.fetchall()}

        code_rows = []
        mn_updates = []
        for r in rows:
            if r["item_id"] in has_code:
                continue
            tokens = tokenize_model_no(r.get("brand_model_number"))
            rep = representative_mn(tokens)
            if tokens:
                gap_items += 1
                for tok in tokens:
                    code_rows.append((r["item_id"], tok, "field", 100))
                if rep != r.get("mn_norm"):
                    mn_updates.append((rep, r["item_id"]))
            elif r.get("mn_norm"):
                mn_updates.append((None, r["item_id"]))

        if execute:
            if code_rows:
                cur.executemany(
                    """
                    INSERT INTO market_item_codes (item_id, code_norm, src, confidence)
                    VALUES (%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE confidence=VALUES(confidence)
                    """,
                    code_rows,
                )
            if mn_updates:
                cur.executemany(
                    "UPDATE market_items SET mn_norm=%s WHERE item_id=%s",
                    mn_updates,
                )

        codes_n += len(code_rows)
        mn_n += len(mn_updates)
        # 자주 찍어서 "멈춘 것처럼" 안 보이게
        if scanned % 20000 == 0 or scanned >= total:
            print(
                f"  {scanned:,}/{total:,} ({100*scanned/max(total,1):.1f}%) "
                f"gap_items={gap_items:,} codes={codes_n:,} {time.time()-t0:.0f}s",
                flush=True,
            )

    print(
        f"[market] gap_items={gap_items:,} codes={codes_n:,} "
        f"mn_updates={mn_n:,} execute={execute} in {time.time()-t0:.1f}s"
    )
    return gap_items, codes_n, mn_n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--listings-only", action="store_true")
    ap.add_argument("--market-only", action="store_true")
    args = ap.parse_args()

    conn = connect(read_timeout=3600, write_timeout=3600, autocommit=True)
    cur = conn.cursor()
    t0 = time.time()

    do_l = not args.market_only
    do_m = not args.listings_only
    if do_l:
        incr_listings(cur, args.execute)
    if do_m:
        incr_market(cur, args.execute)

    conn.close()
    print(f"done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
