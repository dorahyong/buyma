# -*- coding: utf-8 -*-
"""
하자(defective) 표기된 raw 상품을 ace 테이블 + BUYMA에서 정리

대상 선정:
  raw_scraped_data WHERE product_name LIKE '%하자%' (전체 source_site)
  → 해당 raw에 매칭되는 ace_products 삭제 + BUYMA 게시중인 건은 쇼퍼 API로 삭제

흐름:
  Phase 1: 대상 스캔 → CSV/JSON 저장
  Phase 2: BUYMA 삭제 (is_published=1 + reference_number 보유 건만)
  Phase 3: ace 자식 테이블 삭제 (images, options, variants)
           * ace_product_api_logs는 FK CASCADE로 ace_products 삭제 시 자동 정리
  Phase 4: ace_products 삭제

raw_scraped_data 는 유지 (raw_to_converter_*.py 의 LIKE '%하자%' 필터로 재변환 차단)

사용:
  python cleanup_hazard_products.py --scan                       # 대상만 스캔/CSV 저장
  python cleanup_hazard_products.py --delete --dry-run           # 삭제 시뮬레이션
  python cleanup_hazard_products.py --delete                     # 실제 삭제
  python cleanup_hazard_products.py --scan --source-site kasina  # 특정 몰만

작성일: 2026-05-11
"""

import os
import sys
import json
import csv
import time
import argparse
from datetime import datetime
from typing import Dict, List, Optional

import requests as req_lib
import pymysql
from dotenv import load_dotenv

# Windows 콘솔 cp949 회피
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

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
    'cursorclass': pymysql.cursors.DictCursor,
}

BUYMA_SHOPPER_API_URL = os.getenv(
    'BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/'
)
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')

DELETE_DELAY = 0.2  # BUYMA 삭제 API 호출 간 대기

HAZARD_KEYWORD = '하자'

# 출력 파일
TARGETS_JSON = 'hazard_targets.json'
TARGETS_CSV = 'hazard_targets.csv'


def log(msg: str, level: str = 'INFO') -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] [{level}] {msg}', flush=True)


# =====================================================
# Phase 1: 스캔 — 하자 raw + 매칭 ace_products
# =====================================================

def scan_targets(source_site: Optional[str] = None) -> List[Dict]:
    """
    raw_scraped_data 중 product_name LIKE '%하자%' → ace_products LEFT JOIN.

    반환 항목:
      - raw_id, raw_source_site, raw_product_name
      - ace_id, ace_reference_number, ace_buyma_product_id, ace_is_published
        (ace_id IS NULL 이면 아직 변환 안 됨 → BUYMA/ace 삭제 대상 아님)
    """
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            query = """
                SELECT
                    r.id              AS raw_id,
                    r.source_site     AS raw_source_site,
                    r.product_name    AS raw_product_name,
                    a.id              AS ace_id,
                    a.reference_number AS ace_reference_number,
                    a.buyma_product_id AS ace_buyma_product_id,
                    a.is_published    AS ace_is_published
                FROM raw_scraped_data r
                LEFT JOIN ace_products a ON r.id = a.raw_data_id
                WHERE r.product_name LIKE %s
            """
            params = [f'%{HAZARD_KEYWORD}%']
            if source_site:
                query += ' AND r.source_site = %s'
                params.append(source_site)
            query += ' ORDER BY r.source_site, r.id'

            cursor.execute(query, params)
            return cursor.fetchall()
    finally:
        conn.close()


def save_targets(targets: List[Dict]) -> None:
    """스캔 결과를 JSON + CSV로 저장"""
    serializable = []
    for t in targets:
        serializable.append({
            'raw_id': t['raw_id'],
            'raw_source_site': t['raw_source_site'],
            'raw_product_name': t['raw_product_name'],
            'ace_id': t['ace_id'],
            'ace_reference_number': t['ace_reference_number'],
            'ace_buyma_product_id': t['ace_buyma_product_id'],
            'ace_is_published': t['ace_is_published'],
        })

    with open(TARGETS_JSON, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

    with open(TARGETS_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow([
            'raw_id', 'raw_source_site', 'raw_product_name',
            'ace_id', 'ace_reference_number',
            'ace_buyma_product_id', 'ace_is_published',
        ])
        for t in serializable:
            w.writerow([
                t['raw_id'], t['raw_source_site'], (t['raw_product_name'] or '')[:100],
                t['ace_id'], t['ace_reference_number'],
                t['ace_buyma_product_id'], t['ace_is_published'],
            ])

    log(f'저장 완료: {TARGETS_JSON}, {TARGETS_CSV}')


def print_scan_summary(targets: List[Dict]) -> Dict[str, int]:
    """스캔 요약 출력 + 카운트 반환"""
    by_site: Dict[str, int] = {}
    ace_count = 0
    published_count = 0  # BUYMA 삭제 필요 (reference_number + is_published)
    for t in targets:
        site = t['raw_source_site'] or 'UNKNOWN'
        by_site[site] = by_site.get(site, 0) + 1
        if t['ace_id'] is not None:
            ace_count += 1
            if t['ace_is_published'] == 1 and t['ace_reference_number']:
                published_count += 1

    log('=' * 60)
    log(f'스캔 결과: 총 {len(targets)}건의 raw (product_name LIKE %{HAZARD_KEYWORD}%)')
    log('=' * 60)
    log('source_site별:')
    for site, cnt in sorted(by_site.items(), key=lambda x: -x[1]):
        log(f'  {site:20s} {cnt:>6}건')
    log('-' * 60)
    log(f'  ace 매칭 있음     : {ace_count:>6}건  (ace_products 삭제 대상)')
    log(f'  BUYMA 게시 중      : {published_count:>6}건  (쇼퍼 API 삭제 대상)')
    log(f'  ace 매칭 없음     : {len(targets) - ace_count:>6}건  (raw만 존재 — 별도 작업 없음)')
    log('=' * 60)

    return {
        'total': len(targets),
        'ace_count': ace_count,
        'published_count': published_count,
    }


# =====================================================
# Phase 2: BUYMA 삭제 (쇼퍼 API)
# =====================================================

def delete_via_shopper_api(reference_number: str) -> bool:
    """바이마 쇼퍼 API로 상품 삭제 (control: delete)"""
    url = f'{BUYMA_SHOPPER_API_URL}api/v1/products'
    headers = {
        'Content-Type': 'application/json',
        'X-Buyma-Personal-Shopper-Api-Access-Token': BUYMA_ACCESS_TOKEN,
    }
    payload = {
        'product': {
            'control': 'delete',
            'reference_number': reference_number,
        }
    }
    try:
        resp = req_lib.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201, 202):
            return True
        log(f'    삭제 API 실패: {resp.status_code} {resp.text[:120]}', 'WARN')
        return False
    except Exception as e:
        log(f'    삭제 API 오류: {e}', 'ERROR')
        return False


def delete_from_buyma(targets: List[Dict], dry_run: bool) -> Dict:
    """
    BUYMA에 게시중인 건들을 쇼퍼 API로 삭제.

    반환:
      - success: 성공 건수
      - failed:  실패 건수
      - failed_ace_ids: BUYMA 삭제 실패한 ace_id 집합 (ace 삭제에서 보류)
    """
    candidates = [
        t for t in targets
        if t['ace_id'] is not None
        and t['ace_is_published'] == 1
        and t['ace_reference_number']
    ]

    log('=' * 60)
    log(f'Phase 2: BUYMA 삭제 ({len(candidates)}건)')
    if dry_run:
        log('[DRY-RUN] 실제 호출 없음')
    log('=' * 60)

    if not candidates:
        return {'success': 0, 'failed': 0, 'failed_ace_ids': set()}

    if not dry_run and not BUYMA_ACCESS_TOKEN:
        log('BUYMA_ACCESS_TOKEN 환경변수가 설정되지 않았습니다.', 'ERROR')
        sys.exit(1)

    success, failed = 0, 0
    failed_ace_ids: set = set()
    for i, t in enumerate(candidates, 1):
        ref = t['ace_reference_number']
        name = (t['raw_product_name'] or '')[:40]
        log(f'[{i}/{len(candidates)}] ref={ref[:24]}... | {name}')

        if dry_run:
            log('  → [DRY-RUN] 삭제 예정')
            success += 1
            continue

        if delete_via_shopper_api(ref):
            log('  → ✓ 삭제 성공')
            success += 1
        else:
            log('  → ✗ 삭제 실패 (ace 삭제 보류)', 'WARN')
            failed += 1
            failed_ace_ids.add(t['ace_id'])
        time.sleep(DELETE_DELAY)

        if i % 50 == 0:
            log(f'  --- 진행: {i}/{len(candidates)} (성공 {success}, 실패 {failed}) ---')

    log(f'BUYMA 삭제 완료: 성공 {success}, 실패 {failed}')
    return {'success': success, 'failed': failed, 'failed_ace_ids': failed_ace_ids}


# =====================================================
# Phase 3-4: ace 테이블 삭제
# =====================================================

def delete_from_ace(targets: List[Dict], dry_run: bool, buyma_failed_ace_ids: set) -> Dict[str, int]:
    """
    ace_products + 자식 테이블 삭제.

    BUYMA 삭제에 실패한 ace_id는 데이터 정합성 보호 차원에서 ace 삭제도 보류 (skip).
    """
    ace_ids = [
        t['ace_id'] for t in targets
        if t['ace_id'] is not None and t['ace_id'] not in buyma_failed_ace_ids
    ]

    log('=' * 60)
    log(f'Phase 3-4: ace 테이블 삭제 ({len(ace_ids)}건)')
    if buyma_failed_ace_ids:
        log(f'  (BUYMA 삭제 실패 {len(buyma_failed_ace_ids)}건은 보류)', 'WARN')
    if dry_run:
        log('[DRY-RUN] 실제 DELETE 없음')
    log('=' * 60)

    if not ace_ids:
        return {'images': 0, 'options': 0, 'variants': 0, 'products': 0}

    if dry_run:
        log(f'[DRY-RUN] ace_product_images / options / variants / products 에서 ace_product_id IN ({len(ace_ids)}건) 삭제 예정')
        return {'images': 0, 'options': 0, 'variants': 0, 'products': 0}

    counts = {'images': 0, 'options': 0, 'variants': 0, 'products': 0}
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            batch_size = 200
            for start in range(0, len(ace_ids), batch_size):
                batch = ace_ids[start:start + batch_size]
                placeholders = ','.join(['%s'] * len(batch))

                # 자식 테이블 먼저 (FK 없이 만들어진 테이블들)
                cursor.execute(
                    f'DELETE FROM ace_product_images WHERE ace_product_id IN ({placeholders})',
                    batch,
                )
                counts['images'] += cursor.rowcount

                cursor.execute(
                    f'DELETE FROM ace_product_options WHERE ace_product_id IN ({placeholders})',
                    batch,
                )
                counts['options'] += cursor.rowcount

                cursor.execute(
                    f'DELETE FROM ace_product_variants WHERE ace_product_id IN ({placeholders})',
                    batch,
                )
                counts['variants'] += cursor.rowcount

                # ace_products 삭제 — ace_product_api_logs 는 FK CASCADE로 자동 정리됨
                cursor.execute(
                    f'DELETE FROM ace_products WHERE id IN ({placeholders})',
                    batch,
                )
                counts['products'] += cursor.rowcount

                log(f'  → {start + len(batch)}/{len(ace_ids)} 처리 (products: {counts["products"]}, '
                    f'images: {counts["images"]}, options: {counts["options"]}, variants: {counts["variants"]})')

            conn.commit()
        log(f'ace 테이블 삭제 완료: '
            f'products={counts["products"]}, images={counts["images"]}, '
            f'options={counts["options"]}, variants={counts["variants"]}')
    except Exception as e:
        conn.rollback()
        log(f'ace 삭제 실패 (롤백): {e}', 'ERROR')
        raise
    finally:
        conn.close()

    return counts


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='하자 표기 raw 상품 정리 (ace + BUYMA)')
    parser.add_argument('--scan', action='store_true',
                        help='대상 스캔 + CSV/JSON 저장 (삭제 안 함)')
    parser.add_argument('--delete', action='store_true',
                        help='Phase 2-4 실행 (BUYMA + ace 삭제)')
    parser.add_argument('--dry-run', action='store_true',
                        help='--delete 와 함께 사용: 시뮬레이션만')
    parser.add_argument('--source-site', default=None,
                        help='특정 source_site만 (생략 시 전체)')
    args = parser.parse_args()

    if not args.scan and not args.delete:
        parser.print_help()
        return

    if args.delete and not args.scan:
        log('--delete 사용 시 매번 fresh 스캔으로 대상 결정합니다.')

    log(f'스캔 시작 (source_site={args.source_site or "ALL"}, keyword="{HAZARD_KEYWORD}")')
    targets = scan_targets(source_site=args.source_site)
    summary = print_scan_summary(targets)
    save_targets(targets)

    if not args.delete:
        log('스캔만 완료. 실제 삭제는 --delete 옵션으로 실행하세요.')
        return

    if summary['ace_count'] == 0:
        log('삭제할 ace 데이터 없음. 종료.')
        return

    # Phase 2: BUYMA 삭제
    if not args.dry_run:
        # 실제 삭제 — 한번 더 묻기
        confirm_msg = (
            f'\n>>> 실제 삭제를 진행합니다.\n'
            f'    BUYMA 삭제 대상: {summary["published_count"]}건\n'
            f'    ace 삭제 대상  : {summary["ace_count"]}건\n'
            f'    계속하려면 "DELETE" 를 입력하세요: '
        )
        try:
            answer = input(confirm_msg).strip()
        except EOFError:
            answer = ''
        if answer != 'DELETE':
            log('확인 문자열 불일치 — 중단')
            return

    buyma_result = delete_from_buyma(targets, dry_run=args.dry_run)

    if buyma_result['failed'] > 0:
        log(f'BUYMA 삭제 실패 {buyma_result["failed"]}건 — 해당 ace_id는 ace 삭제에서 제외 (재실행으로 재시도 가능)', 'WARN')

    # Phase 3-4: ace 테이블 삭제 (BUYMA 삭제 실패 건은 보류)
    delete_from_ace(
        targets,
        dry_run=args.dry_run,
        buyma_failed_ace_ids=buyma_result['failed_ace_ids'],
    )

    log('=' * 60)
    log('전체 완료')
    log('=' * 60)


if __name__ == '__main__':
    main()
