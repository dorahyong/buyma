# -*- coding: utf-8 -*-
"""
게시일수 N일 이상 + 조회수 0 인 상품을 바이마에서 '출품정지'시키는 배치

원래 기능(2026-05 판)과 판단·동작이 같다. 바뀐 것은 어디를 보고 무엇으로 내리느냐뿐:
  - 대상 판단  ace_products → buyma_listings (바이마 상품번호 = 정체성 단위)
               소싱처가 늘거나 빠져도 조회수·판매량은 상품번호에 붙어 있어 판단이 흔들리지 않는다.
  - 기간 기준  등록일 → **실제 게시일수**(buyma_listing_days). 출품정지로 내려가 있던 기간은 빼고 센다
               (그 기간엔 조회될 기회 자체가 없었으므로).
  - 내리는 법  삭제 API → **재고 API(전 옵션 품절 + 수량 0) = 출품정지**
               상품번호·게시일수가 보존되고, 8만 슬롯에서는 빠진다(슬롯은 출품중만 카운트).
  - 재출품 차단 ace.exception_reason → buyma_listings.exception_reason
               이게 없으면 다음 재고 동기화가 재고 있다고 보고 도로 출품중으로 되살린다.

대상:
  - buyma_listings.is_published = 1 AND buyma_product_id IS NOT NULL
  - buyma_listings.exception_reason IS NULL   (이미 내린 것 제외)
  - 실제 게시일수 >= N일   (buyma_listing_days: 쌓인 시간 + 지금 올라가 있는 시간)
  - buyma_product_stats.access_count = 0

동작:
  1. 재고 API 로 출품정지 (reconcile_buyma_push.execute_retire)
  2. 접수 성공 → buyma_listings.exception_reason = 'low_view_{N}d'
     (is_published / status 는 안 건드림 — webhook 이 0 / 'soldout' 으로 기록)
  3. 접수 실패 → 로그만 (다음 실행 때 다시 대상)

사용법:
    python buyma_low_view_cleaner.py --count               # 대상 건수만
    python buyma_low_view_cleaner.py --dry-run             # 대상 목록만 출력
    python buyma_low_view_cleaner.py --days 60 --limit 10  # 60일 기준 10건 실제 출품정지
"""

import os
import sys
import argparse
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List

if sys.platform == 'win32':
    # reconfigure 로 바꾼다. TextIOWrapper 로 감싸면 아래에서 import 하는 모듈이
    # 같은 buffer 를 또 감쌀 때 우리 래퍼가 닫혀 'I/O operation on closed file' 이 난다.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'okmall'))

import reconcile_buyma_push as push  # noqa: E402  (DB 연결 + execute_retire 공용)

REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.0

LOG_DIR = ROOT / 'logs'
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"low_view_cleaner_{datetime.now().strftime('%Y%m%d')}.log"

# 실제 게시일수(초) = 지금까지 쌓인 시간 + (지금 올라가 있다면) 그 시작부터 지금까지
LISTED_SECONDS = ("(ld.accumulated_seconds + IF(ld.is_listed=1 AND ld.listed_since IS NOT NULL,"
                  " TIMESTAMPDIFF(SECOND, ld.listed_since, NOW()), 0))")


def log(message: str, level: str = "INFO"):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def fetch_targets(conn, days: int, limit: int = None) -> List[Dict]:
    """게시일수 N일 이상 + 조회수 0 인 출품중 목록. 오래 걸린 것부터."""
    sql = f"""
        SELECT bl.id, bl.buyma_product_id, bl.reference_number, bl.locked_reference_number,
               bl.model_no, bl.brand_name, bl.name, bl.price,
               ROUND({LISTED_SECONDS}/86400, 1) AS listed_days,
               s.access_count, s.favorite_count, s.cart_count, s.sold_count
        FROM buyma_listings bl
        JOIN buyma_listing_days ld ON ld.buyma_product_id = bl.buyma_product_id
        JOIN buyma_product_stats s ON s.buyma_product_id = bl.buyma_product_id
        WHERE bl.is_published = 1
          AND bl.buyma_product_id IS NOT NULL
          AND bl.exception_reason IS NULL
          AND s.access_count = 0
          AND {LISTED_SECONDS} >= %s * 86400
        ORDER BY {LISTED_SECONDS} DESC
    """
    params = [days]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def unmark_failed(conn) -> int:
    """지난 실행에서 표시는 찍혔는데 실제로는 안 내려간 것 → 표시를 풀어 다시 대상으로.

    재고 API 는 201(접수)을 주고도 웹훅에서 거부될 수 있다
    (2026-08-08 실측 10건 중 2건: "販売可否/在庫に必要なすべての選択がありません").
    그 경우 상품은 출품중 그대로인데 표시만 남아, reconcile 이 손대지 않는 미아가 된다.
    → 표시 있는데 아직 출품중이고 status='fail' 이면 표시를 지운다.
    """
    with conn.cursor() as cur:
        cur.execute(r"""
            UPDATE buyma_listings
               SET exception_reason = NULL, updated_at = NOW()
             WHERE exception_reason LIKE 'low\_view\_%'
               AND is_published = 1
               AND status = 'fail'
        """)
        n = cur.rowcount
    conn.commit()
    return n


def mark_excluded(conn, listing_id: int, reason: str) -> bool:
    """출품정지 접수 성공 → 재출품 차단 표시. is_published/status 는 webhook 담당."""
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE buyma_listings SET exception_reason=%s, updated_at=NOW() WHERE id=%s",
                        (reason, listing_id))
        conn.commit()
        return True
    except Exception as e:
        log(f"  → DB 표시 실패: {e}", "ERROR")
        conn.rollback()
        return False


def main():
    ap = argparse.ArgumentParser(description='게시일수 N일 이상 + 조회수 0 상품 출품정지')
    ap.add_argument('--count', action='store_true', help='대상 건수만 확인')
    ap.add_argument('--dry-run', action='store_true', help='대상 목록만 출력 (실행 안함)')
    ap.add_argument('--days', type=int, default=60, help='실제 게시일수 기준 (기본 60)')
    ap.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    args = ap.parse_args()

    reason = f'low_view_{args.days}d'

    log("=" * 60)
    log("low_view_cleaner — 게시일수 N일 이상 + 조회수 0 → 출품정지")
    log(f"  모드: {'건수' if args.count else 'DRY-RUN' if args.dry_run else '실제 출품정지'}")
    log(f"  기준: 실제 게시일수 >= {args.days}일, 조회수 0")
    log(f"  표시: exception_reason = '{reason}'")
    if args.limit:
        log(f"  최대: {args.limit}건")
    log("=" * 60)

    conn = push.get_connection()
    try:
        _un = unmark_failed(conn)
        if _un:
            log(f"지난 실행에서 접수는 됐으나 거부된 {_un}건 → 표시 해제(다시 대상)")

        targets = fetch_targets(conn, days=args.days, limit=args.limit)
        log(f"대상: {len(targets)}건")

        if args.count or not targets:
            if not targets:
                log("대상 없음 — 종료")
            return

        if args.dry_run:
            for i, t in enumerate(targets[:50], 1):
                log(f"  [{i}] listing={t['id']} buyma={t['buyma_product_id']} "
                    f"게시 {t['listed_days']}일 찜{t['favorite_count']} 장바구니{t['cart_count']} "
                    f"판매{t['sold_count']} | {t['brand_name']} {t['model_no']}")
            if len(targets) > 50:
                log(f"  ... 외 {len(targets)-50}건")
            return

        ok = fail = 0
        for i, t in enumerate(targets, 1):
            listing_id = t['id']
            log(f"\n[{i}/{len(targets)}] listing={listing_id} buyma={t['buyma_product_id']} "
                f"게시 {t['listed_days']}일 | {t['brand_name']} {t['model_no']}")

            listing = push.fetch_listing(conn, listing_id)
            if listing is None:
                log("  → 목록 사라짐, skip", "WARN")
                fail += 1
                continue

            res = push.execute_retire(conn, listing, dry_run=False)
            if res.get('skipped'):
                log(f"  → 출품정지 못 보냄: {res.get('reason')}", "WARN")
                fail += 1
            elif (res.get('response') or {}).get('success'):
                log("  → 출품정지 접수 (webhook 대기). 거부되면 다음 실행 때 표시 해제 후 재시도")
                if mark_excluded(conn, listing_id, reason):
                    log(f"  → DB: exception_reason='{reason}'")
                    ok += 1
                else:
                    fail += 1
            else:
                log(f"  → 재고 API 실패: {(res.get('response') or {}).get('error')} — DB 변경 X", "WARN")
                fail += 1

            if i < len(targets):
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        log("\n" + "=" * 60)
        log(f"완료: 성공 {ok}건 / 실패 {fail}건")
        log("=" * 60)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
