# -*- coding: utf-8 -*-
"""
scoring Phase 2 스키마 — 대기풀(fresh) 점수 인덱스 + 코호트 prior 테이블 + W 파라미터.
설계: Phase 1(scoring_phase1_schema.py)과 대칭. 신규 테이블만 생성 → buyma_listings 락 무관(러너 돌아도 OK). 멱등.

  score_index_fresh : listing_id(=buyma_listings.id) → score(원/일), calculated_at. score 인덱스.
  cohort_priors     : 브랜드/카테고리 코호트별 예상 일평균판매(prior). 스펙 §8.1.
                      category_id=0 = 해당 층에서 카테고리 미사용(sentinel, 실제 category_id는 절대 0 아님).
                      cohort_level: bc(브랜드×카테고리) / b(브랜드) / c(카테고리) / global(전체)
  scoring_parameters: W2~W9 seed 추가(Phase1의 P1~P9 옆에). O1(코드 하드코딩 금지).

사용: python scoring_phase2_schema.py            # 미리보기
      python scoring_phase2_schema.py --execute  # 적용
"""
import os, sys, io, argparse
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pymysql
from dotenv import load_dotenv
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
DB = os.getenv('DB_NAME')
cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT',3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=DB, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)

DDL = {
 'score_index_fresh': """
    CREATE TABLE IF NOT EXISTS score_index_fresh (
        listing_id    INT PRIMARY KEY,
        score         DECIMAL(14,4),          -- 예상일일마진액 (원/일)
        calculated_at DATETIME,
        INDEX idx_score (score)               -- Top-K 조회 (Fill 후보)
    )""",
 'cohort_priors': """
    CREATE TABLE IF NOT EXISTS cohort_priors (
        brand_normalized VARCHAR(128) NOT NULL,   -- upper().strip(). '_GLOBAL_'=전체층
        category_id      INT NOT NULL,            -- 0=카테고리 미사용(sentinel)
        cohort_level     ENUM('bc','b','c','global') NOT NULL,
        sample_count     INT,                     -- 코호트 내 출품중 표본 수
        sample_avg       DECIMAL(18,10),          -- 표본 평균 일평균판매(원신호)
        smoothed_prior   DECIMAL(18,10),          -- shrinkage 후 prior
        calculated_at    DATETIME,
        PRIMARY KEY (brand_normalized, category_id, cohort_level),
        INDEX idx_level (cohort_level)
    )""",
}

# 스펙 §9.2 W2~W9 + 우리 확장 스위치 1개.
#   W1(waiting_pool_statuses)·W4/W10(정규화 맵)은 코드/데이터로 관리 → 파라미터 테이블 seed 생략.
PARAMS = [
    ('kappa_bc',            '100',      'int',   '브랜드×카테고리 shrinkage 강도 (W2)'),
    ('global_avg_daily_sales','0.000089','float', '전체 평균 일평균판매 fallback (W3). 데이터로 재계산됨'),
    ('min_cohort_samples',  '10',       'int',   '브랜드×카테고리 최소 표본 (W5)'),
    ('kappa_b',             '50',       'int',   'brand 평균 shrinkage 강도 (W6)'),
    ('min_brand_samples',   '10',       'int',   'brand 최소 표본 (W7)'),
    ('kappa_c',             '50',       'int',   'category 평균 shrinkage 강도 (W8)'),
    ('min_category_samples','30',       'int',   'category 최소 표본 (W9)'),
    # 우리 확장(스펙 외): prior 기준 신호. 'sold'=판매만(스펙 기본). 'composite'=찜·조회·장바구니·판매 가중합(나중 튜닝).
    ('fresh_prior_signal',  'sold',     'str',   "prior 기준: 'sold'(스펙 기본) | 'composite'(나중 튜닝 전환용)"),
]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    conn = pymysql.connect(**cfg); cur = conn.cursor()

    print("=== 적용 계획 (신규 테이블만 — buyma_listings 락 무관) ===")
    for t in DDL: print(f"  + CREATE TABLE IF NOT EXISTS {t}")
    print(f"  + scoring_parameters seed {len(PARAMS)}건 (INSERT IGNORE)")
    if not args.execute:
        print("\n(미리보기 — 실제 적용은 --execute)"); conn.close(); return

    for t, sql in DDL.items():
        cur.execute(sql); print(f"  ✅ {t}")
    for k, v, ty, d in PARAMS:
        cur.execute("""INSERT IGNORE INTO scoring_parameters (param_key, value, type, default_value, description, updated_by)
                       VALUES (%s,%s,%s,%s,%s,'init')""", (k, v, ty, v, d))
    conn.commit(); print("  ✅ seed 완료")

    print("\n=== scoring_parameters 현재값 (W·확장) ===")
    cur.execute("SELECT param_key, value, type, description FROM scoring_parameters WHERE param_key IN (%s)" %
                ','.join(['%s']*len(PARAMS)), tuple(k for k,_,_,_ in PARAMS))
    for r in cur.fetchall():
        print(f"  {r['param_key']:<24} = {str(r['value']):<10} ({r['type']})  {r['description']}")
    conn.close(); print("\n[스키마 적용 완료]")


if __name__ == '__main__':
    main()
