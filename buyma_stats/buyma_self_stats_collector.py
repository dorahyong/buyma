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

import requests as req_lib
from bs4 import BeautifulSoup

# 표준 출력 인코딩 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


# =====================================================
# 설정
# =====================================================

BUYMA_BASE_URL = "https://www.buyma.com"

# 출품 중(전시목록) 리스트. tab=b 화면이 호출하는 SSR URL과 동일한 파라미터셋.
BUYMA_LIST_URL_TEMPLATE = (
    "{base}/my/sell?duty_kind=all"
    "&facet=brand_id%2Ccate_pivot%2Cstatus%2Ctag_ids%2Cshop_labels%2Cstock_state"
    "&order=desc&page={{page}}&rows=100&sale_kind=all&sort=item_id"
    "&status=for_sale&timesale_kind=all"
).format(base=BUYMA_BASE_URL)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 쿠키는 buyma_cleaners 폴더와 공유 (cleaner 쪽에서 --login 한 번 하면 같이 적용됨)
COOKIE_FILE = os.path.normpath(
    os.path.join(SCRIPT_DIR, '..', 'buyma_cleaners', 'buyma_cookies.json')
)

OUTPUT_DIR = SCRIPT_DIR
CRAWL_DELAY = 1.0


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

def crawl_all(session: req_lib.Session, max_pages: Optional[int] = None) -> List[Dict]:
    all_rows: List[Dict] = []
    page_num = 1

    while True:
        if max_pages and page_num > max_pages:
            log(f"max-pages={max_pages} 도달. 중단")
            break

        url = BUYMA_LIST_URL_TEMPLATE.format(page=page_num)
        log(f"페이지 {page_num} 요청...")

        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except req_lib.RequestException as e:
            log(f"요청 실패: {e}", "ERROR")
            break

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
# main
# =====================================================

def main():
    parser = argparse.ArgumentParser(
        description='바이마 셀러 전시목록 통계 수집 (조회/장바구니/찜 + 부가 정보)'
    )
    parser.add_argument('--max-pages', type=int, default=None,
                        help='테스트용: 페이지 N개만 (기본: 전체)')
    parser.add_argument('--out', type=str, default=None,
                        help='출력 JSON 경로 (기본: buyma_self_stats_YYYYMMDD_HHMM.json)')
    args = parser.parse_args()

    log("=" * 60)
    log("바이마 자사 전시목록 통계 수집 시작")
    log("=" * 60)

    session = create_session()
    rows = crawl_all(session, max_pages=args.max_pages)

    if not rows:
        log("수집된 데이터 없음")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = args.out or os.path.join(
        OUTPUT_DIR, f'buyma_self_stats_{ts}.json'
    )

    payload = {
        'collected_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(rows),
        'items': rows,
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # HTML 프리뷰가 file:// 에서도 읽을 수 있도록 latest 사본을 .json/.js 둘 다 떨굼
    latest_json = os.path.join(OUTPUT_DIR, 'buyma_self_stats_latest.json')
    latest_js = os.path.join(OUTPUT_DIR, 'buyma_self_stats_latest.js')
    with open(latest_json, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(latest_js, 'w', encoding='utf-8') as f:
        f.write('window.STATS_DATA = ')
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write(';\n')

    log("=" * 60)
    log(f"완료: {len(rows)}건 → {out_path}")
    log(f"        latest 사본 → {os.path.basename(latest_json)}, {os.path.basename(latest_js)}")
    log("=" * 60)


if __name__ == "__main__":
    main()