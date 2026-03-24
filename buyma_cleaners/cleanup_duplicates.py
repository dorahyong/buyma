# -*- coding: utf-8 -*-
"""
중복 데이터 정리 스크립트

처리 순서:
  1. ace_products 중복 model_no → buyma delete API (published) → ace DB 삭제
  2. raw_scraped_data 중복 model_id → 전부 삭제
  3. 고아 ace_products (raw 없는데 ace 있는 것) → buyma delete API (published) → ace DB 삭제

사용법:
    python cleanup_duplicates.py --dry-run    # 테스트
    python cleanup_duplicates.py              # 실행
"""

import os
import sys
import io
import time
import argparse
from datetime import datetime

import requests
import pymysql
from dotenv import load_dotenv

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

BUYMA_MODE = int(os.getenv('BUYMA_MODE', 1))
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')
API_BASE_URL = BUYMA_API_BASE_URL if BUYMA_MODE == 1 else BUYMA_SANDBOX_URL


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def get_connection():
    return pymysql.connect(**DB_CONFIG)


def call_buyma_delete_api(reference_number):
    """바이마 상품 삭제 API"""
    url = f"{API_BASE_URL}api/v1/products"
    headers = {
        "Content-Type": "application/json",
        "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN
    }
    data = {"product": {"control": "delete", "reference_number": reference_number}}
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=30)
        if resp.status_code in [200, 201, 202]:
            return True, None
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def delete_ace_from_db(conn, ace_id):
    """ace_products 및 하위 테이블 삭제"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ace_product_variants WHERE ace_product_id = %s", (ace_id,))
        cur.execute("DELETE FROM ace_product_options WHERE ace_product_id = %s", (ace_id,))
        cur.execute("DELETE FROM ace_product_images WHERE ace_product_id = %s", (ace_id,))
        cur.execute("DELETE FROM ace_products WHERE id = %s", (ace_id,))


def buyma_delete_and_remove(conn, products, dry_run):
    """published 상품은 API 삭제 후 DB 삭제, unpublished는 DB만 삭제"""
    api_ok = 0
    api_fail = 0
    db_deleted = 0

    for p in products:
        ace_id = p['id']
        is_pub = p['is_published']
        ref_num = p.get('reference_number')

        if is_pub == 1 and ref_num:
            if dry_run:
                log(f"    [DRY-RUN] ace_id={ace_id} buyma={p.get('buyma_product_id')} → API 삭제 예정")
            else:
                ok, err = call_buyma_delete_api(ref_num)
                if ok:
                    log(f"    ace_id={ace_id} buyma={p.get('buyma_product_id')} → API 삭제 성공")
                    api_ok += 1
                else:
                    log(f"    ace_id={ace_id} → API 실패: {err}", "ERROR")
                    api_fail += 1
                time.sleep(0.5)

        if dry_run:
            log(f"    [DRY-RUN] ace_id={ace_id} (pub={is_pub}) → DB 삭제 예정")
        else:
            delete_ace_from_db(conn, ace_id)
            db_deleted += 1

    return api_ok, api_fail, db_deleted


def step1_ace_duplicates(dry_run):
    """Step 1: ace_products 중복 model_no 전부 삭제"""
    conn = get_connection()
    log("=" * 60)
    log("Step 1: ace_products 중복 model_no 정리")
    log("=" * 60)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_no, COUNT(*) as cnt
                FROM ace_products
                WHERE model_no IS NOT NULL AND model_no != '' AND is_active = 1
                GROUP BY model_no
                HAVING COUNT(*) > 1
            """)
            dup_models = cur.fetchall()
            log(f"중복 model_no: {len(dup_models)}개")

            total_api_ok = 0
            total_api_fail = 0
            total_db = 0

            for dup in dup_models:
                model_no = dup['model_no']
                cur.execute("""
                    SELECT id, buyma_product_id, reference_number, is_published, model_no
                    FROM ace_products
                    WHERE model_no = %s AND is_active = 1
                    ORDER BY id
                """, (model_no,))
                products = cur.fetchall()

                pub_cnt = sum(1 for p in products if p['is_published'] == 1)
                log(f"\n  model_no={model_no}: {len(products)}건 (등록 {pub_cnt})")

                api_ok, api_fail, db_del = buyma_delete_and_remove(conn, products, dry_run)
                total_api_ok += api_ok
                total_api_fail += api_fail
                total_db += db_del

                if not dry_run:
                    conn.commit()

            log(f"\n--- Step 1 결과 ---")
            log(f"  DB 삭제: {total_db}건")
            log(f"  API 성공: {total_api_ok}건, API 실패: {total_api_fail}건")
    finally:
        conn.close()


def step2_raw_duplicates(dry_run):
    """Step 2: raw_scraped_data 중복 model_id 전부 삭제"""
    conn = get_connection()
    log("")
    log("=" * 60)
    log("Step 2: raw_scraped_data 중복 model_id 정리")
    log("=" * 60)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_id, COUNT(*) as cnt
                FROM raw_scraped_data
                WHERE model_id IS NOT NULL AND model_id != ''
                GROUP BY model_id
                HAVING COUNT(*) > 1
            """)
            dup_models = cur.fetchall()
            log(f"중복 model_id: {len(dup_models)}개")

            total_raw_deleted = 0
            total_ace_deleted = 0

            for idx, dup in enumerate(dup_models, 1):
                model_id = dup['model_id']

                if idx % 50 == 0 or idx == 1:
                    log(f"  [{idx}/{len(dup_models)}] 처리 중... (raw 삭제 {total_raw_deleted}, ace 삭제 {total_ace_deleted})")

                # 해당 model_id의 모든 raw id
                cur.execute("""
                    SELECT id FROM raw_scraped_data WHERE model_id = %s
                """, (model_id,))
                raw_ids = [r['id'] for r in cur.fetchall()]

                if not raw_ids:
                    continue

                # 연결된 ace_products도 삭제 (Step 1에서 안 잡힌 것 포함)
                ph = ','.join(['%s'] * len(raw_ids))
                cur.execute(f"""
                    SELECT id, buyma_product_id, reference_number, is_published, model_no
                    FROM ace_products WHERE raw_data_id IN ({ph})
                """, raw_ids)
                linked_ace = cur.fetchall()

                if dry_run:
                    log(f"  model_id={model_id}: raw {len(raw_ids)}건 삭제, ace {len(linked_ace)}건 삭제 예정")
                else:
                    # ace 먼저 삭제
                    for ace in linked_ace:
                        if ace['is_published'] == 1 and ace.get('reference_number'):
                            ok, err = call_buyma_delete_api(ace['reference_number'])
                            if ok:
                                log(f"    ace_id={ace['id']} → API 삭제 성공")
                            else:
                                log(f"    ace_id={ace['id']} → API 실패: {err}", "ERROR")
                            time.sleep(0.5)
                        delete_ace_from_db(conn, ace['id'])

                    # raw 삭제
                    cur.execute(f"DELETE FROM raw_scraped_data WHERE id IN ({ph})", raw_ids)

                    total_ace_deleted += len(linked_ace)
                    total_raw_deleted += len(raw_ids)

                    conn.commit()

            log(f"\n--- Step 2 결과 ---")
            log(f"  raw 삭제: {total_raw_deleted}건")
            log(f"  연결 ace 삭제: {total_ace_deleted}건")
    finally:
        conn.close()


def step3_orphan_ace(dry_run):
    """Step 3: 고아 ace_products (raw 없는데 ace 있는 것) 삭제"""
    conn = get_connection()
    log("")
    log("=" * 60)
    log("Step 3: 고아 ace_products 정리 (raw 없는 ace)")
    log("=" * 60)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ap.id, ap.buyma_product_id, ap.reference_number,
                       ap.is_published, ap.model_no, ap.raw_data_id
                FROM ace_products ap
                LEFT JOIN raw_scraped_data rsd ON ap.raw_data_id = rsd.id
                WHERE rsd.id IS NULL
            """)
            orphans = cur.fetchall()
            log(f"고아 ace_products: {len(orphans)}건")

            if not orphans:
                log("정리할 고아 데이터 없음")
                return

            pub_cnt = sum(1 for o in orphans if o['is_published'] == 1)
            log(f"  등록됨: {pub_cnt}건, 미등록: {len(orphans) - pub_cnt}건")

            api_ok, api_fail, db_del = buyma_delete_and_remove(conn, orphans, dry_run)

            if not dry_run:
                conn.commit()

            log(f"\n--- Step 3 결과 ---")
            log(f"  DB 삭제: {db_del}건")
            log(f"  API 성공: {api_ok}건, API 실패: {api_fail}건")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='중복 데이터 정리')
    parser.add_argument('--dry-run', action='store_true', help='테스트 모드')
    args = parser.parse_args()

    if args.dry_run:
        log("*** DRY-RUN 모드 ***", "WARNING")

    step1_ace_duplicates(args.dry_run)
    step2_raw_duplicates(args.dry_run)
    step3_orphan_ace(args.dry_run)

    log("\n" + "=" * 60)
    log("전체 정리 완료!")
    log("=" * 60)


if __name__ == "__main__":
    main()
