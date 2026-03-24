# -*- coding: utf-8 -*-
"""
구형식 상품(【 포함) 일괄 삭제 스크립트 (1회성)

처리 순서:
  1. ace_products에서 name LIKE '%【%' 인 상품 조회
  2. buyma_product_id가 있는 상품 → buyma delete API 호출
  3. ace 하위 테이블 삭제 (options, variants, images)
  4. ace_products 삭제

이후 orchestrator.py 실행 시 CONVERT 단계에서 자동 재생성됨.

사용법:
    python cleanup_old_format_products.py --dry-run   # 미리보기
    python cleanup_old_format_products.py              # 실제 실행
"""

import os
import sys
import time
import argparse
from datetime import datetime
from typing import Dict, List

import requests
import pymysql
from dotenv import load_dotenv

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# DB 설정
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
BUYMA_MODE = int(os.getenv('BUYMA_MODE', 1))
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')
API_BASE_URL = BUYMA_API_BASE_URL if BUYMA_MODE == 1 else BUYMA_SANDBOX_URL


def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def call_buyma_delete_api(reference_number: str) -> Dict:
    """바이마 상품 삭제 API 호출"""
    url = f"{API_BASE_URL}api/v1/products"
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


def main():
    parser = argparse.ArgumentParser(description='구형식 상품(【 포함) 일괄 삭제')
    parser.add_argument('--dry-run', action='store_true', help='실제 삭제 없이 대상만 확인')
    args = parser.parse_args()

    log("=" * 60)
    log("구형식 상품 일괄 삭제 시작")
    log(f"  모드: {'DRY-RUN (미리보기)' if args.dry_run else '실제 삭제'}")
    log(f"  환경: {'본환경' if BUYMA_MODE == 1 else '샌드박스'}")
    log("=" * 60)

    conn = pymysql.connect(**DB_CONFIG)

    try:
        with conn.cursor() as cur:
            # 1. 대상 조회
            cur.execute("""
                SELECT id, name, reference_number, buyma_product_id, is_published
                FROM ace_products
                WHERE name LIKE '%%【%%'
            """)
            targets = cur.fetchall()

            need_buyma_delete = [t for t in targets if t['buyma_product_id']]
            log(f"대상: 총 {len(targets)}건 (buyma 삭제 필요: {len(need_buyma_delete)}건)")

            if args.dry_run:
                log("[DRY-RUN] 삭제 대상 샘플 (처음 10건):")
                for t in targets[:10]:
                    buyma_id = t['buyma_product_id'] or 'N/A'
                    log(f"  [{t['id']}] buyma={buyma_id} | {t['name'][:60]}")
                log(f"[DRY-RUN] 실제 삭제하려면 --dry-run 없이 실행하세요.")
                return

            # 2. buyma delete API 호출
            if need_buyma_delete:
                log(f"[Step 1/2] buyma 삭제 API 호출 ({len(need_buyma_delete)}건)...")
                success_count = 0
                fail_count = 0

                for i, t in enumerate(need_buyma_delete):
                    result = call_buyma_delete_api(t['reference_number'])

                    if result.get('success'):
                        success_count += 1
                    else:
                        fail_count += 1
                        error = result.get('error', 'unknown')
                        log(f"  삭제 실패 [{t['id']}] ref={t['reference_number']}: {error}", "WARNING")

                    # 진행률 (50건마다)
                    if (i + 1) % 50 == 0:
                        log(f"  진행: {i + 1}/{len(need_buyma_delete)} (성공: {success_count}, 실패: {fail_count})")

                    # API 부하 방지
                    time.sleep(0.3)

                log(f"  buyma 삭제 완료: 성공 {success_count}건, 실패 {fail_count}건")
            else:
                log("[Step 1/2] buyma 삭제 대상 없음. 스킵.")

            # 3. DB 삭제
            target_ids = [t['id'] for t in targets]
            log(f"[Step 2/2] ace DB 삭제 ({len(target_ids)}건)...")

            # 배치로 삭제 (1000건씩)
            for batch_start in range(0, len(target_ids), 1000):
                batch_ids = target_ids[batch_start:batch_start + 1000]
                placeholders = ', '.join(['%s'] * len(batch_ids))

                cur.execute(f"DELETE FROM ace_product_variants WHERE ace_product_id IN ({placeholders})", batch_ids)
                variants_deleted = cur.rowcount

                cur.execute(f"DELETE FROM ace_product_options WHERE ace_product_id IN ({placeholders})", batch_ids)
                options_deleted = cur.rowcount

                cur.execute(f"DELETE FROM ace_product_images WHERE ace_product_id IN ({placeholders})", batch_ids)
                images_deleted = cur.rowcount

                cur.execute(f"DELETE FROM ace_products WHERE id IN ({placeholders})", batch_ids)
                products_deleted = cur.rowcount

                log(f"  배치 삭제: products={products_deleted}, variants={variants_deleted}, options={options_deleted}, images={images_deleted}")

            conn.commit()

            log("=" * 60)
            log("삭제 완료!")
            log(f"  ace_products {len(target_ids)}건 삭제됨")
            log(f"  orchestrator.py 실행 시 CONVERT에서 자동 재생성됩니다.")
            log("=" * 60)

    except Exception as e:
        conn.rollback()
        log(f"오류 발생: {e}", "ERROR")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
