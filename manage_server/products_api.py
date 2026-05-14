# -*- coding: utf-8 -*-
"""
바이마 출품목록관리 API

4개의 배치 쿼리로 처리:
  1. raw_scraped_data GROUP BY model_id  → N행 (150k 전체 대신)
  2. ace_products        IN (model_ids)
  3. ace_product_images  IN (ace_ids), 첫 번째 이미지만
  4. buyma_product_stats IN (buyma_product_ids)

sources / images 팝업은 lazy load (별도 API).
"""

from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional

import pymysql


# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────

def _chunked(seq: List, size: int) -> Iterable[List]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    if hasattr(v, 'isoformat'):
        return v.isoformat(timespec='seconds') if hasattr(v, 'hour') else v.isoformat()
    return str(v)


def _fmt_dt(v, fmt='%Y/%m/%d %H:%M') -> Optional[str]:
    if v is None:
        return None
    if hasattr(v, 'strftime'):
        return v.strftime(fmt)
    return str(v)


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────
# 쿼리 함수들
# ─────────────────────────────────────────────

def _fetch_raw_aggregated(conn) -> List[Dict]:
    """model_id별 집계: 150k 행 대신 N행만 반환."""
    sql = """
        SELECT
            model_id,
            MAX(brand_name_en)  AS brand_name_en,
            MAX(brand_name_kr)  AS brand_name_kr,
            MAX(product_name)   AS product_name,
            MAX(p_name_full)    AS p_name_full,
            MAX(updated_at)     AS source_updated_at,
            SUM(stock_status = 'out_of_stock') AS oos_count,
            COUNT(*)            AS total_source_count
        FROM raw_scraped_data
        WHERE model_id IS NOT NULL AND model_id != ''
        GROUP BY model_id
    """
    with conn.cursor() as c:
        c.execute(sql)
        return c.fetchall()


def _fetch_ace_products(conn) -> List[Dict]:
    """ace_products 풀스캔 — IN-chunk 누적보다 6배 빠름 (인덱스 풀스캔)."""
    sql = """
        SELECT id, model_no, name, buyma_product_id,
               is_active, is_published, is_ready_to_publish, is_lowest_price,
               buyma_lowest_price, buyma_lowest_price_checked_at,
               price, margin_amount_krw, margin_rate,
               available_until, buyma_registered_at
        FROM ace_products
        WHERE model_no IS NOT NULL AND model_no != ''
    """
    with conn.cursor() as c:
        c.execute(sql)
        return c.fetchall()


def _fetch_first_images(conn) -> Dict[int, Dict]:
    """ace_product_id → 첫 번째 이미지 (position 최소). 풀스캔 GROUP BY."""
    sql = """
        SELECT img.ace_product_id,
               img.cloudflare_image_url,
               img.source_image_url
        FROM ace_product_images img
        INNER JOIN (
            SELECT ace_product_id, MIN(position) AS min_pos
            FROM ace_product_images
            GROUP BY ace_product_id
        ) t ON img.ace_product_id = t.ace_product_id
           AND img.position = t.min_pos
    """
    out: Dict[int, Dict] = {}
    with conn.cursor() as c:
        c.execute(sql)
        for r in c.fetchall():
            out[r['ace_product_id']] = r
    return out


def _fetch_buyma_stats(conn) -> Dict[str, Dict]:
    """buyma_product_id → 통계 dict. 풀스캔."""
    sql = """
        SELECT buyma_product_id, access_count, cart_count,
               favorite_count, access_7d
        FROM buyma_product_stats
    """
    out: Dict[str, Dict] = {}
    with conn.cursor() as c:
        c.execute(sql)
        for r in c.fetchall():
            out[str(r['buyma_product_id'])] = r
    return out


# ─────────────────────────────────────────────
# 상태 판정
# ─────────────────────────────────────────────

def _determine_status(raw_agg: Dict, ace_list: List[Dict],
                      in_seller_listing: bool) -> str:
    if in_seller_listing:
        return 'on_sale'
    if ace_list:
        if any(a.get('is_published') == 1 and a.get('is_active') == 1
               for a in ace_list):
            return 'on_sale'
        if any(a.get('is_ready_to_publish') == 1 and a.get('is_published') == 0
               for a in ace_list):
            return 'waiting'
        if any(a.get('is_lowest_price') == 0 and a.get('is_published') == 0
               for a in ace_list):
            return 'no_lowest'
    oos = raw_agg.get('oos_count') or 0
    total = raw_agg.get('total_source_count') or 0
    if total > 0 and oos >= total:
        return 'sold_out'
    return 'unknown'


def _detect_db_mismatch(in_seller_listing: bool,
                        ace_list: List[Dict]) -> Optional[str]:
    if not in_seller_listing:
        return None
    if not ace_list:
        return 'ace_products 매칭 없음'
    if any(a.get('is_published') == 1 and a.get('is_active') == 1
           for a in ace_list):
        return None
    flags = set()
    for a in ace_list:
        if a.get('is_published') != 1:
            flags.add(f"is_published={a.get('is_published')}")
        if a.get('is_active') != 1:
            flags.add(f"is_active={a.get('is_active')}")
    return ', '.join(sorted(flags)) or 'DB 상태 불일치'


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def build_payload(db_config: Dict) -> Dict:
    """products.html이 기대하는 JSON 구조 생성."""
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    try:
        # 1. raw 집계 (GROUP BY → N행)
        raw_agg_rows = _fetch_raw_aggregated(conn)
        raw_by_model = {r['model_id']: r for r in raw_agg_rows}

        if not raw_by_model:
            return {'collected_at': datetime.now().isoformat(timespec='seconds'),
                    'count': 0, 'items': []}

        # 2. ace_products (풀스캔)
        ace_rows = _fetch_ace_products(conn)
        ace_by_model: Dict[str, List[Dict]] = defaultdict(list)
        for a in ace_rows:
            ace_by_model[a['model_no']].append(a)

        # 3. 첫 번째 이미지 (풀스캔)
        img_by_ace = _fetch_first_images(conn)

        # 4. buyma_stats (풀스캔)
        stats_by_pid = _fetch_buyma_stats(conn)
    finally:
        conn.close()

    items: List[Dict] = []
    for model_id, raw in raw_by_model.items():
        ace_list = ace_by_model.get(model_id, [])

        ace0: Optional[Dict] = None
        if ace_list:
            published = [a for a in ace_list if a.get('is_published') == 1]
            ace0 = published[0] if published else ace_list[0]

        bp_id = ace0.get('buyma_product_id') if ace0 else None
        bstats = stats_by_pid.get(str(bp_id)) if bp_id else None
        in_seller = bstats is not None

        status = _determine_status(raw, ace_list, in_seller)
        db_mismatch = _detect_db_mismatch(in_seller, ace_list)

        img = img_by_ace.get(ace0['id']) if ace0 else None
        image_url = (img.get('cloudflare_image_url') or
                     img.get('source_image_url')) if img else None

        items.append({
            'model_id':                   model_id,
            'buyma_product_id':           str(bp_id) if bp_id else None,
            'status':                     status,
            'db_mismatch_reason':         db_mismatch,
            'name_ja':                    ace0.get('name') if ace0 else None,
            'name_ko':                    raw.get('product_name') or raw.get('p_name_full'),
            'brand_name_en':              raw.get('brand_name_en'),
            'brand_name_kr':              raw.get('brand_name_kr'),
            'image_url':                  image_url,
            'source_count':               int(raw.get('total_source_count') or 0),
            'access_count':               bstats.get('access_count') if bstats else None,
            'cart_count':                 bstats.get('cart_count') if bstats else None,
            'favorite_count':             bstats.get('favorite_count') if bstats else None,
            'access_7d':                  bstats.get('access_7d') if bstats else None,
            'buyma_lowest_price':         ace0.get('buyma_lowest_price') if ace0 else None,
            'available_lowest_price_jpy': ace0.get('price') if ace0 else None,
            'price_yen':                  ace0.get('price') if ace0 else None,
            'margin_amount_krw':          _to_float(ace0.get('margin_amount_krw')) if ace0 else None,
            'margin_rate':                _to_float(ace0.get('margin_rate')) if ace0 else None,
            'price_updated_at':           _iso(ace0.get('buyma_lowest_price_checked_at')) if ace0 else None,
            'source_updated_at':          _iso(raw.get('source_updated_at')),
            'registered_at':              _fmt_dt(ace0.get('buyma_registered_at')) if ace0 else None,
            'expire_at':                  _fmt_dt(ace0.get('available_until'), '%Y/%m/%d') if ace0 else None,
            'same_count':       None,
            'rank_position':    None,
            'our_ranks':        None,
            'top1_link':        None,
            'top1_is_ours':     None,
            'top1_seller_name': None,
            'top1_seller_id':   None,
            'top1_price':       None,
            'top1_name':        None,
        })

    return {
        'collected_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(items),
        'items': items,
    }


# ─────────────────────────────────────────────
# Lazy load용 (팝업 클릭 시)
# ─────────────────────────────────────────────

def get_sources(db_config: Dict, model_id: str) -> List[Dict]:
    sql = """
        SELECT source_site, product_url, mall_product_id, stock_status, raw_price
        FROM raw_scraped_data
        WHERE model_id = %s
    """
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as c:
            c.execute(sql, (model_id,))
            rows = c.fetchall()
    finally:
        conn.close()

    seen: set = set()
    sources: List[Dict] = []
    for r in rows:
        url = r.get('product_url')
        if url and url not in seen:
            seen.add(url)
            sources.append({
                'site':            r.get('source_site'),
                'url':             url,
                'mall_product_id': r.get('mall_product_id'),
                'stock_status':    r.get('stock_status'),
                'price_krw':       _to_float(r.get('raw_price')),
            })
    return sources


def get_images(db_config: Dict, model_id: str) -> List[Dict]:
    sql = """
        SELECT img.cloudflare_image_url, img.source_image_url,
               img.position, img.is_uploaded
        FROM ace_product_images img
        JOIN ace_products a ON img.ace_product_id = a.id
        WHERE a.model_no = %s
        ORDER BY a.is_published DESC, a.id, img.position
    """
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as c:
            c.execute(sql, (model_id,))
            rows = c.fetchall()
    finally:
        conn.close()

    return [{
        'url':         r.get('cloudflare_image_url') or r.get('source_image_url'),
        'source_url':  r.get('source_image_url'),
        'position':    r.get('position'),
        'is_uploaded': bool(r.get('is_uploaded')),
    } for r in rows]
