# -*- coding: utf-8 -*-
"""
검증 스크립트 (읽기 전용 — DB 변경 없음)

목적:
  주문조회 API로 전체 주문을 받아 상품별 (판매수, 판매금액)을 집계하고,
  현재 DB(buyma_product_stats)에 저장된 값과 비교한다.

핵심: "어떤 주문 상태를 '판매'로 칠지"가 현재 HTML 방식과 맞아야 한다.
  현재 HTML 방식 = (진행중 주문) ∪ (발송완료 거래), 취소 제외, 거래ID로 중복제거.
  → API 상태(status)로 어떤 조합이 DB와 가장 일치하는지 여러 변형을 동시에 계산해 보여준다.

집계 규칙(현재 수집기와 동일하게 맞춤):
  - 주문 1건 = 카운트 1 (수량 amount 와 무관, 거래 단위로 셈)
  - 금액 = subtotal_price (소계, 수량 반영된 값)
  - 같은 주문 id 중복 제거

실행:
    python3 buyma_stats/verify_orders_api.py
"""

import os
import sys
import time
from collections import defaultdict

import pymysql
import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(os.path.dirname(SCRIPT_DIR), '.env'))

BUYMA_MODE = int(os.getenv('BUYMA_MODE', 1))
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')
API_BASE_URL = BUYMA_API_BASE_URL if BUYMA_MODE == 1 else BUYMA_SANDBOX_URL

DB_CONFIG = {
    'host':     os.getenv('DB_HOST'),
    'port':     int(os.getenv('DB_PORT', 3306)),
    'user':     os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset':  'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}

PER_PAGE = 100
TIMEOUT = 30

# 취소 계열 상태 (어떤 변형에서도 항상 제외)
CANCEL_STATUSES = {'canceled', 'forcibly_canceled'}

# 비교해볼 "판매로 인정할 상태" 변형들
VARIANTS = {
    # A: 취소만 빼고 전부 (입금대기 포함)  ← HTML '진행중∪발송완료'와 가장 근접 후보
    'A_취소만제외(전부)': lambda s: s not in CANCEL_STATUSES,
    # B: 취소 + 입금대기(예약) 제외
    'B_취소+입금대기제외': lambda s: s not in CANCEL_STATUSES and s != 'waiting_on_payment',
    # C: 발송/수령된 것만 (실제 발송 이상)
    'C_발송이상만': lambda s: s in {'product_sent', 'product_received'},
    # D: 수령완료만
    'D_수령완료만': lambda s: s == 'product_received',
}


def log(msg):
    print(msg, flush=True)


def fetch_all_orders():
    """주문조회 API 전체 페이지 수집 → 주문 dict 리스트."""
    if not BUYMA_ACCESS_TOKEN:
        log("❌ BUYMA_ACCESS_TOKEN 없음")
        sys.exit(1)

    headers = {"X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN}
    url = f"{API_BASE_URL}api/v1/orders.json"
    params = {"status": "any", "per_page": PER_PAGE, "page": 1}

    orders = []
    page = 1
    while True:
        params["page"] = page
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        except requests.exceptions.RequestException as e:
            log(f"❌ 페이지 {page} 요청 실패: {e}")
            sys.exit(1)

        if resp.status_code != 200:
            log(f"❌ 페이지 {page} 응답 {resp.status_code}: {resp.text[:300]}")
            sys.exit(1)

        batch = resp.json()
        if isinstance(batch, dict):
            batch = batch.get("orders") or batch.get("data") or []
        if not batch:
            break

        orders.extend(batch)
        log(f"  · 페이지 {page}: {len(batch)}건 (누적 {len(orders)})")

        # 다음 페이지 존재 여부: Link 헤더에 rel="next" 가 있으면 계속
        link = resp.headers.get("Link", "")
        if 'rel="next"' not in link:
            break
        page += 1
        time.sleep(0.3)

    return orders


def aggregate(orders, keep_fn):
    """keep_fn(status)==True 인 주문만, 주문 id 중복제거 후 상품별 (건수, 금액합)."""
    seen = set()
    agg = defaultdict(lambda: [0, 0])
    skipped_no_pid = 0
    for o in orders:
        status = o.get("status")
        if not keep_fn(status):
            continue
        oid = o.get("id")
        if oid in seen:
            continue
        seen.add(oid)
        prod = o.get("product") or {}
        pid = prod.get("id")
        if pid is None:
            skipped_no_pid += 1
            continue
        amount = o.get("subtotal_price") or 0
        agg[str(pid)][0] += 1
        agg[str(pid)][1] += int(amount)
    return {pid: (c, a) for pid, (c, a) in agg.items()}, skipped_no_pid


def load_db_stats():
    """현재 DB에 저장된 상품별 (sold_count, sales_amount_jpy)."""
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT buyma_product_id, sold_count, sales_amount_jpy
                FROM buyma_product_stats
                WHERE sold_count IS NOT NULL AND sold_count > 0
            """)
            rows = c.fetchall()
    finally:
        conn.close()
    return {str(r['buyma_product_id']): (int(r['sold_count'] or 0),
                                         int(r['sales_amount_jpy'] or 0)) for r in rows}


def compare(api_agg, db_agg, label):
    """API 집계 vs DB 비교 요약 출력."""
    api_cnt = sum(c for c, _ in api_agg.values())
    api_amt = sum(a for _, a in api_agg.values())
    db_cnt = sum(c for c, _ in db_agg.values())
    db_amt = sum(a for _, a in db_agg.values())

    api_pids = set(api_agg)
    db_pids = set(db_agg)
    common = api_pids & db_pids

    exact_match = sum(1 for p in common if api_agg[p] == db_agg[p])
    cnt_diff = sum(1 for p in common if api_agg[p][0] != db_agg[p][0])
    amt_diff = sum(1 for p in common if api_agg[p][1] != db_agg[p][1])

    log("")
    log(f"━━━ 변형 [{label}] ━━━")
    log(f"  총 판매수   : API {api_cnt:>6}   vs DB {db_cnt:>6}   (차이 {api_cnt - db_cnt:+})")
    log(f"  총 판매금액 : API ¥{api_amt:>12,}  vs DB ¥{db_amt:>12,}  (차이 {api_amt - db_amt:+,})")
    log(f"  상품 수     : API {len(api_pids):>5}   vs DB {len(db_pids):>5}   (공통 {len(common)})")
    log(f"  공통상품 중 완전일치 {exact_match}/{len(common)}  "
        f"(건수불일치 {cnt_diff}, 금액불일치 {amt_diff})")
    log(f"  API에만 있음: {len(api_pids - db_pids)}건 / DB에만 있음: {len(db_pids - api_pids)}건")
    return {'label': label, 'api_cnt': api_cnt, 'db_cnt': db_cnt,
            'api_amt': api_amt, 'db_amt': db_amt, 'exact': exact_match,
            'common': len(common), 'api_agg': api_agg}


def main():
    log("=" * 64)
    log("주문조회 API ↔ DB 검증 (읽기 전용)")
    log(f"환경: {'본환경' if BUYMA_MODE == 1 else '샌드박스'}")
    log("=" * 64)

    log("\n[1] API 전체 주문 수집...")
    orders = fetch_all_orders()
    log(f"  → 총 {len(orders)}건 수신")

    # 상태 분포
    status_count = defaultdict(int)
    for o in orders:
        status_count[o.get("status")] += 1
    log("\n[2] 상태(status) 분포:")
    for s, n in sorted(status_count.items(), key=lambda x: -x[1]):
        log(f"    {s:<22} {n}")

    log("\n[3] DB 현재값 로드...")
    db_agg = load_db_stats()
    log(f"  → DB에 판매기록 있는 상품 {len(db_agg)}건")

    log("\n[4] 변형별 비교 (DB와 가장 일치하는 조합 찾기):")
    results = []
    for label, fn in VARIANTS.items():
        api_agg, skipped = aggregate(orders, fn)
        if skipped:
            log(f"  (참고: [{label}] 상품ID 없는 주문 {skipped}건 제외)")
        results.append(compare(api_agg, db_agg, label))

    # 가장 잘 맞는 변형 = 완전일치 상품 수 최대
    best = max(results, key=lambda r: (r['exact'], -abs(r['api_cnt'] - r['db_cnt'])))
    log("\n" + "=" * 64)
    log(f"👉 DB와 가장 잘 맞는 변형: [{best['label']}] "
        f"(공통상품 {best['common']}건 중 완전일치 {best['exact']}건)")
    log("=" * 64)

    # 그 변형 기준 불일치 상위 사례 (디버깅용)
    best_agg = best['api_agg']
    common = set(best_agg) & set(db_agg)
    mismatches = []
    for p in common:
        ac, aa = best_agg[p]
        dc, da = db_agg[p]
        if (ac, aa) != (dc, da):
            mismatches.append((abs(aa - da), p, (dc, da), (ac, aa)))
    mismatches.sort(reverse=True)
    if mismatches:
        log(f"\n[참고] 불일치 상위 {min(15, len(mismatches))}건 "
            f"(상품ID: DB(건수,금액) → API(건수,금액)):")
        for _, p, db_v, api_v in mismatches[:15]:
            log(f"    {p}: DB{db_v} → API{api_v}")
    else:
        log("\n공통 상품은 전부 일치 ✅")


if __name__ == "__main__":
    main()
