# -*- coding: utf-8 -*-
"""
바이마 셀러 출품 목록(전시목록) 통계 수집기

대상 페이지: https://www.buyma.com/my/sell/?tab=b#/
  → 실제 SSR 엔드포인트: /my/sell?...&page=N&rows=100&status=for_sale

수집 컬럼 (상품 1행당):
  - buyma_product_id
  - status_code (Sts01 등 raw)
  - image_url, image_alt
  - name_ja          (상품명 일본어)
  - stock            (재고)
  - units_sold       (판매수)
  - price_yen        (출품가, ¥)
  - registered_at    ("2026/05/06 10:54")
  - expire_at        ("2026/05/10")
  - cart_count       (총 장바구니)
  - favorite_count   (총 찜)
  - access_count     (총 액세스)


출력: buyma_self_stats_YYYYMMDD_HHMM.json
백엔드 분이 페이지/DB 만드시면, 이후에 DB UPSERT 분기를 추가하면 됩니다.

로그인 쿠키는 buyma_cleaners/buyma_cookies.json 공유 사용.
최초 1회 또는 쿠키 만료 시 buyma_cleaners 쪽에서 로그인:
    cd ../buyma_cleaners && python3 buyma_orphan_cleaner.py --login

사용법:
    python3 buyma_self_stats_collector.py                 # 전체 페이지
    python3 buyma_self_stats_collector.py --max-pages 2   # 테스트용 (2페이지만)
    python3 buyma_self_stats_collector.py --out test.json # 출력 경로 지정
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


# =====================================================
# 설정
# =====================================================

BUYMA_BASE_URL = "https://www.buyma.com"

# 셀러 전시목록 — 전체 상태(출품중·정지·종료 등 모두) 한 번에 긁어옴.
# 이전 버전은 &status=for_sale 이 박혀 있어 출품중만 11,300건 정도만 잡혔음.
# status 파라미터를 빼면 셀러페이지의 "전체" 탭(약 460+ 페이지)이 잡힘.
BUYMA_LIST_URL_TEMPLATE = (
    "{base}/my/sell?duty_kind=all"
    "&facet=brand_id%2Ccate_pivot%2Cstatus%2Ctag_ids%2Cshop_labels%2Cstock_state"
    "&order=desc&page={{page}}&rows=100&sale_kind=all&sort=item_id"
    "&timesale_kind=all"
).format(base=BUYMA_BASE_URL)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 쿠키는 buyma_cleaners 폴더와 공유 (cleaner 쪽에서 --login 한 번 하면 같이 적용됨)
COOKIE_FILE = os.path.normpath(
    os.path.join(SCRIPT_DIR, '..', 'buyma_cleaners', 'buyma_cookies.json')
)

CRAWL_DELAY = 1.0

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
        log("buyma_cleaners 쪽에서 먼저 로그인:")
        log("  cd ../buyma_cleaners && python3 buyma_orphan_cleaner.py --login")
        sys.exit(1)

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

def _text(el) -> str:
    if el is None:
        return ""
    return el.get_text(separator=' ', strip=True)


def _to_int(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.replace(',', '').replace('¥', '').strip()
    try:
        return int(s)
    except ValueError:
        return None


def parse_row(tr) -> Optional[Dict]:
    """전시목록 한 행 → 통계 dict"""
    cb = tr.select_one('input[name="chkitems"]')
    if not cb or not cb.get('value'):
        return None
    pid = cb['value'].strip()

    # 상태 코드 (Sts01 등 raw값으로 저장; 매핑은 백엔드/DB 단에서)
    status_el = tr.select_one('[data-item-edit-status]')
    status_code = status_el['data-item-edit-status'] if status_el else None

    # 대표 이미지
    img_el = tr.select_one('td.Image48Box img')
    image_url = img_el['src'] if img_el and img_el.has_attr('src') else None
    image_alt = img_el['alt'] if img_el and img_el.has_attr('alt') else None

    # 상품명 (일본어 원문 — 페이지 자동번역 끄고 받아와짐)
    name_ja = _text(tr.select_one('td.item_name p a'))

    # 재고 / 판매수 / 가격
    stock = _to_int(_text(tr.select_one('.js-list-capacity-amount')))
    units_sold = _to_int(_text(tr.select_one('.js-list-unit-summary')))
    price_yen = _to_int(_text(tr.select_one('.js-item-price-display')))

    # 등록일시 — td 전체에 "YYYY/MM/DD\nHH:MM" 형태
    reg_date_el = tr.select_one('._item_kokaidate_text')
    registered_at = None
    if reg_date_el:
        td = reg_date_el.find_parent('td')
        registered_at = _text(td) or None

    # 구매기한
    expire_at = _text(tr.select_one('._item_yukodate_text')) or None

    # 장바구니 / 찜 / 액세스 — 헤더 순서와 동일하게 td.txtCenter가 3개 옴
    centers = tr.select('td.txtCenter span.fab-typo-nowrap')
    cart_count = _to_int(_text(centers[0])) if len(centers) > 0 else None
    favorite_count = _to_int(_text(centers[1])) if len(centers) > 1 else None
    access_count = _to_int(_text(centers[2])) if len(centers) > 2 else None

    return {
        'buyma_product_id': pid,
        'status_code': status_code,
        'image_url': image_url,
        'image_alt': image_alt,
        'name_ja': name_ja,
        'stock': stock,
        'units_sold': units_sold,
        'price_yen': price_yen,
        'registered_at': registered_at,
        'expire_at': expire_at,
        'cart_count': cart_count,
        'favorite_count': favorite_count,
        'access_count': access_count,
    }


# =====================================================
# 크롤링
# =====================================================

PAGE_TIMEOUT = 60       # 한 페이지 요청 타임아웃(초)
PAGE_RETRY = 3          # 페이지당 재시도 횟수
RETRY_BACKOFF = 5       # 재시도 사이 대기(초). 1차 5s, 2차 10s 식으로 증가


def _fetch_page(session: req_lib.Session, url: str, page_num: int):
    """한 페이지 요청. 재시도 + 백오프. 끝까지 실패하면 None."""
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
            # 연속 3 페이지 실패면 진짜 끝난 것으로 보고 중단
            if consecutive_failures >= 3:
                log("연속 3페이지 실패. 크롤 종료.", "ERROR")
                break
            page_num += 1
            time.sleep(CRAWL_DELAY)
            continue
        consecutive_failures = 0

        if '/login' in resp.url:
            log("세션 만료. cleaner 쪽에서 --login 다시 실행 필요.", "ERROR")
            break

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
        log(f"  → {len(page_data)}개 (누적 {len(all_rows)})")

        if not soup.select_one('a[rel="next"]'):
            log(f"  → 마지막 페이지 (페이지 {page_num})")
            break

        page_num += 1
        time.sleep(CRAWL_DELAY)

    return all_rows


# =====================================================
# DB UPSERT
# =====================================================

def _fetch_ace_id_map(conn, buyma_ids: List[str]) -> Dict[str, int]:
    """buyma_product_id → ace_products.id 매핑."""
    if not buyma_ids:
        return {}
    out: Dict[str, int] = {}
    # IN 절이 너무 길어지지 않게 1000개씩 끊어서 조회
    uniq = [b for b in {b for b in buyma_ids if b}]
    for i in range(0, len(uniq), 1000):
        chunk = uniq[i:i + 1000]
        placeholders = ','.join(['%s'] * len(chunk))
        sql = (
            f"SELECT id, buyma_product_id FROM ace_products "
            f"WHERE buyma_product_id IN ({placeholders})"
        )
        with conn.cursor() as c:
            c.execute(sql, chunk)
            for row in c.fetchall():
                out[str(row[1])] = int(row[0])
    return out


def upsert_stats(rows: List[Dict]) -> None:
    """크롤링 결과를 buyma_product_stats에 UPSERT."""
    if not rows:
        log("UPSERT 대상 없음")
        return

    log(f"DB 접속 → {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    conn = pymysql.connect(**DB_CONFIG)
    try:
        # buyma_product_id 별 ace_product_id 매핑 한 번에 가져오기
        buyma_ids = [r['buyma_product_id'] for r in rows if r.get('buyma_product_id')]
        ace_map = _fetch_ace_id_map(conn, buyma_ids)
        log(f"ace_products 매칭: {len(ace_map)} / {len(buyma_ids)}건")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = """
            INSERT INTO buyma_product_stats
                (buyma_product_id, ace_product_id,
                 access_count, cart_count, favorite_count, stats_collected_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                ace_product_id    = VALUES(ace_product_id),
                access_count      = VALUES(access_count),
                cart_count        = VALUES(cart_count),
                favorite_count    = VALUES(favorite_count),
                stats_collected_at = VALUES(stats_collected_at)
        """
        params = [
            (
                r['buyma_product_id'],
                ace_map.get(r['buyma_product_id']),
                r.get('access_count'),
                r.get('cart_count'),
                r.get('favorite_count'),
                now,
            )
            for r in rows
            if r.get('buyma_product_id')
        ]

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
        description='바이마 셀러 전시목록 통계 수집 → buyma_product_stats UPSERT'
    )
    parser.add_argument('--max-pages', type=int, default=None,
                        help='테스트용: 페이지 N개만 (기본: 전체)')
    parser.add_argument('--start-page', type=int, default=1,
                        help='시작 페이지 (기본: 1). 중단된 곳부터 이어 받기용')
    args = parser.parse_args()

    log("=" * 60)
    log(f"바이마 자사 전시목록 통계 수집 시작 (start_page={args.start_page})")
    log("=" * 60)

    session = create_session()
    rows = crawl_all(session,
                     max_pages=args.max_pages,
                     start_page=args.start_page)

    if not rows:
        log("수집된 데이터 없음")
        return

    upsert_stats(rows)

    # 머지 캐시 파일 갱신 (manage_server/data_cache.json)
    # 화면(products.html)이 빠르게 받아 가도록 미리 만들어 둠.
    try:
        log("DB → 머지 → 캐시 파일 갱신 중...")
        manage_dir = os.path.join(os.path.dirname(SCRIPT_DIR), 'manage_server')
        sys.path.insert(0, manage_dir)
        from products_api import build_and_save_cache, CACHE_PATH
        cache_db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'autocommit'}
        payload = build_and_save_cache(cache_db_cfg)
        log(f"캐시 저장: {payload['count']}건 → {CACHE_PATH}")
    except Exception as e:
        log(f"캐시 갱신 실패 (수동으로 manage_server/build_cache.py 실행 필요): {e}", "WARN")

    log("=" * 60)
    log(f"완료: 크롤 {len(rows)}건 → DB 반영")
    log("=" * 60)


if __name__ == "__main__":
    main()