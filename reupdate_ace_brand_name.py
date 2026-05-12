# -*- coding: utf-8 -*-
"""
ace_products.brand_name을 새 우선순위로 다시 채움:
  1. mall_brands.buyma_brand_name
  2. mall_brands.mall_brand_name_en (buyma_brand_name 없을 때)
  3. raw.brand_name_en (매핑 없으면 fallback - 변경 안 함)

매핑 있는 행만 update. 매핑 없는 행은 그대로.

사용법:
    python reupdate_ace_brand_name.py             # dry-run, 모든 mall
    python reupdate_ace_brand_name.py --apply
    python reupdate_ace_brand_name.py --source euroline --apply
"""
import os, sys, argparse, pymysql
from dotenv import load_dotenv
load_dotenv()

ap = argparse.ArgumentParser()
ap.add_argument('--apply', action='store_true')
ap.add_argument('--source', type=str, default=None)
args = ap.parse_args()

conn = pymysql.connect(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'), charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

# 1) mall_brands 캐시 (mall_name + brand 키 → 결정값)
print('[1/3] mall_brands 캐시 로드')
cur.execute("""SELECT mall_name, mall_brand_name_en, mall_brand_name_ko, buyma_brand_id, buyma_brand_name
    FROM mall_brands WHERE is_active=1""")
cache = {}  # (mall_name, brand_key_upper) → (brand_id, brand_name)
for r in cur.fetchall():
    # 1순위 buyma_brand_name, 2순위 mall_brand_name_en
    new_brand_name = (r['buyma_brand_name'] or '').strip() or (r['mall_brand_name_en'] or '').strip() or None
    new_brand_id = int(r['buyma_brand_id']) if r['buyma_brand_id'] else 0
    info = (new_brand_id, new_brand_name)
    if r['mall_brand_name_en']:
        cache[(r['mall_name'], r['mall_brand_name_en'].upper().strip())] = info
    if r['mall_brand_name_ko']:
        cache[(r['mall_name'], r['mall_brand_name_ko'].upper().strip())] = info
print(f'  → {len(cache)} keys')

# 2) ace + raw JOIN하여 매핑 가능한 행 찾기
print('[2/3] ace + raw JOIN, 매칭')
sql = """SELECT ap.id, ap.source_site, ap.brand_id old_id, ap.brand_name old_name,
    rsd.brand_name_en, rsd.brand_name_kr
    FROM ace_products ap
    JOIN raw_scraped_data rsd ON ap.raw_data_id=rsd.id
    WHERE ap.is_published=0"""
params = []
if args.source:
    sql += " AND ap.source_site = %s"
    params.append(args.source.lower())
cur.execute(sql, params if params else None)
rows = cur.fetchall()
print(f'  → 전체 {len(rows)}건')

to_update = []
unchanged = 0
no_mapping = 0
for r in rows:
    site = r['source_site']
    en = (r['brand_name_en'] or '').upper().strip()
    kr = (r['brand_name_kr'] or '').upper().strip()
    info = cache.get((site, en)) or cache.get((site, kr))
    if not info:
        no_mapping += 1
        continue
    new_id, new_name = info
    if new_id == r['old_id'] and new_name == r['old_name']:
        unchanged += 1
        continue
    to_update.append((r['id'], r['old_id'], r['old_name'], new_id, new_name))

print(f'  → 변경 대상: {len(to_update)}건')
print(f'  → 변경 없음: {unchanged}건')
print(f'  → 매핑 없음 (skip): {no_mapping}건')

# source_site별
from collections import Counter
sites = Counter()
for t in to_update:
    cur.execute('SELECT source_site FROM ace_products WHERE id=%s', (t[0],))
    sites[cur.fetchone()['source_site']] += 1
print()
print('  source_site별 변경 대상:')
for s, c in sites.most_common(15):
    print(f'    {s}: {c}')

print()
print('=== 변경 샘플 (앞 10건) ===')
for ace_id, old_id, old_name, new_id, new_name in to_update[:10]:
    print(f'  id={ace_id}')
    print(f'    OLD: brand_id={old_id} brand_name={old_name!r}')
    print(f'    NEW: brand_id={new_id} brand_name={new_name!r}')

if args.apply and to_update:
    print(f'\n>>> --apply: UPDATE {len(to_update)}건')
    BATCH = 500
    for i in range(0, len(to_update), BATCH):
        chunk = to_update[i:i+BATCH]
        for ace_id, _, _, new_id, new_name in chunk:
            # 안전망: UPDATE 시점에도 is_published=0 재확인
            cur.execute('UPDATE ace_products SET brand_id=%s, brand_name=%s WHERE id=%s AND is_published=0',
                        (new_id, new_name, ace_id))
        conn.commit()
        print(f'  진행: {min(i+BATCH, len(to_update))}/{len(to_update)}')
    print('>>> 완료')
else:
    print(f'\n[DRY-RUN] --apply 옵션 없음. 실행: python {sys.argv[0]} --apply')

conn.close()
