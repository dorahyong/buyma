# -*- coding: utf-8 -*-
"""duple 소싱 연결 끊기 — 사전 백업 (조회 전용, 변경 없음).

대상: status='duple' AND is_active=0 인 ace 를 가리키는 활성 source_offerings
저장: migrations/duple_retire_backup_YYYYMMDD_HHMMSS.json
  - offerings : 끊을 연결의 현재값 (되돌리기용)
  - listings  : 영향받는 라이브 listing 의 현재 winner/가격 (되돌리기용)
"""
import os
import sys
import io
import json
from datetime import datetime
from decimal import Decimal

import pymysql
from dotenv import load_dotenv

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)


def _default(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(str(type(o)))


conn = pymysql.connect(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
                       user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
                       database=os.getenv('DB_NAME'), charset='utf8mb4',
                       cursorclass=pymysql.cursors.DictCursor, read_timeout=1800)
with conn.cursor() as cur:
    cur.execute("SET SESSION net_read_timeout=1800, net_write_timeout=1800")

with conn.cursor() as cur:
    print("① 끊을 소싱 연결 조회 중...")
    cur.execute("""
        SELECT so.id, so.listing_id, so.ace_product_id, so.source_site,
               so.purchase_price_krw, so.is_margin_ok, so.is_active,
               so.created_at, so.updated_at,
               a.status AS ace_status, a.is_active AS ace_is_active
        FROM source_offerings so
        JOIN ace_products a ON a.id = so.ace_product_id
        WHERE a.status = 'duple' AND a.is_active = 0 AND so.is_active = 1
    """)
    offerings = cur.fetchall()
    print(f"   {len(offerings):,}건")

    print("② 영향받는 라이브 listing 의 현재 상태 조회 중...")
    cur.execute("""
        SELECT DISTINCT bl.id, bl.buyma_product_id, bl.winner_offering_id,
               bl.price, bl.buyma_lowest_price, bl.buying_shop_name,
               bl.control, bl.status, bl.is_published, bl.updated_at
        FROM buyma_listings bl
        JOIN source_offerings so ON so.listing_id = bl.id AND so.is_active = 1
        JOIN ace_products a ON a.id = so.ace_product_id
        WHERE a.status = 'duple' AND a.is_active = 0
          AND bl.is_active = 1 AND bl.buyma_product_id IS NOT NULL
    """)
    listings = cur.fetchall()
    print(f"   {len(listings):,}건")

conn.close()

ts = datetime.now().strftime('%Y%m%d_%H%M%S')
path = os.path.join(BASE, 'migrations', f'duple_retire_backup_{ts}.json')
with open(path, 'w', encoding='utf-8') as f:
    json.dump({
        'created_at': ts,
        'note': "duple(is_active=0) ace 를 가리키는 활성 source_offerings 를 is_active=0 으로 끊기 전 스냅샷",
        'offering_count': len(offerings),
        'listing_count': len(listings),
        'offerings': offerings,
        'listings': listings,
    }, f, ensure_ascii=False, default=_default)

print(f"\n✅ 백업 저장: {path}")
print(f"   크기: {os.path.getsize(path):,} 바이트")
