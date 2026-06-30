# -*- coding: utf-8 -*-
"""
버킷② 벌크 생성 (set 기반) — 미연결 라이브 ace 그룹에 listing+source_offerings 를
메모리 그룹핑 + 배치 INSERT 로 생성. 그룹별 ensure_group(=서버에 작은 statement 폭격)이
DB서버 바운드라 워커 늘려도 ~2.9그룹/초로 느린 문제를, statement 수를 확 줄여 해결.

그룹핑 로직은 reconcile_ensure_group.compute_group_members 와 동일하게 포팅:
  seed canonical 이 기존 listing(또는 이번 run 에서 만든 new listing)에 exact/fuzzy 로 맞으면 흡수,
  아니면 신규 그룹(group_key = seed_canon). 흡수/신규 후 같은 브랜드의 canonical 일치 ace 를 멤버로.
이후 옵션/winner/이미지는 기존 set 기반 로더 3종(전역)으로, 마지막에 fill_listing_identity.

옵션/winner/이미지/정체성은 이 스크립트가 안 함 (listing+offering 뼈대만).

사용:
  python bucket2_bulk_create.py --validate 300   # 그룹핑만(메모리) → 300건 ensure_group 대조검증, 쓰기0
  python bucket2_bulk_create.py --execute        # 전체 listing+offering 생성(배치)
  python bucket2_bulk_create.py --execute --limit 500
"""
import os, sys, time, json, argparse, random
from collections import defaultdict
from datetime import datetime
import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'okmall'))
load_dotenv(os.path.join(BASE, '.env'), override=True)
import reconcile_ensure_group as eg   # canonicalize / MIN_FUZZY_LEN / _seed_ace / SOURCE_PRIORITY / compute_group_members

cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4',
           cursorclass=pymysql.cursors.DictCursor)

canon = eg.canonicalize
MINF = eg.MIN_FUZZY_LEN

GROUPS_SQL = """
SELECT DISTINCT a.model_no, a.brand_id
FROM ace_products a
WHERE a.is_published=1 AND a.buyma_product_id IS NOT NULL
  AND a.model_no IS NOT NULL AND a.model_no<>'' AND a.model_no NOT REGEXP '[가-힣]'
  AND a.category_id IS NOT NULL AND a.category_id>0
  AND NOT EXISTS (SELECT 1 FROM source_offerings so WHERE so.ace_product_id=a.id)
"""


def load_caches(conn):
    t0 = time.time()
    brand_ace = defaultdict(list)        # brand_id -> [ace row]
    brand_ace_by_canon = defaultdict(lambda: defaultdict(list))  # brand_id -> canon -> [ace row]
    with conn.cursor() as c:
        c.execute("""
            SELECT id, source_site, name, brand_id, brand_name, category_id, model_no,
                   source_product_url, source_model_id, purchase_price_krw, is_active, status
            FROM ace_products
            WHERE (is_active=1 OR status='duple') AND model_no IS NOT NULL AND model_no<>''
        """)
        n = 0
        for r in c.fetchall():
            bid = r['brand_id']; cc = canon(r['model_no'])
            brand_ace[bid].append(r)
            if len(cc) >= 4:
                brand_ace_by_canon[bid][cc].append(r)
            n += 1
        print(f"  [캐시] 브랜드 ace {n:,}건 / 브랜드 {len(brand_ace):,}개")

        # 기존 listing 클러스터: brand_id -> {listing_id -> set(canon)}
        brand_clusters = defaultdict(dict)
        c.execute("""
            SELECT l.brand_id, l.id AS listing_id, a.model_no
            FROM buyma_listings l
            JOIN source_offerings so ON so.listing_id=l.id AND so.is_active=1
            JOIN ace_products a ON a.id=so.ace_product_id
            WHERE l.is_active=1
        """)
        m = 0
        for r in c.fetchall():
            cl = brand_clusters[r['brand_id']]
            cl.setdefault(r['listing_id'], set()).add(canon(r['model_no']))
            m += 1
        print(f"  [캐시] 기존 listing 멤버 {m:,}건 / 클러스터보유 브랜드 {len(brand_clusters):,}개  ({time.time()-t0:.1f}s)")
    return brand_ace, brand_ace_by_canon, brand_clusters


def find_existing(clusters_bid, seed_canon):
    """clusters_bid: {cluster_key -> set(canon)}. 원본 _find_existing_listing 과 동일 판정."""
    exact = None
    fuzzy = None
    for ck, canons in clusters_bid.items():
        for c in canons:
            if c == seed_canon:
                exact = ck
                break
            if fuzzy is None:
                short, long = (seed_canon, c) if len(seed_canon) <= len(c) else (c, seed_canon)
                if len(short) >= MINF and long.startswith(short):
                    fuzzy = ck
        if exact is not None:
            break
    return exact if exact is not None else fuzzy


def group_all(groups, brand_ace_by_canon, brand_clusters):
    """메모리 그룹핑. 반환:
       new_members: {('NEW',brand,seed_canon) -> [ace rows]}  (신규 listing 멤버)
       offerings:   {cluster_key -> {(source,model_no) -> ace_rep}}  (cluster_key = listing_id int 또는 ('NEW',brand,sc))
       decisions:   {(model_no,brand_id) -> (cluster_key, [member ace ids])}  (검증용)
    """
    new_members = defaultdict(list)
    offerings = defaultdict(dict)
    decisions = {}
    stats = {'absorb': 0, 'new': 0, 'no_member': 0, 'short': 0}

    for g in groups:
        model_no, bid = g['model_no'], g['brand_id']
        sc = canon(model_no)
        if len(sc) < 4:
            stats['short'] += 1
            decisions[(model_no, bid)] = (None, [])
            continue
        clusters_bid = brand_clusters[bid]
        ck = find_existing(clusters_bid, sc)

        if ck is not None:
            member_canons = set(clusters_bid[ck]) | {sc}
            members = [a for cc in member_canons for a in brand_ace_by_canon[bid].get(cc, [])]
            if not members:
                stats['no_member'] += 1
                decisions[(model_no, bid)] = (ck, [])
                continue
            stats['absorb'] += 1
            clusters_bid[ck] |= {canon(a['model_no']) for a in members}
            target = ck
        else:
            members = brand_ace_by_canon[bid].get(sc, [])
            if not members:
                stats['no_member'] += 1
                decisions[(model_no, bid)] = (None, [])
                continue
            stats['new'] += 1
            nk = ('NEW', bid, sc)            # group_key=seed_canon (신규 멤버는 전부 canon==sc)
            if nk not in clusters_bid:
                clusters_bid[nk] = {sc}      # 이후 그룹이 fuzzy 로 찾도록 (chaining)
            new_members[nk].extend(members)
            target = nk

        # 멤버 → offering (source,model) 별 active 우선 대표 (원본 _upsert_offerings by_key)
        ob = offerings[target]
        for a in members:
            k = (a['source_site'], a['model_no'])
            cur = ob.get(k)
            if cur is None or (a['is_active'] == 1 and cur['is_active'] != 1):
                ob[k] = a
        decisions[(model_no, bid)] = (target, sorted({a['id'] for a in members}))

    return new_members, offerings, decisions, stats


def validate(conn, decisions, n):
    """랜덤 n 그룹에 대해 메모리 결정 vs ensure_group.compute_group_members(실DB, 읽기전용) 멤버집합 대조."""
    keys = [k for k, v in decisions.items() if v[0] is not None]
    random.shuffle(keys)
    keys = keys[:n]
    same = diff = 0
    diffs = []
    for (model_no, bid) in keys:
        mem_ids = set(decisions[(model_no, bid)][1])
        _sc, eg_members, _lid = eg.compute_group_members(conn, model_no, bid)
        eg_ids = {m['id'] for m in eg_members}
        if mem_ids == eg_ids:
            same += 1
        else:
            diff += 1
            if len(diffs) < 8:
                diffs.append((model_no, bid, sorted(mem_ids - eg_ids), sorted(eg_ids - mem_ids)))
    print(f"\n[검증] 샘플 {len(keys)}건 — 일치 {same} / 불일치 {diff}")
    for model_no, bid, only_mem, only_eg in diffs:
        print(f"   ⚠ model_no={model_no!r} brand={bid}  mem-only={only_mem}  eg-only={only_eg}")
    return same, diff


def write_all(conn, new_members, offerings):
    cur = conn.cursor()
    # 1) 신규 listing 배치 INSERT (group_key=seed_canon, seed ace 로 필드)
    new_rows = []
    for nk, members in new_members.items():
        _tag, bid, sc = nk
        seed = eg._seed_ace(members)
        new_rows.append((sc, seed['name'], seed['brand_id'], seed['brand_name'],
                         seed['category_id'], seed['model_no']))
    print(f"[쓰기] 신규 listing {len(new_rows):,}건 INSERT...")
    B = 1000
    for i in range(0, len(new_rows), B):
        cur.executemany("""
            INSERT INTO buyma_listings
                (group_key, name, brand_id, brand_name, category_id, model_no, control, is_active)
            VALUES (%s,%s,%s,%s,%s,%s,'draft',1)
            ON DUPLICATE KEY UPDATE name=VALUES(name), brand_id=VALUES(brand_id),
                brand_name=VALUES(brand_name), category_id=VALUES(category_id),
                model_no=VALUES(model_no), updated_at=CURRENT_TIMESTAMP
        """, new_rows[i:i+B])
        conn.commit()

    # 2) group_key -> listing_id 매핑
    gk_to_id = {}
    gks = [r[0] for r in new_rows]
    for i in range(0, len(gks), 1000):
        chunk = gks[i:i+1000]
        ph = ','.join(['%s']*len(chunk))
        cur.execute(f"SELECT id, group_key FROM buyma_listings WHERE group_key IN ({ph})", chunk)
        for r in cur.fetchall():
            gk_to_id[r['group_key']] = r['id']

    # 3) offerings 배치 INSERT (cluster_key -> listing_id 해소)
    off_rows = []
    for ck, ob in offerings.items():
        if isinstance(ck, tuple):       # ('NEW', bid, sc)
            lid = gk_to_id.get(ck[2])
            if lid is None:
                continue
        else:
            lid = ck
        for (src, mid), rep in ob.items():
            off_rows.append((lid, rep['id'], src, rep['source_product_url'], mid, rep['purchase_price_krw']))
    print(f"[쓰기] source_offerings {len(off_rows):,}건 INSERT...")
    for i in range(0, len(off_rows), B):
        cur.executemany("""
            INSERT INTO source_offerings
                (listing_id, ace_product_id, source_site, source_product_url,
                 source_model_id, purchase_price_krw, is_margin_ok, is_active)
            VALUES (%s,%s,%s,%s,%s,%s,0,1)
            ON DUPLICATE KEY UPDATE
                ace_product_id=VALUES(ace_product_id), source_product_url=VALUES(source_product_url),
                purchase_price_krw=VALUES(purchase_price_krw), is_active=1, updated_at=CURRENT_TIMESTAMP
        """, off_rows[i:i+B])
        conn.commit()
    return len(new_rows), len(off_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--validate', type=int, default=0, help='N건 ensure_group 대조검증(쓰기 없음)')
    args = ap.parse_args()

    conn = pymysql.connect(**cfg); conn.autocommit(False)
    print("[1] 캐시 로드")
    brand_ace, brand_ace_by_canon, brand_clusters = load_caches(conn)

    with conn.cursor() as c:
        c.execute("SELECT COALESCE(MAX(id),0) mx FROM buyma_listings")
        prev_max_id = c.fetchone()['mx']
        c.execute(GROUPS_SQL + (f" LIMIT {int(args.limit)}" if args.limit else ""))
        groups = c.fetchall()
    print(f"[2] 버킷② 대상 그룹 {len(groups):,}개" + (f" (limit {args.limit})" if args.limit else "") +
          f"  | 실행 전 MAX(listing.id)={prev_max_id}")

    t0 = time.time()
    new_members, offerings, decisions, gstats = group_all(groups, brand_ace_by_canon, brand_clusters)
    print(f"[3] 메모리 그룹핑 {time.time()-t0:.1f}s — 흡수 {gstats['absorb']:,} / 신규 {gstats['new']:,} "
          f"/ 멤버없음 {gstats['no_member']:,} / canon<4 {gstats['short']:,}")
    print(f"    신규 listing 후보 {len(new_members):,} (group_key 중복 collapse 후) / offering 클러스터 {len(offerings):,}")

    if args.validate:
        validate(conn, decisions, args.validate)

    if not args.execute:
        print("\n(쓰기 없음 — 실제 생성은 --execute)")
        conn.close(); return

    nl, no = write_all(conn, new_members, offerings)

    with conn.cursor() as c:
        c.execute("SELECT id FROM buyma_listings WHERE id > %s", (prev_max_id,))
        new_ids = [r['id'] for r in c.fetchall()]
    affected = sorted({(ck if not isinstance(ck, tuple) else None) for ck in offerings} - {None})
    bk = os.path.join(BASE, 'migrations', f"bucket2_bulk_backup_{datetime.now():%Y%m%d_%H%M%S}.json")
    json.dump({'prev_max_listing_id': prev_max_id, 'new_listing_ids': new_ids,
               'affected_existing_listing_ids': affected, 'stats': gstats,
               'new_listings': nl, 'offerings': no},
              open(bk, 'w', encoding='utf-8'), ensure_ascii=False, default=str, indent=1)
    print(f"\n[완료] {time.time()-t0:.0f}s  신규listing {nl:,} / offering {no:,} / 새id {len(new_ids):,} / 흡수영향 {len(affected):,}")
    print(f"[롤백 기록] {bk}")
    print("→ 다음: 로더3(옵션→resolve→이미지) → fill_listing_identity.py --execute")
    conn.close()


if __name__ == '__main__':
    main()
