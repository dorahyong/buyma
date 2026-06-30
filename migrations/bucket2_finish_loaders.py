# -*- coding: utf-8 -*-
"""
버킷② 마무리 — winner/가격(resolve) + 이미지를 **버킷② listing ID 한정**으로 채운다.
전역 로더가 기존 listing 까지 건드리는 churn 을 피하려고, 스코프를 버킷② 가 만든/흡수한
listing 으로만 제한. 로직은 resolve_merge.resolve_listing / image_union_loader_merge.pick_donor
그대로 재사용(값 동일), 대상만 스코프.

옵션(loader1)은 이미 전역 완료됐으므로 생략. 옵션은 멱등이라 무관.

스코프 = 최신 bucket2_bulk_backup_*.json 의 new_listing_ids + affected_existing_listing_ids.

사용:
  python bucket2_finish_loaders.py            # 미리보기(스코프 수만)
  python bucket2_finish_loaders.py --execute  # 실제 resolve+이미지 (스코프 한정)
"""
import os, sys, glob, json, argparse
from collections import defaultdict
import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'okmall'))
load_dotenv(os.path.join(BASE, '.env'), override=True)
import resolve_merge as rm
import image_union_loader_merge as iu

cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4',
           cursorclass=pymysql.cursors.DictCursor)
DEFAULT_SHIPPING_FEE = rm.DEFAULT_SHIPPING_FEE


def load_scope_ids():
    files = sorted(glob.glob(os.path.join(BASE, 'migrations', 'bucket2_bulk_backup_*.json')))
    if not files:
        raise SystemExit("백업 JSON 없음 — bucket2_bulk_create.py --execute 먼저")
    bk = files[-1]
    d = json.load(open(bk, encoding='utf-8'))
    ids = set(d.get('new_listing_ids', [])) | set(d.get('affected_existing_listing_ids', []))
    print(f"[스코프] {os.path.basename(bk)} → listing {len(ids):,}개 "
          f"(신규 {len(d.get('new_listing_ids', [])):,} + 흡수영향 {len(d.get('affected_existing_listing_ids', [])):,})")
    return sorted(ids)


def _in_chunks(ids, n=1000):
    for i in range(0, len(ids), n):
        yield ids[i:i+n]


def scoped_resolve(conn, ids, dry_run):
    cur = conn.cursor()
    # --- 스코프 listing/offering/option/ace/fee 만 로드 ---
    listings = {}
    for ch in _in_chunks(ids):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"SELECT id, category_id FROM buyma_listings WHERE is_active=1 AND id IN ({ph})", ch)
        for r in cur.fetchall():
            listings[r['id']] = r
    offerings_by_listing = defaultdict(list)
    off_ids, ace_ids = [], set()
    for ch in _in_chunks(ids):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"""SELECT id, listing_id, source_site, ace_product_id, purchase_price_krw
                        FROM source_offerings WHERE is_active=1 AND listing_id IN ({ph})""", ch)
        for r in cur.fetchall():
            offerings_by_listing[r['listing_id']].append(r); off_ids.append(r['id'])
            if r['ace_product_id']:
                ace_ids.add(r['ace_product_id'])
    options_by_offering = defaultdict(list)
    for ch in _in_chunks(off_ids):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"""SELECT id, offering_id, color_value, size_value, stock_type, stocks
                        FROM source_offering_options WHERE offering_id IN ({ph})""", ch)
        for r in cur.fetchall():
            options_by_offering[r['offering_id']].append(r)
    ace_info = {}
    aids = sorted(ace_ids)
    for ch in _in_chunks(aids):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"""SELECT id, buyma_lowest_price, buyma_lowest_price_checked_at, buying_shop_name
                        FROM ace_products WHERE id IN ({ph})""", ch)
        for r in cur.fetchall():
            ace_info[r['id']] = r
    fee_map = {}
    cur.execute("SELECT buyma_category_id, expected_shipping_fee FROM buyma_master_categories_data")
    for r in cur.fetchall():
        if r['expected_shipping_fee'] is not None:
            fee_map[r['buyma_category_id']] = int(float(r['expected_shipping_fee']))

    print(f"  resolve 대상 listing {len(listings):,} / offering {len(off_ids):,} / ace {len(ace_info):,}")

    stats = {'ok': 0, 'no_margin': 0, 'no_price': 0, 'no_offering': 0}
    off_updates, lst_updates, opt_rows = [], [], []

    for lid, listing in listings.items():
        offs = offerings_by_listing.get(lid, [])
        if not offs:
            stats['no_offering'] += 1; continue
        r = rm.resolve_listing(listing, offs, options_by_offering, ace_info, fee_map)
        if r['status'] == 'ok':
            stats['ok'] += 1
            for off in offs:
                rate, amount, is_ok = r['margins'][off['id']]
                off_updates.append((rate, amount, 1 if is_ok else 0, off['id']))
            lst_updates.append((r['selling'], r['competitor'], 1, r['winner']['id'], r['winner_shop'], 'draft', lid))
            for o in r['listing_options']:
                opt_rows.append({'listing_id': lid, **o})
        elif r['status'] == 'no_margin':
            stats['no_margin'] += 1
            for off in offs:
                rate, amount, is_ok = r['margins'][off['id']]
                off_updates.append((rate, amount, 0, off['id']))
            lst_updates.append((r['selling'], r['competitor'], 0, None, None, 'draft', lid))
        else:
            stats['no_price'] += 1

    print(f"  resolve 판정 — ok {stats['ok']:,} / no_margin {stats['no_margin']:,} / "
          f"no_price {stats['no_price']:,} / no_offering {stats['no_offering']:,}")
    if dry_run:
        print("  (dry-run — 쓰기 없음)"); return stats

    # ensure_group 과 동일: 스코프 listing 의 기존 listing_options 비활성화 후 재적재
    for ch in _in_chunks(ids):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"UPDATE listing_options SET is_active=0 WHERE listing_id IN ({ph})", ch)
    for i in range(0, len(off_updates), 2000):
        cur.executemany("""UPDATE source_offerings SET margin_rate=%s, margin_amount_krw=%s,
                           is_margin_ok=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s""", off_updates[i:i+2000])
    for i in range(0, len(lst_updates), 2000):
        cur.executemany("""UPDATE buyma_listings SET price=%s, buyma_lowest_price=%s, is_lowest_price=%s,
                           winner_offering_id=%s, buying_shop_name=%s, control=%s, updated_at=CURRENT_TIMESTAMP
                           WHERE id=%s""", lst_updates[i:i+2000])
    for i in range(0, len(opt_rows), 2000):
        cur.executemany("""INSERT INTO listing_options
            (listing_id, color_value, size_value, stock_type, stocks, sourced_offering_option_id, is_active)
            VALUES (%(listing_id)s,%(color_value)s,%(size_value)s,%(stock_type)s,%(stocks)s,
                    %(sourced_offering_option_id)s,1)
            ON DUPLICATE KEY UPDATE stock_type=VALUES(stock_type), stocks=VALUES(stocks),
                sourced_offering_option_id=VALUES(sourced_offering_option_id), is_active=1,
                updated_at=CURRENT_TIMESTAMP""", opt_rows[i:i+2000])
    conn.commit()
    print(f"  [resolve 반영] listing_options {len(opt_rows):,} / offering마진 {len(off_updates):,} / listing {len(lst_updates):,}")
    return stats


def scoped_images(conn, ids, dry_run):
    cur = conn.cursor()
    listings = []
    for ch in _in_chunks(ids):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"""SELECT id, winner_offering_id FROM buyma_listings
                        WHERE is_active=1 AND winner_offering_id IS NOT NULL AND id IN ({ph})""", ch)
        listings += cur.fetchall()
    offerings_by_listing = defaultdict(list); offering_by_id = {}; ace_ids = set()
    for ch in _in_chunks(ids):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"SELECT id, listing_id, source_site, ace_product_id FROM source_offerings WHERE is_active=1 AND listing_id IN ({ph})", ch)
        for r in cur.fetchall():
            offerings_by_listing[r['listing_id']].append(r); offering_by_id[r['id']] = r
            if r['ace_product_id']:
                ace_ids.add(r['ace_product_id'])
    images_by_ace = defaultdict(list)
    for ch in _in_chunks(sorted(ace_ids)):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"""SELECT ace_product_id, position, source_image_url, cloudflare_image_url, buyma_image_path
                        FROM ace_product_images WHERE ace_product_id IN ({ph}) ORDER BY ace_product_id, position""", ch)
        for r in cur.fetchall():
            images_by_ace[r['ace_product_id']].append(r)

    print(f"  이미지 대상 listing(winner有) {len(listings):,}")
    rows = []; stats = {'with_img': 0, 'no_img': 0}
    for listing in listings:
        donor, images = iu.pick_donor(listing, offerings_by_listing, offering_by_id, images_by_ace)
        if not images:
            stats['no_img'] += 1; continue
        stats['with_img'] += 1
        for pos, img in enumerate(images, start=1):
            rows.append({'listing_id': listing['id'], 'position': pos, 'source_site': donor['source_site'],
                         'source_image_url': img['source_image_url'], 'cloudflare_image_url': img['cloudflare_image_url'],
                         'buyma_image_path': img['buyma_image_path'],
                         'is_uploaded': 1 if img['cloudflare_image_url'] else 0})
    print(f"  이미지 — 채움 {stats['with_img']:,} / 이미지없음 {stats['no_img']:,} / 총 {len(rows):,}장")
    if dry_run:
        print("  (dry-run — 쓰기 없음)"); return stats
    # ensure_group._write_images 와 동일: listing 별 DELETE 후 INSERT (스코프 listing 만)
    lids = [l['id'] for l in listings]
    for ch in _in_chunks(lids):
        ph = ','.join(['%s']*len(ch))
        cur.execute(f"DELETE FROM listing_images WHERE listing_id IN ({ph})", ch)
    for i in range(0, len(rows), 2000):
        cur.executemany("""INSERT INTO listing_images
            (listing_id, position, source_site, source_image_url, cloudflare_image_url, buyma_image_path, is_uploaded)
            VALUES (%(listing_id)s,%(position)s,%(source_site)s,%(source_image_url)s,%(cloudflare_image_url)s,
                    %(buyma_image_path)s,%(is_uploaded)s)""", rows[i:i+2000])
    conn.commit()
    print(f"  [이미지 반영] {len(rows):,}장")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    ids = load_scope_ids()
    conn = pymysql.connect(**cfg); conn.autocommit(False)
    print("\n[resolve — 스코프 한정]")
    scoped_resolve(conn, ids, dry_run=not args.execute)
    print("\n[이미지 — 스코프 한정]")
    scoped_images(conn, ids, dry_run=not args.execute)
    conn.close()
    print("\n[완료]" + ("" if args.execute else " (dry-run)"))


if __name__ == '__main__':
    main()
