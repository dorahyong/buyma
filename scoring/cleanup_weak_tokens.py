# -*- coding: utf-8 -*-
"""
약한 토큰 1회 정리:
  - listing_model_tokens / market_item_codes 에서 새 규칙 탈락 코드 DELETE
  - market_items.mn_norm 이 탈락 코드면 남은 코드로 재지정(없으면 NULL)

  python scoring/cleanup_weak_tokens.py           # dry-run
  python scoring/cleanup_weak_tokens.py --execute
"""
from __future__ import annotations

import argparse
import io
import sys
import time

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from draft_common import connect, normalize_mn_token, representative_mn


def is_rejected_stored(code: str) -> bool:
    """이미 저장된 code_norm이 새 규칙에 걸리는지.
    code는 이미 정규화된 형태이므로 normalize_mn_token(code)가 None이면 탈락.
    """
    return normalize_mn_token(code) is None


def collect_bad(cur, table: str, col: str = "code_norm") -> list[str]:
    cur.execute(f"SELECT DISTINCT {col} AS c FROM {table}")
    bad = []
    for r in cur.fetchall():
        c = r["c"]
        if c and is_rejected_stored(c):
            bad.append(c)
    return bad


def delete_in_chunks(cur, table: str, col: str, codes: list[str], execute: bool):
    if not codes:
        print(f"[{table}] nothing to delete")
        return 0
    total = 0
    step = 500
    for i in range(0, len(codes), step):
        chunk = codes[i : i + step]
        ph = ",".join(["%s"] * len(chunk))
        sql = f"SELECT COUNT(*) n FROM {table} WHERE {col} IN ({ph})"
        cur.execute(sql, chunk)
        n = int(cur.fetchone()["n"])
        total += n
        if execute and n:
            cur.execute(f"DELETE FROM {table} WHERE {col} IN ({ph})", chunk)
        if (i // step) % 20 == 0:
            print(f"  {table} scanned codes {i+len(chunk):,}/{len(codes):,} rows≈{total:,}", flush=True)
    print(f"[{table}] bad_unique={len(codes):,} rows_deleted≈{total:,} execute={execute}")
    return total


def fix_market_mn_norm(cur, execute: bool):
    """mn_norm이 탈락이거나, codes에 없는 경우 재설정."""
    print("[mn_norm] scanning market_items with mn_norm ...")
    cur.execute(
        """
        SELECT item_id, mn_norm FROM market_items
        WHERE mn_norm IS NOT NULL AND mn_norm <> ''
        """
    )
    rows = cur.fetchall()
    need = [r for r in rows if is_rejected_stored(r["mn_norm"])]
    print(f"[mn_norm] with_mn={len(rows):,} rejected_rep={len(need):,}")

    updates = []
    # also fix rejected ones by looking up remaining codes
    for i in range(0, len(need), 500):
        chunk = need[i : i + 500]
        ids = [r["item_id"] for r in chunk]
        ph = ",".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT item_id, code_norm FROM market_item_codes WHERE item_id IN ({ph})",
            ids,
        )
        by = {}
        for r in cur.fetchall():
            by.setdefault(r["item_id"], []).append(r["code_norm"])
        for r in chunk:
            toks = [t for t in by.get(r["item_id"], []) if normalize_mn_token(t)]
            # codes already stored filtered; just pick rep
            rep = representative_mn(toks)
            updates.append((rep, r["item_id"]))

    if execute and updates:
        cur.executemany(
            "UPDATE market_items SET mn_norm=%s WHERE item_id=%s",
            updates,
        )
    print(f"[mn_norm] updates={len(updates):,} execute={execute}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    conn = connect(read_timeout=3600, write_timeout=3600, autocommit=True)
    cur = conn.cursor()
    t0 = time.time()

    print("[1] listing_model_tokens bad codes ...")
    bad_l = collect_bad(cur, "listing_model_tokens")
    delete_in_chunks(cur, "listing_model_tokens", "code_norm", bad_l, args.execute)

    print("[2] market_item_codes bad codes ...")
    bad_m = collect_bad(cur, "market_item_codes")
    delete_in_chunks(cur, "market_item_codes", "code_norm", bad_m, args.execute)

    print("[3] fix market_items.mn_norm ...")
    fix_market_mn_norm(cur, args.execute)

    conn.close()
    print(f"done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
