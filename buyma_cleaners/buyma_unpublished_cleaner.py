# -*- coding: utf-8 -*-
"""
is_published=0이면서 buyma_product_id가 있는 상품을 바이마에서 삭제하는 스크립트

대상: DB에서는 미등록(is_published=0)으로 되어있지만, 바이마에는 실제로 존재하는 상품
원인: webhook에서 fail_to_update 시 is_published=0으로 변경된 경우 등

사용법:
    python buyma_unpublished_cleaner.py --count              # 대상 건수만 확인
    python buyma_unpublished_cleaner.py --dry-run             # 삭제 대상 목록 출력 (실행 안함)
    python buyma_unpublished_cleaner.py                       # 실제 삭제 실행
    python buyma_unpublished_cleaner.py --brand ALYX          # 특정 브랜드만
    python buyma_unpublished_cleaner.py --limit 10            # 최대 N개만

작성일: 2026-03-19
"""

import os
import sys
import json
import argparse
import time
import random
from datetime import datetime
from typing import Dict, List

import pymysql
import requests
from dotenv import load_dotenv

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# DB 연결 정보
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

# 바이마 API 설정
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')

# 요청 간 딜레이
REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.0


def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def get_connection():
    return pymysql.connect(**DB_CONFIG)


def fetch_targets(conn, brand: str = None, limit: int = None) -> List[Dict]:
    """is_published=0이면서 buyma_product_id가 있는 상품 조회"""
    with conn.cursor() as cursor:
        sql = """
            SELECT id, reference_number, buyma_product_id, model_no, brand_name, name, status,
                   source_site, is_active
            FROM ace_products
            WHERE is_published = 0
              AND buyma_product_id IS NOT NULL
        """
        params = []

        if brand:
            sql += " AND UPPER(brand_name) LIKE %s"
            params.append(f"%{brand.upper()}%")

        sql += " ORDER BY id"

        if limit:
            sql += " LIMIT %s"
            params.append(limit)

        cursor.execute(sql, params)
        return cursor.fetchall()


def call_buyma_delete_api(reference_number: str) -> Dict:
    """바이마 상품 삭제 API 호출"""
    url = f"{BUYMA_API_BASE_URL}api/v1/products"
    headers = {
        "Content-Type": "application/json",
        "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN
    }
    request_data = {
        "product": {
            "control": "delete",
            "reference_number": reference_number
        }
    }

    try:
        response = requests.post(url, headers=headers, json=request_data, timeout=30)
        if response.status_code in [200, 201, 202]:
            return {"success": True, "status_code": response.status_code}
        else:
            return {"success": False, "status_code": response.status_code, "error": response.text[:200]}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timeout"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def delete_and_update_db(conn, product: Dict) -> bool:
    """바이마에서 삭제 후 DB 업데이트"""
    product_id = product['id']
    reference_number = product['reference_number']

    # 1. 바이마 API 삭제 호출
    log(f"  → 바이마 삭제 API 호출 중... (reference={reference_number})")
    result = call_buyma_delete_api(reference_number)

    if result.get('success'):
        log(f"  → 삭제 요청 성공")
    else:
        log(f"  → 삭제 요청 실패: {result.get('error', 'Unknown')}", "WARN")
        # 실패해도 DB는 정리 (바이마에 이미 없을 수 있음)

    # 2. DB 업데이트
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE ace_products
                SET is_active = 0,
                    is_published = 0,
                    is_buyma_locked = 0,
                    buyma_product_id = NULL,
                    status = 'deleted',
                    updated_at = NOW()
                WHERE id = %s
            """, (product_id,))
            cursor.execute("""
                INSERT INTO ace_product_api_logs (ace_product_id, api_response_json, last_api_call_at)
                VALUES (%s, %s, NOW())
                ON DUPLICATE KEY UPDATE api_response_json = VALUES(api_response_json), last_api_call_at = NOW()
            """, (product_id, json.dumps({
                'deleted_reason': 'unpublished_cleaner',
                'api_result': result,
                'deleted_at': datetime.now().isoformat()
            }, ensure_ascii=False)))
            conn.commit()
        log(f"  → DB 비활성화 완료 (is_active=0, buyma_product_id=NULL)")
        return True
    except Exception as e:
        log(f"  → DB 업데이트 실패: {e}", "ERROR")
        conn.rollback()
        return False


def main():
    parser = argparse.ArgumentParser(description='is_published=0 & buyma_product_id 있는 상품 바이마 삭제')
    parser.add_argument('--count', action='store_true', help='대상 건수만 확인')
    parser.add_argument('--dry-run', action='store_true', help='삭제 대상 목록만 출력 (실행 안함)')
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    args = parser.parse_args()

    log("=" * 60)
    log("미등록 상품 바이마 삭제 (buyma_unpublished_cleaner)")
    log(f"  모드: {'건수 확인' if args.count else '목록 확인' if args.dry_run else '실제 삭제'}")
    if args.brand:
        log(f"  브랜드: {args.brand}")
    if args.limit:
        log(f"  최대: {args.limit}건")
    log("=" * 60)

    conn = get_connection()
    try:
        targets = fetch_targets(conn, brand=args.brand, limit=args.limit)

        if args.count:
            log(f"대상 상품: {len(targets)}건")
            if targets:
                # 브랜드별 집계
                brand_counts = {}
                for t in targets:
                    b = t['brand_name'] or '(없음)'
                    brand_counts[b] = brand_counts.get(b, 0) + 1
                log("브랜드별:")
                for b, cnt in sorted(brand_counts.items(), key=lambda x: -x[1]):
                    log(f"  {b}: {cnt}건")
            return

        if not targets:
            log("대상 상품이 없습니다.")
            return

        log(f"대상 상품: {len(targets)}건")
        log("")

        success_count = 0
        fail_count = 0

        for idx, product in enumerate(targets, 1):
            log(f"[{idx}/{len(targets)}] ace_id={product['id']}, model_no={product['model_no']}, "
                f"brand={product['brand_name']}, buyma_id={product['buyma_product_id']}, "
                f"status={product['status']}")

            if args.dry_run:
                continue

            ok = delete_and_update_db(conn, product)
            if ok:
                success_count += 1
            else:
                fail_count += 1

            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        log("")
        log("=" * 60)
        if args.dry_run:
            log(f"[DRY-RUN] 대상: {len(targets)}건 (실행 안함)")
        else:
            log(f"완료: 성공 {success_count}건, 실패 {fail_count}건")
        log("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
