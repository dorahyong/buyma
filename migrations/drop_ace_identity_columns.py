# -*- coding: utf-8 -*-
"""ace/listings 에서 안 쓰는 정체성·설명 컬럼 제거 (단일권위 정리 마무리).

지우는 것
  ace_products    : control, reference_number, buyma_product_id
  buyma_listings  : control, comments

근거 (2026-08-04 기준, run_daily_unified.py 실행 코드에서 읽는 곳 0)
  - control        : 항상 코드 상수 publish 로 보냄. DB 값은 판단에 안 씀
  - reference_number: 등록 직전 buyma_listings 에만 발급. ace 값은 헛번호(93.6%)
  - buyma_product_id: 웹훅이 buyma_listings 에만 기록. 등록판정도 목록 기준
  - comments       : 요청서 만들 때 조립. 컬럼은 전 행 비어 있음

안전장치
  1) 지우기 전 ace 정체성 3컬럼을 bak_ace_identity_<날짜> 로 통째 백업
  2) 인덱스 먼저 제거 → 컬럼은 ALGORITHM=INSTANT 로 즉시 삭제(테이블 잠금 최소)
  3) 각 단계 소요시간 출력. --execute 없으면 계획만 출력

사용:
    python migrations/drop_ace_identity_columns.py            # 계획만
    python migrations/drop_ace_identity_columns.py --execute  # 백업 + 실제 삭제
"""
import os
import sys
import time
import argparse

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

import pymysql
from dotenv import load_dotenv
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
DB = os.getenv('DB_NAME')

DROPS = [
    ('ace_products', 'control'),
    ('ace_products', 'reference_number'),
    ('ace_products', 'buyma_product_id'),
    ('buyma_listings', 'control'),
    ('buyma_listings', 'comments'),
]


def conn():
    return pymysql.connect(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
                           user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
                           database=DB, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()

    c = conn()
    cur = c.cursor()

    # 현재 존재하는 컬럼만 대상으로
    targets = []
    for t, col in DROPS:
        cur.execute("""SELECT 1 FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s""", (DB, t, col))
        if cur.fetchone():
            targets.append((t, col))
    print("지울 컬럼:")
    for t, col in targets:
        cur.execute("""SELECT INDEX_NAME FROM information_schema.STATISTICS
                        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s""", (DB, t, col))
        idx = [r['INDEX_NAME'] for r in cur.fetchall()]
        print(f"   {t}.{col}" + (f"   (인덱스 먼저 제거: {', '.join(idx)})" if idx else ""))

    if not args.execute:
        print("\n(계획만 — 실제 삭제는 --execute)")
        c.close()
        return

    # 1) 백업
    tag = datetime.now().strftime('%Y%m%d')
    bak = f"bak_ace_identity_{tag}"
    t0 = time.time()
    cur.execute(f"DROP TABLE IF EXISTS {bak}")
    cur.execute(f"""CREATE TABLE {bak} AS
                    SELECT id, reference_number, buyma_product_id, control, is_published, status
                      FROM ace_products
                     WHERE reference_number IS NOT NULL OR buyma_product_id IS NOT NULL""")
    c.commit()
    cur.execute(f"SELECT COUNT(*) n FROM {bak}")
    print(f"\n백업 테이블 {bak}: {cur.fetchone()['n']:,}행 ({time.time()-t0:.1f}초)")

    # 2) 인덱스 제거 → 컬럼 즉시 삭제
    for t, col in targets:
        cur.execute("""SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS
                        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s""", (DB, t, col))
        for r in cur.fetchall():
            idx = r['INDEX_NAME']
            s = time.time()
            cur.execute(f"ALTER TABLE {t} DROP INDEX `{idx}`")
            print(f"  인덱스 삭제 {t}.{idx} ({time.time()-s:.1f}초)")
        s = time.time()
        try:
            cur.execute(f"ALTER TABLE {t} DROP COLUMN `{col}`, ALGORITHM=INSTANT")
            how = 'INSTANT'
        except Exception as e:
            print(f"  (INSTANT 불가 → 일반 방식으로: {str(e)[:80]})")
            cur.execute(f"ALTER TABLE {t} DROP COLUMN `{col}`")
            how = '일반'
        c.commit()
        print(f"  컬럼 삭제 {t}.{col} [{how}] ({time.time()-s:.1f}초)")

    # 3) 묶음 인덱스 복구 — idx_published_active 는 (is_published, is_active, buyma_product_id) 였다.
    #    컬럼을 지우느라 통째로 없앴으므로, 여전히 쓸모 있는 앞 두 컬럼으로 다시 만든다.
    cur.execute("""SELECT 1 FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='ace_products'
                      AND INDEX_NAME='idx_published_active'""", (DB,))
    if not cur.fetchone():
        s = time.time()
        cur.execute("CREATE INDEX idx_published_active ON ace_products (is_published, is_active)")
        c.commit()
        print(f"  인덱스 재생성 ace_products.idx_published_active (is_published, is_active) ({time.time()-s:.1f}초)")

    print("\n완료. 되돌리려면 백업 테이블에서 값을 복원한 뒤 컬럼을 다시 추가한다.")
    c.close()


if __name__ == '__main__':
    main()
