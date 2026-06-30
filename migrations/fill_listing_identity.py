# -*- coding: utf-8 -*-
"""
단일권위 1단계 — 복사(FILL): 라이브 ace 의 정체성을 그 그룹의 buyma_listings 행에 복사.
  ace 는 안 끔(은퇴는 3단계). listing.is_published=1 로 켬 → 이중상태(둘 다 맞음, 안전).
버킷① 한정: 기존 listing 행이 있고(그림자), 라이브 ace 멤버가 정확히 1개(충돌 아님).

복사 항목: buyma_product_id, reference_number(=ace의 locked_ref or ref), locked_reference_number,
           buyma_registered_at, status='success', is_buyma_locked=1, is_published=1, locked_name/brand/category.

게시일수 트리거는 잠시 DROP 후 복구(FILL의 listing up 이 노이즈 이벤트 만드는 것 방지. 카운터는 어차피 bid 동일이라 불변).

사용:
  python fill_listing_identity.py --limit 50            # 미리보기(쓰기 0)
  python fill_listing_identity.py --limit 50 --execute  # 실제 50건 FILL
  python fill_listing_identity.py --execute             # 전체 FILL
"""
import os, sys, io, re, json, argparse
from datetime import datetime
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pymysql
from dotenv import load_dotenv
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT',3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4',
           cursorclass=pymysql.cursors.DictCursor)
TRIG_FILE = os.path.join(BASE, 'migrations', '2026_listing_days_triggers.sql')
BK = os.path.join(BASE, 'migrations', f"fill_identity_backup_{datetime.now():%Y%m%d_%H%M%S}.json")

# 버킷① 후보: 그림자 listing(미게시·id없음·winner있음) + 라이브 ace 멤버 정확히 1개(충돌 제외)
CANDIDATE_SQL = """
SELECT bl.id AS listing_id, a.id AS ace_id, a.buyma_product_id,
       COALESCE(a.locked_reference_number, a.reference_number) AS ref,
       a.buyma_registered_at, a.locked_name, a.locked_brand_id, a.locked_category_id
FROM buyma_listings bl
JOIN source_offerings so ON so.listing_id = bl.id
JOIN ace_products a ON a.id = so.ace_product_id
WHERE bl.is_published=0 AND bl.buyma_product_id IS NULL AND bl.winner_offering_id IS NOT NULL
  AND a.is_published=1 AND a.buyma_product_id IS NOT NULL
  AND (SELECT COUNT(DISTINCT a2.buyma_product_id)
       FROM source_offerings so2 JOIN ace_products a2 ON a2.id=so2.ace_product_id
       WHERE so2.listing_id=bl.id AND a2.is_published=1 AND a2.buyma_product_id IS NOT NULL) = 1
  AND NOT EXISTS (SELECT 1 FROM buyma_listings bl2
                  WHERE bl2.reference_number = COALESCE(a.locked_reference_number, a.reference_number)
                    AND bl2.id <> bl.id)
ORDER BY bl.id
"""

def recreate_triggers(cur):
    sql = open(TRIG_FILE, encoding='utf-8').read()
    body = '\n'.join(l for l in sql.splitlines() if not l.strip().startswith('--'))
    body = re.sub(r'(?i)\buse\s+buyma\s*;', '', body)
    for d in re.findall(r'DROP TRIGGER IF EXISTS[^;]+;', body):
        cur.execute(d.rstrip(';'))
    for cr in re.findall(r'CREATE TRIGGER.*?END;', body, re.DOTALL):
        cur.execute(cr.rstrip(';'))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    conn = pymysql.connect(**cfg); conn.autocommit(False)
    cur = conn.cursor()

    sql = CANDIDATE_SQL + (f" LIMIT {int(args.limit)}" if args.limit else "")
    cur.execute(sql); cands = cur.fetchall()
    # ref 중복 제거 (같은 ace가 여러 listing에 → ref 하나당 listing 1개만 충전, 나머지는 draft로 남김)
    _seen=set(); _ded=[]
    for _c in cands:
        if _c['ref'] in _seen: continue
        _seen.add(_c['ref']); _ded.append(_c)
    if len(_ded) < len(cands):
        print(f"  ref 중복 {len(cands)-len(_ded)}건 제외(같은 ace 다중 listing — 1개만 충전)")
    cands = _ded
    print(f"[버킷① 후보] {len(cands)}건" + (f" (limit {args.limit})" if args.limit else " (전체)"))
    if cands[:3]:
        for c in cands[:3]:
            print(f"   listing#{c['listing_id']} ← ace#{c['ace_id']} buyma_id={c['buyma_product_id']} ref={c['ref']}")
    if not args.execute:
        print("\n(미리보기 — 실제 복사는 --execute)"); conn.close(); return

    ids = [c['listing_id'] for c in cands]
    # 롤백 백업: 채우기 전 listing 상태
    with conn.cursor() as c2:
        ph = ','.join(['%s']*len(ids))
        c2.execute(f"SELECT id, buyma_product_id, reference_number, locked_reference_number, is_published, status, is_buyma_locked, buyma_registered_at, locked_name, locked_brand_id, locked_category_id FROM buyma_listings WHERE id IN ({ph})", ids)
        before = c2.fetchall()
    json.dump({'filled_ids': ids, 'before': before}, open(BK,'w',encoding='utf-8'), ensure_ascii=False, default=str, indent=1)
    print(f"[백업] {BK}")

    print("[트리거 OFF] 게시일수 트리거 2개 DROP")
    cur.execute("DROP TRIGGER IF EXISTS trg_ace_listing_days")
    cur.execute("DROP TRIGGER IF EXISTS trg_listings_listing_days")
    try:
        n = 0
        for i, c in enumerate(cands, 1):
            cur.execute("""
                UPDATE buyma_listings SET
                    buyma_product_id=%s, reference_number=%s, locked_reference_number=%s,
                    buyma_registered_at=%s, status='success', is_buyma_locked=1, is_published=1,
                    locked_name=COALESCE(locked_name,%s), locked_brand_id=COALESCE(locked_brand_id,%s),
                    locked_category_id=COALESCE(locked_category_id,%s), updated_at=CURRENT_TIMESTAMP
                WHERE id=%s AND buyma_product_id IS NULL""",
                (c['buyma_product_id'], c['ref'], c['ref'], c['buyma_registered_at'],
                 c['locked_name'], c['locked_brand_id'], c['locked_category_id'], c['listing_id']))
            n += cur.rowcount
            if i % 2000 == 0:
                conn.commit(); print(f"   ... {i}/{len(cands)} 처리 (누적 {n})")
        conn.commit()
        print(f"[FILL 완료] {n}건 listing 정체성 복사 (ace 는 그대로)")
    finally:
        print("[트리거 ON] 복구")
        recreate_triggers(cur); conn.commit()
        cur.execute("SHOW TRIGGERS LIKE 'ace_products'"); a=len(cur.fetchall())
        cur.execute("SHOW TRIGGERS LIKE 'buyma_listings'"); b=len(cur.fetchall())
        print(f"   트리거 확인: ace_products {a}개 / buyma_listings {b}개")
    conn.close()
    print("\n[1단계 FILL 종료]")

if __name__ == '__main__':
    main()
