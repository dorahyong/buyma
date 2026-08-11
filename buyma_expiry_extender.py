# -*- coding: utf-8 -*-
"""
출품기한(available_until) 만료 임박 상품을 today+90으로 연장 (일배치).

배경:
  - BUYMA 購入期限(available_until) 90일이 지나면 출품이 자동 종료됨.
  - 재고/가격 변동이 없는 상품은 stock sync가 edit를 안 보내 연장이 안 되므로,
    만료 임박분을 골라 강제로 today+90을 재푸시해 자동 종료를 막는다.

동작 (단일권위):
  1. buyma_listings 출품중(is_published=1, buyma_product_id 있음, exception_reason 없음) 중
     만료 임박(available_until <= today+GUARD) 조회
     · listings.available_until 이 비어 있으면 winner ace 의 값을 임시로 씀(이관 과도기)
  2. listing 기준 full-edit(reconcile_buyma_push.execute_edit_safe)
     → build_request_json 이 available_until=today+90 을 넣음 (가격·옵션도 현재 listing 값 유지)
  3. 전체 품절 등으로 edit 불가 → SKIP (삭/출품정지는 stock/reconcile 몫 — 이 배치는 연장만)
  4. API 성공 → buyma_listings.available_until write-back

사용법:
    python buyma_expiry_extender.py --count                # 대상 건수만
    python buyma_expiry_extender.py --dry-run              # 조회+판정만, API/DB 변경 없음
    python buyma_expiry_extender.py                        # 실제 연장
    python buyma_expiry_extender.py --limit 500            # 최대 500건 (점진 실행)
    python buyma_expiry_extender.py --guard-days 10        # 임박 기준 (기본 10일)
    python buyma_expiry_extender.py --id 123               # listing id 1건
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, 'okmall'))

import reconcile_buyma_push as push  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

API_CALL_DELAY = 0.3  # BUYMA API 호출 간 대기(초) — 구버전과 동일

# listings 우선, 비어 있으면 winner ace (이관 과도기). 날짜만 빌리는 것.
DUE_UNTIL = "COALESCE(bl.available_until, a.available_until)"


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def fetch_targets(conn, guard_days: int, limit: Optional[int] = None,
                  listing_id: Optional[int] = None) -> List[Dict]:
    """만료 임박(due_until <= today+guard) 인 출품중 listing."""
    sql = f"""
        SELECT bl.id, bl.buyma_product_id, bl.model_no, bl.brand_name, bl.brand_id,
               bl.price, bl.group_key, bl.reference_number, bl.locked_reference_number,
               bl.available_until AS listing_until,
               a.available_until AS ace_until,
               {DUE_UNTIL} AS due_until,
               a.id AS winner_ace_id
        FROM buyma_listings bl
        LEFT JOIN source_offerings so
               ON so.id = bl.winner_offering_id AND so.is_active = 1
        LEFT JOIN ace_products a ON a.id = so.ace_product_id
        WHERE bl.is_active = 1
          AND bl.is_published = 1
          AND bl.buyma_product_id IS NOT NULL
          AND bl.exception_reason IS NULL
          AND {DUE_UNTIL} IS NOT NULL
          AND {DUE_UNTIL} <= DATE_ADD(CURDATE(), INTERVAL %s DAY)
    """
    params: list = [guard_days]
    if listing_id is not None:
        sql += " AND bl.id = %s"
        params.append(listing_id)
    sql += f" ORDER BY {DUE_UNTIL} ASC, bl.id ASC"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def count_targets(conn, guard_days: int) -> int:
    sql = f"""
        SELECT COUNT(*) AS n
        FROM buyma_listings bl
        LEFT JOIN source_offerings so
               ON so.id = bl.winner_offering_id AND so.is_active = 1
        LEFT JOIN ace_products a ON a.id = so.ace_product_id
        WHERE bl.is_active = 1
          AND bl.is_published = 1
          AND bl.buyma_product_id IS NOT NULL
          AND bl.exception_reason IS NULL
          AND {DUE_UNTIL} IS NOT NULL
          AND {DUE_UNTIL} <= DATE_ADD(CURDATE(), INTERVAL %s DAY)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (guard_days,))
        return int(cur.fetchone()['n'])


def write_back_until(conn, listing_id: int, until_str: str) -> None:
    """API에 보낸 available_until(YYYY/MM/DD) → listings 날짜 칸."""
    date_val = until_str.replace('/', '-')
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE buyma_listings SET available_until=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (date_val, listing_id),
        )
    conn.commit()


def extend_one(conn, row: Dict, dry_run: bool) -> str:
    """한 listing 연장. 반환: extended|skipped|failed."""
    listing_id = row['id']
    pid = row['buyma_product_id']
    due = row['due_until']

    listing = push.fetch_listing(conn, listing_id)
    if not listing:
        log(f"listing#{listing_id} pid={pid} → listing 없음", "WARN")
        return 'failed'

    # 연장만 — process_one_group 은 마진X 시 retire 하므로 쓰지 않는다.
    # 옛 버전: control=delete 면 skip. 여기선 edit 불가(전체품절·이미지0)면 skip.
    result = push.execute_edit_safe(conn, listing, dry_run=dry_run)

    if result.get('skipped'):
        log(f"listing#{listing_id} pid={pid} due={due} → skip ({result.get('reason')})")
        return 'skipped'

    if dry_run:
        req = result.get('request') or {}
        new_until = (req.get('product') or {}).get('available_until')
        log(f"[DRY-RUN] listing#{listing_id} pid={pid} {due} → {new_until}")
        return 'extended'

    resp = result.get('response') or {}
    req = result.get('request') or {}
    new_until = (req.get('product') or {}).get('available_until')
    if resp.get('success') and new_until:
        write_back_until(conn, listing_id, new_until)
        log(f"listing#{listing_id} pid={pid} → 연장 {new_until} OK")
        return 'extended'

    err = str(resp.get('error') or result)[:160]
    log(f"listing#{listing_id} pid={pid} → API 실패: {err}", "ERROR")
    return 'failed'


def main():
    parser = argparse.ArgumentParser(
        description='available_until 만료 임박분을 today+90으로 연장 (listings 단일권위)'
    )
    parser.add_argument('--guard-days', type=int, default=10,
                        help='임박 기준 일수 (기본 10): due_until <= today+N 이면 연장')
    parser.add_argument('--limit', type=int, default=None,
                        help='최대 처리 건수 (점진 실행용)')
    parser.add_argument('--dry-run', action='store_true',
                        help='조회+판정만, API/DB 변경 없음')
    parser.add_argument('--count', action='store_true',
                        help='대상 건수만 출력')
    parser.add_argument('--id', type=int, default=None,
                        help='특정 listing id 1건만')
    args = parser.parse_args()

    conn = push.get_connection()
    try:
        log("=" * 60)
        log(f"만료 임박 연장 시작 (guard_days={args.guard_days}, limit={args.limit}, "
            f"dry_run={args.dry_run}, id={args.id})")
        log("=" * 60)

        if args.count and args.id is None:
            n = count_targets(conn, args.guard_days)
            log(f"대상: {n}건 (due_until <= today+{args.guard_days})")
            return

        targets = fetch_targets(conn, args.guard_days, args.limit, args.id)
        total = len(targets)
        log(f"대상: {total}건 (due_until <= today+{args.guard_days})")
        if not targets:
            log("대상 없음. 종료.")
            return

        stats = {'extended': 0, 'skipped': 0, 'failed': 0}
        for i, row in enumerate(targets, 1):
            try:
                status = extend_one(conn, row, dry_run=args.dry_run)
                stats[status] = stats.get(status, 0) + 1
                if status == 'extended' and not args.dry_run:
                    time.sleep(API_CALL_DELAY)
            except Exception as e:
                stats['failed'] += 1
                log(f"[{i}/{total}] listing#{row['id']} → 오류: {e}", "ERROR")

        log("=" * 60)
        log(f"완료: 연장 {stats['extended']}, skip {stats['skipped']}, 실패 {stats['failed']}")
        log(f"(참고) today+90 ≈ {(datetime.now() + timedelta(days=90)).strftime('%Y-%m-%d')}")
        log("=" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
