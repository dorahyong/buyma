# -*- coding: utf-8 -*-
"""buying_shop_name 을 buyma_listings 단독 보관으로 옮기고 ace 컬럼을 지운다.

왜:
  - 이 값은 BUYMA 가 게시 후 변경을 허용하지 않는다("変更できません").
    따라서 등록 당시 보낸 값이 곧 BUYMA 가 아는 값이고, 우리 쪽에도 그대로 남아 있어야 한다(정체성).
  - 지금까지는 ace 가 원본이고 목록이 사본이라, 매 사이클 winner 의 ace 값으로 목록을 덮어썼다.
    소싱 몰이 바뀌면 목록 값이 바뀌는데 BUYMA 값은 안 바뀌므로 장부가 어긋난다.
  - 브랜드명으로 재생성하는 방식도 불가: 실측 결과 게시중 82,080건 중 12,769건(15%)이
    현재 규칙으로 재생성한 값과 다르다(예: 'A.P.C正規販売店' vs 'A.P.C.正規販売店').
    과거 규칙으로 만들어진 값이 섞여 있어, 재생성하면 BUYMA 와 어긋난 값이 된다.

하는 일:
  1) 목록에 값이 비어 있는 행을 winner ace 값으로 채운다(백필). 이미 값이 있으면 손대지 않는다.
  2) ace_products.buying_shop_name 을 백업 후 DROP.

전제: 코드에서 ace 쪽 읽기·쓰기를 먼저 제거했을 것(변환기 2, resolve_merge, ensure_group).

사용:
    python migrations/move_buying_shop_name_to_listings.py            # 미리보기
    python migrations/move_buying_shop_name_to_listings.py --execute  # 백필 + 백업 + DROP
"""
import os
import sys
import time
import argparse
from datetime import datetime

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
DB = os.getenv('DB_NAME')


def conn():
    return pymysql.connect(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
                           user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
                           database=DB, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)


BACKFILL_SELECT = """
    SELECT COUNT(*) n
      FROM buyma_listings bl
      JOIN source_offerings so ON so.id = bl.winner_offering_id
      JOIN ace_products a ON a.id = so.ace_product_id
     WHERE (bl.buying_shop_name IS NULL OR bl.buying_shop_name = '')
       AND a.buying_shop_name IS NOT NULL AND a.buying_shop_name <> ''
"""

BACKFILL_UPDATE = """
    UPDATE buyma_listings bl
      JOIN source_offerings so ON so.id = bl.winner_offering_id
      JOIN ace_products a ON a.id = so.ace_product_id
       SET bl.buying_shop_name = a.buying_shop_name, bl.updated_at = CURRENT_TIMESTAMP
     WHERE (bl.buying_shop_name IS NULL OR bl.buying_shop_name = '')
       AND a.buying_shop_name IS NOT NULL AND a.buying_shop_name <> ''
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()

    c = conn()
    cur = c.cursor()

    cur.execute("""SELECT is_published,
                          SUM(buying_shop_name IS NULL OR buying_shop_name='') 비었음,
                          COUNT(*) 계
                     FROM buyma_listings GROUP BY is_published""")
    print("현재 목록 상태:")
    for r in cur.fetchall():
        print(f"   게시중={r['is_published']}  비었음 {r['비었음']:,} / 전체 {r['계']:,}")

    cur.execute(BACKFILL_SELECT)
    n_fill = cur.fetchone()['n']
    print(f"\nwinner ace 값으로 채울 수 있는 행: {n_fill:,}건")

    cur.execute("""SELECT 1 FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='ace_products'
                      AND COLUMN_NAME='buying_shop_name'""", (DB,))
    has_col = bool(cur.fetchone())
    print(f"ace_products.buying_shop_name 존재: {has_col}")

    if not args.execute:
        print("\n(미리보기 — 아무것도 바꾸지 않음. 실제 반영은 --execute)")
        c.close()
        return

    t0 = time.time()
    cur.execute(BACKFILL_UPDATE)
    c.commit()
    print(f"\n백필 완료: {cur.rowcount:,}건 ({time.time()-t0:.1f}초)")

    if has_col:
        tag = datetime.now().strftime('%Y%m%d')
        bak = f"bak_ace_buying_shop_name_{tag}"
        t0 = time.time()
        cur.execute(f"DROP TABLE IF EXISTS {bak}")
        cur.execute(f"""CREATE TABLE {bak} AS
                        SELECT id, buying_shop_name FROM ace_products
                         WHERE buying_shop_name IS NOT NULL AND buying_shop_name <> ''""")
        c.commit()
        cur.execute(f"SELECT COUNT(*) n FROM {bak}")
        print(f"백업 {bak}: {cur.fetchone()['n']:,}행 ({time.time()-t0:.1f}초)")

        t0 = time.time()
        try:
            cur.execute("ALTER TABLE ace_products DROP COLUMN buying_shop_name, ALGORITHM=INSTANT")
            how = 'INSTANT'
        except Exception as e:
            print(f"  (INSTANT 불가 → 일반 방식: {str(e)[:80]})")
            cur.execute("ALTER TABLE ace_products DROP COLUMN buying_shop_name")
            how = '일반'
        c.commit()
        print(f"컬럼 삭제 ace_products.buying_shop_name [{how}] ({time.time()-t0:.1f}초)")

    cur.execute("""SELECT is_published,
                          SUM(buying_shop_name IS NULL OR buying_shop_name='') 비었음,
                          COUNT(*) 계
                     FROM buyma_listings GROUP BY is_published""")
    print("\n반영 후 목록 상태:")
    for r in cur.fetchall():
        print(f"   게시중={r['is_published']}  비었음 {r['비었음']:,} / 전체 {r['계']:,}")
    c.close()


if __name__ == '__main__':
    main()
