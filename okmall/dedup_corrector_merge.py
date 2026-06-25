# -*- coding: utf-8 -*-
"""
멀티소스 중복 상품 → MERGE 그룹 적재 스크립트 (2단계 GROUP)

원본: dedup_corrector.py 사본. 매칭 로직은 동일, "죽이기"를 "적재"로 교체.

1. raw_scraped_data에서 model_id 정규화 → 같은 상품 그룹핑 (exact + fuzzy)  [원본과 동일]
2. 각 그룹 → buyma_listings 1행 (group_key 부여)
3. 그룹 멤버(수집처별 상품) → source_offerings 적재 (안 죽임, ace_products 안 건드림)

2단계에서 하지 않는 것:
- 마진 계산 / winner 선정  → 4단계 RESOLVE
- 옵션·재고 적재           → 3단계 converter (source_offering_options)
- 이미지 union             → 5단계 (listing_images)
- ace_products 변경        → 없음 (읽기만)

Usage:
    python dedup_corrector_merge.py             # DRY-RUN (기본, 변경 없음)
    python dedup_corrector_merge.py --execute   # 실제 적재
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

# 데이터 우선순위 (낮을수록 우선) — 원본과 동일
SOURCE_PRIORITY = {
    'okmall': 0,
    'nextzennpack': 1,
    'labellusso': 2,
    'trendmecca': 3,
    'kasina': 4,
    'loutique': 5,
    'vvano': 6,
    'veroshopmall': 7,
    'fabstyle': 8,
    'premiumsneakers': 9,
    't1global': 10,
    'carpi': 11,
    'dmont': 12,
    'tuttobene': 13,
    'joharistore': 14,
    'thefactor2': 15,
    '9tems': 16,
    'laprima': 17,
    'maniaon': 18,
    'bblue': 19,
    'euroline': 20,
    'unico': 21,
    'kometa': 22,
    'larlashoes': 23,
    'thegrande': 24,
    'upset': 25,
    'luxlimit': 26,
    'pano': 27,
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
# Phase 1: 정규화 함수 — 원본과 동일
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
# Phase 2: 중복 그룹 생성 — 원본과 동일
# ============================================================

def load_raw_model_ids(conn):
    """raw_scraped_data에서 전체 model_id 로드"""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT source_site, brand_name_en, model_id
        FROM raw_scraped_data
        WHERE model_id IS NOT NULL AND model_id != ''
        AND source_site IN ('okmall', 'nextzennpack', 'labellusso', 'trendmecca',
                            'kasina', 'loutique', 'vvano', 'veroshopmall', 'fabstyle',
                            'premiumsneakers', 't1global', 'carpi', 'dmont', 'tuttobene',
                            'joharistore', 'thefactor2', '9tems', 'laprima',
                            'maniaon', 'bblue', 'euroline', 'unico', 'kometa',
                            'larlashoes', 'thegrande', 'upset', 'luxlimit', 'pano')
    """)
    rows = cur.fetchall()
    logger.info(f"raw_scraped_data에서 {len(rows)}개 (source, brand, model_id) 로드")
    return rows


def build_duplicate_groups(rows):
    """정규화 → exact match 그룹 → fuzzy(contains) 병합 — 원본과 동일"""

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
# Phase 3: 그룹 → 새 테이블 적재 (원본 process_groups 대체)
# ============================================================

def load_ace_index(conn):
    """활성 ace_products 전체를 1쿼리로 로드 → (source_site, model_no) 인덱스.
    그룹마다 원격 DB 왕복하는 대신 메모리 조회로 대체."""
    cur = conn.cursor()
    # is_active=1(살아남은 primary) + is_active=0 'duple'(옛 dedup이 죽인 형제) 모두 포함.
    # 죽은 멤버 행에 다른 mall의 매입가/URL이 보존돼 있어 merge 멤버로 복원해야 함.
    cur.execute("""
        SELECT id, source_site, name, brand_id, brand_name, category_id, model_no,
               source_product_url, source_model_id, purchase_price_krw,
               is_active, status
        FROM ace_products
        WHERE is_active = 1 OR status = 'duple'
    """)
    index = defaultdict(list)  # (source_site, model_no) -> [ace rows]
    n = 0
    for row in cur.fetchall():
        index[(row['source_site'], row['model_no'])].append(row)
        n += 1
    logger.info(f"ace_products 인덱스 로드(active+duple): {n}건 / 키 {len(index)}개")
    return index


def find_member_aces(ace_index, source, model_ids):
    """그룹 멤버의 활성 ace_products 조회 (메모리 인덱스). (source, model_id) 단위로 묶어 반환."""
    by_model = {}  # model_id -> [ace rows]
    for mid in model_ids:
        aces = ace_index.get((source, mid))
        if aces:
            by_model[mid] = aces
    return by_model


def load_groups_into_tables(conn, groups, ace_index, dry_run=True):
    """각 그룹 → buyma_listings 1행 + 멤버 → source_offerings 적재."""
    cur = conn.cursor()

    stats = {
        'total_groups': len(groups),
        'listings_loaded': 0,
        'offerings_loaded': 0,
        'multi_member_listings': 0,   # offering >= 2 (진짜 merge 대상)
        'skipped_no_ace': 0,
        'offerings_by_source': defaultdict(int),
    }

    batch_count = 0
    BATCH_SIZE = 50

    for cid, items in groups.items():
        # 그룹 내 source별 model_id 정리 (원본과 동일)
        source_models = {}  # source_site -> set of model_ids
        for it in items:
            source_models.setdefault(it['source_site'], set()).add(it['model_id'])

        sources_in_group = sorted(source_models.keys(), key=lambda s: SOURCE_PRIORITY.get(s, 99))

        # --- 멤버별 ace 조회 (source -> {model_id -> [ace rows]}) ---
        member_aces = {}
        for src in sources_in_group:
            by_model = find_member_aces(ace_index, src, source_models[src])
            if by_model:
                member_aces[src] = by_model

        if not member_aces:
            # 그룹 내 어느 멤버도 활성 ace 없음 → listing 만들 정체성 없음 → skip
            stats['skipped_no_ace'] += 1
            continue

        # --- 정체성 seed: 우선순위 최상위 멤버의 대표 ace, 살아있는(active) 행 우선 ---
        seed = None
        for src in sources_in_group:
            if src not in member_aces:
                continue
            for aces in member_aces[src].values():
                active = [a for a in aces if a['is_active'] == 1]
                seed = (active or aces)[0]
                break
            if seed:
                break

        # --- buyma_listings upsert (group_key 기준) ---
        listing_id = None
        if not dry_run:
            cur.execute("""
                INSERT INTO buyma_listings
                    (group_key, name, brand_id, brand_name, category_id, model_no, control, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, 'draft', 1)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    brand_id = VALUES(brand_id),
                    brand_name = VALUES(brand_name),
                    category_id = VALUES(category_id),
                    model_no = VALUES(model_no),
                    updated_at = CURRENT_TIMESTAMP
            """, (cid, seed['name'], seed['brand_id'], seed['brand_name'],
                  seed['category_id'], seed['model_no']))
            cur.execute("SELECT id FROM buyma_listings WHERE group_key = %s", (cid,))
            listing_id = cur.fetchone()['id']
        stats['listings_loaded'] += 1

        # --- 멤버 → source_offerings upsert ((listing, source, model_id) 단위) ---
        group_offerings = 0
        for src, by_model in member_aces.items():
            for mid, aces in by_model.items():
                # 같은 (source, model_id) 다색상 행이면 active 우선 대표 1개로 offering
                active = [a for a in aces if a['is_active'] == 1]
                rep = (active or aces)[0]
                stats['offerings_loaded'] += 1
                stats['offerings_by_source'][src] += 1
                group_offerings += 1
                if not dry_run:
                    cur.execute("""
                        INSERT INTO source_offerings
                            (listing_id, ace_product_id, source_site, source_product_url,
                             source_model_id, purchase_price_krw, is_margin_ok, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, 0, 1)
                        ON DUPLICATE KEY UPDATE
                            ace_product_id = VALUES(ace_product_id),
                            source_product_url = VALUES(source_product_url),
                            purchase_price_krw = VALUES(purchase_price_krw),
                            updated_at = CURRENT_TIMESTAMP
                    """, (listing_id, rep['id'], src, rep['source_product_url'],
                          mid, rep['purchase_price_krw']))

        if group_offerings >= 2:
            stats['multi_member_listings'] += 1

        batch_count += 1
        if not dry_run and batch_count % BATCH_SIZE == 0:
            conn.commit()
            logger.info(f"  진행: {batch_count}/{len(groups)} 그룹, "
                        f"listings={stats['listings_loaded']}, offerings={stats['offerings_loaded']}")

    if not dry_run:
        conn.commit()

    return stats


# ============================================================
# Phase 4: 리포트
# ============================================================

def print_report(stats, dry_run):
    mode = "DRY-RUN" if dry_run else "EXECUTED"
    logger.info("=" * 60)
    logger.info(f"  MERGE 그룹 적재 결과 [{mode}]")
    logger.info("=" * 60)
    logger.info(f"  총 중복 그룹 수:              {stats['total_groups']}")
    logger.info(f"  buyma_listings 적재:          {stats['listings_loaded']}건")
    logger.info(f"  source_offerings 적재:        {stats['offerings_loaded']}건")
    logger.info(f"  멤버 2개+ listing (진짜 merge): {stats['multi_member_listings']}건")
    logger.info(f"  ace 없어 skip한 그룹:         {stats['skipped_no_ace']}")
    logger.info("-" * 60)
    logger.info("  수집처별 offering 분포:")
    for src, cnt in sorted(stats['offerings_by_source'].items(), key=lambda x: -x[1]):
        logger.info(f"    {src:18} {cnt}건")
    logger.info("=" * 60)


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='MERGE 2단계 GROUP — 중복 그룹을 새 테이블에 적재')
    parser.add_argument('--execute', action='store_true', help='실제 적재 (없으면 DRY-RUN)')
    args = parser.parse_args()
    dry_run = not args.execute

    logger.info(f"dedup_corrector_merge (GROUP) 시작 (mode: {'DRY-RUN' if dry_run else 'EXECUTE'})")

    conn = get_connection()
    try:
        rows = load_raw_model_ids(conn)
        groups = build_duplicate_groups(rows)
        ace_index = load_ace_index(conn)
        stats = load_groups_into_tables(conn, groups, ace_index, dry_run=dry_run)
        print_report(stats, dry_run)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
