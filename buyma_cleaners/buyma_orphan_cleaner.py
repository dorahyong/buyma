# -*- coding: utf-8 -*-
"""
바이마 고아/유령 상품 정리 스크립트

1) 고아 상품: 바이마에 등록되어 있지만 ace_products 테이블에 없는 상품 → 바이마에서 삭제
2) 유령 상품: DB에 is_published=1인데 바이마에 실제로 없는 상품 → DB is_published=0 처리

흐름:
  Phase 1:   바이마 출품 리스트 크롤링 → 상품ID 전체 수집 (requests + BeautifulSoup)
  Phase 2-A: DB와 비교 → 고아 상품 추출 (바이마에만 있는 것)
  Phase 2-B: DB와 비교 → 유령 상품 추출 (DB에만 있는 것)
  Phase 3:   고아 상품 삭제 (내부 API → 쇼퍼 API)
  Phase 4:   유령 상품 DB 정리 (is_published=0)

사용법:
    # Step 1: 로그인 (최초 1회 - 브라우저 열림)
    python buyma_orphan_cleaner.py --login

    # Step 2: 크롤링 + DB 비교 (고아/유령 상품 목록 생성)
    python buyma_orphan_cleaner.py --scan

    # Step 3-A: 고아 상품 삭제 (먼저 dry-run)
    python buyma_orphan_cleaner.py --delete --dry-run
    python buyma_orphan_cleaner.py --delete

    # Step 3-B: 유령 상품 DB 정리 (먼저 dry-run)
    python buyma_orphan_cleaner.py --clean-ghost --dry-run
    python buyma_orphan_cleaner.py --clean-ghost

    # 전체 한번에
    python buyma_orphan_cleaner.py --scan --delete --clean-ghost

작성일: 2026-02-25
"""

import os
import sys
import json
import csv
import time
import argparse
from datetime import datetime
from typing import Dict, List, Set, Optional

import requests as req_lib
import pymysql
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# =====================================================
# 설정값
# =====================================================

DB_CONFIG = {
    'host': os.getenv('DB_HOST', '54.180.248.182'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'block'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': os.getenv('DB_NAME', 'buyma'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

BUYMA_BASE_URL = "https://www.buyma.com"

# 출품 리스트 URL (rows=100, 페이지당 100개)
BUYMA_LIST_URL_TEMPLATE = (
    "{base}/my/sell?duty_kind=all"
    "&facet=brand_id%2Ccate_pivot%2Cstatus%2Ctag_ids%2Cshop_labels%2Cstock_state"
    "&order=desc&page={{page}}&rows=100&sale_kind=all&sort=item_id"
    "&status=for_sale&timesale_kind=all"
).format(base=BUYMA_BASE_URL)

# 내부 API: 상품 상세 (reference_number 포함)
BUYMA_PRODUCT_API = "{base}/rorapi/sell/products/{{product_id}}?_={{ts}}"
BUYMA_PRODUCT_API = BUYMA_PRODUCT_API.format(base=BUYMA_BASE_URL)

# 바이마 쇼퍼 API (삭제용)
BUYMA_SHOPPER_API_URL = os.getenv(
    'BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/'
)
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')

# 파일 경로
COOKIE_FILE = "buyma_cookies.json"
BUYMA_IDS_FILE = "buyma_all_product_ids.json"
ORPHAN_IDS_FILE = "buyma_orphan_ids.json"
ORPHAN_CSV_FILE = "buyma_orphan_products.csv"
GHOST_IDS_FILE = "buyma_ghost_ids.json"
GHOST_CSV_FILE = "buyma_ghost_products.csv"

# 속도 설정
CRAWL_DELAY = 1.0         # 리스트 페이지 간 대기
API_DETAIL_DELAY = 0.5    # 내부 API 호출 간 대기
DELETE_DELAY = 1.5         # 삭제 API 호출 간 대기


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


# =====================================================
# 세션 관리
# =====================================================

def create_session() -> req_lib.Session:
    """쿠키가 적용된 requests 세션 생성"""
    if not os.path.exists(COOKIE_FILE):
        log(f"쿠키 파일 없음: {COOKIE_FILE}", "ERROR")
        log("먼저 --login으로 로그인해주세요.")
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
# Step 1: 로그인 (Playwright - 최초 1회만)
# =====================================================

async def login_and_save_cookies():
    """브라우저를 열어 수동 로그인 후 쿠키 저장"""
    from playwright.async_api import async_playwright

    log("브라우저를 열어 바이마에 로그인해주세요...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900}, locale="ja-JP"
        )
        page = await context.new_page()
        await page.goto("https://www.buyma.com/login/")
        log("로그인 페이지 열림. 로그인 후 마이페이지로 이동해주세요.")

        try:
            await page.wait_for_url("**/my/**", timeout=300000)
            log("로그인 확인!")
        except Exception:
            log("자동 감지 실패. 로그인 완료했으면 Enter를 눌러주세요.", "WARN")
            input(">>> Enter: ")

        cookies = await context.cookies()
        with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)

        log(f"쿠키 저장 완료: {COOKIE_FILE} ({len(cookies)}개)")
        await browser.close()


# =====================================================
# Phase 1: 리스트 크롤링 → 상품ID 수집
# =====================================================

def crawl_buyma_product_ids() -> List[Dict]:
    """
    바이마 출품 리스트 전체를 크롤링하여 상품ID를 수집.

    HTML 셀렉터:
      <input type="checkbox" name="chkitems" value="128400709">
      <td class="item_name"><p><a href="...">상품명</a></p></td>
    """
    session = create_session()
    all_products = []
    page_num = 1

    log("=" * 60)
    log("Phase 1: 바이마 출품 리스트 크롤링 (rows=100)")
    log("=" * 60)

    while True:
        url = BUYMA_LIST_URL_TEMPLATE.format(page=page_num)
        log(f"페이지 {page_num} 크롤링 중...")

        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except req_lib.RequestException as e:
            log(f"요청 실패: {e}", "ERROR")
            break

        if '/login' in resp.url:
            log("세션 만료! --login으로 다시 로그인해주세요.", "ERROR")
            break

        soup = BeautifulSoup(resp.text, 'html.parser')
        checkboxes = soup.select('input[name="chkitems"]')

        if not checkboxes:
            log(f"  → 상품 없음. 종료 (페이지 {page_num})")
            break

        page_products = []
        for cb in checkboxes:
            pid = cb.get('value', '').strip()
            if not pid:
                continue

            name = ""
            tr = cb.find_parent('tr')
            if tr:
                name_el = tr.select_one('td.item_name p a')
                if name_el:
                    name = name_el.get_text(strip=True)

            page_products.append({
                'buyma_product_id': pid,
                'name': name,
            })

        all_products.extend(page_products)
        log(f"  → {len(page_products)}개 (누적: {len(all_products)})")

        # 다음 페이지 확인
        if not soup.select_one('a[rel="next"]'):
            log(f"  → 마지막 페이지 (페이지 {page_num})")
            break

        page_num += 1
        time.sleep(CRAWL_DELAY)

    with open(BUYMA_IDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_products, f, ensure_ascii=False, indent=2)

    log(f"\n크롤링 완료! 총 {len(all_products)}개 → {BUYMA_IDS_FILE}")
    return all_products


# =====================================================
# Phase 2: DB 비교 → 고아 상품 추출
# =====================================================

def get_db_buyma_ids() -> Set[str]:
    """DB ace_products에서 buyma_product_id 전체 조회"""
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT buyma_product_id
                FROM ace_products
                WHERE buyma_product_id IS NOT NULL
                  AND buyma_product_id != ''
            """)
            return {str(r['buyma_product_id']).strip() for r in cursor.fetchall()}
    finally:
        conn.close()


def find_orphans(buyma_products: List[Dict]) -> List[Dict]:
    """바이마에만 있고 DB에 없는 고아 상품 추출"""
    log("=" * 60)
    log("Phase 2: DB 비교 → 고아 상품 추출")
    log("=" * 60)

    db_ids = get_db_buyma_ids()
    log(f"DB buyma_product_id: {len(db_ids)}개")

    orphans = [p for p in buyma_products if p['buyma_product_id'] not in db_ids]

    log(f"바이마 전체: {len(buyma_products)}개")
    log(f"DB에 있음: {len(buyma_products) - len(orphans)}개")
    log(f"★ 고아 상품: {len(orphans)}개")

    if orphans:
        log("\n--- 고아 상품 샘플 ---")
        for i, p in enumerate(orphans[:30], 1):
            log(f"  {i}. ID={p['buyma_product_id']} | {p['name'][:50]}")
        if len(orphans) > 30:
            log(f"  ... 외 {len(orphans) - 30}개")

    # JSON 저장
    with open(ORPHAN_IDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(orphans, f, ensure_ascii=False, indent=2)

    # CSV 저장
    with open(ORPHAN_CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['buyma_product_id', 'name', 'edit_url'])
        for p in orphans:
            w.writerow([
                p['buyma_product_id'], p['name'],
                f"{BUYMA_BASE_URL}/my/sell/{p['buyma_product_id']}/edit?tab=b"
            ])

    log(f"\n저장: {ORPHAN_IDS_FILE}, {ORPHAN_CSV_FILE}")
    return orphans


# =====================================================
# Phase 2-B: DB 비교 → 유령 상품 추출
#   DB에 is_published=1인데 바이마에 없는 상품
# =====================================================

def get_db_published_products() -> List[Dict]:
    """DB에서 is_published=1인 상품 목록 조회 (buyma_product_id + model_no)"""
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, buyma_product_id, model_no
                FROM ace_products
                WHERE buyma_product_id IS NOT NULL
                  AND buyma_product_id != ''
                  AND is_published = 1
            """)
            return cursor.fetchall()
    finally:
        conn.close()


def find_ghosts(buyma_products: List[Dict]) -> List[Dict]:
    """DB에 is_published=1이지만 바이마에 없는 유령 상품 추출"""
    log("=" * 60)
    log("Phase 2-B: DB 비교 → 유령 상품 추출")
    log("  (DB is_published=1 인데 바이마에 없는 상품)")
    log("=" * 60)

    # 바이마 크롤링 결과에서 ID set 생성
    buyma_id_set = {p['buyma_product_id'] for p in buyma_products}
    log(f"바이마 실제 출품: {len(buyma_id_set)}개")

    # DB에서 is_published=1인 상품 전체 조회
    db_published = get_db_published_products()
    log(f"DB is_published=1: {len(db_published)}개")

    # 유령 상품 = DB에는 published인데 바이마에 없음
    ghosts = [
        p for p in db_published
        if str(p['buyma_product_id']).strip() not in buyma_id_set
    ]

    log(f"★ 유령 상품: {len(ghosts)}개")

    if ghosts:
        log("\n--- 유령 상품 샘플 ---")
        for i, p in enumerate(ghosts[:30], 1):
            name = (p.get('model_no') or '')[:50]
            log(f"  {i}. DB id={p['id']} | buyma_id={p['buyma_product_id']} | {name}")
        if len(ghosts) > 30:
            log(f"  ... 외 {len(ghosts) - 30}개")

    # JSON 저장
    ghost_data = [
        {
            'db_id': p['id'],
            'buyma_product_id': str(p['buyma_product_id']),
            'model_no': p.get('model_no', ''),
        }
        for p in ghosts
    ]
    with open(GHOST_IDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(ghost_data, f, ensure_ascii=False, indent=2)

    # CSV 저장
    with open(GHOST_CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['db_id', 'buyma_product_id', 'model_no'])
        for g in ghost_data:
            w.writerow([g['db_id'], g['buyma_product_id'], g['model_no']])

    log(f"\n저장: {GHOST_IDS_FILE}, {GHOST_CSV_FILE}")
    return ghost_data


# =====================================================
# Phase 4: 유령 상품 DB 정리 (is_published=0)
# =====================================================

def clean_ghosts(dry_run: bool = False):
    """유령 상품의 is_published를 0으로 업데이트"""
    if not os.path.exists(GHOST_IDS_FILE):
        log("유령 상품 파일 없음. 먼저 --scan을 실행해주세요.", "ERROR")
        return

    with open(GHOST_IDS_FILE, 'r', encoding='utf-8') as f:
        ghosts = json.load(f)

    if not ghosts:
        log("정리할 유령 상품이 없습니다.")
        return

    total = len(ghosts)
    log("=" * 60)
    log(f"Phase 4: 유령 상품 DB 정리 ({total}개)")
    if dry_run:
        log("[DRY-RUN] 실제 업데이트하지 않습니다.")
    log("=" * 60)

    if dry_run:
        for i, g in enumerate(ghosts[:20], 1):
            name = (g.get('model_no') or '')[:40]
            log(f"  [DRY-RUN] {i}. DB id={g['db_id']} | {name} → is_published=0")
        if total > 20:
            log(f"  ... 외 {total - 20}개")
        log(f"\n[DRY-RUN] 총 {total}개가 is_published=0으로 변경 예정")
        return

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            db_ids = [g['db_id'] for g in ghosts]

            # 배치 업데이트 (100개씩)
            batch_size = 100
            updated = 0
            for start in range(0, len(db_ids), batch_size):
                batch = db_ids[start:start + batch_size]
                placeholders = ','.join(['%s'] * len(batch))
                cursor.execute(
                    f"UPDATE ace_products SET is_published = 0 WHERE id IN ({placeholders})",
                    batch
                )
                updated += cursor.rowcount
                log(f"  → {start + len(batch)}/{total} 처리 (업데이트: {updated}건)")

            conn.commit()

        log(f"\n유령 상품 정리 완료: {updated}건 is_published=0 처리")
    except Exception as e:
        conn.rollback()
        log(f"DB 업데이트 실패: {e}", "ERROR")
    finally:
        conn.close()


# =====================================================
# Phase 3: reference_number 조회 + 삭제
# =====================================================

def get_reference_number(session: req_lib.Session, product_id: str) -> Optional[str]:
    """
    바이마 내부 API로 reference_number를 조회합니다.
    
    GET /rorapi/sell/products/{product_id}?_={timestamp}
    → response.data.reference_number
    """
    ts = int(time.time() * 1000)
    url = BUYMA_PRODUCT_API.format(product_id=product_id, ts=ts)

    try:
        resp = session.get(
            url,
            headers={
                'Accept': 'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': f'{BUYMA_BASE_URL}/my/sell/{product_id}/edit?tab=b',
            },
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            ref = data.get('data', {}).get('reference_number', '')
            return ref if ref else None
        else:
            log(f"    내부 API 실패: {resp.status_code}", "WARN")
            return None

    except Exception as e:
        log(f"    내부 API 오류: {e}", "ERROR")
        return None


def delete_via_shopper_api(reference_number: str) -> bool:
    """바이마 쇼퍼 API로 상품 삭제 (control: delete)"""
    url = f"{BUYMA_SHOPPER_API_URL}api/v1/products"
    headers = {
        "Content-Type": "application/json",
        "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN
    }
    payload = {
        "product": {
            "control": "delete",
            "reference_number": reference_number
        }
    }

    try:
        resp = req_lib.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code in [200, 201, 202]:
            return True
        else:
            log(f"    삭제 API 실패: {resp.status_code} {resp.text[:100]}", "WARN")
            return False
    except Exception as e:
        log(f"    삭제 API 오류: {e}", "ERROR")
        return False


def delete_orphans(dry_run: bool = False):
    """
    고아 상품 삭제 실행

    1. 내부 API로 reference_number 조회
    2. 바이마 쇼퍼 API로 삭제
    """
    if not os.path.exists(ORPHAN_IDS_FILE):
        log("고아 상품 파일 없음. 먼저 --scan을 실행해주세요.", "ERROR")
        return

    with open(ORPHAN_IDS_FILE, 'r', encoding='utf-8') as f:
        orphans = json.load(f)

    if not orphans:
        log("삭제할 고아 상품이 없습니다.")
        return

    total = len(orphans)
    log("=" * 60)
    log(f"Phase 3: 고아 상품 삭제 ({total}개)")
    if dry_run:
        log("[DRY-RUN] 실제 삭제하지 않습니다.")
    log("=" * 60)

    if not dry_run and not BUYMA_ACCESS_TOKEN:
        log("BUYMA_ACCESS_TOKEN이 설정되지 않았습니다.", "ERROR")
        return

    session = create_session()

    success = 0
    failed = 0
    no_ref = 0

    for i, p in enumerate(orphans, 1):
        pid = p['buyma_product_id']
        name = p.get('name', '')[:40]
        log(f"\n[{i}/{total}] ID={pid} | {name}")

        # 1. reference_number 조회
        ref_num = get_reference_number(session, pid)

        if not ref_num:
            log(f"  → reference_number 없음. 스킵", "WARN")
            no_ref += 1
            time.sleep(API_DETAIL_DELAY)
            continue

        log(f"  → ref: {ref_num[:20]}...")

        if dry_run:
            log(f"  → [DRY-RUN] 삭제 예정")
            success += 1
            time.sleep(API_DETAIL_DELAY)
            continue

        # 2. 쇼퍼 API로 삭제
        if delete_via_shopper_api(ref_num):
            log(f"  → ✓ 삭제 성공")
            success += 1
        else:
            log(f"  → ✗ 삭제 실패", "WARN")
            failed += 1

        time.sleep(DELETE_DELAY)

        # 50건마다 중간 리포트
        if i % 50 == 0:
            log(f"\n--- 중간 리포트: {i}/{total} (성공 {success}, 실패 {failed}, ref없음 {no_ref}) ---")

    log("\n" + "=" * 60)
    log("삭제 완료")
    log(f"성공: {success}, 실패: {failed}, ref없음: {no_ref}")
    log("=" * 60)


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='바이마 고아/유령 상품 정리')
    parser.add_argument('--login', action='store_true',
                        help='브라우저 로그인 & 쿠키 저장 (최초 1회)')
    parser.add_argument('--scan', action='store_true',
                        help='크롤링 + DB 비교 → 고아/유령 상품 목록 생성')
    parser.add_argument('--delete', action='store_true',
                        help='고아 상품 삭제 (바이마에만 있는 것)')
    parser.add_argument('--clean-ghost', action='store_true',
                        help='유령 상품 DB 정리 (is_published=0)')
    parser.add_argument('--dry-run', action='store_true',
                        help='삭제/정리 없이 테스트')
    args = parser.parse_args()

    if not any([args.login, args.scan, args.delete, args.clean_ghost]):
        parser.print_help()
        return

    if args.login:
        import asyncio
        asyncio.run(login_and_save_cookies())

    if args.scan:
        products = crawl_buyma_product_ids()
        if products:
            find_orphans(products)
            find_ghosts(products)

    if args.delete:
        delete_orphans(dry_run=args.dry_run)

    if args.clean_ghost:
        clean_ghosts(dry_run=args.dry_run)


if __name__ == "__main__":
    main()