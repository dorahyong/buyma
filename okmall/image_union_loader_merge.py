# -*- coding: utf-8 -*-
"""
MERGE 5단계 — listing_images 적재 (이미지 선택·투영)

출품가능 listing(winner 설정됨)마다 이미지를 listing_images에 채운다.
이미지는 이미 ace_product_images에 있으므로 재수집 없이 투영.

이미지 선택 규칙 (사용자 확정):
  - winner 이미지 > 5장  → winner 이미지 사용
  - winner 이미지 <= 5장 → 멤버 중 이미지 최다 mall로 폴백 (winner가 최다면 winner)
  - 최대 20장(BUYMA 한도), position 1부터 재매김

donor = offering(멤버) 단위, 그 offering의 ace_product_id 이미지 사용.

Usage:
    python image_union_loader_merge.py             # DRY-RUN (기본)
    python image_union_loader_merge.py --execute   # 실제 적재
"""

import os
import argparse
import logging
from collections import defaultdict

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

WINNER_MIN_IMAGES = 5   # winner 이미지가 이 값 이하면 최다 멤버로 폴백
MAX_IMAGES = 20         # BUYMA 한도

# 폴백 시 동점 우선순위 (낮을수록 우선) — dedup_corrector와 동일 계열
SOURCE_PRIORITY = {
    'okmall': 0, 'nextzennpack': 1, 'labellusso': 2, 'trendmecca': 3, 'kasina': 4,
    'loutique': 5, 'vvano': 6, 'veroshopmall': 7, 'fabstyle': 8, 'premiumsneakers': 9,
    't1global': 10, 'carpi': 11, 'dmont': 12, 'tuttobene': 13, 'joharistore': 14,
    'thefactor2': 15, '9tems': 16, 'laprima': 17, 'maniaon': 18, 'bblue': 19,
    'euroline': 20, 'unico': 21, 'kometa': 22, 'larlashoes': 23, 'thegrande': 24,
    'upset': 25, 'luxlimit': 26, 'pano': 27,
}


def get_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST', '54.180.248.182'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER', 'block'),
        password=os.getenv('DB_PASSWORD', '1234'),
        database=os.getenv('DB_NAME', 'buyma'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


def load_all(conn):
    cur = conn.cursor()

    cur.execute("SELECT id, winner_offering_id FROM buyma_listings WHERE winner_offering_id IS NOT NULL AND is_active=1")
    listings = cur.fetchall()
    logger.info(f"출품가능 listing 로드: {len(listings)}")

    cur.execute("SELECT id, listing_id, source_site, ace_product_id FROM source_offerings WHERE is_active=1")
    offerings_by_listing = defaultdict(list)
    offering_by_id = {}
    ace_ids = set()
    for r in cur.fetchall():
        offerings_by_listing[r['listing_id']].append(r)
        offering_by_id[r['id']] = r
        if r['ace_product_id']:
            ace_ids.add(r['ace_product_id'])
    logger.info(f"offerings 로드: {len(offering_by_id)} / 관련 ace {len(ace_ids)}")

    # 이미지: 관련 ace만 메모리 인덱스 (position 순)
    cur.execute("""
        SELECT ace_product_id, position, source_image_url, cloudflare_image_url, buyma_image_path
        FROM ace_product_images
        ORDER BY ace_product_id, position
    """)
    images_by_ace = defaultdict(list)
    kept = 0
    for r in cur.fetchall():
        if r['ace_product_id'] in ace_ids:
            images_by_ace[r['ace_product_id']].append(r)
            kept += 1
    logger.info(f"이미지 인덱스 로드: {kept}장 / ace {len(images_by_ace)}개")

    return listings, offerings_by_listing, offering_by_id, images_by_ace


def pick_donor(listing, offerings_by_listing, offering_by_id, images_by_ace):
    """이미지 donor offering 선택 → (donor_offering, images)."""
    winner = offering_by_id.get(listing['winner_offering_id'])
    members = offerings_by_listing.get(listing['id'], [])

    def img_count(off):
        return len(images_by_ace.get(off['ace_product_id'], [])) if off else 0

    winner_cnt = img_count(winner)

    if winner and winner_cnt > WINNER_MIN_IMAGES:
        donor = winner
    else:
        # 최다 이미지 멤버 (동점: winner 우선 → source priority)
        def sort_key(off):
            is_winner = (winner and off['id'] == winner['id'])
            return (-img_count(off), 0 if is_winner else 1, SOURCE_PRIORITY.get(off['source_site'], 99))
        candidates = sorted(members, key=sort_key)
        donor = candidates[0] if candidates else winner

    images = images_by_ace.get(donor['ace_product_id'], []) if donor else []
    return donor, images[:MAX_IMAGES]


def run(conn, dry_run=True):
    listings, offerings_by_listing, offering_by_id, images_by_ace = load_all(conn)
    cur = conn.cursor()

    stats = {
        'total': len(listings), 'donor_winner': 0, 'donor_fallback': 0,
        'no_image': 0, 'images_loaded': 0, 'donor_by_source': defaultdict(int),
    }

    rows = []
    BATCH = 2000
    processed = 0

    def flush():
        if dry_run:
            rows.clear(); return
        if rows:
            cur.executemany("""
                INSERT INTO listing_images
                    (listing_id, position, source_site, source_image_url, cloudflare_image_url,
                     buyma_image_path, is_uploaded)
                VALUES (%(listing_id)s, %(position)s, %(source_site)s, %(source_image_url)s,
                        %(cloudflare_image_url)s, %(buyma_image_path)s, %(is_uploaded)s)
                ON DUPLICATE KEY UPDATE
                    source_site=VALUES(source_site),
                    source_image_url=VALUES(source_image_url),
                    cloudflare_image_url=VALUES(cloudflare_image_url),
                    buyma_image_path=VALUES(buyma_image_path),
                    is_uploaded=VALUES(is_uploaded),
                    updated_at=CURRENT_TIMESTAMP
            """, rows)
            conn.commit()
            rows.clear()

    for listing in listings:
        winner = offering_by_id.get(listing['winner_offering_id'])
        donor, images = pick_donor(listing, offerings_by_listing, offering_by_id, images_by_ace)

        if not images:
            stats['no_image'] += 1
            continue

        if winner and donor and donor['id'] == winner['id']:
            stats['donor_winner'] += 1
        else:
            stats['donor_fallback'] += 1
        stats['donor_by_source'][donor['source_site']] += 1

        for pos, img in enumerate(images, start=1):
            rows.append({
                'listing_id': listing['id'],
                'position': pos,
                'source_site': donor['source_site'],
                'source_image_url': img['source_image_url'],
                'cloudflare_image_url': img['cloudflare_image_url'],
                'buyma_image_path': img['buyma_image_path'],
                'is_uploaded': 1 if img['cloudflare_image_url'] else 0,
            })
            stats['images_loaded'] += 1

        processed += 1
        if len(rows) >= BATCH:
            flush()
        if processed % 3000 == 0:
            logger.info(f"  진행: {processed}/{len(listings)}, images={stats['images_loaded']}")

    flush()
    return stats


def print_report(stats, dry_run):
    mode = "DRY-RUN" if dry_run else "EXECUTED"
    logger.info("=" * 60)
    logger.info(f"  MERGE 이미지 적재 결과 [{mode}]")
    logger.info("=" * 60)
    logger.info(f"  출품가능 listing:             {stats['total']}")
    logger.info(f"  donor=winner:                 {stats['donor_winner']}")
    logger.info(f"  donor=폴백(최다 멤버):        {stats['donor_fallback']}")
    logger.info(f"  이미지 없음:                  {stats['no_image']}")
    logger.info(f"  listing_images 적재:          {stats['images_loaded']}장")
    logger.info("-" * 60)
    logger.info("  donor 수집처 분포:")
    for src, cnt in sorted(stats['donor_by_source'].items(), key=lambda x: -x[1]):
        logger.info(f"    {src:18} {cnt}건")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='MERGE 5단계 — listing_images 적재')
    parser.add_argument('--execute', action='store_true', help='실제 적재 (없으면 DRY-RUN)')
    args = parser.parse_args()
    dry_run = not args.execute

    logger.info(f"image_union_loader_merge 시작 (mode: {'DRY-RUN' if dry_run else 'EXECUTE'})")
    conn = get_connection()
    try:
        stats = run(conn, dry_run=dry_run)
        print_report(stats, dry_run)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
