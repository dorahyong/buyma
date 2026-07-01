# -*- coding: utf-8 -*-
"""
바이마 출품목록관리 API v2 — **buyma_listings(단일권위) 기준**.

레거시 products_api.py 는 raw_scraped_data.model_id + ace_products 기준(반쪽).
v2 는 실제 BUYMA 단위인 buyma_listings 를 행으로 하고, merge 결과 테이블을 조인:
  - 정체성/게시상태 : buyma_listings (is_published/buyma_product_id/control)
  - 몰/winner/마진   : source_offerings (+ winner 의 ace margin)
  - 신호(찜/조회/…)  : buyma_product_stats (buyma_product_id)
  - 게시일수         : v_listing_days (buyma_product_id)
  - 점수             : score_index_listed (listing_id 직접)
  - 이미지           : listing_images (listing_id)

products.html 이 기대하는 item 스키마와 동일한 키를 낸다(프런트 복사 재사용 위해).
"""
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

import pymysql


def _iso(v) -> Optional[str]:
    if v is None: return None
    return v.isoformat(timespec='seconds') if hasattr(v, 'hour') else (v.isoformat() if hasattr(v, 'isoformat') else str(v))

def _fmt_dt(v, fmt='%Y/%m/%d %H:%M') -> Optional[str]:
    if v is None: return None
    return v.strftime(fmt) if hasattr(v, 'strftime') else str(v)

def _to_float(v) -> Optional[float]:
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _status_of(l: Dict) -> str:
    """listing 상태 → products.html STATUS_META 코드."""
    if l.get('is_published') == 1:
        return 'on_sale'
    # 미게시: winner 있으면 대기, 없으면 확인필요
    if l.get('winner_offering_id') is not None:
        return 'waiting'
    return 'unknown'


def build_payload(db_config: Dict) -> Dict:
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    try:
        cur = conn.cursor()

        # 1. listings (활성)
        cur.execute("""
            SELECT id, buyma_product_id, name, brand_name, category_id, model_no,
                   is_published, control, status, price, buyma_lowest_price, is_lowest_price,
                   winner_offering_id, buyma_registered_at, available_until
            FROM buyma_listings WHERE is_active=1
        """)
        listings = cur.fetchall()

        # 2. offerings (listing별 몰/ace/매입가)
        cur.execute("""SELECT id, listing_id, source_site, ace_product_id, purchase_price_krw
                       FROM source_offerings WHERE is_active=1""")
        off_by_listing = defaultdict(list); off_by_id = {}
        ace_ids = set()
        for r in cur.fetchall():
            off_by_listing[r['listing_id']].append(r); off_by_id[r['id']] = r
            if r['ace_product_id']: ace_ids.add(r['ace_product_id'])

        # 3. ace margin/이름 (offering이 참조하는 ace)
        ace_info = {}
        ids = list(ace_ids)
        for i in range(0, len(ids), 2000):
            ch = ids[i:i+2000]; ph = ','.join(['%s']*len(ch))
            cur.execute(f"SELECT id, margin_amount_krw, margin_rate, name FROM ace_products WHERE id IN ({ph})", ch)
            for r in cur.fetchall(): ace_info[r['id']] = r

        # 4. stats
        cur.execute("""SELECT buyma_product_id, access_count, cart_count, favorite_count,
                              access_7d, sold_count, sales_amount_jpy FROM buyma_product_stats""")
        stats_by_pid = {str(r['buyma_product_id']): r for r in cur.fetchall()}

        # 5. 게시일수
        cur.execute("SELECT buyma_product_id, total_listed_days FROM v_listing_days")
        days_by_pid = {str(r['buyma_product_id']): _to_float(r['total_listed_days']) for r in cur.fetchall()}

        # 6. 점수 (listing_id 직접)
        cur.execute("SELECT listing_id, score FROM score_index_listed")
        score_by_lid = {r['listing_id']: _to_float(r['score']) for r in cur.fetchall()}

        # 7. 첫 이미지 (listing별 position 최소)
        cur.execute("""SELECT li.listing_id, li.cloudflare_image_url, li.source_image_url
                       FROM listing_images li
                       INNER JOIN (SELECT listing_id, MIN(position) mp FROM listing_images GROUP BY listing_id) t
                         ON li.listing_id=t.listing_id AND li.position=t.mp""")
        img_by_lid = {r['listing_id']: r for r in cur.fetchall()}
    finally:
        conn.close()

    items: List[Dict] = []
    for l in listings:
        lid = l['id']; bp_id = l['buyma_product_id']
        offs = off_by_listing.get(lid, [])
        winner = off_by_id.get(l['winner_offering_id']) if l['winner_offering_id'] else None
        wace = ace_info.get(winner['ace_product_id']) if winner else None
        bstats = stats_by_pid.get(str(bp_id)) if bp_id else None
        img = img_by_lid.get(lid)

        items.append({
            'listing_id':        lid,
            'model_id':          l.get('model_no'),
            'buyma_product_id':  str(bp_id) if bp_id else None,
            'status':            _status_of(l),
            'db_mismatch_reason': None,
            'name_ja':           l.get('name'),
            'name_ko':           None,   # TODO: winner ace→raw 연결(추후)
            'brand_name_en':     l.get('brand_name'),
            'malls':             sorted({o['source_site'] for o in offs}),
            'category_path':     None,   # listings엔 경로 없음(category_id만) — 추후 매핑
            'image_url':         (img.get('cloudflare_image_url') or img.get('source_image_url')) if img else None,
            'source_count':      len(offs),
            'access_count':      bstats.get('access_count') if bstats else None,
            'cart_count':        bstats.get('cart_count') if bstats else None,
            'favorite_count':    bstats.get('favorite_count') if bstats else None,
            'access_7d':         bstats.get('access_7d') if bstats else None,
            'sold_count':        bstats.get('sold_count') if bstats else None,
            'sales_amount_jpy':  int(bstats['sales_amount_jpy']) if bstats and bstats.get('sales_amount_jpy') is not None else None,
            'buyma_lowest_price': l.get('buyma_lowest_price'),
            'available_lowest_price_jpy': None,  # 추후(offering breakeven)
            'price_yen':         l.get('price'),
            'margin_amount_krw': _to_float(wace.get('margin_amount_krw')) if wace else None,
            'margin_rate':       _to_float(wace.get('margin_rate')) if wace else None,
            'expected_margin_krw': _to_float(wace.get('margin_amount_krw')) if wace else None,
            'expected_margin_rate': _to_float(wace.get('margin_rate')) if wace else None,
            'price_updated_at':  None,
            'source_updated_at': None,
            'registered_at':     _fmt_dt(l.get('buyma_registered_at')),
            'listed_days':       days_by_pid.get(str(bp_id)) if bp_id else None,
            'expire_at':         _fmt_dt(l.get('available_until'), '%Y/%m/%d'),
            'expected_daily_margin': score_by_lid.get(lid),
            'same_count': None, 'rank_position': None, 'our_ranks': None,
            'top1_link': None, 'top1_is_ours': None, 'top1_seller_name': None,
            'top1_seller_id': None, 'top1_price': None, 'top1_name': None,
        })

    return {'collected_at': datetime.now().isoformat(timespec='seconds'),
            'count': len(items), 'items': items}


if __name__ == '__main__':
    import os, sys, io, json
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
    cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT',3306)), user=os.getenv('DB_USER'),
               password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4')
    pl = build_payload(cfg)
    print("count:", pl['count'])
    from collections import Counter
    print("status 분포:", dict(Counter(i['status'] for i in pl['items'])))
    print("\n출품중 샘플 3건:")
    for i in [x for x in pl['items'] if x['status']=='on_sale'][:3]:
        print(f"  #{i['listing_id']} {i['brand_name_en']} | {str(i['name_ja'])[:25]} | 몰{i['malls']} 찜{i['favorite_count']}/조회{i['access_count']}/판{i['sold_count']} | 마진{i['margin_amount_krw']} 게시일수{i['listed_days']} 점수{i['expected_daily_margin']}")
