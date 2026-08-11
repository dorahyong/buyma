# -*- coding: utf-8 -*-
"""
미게시인데 buyma_product_id 가 남은 listing 을 바이마에서 삭제 (buyma_listings 단일권위)

대상:
  - buyma_listings.is_published = 0
  - buyma_product_id IS NOT NULL
  - status IN ('deleted', 'fail')   ← 영구 제거 의도만

제외 (절대 건드리지 않음):
  - status='soldout' 출품정지 — buyma_id·게시일수 유지를 위해 id 를 남겨 둔 정상 상태
  - exception_reason 있는 출품정지(low_view 등)도 soldout 경로라 제외됨

원인 예: webhook fail / 수동 deleted 표시 후 바이마 페이지만 남은 경우.

사용법:
    python buyma_unpublished_cleaner.py --count
    python buyma_unpublished_cleaner.py --dry-run
    python buyma_unpublished_cleaner.py --limit 10
    python buyma_unpublished_cleaner.py --brand ALYX
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import time
import random
from datetime import datetime
from typing import Dict, List, Optional

import pymysql
import requests
from dotenv import load_dotenv

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

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

REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.0

# 영구 제거 의도만. soldout 은 재출품용 id 보존.
TARGET_STATUSES = ('deleted', 'fail')


def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def get_connection():
    return pymysql.connect(**DB_CONFIG)


def fetch_targets(conn, brand: str = None, limit: int = None) -> List[Dict]:
    """미게시 + buyma_id 잔존 + deleted/fail listing."""
    with conn.cursor() as cursor:
        sql = """
            SELECT id, reference_number, locked_reference_number, buyma_product_id,
                   model_no, brand_name, name, status, is_active
            FROM buyma_listings
            WHERE is_published = 0
              AND buyma_product_id IS NOT NULL
              AND status IN %s
        """
        params: list = [TARGET_STATUSES]

        if brand:
            sql += " AND UPPER(brand_name) LIKE %s"
            params.append(f"%{brand.upper()}%")

        sql += " ORDER BY id"

        if limit:
            sql += " LIMIT %s"
            params.append(limit)

        cursor.execute(sql, params)
        return list(cursor.fetchall())


def _ref_of(listing: Dict) -> Optional[str]:
    return listing.get('locked_reference_number') or listing.get('reference_number')


def call_buyma_delete_api(reference_number: str) -> Dict:
    """바이마 상품 삭제 API 호출"""
    url = f"{BUYMA_API_BASE_URL}api/v1/products"
    headers = {
        "Content-Type": "application/json",
        "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN,
    }
    request_data = {
        "product": {
            "control": "delete",
            "reference_number": reference_number,
        }
    }

    try:
        response = requests.post(url, headers=headers, json=request_data, timeout=30)
        if response.status_code in [200, 201, 202]:
            return {"success": True, "status_code": response.status_code}
        return {
            "success": False,
            "status_code": response.status_code,
            "error": response.text[:200],
        }
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timeout"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def delete_and_update_db(conn, listing: Dict) -> bool:
    """바이마 삭제 후 listing 정체성 정리."""
    listing_id = listing['id']
    ref = _ref_of(listing)
    if not ref:
        log(f"  → reference_number 없음. 스킵", "WARN")
        return False

    log(f"  → 바이마 삭제 API 호출 중... (reference={ref[:24]}...)")
    result = call_buyma_delete_api(ref)

    if result.get('success'):
        log(f"  → 삭제 요청 성공")
    else:
        log(f"  → 삭제 요청 실패: {result.get('error', 'Unknown')}", "WARN")
        # 실패해도 DB 잔존 id 는 정리 (바이마에 이미 없을 수 있음) — 옛 동작 유지

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE buyma_listings
                SET buyma_product_id = NULL,
                    is_published = 0,
                    status = 'deleted',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (listing_id,),
            )
            cursor.execute(
                """
                INSERT INTO buyma_listing_api_logs
                    (buyma_listing_id, api_request_json, api_response_json, last_api_call_at)
                VALUES (%s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    api_request_json = VALUES(api_request_json),
                    api_response_json = VALUES(api_response_json),
                    last_api_call_at = NOW()
                """,
                (
                    listing_id,
                    json.dumps({
                        'control': 'delete',
                        'reference_number': ref,
                        'reason': 'unpublished_cleaner',
                    }, ensure_ascii=False),
                    json.dumps({
                        'deleted_reason': 'unpublished_cleaner',
                        'api_result': result,
                        'deleted_at': datetime.now().isoformat(),
                    }, ensure_ascii=False),
                ),
            )
            conn.commit()
        log(f"  → DB 정리 완료 (listing#{listing_id} buyma_product_id=NULL, status=deleted)")
        return True
    except Exception as e:
        log(f"  → DB 업데이트 실패: {e}", "ERROR")
        conn.rollback()
        return False


def main():
    parser = argparse.ArgumentParser(
        description='미게시 listing 중 deleted/fail 의 바이마 잔존 페이지 삭제'
    )
    parser.add_argument('--count', action='store_true', help='대상 건수만 확인')
    parser.add_argument('--dry-run', action='store_true', help='삭제 대상 목록만 출력')
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    args = parser.parse_args()

    log("=" * 60)
    log("미게시 잔존 삭제 (buyma_unpublished_cleaner → buyma_listings)")
    log(f"  대상 status: {', '.join(TARGET_STATUSES)}  (soldout 제외)")
    log(f"  모드: {'건수 확인' if args.count else '목록 확인' if args.dry_run else '실제 삭제'}")
    if args.brand:
        log(f"  브랜드: {args.brand}")
    if args.limit:
        log(f"  최대: {args.limit}건")
    log("=" * 60)

    if not args.count and not args.dry_run and not BUYMA_ACCESS_TOKEN:
        log("BUYMA_ACCESS_TOKEN이 설정되지 않았습니다.", "ERROR")
        return

    conn = get_connection()
    try:
        targets = fetch_targets(conn, brand=args.brand, limit=args.limit)

        if args.count:
            log(f"대상 listing: {len(targets)}건")
            if targets:
                brand_counts: Dict[str, int] = {}
                status_counts: Dict[str, int] = {}
                for t in targets:
                    b = t['brand_name'] or '(없음)'
                    brand_counts[b] = brand_counts.get(b, 0) + 1
                    status_counts[t['status']] = status_counts.get(t['status'], 0) + 1
                log("status별:")
                for s, cnt in sorted(status_counts.items(), key=lambda x: -x[1]):
                    log(f"  {s}: {cnt}건")
                log("브랜드별:")
                for b, cnt in sorted(brand_counts.items(), key=lambda x: -x[1])[:20]:
                    log(f"  {b}: {cnt}건")
            return

        if not targets:
            log("대상 listing이 없습니다.")
            return

        log(f"대상 listing: {len(targets)}건")
        log("")

        success_count = 0
        fail_count = 0
        skip_count = 0

        for idx, listing in enumerate(targets, 1):
            ref = _ref_of(listing)
            log(
                f"[{idx}/{len(targets)}] listing#{listing['id']} "
                f"status={listing['status']} model={listing['model_no']} "
                f"brand={listing['brand_name']} buyma_id={listing['buyma_product_id']} "
                f"ref={'yes' if ref else 'NO'}"
            )

            if args.dry_run:
                if not ref:
                    skip_count += 1
                continue

            ok = delete_and_update_db(conn, listing)
            if ok:
                success_count += 1
            else:
                fail_count += 1

            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        log("")
        log("=" * 60)
        if args.dry_run:
            log(f"[DRY-RUN] 대상: {len(targets)}건 (ref없음 {skip_count})")
        else:
            log(f"완료: 성공 {success_count}건, 실패 {fail_count}건")
        log("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
