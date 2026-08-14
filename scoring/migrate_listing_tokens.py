# -*- coding: utf-8 -*-
"""
buyma_listings.mn_norm → listing_model_tokens 로 이전.

  1) listing_model_tokens CREATE
  2) model_no 토큰 전량 적재 (1:N)
  3) buyma_listings.mn_norm 인덱스·컬럼 DROP

  python scoring/migrate_listing_tokens.py           # dry-run
  python scoring/migrate_listing_tokens.py --execute
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from draft_common import connect, tokenize_model_no

CHUNK = 10000

CREATE_SQL = """
CREATE TABLE listing_model_tokens (
  listing_id BIGINT NOT NULL,
  code_norm  VARCHAR(64) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (listing_id, code_norm),
  KEY idx_code (code_norm)
) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
"""


def table_exists(cur, name: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) n FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
        """,
        (name,),
    )
    return int(cur.fetchone()["n"]) > 0


def column_exists(cur, table: str, col: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) n FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
        """,
        (table, col),
    )
    return int(cur.fetchone()["n"]) > 0


def index_exists(cur, table: str, idx: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) n FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
        """,
        (table, idx),
    )
    return int(cur.fetchone()["n"]) > 0


def backfill_tokens(cur, execute: bool):
    cur.execute("SELECT COUNT(*) AS n FROM buyma_listings")
    total = int(cur.fetchone()["n"])
    print(f"[tokens] listings={total:,} execute={execute}")

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
        if not execute and done >= CHUNK:
            print("  dry-run: first chunk only")
            break

    print(f"[tokens] done {time.time()-t0:.1f}s tokens={tok_n:,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--skip-drop", action="store_true", help="사이드 테이블만, listings 컬럼 유지")
    args = ap.parse_args()

    print(f"start {datetime.now():%Y-%m-%d %H:%M:%S} execute={args.execute}")
    conn = connect(read_timeout=3600, write_timeout=3600, autocommit=True)
    cur = conn.cursor()

    # 0) busy check
    cur.execute(
        """
        SELECT ID, TIME, STATE, LEFT(IFNULL(INFO,''),80) INFO
        FROM information_schema.PROCESSLIST
        WHERE COMMAND != 'Sleep' AND TIME > 5
          AND IFNULL(INFO,'') LIKE %s
        """,
        ("%buyma_listings%",),
    )
    busy = cur.fetchall()
    if busy:
        print("WARN buyma_listings busy queries:")
        for b in busy:
            print(" ", b)
        if args.execute and not args.skip_drop:
            print("ABORT: drop unsafe while listings queries run. re-run later or --skip-drop")
            conn.close()
            return

    # 1) create
    if table_exists(cur, "listing_model_tokens"):
        print("[EXISTS] listing_model_tokens")
    else:
        print("[MISSING] listing_model_tokens")
        if args.execute:
            cur.execute(CREATE_SQL)
            print("  created")

    # 2) backfill
    if args.execute and table_exists(cur, "listing_model_tokens"):
        # 재실행 안전: 비우고 다시 (15만 규모)
        cur.execute("SELECT COUNT(*) n FROM listing_model_tokens")
        n = int(cur.fetchone()["n"])
        if n > 0:
            print(f"[tokens] truncate existing {n:,} rows")
            cur.execute("TRUNCATE TABLE listing_model_tokens")
        backfill_tokens(cur, True)
    else:
        backfill_tokens(cur, False)

    # 3) drop listings.mn_norm
    has_col = column_exists(cur, "buyma_listings", "mn_norm")
    has_idx = index_exists(cur, "buyma_listings", "idx_mn_norm")
    print(f"[listings.mn_norm] column={'yes' if has_col else 'no'} idx={'yes' if has_idx else 'no'}")

    if args.execute and not args.skip_drop:
        if has_idx:
            print("  DROP INDEX idx_mn_norm ...")
            cur.execute("ALTER TABLE buyma_listings DROP INDEX idx_mn_norm")
            print("  OK")
        if has_col:
            print("  DROP COLUMN mn_norm ...")
            cur.execute("ALTER TABLE buyma_listings DROP COLUMN mn_norm, ALGORITHM=INSTANT")
            print("  OK")
    elif args.skip_drop:
        print("[SKIP] drop listings.mn_norm")

    conn.close()
    print(f"end {datetime.now():%Y-%m-%d %H:%M:%S}")


if __name__ == "__main__":
    main()
