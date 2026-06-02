# -*- coding: utf-8 -*-
"""
출품기한(available_until) 만료 임박 상품을 today+90으로 연장 (일배치).

배경:
  - BUYMA 購入期限(available_until) 90일이 지나면 출품이 자동 종료됨.
  - 재고/가격 변동이 없는 상품은 stock sync가 edit를 안 보내 연장이 안 되므로,
    만료 임박분을 골라 강제로 today+90을 재푸시해 자동 종료를 막는다.

동작:
  1. ace_products에서 is_published=1, is_active=1, available_until <= today+GUARD 인 상품 조회
  2. 기존 stock sync(StockPriceSynchronizer)의 검증된 full-edit payload를 그대로 재사용
  3. 단, build 결과가 control=delete(전 옵션 품절 등)면 SKIP — 삭제는 stock sync 책임,
     이 배치는 "연장"만 한다 (품절 상품을 삭제하지 않음)
  4. publish 건만 BUYMA edit 호출 → 성공 시 DB available_until=today+90 write-back
     (available_until=today+90 세팅은 build_buyma_request가, DB 반영은 update_product_after_api_call이 처리)

전제: ace_products.available_until 이 BUYMA 실제값과 정합돼 있어야 함
      (buyma_stats/buyma_available_until_updater.py 로 1회 정합 후 운영).

사용법:
    python buyma_expiry_extender.py --dry-run         # 조회+판정만, API/DB 변경 없음
    python buyma_expiry_extender.py                    # 실제 연장
    python buyma_expiry_extender.py --limit 500        # 최대 500건 (점진 실행)
    python buyma_expiry_extender.py --guard-days 10    # 임박 기준 (기본 10일)
"""

import os
import sys
import time
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, 'okmall'))

# StockPriceSynchronizer 재사용: payload 빌더 / API 호출 / write-back 로직 공유 (중복·drift 방지)
from stock_price_synchronizer import StockPriceSynchronizer  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

API_CALL_DELAY = 0.3   # BUYMA API 호출 간 대기(초)


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def fetch_targets(sync: StockPriceSynchronizer, guard_days: int, limit):
    """만료 임박(available_until <= today+guard) 인 출품 상품 조회."""
    conn = sync.get_connection()
    try:
        with conn.cursor() as c:
            sql = """
                SELECT id, buyma_product_id, available_until
                FROM ace_products
                WHERE is_published = 1
                  AND is_active = 1
                  AND buyma_product_id IS NOT NULL
                  AND available_until IS NOT NULL
                  AND available_until <= DATE_ADD(CURDATE(), INTERVAL %s DAY)
                ORDER BY available_until ASC, id ASC
            """
            params = [guard_days]
            if limit:
                sql += " LIMIT %s"
                params.append(limit)
            c.execute(sql, params)
            return c.fetchall()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='available_until 만료 임박분을 today+90으로 연장'
    )
    parser.add_argument('--guard-days', type=int, default=10,
                        help='임박 기준 일수 (기본 10): available_until <= today+N 이면 연장')
    parser.add_argument('--limit', type=int, default=None,
                        help='최대 처리 건수 (점진 실행용)')
    parser.add_argument('--dry-run', action='store_true',
                        help='조회+판정만, API/DB 변경 없음')
    args = parser.parse_args()

    sync = StockPriceSynchronizer()

    log("=" * 60)
    log(f"만료 임박 연장 시작 (guard_days={args.guard_days}, limit={args.limit}, dry_run={args.dry_run})")
    log("=" * 60)

    targets = fetch_targets(sync, args.guard_days, args.limit)
    total = len(targets)
    log(f"대상: {total}건 (available_until <= today+{args.guard_days})")
    if not targets:
        log("대상 없음. 종료.")
        return

    stats = {'extended': 0, 'skipped_delete': 0, 'failed': 0, 'no_data': 0}
    for i, row in enumerate(targets, 1):
        ace_id = row['id']
        pid = row['buyma_product_id']
        try:
            data = sync.get_product_data_for_api(ace_id)
            if not data or not data.get('product'):
                stats['no_data'] += 1
                log(f"[{i}/{total}] id={ace_id} pid={pid} → 데이터 없음, skip", "WARN")
                continue

            req = sync.build_buyma_request(data)  # is_delete=False
            control = req.get('product', {}).get('control')

            # 삭제 판정(전 옵션 품절 등)은 이 배치가 건드리지 않음 — stock sync에 위임
            if control == 'delete':
                stats['skipped_delete'] += 1
                log(f"[{i}/{total}] id={ace_id} pid={pid} → delete 판정, skip (연장 안 함)")
                continue

            new_until = req.get('product', {}).get('available_until')

            if args.dry_run:
                stats['extended'] += 1
                log(f"[{i}/{total}] [DRY-RUN] id={ace_id} pid={pid} {row['available_until']} → {new_until}")
                continue

            result = sync.call_buyma_api(req)
            sync.update_product_after_api_call(ace_id, req, result)  # 성공 시 DB available_until write-back
            if result.get('success'):
                stats['extended'] += 1
                log(f"[{i}/{total}] id={ace_id} pid={pid} → 연장 {new_until} OK")
            else:
                stats['failed'] += 1
                log(f"[{i}/{total}] id={ace_id} pid={pid} → API 실패: {str(result.get('error',''))[:120]}", "ERROR")
            time.sleep(API_CALL_DELAY)
        except Exception as e:
            stats['failed'] += 1
            log(f"[{i}/{total}] id={ace_id} pid={pid} → 오류: {e}", "ERROR")

    log("=" * 60)
    log(f"완료: 연장 {stats['extended']}, 삭제판정 skip {stats['skipped_delete']}, "
        f"데이터없음 {stats['no_data']}, 실패 {stats['failed']}")
    log("=" * 60)


if __name__ == "__main__":
    main()
