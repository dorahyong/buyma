# -*- coding: utf-8 -*-
"""duple(is_active=0) ace 및 딸린 데이터 삭제.

배경: 옛 dedup_corrector 가 끈 ace 가 merge 소싱처로 계속 쓰이는데 STOCK 대상에서는
      빠져 매입가·재고가 냉동됨 → winner=최저가 규칙 때문에 냉동 가격이 계속 이겨
      실제로 살 수 없는 소싱처가 winner 가 됨 (주문 34975478 취소 사례).
      → 해당 ace 를 지우면 다음 CONVERT 가 raw 로부터 is_active=1 로 재생성한다.

단계:
  --backup   백업 테이블 생성 + 건수 검증 (비파괴)
  --delete   실제 삭제 (백업 테이블 존재 확인 후)

raw_scraped_data 는 건드리지 않는다(수집 원본 보존 + 재생성 경로).
"""
import os
import sys
import io
import argparse
from datetime import datetime

import pymysql
from dotenv import load_dotenv

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)

TAG = '20260722'
SEL_ACE = "SELECT id FROM ace_products WHERE status='duple' AND is_active=0"

# (백업테이블, 원본테이블, ace 를 가리키는 컬럼)
CHILDREN = [
    (f'bak_duple_offopts_{TAG}', 'source_offering_options', None),   # offering 경유 (특수)
    (f'bak_duple_offerings_{TAG}', 'source_offerings', 'ace_product_id'),
    (f'bak_duple_images_{TAG}', 'ace_product_images', 'ace_product_id'),
    (f'bak_duple_options_{TAG}', 'ace_product_options', 'ace_product_id'),
    (f'bak_duple_variants_{TAG}', 'ace_product_variants', 'ace_product_id'),
    (f'bak_duple_thumbs_{TAG}', 'ace_product_thumbnails', 'ace_product_id'),
    (f'bak_duple_apilogs_{TAG}', 'ace_product_api_logs', 'ace_product_id'),
]
BAK_ACE = f'bak_duple_ace_{TAG}'

ap = argparse.ArgumentParser()
ap.add_argument('--backup', action='store_true')
ap.add_argument('--delete', action='store_true')
args = ap.parse_args()

conn = pymysql.connect(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
                       user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
                       database=os.getenv('DB_NAME'), charset='utf8mb4',
                       cursorclass=pymysql.cursors.DictCursor, read_timeout=3600)
with conn.cursor() as cur:
    cur.execute("SET SESSION net_read_timeout=3600, net_write_timeout=3600")


def scalar(sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return list(row.values())[0]


def table_exists(name):
    return scalar("SELECT COUNT(*) FROM information_schema.TABLES "
                  "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (name,)) > 0


def child_where(orig, col):
    if orig == 'source_offering_options':
        return (f"offering_id IN (SELECT id FROM source_offerings "
                f"WHERE ace_product_id IN ({SEL_ACE}))")
    return f"{col} IN ({SEL_ACE})"


if args.backup:
    print("=" * 70)
    print("백업 테이블 생성")
    print("=" * 70)
    n_ace = scalar(f"SELECT COUNT(*) FROM ({SEL_ACE}) t")
    print(f"  대상 ace: {n_ace:,}건\n")

    with conn.cursor() as cur:
        if table_exists(BAK_ACE):
            print(f"  {BAK_ACE:<34} 이미 존재 → 건너뜀")
        else:
            cur.execute(f"CREATE TABLE {BAK_ACE} AS "
                        f"SELECT * FROM ace_products WHERE status='duple' AND is_active=0")
            conn.commit()
            print(f"  {BAK_ACE:<34} {scalar(f'SELECT COUNT(*) FROM {BAK_ACE}'):>9,}행")

        for bak, orig, col in CHILDREN:
            if table_exists(bak):
                print(f"  {bak:<34} 이미 존재 → 건너뜀")
                continue
            cur.execute(f"CREATE TABLE {bak} AS SELECT * FROM {orig} WHERE {child_where(orig, col)}")
            conn.commit()
            print(f"  {bak:<34} {scalar(f'SELECT COUNT(*) FROM {bak}'):>9,}행")

    print("\n검증 — 백업 건수 vs 원본 건수")
    ok = True
    a1 = scalar(f"SELECT COUNT(*) FROM {BAK_ACE}")
    a2 = scalar(f"SELECT COUNT(*) FROM ({SEL_ACE}) t")
    print(f"  ace_products                {a1:>9,} / {a2:>9,}  {'OK' if a1 == a2 else '불일치'}")
    ok &= (a1 == a2)
    for bak, orig, col in CHILDREN:
        b = scalar(f"SELECT COUNT(*) FROM {bak}")
        o = scalar(f"SELECT COUNT(*) FROM {orig} WHERE {child_where(orig, col)}")
        s = 'OK' if b == o else '불일치'
        print(f"  {orig:<28}{b:>9,} / {o:>9,}  {s}")
        ok &= (b == o)
    print("\n" + ("✅ 백업 완료. --delete 로 삭제 가능" if ok else "⛔ 불일치 — 삭제 금지"))

elif args.delete:
    print("=" * 70)
    print("삭제 실행")
    print("=" * 70)
    missing = [t for t in [BAK_ACE] + [b for b, _, _ in CHILDREN] if not table_exists(t)]
    if missing:
        print(f"⛔ 백업 테이블 없음: {missing} → 중단")
        sys.exit(1)

    n_ace = scalar(f"SELECT COUNT(*) FROM ({SEL_ACE}) t")
    print(f"  대상 ace {n_ace:,}건 — 자식부터 삭제\n")
    with conn.cursor() as cur:
        for bak, orig, col in CHILDREN:
            cur.execute(f"DELETE FROM {orig} WHERE {child_where(orig, col)}")
            print(f"  {orig:<30} {cur.rowcount:>9,}행 삭제")
            conn.commit()
        cur.execute("DELETE FROM ace_products WHERE status='duple' AND is_active=0")
        print(f"  {'ace_products':<30} {cur.rowcount:>9,}행 삭제")
        conn.commit()

    print("\n검증 — 남은 것")
    print(f"  duple(is_active=0) 잔여     : {scalar(f'SELECT COUNT(*) FROM ({SEL_ACE}) t'):,}")
    print(f"  고아 source_offerings       : "
          f"{scalar('SELECT COUNT(*) FROM source_offerings so LEFT JOIN ace_products a ON a.id=so.ace_product_id WHERE a.id IS NULL'):,}")
    print("\n✅ 삭제 완료")
else:
    print("--backup 또는 --delete 를 지정하세요")

conn.close()
