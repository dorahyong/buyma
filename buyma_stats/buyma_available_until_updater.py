# -*- coding: utf-8 -*-
"""
바이마 셀러 전시목록에서 有効期限(게시기한)을 긁어 ace_products.available_until 정합 (일회성).

배경:
  - 그동안 stock edit/register는 BUYMA에 available_until=today+90을 푸시했지만 DB엔 write-back을 안 해
    DB.available_until 이 등록 시점 값에 멈춰(stale) BUYMA 실제값보다 이른 상태였음.
  - 셀러 전시목록 행 HTML의 span._item_yukodate_text[data-item-yukodate-text] 에 실제 有効期限이 노출됨.
  - 이 값을 한 번 긁어 DB에 반영하면 정합 완료. 이후엔 edit 시 write-back + 연장 배치가 DB를 계속 일치시킴.

수집 대상 페이지: https://www.buyma.com/my/sell (전체 상태)
크롤 인프라는 buyma_self_stats_collector.py 와 동일 (쿠키/세션/페이지네이션/재시도).

쿠키: 이 스크립트 폴더의 .buyma_cookies.json (stats collector와 공유).
  갱신: buyma_cleaners/buyma_orphan_cleaner.py --login → 생성된 buyma_cookies.json 을 이 경로로 복사.

사용법:
    python buyma_available_until_updater.py                 # 전체 페이지 크롤 후 UPDATE
    python buyma_available_until_updater.py --dry-run       # UPDATE 없이 미리보기
    python buyma_available_until_updater.py --max-pages 2   # 테스트용 (2페이지만)
    python buyma_available_until_updater.py --start-page 5  # 중단 지점부터 이어받기
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from typing import Dict, List, Optional

import pymysql
import requests as req_lib
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# 표준 출력 인코딩 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


BUYMA_BASE_URL = "https://www.buyma.com"

# 전체 상태(출품중·정지·종료 등) — buyma_self_stats_collector.py 와 동일 URL
BUYMA_LIST_URL_TEMPLATE = (
    "{base}/my/sell?duty_kind=all"
    "&facet=brand_id%2Ccate_pivot%2Cstatus%2Ctag_ids%2Cshop_labels%2Cstock_state"
    "&order=desc&page={{page}}&rows=100&sale_kind=all&sort=item_id"
    "&timesale_kind=all"
).format(base=BUYMA_BASE_URL)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(SCRIPT_DIR, '.buyma_cookies.json')

CRAWL_DELAY = 1.0
PAGE_TIMEOUT = 60
PAGE_RETRY = 3
RETRY_BACKOFF = 5

load_dotenv(os.path.join(os.path.dirname(SCRIPT_DIR), '.env'))

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
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
        log("갱신: buyma_cleaners/buyma_orphan_cleaner.py --login → buyma_cookies.json 을 이 경로(.buyma_cookies.json)로 복사")
        sys.exit(2)

    with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
        pw_cookies = json.load(f)

    session = req_lib.Session()
    session.cookies.update({c['name']: c['value'] for c in pw_cookies})
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'ja,en;q=0.9',
    })
    return session


# =====================================================
# 행 파싱
# =====================================================

def parse_row(tr) -> Optional[Dict]:
    """전시목록 한 행 → {buyma_product_id, available_until}"""
    cb = tr.select_one('input[name="chkitems"]')
    if not cb or not cb.get('value'):
        return None
    pid = cb['value'].strip()

    # 有効期限: span._item_yukodate_text 의 data-item-yukodate-text (예: "2026/08/26")
    yk = tr.select_one('span._item_yukodate_text')
    available_until = None
    if yk:
        available_until = (yk.get('data-item-yukodate-text') or yk.get_text(strip=True) or '').strip() or None

    return {'buyma_product_id': pid, 'available_until': available_until}


# =====================================================
# 크롤링
# =====================================================

def _fetch_page(session: req_lib.Session, url: str, page_num: int):
    last_err = None
    for attempt in range(1, PAGE_RETRY + 1):
        try:
            resp = session.get(url, timeout=PAGE_TIMEOUT)
            resp.raise_for_status()
            return resp
        except req_lib.RequestException as e:
            last_err = e
            wait = RETRY_BACKOFF * attempt
            log(f"  ↻ 페이지 {page_num} 시도 {attempt}/{PAGE_RETRY} 실패 ({e}). {wait}s 대기 후 재시도", "WARN")
            time.sleep(wait)
    log(f"  ✗ 페이지 {page_num} 최종 실패: {last_err}", "ERROR")
    return None


def crawl_all(session: req_lib.Session,
              max_pages: Optional[int] = None,
              start_page: int = 1) -> List[Dict]:
    all_rows: List[Dict] = []
    page_num = start_page
    consecutive_failures = 0
    end_page = (start_page + max_pages - 1) if max_pages else None

    while True:
        if end_page and page_num > end_page:
            log(f"max-pages={max_pages} (페이지 {start_page}~{end_page}) 도달. 중단")
            break

        url = BUYMA_LIST_URL_TEMPLATE.format(page=page_num)
        log(f"페이지 {page_num} 요청...")

        resp = _fetch_page(session, url, page_num)
        if resp is None:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                log("연속 3페이지 실패. 크롤 종료.", "ERROR")
                break
            page_num += 1
            time.sleep(CRAWL_DELAY)
            continue
        consecutive_failures = 0

        if '/login' in resp.url:
            log("세션 만료 — 쿠키 무효. 배치 중단.", "ERROR")
            log("갱신: buyma_orphan_cleaner.py --login → .buyma_cookies.json 복사", "ERROR")
            sys.exit(3)

        soup = BeautifulSoup(resp.text, 'html.parser')
        rows = soup.select('tr.js-checkbox-check-row')

        if not rows:
            log(f"  → 행 없음. 종료 (페이지 {page_num})")
            break

        page_data = []
        for tr in rows:
            parsed = parse_row(tr)
            if parsed:
                page_data.append(parsed)

        all_rows.extend(page_data)
        with_date = sum(1 for r in page_data if r.get('available_until'))
        log(f"  → {len(page_data)}개 (期限보유 {with_date}, 누적 {len(all_rows)})")

        if not soup.select_one('a[rel="next"]'):
            log(f"  → 마지막 페이지 (페이지 {page_num})")
            break

        page_num += 1
        time.sleep(CRAWL_DELAY)

    return all_rows


# =====================================================
# DB UPDATE
# =====================================================

def update_available_until(rows: List[Dict], dry_run: bool = False) -> None:
    """크롤한 有効期限을 ace_products.available_until 에 반영 (buyma_product_id 기준)."""
    valid = [
        (r['available_until'].replace('/', '-'), r['buyma_product_id'])
        for r in rows
        if r.get('buyma_product_id') and r.get('available_until')
    ]
    log(f"UPDATE 대상: {len(valid)} / 크롤 {len(rows)} (期限 없는 행 제외)")
    if not valid:
        return

    if dry_run:
        for au, pid in valid[:20]:
            log(f"  [DRY-RUN] buyma_product_id={pid} → available_until={au}")
        if len(valid) > 20:
            log(f"  ... 외 {len(valid) - 20}건")
        log(f"[DRY-RUN] 총 {len(valid)}건 UPDATE 예정 (실제 변경 없음)")
        return

    log(f"DB 접속 → {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    conn = pymysql.connect(**DB_CONFIG)
    try:
        sql = "UPDATE ace_products SET available_until = %s WHERE buyma_product_id = %s"
        affected = 0
        with conn.cursor() as c:
            for i in range(0, len(valid), 1000):
                chunk = valid[i:i + 1000]
                c.executemany(sql, chunk)
                affected += c.rowcount
        conn.commit()
        log(f"UPDATE 완료: {len(valid)}건 시도, {affected} rows affected")
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
        description='바이마 전시목록 有効期限 → ace_products.available_until 정합 (일회성)'
    )
    parser.add_argument('--max-pages', type=int, default=None,
                        help='테스트용: 페이지 N개만 (기본: 전체)')
    parser.add_argument('--start-page', type=int, default=1,
                        help='시작 페이지 (기본: 1). 중단 지점부터 이어받기용')
    parser.add_argument('--dry-run', action='store_true',
                        help='UPDATE 없이 미리보기')
    args = parser.parse_args()

    log("=" * 60)
    log(f"available_until 정합 시작 (start_page={args.start_page}, dry_run={args.dry_run})")
    log("=" * 60)

    session = create_session()
    rows = crawl_all(session, max_pages=args.max_pages, start_page=args.start_page)

    if not rows:
        log("수집된 데이터 없음")
        return

    update_available_until(rows, dry_run=args.dry_run)

    log("=" * 60)
    log(f"완료: 크롤 {len(rows)}건")
    log("=" * 60)


if __name__ == "__main__":
    main()
