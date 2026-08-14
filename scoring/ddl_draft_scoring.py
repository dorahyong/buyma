# -*- coding: utf-8 -*-
"""
draft scoring 1번 DDL만.

  python scoring/ddl_draft_scoring.py              # 상태 조회
  python scoring/ddl_draft_scoring.py --execute    # 적용
  python scoring/ddl_draft_scoring.py --execute --with-indexes
"""
from __future__ import annotations

import argparse
import io
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

import os
import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, ".env"), override=True)


def connect():
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=30,
        read_timeout=3600,
        write_timeout=3600,
        autocommit=True,
    )


STEPS = [
    (
        "market_item_codes",
        "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_item_codes'",
        """
        CREATE TABLE market_item_codes (
          item_id    VARCHAR(64)  NOT NULL,
          code_norm  VARCHAR(64)  NOT NULL,
          src        ENUM('field','name','desc') NOT NULL,
          confidence TINYINT      NOT NULL DEFAULT 100,
          created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (item_id, code_norm, src),
          KEY idx_code (code_norm),
          KEY idx_code_src (code_norm, src)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
        """,
    ),
    (
        "market_demand_cells",
        "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_demand_cells'",
        """
        CREATE TABLE market_demand_cells (
          level    ENUM('L3','L4','L6') NOT NULL,
          cell_key VARCHAR(255) NOT NULL,
          n_act    INT NOT NULL DEFAULT 0,
          ord90    INT NOT NULL DEFAULT 0,
          density  DECIMAL(12,8) NOT NULL DEFAULT 0,
          avg_fav  DECIMAL(14,4) NULL,
          refreshed_at DATETIME NOT NULL,
          PRIMARY KEY (level, cell_key)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
        """,
    ),
    (
        "market_mn_cells",
        "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_mn_cells'",
        """
        CREATE TABLE market_mn_cells (
          mn_norm   VARCHAR(64) NOT NULL,
          brand_key VARCHAR(120) NOT NULL,
          n_act     INT NOT NULL DEFAULT 0,
          ord90     INT NOT NULL DEFAULT 0,
          max_fav   INT NOT NULL DEFAULT 0,
          refreshed_at DATETIME NOT NULL,
          PRIMARY KEY (mn_norm, brand_key),
          KEY idx_brand (brand_key)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
        """,
    ),
    (
        "score_index_draft",
        "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='score_index_draft'",
        """
        CREATE TABLE score_index_draft (
          listing_id BIGINT NOT NULL,
          expected_sales_daily DECIMAL(18,12) NOT NULL,
          d_level VARCHAR(2) NOT NULL,
          d_cell_key VARCHAR(255) NULL,
          d_adj DECIMAL(6,4) NOT NULL DEFAULT 1.0000,
          kappa DECIMAL(10,6) NOT NULL,
          calculated_at DATETIME NOT NULL,
          PRIMARY KEY (listing_id),
          KEY idx_expected (expected_sales_daily),
          KEY idx_level (d_level)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
        """,
    ),
    (
        "listing_model_tokens",
        "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='listing_model_tokens'",
        """
        CREATE TABLE listing_model_tokens (
          listing_id BIGINT NOT NULL,
          code_norm  VARCHAR(64) NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (listing_id, code_norm),
          KEY idx_code (code_norm)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci
        """,
    ),
    (
        "market_items.mn_norm",
        "SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='market_items' AND COLUMN_NAME='mn_norm'",
        "ALTER TABLE market_items ADD COLUMN mn_norm VARCHAR(64) NULL, ALGORITHM=INSTANT",
    ),
]

INDEXES = [
    ("market_items", "idx_mn_norm", "ALTER TABLE market_items ADD INDEX idx_mn_norm (mn_norm)"),
    ("market_items", "idx_brand", "ALTER TABLE market_items ADD INDEX idx_brand (brand)"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--with-indexes", action="store_true")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()

    print("=== processlist (non-sleep, TIME>2) ===")
    cur.execute(
        """
        SELECT ID, TIME, STATE, LEFT(IFNULL(INFO,''),100) INFO
        FROM information_schema.PROCESSLIST
        WHERE COMMAND != 'Sleep' AND TIME > 2
        ORDER BY TIME DESC
        LIMIT 20
        """
    )
    procs = cur.fetchall()
    if not procs:
        print("(clean)")
    else:
        for p in procs:
            print(p)

    print(f"\nmode={'EXECUTE' if args.execute else 'DRY-RUN'}")
    for label, check, ddl in STEPS:
        cur.execute(check)
        n = int(cur.fetchone()["n"])
        status = "EXISTS" if n else "MISSING"
        print(f"[{status}] {label}")
        if not n and args.execute:
            print(f"  applying...")
            cur.execute(ddl)
            print("  OK")

    if args.with_indexes:
        for table, idx, ddl in INDEXES:
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
                """,
                (table, idx),
            )
            n = int(cur.fetchone()["n"])
            status = "EXISTS" if n else "MISSING"
            print(f"[{status}] {table}.{idx}")
            if not n and args.execute:
                print("  building index (may take a while)...")
                cur.execute(ddl)
                print("  OK")
    else:
        print("[SKIP] indexes (use --with-indexes later)")

    conn.close()
    print("done")


if __name__ == "__main__":
    main()
