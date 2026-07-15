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


def combine_images(listing, offerings_by_listing, offering_by_id, images_by_ace):
    """listing 의 모든 소싱 이미지를 winner 부터 이어붙여 최대 MAX_IMAGES(20) 장 반환.
    각 이미지 dict 에 그 소싱의 source_site 를 실어 준다(멤버마다 다름).

    순서: winner offering 먼저 → 나머지 멤버(SOURCE_PRIORITY 순).
    R2 에 업로드된(cloudflare_image_url 있는) 이미지만 — BUYMA 는 이미지를 URL 로 받으므로
    업로드 안 된 건 보낼 수 없다. 중복 사진은 제거하지 않는다(몰마다 이미 걸러 수집).
    → 대표(position 1) = winner 첫 이미지 → 뱃지 썸네일(winner 기준 생성)과 정확히 일치해
      신규 등록부터 대표이미지에 뱃지가 붙는다. (기존 pick_donor 는 '한 개 몰'만 골라
      대표가 winner 가 아닐 수 있어 뱃지가 어긋났다 — 이 함수로 대체한다.)
    """
    winner = offering_by_id.get(listing['winner_offering_id'])
    members = offerings_by_listing.get(listing['id'], [])

    # winner 먼저, 그다음 나머지 멤버를 SOURCE_PRIORITY 순으로
    ordered = ([winner] if winner else []) + [
        o for o in sorted(members, key=lambda x: SOURCE_PRIORITY.get(x['source_site'], 99))
        if not (winner and o['id'] == winner['id'])
    ]

    def uploaded(off):
        return [i for i in images_by_ace.get(off['ace_product_id'], [])
                if off and i.get('cloudflare_image_url')]

    combined = []
    for off in ordered:
        for img in uploaded(off):
            combined.append({**img, 'source_site': off['source_site']})
            if len(combined) >= MAX_IMAGES:
                return combined
    return combined


def run(conn, dry_run=True):
    # ★ 백필도 라이브 등록과 '똑같은' 규칙을 쓰도록 reconcile_ensure_group._write_images 재사용:
    #     winner-first 합치기 + 이미 등록분은 대표 고정·새 이미지만 뒤에 추가(20장까지).
    #   예전엔 백필이 pick_donor('한 개 몰')로 DELETE+재삽입이라, 재실행하면 라이브 대표사진까지
    #   덮어써 churn 위험이 있었다. 이제 라이브/백필 로직이 완전히 일치한다.
    #   지연 import: 두 모듈이 서로 참조하므로 순환 import 방지.
    from reconcile_ensure_group import _write_images
    listings, offerings_by_listing, offering_by_id, images_by_ace = load_all(conn)

    stats = {'total': len(listings), 'no_image': 0, 'targets': 0}
    for i, listing in enumerate(listings, start=1):
        lid = listing['id']
        images = combine_images(listing, offerings_by_listing, offering_by_id, images_by_ace)
        if not images:
            stats['no_image'] += 1
            continue
        stats['targets'] += 1
        if not dry_run:
            _write_images(conn, lid, offerings_by_listing.get(lid, []), listing['winner_offering_id'])
            if i % 2000 == 0:
                conn.commit()
        if i % 5000 == 0:
            logger.info(f"  진행: {i}/{len(listings)}, 적재대상 {stats['targets']}")
    if not dry_run:
        conn.commit()
    return stats


def print_report(stats, dry_run):
    mode = "DRY-RUN" if dry_run else "EXECUTED"
    logger.info("=" * 60)
    logger.info(f"  MERGE 이미지 적재 결과 [{mode}]")
    logger.info("=" * 60)
    logger.info(f"  출품가능 listing:   {stats['total']}")
    logger.info(f"  적재 대상(이미지有): {stats['targets']}")
    logger.info(f"  이미지 없음:        {stats['no_image']}")
    if dry_run:
        logger.info("  (DRY-RUN — 실제 기록 안 함. --execute 로 _write_images 적재)")
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
