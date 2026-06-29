# -*- coding: utf-8 -*-
"""
오염 브랜드(premiumsneakers 5 + veroshopmall ACNE) 정리.
  A 순수오염 listing  → BUYMA delete + listing/오염 db 삭제
  B 정상유지 listing  → 오염 멤버 db만 삭제 (listing 유지)
  C 정상오염 listing  → BUYMA delete + listing 삭제 (정상 ace 는 안 지움 → 자동 재등록은 파이프라인이)
  옛경로 오염 ace(자체 등록) → BUYMA delete
  mall_brands 죽은 6브랜드 → is_active=0
사용: python cleanup_premiumsneakers_contam.py            # 미리보기(쓰기·BUYMA 0) + 백업
      python cleanup_premiumsneakers_contam.py --execute  # 실제 실행
"""
import os, sys, io, json, argparse, time
from datetime import datetime
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import pymysql, requests
from dotenv import load_dotenv
from collections import defaultdict
load_dotenv(os.path.join(BASE, '.env'), override=True)

cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4',
           cursorclass=pymysql.cursors.DictCursor)
MODE = int(os.getenv('BUYMA_MODE', 1))
API = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/') if MODE == 1 else os.getenv('BUYMA_SANDBOX_URL')
TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')

BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      f"cleanup_backup_{datetime.now():%Y%m%d_%H%M%S}.json")

conn = pymysql.connect(**cfg)
def run(sql, p=None):
    with conn.cursor() as c:
        c.execute(sql, p or []); return c.fetchall()
def exec_sql(sql, p=None):
    with conn.cursor() as c:
        c.execute(sql, p or []); return c.rowcount

COND = """((a.source_site='premiumsneakers' AND r.brand_name_en IN ('AMI','COMMON PROJECTS','GUCCI KIDS','BOTTEGA VENETA','ROGER VIVIER'))
        OR (a.source_site='veroshopmall' AND r.brand_name_en='ACNE STUDIOS'))"""

def build_sets():
    ace = run(f"""SELECT a.id, a.buyma_product_id, a.reference_number, a.raw_data_id
                  FROM ace_products a JOIN raw_scraped_data r ON r.id=a.raw_data_id WHERE {COND}""")
    bad_ace = [a['id'] for a in ace]
    bad_set = set(bad_ace)
    bad_raw = sorted({a['raw_data_id'] for a in ace if a['raw_data_id']})
    aph = ','.join(['%s']*len(bad_ace))
    # 옛경로 오염 ace 자체 BUYMA 등록 (ref 보유)
    old_refs = [(a['reference_number'], a['buyma_product_id'], 'old_ace') for a in ace
                if a['reference_number']]
    # 엮인 listing 분류
    offs = run(f"SELECT id, listing_id, ace_product_id FROM source_offerings WHERE ace_product_id IN ({aph})", bad_ace)
    lids = sorted({o['listing_id'] for o in offs})
    A, B, C = [], [], []
    A_refs, C_refs = [], []
    B_bad_offerings = []
    if lids:
        lph = ','.join(['%s']*len(lids))
        for l in run(f"SELECT id, brand_id, buyma_product_id, reference_number, locked_reference_number FROM buyma_listings WHERE id IN ({lph})", lids):
            lid = l['id']
            members = run("SELECT ace_product_id FROM source_offerings WHERE listing_id=%s", [lid])
            legit = [m['ace_product_id'] for m in members if m['ace_product_id'] not in bad_set]
            ref = l['locked_reference_number'] or l['reference_number']
            if not legit:
                A.append(lid)
                if ref: A_refs.append((ref, l['buyma_product_id'], 'A_listing'))
            else:
                legit_brands = set(x['brand_id'] for x in run(
                    f"SELECT brand_id FROM ace_products WHERE id IN ({','.join(['%s']*len(legit))})", legit))
                if l['brand_id'] in legit_brands:
                    B.append(lid)
                    B_bad_offerings += [o['id'] for o in offs if o['listing_id'] == lid]
                else:
                    C.append(lid)
                    if ref: C_refs.append((ref, l['buyma_product_id'], 'C_listing'))
    # BUYMA 삭제 ref 중복 제거
    seen, del_refs = set(), []
    for ref, bpid, src in old_refs + A_refs + C_refs:
        if ref and ref not in seen:
            seen.add(ref); del_refs.append({'ref': ref, 'buyma_product_id': bpid, 'src': src})
    return dict(bad_ace=bad_ace, bad_raw=bad_raw, A=A, B=B, C=C,
                B_bad_offerings=sorted(set(B_bad_offerings)), del_refs=del_refs)

def preview(s):
    print(f"\n{'='*64}\n[미리보기] 삭제 대상 (쓰기·BUYMA 호출 0)\n{'='*64}")
    print(f"오염 ace 삭제      : {len(s['bad_ace'])}")
    print(f"오염 raw 삭제      : {len(s['bad_raw'])}")
    print(f"A 순수 listing 삭제 : {len(s['A'])}")
    print(f"B 정상 listing 유지 : {len(s['B'])}  (오염 offering {len(s['B_bad_offerings'])}개만 제거)")
    print(f"C 정상오염 listing 삭제: {len(s['C'])}  (정상 ace 는 안 지움 → 자동 재등록)")
    print(f"BUYMA delete (ref 중복제거): {len(s['del_refs'])}")
    src_cnt = defaultdict(int)
    for d in s['del_refs']: src_cnt[d['src']] += 1
    print(f"   내역: {dict(src_cnt)}")
    mb = run("""SELECT mall_name, mall_brand_name_en FROM mall_brands
                WHERE (mall_name='premiumsneakers' AND mall_brand_name_en IN ('AMI','COMMON PROJECTS','GUCCI KIDS','BOTTEGA VENETA','ROGER VIVIER'))
                   OR (mall_name='veroshopmall' AND mall_brand_name_en='ACNE STUDIOS')""")
    print(f"mall_brands is_active=0: {len(mb)}건 {[m['mall_brand_name_en'] for m in mb]}")
    with open(BACKUP, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n백업 저장: {BACKUP}")

def buyma_delete(ref):
    try:
        r = requests.post(f"{API}api/v1/products",
                          headers={"Content-Type": "application/json",
                                   "X-Buyma-Personal-Shopper-Api-Access-Token": TOKEN},
                          json={"product": {"control": "delete", "reference_number": ref}}, timeout=30)
        return r.status_code in (200, 201, 202), r.status_code, r.text[:120]
    except Exception as e:
        return False, 0, str(e)[:120]

def execute(s, skip_buyma=False):
    global conn
    print(f"\n{'='*64}\n[실행]\n{'='*64}")
    hist = {'started_at': str(datetime.now()), 'buyma': [], 'db': {}}
    HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f"cleanup_executed_{datetime.now():%Y%m%d_%H%M%S}.json")
    # 1. BUYMA delete
    if skip_buyma:
        print("BUYMA delete 스킵 (이미 604/604 완료됨)")
        hist['buyma_summary'] = {'skipped': True, 'note': '이전 실행에서 604/604 성공'}
    else:
        ok = fail = 0
        for i, d in enumerate(s['del_refs'], 1):
            success, code, msg = buyma_delete(d['ref'])
            hist['buyma'].append({'ref': d['ref'], 'buyma_product_id': d['buyma_product_id'],
                                  'src': d['src'], 'success': success, 'code': code})
            if success: ok += 1
            else:
                fail += 1
                if fail <= 30: print(f"  delete 실패 {d['ref']} code={code} {msg}")
            if i % 50 == 0: print(f"  BUYMA delete {i}/{len(s['del_refs'])} (ok {ok}/fail {fail})")
            time.sleep(0.25)
        print(f"BUYMA delete 완료: 성공 {ok} / 실패(미등록추정 포함) {fail}")
        hist['buyma_summary'] = {'total': len(s['del_refs']), 'ok': ok, 'fail': fail}
    # ★ DB 연결 재생성 (BUYMA 도는 동안 유휴 타임아웃으로 끊겼을 수 있음)
    try: conn.close()
    except Exception: pass
    conn = pymysql.connect(**cfg)
    # 2. DB 삭제
    aph = ','.join(['%s']*len(s['bad_ace']))
    db = hist['db']
    # B: 오염 offering 만 제거 (옵션 cascade)
    if s['B_bad_offerings']:
        oph = ','.join(['%s']*len(s['B_bad_offerings']))
        db['B_offering_del'] = exec_sql(f"DELETE FROM source_offerings WHERE id IN ({oph})", s['B_bad_offerings'])
        print(f"  B 오염 offering 삭제: {db['B_offering_del']}")
    # A+C listing 삭제 (cascade: offerings/options/listing_options/images)
    AC = s['A'] + s['C']
    if AC:
        lph = ','.join(['%s']*len(AC))
        db['AC_listing_del'] = exec_sql(f"DELETE FROM buyma_listings WHERE id IN ({lph})", AC)
        print(f"  A+C listing 삭제(cascade): {db['AC_listing_del']}")
    # 남은 오염 offering (listing 안 엮인 것 등) 정리
    db['offering_cleanup'] = exec_sql(f"DELETE FROM source_offerings WHERE ace_product_id IN ({aph})", s['bad_ace'])
    # ace 자식 + ace + raw
    for t in ['ace_product_variants', 'ace_product_options', 'ace_product_images']:
        db[t] = exec_sql(f"DELETE FROM {t} WHERE ace_product_id IN ({aph})", s['bad_ace'])
    db['ace_products'] = exec_sql(f"DELETE FROM ace_products WHERE id IN ({aph})", s['bad_ace'])
    print(f"  ace_products 삭제: {db['ace_products']}")
    if s['bad_raw']:
        rph = ','.join(['%s']*len(s['bad_raw']))
        db['raw_scraped_data'] = exec_sql(f"DELETE FROM raw_scraped_data WHERE id IN ({rph})", s['bad_raw'])
        print(f"  raw_scraped_data 삭제: {db['raw_scraped_data']}")
    # mall_brands is_active=0
    db['mall_brands_deactivated'] = exec_sql("""UPDATE mall_brands SET is_active=0
                    WHERE (mall_name='premiumsneakers' AND mall_brand_name_en IN ('AMI','COMMON PROJECTS','GUCCI KIDS','BOTTEGA VENETA','ROGER VIVIER'))
                       OR (mall_name='veroshopmall' AND mall_brand_name_en='ACNE STUDIOS')""")
    print(f"  mall_brands is_active=0: {db['mall_brands_deactivated']}")
    conn.commit()
    print("DB 커밋 완료.")
    hist['finished_at'] = str(datetime.now())
    hist['backup_file'] = BACKUP
    with open(HISTORY, 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n실행 이력 저장: {HISTORY}")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--skip-buyma', action='store_true', help='BUYMA delete 건너뛰고 DB만 (BUYMA 이미 완료 시)')
    args = ap.parse_args()
    s = build_sets()
    preview(s)
    if args.execute:
        execute(s, skip_buyma=args.skip_buyma)
    else:
        print("\n(미리보기만. 실제 실행은 --execute)")
    try: conn.close()
    except Exception: pass
