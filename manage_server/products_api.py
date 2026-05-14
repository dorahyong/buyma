# -*- coding: utf-8 -*-
"""
바이마 출품목록관리 API

SQL JOIN + GROUP BY로 model_id당 1행만 조회.
sources / images 는 팝업 클릭 시 별도 API로 lazy load.
"""

from datetime import datetime
from typing import Dict, List, Optional

import pymysql


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


# SQL JOIN으로 model_id당 1행 조회.
# - raw_scraped_data: GROUP BY model_id 집계 + 최신 대표행 JOIN
# - ace_products: model_no 기준 published 우선 1개
# - buyma_product_stats: buyma_product_id 기준 JOIN
# - ace_product_images: ace_product_id 기준 첫 번째 이미지
_MAIN_SQL = """
SELECT
    agg.model_id,
    rep.brand_name_en,
    rep.brand_name_kr,
    rep.product_name,
    rep.p_name_full,
    agg.source_updated_at,
    agg.oos_count,
    agg.total_source_count,
    a.id                                AS ace_id,
    a.name                              AS name_ja,
    a.buyma_product_id,
    a.is_published,
    a.is_active,
    a.is_ready_to_publish,
    a.is_lowest_price,
    a.buyma_lowest_price,
    a.buyma_lowest_price_checked_at,
    a.price,
    a.margin_amount_krw,
    a.margin_rate,
    a.available_until,
    a.buyma_registered_at,
    s.access_count,
    s.cart_count,
    s.favorite_count,
    s.access_7d,
    COALESCE(img.cloudflare_image_url, img.source_image_url) AS image_url
FROM (
    SELECT
        model_id,
        MAX(updated_at)                     AS source_updated_at,
        SUM(stock_status = 'out_of_stock')  AS oos_count,
        COUNT(*)                            AS total_source_count
    FROM raw_scraped_data
    WHERE model_id IS NOT NULL AND model_id != ''
    GROUP BY model_id
) agg
JOIN raw_scraped_data rep
    ON rep.model_id = agg.model_id
   AND rep.id = (
       SELECT id FROM raw_scraped_data r2
       WHERE r2.model_id = agg.model_id
       ORDER BY r2.updated_at DESC, r2.id DESC
       LIMIT 1
   )
LEFT JOIN ace_products a
    ON a.model_no = agg.model_id
   AND a.id = (
       SELECT id FROM ace_products a2
       WHERE a2.model_no = agg.model_id
       ORDER BY a2.is_published DESC, a2.id
       LIMIT 1
   )
LEFT JOIN buyma_product_stats s
    ON s.buyma_product_id = a.buyma_product_id
LEFT JOIN ace_product_images img
    ON img.id = (
       SELECT id FROM ace_product_images i2
       WHERE i2.ace_product_id = a.id
       ORDER BY i2.position
       LIMIT 1
   )
"""


def _determine_status(row: Dict, in_seller_listing: bool) -> str:
    if in_seller_listing:
        return 'on_sale'
    if row.get('ace_id'):
        if row.get('is_published') == 1 and row.get('is_active') == 1:
            return 'on_sale'
        if row.get('is_ready_to_publish') == 1 and row.get('is_published') == 0:
            return 'waiting'
        if row.get('is_lowest_price') == 0 and row.get('is_published') == 0:
            return 'no_lowest'
    oos = row.get('oos_count') or 0
    total = row.get('total_source_count') or 0
    if total > 0 and oos >= total:
        return 'sold_out'
    return 'unknown'


def _detect_db_mismatch(in_seller_listing: bool, row: Dict) -> Optional[str]:
    if not in_seller_listing:
        return None
    if not row.get('ace_id'):
        return 'ace_products 매칭 없음'
    if row.get('is_published') == 1 and row.get('is_active') == 1:
        return None
    flags = []
    if row.get('is_published') != 1:
        flags.append(f"is_published={row.get('is_published')}")
    if row.get('is_active') != 1:
        flags.append(f"is_active={row.get('is_active')}")
    return ', '.join(flags) or 'DB 상태 불일치'


def build_payload(db_config: Dict) -> Dict:
    """products.html이 기대하는 JSON 구조 생성. SQL JOIN으로 model_id당 1행."""
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as c:
            c.execute(_MAIN_SQL)
            rows = c.fetchall()
    finally:
        conn.close()

    items: List[Dict] = []
    for row in rows:
        bp_id = row.get('buyma_product_id')
        in_seller = row.get('access_count') is not None
        status = _determine_status(row, in_seller)
        db_mismatch = _detect_db_mismatch(in_seller, row)

        items.append({
            'model_id':                   row['model_id'],
            'buyma_product_id':           str(bp_id) if bp_id else None,
            'status':                     status,
            'db_mismatch_reason':         db_mismatch,
            'name_ja':                    row.get('name_ja'),
            'name_ko':                    row.get('product_name') or row.get('p_name_full'),
            'brand_name_en':              row.get('brand_name_en'),
            'brand_name_kr':              row.get('brand_name_kr'),
            'image_url':                  row.get('image_url'),
            'source_count':               int(row.get('total_source_count') or 0),
            'access_count':               row.get('access_count'),
            'cart_count':                 row.get('cart_count'),
            'favorite_count':             row.get('favorite_count'),
            'access_7d':                  row.get('access_7d'),
            'buyma_lowest_price':         row.get('buyma_lowest_price'),
            'available_lowest_price_jpy': row.get('price'),
            'price_yen':                  row.get('price'),
            'margin_amount_krw':          _to_float(row.get('margin_amount_krw')),
            'margin_rate':                _to_float(row.get('margin_rate')),
            'price_updated_at':           _iso(row.get('buyma_lowest_price_checked_at')),
            'source_updated_at':          _iso(row.get('source_updated_at')),
            'registered_at':              _fmt_dt(row.get('buyma_registered_at')),
            'expire_at':                  _fmt_dt(row.get('available_until'), '%Y/%m/%d'),
            # 미수집 시장 데이터 (별도 크롤러 예정)
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


def get_sources(db_config: Dict, model_id: str) -> List[Dict]:
    """sources 팝업용 — model_id의 모든 소싱처 정보 반환."""
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
                'site':           r.get('source_site'),
                'url':            url,
                'mall_product_id': r.get('mall_product_id'),
                'stock_status':   r.get('stock_status'),
                'price_krw':      _to_float(r.get('raw_price')),
            })
    return sources


def get_images(db_config: Dict, model_id: str) -> List[Dict]:
    """이미지 팝업용 — model_id의 모든 이미지 반환."""
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
        'url':        r.get('cloudflare_image_url') or r.get('source_image_url'),
        'source_url': r.get('source_image_url'),
        'position':   r.get('position'),
        'is_uploaded': bool(r.get('is_uploaded')),
    } for r in rows]
