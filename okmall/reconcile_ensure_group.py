# -*- coding: utf-8 -*-
"""
MERGE reconcile — ensure_group: 한 model_no의 그룹만 즉석에 빌드 (배치 대체, 내재형)

배치 merge(dedup_corrector_merge → resolve → options → images)를 "한 그룹만" 버전으로.
register/stock(reconcile)이 상품 처리 시 그 그룹만 묶어 merge 테이블에 upsert한다.

[그룹핑] 규칙 변경 없음 — 기존 build_duplicate_groups 를 그대로 재사용.
  배치와 동일하게 동작(과병합 포함). 과병합 수정은 별개 과제(나중).
  단, 브랜드 스코프로 입력을 좁혀 한 그룹만 빠르게 계산.

[1단계] 이 파일에선 그룹핑(멤버 산출)만 구현·검증.
  이후 upsert/options/resolve/images 는 검증 후 이어 붙인다.
"""

import argparse
from collections import defaultdict

import reconcile_buyma_push as push
from dedup_corrector_merge import canonicalize, SOURCE_PRIORITY
from offering_options_loader_merge import load_options, INSERT_SQL as OPT_INSERT_SQL
from resolve_merge import resolve_listing, DEFAULT_SHIPPING_FEE
from image_union_loader_merge import combine_images, MAX_IMAGES


MIN_FUZZY_LEN = 6  # contains 편입 시 짧은 canonical 최소 길이 (짧은 색상코드 오편입 방지)


def _load_brand_aces(conn, brand_id):
    """같은 브랜드의 살아있는 ace 행 (그룹핑·소싱 후보).

    ★ 2026-07-22: 옛 `OR status='duple'` 제거.
      죽은(is_active=0) duple 은 STOCK 이 갱신하지 않으므로(대상 조건 ap.is_active=1)
      매입가·재고가 죽은 시점에 냉동된다. 그런데 winner 는 최저 매입가로 뽑히므로
      냉동된 옛 가격이 계속 이겨서, 실제로 살 수 없는 소싱처가 winner 가 됐다.
      (실사례: 주문 34975478 취소 — reports/issue_dead_ace_walkthrough_order_34975478.md)
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, source_site, name, brand_id, brand_name, category_id, model_no,
                   source_product_url, source_model_id, purchase_price_krw, is_active, status
            FROM ace_products
            WHERE brand_id = %s AND is_active = 1
              AND model_no IS NOT NULL AND model_no <> ''
        """, (brand_id,))
        return cur.fetchall()


def _find_existing_listing(conn, seed_canon, brand_id):
    """seed canonical 을 이미 포함한 기존 listing 찾기 (증분 편입의 핵심).

    기존 배치가 만든 클러스터를 보존: 같은 브랜드 listing 의 멤버(offering→ace) 중
    canonical 이 seed 와 exact 일치하면 그 listing 으로 편입.
    (fuzzy contains 는 exact 가 없을 때만, 길이 가드)
    반환: listing_id or None
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT l.id AS listing_id, a.model_no
            FROM buyma_listings l
            JOIN source_offerings so ON so.listing_id = l.id AND so.is_active = 1
            JOIN ace_products a ON a.id = so.ace_product_id
            WHERE l.is_active = 1 AND l.brand_id = %s
        """, (brand_id,))
        rows = cur.fetchall()

    exact_hit = None
    fuzzy_hit = None
    for r in rows:
        c = canonicalize(r['model_no'])
        if c == seed_canon:
            exact_hit = r['listing_id']
            break
        if fuzzy_hit is None:
            short, long = (seed_canon, c) if len(seed_canon) <= len(c) else (c, seed_canon)
            if len(short) >= MIN_FUZZY_LEN and long.startswith(short):
                fuzzy_hit = r['listing_id']
    return exact_hit or fuzzy_hit


def compute_group_members(conn, model_no, brand_id, _existing_listing_id=None):
    """seed (model_no, brand_id) → 같은 그룹 멤버 ace 행들 (증분 방식).

    1. seed canonical 이 속한 기존 listing 이 있으면 → 그 listing 의 멤버 그대로 (배치 클러스터 보존)
    2. 없으면 → 신규 그룹: 같은 브랜드에서 canonical exact 일치 ace 모음
    반환: (seed_canon, [ace rows], existing_listing_id or None)
    """
    seed_canon = canonicalize(model_no)
    if len(seed_canon) < 4:
        return seed_canon, [], None

    listing_id = _existing_listing_id or _find_existing_listing(conn, seed_canon, brand_id)

    if listing_id:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.id, a.source_site, a.name, a.brand_id, a.brand_name, a.category_id,
                       a.model_no, a.source_product_url, a.source_model_id, a.purchase_price_krw,
                       a.is_active, a.status
                FROM source_offerings so JOIN ace_products a ON a.id = so.ace_product_id
                WHERE so.listing_id = %s AND so.is_active = 1
            """, (listing_id,))
            members = cur.fetchall()
        # ★ 처리 중인 상품(seed) + 같은 그룹인데 아직 offering 아닌 ace 들을 편입.
        #   그룹 canonical 집합(기존 멤버 + seed) 과 exact 일치하는 brand ace 를 추가.
        member_canons = {canonicalize(m['model_no']) for m in members} | {seed_canon}
        existing_ids = {m['id'] for m in members}
        for a in _load_brand_aces(conn, brand_id):
            if a['id'] in existing_ids:
                continue
            if canonicalize(a['model_no']) in member_canons:
                members.append(a)
                existing_ids.add(a['id'])
        return seed_canon, members, listing_id

    # 신규 그룹: exact canonical 일치 ace (같은 브랜드)
    aces = _load_brand_aces(conn, brand_id)
    members = [a for a in aces if canonicalize(a['model_no']) == seed_canon]
    return seed_canon, members, None


# ============================================================
# 그룹 빌드: upsert listing/offerings → options → resolve → images
# (각 단계 기존 검증 함수 재사용, 스코프만 한 그룹)
# ============================================================

def _seed_ace(members):
    """정체성 seed: 우선순위 최상위 source, active 우선."""
    return sorted(members, key=lambda a: (SOURCE_PRIORITY.get(a['source_site'], 99),
                                          0 if a['is_active'] == 1 else 1, a['id']))[0]


def _fee_map(conn):
    fee = {}
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT buyma_category_id, expected_shipping_fee FROM buyma_master_categories_data")
            for r in cur.fetchall():
                if r['expected_shipping_fee'] is not None:
                    fee[r['buyma_category_id']] = int(float(r['expected_shipping_fee']))
        except Exception:
            pass
    return fee


def _scoped_indexes_for_options(conn, members, offerings):
    """load_options 용 (ace_key_idx, variants_idx) 스코프 빌드."""
    ace_key_idx = defaultdict(list)
    for a in members:
        ace_key_idx[(a['source_site'], a['model_no'])].append(a['id'])
    ace_ids = [a['id'] for a in members]
    variants_idx = defaultdict(list)
    if ace_ids:
        with conn.cursor() as cur:
            fmt = ','.join(['%s'] * len(ace_ids))
            cur.execute(f"""
                SELECT ace_product_id, color_value, size_value, color_value_original,
                       size_value_original, source_option_code, stock_type, stocks, source_stock_status
                FROM ace_product_variants WHERE ace_product_id IN ({fmt})
            """, ace_ids)
            for v in cur.fetchall():
                variants_idx[v['ace_product_id']].append(v)
    return ace_key_idx, variants_idx


def _load_offerings(conn, listing_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT id, listing_id, source_site, source_model_id, ace_product_id, purchase_price_krw
                       FROM source_offerings WHERE listing_id=%s AND is_active=1""", (listing_id,))
        return cur.fetchall()


def _resolve_inputs(conn, listing_id, listing_row, offerings):
    off_ids = [o['id'] for o in offerings]
    ace_ids = [o['ace_product_id'] for o in offerings if o['ace_product_id']]
    options_by_offering = defaultdict(list)
    ace_info = {}
    with conn.cursor() as cur:
        if off_ids:
            fmt = ','.join(['%s'] * len(off_ids))
            cur.execute(f"""SELECT id, offering_id, color_value, size_value, stock_type, stocks
                            FROM source_offering_options WHERE offering_id IN ({fmt})""", off_ids)
            for r in cur.fetchall():
                options_by_offering[r['offering_id']].append(r)
        if ace_ids:
            fmt = ','.join(['%s'] * len(ace_ids))
            cur.execute(f"""SELECT id, buyma_lowest_price, buyma_lowest_price_checked_at, buying_shop_name
                            FROM ace_products WHERE id IN ({fmt})""", ace_ids)
            ace_info = {r['id']: r for r in cur.fetchall()}
    return options_by_offering, ace_info


def ensure_group(conn, model_no, brand_id, dry_run=True):
    """한 그룹을 즉석 빌드: 멤버 → upsert → 옵션 → winner/옵션union → 이미지.
    dry_run: 쓰기 없이 계산 결과만(기존 listing 한정 resolve 미리보기).
    반환: {listing_id, members, resolve} (resolve는 winner/selling/listing_options 등)
    """
    seed_canon, members, existing_id = compute_group_members(conn, model_no, brand_id)
    if not members:
        return {'listing_id': existing_id, 'members': [], 'resolve': None, 'reason': 'no member'}
    seed = _seed_ace(members)

    if dry_run:
        # 기존 listing 있으면 그 데이터로 resolve 미리보기 (쓰기 없음)
        if not existing_id:
            return {'listing_id': None, 'members': members, 'resolve': None,
                    'reason': 'new group (execute 필요)'}
        offerings = _load_offerings(conn, existing_id)
        listing_row = {'id': existing_id, 'category_id': seed['category_id']}
        opt_by_off, ace_info = _resolve_inputs(conn, existing_id, listing_row, offerings)
        r = resolve_listing(listing_row, offerings, opt_by_off, ace_info, _fee_map(conn))
        return {'listing_id': existing_id, 'members': members, 'resolve': r}

    # ---- EXECUTE ----
    listing_id = _upsert_listing(conn, _group_key(seed_canon, members, existing_id), seed, existing_id)
    _upsert_offerings(conn, listing_id, members)
    conn.commit()
    offerings = _load_offerings(conn, listing_id)
    # 옵션 적재 (기존 load_options 재사용)
    ace_key_idx, variants_idx = _scoped_indexes_for_options(conn, members, offerings)
    load_options(conn, [{'id': o['id'], 'source_site': o['source_site'],
                         'source_model_id': o['source_model_id'],
                         'purchase_price_krw': o['purchase_price_krw']} for o in offerings],
                 ace_key_idx, variants_idx, dry_run=False)
    # resolve (winner + 옵션 union)
    listing_row = {'id': listing_id, 'category_id': seed['category_id']}
    opt_by_off, ace_info = _resolve_inputs(conn, listing_id, listing_row, offerings)
    r = resolve_listing(listing_row, offerings, opt_by_off, ace_info, _fee_map(conn))
    _write_resolve(conn, listing_id, offerings, r)
    # 이미지
    if r.get('status') == 'ok':
        _write_images(conn, listing_id, offerings, r['winner']['id'])
    conn.commit()
    return {'listing_id': listing_id, 'members': members, 'resolve': r}


def _group_key(seed_canon, members, existing_id):
    """신규 그룹 group_key = 멤버 canonical 중 최단(베이스). 기존이면 그대로."""
    canons = {canonicalize(m['model_no']) for m in members} or {seed_canon}
    return min(canons, key=len)


def _upsert_listing(conn, group_key, seed, existing_id):
    if existing_id:
        return existing_id
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO buyma_listings
                (group_key, name, brand_id, brand_name, category_id, model_no, control, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, 'draft', 1)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), brand_id=VALUES(brand_id), brand_name=VALUES(brand_name),
                category_id=VALUES(category_id), model_no=VALUES(model_no), updated_at=CURRENT_TIMESTAMP
        """, (group_key, seed['name'], seed['brand_id'], seed['brand_name'],
              seed['category_id'], seed['model_no']))
        cur.execute("SELECT id FROM buyma_listings WHERE group_key=%s", (group_key,))
        return cur.fetchone()['id']


def _upsert_offerings(conn, listing_id, members):
    by_key = {}
    for a in members:
        k = (a['source_site'], a['model_no'])
        cur_rep = by_key.get(k)
        if cur_rep is None or (a['is_active'] == 1 and cur_rep['is_active'] != 1):
            by_key[k] = a
    with conn.cursor() as cur:
        for (src, mid), rep in by_key.items():
            cur.execute("""
                INSERT INTO source_offerings
                    (listing_id, ace_product_id, source_site, source_product_url,
                     source_model_id, purchase_price_krw, is_margin_ok, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, 0, 1)
                ON DUPLICATE KEY UPDATE
                    ace_product_id=VALUES(ace_product_id), source_product_url=VALUES(source_product_url),
                    purchase_price_krw=VALUES(purchase_price_krw), is_active=1, updated_at=CURRENT_TIMESTAMP
            """, (listing_id, rep['id'], src, rep['source_product_url'], mid, rep['purchase_price_krw']))


def _write_resolve(conn, listing_id, offerings, r):
    with conn.cursor() as cur:
        if r['status'] == 'ok':
            for off in offerings:
                rate, amount, is_ok = r['margins'][off['id']]
                cur.execute("""UPDATE source_offerings SET margin_rate=%s, margin_amount_krw=%s,
                               is_margin_ok=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                            (rate, amount, 1 if is_ok else 0, off['id']))
            cur.execute("""UPDATE buyma_listings SET price=%s, buyma_lowest_price=%s, is_lowest_price=%s,
                           winner_offering_id=%s, buying_shop_name=%s, control='draft', updated_at=CURRENT_TIMESTAMP
                           WHERE id=%s""",
                        (r['selling'], r['competitor'], 1, r['winner']['id'], r['winner_shop'], listing_id))
            # listing_options 재적재 (기존 비우고 union 다시)
            cur.execute("UPDATE listing_options SET is_active=0 WHERE listing_id=%s", (listing_id,))
            for o in r['listing_options']:
                cur.execute("""INSERT INTO listing_options
                    (listing_id, color_value, size_value, stock_type, stocks, sourced_offering_option_id, is_active)
                    VALUES (%s,%s,%s,%s,%s,%s,1)
                    ON DUPLICATE KEY UPDATE stock_type=VALUES(stock_type), stocks=VALUES(stocks),
                        sourced_offering_option_id=VALUES(sourced_offering_option_id), is_active=1,
                        updated_at=CURRENT_TIMESTAMP""",
                    (listing_id, o['color_value'], o['size_value'], o['stock_type'], o['stocks'],
                     o['sourced_offering_option_id']))
        elif r['status'] == 'no_margin':
            for off in offerings:
                rate, amount, is_ok = r['margins'][off['id']]
                cur.execute("""UPDATE source_offerings SET margin_rate=%s, margin_amount_krw=%s,
                               is_margin_ok=0, updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                            (rate, amount, off['id']))
            cur.execute("""UPDATE buyma_listings SET price=%s, winner_offering_id=NULL,
                           control='draft', updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                        (r['selling'], listing_id))


def _write_images(conn, listing_id, offerings, winner_offering_id):
    offering_by_id = {o['id']: o for o in offerings}
    offerings_by_listing = {listing_id: offerings}
    ace_ids = [o['ace_product_id'] for o in offerings if o['ace_product_id']]
    images_by_ace = defaultdict(list)
    if ace_ids:
        with conn.cursor() as cur:
            fmt = ','.join(['%s'] * len(ace_ids))
            cur.execute(f"""SELECT ace_product_id, position, source_image_url, cloudflare_image_url, buyma_image_path
                            FROM ace_product_images WHERE ace_product_id IN ({fmt})
                            ORDER BY ace_product_id, position""", ace_ids)
            for r in cur.fetchall():
                images_by_ace[r['ace_product_id']].append(r)
    listing = {'id': listing_id, 'winner_offering_id': winner_offering_id}
    # winner 부터 전 소싱 이미지를 이어붙여 최대 20장 (대표=winner 첫 이미지 → 뱃지 일치)
    images = combine_images(listing, offerings_by_listing, offering_by_id, images_by_ace)
    if not images:
        return
    def _ins(cur, pos, img):
        cur.execute("""INSERT INTO listing_images
            (listing_id, position, source_site, source_image_url, cloudflare_image_url, buyma_image_path, is_uploaded)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (listing_id, pos, img['source_site'], img['source_image_url'],
             img['cloudflare_image_url'], img['buyma_image_path'], 1 if img['cloudflare_image_url'] else 0))

    with conn.cursor() as cur:
        # 대표사진(및 기존 자리)은 고정하되, 새 소싱에서 들어온 '아직 없는' 이미지는 빈 뒤쪽에
        #   추가해 20장까지 채운다.
        #   - 미등록(신규): winner-first 로 새로 구성 → 대표=winner 첫 이미지(뱃지 썸네일과 일치).
        #   - 이미 등록됨: 기존 자리는 절대 안 바꾼다(대표 고정 = 첫 등록 winner 유지). 대신 아직
        #     없는 새 이미지만 뒤에 append → 단일이던 상품이 나중에 중복(새 수집처)이 되면
        #     이미지가 20장까지 늘어난다. 같은 URL 은 건너뜀(멱등 — 매 실행마다 중복 추가 방지).
        cur.execute("SELECT buyma_product_id FROM buyma_listings WHERE id=%s", (listing_id,))
        _row = cur.fetchone()
        already_live = bool(_row and _row.get('buyma_product_id'))
        if already_live:
            cur.execute("SELECT position, cloudflare_image_url FROM listing_images WHERE listing_id=%s", (listing_id,))
            _ex = cur.fetchall()
            _have = {r['cloudflare_image_url'] for r in _ex}
            _pos = max((r['position'] for r in _ex), default=0)
            for img in images:
                if _pos >= MAX_IMAGES:
                    break
                if img['cloudflare_image_url'] in _have:
                    continue   # 이미 있는 이미지(대표 등) = 고정, 건너뜀
                _pos += 1
                _ins(cur, _pos, img)
                _have.add(img['cloudflare_image_url'])
        else:
            cur.execute("DELETE FROM listing_images WHERE listing_id=%s", (listing_id,))
            for pos, img in enumerate(images, start=1):
                _ins(cur, pos, img)


# ============================================================
# 검증: ensure_group 그룹핑 vs 현재 배치-적재 멤버 비교
# ============================================================

def _validate(conn, listing_ids):
    n_same = 0
    for lid in listing_ids:
        with conn.cursor() as cur:
            cur.execute("SELECT group_key, name, model_no, brand_id FROM buyma_listings WHERE id=%s", (lid,))
            l = cur.fetchone()
            cur.execute("""
                SELECT a.id, a.model_no, a.source_site
                FROM source_offerings so JOIN ace_products a ON a.id=so.ace_product_id
                WHERE so.listing_id=%s
            """, (lid,))
            batch_members = cur.fetchall()
        seed_model = l['model_no'] or (batch_members[0]['model_no'] if batch_members else None)
        seed_canon, eg_members, _lid = compute_group_members(conn, seed_model, l['brand_id'])
        batch_ids = {m['id'] for m in batch_members}
        eg_ids = {m['id'] for m in eg_members}
        same = batch_ids == eg_ids
        n_same += 1 if same else 0
        mark = '✅ 동일' if same else '⚠ 다름'
        print(f"#{lid:<6} {l['name'][:40]:<40} 배치{len(batch_ids):>3} / eg{len(eg_ids):>3}  {mark}")
        if not same:
            ob, oe = batch_ids - eg_ids, eg_ids - batch_ids
            if ob:
                print(f"        배치에만 {len(ob)}: " + ', '.join(repr(m['model_no']) for m in batch_members if m['id'] in ob)[:120])
            if oe:
                print(f"        eg에만 {len(oe)}: " + ', '.join(repr(m['model_no']) for m in eg_members if m['id'] in oe)[:120])
    print(f"\n일치: {n_same}/{len(listing_ids)}")


def main():
    ap = argparse.ArgumentParser(description='ensure_group 그룹핑 검증')
    ap.add_argument('--listing-ids', type=str, help='쉼표구분 listing id')
    args = ap.parse_args()
    conn = push.get_connection()
    try:
        if args.listing_ids:
            _validate(conn, [int(x) for x in args.listing_ids.split(',')])
    finally:
        conn.close()


if __name__ == '__main__':
    main()
