# -*- coding: utf-8 -*-
"""
mn_norm / market_item_codes(field) 백필.

market: brand_model_number 있는 행만 → market_item_codes + market_items.mn_norm
listings: model_no 토큰 → listing_model_tokens (buyma_listings에 mn_norm 없음)

  python scoring/backfill_mn_norm.py --all --execute
  python scoring/backfill_mn_norm.py --market --execute
  python scoring/backfill_mn_norm.py --listings --execute
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from draft_common import connect, representative_mn, tokenize_model_no
# representative_mn: market_items.mn_norm 캐시용

CHUNK = 10000


def backfill_market(conn, execute: bool, limit: int | None = None):
    """품번 필드(brand_model_number)가 있는 행만 토큰화·적재. 빈 품번은 건드리지 않음."""
    cur = conn.cursor()
    # TRIM은 인덱스를 못 타므로 <> '' 만 사용. 공백만 있는 값은 tokenize가 걸러냄.
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM market_items
        WHERE brand_model_number IS NOT NULL
          AND brand_model_number <> ''
        """
    )
    total = int(cur.fetchone()["n"])
    print(f"[market] with_model_no={total:,} execute={execute} (empty skipped)")

    last_id = ""
    done = mn_n = codes_n = 0
    t0 = time.time()

    while True:
        cur.execute(
            """
            SELECT item_id, brand_model_number
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

        mn_updates = []
        code_rows = []
        for r in rows:
            last_id = r["item_id"]
            tokens = tokenize_model_no(r.get("brand_model_number"))
            # 유효 토큰이 없어도 필드는 있었으므로 대표는 NULL로 명시
            mn_updates.append((representative_mn(tokens), r["item_id"]))
            for tok in tokens:
                code_rows.append((r["item_id"], tok, "field", 100))

        if execute:
            if mn_updates:
                cur.executemany(
                    "UPDATE market_items SET mn_norm=%s WHERE item_id=%s",
                    mn_updates,
                )
            if code_rows:
                cur.executemany(
                    """
                    INSERT INTO market_item_codes (item_id, code_norm, src, confidence)
                    VALUES (%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE confidence=VALUES(confidence)
                    """,
                    code_rows,
                )

        done += len(rows)
        mn_n += sum(1 for v, _ in mn_updates if v)
        codes_n += len(code_rows)
        rate = done / (time.time() - t0) if time.time() > t0 else 0
        print(
            f"  {done:,}/{total:,} ({100*done/max(total,1):.1f}%) "
            f"mn={mn_n:,} codes={codes_n:,} {rate:,.0f}/s",
            flush=True,
        )
        if limit and done >= limit:
            break
        if not execute and done >= CHUNK:
            print("  dry-run: first chunk only")
            break

    print(f"[market] done {time.time()-t0:.1f}s mn={mn_n:,} codes={codes_n:,}")


def backfill_listings(conn, execute: bool, limit: int | None = None):
    """buyma_listings.model_no → listing_model_tokens (1:N). 코어 테이블 컬럼 없음."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM buyma_listings")
    total = int(cur.fetchone()["n"])
    print(f"[listings→tokens] rows={total:,} execute={execute}")

    last_id = 0
    done = tok_n = 0
    t0 = time.time()

    while True:
        cur.execute(
            """
            SELECT id, model_no FROM buyma_listings
            WHERE id > %s ORDER BY id LIMIT %s
            """,
            (last_id, CHUNK),
        )
        rows = cur.fetchall()
        if not rows:
            break

        batch = []
        for r in rows:
            last_id = int(r["id"])
            for tok in tokenize_model_no(r.get("model_no")):
                batch.append((last_id, tok))

        if execute and batch:
            cur.executemany(
                """
                INSERT INTO listing_model_tokens (listing_id, code_norm)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE code_norm=VALUES(code_norm)
                """,
                batch,
            )

        done += len(rows)
        tok_n += len(batch)
        rate = done / (time.time() - t0) if time.time() > t0 else 0
        print(
            f"  {done:,}/{total:,} ({100*done/max(total,1):.1f}%) "
            f"tokens={tok_n:,} {rate:,.0f}/s",
            flush=True,
        )
        if limit and done >= limit:
            break
        if not execute and done >= CHUNK:
            print("  dry-run: first chunk only")
            break

    print(f"[listings→tokens] done {time.time()-t0:.1f}s tokens={tok_n:,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--market", action="store_true")
    ap.add_argument("--listings", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    do_m = args.market or args.all
    do_l = args.listings or args.all
    if not do_m and not do_l:
        ap.error("use --market / --listings / --all")

    print(f"start {datetime.now():%Y-%m-%d %H:%M:%S}")
    conn = connect(read_timeout=3600, write_timeout=3600, autocommit=True)
    if do_m:
        backfill_market(conn, args.execute, args.limit)
    if do_l:
        backfill_listings(conn, args.execute, args.limit)
    conn.close()
    print(f"end {datetime.now():%Y-%m-%d %H:%M:%S}")


if __name__ == "__main__":
    main()
