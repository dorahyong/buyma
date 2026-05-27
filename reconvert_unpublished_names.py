# -*- coding: utf-8 -*-
"""
ace_products의 name만 raw 기준으로 다시 생성 (바이마 미등록 상품 한정)

- 기존 convert와 동일한 로직 (resolve_brand_name_en + 코메타럭스 제거 + format_buyma_product_name + sanitize_text)
- 다른 필드(price/comments/options/variants 등)는 건드리지 않음
- 대상: ace.is_active=1 AND ace.is_published=0 AND raw_data_id 있음

사용법:
    python reconvert_unpublished_names.py             # dry-run, 모든 mall
    python reconvert_unpublished_names.py --apply     # 실제 UPDATE
    python reconvert_unpublished_names.py --source kometa --apply
"""
import os, re, sys, argparse, unicodedata
import pymysql
from dotenv import load_dotenv

load_dotenv()

ap = argparse.ArgumentParser()
ap.add_argument('--apply', action='store_true', help='실제 UPDATE')
ap.add_argument('--source', type=str, default=None, help='특정 source_site만 (예: kometa, okmall)')
ap.add_argument('--limit', type=int, default=None)
args = ap.parse_args()


# ===== converter와 동일한 로직 (복사) =====
def sanitize_text(text):
    if not text:
        return ""
    normalized = unicodedata.normalize('NFD', text)
    sanitized = "".join(c for c in normalized if unicodedata.category(c) != 'Mn')
    replacements = {'’': "'", '‘': "'", '“': '"', '”': '"',
        '–': '-', '—': '-', '™': '(TM)', '®': '(R)',
        '©': '(C)', '…': '...', '½': '1/2', '⅓': '1/3', '¼': '1/4'}
    for o, n in replacements.items():
        sanitized = sanitized.replace(o, n)
    return unicodedata.normalize('NFC', sanitized)


def resolve_brand_name_en(brand_info, raw_brand_name_en_fallback=''):
    """우선순위: mall_brand_name_en (한글체크) → buyma_brand_name 괄호앞 영문 → raw fallback"""
    mall_en = (brand_info.get('source_brand_en') or '').strip()
    if mall_en and not re.search(r'[가-힣]', mall_en):
        return mall_en
    buyma_full = (brand_info.get('buyma_brand_name') or '').strip()
    if buyma_full:
        m = re.match(r'^([^(（]+)', buyma_full)
        if m:
            en_part = m.group(1).strip()
            if en_part and not re.search(r'[가-힣]', en_part):
                return en_part
    return (raw_brand_name_en_fallback or '').strip()


def format_buyma_product_name(brand_name, product_name, model_id=None):
    clean_name = (product_name or '').strip()
    full = f"{clean_name} {model_id}" if model_id else clean_name
    name = f"送料・関税込 | {brand_name} | {full}"
    # ace_products.name VARCHAR(500) 안전망
    if len(name) > 500:
        name = name[:500]
    return name


# ===== mall_brands 매핑 캐시 =====
def load_brand_mapping(cur):
    """mall_name + brand 키 → brand_info"""
    cur.execute("""SELECT mall_name, raw_brand_name, mall_brand_name_en, buyma_brand_id, buyma_brand_name
        FROM mall_brands WHERE is_active=1""")
    cache = {}
    for r in cur.fetchall():
        info = {
            'source_brand_en': r['mall_brand_name_en'],
            'buyma_brand_id': int(r['buyma_brand_id']) if r['buyma_brand_id'] else 0,
            'buyma_brand_name': r['buyma_brand_name'],
        }
        if r['raw_brand_name']:
            cache[(r['mall_name'], r['raw_brand_name'].strip())] = info
    return cache


def get_brand_info(cache, mall_name, brand_en):
    key = (mall_name, (brand_en or '').strip())
    if key in cache:
        info = dict(cache[key])
        if not info.get('buyma_brand_id'):
            info['buyma_brand_name'] = brand_en
        return info
    return {'buyma_brand_id': 0, 'buyma_brand_name': brand_en, 'source_brand_en': None}


# ===== Main =====
conn = pymysql.connect(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'), charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

print('[1/4] mall_brands 매핑 로드')
brand_cache = load_brand_mapping(cur)
print(f'  → {len(brand_cache)} keys')

print('[2/4] 대상 SELECT (raw + 바이마 미등록 ace JOIN)')
sql = """SELECT ap.id ace_id, ap.source_site, ap.name old_name,
    rsd.brand_name_en, rsd.product_name, rsd.model_id
    FROM ace_products ap
    JOIN raw_scraped_data rsd ON ap.raw_data_id = rsd.id
    WHERE ap.is_published=0"""
params = []
if args.source:
    sql += " AND ap.source_site = %s"
    params.append(args.source.lower())
if args.limit:
    sql += f" LIMIT {int(args.limit)}"

cur.execute(sql, params if params else None)
rows = cur.fetchall()
print(f'  → 대상 {len(rows)}건')

print('[3/4] 변환 + 비교')
to_update = []
unchanged = 0
for r in rows:
    site = r['source_site']
    product_name = r['product_name'] or ''
    # kometa 셀러명 제거
    if site == 'kometa':
        product_name = re.sub(r'^\s*코메타럭스\s+', '', product_name).strip()
    # brand
    brand_info = get_brand_info(brand_cache, site, r['brand_name_en'])
    brand_for_title = resolve_brand_name_en(brand_info, r['brand_name_en'])
    new_name = format_buyma_product_name(brand_for_title, product_name, r['model_id'])
    new_name = sanitize_text(new_name)
    if new_name == r['old_name']:
        unchanged += 1
        continue
    to_update.append((r['ace_id'], r['old_name'], new_name))

print(f'  → 변경 대상: {len(to_update)}건')
print(f'  → 변경 없음: {unchanged}건')

# source_site별
from collections import Counter
by_site = Counter()
for r in rows:
    by_site[r['source_site']] += 1
print()
print('  source_site 분포 (전체 대상):')
for s, c in by_site.most_common(15):
    print(f'    {s}: {c}')

print()
print('=== 변환 샘플 (앞 8건) ===')
for ace_id, old, new in to_update[:8]:
    print(f'  id={ace_id}')
    print(f'    OLD: {old[:120]}')
    print(f'    NEW: {new[:120]}')

if args.apply and to_update:
    print(f'\n>>> --apply: UPDATE {len(to_update)}건')
    BATCH = 500
    for i in range(0, len(to_update), BATCH):
        chunk = to_update[i:i+BATCH]
        for ace_id, _, new in chunk:
            cur.execute('UPDATE ace_products SET name=%s WHERE id=%s AND is_published=0', (new, ace_id))
        conn.commit()
        print(f'  진행: {min(i+BATCH, len(to_update))}/{len(to_update)}')
    print('>>> 완료')
else:
    print(f'\n[DRY-RUN] --apply 옵션 없음. 실행: python {sys.argv[0]} --apply')

conn.close()
