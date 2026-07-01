# -*- coding: utf-8 -*-
"""
scoring Phase 1 스키마 — 점수 인덱스 테이블 + 파라미터 테이블.
설계: 점수는 파생·휘발값 + Swap이 Top-K/Bottom-K 정렬조회를 요구 → hot한 buyma_listings에
      얹지 않고 얇은 전용 테이블(score DESC 인덱스)로 분리. (스펙 8.1 score_index_* 설계와 일치)
멱등(재실행 안전). 신규 테이블만 생성하므로 buyma_listings 락과 무관(러너 돌아도 OK).

  score_index_listed  : listing_id(=buyma_listings.id) → score(원/일), calculated_at. score 인덱스.
  scoring_parameters  : P1~P9 seed. 운영자 튜닝(O1).

사용: python scoring_phase1_schema.py            # 미리보기
      python scoring_phase1_schema.py --execute  # 적용
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
 'score_index_listed': """
    CREATE TABLE IF NOT EXISTS score_index_listed (
        listing_id    INT PRIMARY KEY,
        score         DECIMAL(14,4),          -- 예상일일마진액 (원/일)
        calculated_at DATETIME,
        INDEX idx_score (score)               -- Top-K / Bottom-K 조회
    )""",
 'scoring_parameters': """
    CREATE TABLE IF NOT EXISTS scoring_parameters (
        param_key     VARCHAR(64) PRIMARY KEY,
        value         TEXT,
        type          VARCHAR(16),
        default_value TEXT,
        description   TEXT,
        updated_by    VARCHAR(64),
        updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""",
}

PARAMS = [
    ('grace_period_days',     '30',       'int',   '유예기간(일) — 이 기간 시간감점 1.0'),
    ('decay_threshold_days',  '60',       'int',   '시간감점 임계점(일)'),
    ('decay_threshold_value', '0.70',     'float', '임계점 시점 잔여 배율'),
    ('decay_zero_days',       '90',       'int',   '베이스라인 보호 해제일(일)'),
    ('baseline_alpha',        '0.001544', 'float', '베이스라인 α'),
    ('w_favorite',            '0.05644',  'float', '일평균 찜수 가중치 (P5)'),
    ('w_access',              '0.00177',  'float', '일평균 조회수 가중치 (P6)'),
    ('w_cart',                '0.9',      'float', '일평균 장바구니 가중치 (P7)'),
    ('w_sold',                '1.0',      'float', '일평균 판매수 가중치 (P8)'),
    ('margin_floor_policy',   'exclude',  'str',   '음수마진 처리 (P9)'),
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

    print("\n=== scoring_parameters 현재값 ===")
    cur.execute("SELECT param_key, value, type, description FROM scoring_parameters ORDER BY param_key")
    for r in cur.fetchall():
        print(f"  {r['param_key']:<22} = {str(r['value']):<10} ({r['type']})  {r['description']}")
    conn.close(); print("\n[스키마 적용 완료]")


if __name__ == '__main__':
    main()
