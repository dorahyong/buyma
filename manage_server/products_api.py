# -*- coding: utf-8 -*-
"""
바이마 출품목록관리 화면(products.html)이 fetch로 받아 가는 JSON 페이로드 생성.

데이터 단위: model_id (품번) 1개 = 화면 1행
  - 같은 품번이 여러 소싱처에 있으면 sources[]에 모임
  - ace_products는 model_no로 매칭
  - 바이마 셀러 통계는 buyma_product_stats 테이블에서 buyma_product_id로 매칭

상태 판정 (build_merged_dataset.py와 동일 규칙):
  on_sale       : 바이마 셀러페이지 노출 중 OR ace.is_published=1 AND is_active=1
  waiting       : ace.is_ready_to_publish=1 AND ace.is_published=0
  no_lowest     : ace.is_lowest_price=0 AND ace.is_published=0
  sold_out      : 모든 raw.stock_status='out_of_stock'
  unknown       : 위 어디에도 안 걸림
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pymysql

# 머지된 결과를 보관할 캐시 파일. 크롤러가 끝나면 자동 갱신.
# API는 이 파일을 그대로 응답 → 매번 5만 행 머지하지 않음.
CACHE_PATH = Path(__file__).resolve().parent / 'data_cache.json'

STATUS_PRIORITY = ['on_sale', 'waiting', 'no_lowest', 'sold_out', 'unknown']


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


def _fetch_raw_scraped(conn) -> List[Dict]:
    sql = """
        SELECT id, source_site, mall_product_id, brand_name_en, brand_name_kr,
               product_name, p_name_full, model_id, raw_price, stock_status,
               product_url, updated_at
        FROM raw_scraped_data
        WHERE model_id IS NOT NULL AND model_id != ''
    """
    with conn.cursor() as c:
        c.execute(sql)
        return c.fetchall()


def _fetch_ace_products(conn, model_ids: List[str]) -> List[Dict]:
    if not model_ids:
        return []
    out: List[Dict] = []
    for chunk in _chunked(list({m for m in model_ids if m}), 1000):
        placeholders = ','.join(['%s'] * len(chunk))
        sql = f"""
            SELECT id, raw_data_id, model_no, name, buyma_product_id, status,
                   control, is_active, is_published, is_ready_to_publish,
                   is_lowest_price, buyma_lowest_price, buyma_lowest_price_checked_at,
                   price, margin_amount_krw, margin_rate, available_until,
                   buyma_registered_at, source_product_url
            FROM ace_products
            WHERE model_no IN ({placeholders})
        """
        with conn.cursor() as c:
            c.execute(sql, list(chunk))
            out.extend(c.fetchall())
    return out


def _fetch_ace_images(conn, ace_product_ids: List[int]) -> List[Dict]:
    if not ace_product_ids:
        return []
    out: List[Dict] = []
    for chunk in _chunked(ace_product_ids, 1000):
        placeholders = ','.join(['%s'] * len(chunk))
        sql = f"""
            SELECT ace_product_id, position, source_image_url,
                   cloudflare_image_url, is_uploaded
            FROM ace_product_images
            WHERE ace_product_id IN ({placeholders})
            ORDER BY ace_product_id, position
        """
        with conn.cursor() as c:
            c.execute(sql, list(chunk))
            out.extend(c.fetchall())
    return out


def _fetch_buyma_stats(conn) -> Dict[str, Dict]:
    """buyma_product_id → 통계 dict"""
    sql = """
        SELECT buyma_product_id, ace_product_id,
               access_count, cart_count, favorite_count, access_7d,
               stats_collected_at, weekly_collected_at
        FROM buyma_product_stats
    """
    out: Dict[str, Dict] = {}
    with conn.cursor() as c:
        c.execute(sql)
        for r in c.fetchall():
            pid = str(r['buyma_product_id'])
            out[pid] = {
                'access_count': r.get('access_count'),
                'cart_count': r.get('cart_count'),
                'favorite_count': r.get('favorite_count'),
                'access_7d': r.get('access_7d'),
                'stats_collected_at': _iso(r.get('stats_collected_at')),
            }
    return out


def _determine_status(ace_rows: List[Dict], raw_rows: List[Dict],
                      in_seller_listing: bool = False) -> str:
    if in_seller_listing:
        return 'on_sale'
    if ace_rows:
        if any(a.get('is_published') == 1 and a.get('is_active') == 1 for a in ace_rows):
            return 'on_sale'
        if any(a.get('is_ready_to_publish') == 1 and a.get('is_published') == 0 for a in ace_rows):
            return 'waiting'
        if any(a.get('is_lowest_price') == 0 and a.get('is_published') == 0 for a in ace_rows):
            return 'no_lowest'
    if raw_rows and all((r.get('stock_status') or '').lower() == 'out_of_stock' for r in raw_rows):
        return 'sold_out'
    return 'unknown'


def _detect_db_mismatch(in_seller_listing: bool, ace_rows: List[Dict]) -> Optional[str]:
    if not in_seller_listing:
        return None
    if not ace_rows:
        return 'ace_products 매칭 없음'
    if any(a.get('is_published') == 1 and a.get('is_active') == 1 for a in ace_rows):
        return None
    flags = set()
    for a in ace_rows:
        if a.get('is_published') != 1:
            flags.add(f"is_published={a.get('is_published')}")
        if a.get('is_active') != 1:
            flags.add(f"is_active={a.get('is_active')}")
    return ', '.join(sorted(flags)) or 'DB 상태 불일치'


def build_payload(db_config: Dict) -> Dict:
    """products.html이 기대하는 JSON 구조 생성."""
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    try:
        raw_rows = _fetch_raw_scraped(conn)
        by_model: Dict[str, List[Dict]] = defaultdict(list)
        for r in raw_rows:
            by_model[r['model_id']].append(r)

        ace_rows = _fetch_ace_products(conn, list(by_model.keys()))
        ace_by_model: Dict[str, List[Dict]] = defaultdict(list)
        for a in ace_rows:
            ace_by_model[a['model_no']].append(a)

        ace_ids = [a['id'] for a in ace_rows]
        img_rows = _fetch_ace_images(conn, ace_ids)
        img_by_ace: Dict[int, List[Dict]] = defaultdict(list)
        for img in img_rows:
            img_by_ace[img['ace_product_id']].append(img)

        buyma_stats_by_pid = _fetch_buyma_stats(conn)
    finally:
        conn.close()

    items: List[Dict] = []
    for model_id, raw_list in by_model.items():
        ace_list = ace_by_model.get(model_id, [])

        raw0 = max(raw_list, key=lambda r: r.get('updated_at') or datetime.min)
        ace0 = None
        if ace_list:
            published = [a for a in ace_list if a.get('is_published') == 1]
            ace0 = published[0] if published else ace_list[0]

        bp_id = ace0.get('buyma_product_id') if ace0 else None
        bstats = buyma_stats_by_pid.get(str(bp_id)) if bp_id else None
        in_seller = bstats is not None

        status = _determine_status(ace_list, raw_list, in_seller_listing=in_seller)
        db_mismatch_reason = _detect_db_mismatch(in_seller, ace_list)

        sources: List[Dict] = []
        seen_urls = set()
        for r in raw_list:
            url = r.get('product_url')
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({
                    'site': r.get('source_site'),
                    'url': url,
                    'mall_product_id': r.get('mall_product_id'),
                    'stock_status': r.get('stock_status'),
                    'price_krw': _to_float(r.get('raw_price')),
                })

        all_images: List[Dict] = []
        if ace0:
            for img in img_by_ace.get(ace0['id'], []):
                all_images.append({
                    'url': img.get('cloudflare_image_url') or img.get('source_image_url'),
                    'source_url': img.get('source_image_url'),
                    'position': img.get('position'),
                    'is_uploaded': bool(img.get('is_uploaded')),
                })
        rep_image = all_images[0]['url'] if all_images else None

        items.append({
            'model_id': model_id,
            'buyma_product_id': str(bp_id) if bp_id else None,
            'status': status,
            'db_mismatch_reason': db_mismatch_reason,
            'name_ja': ace0.get('name') if ace0 else None,
            'name_ko': raw0.get('product_name') or raw0.get('p_name_full'),
            'brand_name_en': raw0.get('brand_name_en'),
            'brand_name_kr': raw0.get('brand_name_kr'),
            'image_url': rep_image,
            'all_images': all_images,
            'access_count': bstats.get('access_count') if bstats else None,
            'cart_count': bstats.get('cart_count') if bstats else None,
            'favorite_count': bstats.get('favorite_count') if bstats else None,
            'access_7d': bstats.get('access_7d') if bstats else None,
            'buyma_lowest_price': ace0.get('buyma_lowest_price') if ace0 else None,
            'available_lowest_price_jpy': ace0.get('price') if ace0 else None,
            'price_yen': ace0.get('price') if ace0 else None,
            'margin_amount_krw': _to_float(ace0.get('margin_amount_krw')) if ace0 else None,
            'margin_rate': _to_float(ace0.get('margin_rate')) if ace0 else None,
            'price_updated_at': _iso(ace0.get('buyma_lowest_price_checked_at')) if ace0 else None,
            'source_updated_at': _iso(raw0.get('updated_at')),
            'registered_at': _fmt_dt(ace0.get('buyma_registered_at')) if ace0 else None,
            'expire_at': _fmt_dt(ace0.get('available_until'), '%Y/%m/%d') if ace0 else None,
            # 시장 데이터 (별도 크롤러가 채우게 될 자리; 지금은 비워둠)
            'same_count': None,
            'rank_position': None,
            'our_ranks': None,
            'top1_link': None,
            'top1_is_ours': None,
            'top1_seller_name': None,
            'top1_seller_id': None,
            'top1_price': None,
            'top1_name': None,
            'sources': sources,
        })

    return {
        'collected_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(items),
        'items': items,
    }


def build_and_save_cache(db_config: Dict) -> Dict:
    """DB → 머지 → 캐시 파일에 저장. 호출 측에 payload도 반환."""
    payload = build_payload(db_config)
    tmp_path = CACHE_PATH.with_suffix('.tmp.json')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, default=str)
    tmp_path.replace(CACHE_PATH)   # 원자적 교체 (반쯤 쓴 파일 응답하지 않도록)
    return payload
