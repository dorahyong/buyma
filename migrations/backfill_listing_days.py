# -*- coding: utf-8 -*-
"""
게시일수 백필 (3단계) — 현재 게시중 상품의 현재 구간 시작점을 시드.

원칙(설계 확정):
  - 이미 올라간 상품은 "오늘부터"가 아니라 "등록일부터" (스코어링 시간감점이 오래된 부진상품을 도태시키려면 실제 등록기간이 필요).
  - listed_since = 실제 등록시각, accumulated_seconds = 0 (과거 down 이력은 복원 불가 → 트리거 가동 시점부터 정확 누적).
  - 키 = buyma_product_id. ace(옛 직접등록) + buyma_listings(신규 병합) 둘 다 시드.

listed_since 출처(우선순위):
  - ace 분     : buyma_registered_at → (없으면) created_at → (없으면) NOW()
  - listings 분: 멤버 ace 최소 buyma_registered_at → (없으면) buyma_listings.updated_at → NOW()

사용:
  python backfill_listing_days.py            # dry-run (읽기만, 무엇을 시드할지 리포트)
  python backfill_listing_days.py --execute  # 실제 시드
"""
import os, sys, io, argparse
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pymysql
from dotenv import load_dotenv
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4',
           cursorclass=pymysql.cursors.DictCursor)

# 멤버 ace 최소 등록시각 서브쿼리 (listings 용)
MEMBER_REG = ("(SELECT MIN(ap.buyma_registered_at) FROM source_offerings so "
              "JOIN ace_products ap ON ap.id=so.ace_product_id "
              "WHERE so.listing_id=bl.id AND ap.buyma_registered_at IS NOT NULL)")

INSERT_ACE = """
INSERT INTO buyma_listing_days (buyma_product_id, listed_since, accumulated_seconds, first_listed_at, is_listed, last_event_at)
SELECT a.buyma_product_id,
       COALESCE(a.buyma_registered_at, a.created_at, NOW()),
       0,
       COALESCE(a.buyma_registered_at, a.created_at, NOW()),
       1, NULL
FROM ace_products a
WHERE a.is_published=1 AND a.buyma_product_id IS NOT NULL
ON DUPLICATE KEY UPDATE buyma_product_id = buyma_listing_days.buyma_product_id
"""
INSERT_LISTINGS = f"""
INSERT INTO buyma_listing_days (buyma_product_id, listed_since, accumulated_seconds, first_listed_at, is_listed, last_event_at)
SELECT bl.buyma_product_id,
       COALESCE({MEMBER_REG}, bl.updated_at, NOW()),
       0,
       COALESCE({MEMBER_REG}, bl.updated_at, NOW()),
       1, NULL
FROM buyma_listings bl
WHERE bl.is_published=1 AND bl.buyma_product_id IS NOT NULL
ON DUPLICATE KEY UPDATE buyma_product_id = buyma_listing_days.buyma_product_id
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    conn = pymysql.connect(**cfg)
    def one(sql, p=None):
        with conn.cursor() as c: c.execute(sql, p or []); return c.fetchone()
    def run(sql, p=None):
        with conn.cursor() as c: c.execute(sql, p or []); return c.fetchall()

    print("="*64); print("[백필 대상 분석]"); print("="*64)
    a_total = one("SELECT COUNT(*) n FROM ace_products WHERE is_published=1 AND buyma_product_id IS NOT NULL")['n']
    a_reg = one("SELECT COUNT(*) n FROM ace_products WHERE is_published=1 AND buyma_product_id IS NOT NULL AND buyma_registered_at IS NOT NULL")['n']
    print(f"ace 분: {a_total}  (등록일 보유 {a_reg} / created_at 폴백 {a_total-a_reg})")
    b_total = one("SELECT COUNT(*) n FROM buyma_listings WHERE is_published=1 AND buyma_product_id IS NOT NULL")['n']
    b_member = one(f"SELECT COUNT(*) n FROM buyma_listings bl WHERE bl.is_published=1 AND bl.buyma_product_id IS NOT NULL AND {MEMBER_REG} IS NOT NULL")['n']
    print(f"listings 분: {b_total}  (멤버등록일 보유 {b_member} / updated_at 폴백 {b_total-b_member})")
    print(f"합계 시드 예정(중복 제거 전): {a_total + b_total}")

    print("\n[샘플] 시드 시 계산되는 게시일수 (오늘 기준)")
    for r in run(f"""SELECT a.buyma_product_id,
                            COALESCE(a.buyma_registered_at, a.created_at) AS listed_since,
                            ROUND(TIMESTAMPDIFF(SECOND, COALESCE(a.buyma_registered_at, a.created_at), NOW())/86400.0,1) AS days
                     FROM ace_products a
                     WHERE a.is_published=1 AND a.buyma_product_id IS NOT NULL
                     ORDER BY a.buyma_registered_at LIMIT 3"""):
        print(f"   ace  buyma_id={r['buyma_product_id']} 등록일={r['listed_since']} → {r['days']}일")
    for r in run(f"""SELECT bl.buyma_product_id,
                            COALESCE({MEMBER_REG}, bl.updated_at) AS listed_since,
                            ROUND(TIMESTAMPDIFF(SECOND, COALESCE({MEMBER_REG}, bl.updated_at), NOW())/86400.0,1) AS days
                     FROM buyma_listings bl
                     WHERE bl.is_published=1 AND bl.buyma_product_id IS NOT NULL LIMIT 3"""):
        print(f"   list buyma_id={r['buyma_product_id']} 시작={r['listed_since']} → {r['days']}일")

    if not args.execute:
        print("\n(dry-run — 실제 시드는 --execute)")
        conn.close(); return

    print("\n" + "="*64); print("[실행] 시드 INSERT"); print("="*64)
    with conn.cursor() as c:
        n1 = c.execute(INSERT_ACE);      print(f"  ace 분 처리: {n1}")
        n2 = c.execute(INSERT_LISTINGS); print(f"  listings 분 처리: {n2}")
    conn.commit()
    total = one("SELECT COUNT(*) n FROM buyma_listing_days")['n']
    listed = one("SELECT COUNT(*) n FROM buyma_listing_days WHERE is_listed=1")['n']
    print(f"  buyma_listing_days 총 {total}행 (게시중 {listed})")
    print("\n[검증] v_listing_days 분포")
    for r in run("""SELECT
        SUM(total_listed_days<30) d0_30, SUM(total_listed_days>=30 AND total_listed_days<60) d30_60,
        SUM(total_listed_days>=60 AND total_listed_days<90) d60_90, SUM(total_listed_days>=90) d90p,
        ROUND(AVG(total_listed_days),1) avg_days, ROUND(MAX(total_listed_days),1) max_days
        FROM v_listing_days WHERE is_listed=1"""):
        print(f"   <30일:{r['d0_30']} / 30~60:{r['d30_60']} / 60~90:{r['d60_90']} / 90일+:{r['d90p']}  (평균 {r['avg_days']}일, 최대 {r['max_days']}일)")
    conn.close()
    print("\n[백필 완료]")

if __name__ == '__main__':
    main()
