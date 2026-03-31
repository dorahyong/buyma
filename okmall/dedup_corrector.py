# -*- coding: utf-8 -*-
"""
멀티소스 중복 상품 데이터 보정 스크립트

1. raw_scraped_data에서 model_id 정규화 → 같은 상품 그룹핑 (exact + fuzzy)
2. 그룹별 primary source 결정 (okmall > nextzennpack > labellusso > trendmecca > kasina)
3. okmall primary + 이미지 없음 → best source에서 이미지 복사
4. 나머지 source의 ace_products → status='duple', is_active=0

Usage:
    python dedup_corrector.py --dry-run     # 영향 범위 확인만
    python dedup_corrector.py               # 실제 실행
"""

import os
import re
import argparse
import logging
from collections import defaultdict
from datetime import datetime

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 데이터 우선순위 (낮을수록 우선)
SOURCE_PRIORITY = {
    'okmall': 0,
    'nextzennpack': 1,
    'labellusso': 2,
    'trendmecca': 3,
    'kasina': 4,
}

# 이미지 우선순위 (okmall 제외, 낮을수록 우선)
IMAGE_PRIORITY = {
    'nextzennpack': 0,
    'labellusso': 1,
    'trendmecca': 2,
    'kasina': 3,
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


# ============================================================
# Phase 1: 정규화 함수
# ============================================================

def canonicalize(model_id: str) -> str:
    """model_id를 정규화하여 cross-source 비교 가능한 형태로 변환"""
    if not model_id:
        return ''
    s = model_id.strip()
    # 1. 괄호 접미 제거: "(OOO)", "(NOO)" 등 (labellusso 패턴)
    s = re.sub(r'\s*\([A-Z]{2,4}\)\s*$', '', s)
    # 2. 슬래시 병기에서 마지막 항목만: "A / B" → "B"
    if ' / ' in s:
        s = s.split(' / ')[-1].strip()
    # 3. 시즌 접두사 제거: "25FW-", "25SS-" 등
    s = re.sub(r'^\d{2}[A-Z]{2}-', '', s)
    # 4. 공백/하이픈/슬래시/백틱/어포스트로피 제거 + 대문자
    s = re.sub(r'[\s\-/`\']+', '', s).upper()
    return s


def normalize_brand(brand: str) -> str:
    if not brand:
        return ''
    return re.sub(r'[\s\-/\.`\']+', '', brand).upper()


# ============================================================
# Phase 2: 중복 그룹 생성
# ============================================================

def load_raw_model_ids(conn):
    """raw_scraped_data에서 전체 model_id 로드"""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT source_site, brand_name_en, model_id
        FROM raw_scraped_data
        WHERE model_id IS NOT NULL AND model_id != ''
        AND source_site IN ('okmall', 'kasina', 'nextzennpack', 'labellusso', 'trendmecca')
    """)
    rows = cur.fetchall()
    logger.info(f"raw_scraped_data에서 {len(rows)}개 (source, brand, model_id) 로드")
    return rows


def build_duplicate_groups(rows):
    """정규화 → exact match 그룹 → fuzzy(contains) 병합"""

    # Step 1: canonical_model_id 기준 그룹핑
    canonical_groups = defaultdict(list)  # canonical -> [(source, brand, model_id)]
    for row in rows:
        cid = canonicalize(row['model_id'])
        if len(cid) < 4:
            continue
        canonical_groups[cid].append({
            'source_site': row['source_site'],
            'brand': row['brand_name_en'],
            'brand_norm': normalize_brand(row['brand_name_en']),
            'model_id': row['model_id'],
            'canonical': cid,
        })

    # Step 2: exact match 그룹 중 multi-source만 필터
    exact_groups = {}
    single_items = {}  # canonical -> item (단일 source)
    for cid, items in canonical_groups.items():
        sources = set(it['source_site'] for it in items)
        if len(sources) >= 2:
            exact_groups[cid] = items
        else:
            single_items[cid] = items

    logger.info(f"정규화 후 exact match 중복 그룹: {len(exact_groups)}개")

    # Step 3: fuzzy(contains) 매칭 — 같은 브랜드 내, single_items에서 exact_groups로 병합
    # single_items 중 exact_groups의 어떤 canonical에 contains 관계인 것을 찾음
    fuzzy_merged = 0
    exact_cids_by_brand = defaultdict(list)
    for cid, items in exact_groups.items():
        for it in items:
            exact_cids_by_brand[it['brand_norm']].append(cid)

    single_cids_by_brand = defaultdict(list)
    for cid, items in single_items.items():
        if len(cid) >= 6:
            single_cids_by_brand[items[0]['brand_norm']].append(cid)

    for brand_norm in set(exact_cids_by_brand.keys()) & set(single_cids_by_brand.keys()):
        e_cids = exact_cids_by_brand[brand_norm]
        s_cids = single_cids_by_brand[brand_norm]
        for s_cid in s_cids:
            for e_cid in e_cids:
                if s_cid == e_cid:
                    continue
                short, long = (s_cid, e_cid) if len(s_cid) <= len(e_cid) else (e_cid, s_cid)
                if short in long:
                    # single_item을 exact_group에 병합
                    exact_groups[e_cid].extend(single_items[s_cid])
                    fuzzy_merged += 1
                    break

    # Step 4: single_items 끼리도 같은 브랜드 내 contains 매칭
    for brand_norm, s_cids in single_cids_by_brand.items():
        if len(s_cids) < 2:
            continue
        s_cids_sorted = sorted(s_cids, key=len)
        used = set()
        for i in range(len(s_cids_sorted)):
            if s_cids_sorted[i] in used:
                continue
            for j in range(i + 1, len(s_cids_sorted)):
                if s_cids_sorted[j] in used:
                    continue
                short, long = s_cids_sorted[i], s_cids_sorted[j]
                if short in long:
                    # 두 single을 합쳐 새 그룹 생성
                    items_a = single_items.get(short, [])
                    items_b = single_items.get(long, [])
                    combined = items_a + items_b
                    sources = set(it['source_site'] for it in combined)
                    if len(sources) >= 2:
                        exact_groups[short] = combined
                        used.add(short)
                        used.add(long)
                        fuzzy_merged += 1
                        break

    logger.info(f"fuzzy(contains) 추가 병합: {fuzzy_merged}건")
    logger.info(f"최종 중복 그룹: {len(exact_groups)}개")

    return exact_groups


# ============================================================
# Phase 3: 그룹별 처리
# ============================================================

def process_groups(conn, groups, dry_run=True):
    """각 그룹에서 primary 결정, 이미지 복사, duple 처리"""
    cur = conn.cursor()

    stats = {
        'total_groups': len(groups),
        'okmall_primary': 0,
        'other_primary': 0,
        'images_copied': 0,
        'duple_marked': 0,
        'duple_published': 0,  # is_published=1인데 duple 되는 것
        'skipped_no_ace': 0,
    }

    batch_count = 0
    BATCH_SIZE = 50  # 50그룹마다 commit (lock timeout 방지)

    for cid, items in groups.items():
        # 그룹 내 source별 model_id 정리
        source_models = {}  # source_site -> set of model_ids
        for it in items:
            source_models.setdefault(it['source_site'], set()).add(it['model_id'])

        sources_in_group = sorted(source_models.keys(), key=lambda s: SOURCE_PRIORITY.get(s, 99))
        primary_source = sources_in_group[0]
        primary_model_ids = source_models[primary_source]
        non_primary_sources = sources_in_group[1:]

        if primary_source == 'okmall':
            stats['okmall_primary'] += 1
        else:
            stats['other_primary'] += 1

        # --- Primary의 ace_products 조회 ---
        primary_ace_ids = []
        for mid in primary_model_ids:
            cur.execute("""
                SELECT id, model_no, is_published, is_active
                FROM ace_products
                WHERE source_site = %s AND model_no = %s AND is_active = 1
            """, (primary_source, mid))
            for row in cur.fetchall():
                primary_ace_ids.append(row)

        if not primary_ace_ids:
            stats['skipped_no_ace'] += 1
            # primary에 ace가 없어도, non-primary끼리 duple 처리는 해야 함
            # non-primary 중 최상위를 새 primary로
            if len(non_primary_sources) >= 2:
                new_primary = non_primary_sources[0]
                non_primary_sources = non_primary_sources[1:]
                for mid in source_models[new_primary]:
                    cur.execute("""
                        SELECT id, model_no, is_published, is_active
                        FROM ace_products
                        WHERE source_site = %s AND model_no = %s AND is_active = 1
                    """, (new_primary, mid))
                    for row in cur.fetchall():
                        primary_ace_ids.append(row)

        # --- okmall primary일 때 이미지 복사 ---
        if primary_source == 'okmall' and primary_ace_ids:
            # okmall에 이미지 없는 ace_product 찾기
            for ace in primary_ace_ids:
                cur.execute("""
                    SELECT COUNT(*) as cnt FROM ace_product_images
                    WHERE ace_product_id = %s AND (cloudflare_image_url IS NOT NULL OR source_image_url IS NOT NULL)
                """, (ace['id'],))
                img_count = cur.fetchone()['cnt']

                if img_count == 0:
                    # 이미지 제공할 best source 찾기
                    donor_found = False
                    for donor_source in sorted(non_primary_sources, key=lambda s: IMAGE_PRIORITY.get(s, 99)):
                        for donor_mid in source_models[donor_source]:
                            cur.execute("""
                                SELECT ap.id as ace_id FROM ace_products ap
                                WHERE ap.source_site = %s AND ap.model_no = %s
                                AND EXISTS (SELECT 1 FROM ace_product_images img WHERE img.ace_product_id = ap.id)
                                LIMIT 1
                            """, (donor_source, donor_mid))
                            donor_ace = cur.fetchone()
                            if donor_ace:
                                # 이미지 복사
                                cur.execute("""
                                    SELECT source_image_url, cloudflare_image_url, position
                                    FROM ace_product_images
                                    WHERE ace_product_id = %s
                                    ORDER BY position
                                """, (donor_ace['ace_id'],))
                                donor_images = cur.fetchall()

                                if donor_images:
                                    if not dry_run:
                                        for img in donor_images:
                                            cur.execute("""
                                                INSERT INTO ace_product_images
                                                (ace_product_id, position, source_image_url, cloudflare_image_url, is_uploaded)
                                                VALUES (%s, %s, %s, %s, %s)
                                            """, (
                                                ace['id'],
                                                img['position'],
                                                img['source_image_url'],
                                                img['cloudflare_image_url'],
                                                1 if img['cloudflare_image_url'] else 0,
                                            ))
                                    stats['images_copied'] += 1
                                    logger.debug(f"이미지 복사: okmall ace_id={ace['id']} ← {donor_source} ace_id={donor_ace['ace_id']} ({len(donor_images)}장)")
                                    donor_found = True
                                    break
                        if donor_found:
                            break

        # --- Non-primary ace_products → duple 처리 ---
        for np_source in non_primary_sources:
            for mid in source_models[np_source]:
                cur.execute("""
                    SELECT id, is_published, buyma_product_id
                    FROM ace_products
                    WHERE source_site = %s AND model_no = %s AND is_active = 1
                """, (np_source, mid))
                np_aces = cur.fetchall()

                for np_ace in np_aces:
                    if np_ace['is_published'] == 1:
                        stats['duple_published'] += 1

                    if not dry_run:
                        cur.execute("""
                            UPDATE ace_products
                            SET status = 'duple', is_active = 0
                            WHERE id = %s
                        """, (np_ace['id'],))

                    stats['duple_marked'] += 1

        # 배치 commit
        batch_count += 1
        if not dry_run and batch_count % BATCH_SIZE == 0:
            conn.commit()
            logger.info(f"  진행: {batch_count}/{len(groups)} 그룹 처리, duple={stats['duple_marked']}, img={stats['images_copied']}")

    if not dry_run:
        conn.commit()

    return stats


# ============================================================
# Phase 4: 리포트
# ============================================================

def print_report(stats, dry_run):
    mode = "DRY-RUN" if dry_run else "EXECUTED"
    logger.info("=" * 60)
    logger.info(f"  중복 데이터 보정 결과 [{mode}]")
    logger.info("=" * 60)
    logger.info(f"  총 중복 그룹 수:              {stats['total_groups']}")
    logger.info(f"  okmall primary 그룹:          {stats['okmall_primary']}")
    logger.info(f"  타 source primary 그룹:       {stats['other_primary']}")
    logger.info(f"  primary에 ace 없어 skip:      {stats['skipped_no_ace']}")
    logger.info(f"  이미지 복사 (okmall ← 타):    {stats['images_copied']}건")
    logger.info(f"  duple 처리된 ace_products:     {stats['duple_marked']}건")
    logger.info(f"  ⚠ duple 중 is_published=1:    {stats['duple_published']}건 (바이마 삭제 필요)")
    logger.info("=" * 60)


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='멀티소스 중복 상품 데이터 보정')
    parser.add_argument('--dry-run', action='store_true', help='변경 없이 영향 범위만 확인')
    args = parser.parse_args()

    logger.info(f"dedup_corrector 시작 (mode: {'DRY-RUN' if args.dry_run else 'EXECUTE'})")

    conn = get_connection()
    try:
        # 1. raw 데이터 로드
        rows = load_raw_model_ids(conn)

        # 2. 중복 그룹 생성
        groups = build_duplicate_groups(rows)

        # 3. 그룹별 처리
        stats = process_groups(conn, groups, dry_run=args.dry_run)

        # 4. 리포트
        print_report(stats, args.dry_run)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
