# -*- coding: utf-8 -*-
"""
mall_brands.is_active=0 또는 mall_categories.is_active=0 매핑에 연결된 ace_products 정리.

배경:
  - 검수자가 mall_brands/mall_categories에서 사용 불가로 끈(is_active=0) brand/category가 있음.
  - 그런데 그 매핑을 가진 raw로 만들어진 ace_products는 과거에 들어가 있어 그대로 남아있음.
  - 일부는 BUYMA에 라이브 등록(is_published=1) 또는 ghost(buyma_product_id 잔존) 상태.

동작:
  1. ace_products에서 mall_brands/mall_categories.is_active=0 매핑에 걸리는 행 SELECT
  2. buyma_product_id 있으면 → BUYMA delete API 호출 (실패해도 DB는 정리)
  3. ace_product_api_logs (FK) → ace_products 순으로 DB DELETE
  4. raw_scraped_data 는 건드리지 않음 (게이트가 막으면 재변환 안 됨)

전제:
  - converter 게이트(미매핑 raw skip)가 들어간 후에 돌리는 게 안전.
  - 이번 회는 cron 정지 상태에서 단발 실행으로 가는 것도 OK.

사용법:
  python buyma_inactive_mapping_cleaner.py --count       # 카운트만
  python buyma_inactive_mapping_cleaner.py --dry-run     # 목록 미리보기, 변경 없음
  python buyma_inactive_mapping_cleaner.py               # 실제 실행
  python buyma_inactive_mapping_cleaner.py --limit 50    # 점진 실행
"""

import os
import sys
import json
import time
import random
import argparse
from datetime import datetime
from typing import Dict, List

import pymysql
import requests
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
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
    'cursorclass': pymysql.cursors.DictCursor,
}

BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')

REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.6


def log(msg: str, level: str = 'INFO') -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] [{level}] {msg}', flush=True)


def get_connection():
    return pymysql.connect(**DB_CONFIG)


def fetch_targets(conn, limit=None) -> List[Dict]:
    """mall_brands 또는 mall_categories.is_active=0 매핑에 연결된 ace 행."""
    with conn.cursor() as c:
        sql = """
            SELECT DISTINCT
                a.id, a.reference_number, a.buyma_product_id,
                a.brand_name, a.is_published, a.is_active,
                a.source_site, a.status,
                r.brand_name_en, r.category_path,
                CASE WHEN mb.is_active = 0 THEN 1 ELSE 0 END AS hit_brand,
                CASE WHEN mc.is_active = 0 THEN 1 ELSE 0 END AS hit_category
            FROM ace_products a
            JOIN raw_scraped_data r ON r.id = a.raw_data_id
            LEFT JOIN mall_brands mb
              ON mb.mall_name = r.source_site
             AND TRIM(mb.raw_brand_name) = TRIM(r.brand_name_en)
             AND mb.is_active = 0
            LEFT JOIN mall_categories mc
              ON mc.mall_name = r.source_site
             AND mc.full_path = r.category_path
             AND mc.is_active = 0
            WHERE mb.is_active = 0 OR mc.is_active = 0
            ORDER BY a.is_published DESC, a.id
        """
        params = []
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        c.execute(sql, params)
        return c.fetchall()


def call_buyma_delete_api(reference_number: str) -> Dict:
    url = f"{BUYMA_API_BASE_URL}api/v1/products"
    headers = {
        "Content-Type": "application/json",
        "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN,
    }
    payload = {
        "product": {
            "control": "delete",
            "reference_number": reference_number,
        }
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201, 202):
            return {"success": True, "status_code": resp.status_code}
        return {"success": False, "status_code": resp.status_code, "error": resp.text[:200]}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timeout"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def delete_ace_row(conn, ace_id: int) -> None:
    """FK 순서대로 DELETE: api_logs → ace_products."""
    with conn.cursor() as c:
        c.execute("DELETE FROM ace_product_api_logs WHERE ace_product_id = %s", (ace_id,))
        c.execute("DELETE FROM ace_products WHERE id = %s", (ace_id,))
    conn.commit()


def main():
    ap = argparse.ArgumentParser(
        description='mall_brands/categories.is_active=0 매핑에 연결된 ace 정리'
    )
    ap.add_argument('--count', action='store_true', help='카운트만 확인 (실행 없음)')
    ap.add_argument('--dry-run', action='store_true', help='목록 미리보기, 변경 없음')
    ap.add_argument('--limit', type=int, default=None, help='최대 처리 건수 (점진 실행용)')
    args = ap.parse_args()

    log("=" * 60)
    log("inactive mapping cleaner (mall_brands/categories.is_active=0)")
    mode = '카운트' if args.count else ('dry-run' if args.dry_run else '실행')
    log(f"  모드: {mode}")
    if args.limit:
        log(f"  limit: {args.limit}")
    log("=" * 60)

    conn = get_connection()
    try:
        targets = fetch_targets(conn, limit=args.limit)
        total = len(targets)
        pub_cnt = sum(1 for t in targets if t['is_published'] == 1)
        with_buyma_id = sum(1 for t in targets if t['buyma_product_id'])
        hit_brand = sum(1 for t in targets if t['hit_brand'])
        hit_cat = sum(1 for t in targets if t['hit_category'])

        log(f"대상: {total}")
        log(f"  - is_published=1: {pub_cnt}")
        log(f"  - buyma_product_id 보유 (API delete 시도 대상): {with_buyma_id}")
        log(f"  - brand 매핑이 is_active=0: {hit_brand}")
        log(f"  - category 매핑이 is_active=0: {hit_cat}")

        if args.count or not targets:
            return

        if args.dry_run:
            for i, t in enumerate(targets[:30], 1):
                tag = []
                if t['hit_brand']:
                    tag.append('B')
                if t['hit_category']:
                    tag.append('C')
                log(
                    f"  [{i}] ace_id={t['id']} pub={t['is_published']} "
                    f"buyma={t['buyma_product_id']} ref={t['reference_number']} "
                    f"[{'/'.join(tag)}] brand='{t['brand_name_en']}' "
                    f"path='{(t['category_path'] or '')[:30]}'"
                )
            if total > 30:
                log(f"  ... 외 {total - 30}건")
            log(f"[DRY-RUN] 총 {total}건 (실제 변경 없음)")
            return

        ok_api = 0
        fail_api = 0
        db_only = 0
        db_fail = 0

        for i, t in enumerate(targets, 1):
            ace_id = t['id']
            ref = t['reference_number']
            buyma_id = t['buyma_product_id']

            try:
                if buyma_id and ref:
                    log(f"[{i}/{total}] ace_id={ace_id} pub={t['is_published']} buyma={buyma_id} → API delete")
                    res = call_buyma_delete_api(ref)
                    if res.get('success'):
                        ok_api += 1
                        log("  → API OK")
                    else:
                        fail_api += 1
                        log(f"  → API 실패: {str(res.get('error', ''))[:120]}", "WARN")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                else:
                    db_only += 1
                    log(f"[{i}/{total}] ace_id={ace_id} (buyma_id 없음) → DB DELETE only")

                delete_ace_row(conn, ace_id)
            except Exception as e:
                db_fail += 1
                log(f"[{i}/{total}] ace_id={ace_id} 오류: {e}", "ERROR")
                try:
                    conn.rollback()
                except Exception:
                    pass

        log("=" * 60)
        log(f"완료: API OK {ok_api}, API 실패 {fail_api}, DB only {db_only}, DB 오류 {db_fail}")
        log("=" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
