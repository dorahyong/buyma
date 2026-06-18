# -*- coding: utf-8 -*-
"""
바이마 셀러 판매 실적 수집기 (총판매수 / 총판매금액)  ※ API 방식

[변경 이력]
  이전: /my/orders, /my/buyersales 페이지를 HTML로 긁어 집계 (쿠키 로그인 의존)
        → 백업: buyma_self_sales_collector_html_backup.py
  현재: BUYMA Personal Shopper API(주문조회)로 수집 (액세스 토큰 사용)

[동작]
  1) 주문조회 API(GET /api/v1/orders.json?status=any)로 최근 주문 전체 수집
     - BUYMA는 약 4개월치 최근 주문만 반환(롤링 윈도우 추정)
  2) buyma_self_orders 테이블에 주문을 "현재 상태와 함께" UPSERT
     - 한번 들어온 주문은 영구 보관 → 4개월 지나 API에서 빠져도 유지(진짜 누적)
     - 매번 status를 최신으로 갱신 → 나중에 취소되면 자동 반영
  3) buyma_self_orders 내 모든 상품을 재계산(취소는 제외=0으로 빠짐)
     - sold_count = 취소 아닌 거래 건수 (수량 amount 아님, HTML 방식과 동일)
     - sales_amount_jpy = 취소 아닌 SUM(subtotal_price)
     - 전부 취소된 상품은 0으로 갱신 → 취소 박제 자동 보정
     - 단, 4개월 창에서 사라진 옛 상품(주문표에 없음)은 손대지 않아 보존
  4) buyma_product_stats 에 UPSERT (화면이 읽는 테이블, 형식 그대로)

집계 규칙(이전 HTML 방식과 동일하게 유지):
  - 취소(canceled / forcibly_canceled) 제외, 그 외 전부 판매로 인정
  - 주문 1건 = 카운트 1

사용법:
    python3 buyma_self_sales_collector.py
"""

import os
import sys
import time
import argparse
from datetime import datetime
from typing import Dict, List, Optional

import pymysql
import requests
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


# =====================================================
# 설정
# =====================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(os.path.dirname(SCRIPT_DIR), '.env'))

BUYMA_MODE         = int(os.getenv('BUYMA_MODE', 1))  # 1: 본환경, 2: 샌드박스
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL  = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')
API_BASE_URL       = BUYMA_API_BASE_URL if BUYMA_MODE == 1 else BUYMA_SANDBOX_URL

PER_PAGE      = 100
API_TIMEOUT   = 30
API_RETRY     = 3
RETRY_BACKOFF = 5
PAGE_DELAY    = 0.3

# 취소 계열 = 판매에서 제외
CANCEL_STATUSES = ('canceled', 'forcibly_canceled')

DB_CONFIG = {
    'host':       os.getenv('DB_HOST'),
    'port':       int(os.getenv('DB_PORT', 3306)),
    'user':       os.getenv('DB_USER'),
    'password':   os.getenv('DB_PASSWORD'),
    'database':   os.getenv('DB_NAME'),
    'charset':    'utf8mb4',
    'autocommit': False,
}


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


# =====================================================
# API 수집
# =====================================================

def _fetch_page(url: str, params: dict):
    """1페이지 요청 (재시도 포함). (json, link헤더) 반환."""
    headers = {"X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN}
    last_err = None
    for attempt in range(1, API_RETRY + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=API_TIMEOUT)
            if resp.status_code in (401, 403):
                log("토큰 인증/권한 거부됨. 주문조회 권한을 확인하세요.", "ERROR")
                log(resp.text[:300], "ERROR")
                sys.exit(3)
            resp.raise_for_status()
            return resp.json(), resp.headers.get("Link", "")
        except requests.RequestException as e:
            last_err = e
            wait = RETRY_BACKOFF * attempt
            log(f"  ↻ page={params.get('page')} 시도 {attempt}/{API_RETRY} 실패 ({e}). {wait}s 대기", "WARN")
            time.sleep(wait)
    log(f"  ✗ page={params.get('page')} 최종 실패: {last_err}", "ERROR")
    sys.exit(1)


def fetch_all_orders() -> List[Dict]:
    """주문조회 API 전체 페이지 수집."""
    if not BUYMA_ACCESS_TOKEN:
        log("BUYMA_ACCESS_TOKEN 이 .env 에 없습니다.", "ERROR")
        sys.exit(2)

    url = f"{API_BASE_URL}api/v1/orders.json"
    orders: List[Dict] = []
    page = 1
    while True:
        params = {"status": "any", "per_page": PER_PAGE, "page": page}
        batch, link = _fetch_page(url, params)
        if isinstance(batch, dict):
            batch = batch.get("orders") or batch.get("data") or []
        if not batch:
            break
        orders.extend(batch)
        log(f"  · page {page}: {len(batch)}건 (누적 {len(orders)})")
        if 'rel="next"' not in link:
            break
        page += 1
        time.sleep(PAGE_DELAY)
    return orders


def _parse_ordered_at(o: Dict) -> Optional[str]:
    """ISO('2026-06-18T10:46:57.000+09:00') → 'YYYY-MM-DD HH:MM:SS' (KST)."""
    raw = o.get("ordered_at") or o.get("pre_ordered_at")
    if not raw or len(raw) < 19:
        return None
    return raw[:19].replace("T", " ")


# =====================================================
# DB: 주문 저장(누적) & 집계
# =====================================================

DDL_ORDERS = """
CREATE TABLE IF NOT EXISTS buyma_self_orders (
    order_id          BIGINT       NOT NULL COMMENT '거래ID(주문ID)',
    buyma_product_id  VARCHAR(32)  NOT NULL,
    subtotal_price    BIGINT       DEFAULT NULL COMMENT '소계(엔, 수량반영)',
    amount            INT          DEFAULT NULL COMMENT '수량',
    status            VARCHAR(32)  DEFAULT NULL,
    ordered_at        DATETIME     DEFAULT NULL,
    updated_at        DATETIME     DEFAULT NULL COMMENT '마지막 수집 시각',
    PRIMARY KEY (order_id),
    KEY idx_product (buyma_product_id),
    KEY idx_status  (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

UPSERT_ORDER = """
INSERT INTO buyma_self_orders
    (order_id, buyma_product_id, subtotal_price, amount, status, ordered_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    buyma_product_id = VALUES(buyma_product_id),
    subtotal_price   = VALUES(subtotal_price),
    amount           = VALUES(amount),
    status           = VALUES(status),
    ordered_at       = VALUES(ordered_at),
    updated_at       = VALUES(updated_at)
"""

# 주문창 안의 "모든" 상품을 재계산하되, 취소는 0으로 빠지게 집계.
#   → 전부 취소된 상품은 sold_count=0 으로 갱신되어 자동 보정됨(박제 방지).
#   → 4개월 창에서 완전히 사라진 옛 상품(이 표에 없음)은 건드리지 않아 보존됨.
_CANCEL_PH = ','.join(['%s'] * len(CANCEL_STATUSES))
AGG_SQL = f"""
SELECT buyma_product_id,
       SUM(CASE WHEN status NOT IN ({_CANCEL_PH}) THEN 1 ELSE 0 END) AS sold_count,
       COALESCE(SUM(CASE WHEN status NOT IN ({_CANCEL_PH})
                         THEN subtotal_price ELSE 0 END), 0)         AS sales_amount_jpy
FROM buyma_self_orders
GROUP BY buyma_product_id
"""

UPSERT_STATS = """
INSERT INTO buyma_product_stats
    (buyma_product_id, sold_count, sales_amount_jpy, stats_collected_at)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    sold_count        = VALUES(sold_count),
    sales_amount_jpy  = VALUES(sales_amount_jpy),
    stats_collected_at = VALUES(stats_collected_at)
"""


def store_and_aggregate(orders: List[Dict]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 주문 → UPSERT 파라미터
    order_params = []
    skipped = 0
    for o in orders:
        oid = o.get("id")
        prod = o.get("product") or {}
        pid = prod.get("id")
        if oid is None or pid is None:
            skipped += 1
            continue
        order_params.append((
            oid,
            str(pid),
            int(o.get("subtotal_price") or 0),
            int(o.get("amount") or 0),
            o.get("status"),
            _parse_ordered_at(o),
            now,
        ))
    if skipped:
        log(f"상품ID/주문ID 없는 주문 {skipped}건 제외")

    log(f"DB 접속 → {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as c:
            c.execute(DDL_ORDERS)

            # 1) 주문 누적 저장 (상태 갱신 포함)
            if order_params:
                c.executemany(UPSERT_ORDER, order_params)
            log(f"buyma_self_orders UPSERT: {len(order_params)}건")

            # 2) 주문창 내 모든 상품 재계산 (취소는 0으로 빠짐 → 박제 자동 보정)
            c.execute(AGG_SQL, CANCEL_STATUSES + CANCEL_STATUSES)
            rows = c.fetchall()  # (pid, cnt, amt)

            # 3) buyma_product_stats UPSERT
            stats_params = [(r[0], int(r[1]), int(r[2]), now) for r in rows]
            if stats_params:
                c.executemany(UPSERT_STATS, stats_params)

        conn.commit()

        total_cnt = sum(p[1] for p in stats_params)
        total_amt = sum(p[2] for p in stats_params)
        log(f"buyma_product_stats UPSERT: {len(stats_params)}개 상품")
        log(f"총 판매수: {total_cnt} / 총 판매금액: ¥{total_amt:,} (취소 제외 누적)")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =====================================================
# main
# =====================================================

def main():
    parser = argparse.ArgumentParser(
        description='바이마 자사 판매 실적 수집(API) → buyma_self_orders 누적 → buyma_product_stats UPSERT'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='DB 변경 없이 수집/집계 결과만 출력')
    args = parser.parse_args()

    log("=" * 60)
    log(f"바이마 자사 판매 실적 수집 시작 (API / {'본환경' if BUYMA_MODE == 1 else '샌드박스'})")
    log("=" * 60)

    orders = fetch_all_orders()
    log(f"API 주문 수집: {len(orders)}건")

    if args.dry_run:
        from collections import defaultdict
        agg = defaultdict(lambda: [0, 0])
        seen = set()
        for o in orders:
            oid = o.get("id"); prod = o.get("product") or {}; pid = prod.get("id")
            if oid in seen or pid is None:
                continue
            seen.add(oid)
            if o.get("status") in CANCEL_STATUSES:
                continue
            agg[str(pid)][0] += 1
            agg[str(pid)][1] += int(o.get("subtotal_price") or 0)
        tc = sum(v[0] for v in agg.values()); ta = sum(v[1] for v in agg.values())
        log(f"[DRY-RUN] 상품 {len(agg)}개 / 총 판매수 {tc} / 총 판매금액 ¥{ta:,} (DB 변경 안 함)")
        return

    store_and_aggregate(orders)

    log("=" * 60)
    log("완료")
    log("=" * 60)


if __name__ == "__main__":
    main()