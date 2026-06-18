# -*- coding: utf-8 -*-
"""
바이마 셀러 판매 실적 수집기 (총판매수 / 총판매금액)

대상 페이지:
  - https://www.buyma.com/my/orders/?page=N            (진행 중 주문: 受注/배송중)
  - https://www.buyma.com/my/buyersales/?srt=7&sro=2&p=N  (종료된 거래: 수령완료/취소)
      ※ 사이트 첫 진입 URL은 /my/buyersales/?tab=b 이며, 위 srt/sro 조합으로 재해석됨

수집 규칙:
  - 두 페이지를 끝까지 긁어 取引ID 기준 dedup
  - "取引キャンセル" 행은 제외
  - 결과 = (수령완료 거래) ∪ (배송중 거래)
  - buyma_product_id 별로 SUM(amount), COUNT(*) 집계
  - buyma_product_stats.sold_count, sales_amount_jpy UPSERT

쿠키: buyma_stats/.buyma_cookies.json (buyma_self_stats_collector.py와 동일)

사용법:
    python3 buyma_self_sales_collector.py                 # 전체 누적
    python3 buyma_self_sales_collector.py --max-pages 2   # 각 페이지 2장씩만(테스트)
"""

import os
import re
import sys
import json
import time
import argparse
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import pymysql
import requests as req_lib
from bs4 import BeautifulSoup
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


# =====================================================
# 설정
# =====================================================

BUYMA_BASE_URL = "https://www.buyma.com"

ORDERS_URL_TEMPLATE     = f"{BUYMA_BASE_URL}/my/orders/?page={{page}}"
BUYERSALES_URL_TEMPLATE = f"{BUYMA_BASE_URL}/my/buyersales/?srt=7&sro=2&p={{page}}"

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(SCRIPT_DIR, '.buyma_cookies.json')

CRAWL_DELAY   = 1.0
PAGE_TIMEOUT  = 60
PAGE_RETRY    = 3
RETRY_BACKOFF = 5

load_dotenv(os.path.join(os.path.dirname(SCRIPT_DIR), '.env'))

DB_CONFIG = {
    'host':     os.getenv('DB_HOST'),
    'port':     int(os.getenv('DB_PORT', 3306)),
    'user':     os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset':  'utf8mb4',
    'autocommit': False,
}


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


# =====================================================
# 세션
# =====================================================

def create_session() -> req_lib.Session:
    if not os.path.exists(COOKIE_FILE):
        log(f"쿠키 파일 없음: {COOKIE_FILE}", "ERROR")
        log("갱신: buyma_cleaners/buyma_orphan_cleaner.py --login 후 결과를 이 경로로 복사", "ERROR")
        sys.exit(2)

    with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
        pw_cookies = json.load(f)

    session = req_lib.Session()
    session.cookies.update({c['name']: c['value'] for c in pw_cookies})
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'ja,en;q=0.9',
        'Referer': f'{BUYMA_BASE_URL}/my/',
    })
    return session


_END_SENTINEL = object()  # 404 = 마지막 페이지 넘어섬 신호


def _fetch_page(session: req_lib.Session, url: str, page_num: int):
    last_err = None
    for attempt in range(1, PAGE_RETRY + 1):
        try:
            resp = session.get(url, timeout=PAGE_TIMEOUT)
            # 404는 끝페이지 — 재시도 없이 즉시 종료 신호
            if resp.status_code == 404:
                return _END_SENTINEL
            resp.raise_for_status()
            return resp
        except req_lib.RequestException as e:
            last_err = e
            wait = RETRY_BACKOFF * attempt
            log(f"  ↻ 페이지 {page_num} 시도 {attempt}/{PAGE_RETRY} 실패 ({e}). {wait}s 대기", "WARN")
            time.sleep(wait)
    log(f"  ✗ 페이지 {page_num} 최종 실패: {last_err}", "ERROR")
    return None


# =====================================================
# 행 파싱
# =====================================================

# imgdata/item/{cat}/0{buyma_product_id}/{imageid}/...
_RE_PID_IN_IMG = re.compile(r'imgdata/item/\d+/0?(\d{9,10})/')
# orders: tr class "trade_row_dt_tr_{id}"
_RE_TRADE_ORDERS = re.compile(r'trade_row_dt_tr_(\d+)')
# buyersales: 본문 "取引ID 34732217"
_RE_TRADE_BUYERSALES = re.compile(r'取引ID[^\d]*(\d+)')
# 금액: "¥36,113" 또는 "11,894円"
_RE_AMOUNT_YEN_PREFIX = re.compile(r'¥\s*([\d,]+)')
_RE_AMOUNT_YEN_SUFFIX = re.compile(r'([\d,]+)\s*円')
# 날짜: "2026/05/22" — buyersales의 발송일이 진짜 날짜인지 판정용.
# 발송 전이거나 취소된 거래는 "取引キャンセル" 또는 "-"가 들어옴.
_RE_DATE = re.compile(r'^\d{4}/\d{1,2}/\d{1,2}')


def _extract_buyma_product_id(tr) -> Optional[str]:
    for im in tr.find_all('img'):
        src = im.get('src') or im.get('data-src') or ''
        m = _RE_PID_IN_IMG.search(src)
        if m:
            return m.group(1)
    return None


def _extract_amount_jpy(tr) -> Optional[int]:
    """행 내 td 중 가장 먼저 잡히는 ¥/円 금액 → int."""
    for td in tr.find_all(['th', 'td']):
        txt = td.get_text(separator=' ', strip=True)
        if not txt:
            continue
        m = _RE_AMOUNT_YEN_PREFIX.search(txt) or _RE_AMOUNT_YEN_SUFFIX.search(txt)
        if m:
            try:
                return int(m.group(1).replace(',', ''))
            except ValueError:
                return None
    return None


def _is_cancelled(tr) -> bool:
    return '取引キャンセル' in tr.get_text()


def parse_orders_page(soup: BeautifulSoup) -> List[Dict]:
    """ /my/orders/ 한 페이지 → 거래 list."""
    t = soup.find('table', class_='orders-table')
    if not t:
        return []
    rows = []
    for tr in t.find_all('tr', class_='orders-table-tr-main'):
        if _is_cancelled(tr):
            continue
        trade_id = None
        for cls in tr.get('class', []):
            m = _RE_TRADE_ORDERS.match(cls)
            if m:
                trade_id = m.group(1)
                break
        pid    = _extract_buyma_product_id(tr)
        amount = _extract_amount_jpy(tr)
        if not (trade_id and pid and amount is not None):
            continue
        rows.append({
            'trade_id':         trade_id,
            'buyma_product_id': pid,
            'amount_jpy':       amount,
            'source':           'orders',
        })
    return rows


def parse_buyersales_page(soup: BeautifulSoup) -> List[Dict]:
    """ /my/buyersales/ 한 페이지 → 거래 list.

    "発送日(td[6])이 진짜 날짜인 행"만 카운트.
    - 진짜 취소 / 발송 전 거래는 발송일 칸에 "取引キャンセル" 또는 "-" 가 들어와서 자동 제외.
    - 발송 전 진행중 주문은 orders 페이지에 있으므로 그쪽에서 잡힘 (取引ID로 dedup).
    """
    rows = []
    for tr in soup.find_all('tr'):
        if not tr.find('img', src=re.compile(r'^https://cdn-images\.buyma\.com/')):
            continue
        tds = tr.find_all(['th', 'td'])
        if len(tds) < 7:
            continue
        shipped_text = tds[6].get_text(strip=True)
        if not _RE_DATE.match(shipped_text):
            continue
        body_text = tr.get_text(separator=' ', strip=True)
        m_tid = _RE_TRADE_BUYERSALES.search(body_text)
        trade_id = m_tid.group(1) if m_tid else None
        pid    = _extract_buyma_product_id(tr)
        amount = _extract_amount_jpy(tr)
        if not (trade_id and pid and amount is not None):
            continue
        rows.append({
            'trade_id':         trade_id,
            'buyma_product_id': pid,
            'amount_jpy':       amount,
            'source':           'buyersales',
        })
    return rows


# =====================================================
# 크롤링 루프
# =====================================================

def crawl(session: req_lib.Session,
          label: str,
          url_template: str,
          parse_fn,
          max_pages: Optional[int]) -> List[Dict]:
    all_rows: List[Dict] = []
    consecutive_empty = 0
    page = 1

    while True:
        if max_pages and page > max_pages:
            log(f"[{label}] max-pages={max_pages} 도달. 중단")
            break

        url = url_template.format(page=page)
        log(f"[{label}] 페이지 {page} 요청...")

        resp = _fetch_page(session, url, page)
        if resp is _END_SENTINEL:
            log(f"[{label}]   → 404 (페이지 {page-1}이 마지막). 종료")
            break
        if resp is None:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                log(f"[{label}] 연속 3페이지 실패. 종료.", "ERROR")
                break
            page += 1
            time.sleep(CRAWL_DELAY)
            continue

        if '/login' in resp.url:
            log("=" * 60, "ERROR")
            log("세션 만료 — 쿠키 무효. 배치 중단.", "ERROR")
            log("갱신: buyma_cleaners/buyma_orphan_cleaner.py --login 후 쿠키 복사", "ERROR")
            log("=" * 60, "ERROR")
            sys.exit(3)

        soup = BeautifulSoup(resp.text, 'html.parser')
        page_rows = parse_fn(soup)

        if not page_rows:
            log(f"[{label}]   → 행 없음. 종료 (페이지 {page})")
            break

        all_rows.extend(page_rows)
        log(f"[{label}]   → {len(page_rows)}건 (누적 {len(all_rows)})")

        consecutive_empty = 0
        page += 1
        time.sleep(CRAWL_DELAY)

    return all_rows


# =====================================================
# 집계 & UPSERT
# =====================================================

def aggregate_by_product(rows: List[Dict]) -> Dict[str, Tuple[int, int]]:
    """ buyma_product_id → (sold_count, sales_amount_jpy)."""
    seen_trade: set = set()
    agg: Dict[str, List[int]] = defaultdict(lambda: [0, 0])

    for r in rows:
        tid = r['trade_id']
        if tid in seen_trade:
            continue
        seen_trade.add(tid)
        pid    = r['buyma_product_id']
        amount = r['amount_jpy']
        agg[pid][0] += 1
        agg[pid][1] += amount

    return {pid: (cnt, amt) for pid, (cnt, amt) in agg.items()}


def upsert_sales(per_product: Dict[str, Tuple[int, int]]) -> None:
    if not per_product:
        log("UPSERT 대상 없음")
        return

    log(f"DB 접속 → {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    conn = pymysql.connect(**DB_CONFIG)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql = """
        INSERT INTO buyma_product_stats
            (buyma_product_id, sold_count, sales_amount_jpy, stats_collected_at)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            sold_count        = VALUES(sold_count),
            sales_amount_jpy  = VALUES(sales_amount_jpy),
            stats_collected_at = VALUES(stats_collected_at)
    """
    params = [(pid, cnt, amt, now) for pid, (cnt, amt) in per_product.items()]

    try:
        with conn.cursor() as c:
            c.executemany(sql, params)
        conn.commit()
        log(f"UPSERT 완료: {len(params)}건 (stats_collected_at={now})")
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
        description='바이마 자사 판매 실적 수집 → buyma_product_stats UPSERT'
    )
    parser.add_argument('--max-pages', type=int, default=None,
                        help='테스트용: 각 페이지 N장씩만 (기본: 전체)')
    args = parser.parse_args()

    log("=" * 60)
    log("바이마 자사 판매 실적 수집 시작")
    log("=" * 60)

    session = create_session()

    orders_rows     = crawl(session, 'orders',     ORDERS_URL_TEMPLATE,     parse_orders_page,     args.max_pages)
    buyersales_rows = crawl(session, 'buyersales', BUYERSALES_URL_TEMPLATE, parse_buyersales_page, args.max_pages)

    log(f"orders     수집: {len(orders_rows)}건")
    log(f"buyersales 수집: {len(buyersales_rows)}건 (취소 제외)")

    per_product = aggregate_by_product(orders_rows + buyersales_rows)
    total_cnt = sum(c for c, _ in per_product.values())
    total_amt = sum(a for _, a in per_product.values())
    log(f"상품 수: {len(per_product)} / 총 판매수: {total_cnt} / 총 판매금액: ¥{total_amt:,}")

    upsert_sales(per_product)

    log("=" * 60)
    log("완료")
    log("=" * 60)


if __name__ == "__main__":
    main()
