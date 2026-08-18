# -*- coding: utf-8 -*-
"""
지재권 차단 수집처의 이미지를 걷어내는 정리기

배경:
  일부 수집처는 상품(가격·재고)은 계속 쓰되 **이미지는 쓸 수 없다**(지재권).
    - fabstyle : 몰 전체가 대상
    - shinsegae: 럭스보이 입점분만 대상 (naver/shinsegae_luxboy_scan.py 판정 결과 사용)
  수집처를 통째로 빼면 최저가 매입처를 잃으므로, 이미지만 제거한다.

이미지가 빠진 뒤 상품이 어떻게 되는지:
  - 다른 수집처에도 있는 상품 → listing_images 가 다른 몰 사진으로 다시 채워진다(자동).
  - 이 수집처 단독 상품     → 쓸 사진이 없다.
      · 미등록이면 등록 후보 조건("R2 업로드된 이미지 1장 이상")에서 자동으로 빠진다.
      · 이미 등록됐으면 바이마에서 삭제한다(사진 없이 남겨두면 차단 이미지가 계속 노출됨).

단계 (이 순서대로 실행할 것):
  1 --purge-raw      raw_scraped_data 의 이미지 목록 비우기   ← 안 하면 다음 변환 때 되살아남
  2 --purge-ace      ace_product_images 삭제
  3 --fix-listings   listing_images 에서 차단 이미지 제거 + 자리 재매김
  4 --push           바이마 반영: 사진 남으면 수정(EDIT), 0장이면 삭제(DELETE)
  5 --purge-r2       R2 사본 삭제  ← 반드시 4단계 뒤. 먼저 지우면 EDIT 이 죽은 URL 을 보낸다

사용법:
    python3 blocked_image_cleaner.py --source fabstyle --scan
    python3 blocked_image_cleaner.py --source fabstyle --purge-raw --execute
    python3 blocked_image_cleaner.py --source fabstyle --purge-ace --execute
    python3 blocked_image_cleaner.py --source fabstyle --fix-listings --execute
    python3 blocked_image_cleaner.py --source fabstyle --push --execute --confirm-live
    python3 blocked_image_cleaner.py --source fabstyle --purge-r2 --execute

    python3 blocked_image_cleaner.py --source shinsegae --luxboy --scan

--execute 없으면 전부 DRY-RUN(아무것도 안 바꿈).
--push 는 바이마를 실제로 건드리므로 --confirm-live 까지 있어야 발사한다.
--keep-work 를 주면 작업표를 다시 만들지 않는다(단계별로 이어 돌릴 때 시간 절약).

작성일: 2026-08-18
"""

from __future__ import annotations

import os
import sys
import csv
import json
import time
import argparse
import logging
from datetime import datetime
from typing import Dict, List, Optional

import pymysql
import requests
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, '.env'))
sys.path.insert(0, os.path.join(ROOT_DIR, 'okmall'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

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

R2_ACCESS_KEY_ID = os.getenv('R2_ACCESS_KEY_ID', '')
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY', '')
R2_ENDPOINT_URL = os.getenv('R2_ENDPOINT_URL', '')
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME', 'buyma-images')

CHUNK = 500              # DB 배치 단위
API_DELAY = 0.4          # 바이마 API 호출 간격
REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')


def get_connection(retries: int = 5):
    """원격 DB 연결. 간헐적으로 접속이 타임아웃되므로 몇 번 다시 시도한다."""
    last = None
    for attempt in range(retries):
        try:
            return pymysql.connect(connect_timeout=20, read_timeout=600,
                                   write_timeout=600, **DB_CONFIG)
        except pymysql.err.OperationalError as e:
            last = e
            wait = 5 * (attempt + 1)
            logger.warning(f"DB 접속 실패({e.args[0]}) — {wait}초 후 재시도 {attempt + 1}/{retries}")
            time.sleep(wait)
    raise last


class DB:
    """끊기면 다시 붙는 연결 래퍼.

    원격 DB 가 대량 조회 중 연결을 자주 끊는다(2013). 매번 손으로 감싸지 않도록
    조회/실행을 여기로 모으고, 끊기면 재접속 후 같은 쿼리를 다시 던진다.
    쓰기는 전부 '다시 돌려도 결과가 같은' 배치만 통과시킨다.
    """

    def __init__(self):
        self.conn = get_connection()

    def _retry(self, fn, retries: int = 4):
        last = None
        for attempt in range(retries):
            try:
                return fn()
            except pymysql.err.OperationalError as e:
                last = e
                if e.args[0] not in (2003, 2006, 2013):
                    raise
                wait = 5 * (attempt + 1)
                logger.warning(f"DB 연결 끊김({e.args[0]}) — {wait}초 후 재접속 재시도 {attempt + 1}/{retries}")
                time.sleep(wait)
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = get_connection()
        raise last

    def query(self, sql: str, params=None) -> List[Dict]:
        def go():
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        return self._retry(go)

    def execute(self, sql: str, params=None, many: bool = False) -> int:
        def go():
            with self.conn.cursor() as cur:
                if many:
                    cur.executemany(sql, params)
                else:
                    cur.execute(sql, params)
                n = cur.rowcount
            self.conn.commit()
            return n
        return self._retry(go)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def chunks(seq: List, size: int = CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# =====================================================
# 작업표 — 차단 대상 이미지를 한 곳에 모아 조인에 쓴다
# =====================================================

WORK_TABLE = 'blocked_image_work'

CREATE_WORK_SQL = f"""
CREATE TABLE IF NOT EXISTS {WORK_TABLE} (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    source_site          VARCHAR(50)  NOT NULL,
    ace_product_id       INT          NOT NULL,
    raw_data_id          INT          NULL,
    image_id             INT          NULL,
    source_image_url     VARCHAR(500) NULL,
    cloudflare_image_url VARCHAR(500) NULL,
    created_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_site (source_site),
    KEY idx_ace (ace_product_id),
    KEY idx_cf (cloudflare_image_url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='지재권 차단 이미지 정리 작업표'
"""
# COLLATE 는 ace_products/raw_scraped_data 와 같은 utf8mb4_unicode_ci 로 고정.
#   서버 기본값(utf8mb4_uca1400_ai_ci)으로 만들면 조인에서 "Illegal mix of collations" 로 깨진다.

TARGET_ALL_SQL = """
SELECT a.id AS ace_product_id, a.raw_data_id
FROM ace_products a
WHERE a.source_site = %s
"""

TARGET_LUXBOY_SQL = """
SELECT a.id AS ace_product_id, a.raw_data_id
FROM ace_products a
JOIN raw_scraped_data r      ON r.id = a.raw_data_id
JOIN shinsegae_luxboy_scan s ON s.mall_product_id = r.mall_product_id
WHERE a.source_site = %s
  AND s.is_luxboy = 1
"""


def build_work_table(db: DB, source: str, luxboy: bool) -> int:
    db.execute(CREATE_WORK_SQL)
    db.execute(f"DELETE FROM {WORK_TABLE} WHERE source_site = %s", (source,))
    sql = TARGET_LUXBOY_SQL if luxboy else TARGET_ALL_SQL
    return db.execute(f"""
        INSERT INTO {WORK_TABLE}
            (source_site, ace_product_id, raw_data_id, image_id,
             source_image_url, cloudflare_image_url)
        SELECT %s, t.ace_product_id, t.raw_data_id, i.id,
               i.source_image_url, i.cloudflare_image_url
        FROM ({sql}) t
        JOIN ace_product_images i ON i.ace_product_id = t.ace_product_id
    """, (source, source))


def work_count(db: DB, source: str) -> Dict:
    return db.query(f"""SELECT COUNT(*) n, COUNT(DISTINCT ace_product_id) aces,
                               COUNT(cloudflare_image_url) r2
                          FROM {WORK_TABLE} WHERE source_site=%s""", (source,))[0]


def affected_listing_ids(db: DB, source: str) -> List[int]:
    """차단 이미지가 실려 있는 목록 id.

    ★ 3단계(fix-listings) 이후에는 listing_images 에서 이미 지워져 안 잡힌다.
      그 뒤 단계는 offering_listing_ids() 로 되짚는다.
    """
    rows = db.query(f"""
        SELECT DISTINCT li.listing_id
        FROM {WORK_TABLE} w
        JOIN listing_images li ON li.cloudflare_image_url = w.cloudflare_image_url
        WHERE w.source_site = %s AND w.cloudflare_image_url IS NOT NULL
    """, (source,))
    return [r['listing_id'] for r in rows]


def offering_listing_ids(db: DB, source: str) -> List[int]:
    """이 몰이 매입처로 물려 있는 목록 id (이미지가 이미 지워진 뒤에도 찾아진다)."""
    rows = db.query(f"""
        SELECT DISTINCT so.listing_id
        FROM {WORK_TABLE} w
        JOIN source_offerings so ON so.ace_product_id = w.ace_product_id AND so.is_active = 1
        WHERE w.source_site = %s
    """, (source,))
    return [r['listing_id'] for r in rows]


# =====================================================
# SCAN — 무엇이 얼마나 영향받는지
# =====================================================

def scan(db: DB, source: str) -> Dict:
    img = work_count(db, source)
    lids = affected_listing_ids(db, source)
    logger.info(f"  영향 목록 {len(lids):,}건 — 이미지 구성 확인 중")

    rows: List[Dict] = []
    for batch in chunks(lids):
        fmt = ','.join(['%s'] * len(batch))
        rows += db.query(f"""
            SELECT b.id AS listing_id, b.buyma_product_id, b.reference_number,
                   b.is_published, b.status, b.name,
                   SUM(CASE WHEN w.id IS NOT NULL THEN 1 ELSE 0 END) AS blocked,
                   SUM(CASE WHEN w.id IS NULL     THEN 1 ELSE 0 END) AS remaining
            FROM buyma_listings b
            JOIN listing_images li ON li.listing_id = b.id
            LEFT JOIN {WORK_TABLE} w ON w.source_site = %s
                                    AND w.cloudflare_image_url = li.cloudflare_image_url
            WHERE b.id IN ({fmt})
            GROUP BY b.id
        """, [source] + batch)

    published = [r for r in rows if r['is_published'] == 1]
    stats = {
        'images': img['n'], 'aces': img['aces'], 'r2': img['r2'],
        'listings': len(rows),
        'published': len(published),
        'pub_replaceable': len([r for r in published if r['remaining'] > 0]),
        'pub_to_delete': len([r for r in published if r['remaining'] == 0]),
        'unpub': len(rows) - len(published),
    }

    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f'blocked_image_{source}_{datetime.now():%Y%m%d_%H%M%S}.csv')
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['listing_id', 'buyma_product_id', 'reference_number', 'is_published',
                    'status', 'blocked_images', 'remaining_images', 'action', 'name'])
        for r in rows:
            if r['is_published'] != 1:
                action = '미게시 — 바이마 조치 없음'
            elif r['remaining'] > 0:
                action = '이미지 교체(EDIT)'
            else:
                action = '바이마 삭제(DELETE)'
            w.writerow([r['listing_id'], r['buyma_product_id'], r['reference_number'],
                        r['is_published'], r['status'], r['blocked'], r['remaining'],
                        action, (r['name'] or '')[:80]])
    stats['report'] = path
    return stats


# =====================================================
# 1단계 --purge-raw — raw 의 이미지 목록 비우기
# =====================================================

def purge_raw(db: DB, source: str, execute: bool) -> Dict:
    """raw_scraped_data.raw_json_data 의 images 를 [] 로.

    변환기는 매 실행마다 raw 의 images 로 ace_product_images 를 다시 만든다
    (kasina/raw_to_converter_kasina.py:1536). 여기를 안 비우면 지워도 되살아난다.
    """
    raw_ids = [r['raw_data_id'] for r in db.query(
        f"""SELECT DISTINCT raw_data_id FROM {WORK_TABLE}
             WHERE source_site=%s AND raw_data_id IS NOT NULL""", (source,))]
    stats = {'targets': len(raw_ids), 'updated': 0, 'already': 0, 'broken': 0}

    done = 0
    for batch in chunks(raw_ids):
        fmt = ','.join(['%s'] * len(batch))
        rows = db.query(f"SELECT id, raw_json_data FROM raw_scraped_data WHERE id IN ({fmt})", batch)
        updates = []
        for r in rows:
            try:
                data = json.loads(r['raw_json_data'] or '{}')
            except Exception:
                stats['broken'] += 1
                continue
            if not data.get('images'):
                stats['already'] += 1
                continue
            data['images'] = []
            data['images_blocked_reason'] = 'ip_rights'
            updates.append((json.dumps(data, ensure_ascii=False), r['id']))
        if updates and execute:
            db.execute("UPDATE raw_scraped_data SET raw_json_data=%s, updated_at=NOW() WHERE id=%s",
                       updates, many=True)
        stats['updated'] += len(updates)
        done += len(batch)
        logger.info(f"  raw 정리 {done:,}/{len(raw_ids):,} (비움 {stats['updated']:,}, 이미 없음 {stats['already']:,})")
    return stats


# =====================================================
# 2단계 --purge-ace — ace_product_images 삭제
# =====================================================

def purge_ace(db: DB, source: str, execute: bool) -> Dict:
    ids = [r['image_id'] for r in db.query(
        f"SELECT image_id FROM {WORK_TABLE} WHERE source_site=%s AND image_id IS NOT NULL",
        (source,))]
    stats = {'targets': len(ids), 'deleted': 0}
    if not execute:
        return stats
    done = 0
    for batch in chunks(ids):
        fmt = ','.join(['%s'] * len(batch))
        stats['deleted'] += db.execute(f"DELETE FROM ace_product_images WHERE id IN ({fmt})", batch)
        done += len(batch)
        logger.info(f"  ace 이미지 삭제 {done:,}/{len(ids):,}")
    return stats


# =====================================================
# 3단계 --fix-listings — listing_images 정리 + 자리 재매김
# =====================================================

def fix_listings(db: DB, source: str, execute: bool) -> Dict:
    """listing_images 에서 차단 이미지를 빼고 position 을 1부터 다시 매긴다.

    자리(position)에 구멍이 나면 바이마가 요청을 거부한다(422). 반드시 재매김.
    """
    lids = affected_listing_ids(db, source)
    stats = {'listings': len(lids), 'removed': 0, 'emptied': 0}

    for n, lid in enumerate(lids, 1):
        rows = db.query(f"""
            SELECT li.id, li.position, li.cloudflare_image_url,
                   (w.id IS NOT NULL) AS blocked
            FROM listing_images li
            LEFT JOIN {WORK_TABLE} w ON w.source_site=%s
                                    AND w.cloudflare_image_url = li.cloudflare_image_url
            WHERE li.listing_id=%s
            ORDER BY li.position
        """, (source, lid))
        blocked = [r for r in rows if r['blocked']]
        keep = [r for r in rows if not r['blocked']]
        stats['removed'] += len(blocked)
        if not keep:
            stats['emptied'] += 1
        if execute and blocked:
            fmt = ','.join(['%s'] * len(blocked))
            db.execute(f"DELETE FROM listing_images WHERE id IN ({fmt})",
                       [r['id'] for r in blocked])
            renum = [(pos, r['id']) for pos, r in enumerate(keep, start=1)
                     if r['position'] != pos]
            if renum:
                db.execute("UPDATE listing_images SET position=%s, updated_at=NOW() WHERE id=%s",
                           renum, many=True)
        if n % 200 == 0:
            logger.info(f"  listing_images 정리 {n:,}/{len(lids):,}")
    return stats


# =====================================================
# 4단계 --push — 바이마 반영
# =====================================================

def call_buyma_delete_api(reference_number: str) -> Dict:
    url = f"{BUYMA_API_BASE_URL}api/v1/products"
    headers = {
        'Content-Type': 'application/json',
        'X-Buyma-Personal-Shopper-Api-Access-Token': BUYMA_ACCESS_TOKEN,
    }
    payload = {'product': {'control': 'delete', 'reference_number': reference_number}}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code in (200, 201, 202):
            return {'success': True, 'status_code': resp.status_code}
        return {'success': False, 'status_code': resp.status_code, 'error': resp.text[:200]}
    except requests.RequestException as e:
        return {'success': False, 'error': str(e)}


def _mark_deleted(db: DB, listing_id: int, ref: str, result: Dict):
    """바이마 삭제 후 목록 정체성 정리 — buyma_unpublished_cleaner 와 동일 규칙.

    ★ buyma_product_id 를 반드시 비운다. 남겨두면 나중에 다른 수집처가 같은 상품을
      들고 들어왔을 때 '이미 등록됨'으로 보고 EDIT 을 보내 죽은 상품번호에 실패한다.
    """
    db.execute("""
        UPDATE buyma_listings
           SET buyma_product_id = NULL, is_published = 0,
               status = 'deleted', updated_at = NOW()
         WHERE id = %s
    """, (listing_id,))
    db.execute("""
        INSERT INTO buyma_listing_api_logs
            (buyma_listing_id, api_request_json, api_response_json, last_api_call_at)
        VALUES (%s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            api_request_json = VALUES(api_request_json),
            api_response_json = VALUES(api_response_json),
            last_api_call_at = NOW()
    """, (listing_id,
          json.dumps({'control': 'delete', 'reference_number': ref,
                      'reason': 'blocked_image_ip_rights'}, ensure_ascii=False),
          json.dumps({'deleted_reason': 'blocked_image_ip_rights',
                      'api_result': result,
                      'deleted_at': datetime.now().isoformat()}, ensure_ascii=False)))


def push(db: DB, source: str, execute: bool, limit: Optional[int]) -> Dict:
    import reconcile_buyma_push as push_mod

    # 3단계에서 listing_images 의 차단 행을 이미 지웠으므로 이미지로는 역추적이 안 된다
    #   → source_offerings(ace_product_id)로 이 몰이 물린 게시중 목록을 되짚는다.
    #   imgs = 3단계 이후 남은 사진 수 → EDIT/DELETE 를 가르는 기준.
    lids = offering_listing_ids(db, source)
    rows: List[Dict] = []
    for batch in chunks(lids):
        fmt = ','.join(['%s'] * len(batch))
        rows += db.query(f"""
            SELECT b.id AS listing_id, b.buyma_product_id, b.reference_number,
                   b.locked_reference_number, b.name,
                   (SELECT COUNT(*) FROM listing_images li
                     WHERE li.listing_id=b.id AND li.cloudflare_image_url IS NOT NULL) AS imgs
            FROM buyma_listings b
            WHERE b.id IN ({fmt})
              AND b.is_published = 1 AND b.buyma_product_id IS NOT NULL
        """, batch)
    if limit:
        rows = rows[:limit]

    stats = {'targets': len(rows), 'edit_ok': 0, 'edit_fail': 0,
             'deleted': 0, 'delete_fail': 0, 'skipped': 0}

    for i, r in enumerate(rows, 1):
        lid = r['listing_id']
        ref = r['locked_reference_number'] or r['reference_number']
        if r['imgs'] > 0:
            # 사진이 남음 → 수정으로 밀어 차단 이미지를 걷어낸다
            if not execute:
                stats['edit_ok'] += 1
                continue
            listing = push_mod.fetch_listing(db.conn, lid)
            res = push_mod.execute_edit(db.conn, listing, dry_run=False)
            if res.get('skipped') or res.get('error'):
                stats['edit_fail'] += 1
                logger.warning(f"[{i}/{len(rows)}] listing#{lid} 수정 실패: {res}")
            else:
                stats['edit_ok'] += 1
            time.sleep(API_DELAY)
        else:
            # 사진 0장 → 바이마에서 삭제
            if not ref:
                stats['skipped'] += 1
                continue
            if not execute:
                stats['deleted'] += 1
                continue
            result = call_buyma_delete_api(ref)
            if result.get('success'):
                stats['deleted'] += 1
            else:
                stats['delete_fail'] += 1
                logger.warning(f"[{i}/{len(rows)}] listing#{lid} 삭제 실패: {result.get('error')}")
            # 실패해도 DB 잔존 상품번호는 정리(바이마에 이미 없을 수 있음)
            _mark_deleted(db, lid, ref, result)
            time.sleep(API_DELAY)

        if i % 50 == 0:
            logger.info(f"  push 진행 {i:,}/{len(rows):,} — {stats}")
    return stats


# =====================================================
# 5단계 --purge-r2 — R2 사본 삭제
# =====================================================

def purge_r2(db: DB, source: str, execute: bool) -> Dict:
    import boto3
    from botocore.config import Config

    urls = [r['cloudflare_image_url'] for r in db.query(
        f"""SELECT cloudflare_image_url FROM {WORK_TABLE}
             WHERE source_site=%s AND cloudflare_image_url IS NOT NULL""", (source,))]

    # https://host/upload/xxx.jpg → upload/xxx.jpg
    #   파일명이 {ace_product_id}_{position}_{hash} 라 몰 간 파일 공유는 없다
    #   (okmall/r2_image_uploader.py:139).
    keys, skipped = [], 0
    for u in urls:
        parts = u.split('/', 3)
        if len(parts) == 4 and parts[3]:
            keys.append(parts[3])
        else:
            skipped += 1

    stats = {'targets': len(keys), 'deleted': 0, 'failed': 0, 'skipped': skipped}
    if not execute:
        return stats

    client = boto3.client('s3', endpoint_url=R2_ENDPOINT_URL,
                          aws_access_key_id=R2_ACCESS_KEY_ID,
                          aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                          config=Config(signature_version='s3v4', retries={'max_attempts': 3}))
    for i in range(0, len(keys), 1000):
        batch = keys[i:i + 1000]
        resp = client.delete_objects(Bucket=R2_BUCKET_NAME,
                                     Delete={'Objects': [{'Key': k} for k in batch]})
        stats['deleted'] += len(resp.get('Deleted', []))
        stats['failed'] += len(resp.get('Errors', []))
        for e in resp.get('Errors', [])[:5]:
            logger.warning(f"  R2 삭제 실패: {e.get('Key')} — {e.get('Message')}")
        logger.info(f"  R2 삭제 {min(i + 1000, len(keys)):,}/{len(keys):,}")
        time.sleep(0.5)
    return stats


# =====================================================

def main():
    ap = argparse.ArgumentParser(description='지재권 차단 수집처 이미지 정리')
    ap.add_argument('--source', required=True, help='수집처 (예: fabstyle, shinsegae)')
    ap.add_argument('--luxboy', action='store_true',
                    help='신세계 전용 — 럭스보이 판정분만 대상 (shinsegae_luxboy_scan)')
    ap.add_argument('--scan', action='store_true', help='영향 범위 산출 + CSV 리포트')
    ap.add_argument('--purge-raw', action='store_true', help='1단계 raw 이미지 목록 비우기')
    ap.add_argument('--purge-ace', action='store_true', help='2단계 ace_product_images 삭제')
    ap.add_argument('--fix-listings', action='store_true', help='3단계 listing_images 정리')
    ap.add_argument('--push', action='store_true', help='4단계 바이마 반영(EDIT/DELETE)')
    ap.add_argument('--purge-r2', action='store_true', help='5단계 R2 사본 삭제')
    ap.add_argument('--execute', action='store_true', help='실제 반영 (없으면 DRY-RUN)')
    ap.add_argument('--confirm-live', action='store_true', help='--push 실발사 확인')
    ap.add_argument('--limit', type=int, help='--push 처리 개수 제한')
    ap.add_argument('--keep-work', action='store_true', help='작업표를 다시 만들지 않음')
    args = ap.parse_args()

    if args.luxboy and args.source != 'shinsegae':
        ap.error('--luxboy 는 --source shinsegae 에만 쓸 수 있습니다')
    if args.push and args.execute and not args.confirm_live:
        ap.error('--push --execute 는 --confirm-live 가 필요합니다 (바이마 실반영)')

    mode = 'EXECUTE' if args.execute else 'DRY-RUN'
    logger.info(f"차단 이미지 정리 — source={args.source} luxboy={args.luxboy} [{mode}]")

    db = DB()
    try:
        if args.keep_work:
            c = work_count(db, args.source)
            logger.info(f"작업표 재사용: 차단 대상 이미지 {c['n']:,}장 (상품 {c['aces']:,}개)")
        else:
            n = build_work_table(db, args.source, args.luxboy)
            logger.info(f"작업표 구성: 차단 대상 이미지 {n:,}장")

        if args.scan:
            s = scan(db, args.source)
            logger.info("=" * 66)
            logger.info(f"  차단 이미지        : {s['images']:,}장 (상품 {s['aces']:,}개, R2 사본 {s['r2']:,})")
            logger.info(f"  영향 목록          : {s['listings']:,}건")
            logger.info(f"   ├ 게시중          : {s['published']:,}")
            logger.info(f"   │  ├ 이미지 교체   : {s['pub_replaceable']:,}  (다른 몰 사진 있음 → EDIT)")
            logger.info(f"   │  └ 바이마 삭제   : {s['pub_to_delete']:,}  (사진 0장 → DELETE)")
            logger.info(f"   └ 미게시          : {s['unpub']:,}  (바이마 조치 없음)")
            logger.info(f"  리포트: {s['report']}")
            logger.info("=" * 66)

        if args.purge_raw:
            s = purge_raw(db, args.source, args.execute)
            logger.info(f"[1단계 raw] 대상 {s['targets']:,} / 비움 {s['updated']:,} / 이미 없음 {s['already']:,} / 깨짐 {s['broken']:,}")
        if args.purge_ace:
            s = purge_ace(db, args.source, args.execute)
            logger.info(f"[2단계 ace 이미지] 대상 {s['targets']:,} / 삭제 {s['deleted']:,}")
        if args.fix_listings:
            s = fix_listings(db, args.source, args.execute)
            logger.info(f"[3단계 listing_images] 목록 {s['listings']:,} / 제거 {s['removed']:,}장 / 사진0장 된 목록 {s['emptied']:,}")
        if args.push:
            s = push(db, args.source, args.execute, args.limit)
            logger.info(f"[4단계 바이마] {s}")
        if args.purge_r2:
            s = purge_r2(db, args.source, args.execute)
            logger.info(f"[5단계 R2] 대상 {s['targets']:,} / 삭제 {s['deleted']:,} / 실패 {s['failed']:,}")

        if not args.execute:
            logger.info("(DRY-RUN — 아무것도 바꾸지 않았습니다. --execute 로 실행)")
    finally:
        db.close()


if __name__ == '__main__':
    main()
