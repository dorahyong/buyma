# -*- coding: utf-8 -*-
"""
MERGE reconcile — BUYMA push 빌더 (모드 A: CREATE) [dry-run 우선]

merge 테이블(buyma_listings + listing_options + listing_images)을 읽어
BUYMA create 요청 JSON을 구성한다.

원칙:
- 운영 파일 안 건드림. 요청 빌드 로직은 **검증된 buyma_new_product_register 의
  함수를 import 재사용**(수정 X). 실제 create(3단계)에서 reference_number 발급/
  no-delete 등 merge 특화가 필요해지면 그때 사본으로 분리.
- BUYMA options 배열의 master_id / size details 는 listing_options 에 없으므로,
  각 옵션의 소싱 ace(sourced_offering_option_id → ... → ace_product_id)에서 끌어온다.
- 이 모듈은 JSON 구성까지만. 실제 API 호출/DB 반영은 하지 않는다(2단계 dry-run 범위).
"""

import os
import re
import sys
import uuid
import hashlib
from contextlib import contextmanager
from collections import Counter, defaultdict

import pymysql
from dotenv import load_dotenv

# okmall/ 를 import 경로에 추가 → 검증된 register 함수 재사용
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buyma_new_product_register as reg  # noqa: E402
import authority_flag  # noqa: E402  단일권위 전환 스위치

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))


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
# merge 테이블 조회
# ============================================================

def fetch_listing(conn, listing_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM buyma_listings WHERE id=%s", (listing_id,))
        return cur.fetchone()


def _winner_offering(conn, listing):
    if not listing.get('winner_offering_id'):
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM source_offerings WHERE id=%s", (listing['winner_offering_id'],))
        return cur.fetchone()


def _images(conn, listing_id):
    """listing_images → register.build_images_array 입력 규격(cloudflare_image_url, position).
    ★ 대표이미지(position=1)는 뱃지 썸네일이 있으면 그 주소를 대신 쓴다
      (reg.get_product_images 와 같은 규칙). CREATE/EDIT 둘 다 이 경로를 타므로
      run_daily 등록·수정 모두 뱃지가 적용된다. 원본(listing_images)은 그대로 보존.
      listing_images 엔 image_id 가 없어 thumbnails.source_cf_url 로 매칭하되,
      source_cf_url 은 인덱스가 없으므로 ace_product_id(인덱스)로 이 목록의 멤버로 먼저 좁힌다."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT position, cloudflare_image_url
            FROM listing_images
            WHERE listing_id=%s AND cloudflare_image_url IS NOT NULL
            ORDER BY position LIMIT 20
        """, (listing_id,))
        rows = cur.fetchall()
        for r in rows:
            if r['position'] != 1:
                continue
            cur.execute("""
                SELECT t.thumbnail_cloudflare_url
                FROM ace_product_thumbnails t
                JOIN source_offerings so ON so.ace_product_id = t.ace_product_id
                                       AND so.listing_id = %s AND so.is_active = 1
                WHERE t.is_generated = 1
                  AND t.source_cf_url = %s
                  AND t.thumbnail_cloudflare_url IS NOT NULL
                  AND t.thumbnail_cloudflare_url <> ''
                LIMIT 1
            """, (listing_id, r['cloudflare_image_url']))
            th = cur.fetchone()
            if th:
                r['cloudflare_image_url'] = th['thumbnail_cloudflare_url']
            break
        return rows


def _listing_options(conn, listing_id):
    """출품 옵션 + 각 옵션의 소싱 ace_product_id (master_id/details 끌어올 대상)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT lo.color_value, lo.size_value, lo.stock_type, lo.stocks,
                   so.ace_product_id
            FROM listing_options lo
            LEFT JOIN source_offering_options soo ON soo.id = lo.sourced_offering_option_id
            LEFT JOIN source_offerings so ON so.id = soo.offering_id
            WHERE lo.listing_id=%s AND lo.is_active=1
        """, (listing_id,))
        return cur.fetchall()


def _ace_option_meta(conn, ace_id, option_type, value):
    """소싱 ace 의 ace_product_options 에서 (master_id, details_json) 조회. 없으면 (0, None)."""
    if not ace_id or value is None:
        return 0, None
    with conn.cursor() as cur:
        cur.execute("""
            SELECT master_id, details_json
            FROM ace_product_options
            WHERE ace_product_id=%s AND option_type=%s AND value=%s
            LIMIT 1
        """, (ace_id, option_type, value))
        r = cur.fetchone()
    if not r:
        return 0, None
    return (r['master_id'] or 0), r['details_json']


def make_reference_number(listing):
    """merge 전용 reference_number (unique, <=50). 실제 발급·DB 저장은 3단계."""
    return f"MG{listing['id']}"


# ============================================================
# 옵션 값 표기 통합 (FREE/Free 같은 표기변종 → 대표 1개)
#   - 원본 데이터는 안 건드림. 출구(빌더)에서만 합침 → 미래 데이터도 자동 처리.
#   - 매칭/소싱은 원본 값으로, BUYMA 출력 값만 대표 표기로.
# ============================================================

def _norm_value(s):
    """대소문자/공백/하이픈/'インチ' 접미 무시한 비교 키."""
    if s is None:
        return ''
    s = s.strip().upper()
    s = re.sub(r'インチ$', '', s)
    s = re.sub(r'[\s\-]+', '', s)
    return s


def _canonical_map(values):
    """raw 값들 → {raw: 대표표기}. 정규화 키 같으면 최빈 표기로 통합(동률 시 사전순 첫)."""
    by_key = defaultdict(Counter)
    for v in values:
        if v is None:
            continue
        by_key[_norm_value(v)][v] += 1
    canon = {}
    for counter in by_key.values():
        best = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        for raw in counter:
            canon[raw] = best
    return canon


# BUYMA 재고 우선순위 (높을수록 살림) — 같은 옵션 중복 시 재고 있는 쪽 유지
_STOCK_RANK = {'stock_in_hand': 2, 'purchase_for_order': 1, 'out_of_stock': 0}


# ============================================================
# CREATE 요청 JSON 구성
# ============================================================

def build_create_request(conn, listing):
    """
    한 listing(buyma_listings 행) → BUYMA create 요청 JSON.
    전체 품절 등으로 등록 불가면 None (register.build_request_json 규칙).
    """
    winner = _winner_offering(conn, listing)
    images = _images(conn, listing['id'])
    if not images:
        # 이미지 0장으로 CREATE 하면 BUYMA 가 거부한다(images 필수) → 요청 자체를 안 보낸다.
        #   업로더가 '등록 가능한 상품만' R2 에 올리도록 바뀌면서, 그룹의 어떤 멤버도 아직
        #   업로드 안 된 순간이 생길 수 있다. 그때 빈 요청을 쏘지 않게 하는 안전망.
        return None
    lopts = _listing_options(conn, listing['id'])

    # 표기 통합 맵 (색/사이즈 각각)
    color_canon = _canonical_map([o['color_value'] for o in lopts])
    size_canon = _canonical_map([o['size_value'] for o in lopts])

    def _cc(v):
        return color_canon.get(v, v)

    def _cs(v):
        return size_canon.get(v, v)

    # 1) variants rows = ace_product_variants 모양. 대표표기 적용 + (색,사이즈) 중복은 재고 우선으로 1개.
    variant_map = {}  # (canon_color, canon_size) -> row
    for o in lopts:
        cc, cs = _cc(o['color_value']), _cs(o['size_value'])
        key = (cc, cs)
        cand = {'color_value': cc, 'size_value': cs,
                'stock_type': o['stock_type'], 'stocks': o['stocks']}
        cur_row = variant_map.get(key)
        if cur_row is None or _STOCK_RANK.get(o['stock_type'], 0) > _STOCK_RANK.get(cur_row['stock_type'], 0):
            variant_map[key] = cand

    # BUYMA 는 (색 × 사이즈) 모든 조합에 variant 필요 ("販売可否/在庫に必要なすべての選択がありません").
    # 멀티몰 union 으로 조합이 비면 거부 → 없는 조합은 out_of_stock(품절)로 채워 완전한 그리드 구성.
    colors, sizes = [], []
    for o in lopts:
        cc, cs = _cc(o['color_value']), _cs(o['size_value'])
        if cc not in colors:
            colors.append(cc)
        if cs not in sizes:
            sizes.append(cs)
    for cc in colors:
        for cs in sizes:
            if (cc, cs) not in variant_map:
                variant_map[(cc, cs)] = {'color_value': cc, 'size_value': cs,
                                         'stock_type': 'out_of_stock', 'stocks': 0}
    variant_rows = list(variant_map.values())

    # 2) options rows = ace_product_options 모양. 대표표기 distinct, master_id·details 는
    #    원본 값으로 소싱 ace 조회(대표표기로 조회하면 ace 에 없을 수 있음). register 와 동일하게 size 먼저.
    options_rows = []
    seen = set()
    for typ in ('size', 'color'):
        pos = 0  # ★ position 은 타입별로 1부터 빈칸없이 (BUYMA 歯抜け 금지). 통합 카운터 쓰면 색이 7 등으로 떠 거부됨
        key = 'size_value' if typ == 'size' else 'color_value'
        cmap = size_canon if typ == 'size' else color_canon
        for o in lopts:
            raw = o[key]
            if raw is None:
                continue
            canon = cmap.get(raw, raw)
            if (typ, canon) in seen:
                continue
            seen.add((typ, canon))
            master_id, details_json = _ace_option_meta(conn, o['ace_product_id'], typ, raw)
            pos += 1
            options_rows.append({
                'option_type': typ,
                'value': canon,
                'master_id': master_id,
                'position': pos,
                'details_json': details_json,
            })

    # 3) product dict (register.build_request_json 입력 규격)
    product = {
        'name': listing['name'],
        'brand_id': listing['brand_id'],
        'brand_name': listing.get('brand_name'),
        'category_id': listing['category_id'],
        'model_no': listing.get('model_no') or '',
        'price': listing['price'],
        'reference_number': listing.get('reference_number') or make_reference_number(listing),
        'buying_shop_name': listing.get('buying_shop_name'),
        # buyma_listings 컬럼명은 colorsize_comments → register 는 *_jp 키로 읽음
        'colorsize_comments_jp': listing.get('colorsize_comments'),
        'source_product_url': winner['source_product_url'] if winner else None,
        'source_site': winner['source_site'] if winner else None,
    }

    # 4) 검증된 빌더 재사용 (control=publish, id 없음 → CREATE)
    formatted_variants = reg.build_variants_array(variant_rows)
    req = reg.build_request_json(product, images, options_rows, formatted_variants)
    return req


# ============================================================
# CREATE 실행 (3단계 — 실제 BUYMA 쓰기)
#   ① reference_number(UUID) 발급·저장 → ② POST(control=publish) → ③ 상태기록
#   웹훅 회수(buyma_product_id)는 server.py 보강 후. 여기선 status='pending'까지.
# ============================================================

def issue_reference_number(conn, listing, dry_run=False):
    """listing 에 reference_number(UUID) 발급·저장. 이미 있으면 그대로."""
    if listing.get('reference_number'):
        return listing['reference_number']
    ref = str(uuid.uuid4())
    if not dry_run:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE buyma_listings SET reference_number=%s, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=%s AND reference_number IS NULL",
                (ref, listing['id']))
        conn.commit()
    listing['reference_number'] = ref
    return ref


def record_after_create(conn, listing, resp):
    """register.update_product_after_request 의 buyma_listings 버전.
    성공 → status='pending' + locked_* 백업 / 실패 → status='api_error'.
    (실제 buyma_product_id·is_published 는 웹훅이 나중에 기록)"""
    with conn.cursor() as cur:
        if resp.get('success'):
            cur.execute("""
                UPDATE buyma_listings
                SET status='pending',
                    locked_name=COALESCE(locked_name, name),
                    locked_brand_id=COALESCE(locked_brand_id, brand_id),
                    locked_category_id=COALESCE(locked_category_id, category_id),
                    locked_reference_number=COALESCE(locked_reference_number, reference_number),
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
            """, (listing['id'],))
        else:
            cur.execute(
                "UPDATE buyma_listings SET status='api_error', updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (listing['id'],))
    conn.commit()


def execute_create(conn, listing, dry_run=True):
    """한 listing CREATE. dry_run=True 면 실제 POST 안 함(ref 발급도 메모리만)."""
    # ★ 이중 안전장치: 이미 BUYMA 등록(buyma_product_id 있음)인 listing 은 CREATE 차단 → 재등록(중복) 방지.
    #   단일권위 전환 등으로 listing 에 정체성이 들어온 뒤 이미 라이브인 상품을 다시 등록하는 사고 방지.
    #   (execute_create_safe 의 동일 가드를 직접 호출 경로에도 적용 — 정상 신규는 buyma_product_id NULL 이라 안 막힘)
    if listing.get('buyma_product_id'):
        return {'skipped': True,
                'reason': f"이미 등록됨(buyma_product_id={listing['buyma_product_id']}) → CREATE 차단(중복방지)",
                'ref': listing.get('reference_number')}
    issue_reference_number(conn, listing, dry_run=dry_run)
    req = build_create_request(conn, listing)
    if req is None:
        return {'skipped': True, 'reason': 'build None (전체 품절 등)', 'ref': listing.get('reference_number')}
    if dry_run:
        return {'dry_run': True, 'request': req, 'ref': listing['reference_number']}
    resp = reg.call_buyma_api(req)
    record_after_create(conn, listing, resp)
    return {'request': req, 'response': resp, 'ref': listing['reference_number']}


# ============================================================
# 그룹 락 — multi-PC 안전 (MySQL GET_LOCK = 서버 전역 named lock)
#   같은 group_key 는 어느 PC에서 돌리든 한 번에 한 프로세스만 reconcile.
#   락 이름은 64자 한도 → md5 해시(고정·PC간 동일).
# ============================================================

def _lock_name(group_key):
    h = hashlib.md5(str(group_key).encode('utf-8')).hexdigest()
    return f"mg:{h}"  # 35자 < 64


@contextmanager
def group_lock(conn, group_key, timeout=10):
    """그룹 단위 상호배제. 획득 실패(다른 PC 처리중) 시 got=False 를 yield."""
    name = _lock_name(group_key)
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, %s) AS ok", (name, timeout))
        ok = cur.fetchone()['ok']
    if ok != 1:
        yield False
        return
    try:
        yield True
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
            cur.fetchall()


_DONE_STATUSES = ('pending', 'success', 'fail')


def execute_create_safe(conn, listing, dry_run=True, lock_timeout=10):
    """락 + 락 획득 후 재확인 + CREATE. multi-PC / 동시 실행 안전.

    락 획득 후 DB 를 다시 읽어, 그 사이 다른 PC 가 이미 처리(등록/시도)했으면 스킵.
    이것이 'register 를 따로 돌려도 중복이 완벽하게 처리되는' 핵심.
    """
    gk = listing.get('group_key')
    if not gk:
        # group_key 없으면 락 대상 불명확 → listing id 로 대체 락
        gk = f"listing:{listing['id']}"
    with group_lock(conn, gk, timeout=lock_timeout) as got:
        if not got:
            return {'skipped': True, 'reason': f'다른 PC가 같은 그룹 처리중 (group_key={gk})',
                    'ref': listing.get('reference_number')}
        fresh = fetch_listing(conn, listing['id'])
        if fresh is None:
            return {'skipped': True, 'reason': 'listing 사라짐', 'ref': listing.get('reference_number')}
        if fresh.get('buyma_product_id'):
            return {'skipped': True, 'reason': f"락 후 재확인: 이미 등록됨 (buyma_product_id={fresh['buyma_product_id']})",
                    'ref': fresh.get('reference_number')}
        if fresh.get('status') in _DONE_STATUSES:
            return {'skipped': True, 'reason': f"락 후 재확인: 이미 처리됨 (status={fresh.get('status')})",
                    'ref': fresh.get('reference_number')}
        return execute_create(conn, fresh, dry_run=dry_run)


# ============================================================
# EDIT (기존 출품 수정 — 소싱교정/가격/옵션/재고)
#   불변필드(이름/브랜드/카테고리/ref)는 기존 게시본 locked 유지.
#   바꾸는 것: 가격·buying_shop·옵션·재고. 이미지는 기존 게시 멤버 것 유지.
# ============================================================

def _build_ov(conn, listing):
    """listing → (options_rows, formatted_variants). create 빌더와 동일 로직(표기통합+그리드)."""
    lopts = _listing_options(conn, listing['id'])
    color_canon = _canonical_map([o['color_value'] for o in lopts])
    size_canon = _canonical_map([o['size_value'] for o in lopts])
    _cc = lambda v: color_canon.get(v, v)
    _cs = lambda v: size_canon.get(v, v)

    variant_map = {}
    for o in lopts:
        cc, cs = _cc(o['color_value']), _cs(o['size_value'])
        cand = {'color_value': cc, 'size_value': cs, 'stock_type': o['stock_type'], 'stocks': o['stocks']}
        cur_row = variant_map.get((cc, cs))
        if cur_row is None or _STOCK_RANK.get(o['stock_type'], 0) > _STOCK_RANK.get(cur_row['stock_type'], 0):
            variant_map[(cc, cs)] = cand
    colors, sizes = [], []
    for o in lopts:
        cc, cs = _cc(o['color_value']), _cs(o['size_value'])
        if cc not in colors:
            colors.append(cc)
        if cs not in sizes:
            sizes.append(cs)
    for cc in colors:
        for cs in sizes:
            if (cc, cs) not in variant_map:
                variant_map[(cc, cs)] = {'color_value': cc, 'size_value': cs, 'stock_type': 'out_of_stock', 'stocks': 0}
    variant_rows = list(variant_map.values())

    options_rows = []
    seen = set()
    for typ in ('size', 'color'):
        pos = 0
        key = 'size_value' if typ == 'size' else 'color_value'
        cmap = size_canon if typ == 'size' else color_canon
        for o in lopts:
            raw = o[key]
            if raw is None:
                continue
            canon = cmap.get(raw, raw)
            if (typ, canon) in seen:
                continue
            seen.add((typ, canon))
            master_id, details_json = _ace_option_meta(conn, o['ace_product_id'], typ, raw)
            pos += 1
            options_rows.append({'option_type': typ, 'value': canon, 'master_id': master_id,
                                 'position': pos, 'details_json': details_json})
    return options_rows, reg.build_variants_array(variant_rows)


def published_member(conn, listing):
    """그룹에서 BUYMA 등록된 멤버 ace (edit 대상 정체성). edit 모드는 정확히 1개."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.id AS ace_id, a.buyma_product_id, a.is_buyma_locked,
                   a.locked_name, a.locked_brand_id, a.locked_category_id, a.locked_reference_number,
                   a.name, a.brand_id, a.category_id, a.reference_number, a.source_site
            FROM source_offerings so JOIN ace_products a ON a.id = so.ace_product_id
            WHERE so.listing_id=%s AND a.is_published = 1
            ORDER BY a.id LIMIT 1
        """, (listing['id'],))
        return cur.fetchone()


def build_edit_request(conn, listing, pub):
    """EDIT 요청 JSON. 불변필드는 pub(기존 게시본)의 locked값 유지, id 부착."""
    options_rows, formatted_variants = _build_ov(conn, listing)
    winner = _winner_offering(conn, listing)
    if authority_flag.use_listing_authority():
        # 새 방식: 목록 자신의 이미지 우선(네이티브 목록), 없으면 게시/winner ace 이미지로 폴백
        images = _images(conn, listing['id'])
        if not images and pub.get('ace_id'):
            images = reg.get_product_images(conn, pub['ace_id'])
        if not images:
            # 이미지 0장을 그대로 edit 하면 라이브 페이지 이미지 훼손/거부 위험 → edit 안 함(스킵)
            return None
    else:
        images = reg.get_product_images(conn, pub['ace_id'])  # 기존 게시 멤버 이미지 유지
    locked = (pub.get('is_buyma_locked') == 1)

    def L(lk, fb):
        v = pub.get(lk)
        return v if (locked and v not in (None, '')) else pub.get(fb)

    product = {
        'name': L('locked_name', 'name'),
        'brand_id': L('locked_brand_id', 'brand_id'),
        'brand_name': listing.get('brand_name'),
        'category_id': L('locked_category_id', 'category_id'),
        'model_no': listing.get('model_no') or '',
        'price': listing['price'],
        'reference_number': pub.get('locked_reference_number') or pub.get('reference_number'),
        # ★ buying_shop_name 은 BUYMA 불변("変更できません") → winner 값으로 바꾸지 않음.
        #   소싱 교정은 내부(merge 테이블)에서만, BUYMA push 안 함. (None → 요청서 생략)
        'buying_shop_name': None,
        'colorsize_comments_jp': listing.get('colorsize_comments'),
        'source_product_url': winner['source_product_url'] if winner else None,
        'source_site': winner['source_site'] if winner else None,
    }
    req = reg.build_request_json(product, images, options_rows, formatted_variants)
    if req is None:
        return None  # 전체 품절 → stock-out edit (별도 처리)
    req['product']['id'] = pub['buyma_product_id']  # ★ id 있으면 EDIT
    return req


def _identity_from_listing(conn, listing):
    """단일권위 ON 전용: 목록(buyma_listings) 자신의 정체성으로 edit 대상 pub 딕트 구성.
    정체성(번호/불변필드)=목록, 이미지/소싱=현재 winner ace. published_member 와 같은 형태 반환."""
    winner = _winner_offering(conn, listing)
    ace_id = winner.get('ace_product_id') if winner else None
    return {
        'ace_id': ace_id,
        'buyma_product_id': listing.get('buyma_product_id'),
        'is_buyma_locked': listing.get('is_buyma_locked'),
        'locked_name': listing.get('locked_name'),
        'locked_brand_id': listing.get('locked_brand_id'),
        'locked_category_id': listing.get('locked_category_id'),
        'locked_reference_number': listing.get('locked_reference_number'),
        'name': listing.get('name'),
        'brand_id': listing.get('brand_id'),
        'category_id': listing.get('category_id'),
        'reference_number': listing.get('reference_number'),
        'source_site': winner.get('source_site') if winner else None,
    }


def execute_edit(conn, listing, dry_run=True):
    """한 listing EDIT. dry_run=True면 POST 안 함."""
    pub = published_member(conn, listing)
    if authority_flag.use_listing_authority():
        # 새 방식: 목록 자신이 정체성 권위. 게시 ace 멤버가 없거나(=D 케이스) 목록 번호와
        # 다르면 목록 기준으로 edit. 게시멤버가 목록 번호와 일치하면 기존과 동일(pub 유지).
        l_bid = listing.get('buyma_product_id')
        if l_bid and (not pub or pub.get('buyma_product_id') != l_bid):
            pub = _identity_from_listing(conn, listing)
    if not pub:
        return {'skipped': True, 'reason': '게시된 멤버 없음 (edit 대상 아님)'}
    req = build_edit_request(conn, listing, pub)
    if req is None:
        return {'skipped': True, 'reason': '전체 품절 → stock-out edit 대상(미구현)', 'pub': pub}
    if dry_run:
        return {'dry_run': True, 'request': req, 'pub': pub}
    resp = reg.call_buyma_api(req)
    return {'request': req, 'response': resp, 'pub': pub}


def execute_retire(conn, listing, dry_run=True):
    """마진X / 전체품절 → BUYMA '품절(출품정지중)' 처리. ★삭제하지 않음.
    재고 API(variants.json)로 전 옵션 out_of_stock + order_quantity:0 전송.
    → buyma_product_id·등록일(게시일수) 유지. 재입고/마진회복 시 기존 stock 흐름(상품수정)이
      같은 id로 출품중 복구. 실제 is_published=0 은 buyer_suspended webhook 이 반영(server.py).

    이미 BUYMA에 올라간(reference_number 있는) listing 만 대상.
    미등록 신규는 호출 안 함(=그냥 등록 안 함, 기존 register 와 동일).
    """
    ref = listing.get('locked_reference_number') or listing.get('reference_number')
    if not ref:
        pub = published_member(conn, listing)
        if pub:
            ref = pub.get('locked_reference_number') or pub.get('reference_number')
    if not ref:
        return {'skipped': True, 'reason': '미등록(ref 없음) → 내릴 것 없음'}
    opts = _listing_options(conn, listing['id'])
    if not opts:
        return {'skipped': True, 'reason': '옵션 없음 → 품절 보낼 변이 없음', 'ref': ref}
    if dry_run:
        return {'dry_run': True, 'action': 'soldout', 'ref': ref, 'variants': len(opts)}
    resp = reg.call_buyma_variants_soldout(ref, opts)
    # 출품정지중 전이(is_published=0)는 buyer_suspended webhook 이 반영
    return {'action': 'soldout', 'response': resp, 'ref': ref}


def execute_edit_safe(conn, listing, dry_run=True, lock_timeout=10):
    """락 + 락 후 최신 재조회 + EDIT. multi-PC / 동시 실행 안전 (stock 동기화용).

    CREATE 와 달리 EDIT 는 반복 실행이 정상(재고 변동 때마다 갱신)이므로
    status 기반 스킵은 하지 않는다. 락 획득 후 listing 을 최신값으로 다시 읽어
    그 시점의 winner/옵션/재고로 수정한다. 게시 멤버가 사라졌으면(언퍼블리시) 스킵.
    """
    gk = listing.get('group_key') or f"listing:{listing['id']}"
    with group_lock(conn, gk, timeout=lock_timeout) as got:
        if not got:
            return {'skipped': True, 'reason': f'다른 PC가 같은 그룹 처리중 (group_key={gk})'}
        fresh = fetch_listing(conn, listing['id'])
        if fresh is None:
            return {'skipped': True, 'reason': 'listing 사라짐'}
        # 게시 멤버(is_published=1) 확인은 execute_edit 내부 published_member 가 함
        return execute_edit(conn, fresh, dry_run=dry_run)

