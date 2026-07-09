# -*- coding: utf-8 -*-
"""
ace_product_thumbnails 에 'BUYMA 반영' 추적 컬럼 추가.

  buyma_applied_at   대표이미지를 뱃지 썸네일로 BUYMA 에 보낸 시각(NULL=아직)
  buyma_apply_error  전송 실패 시 이유

이러면:
  반영 완료          = buyma_applied_at IS NOT NULL
  수동 배치 대상     = is_generated=1 AND buyma_applied_at IS NULL

멱등(재실행 안전): 컬럼이 이미 있으면 건너뜀.

사용:
  python thumbnail_applied_columns.py            # 미리보기
  python thumbnail_applied_columns.py --execute  # 적용
"""
import os, sys, io, argparse
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
DB = os.getenv('DB_NAME')
cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=DB, charset='utf8mb4',
           cursorclass=pymysql.cursors.DictCursor)

TABLE = 'ace_product_thumbnails'
# 추가할 컬럼: (이름, ADD 절)
COLUMNS = [
    ('buyma_applied_at',  "ADD COLUMN buyma_applied_at DATETIME NULL DEFAULT NULL "
                          "COMMENT 'BUYMA에 뱃지썸네일로 반영한 시각(NULL=아직)'"),
    ('buyma_apply_error', "ADD COLUMN buyma_apply_error TEXT NULL DEFAULT NULL "
                          "COMMENT 'BUYMA 반영 실패 이유'"),
]
INDEX = ('idx_applied', "ADD INDEX idx_applied (buyma_applied_at)")


def existing_columns(cur):
    cur.execute("""SELECT COLUMN_NAME FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s""", (DB, TABLE))
    return {r['COLUMN_NAME'] for r in cur.fetchall()}


def existing_indexes(cur):
    cur.execute("""SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s""", (DB, TABLE))
    return {r['INDEX_NAME'] for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()

    conn = pymysql.connect(**cfg)
    cur = conn.cursor()
    have_cols = existing_columns(cur)
    have_idx = existing_indexes(cur)

    todo = [(name, clause) for name, clause in COLUMNS if name not in have_cols]
    add_index = INDEX[0] not in have_idx

    print(f"=== {TABLE} 변경 계획 ===")
    if not todo and not add_index:
        print("  (이미 모두 적용됨 — 할 일 없음)")
    for name, _ in todo:
        print(f"  + ADD COLUMN {name}")
    if add_index:
        print(f"  + ADD INDEX {INDEX[0]}")
    if not args.execute:
        print("\n(미리보기 — 실제 적용은 --execute)")
        conn.close()
        return

    clauses = [c for _, c in todo]
    if add_index:
        clauses.append(INDEX[1])
    if clauses:
        cur.execute(f"ALTER TABLE {TABLE} " + ", ".join(clauses))
        conn.commit()
    print("\n=== 적용 후 컬럼 ===")
    cur.execute(f"SHOW COLUMNS FROM {TABLE}")
    for r in cur.fetchall():
        print(f"  {r['Field']:<26} {r['Type']}")
    conn.close()
    print("\n[적용 완료]")


if __name__ == '__main__':
    main()
