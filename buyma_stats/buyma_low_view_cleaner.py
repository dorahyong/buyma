# -*- coding: utf-8 -*-
"""
30일 경과 + 조회수 0 인 상품을 바이마에서 삭제하는 배치

대상:
  - ace_products.is_active = 1
  - ace_products.is_published = 1
  - ace_products.exception_reason IS NULL
  - ace_products.buyma_registered_at < NOW() - INTERVAL N DAY
  - buyma_product_stats.access_count = 0

동작 (옵션 A — webhook 기반):
  1. 바이마 delete API 호출
  2. API 응답 success → DB: is_active = 0, exception_reason = 'low_view_30d'
     (status / is_published 은 안 건드림 — webhook(server.py:51)이 'deleted' / 0 으로 자동 처리)
  3. API 응답 fail → 로그만 (다음 실행 때 재시도)

사용법:
    python buyma_low_view_cleaner.py --count                # 대상 건수만
    python buyma_low_view_cleaner.py --dry-run              # 대상 목록만 출력
    python buyma_low_view_cleaner.py --limit 50             # 최대 50건 실제 삭제
    python buyma_low_view_cleaner.py --days 60 --limit 50   # 60일 경과로 변경
"""

import os
import sys
import json
import argparse
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pymysql
import requests
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')

REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.0

EXCEPTION_REASON = 'low_view_30d'

LOG_DIR = ROOT / 'logs'
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"low_view_cleaner_{datetime.now().strftime('%Y%m%d')}.log"


def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def get_connection():
    return pymysql.connect(**DB_CONFIG)


def fetch_targets(conn, days: int, limit: int = None) -> List[Dict]:
    """조건 매치 row 조회"""
    with conn.cursor() as cursor:
        sql = """
            SELECT ap.id, ap.reference_number, ap.buyma_product_id, ap.model_no,
                   ap.brand_name, ap.name, ap.source_site, ap.status,
                   ap.buyma_registered_at, bps.access_count
            FROM ace_products ap
            JOIN buyma_product_stats bps ON bps.buyma_product_id = ap.buyma_product_id
            WHERE ap.is_active = 1
              AND ap.is_published = 1
              AND ap.exception_reason IS NULL
              AND ap.buyma_registered_at < NOW() - INTERVAL %s DAY
              AND bps.access_count = 0
            ORDER BY ap.buyma_registered_at ASC
        """
        params = [days]

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


def mark_exception(conn, product_id: int, api_result: Dict) -> bool:
    """API success 시 — DB: is_active=0, exception_reason='low_view_30d' (status/is_published 은 안 건드림)"""
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE ace_products
                SET is_active = 0,
                    exception_reason = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (EXCEPTION_REASON, product_id))
            cursor.execute("""
                INSERT INTO ace_product_api_logs (ace_product_id, api_response_json, last_api_call_at)
                VALUES (%s, %s, NOW())
                ON DUPLICATE KEY UPDATE api_response_json = VALUES(api_response_json), last_api_call_at = NOW()
            """, (product_id, json.dumps({
                'cleaner': 'low_view_cleaner',
                'exception_reason': EXCEPTION_REASON,
                'api_result': api_result,
                'requested_at': datetime.now().isoformat(),
                'note': 'delete API 호출 후 webhook 대기 (status/is_published 변경은 webhook 이 처리)'
            }, ensure_ascii=False)))
            conn.commit()
        return True
    except Exception as e:
        log(f"  → DB 업데이트 실패: {e}", "ERROR")
        conn.rollback()
        return False


def main():
    parser = argparse.ArgumentParser(description='조회수 0 + N일 경과 상품 바이마 삭제 (옵션 A: webhook 기반)')
    parser.add_argument('--count', action='store_true', help='대상 건수만 확인')
    parser.add_argument('--dry-run', action='store_true', help='대상 목록만 출력 (실행 안함)')
    parser.add_argument('--days', type=int, default=30, help='경과 일수 기준 (기본 30)')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    args = parser.parse_args()

    log("=" * 60)
    log("low_view_cleaner — 조회수 0 + N일 경과 상품 삭제")
    log(f"  모드: {'건수' if args.count else 'DRY-RUN' if args.dry_run else '실제 삭제'}")
    log(f"  기준: buyma_registered_at < NOW() - {args.days} DAY")
    if args.limit:
        log(f"  최대: {args.limit}건")
    log("=" * 60)

    conn = get_connection()
    try:
        targets = fetch_targets(conn, days=args.days, limit=args.limit)
        log(f"대상: {len(targets)}건")

        if args.count:
            if targets:
                site_counts = {}
                for t in targets:
                    s = t['source_site'] or '(없음)'
                    site_counts[s] = site_counts.get(s, 0) + 1
                log("source_site 별:")
                for s, cnt in sorted(site_counts.items(), key=lambda x: -x[1]):
                    log(f"  {s}: {cnt}건")
            return

        if args.dry_run:
            for i, t in enumerate(targets[:50], 1):
                log(f"  [{i}] id={t['id']} buyma_id={t['buyma_product_id']} site={t['source_site']} reg={t['buyma_registered_at']} model={t['model_no']} name={t['name'][:40] if t['name'] else ''}")
            if len(targets) > 50:
                log(f"  ... 외 {len(targets)-50}건")
            return

        if not targets:
            log("대상 없음 — 종료")
            return

        success_count = 0
        fail_count = 0

        for i, t in enumerate(targets, 1):
            product_id = t['id']
            reference_number = t['reference_number']
            buyma_id = t['buyma_product_id']

            log(f"\n[{i}/{len(targets)}] ace_id={product_id} buyma_id={buyma_id} site={t['source_site']} reg={t['buyma_registered_at']}")

            if not reference_number:
                log(f"  → reference_number 없음, skip", "WARN")
                fail_count += 1
                continue

            api_result = call_buyma_delete_api(reference_number)

            if api_result.get('success'):
                log(f"  → delete API 성공 (status={api_result.get('status_code')}, webhook 대기)")
                if mark_exception(conn, product_id, api_result):
                    log(f"  → DB: is_active=0, exception_reason='{EXCEPTION_REASON}'")
                    success_count += 1
                else:
                    fail_count += 1
            else:
                log(f"  → delete API 실패: {api_result.get('error', 'Unknown')} (status={api_result.get('status_code')}) — DB 변경 X", "WARN")
                fail_count += 1

            if i < len(targets):
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        log("\n" + "=" * 60)
        log(f"완료: 성공 {success_count}건 / 실패 {fail_count}건")
        log("=" * 60)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
