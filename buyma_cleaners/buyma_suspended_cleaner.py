# -*- coding: utf-8 -*-
"""
바이마 출품정지/비승인 상품 비활성화 스크립트

대상:
  - 출품정지중: https://www.buyma.com/my/sell/?status=suspended
  - 비승인:     https://www.buyma.com/my/sell/?status=not_approved

처리:
  Phase 1: 바이마 페이지 크롤링 → buyma_product_id 수집
  Phase 2: DB(ace_products) 매칭
  Phase 3: DB 업데이트 (status='suspended'/'not_approved', is_active=0)

사용법:
    # 로그인 (쿠키 갱신 - orphan_cleaner와 공유)
    python buyma_suspended_cleaner.py --login

    # 크롤링 + DB 매칭 결과 확인
    python buyma_suspended_cleaner.py --scan

    # 실제 DB 업데이트
    python buyma_suspended_cleaner.py --scan --apply

    # 특정 상태만
    python buyma_suspended_cleaner.py --scan --type suspended
    python buyma_suspended_cleaner.py --scan --type not_approved

작성일: 2026-04-06
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from typing import Dict, List

import requests as req_lib
import pymysql
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# =====================================================
# 설정값
# =====================================================

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

BUYMA_BASE_URL = "https://www.buyma.com"

# 크롤링 URL 템플릿 (status 부분만 변경)
BUYMA_LIST_URL_TEMPLATE = (
    "{base}/my/sell?order=desc&page={{page}}&rows=100&sort=item_id"
    "&status={{status}}"
).format(base=BUYMA_BASE_URL)

# 쿠키 파일 (orphan_cleaner와 공유)
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "buyma_cookies.json")

# 산출물 파일
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUSPENDED_FILE = os.path.join(SCRIPT_DIR, "buyma_suspended_products.json")
NOT_APPROVED_FILE = os.path.join(SCRIPT_DIR, "buyma_not_approved_products.json")

# 대상 상태 정의
TARGET_STATUSES = {
    'suspended': {
        'label': '출품정지중',
        'db_status': 'suspended',
        'output_file': SUSPENDED_FILE,
    },
    'not_approved': {
        'label': '비승인',
        'db_status': 'not_approved',
        'output_file': NOT_APPROVED_FILE,
    },
}

CRAWL_DELAY = 1.0


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
# Phase 1: 크롤링 → buyma_product_id 수집
# =====================================================

def crawl_products_by_status(session: req_lib.Session, status_key: str) -> List[Dict]:
    """특정 상태의 바이마 상품을 크롤링하여 buyma_product_id 수집"""
    config = TARGET_STATUSES[status_key]
    all_products = []
    page_num = 1

    log(f"  [{config['label']}] 크롤링 시작 (status={status_key})")

    while True:
        url = BUYMA_LIST_URL_TEMPLATE.format(page=page_num, status=status_key)

        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except req_lib.RequestException as e:
            log(f"  요청 실패: {e}", "ERROR")
            break

        if '/login' in resp.url:
            log("  세션 만료! --login으로 다시 로그인해주세요.", "ERROR")
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
        log(f"  → 페이지 {page_num}: {len(page_products)}개 (누적: {len(all_products)})")

        if not soup.select_one('a[rel="next"]'):
            break

        page_num += 1
        time.sleep(CRAWL_DELAY)

    # JSON 저장
    with open(config['output_file'], 'w', encoding='utf-8') as f:
        json.dump(all_products, f, ensure_ascii=False, indent=2)

    log(f"  [{config['label']}] 총 {len(all_products)}개 → {os.path.basename(config['output_file'])}")
    return all_products


# =====================================================
# Phase 2: DB 매칭
# =====================================================

def match_with_db(conn, products: List[Dict], status_key: str) -> List[Dict]:
    """크롤링된 상품을 DB와 매칭"""
    if not products:
        return []

    config = TARGET_STATUSES[status_key]
    buyma_ids = [p['buyma_product_id'] for p in products]
    placeholders = ','.join(['%s'] * len(buyma_ids))

    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT id, buyma_product_id, model_no, brand_name, name,
                   status, is_active, is_published, source_site
            FROM ace_products
            WHERE buyma_product_id IN ({placeholders})
        """, buyma_ids)
        db_rows = cursor.fetchall()

    # buyma_product_id → name 매핑 (크롤링 데이터에서)
    crawl_name_map = {p['buyma_product_id']: p['name'] for p in products}

    matched = []
    for row in db_rows:
        row['buyma_name'] = crawl_name_map.get(str(row['buyma_product_id']), '')
        row['target_status'] = config['db_status']
        matched.append(row)

    # DB에 없는 상품 (= orphan과 유사)
    db_ids = {str(r['buyma_product_id']) for r in db_rows}
    not_in_db = [p for p in products if p['buyma_product_id'] not in db_ids]

    log(f"  [{config['label']}] DB 매칭: {len(matched)}건, DB에 없음: {len(not_in_db)}건")

    return matched


# =====================================================
# Phase 3: DB 업데이트
# =====================================================

def apply_updates(conn, matched: List[Dict], dry_run: bool = False) -> Dict:
    """매칭된 상품의 status, is_active 업데이트"""
    results = {'updated': 0, 'skipped': 0, 'failed': 0}

    for row in matched:
        ace_id = row['id']
        current_status = row['status']
        current_active = row['is_active']
        target_status = row['target_status']

        # 이미 동일한 상태면 스킵
        if current_status == target_status and current_active == 0:
            results['skipped'] += 1
            continue

        log(f"  ace_id={ace_id} | {row['brand_name']} | {row['model_no']} | "
            f"status: {current_status}→{target_status}, is_active: {current_active}→0")

        if dry_run:
            results['updated'] += 1
            continue

        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ace_products
                    SET status = %s,
                        is_active = 0,
                        updated_at = NOW()
                    WHERE id = %s
                """, (target_status, ace_id))
            conn.commit()
            results['updated'] += 1
        except Exception as e:
            log(f"  → DB 업데이트 실패: {e}", "ERROR")
            conn.rollback()
            results['failed'] += 1

    return results


# =====================================================
# main
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='바이마 출품정지/비승인 상품 비활성화')
    parser.add_argument('--login', action='store_true', help='브라우저 로그인으로 쿠키 갱신')
    parser.add_argument('--scan', action='store_true', help='크롤링 + DB 매칭')
    parser.add_argument('--apply', action='store_true', help='실제 DB 업데이트 (--scan과 함께)')
    parser.add_argument('--type', type=str, choices=['suspended', 'not_approved'],
                        default=None, help='특정 상태만 처리 (미지정 시 둘 다)')
    args = parser.parse_args()

    if not any([args.login, args.scan]):
        parser.print_help()
        return

    # --login
    if args.login:
        import asyncio
        asyncio.run(login_and_save_cookies())
        if not args.scan:
            return

    # --scan
    if args.scan:
        dry_run = not args.apply
        status_keys = [args.type] if args.type else ['suspended', 'not_approved']

        log("=" * 60)
        log("바이마 출품정지/비승인 상품 비활성화")
        log(f"  대상: {', '.join(status_keys)}")
        log(f"  모드: {'DRY-RUN (--apply로 실행)' if dry_run else '실제 업데이트'}")
        log("=" * 60)

        session = create_session()
        conn = pymysql.connect(**DB_CONFIG)

        total_results = {'updated': 0, 'skipped': 0, 'failed': 0}

        try:
            for status_key in status_keys:
                config = TARGET_STATUSES[status_key]
                log("")
                log(f"--- {config['label']} ({status_key}) ---")

                # Phase 1: 크롤링
                products = crawl_products_by_status(session, status_key)
                if not products:
                    log(f"  [{config['label']}] 대상 없음")
                    continue

                # Phase 2: DB 매칭
                matched = match_with_db(conn, products, status_key)
                if not matched:
                    log(f"  [{config['label']}] DB 매칭 결과 없음")
                    continue

                # Phase 3: DB 업데이트
                results = apply_updates(conn, matched, dry_run=dry_run)
                for k in total_results:
                    total_results[k] += results[k]

        finally:
            conn.close()

        log("")
        log("=" * 60)
        if dry_run:
            log(f"[DRY-RUN] 업데이트 대상: {total_results['updated']}건, "
                f"이미 처리됨: {total_results['skipped']}건")
            if total_results['updated'] > 0:
                log("실제 반영하려면 --apply 옵션을 추가하세요.")
        else:
            log(f"완료: 업데이트 {total_results['updated']}건, "
                f"스킵 {total_results['skipped']}건, 실패 {total_results['failed']}건")
        log("=" * 60)


if __name__ == "__main__":
    main()
