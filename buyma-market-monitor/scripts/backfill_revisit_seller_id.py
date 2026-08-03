"""market_revisit_state.seller_id 백필 (운영 MySQL, 청크 실행).

기존 178만 행에 seller_id 가 비어 있으므로 market_items 에서 복사해 채운다.
한 번에 조인하면 recompute 가 죽던 그 조인과 같아 타임아웃(2013)이 나므로,
item_id(PK) 를 키셋 페이지네이션으로 끊어 batch 건씩만 조인해 UPDATE 한다.

- 멀티테이블 UPDATE 는 LIMIT 을 못 쓰므로 WHERE item_id IN (batch) 로 범위를 좁힌다.
- 이미 채워진 행을 다시 SET 해도 무해(멱등). 중간에 끊겨도 --resume 로 이어서 가능.
- 각 배치는 batch 개 PK 조회라 read_timeout 안쪽에서 끝난다(batch=10000 권장).

사용:
  python scripts/backfill_revisit_seller_id.py                # 처음부터
  python scripts/backfill_revisit_seller_id.py --batch 5000   # 배치 크기 조정
  python scripts/backfill_revisit_seller_id.py --resume <item_id>   # 중단 지점부터
  python scripts/backfill_revisit_seller_id.py --check        # 남은 NULL 개수만 확인
"""
import argparse
import os
import sys
import time
from pathlib import Path

import pymysql
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def connect():
    return pymysql.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"), charset="utf8mb4",
        connect_timeout=10, read_timeout=280, write_timeout=280,
        autocommit=False,
    )


def null_count(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM market_revisit_state WHERE seller_id IS NULL")
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=10000)
    ap.add_argument("--resume", default="")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    conn = connect()

    if args.check:
        print(f"남은 NULL seller_id 행: {null_count(conn):,}")
        return

    total = None
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM market_revisit_state")
    total = cur.fetchone()[0]
    print(f"revisit_state 총 {total:,} 행 · 시작 NULL {null_count(conn):,} 행 · batch={args.batch:,}")

    last = args.resume  # item_id > last (키셋). '' 이면 처음부터.
    done = 0
    t0 = time.time()
    while True:
        cur = conn.cursor()
        cur.execute(
            "SELECT item_id FROM market_revisit_state WHERE item_id > %s "
            "ORDER BY item_id LIMIT %s",
            (last, args.batch),
        )
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            break
        placeholders = ",".join(["%s"] * len(ids))
        cur = conn.cursor()
        cur.execute(
            f"UPDATE market_revisit_state r JOIN market_items i ON i.item_id = r.item_id "
            f"SET r.seller_id = i.seller_id WHERE r.item_id IN ({placeholders})",
            ids,
        )
        conn.commit()
        last = ids[-1]
        done += len(ids)
        rate = done / max(time.time() - t0, 1e-6)
        print(f"  {done:,}/{total:,} 처리 · 마지막 item_id={last} · {rate:,.0f} 행/s", flush=True)

    remaining = null_count(conn)
    print(f"완료. 처리 {done:,} 행 · 남은 NULL {remaining:,} 행")
    if remaining:
        print("⚠️ NULL 이 남아 있음 — market_items 에 없는 item_id(고아 행)일 수 있음. "
              "새 코드는 NULL 을 무시하므로 집계엔 안전하나, 원인 확인 권장.")


if __name__ == "__main__":
    main()
