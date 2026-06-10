# -*- coding: utf-8 -*-
"""
DB(raw_scraped_data + ace_products + ace_product_images) + 바이마 셀러 크롤 결과를
머지해서 화면(buyma_stats_view.html)이 읽는 merged_latest.js / .json 을 생성.

데이터 단위: model_id (품번) 1개 = 화면 1행
  - 같은 품번이 여러 소싱처에 있으면 sources[]에 모임
  - ace_products는 model_no 로 매칭
  - 바이마 셀러 통계(buyma_self_stats_latest.json)는 buyma_product_id 로 매칭

상태 판정 (현재 시안 — 실제 비즈니스룰과 다르면 사용자가 점검 후 수정):
  on_sale       (출품중)        : ace.is_published=1 AND ace.is_active=1
  waiting       (출품대기중)    : ace.is_ready_to_publish=1 AND ace.is_published=0
  no_lowest     (최저가확보불가): is_published=0 AND 출품가능최저가(마진0 가격) >= 바이마최저가
                                  (경쟁자 없음/매입가 없음은 제외 → unknown)
  sold_out      (품절)          : 모든 raw.stock_status='out_of_stock'
  unknown                       : 위 어디에도 안 걸림

사용법:
    python3 build_merged_dataset.py                # 전체
    python3 build_merged_dataset.py --limit 500    # 테스트용 (raw 500행만)
"""

import os
import sys
import json
import math
import argparse
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional, Iterable

import pymysql
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(os.path.dirname(SCRIPT_DIR), '.env'))

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}

BUYMA_STATS_LATEST = os.path.join(SCRIPT_DIR, 'buyma_self_stats_latest.json')
SEVEN_DAYS_LATEST = os.path.join(SCRIPT_DIR, 'buyma_self_7days_latest.json')
MARKET_LATEST = os.path.join(SCRIPT_DIR, 'buyma_market_latest.json')
OUT_JSON = os.path.join(SCRIPT_DIR, 'merged_latest.json')
OUT_JS = os.path.join(SCRIPT_DIR, 'merged_latest.js')


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


# =====================================================
# DB 조회
# =====================================================

def fetch_raw_scraped(conn, limit: Optional[int] = None) -> List[Dict]:
    sql = """
        SELECT id, source_site, mall_product_id, brand_name_en,
               product_name, p_name_full, model_id, raw_price, stock_status,
               product_url, updated_at
        FROM raw_scraped_data
        WHERE model_id IS NOT NULL AND model_id != ''
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor() as c:
        c.execute(sql)
        return c.fetchall()


def _chunked(seq: List, size: int) -> Iterable[List]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def fetch_ace_products(conn, model_ids: List[str]) -> List[Dict]:
    """ace_products: model_no가 raw_scraped_data.model_id와 동일하다는 가정"""
    if not model_ids:
        return []
    out: List[Dict] = []
    # IN 절이 너무 길어지지 않게 1000개씩 끊기
    for chunk in _chunked(list({m for m in model_ids if m}), 1000):
        placeholders = ','.join(['%s'] * len(chunk))
        sql = f"""
            SELECT id, raw_data_id, model_no, name, buyma_product_id, status,
                   control, is_active, is_published, is_ready_to_publish,
                   is_lowest_price, buyma_lowest_price, buyma_lowest_price_checked_at,
                   price, margin_amount_krw, margin_rate, available_until,
                   purchase_price_krw, expected_shipping_fee,
                   buyma_registered_at, source_product_url
            FROM ace_products
            WHERE model_no IN ({placeholders})
        """
        with conn.cursor() as c:
            c.execute(sql, list(chunk))
            out.extend(c.fetchall())
    return out


def fetch_ace_images(conn, ace_product_ids: List[int]) -> List[Dict]:
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


# =====================================================
# 머지 로직
# =====================================================

STATUS_PRIORITY = ['on_sale', 'waiting', 'no_lowest', 'sold_out', 'unknown']


def _cannot_secure_lowest(a: Dict) -> bool:
    """확보불가 판정: 미출품 상태인데 출품가능최저가(마진0 가격)마저
    바이마최저가와 같거나 높아, 아무리 내려도 최저가 확보가 불가능한 경우만 True.

    - 바이마최저가 없음(경쟁자 없음)   → False (확인필요로 흐름)
    - 매입가 없어 마진0 가격 계산 불가  → False (확인필요로 흐름)
    ※ breakeven_price_jpy는 모듈 하단 정의 — 호출 시점엔 항상 바인딩됨.
    """
    if a.get('is_published') != 0:
        return False
    lp = a.get('buyma_lowest_price')
    if not lp:
        return False
    be = breakeven_price_jpy(a.get('purchase_price_krw'), a.get('expected_shipping_fee'))
    if be is None:
        return False
    return be >= lp


def determine_status(ace_rows: List[Dict], raw_rows: List[Dict],
                     in_seller_listing: bool = False) -> str:
    """
    4가지 상태 판정.
    ※ in_seller_listing은 detect_db_mismatch에서만 사용. status 판정에서는 제외
    (buyma_product_stats는 삭제/비활성된 상품의 stale row도 보유 → 출품중 카운트 부풀림 원인).
    """
    if ace_rows:
        if any(a.get('is_published') == 1 and a.get('is_active') == 1 for a in ace_rows):
            return 'on_sale'
        if any(a.get('is_ready_to_publish') == 1 and a.get('is_published') == 0 for a in ace_rows):
            return 'waiting'
        # 확보불가: 출품가능최저가(마진0 가격)마저 바이마최저가 이상이라
        # 가격을 내려도 최저가 확보가 불가능한 경우만. (경쟁자 없음/매입가 없음은 확인필요로 흐름)
        if any(_cannot_secure_lowest(a) for a in ace_rows):
            return 'no_lowest'
    if raw_rows and all((r.get('stock_status') or '').lower() == 'out_of_stock' for r in raw_rows):
        return 'sold_out'
    return 'unknown'


def detect_db_mismatch(in_seller_listing: bool, ace_rows: List[Dict]) -> Optional[str]:
    """
    셀러 페이지엔 떠 있는데 DB(ace_products)는 다르게 말하는 경우 사유 반환.
    일치하면 None. 셀러에 없으면 None (불일치 개념 자체가 안 잡힘).
    """
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


# 마진 계산 상수 (fast_price_updater.py / stock_price_synchronizer.py와 동일)
EXCHANGE_RATE = 9.2
SALES_FEE_RATE = 0.055
DEFAULT_SHIPPING_FEE = 15000

def breakeven_price_jpy(purchase_price_krw, shipping_fee_krw=DEFAULT_SHIPPING_FEE) -> Optional[int]:
    """수집처 판매가(매입가) 기준 마진이 0이 되는 출품가(¥) — 올림 처리.

    마진 = price_jpy*9.2*(1-0.055) - (매입가+배송료) + 매입가/11 = 0 을 역산.
    """
    purchase = _to_float(purchase_price_krw)
    if purchase is None or purchase <= 0:
        return None
    ship = _to_float(shipping_fee_krw)
    if ship is None or ship <= 0:
        ship = DEFAULT_SHIPPING_FEE
    net_cost_krw = purchase + ship - purchase / 11
    denom = EXCHANGE_RATE * (1 - SALES_FEE_RATE)
    return math.ceil(net_cost_krw / denom)


def expected_margin(item: Dict) -> tuple:
    """기대마진(원) + 마진율(%) 실시간 계산 → (margin_krw, margin_rate).

    기준가 = 출품중이면 바이마출품가(price_yen), 아니면 바이마최저가(buyma_lowest_price).
    기대마진(원) = (기준가 − 출품가능최저가) × 환율 × (1−판매수수료).
      ※ 출품가능최저가는 '마진 0이 되는 가격'이므로, 기준가와의 차액을 원화로 환산하면 곧 그 가격의 마진.
    출품가능최저가나 기준가가 없으면(경쟁자 없음/매입가 없음 등) (None, None).
    """
    avail = _to_float(item.get('available_lowest_price_jpy'))
    base = (_to_float(item.get('price_yen')) if item.get('status') == 'on_sale'
            else _to_float(item.get('buyma_lowest_price')))
    if avail is None or base is None:
        return None, None
    margin_krw = round((base - avail) * EXCHANGE_RATE * (1 - SALES_FEE_RATE))
    sales_krw = base * EXCHANGE_RATE
    rate = round(margin_krw / sales_krw * 100, 2) if sales_krw else None
    return margin_krw, rate


def build_merged(limit: Optional[int] = None) -> Dict:
    log("=" * 60)
    log("DB → 머지 데이터셋 생성 시작")
    log(f"  raw 행 한도: {limit if limit else '전체'}")
    log("=" * 60)

    # 1. 바이마 셀러 통계 로드 (있으면)
    buyma_stats_by_pid: Dict[str, Dict] = {}
    if os.path.exists(BUYMA_STATS_LATEST):
        with open(BUYMA_STATS_LATEST, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for it in data.get('items', []):
            buyma_stats_by_pid[str(it.get('buyma_product_id'))] = it
        log(f"바이마 셀러 통계 로드: {len(buyma_stats_by_pid)}건 "
            f"(수집일시 {data.get('collected_at', '-')})")
    else:
        log("바이마 셀러 통계 파일이 아직 없음. 통계 컬럼은 비워짐.", "WARN")

    # 1-1. 최근 7일 조회수 로드 (있으면)
    seven_days_by_pid: Dict[str, int] = {}
    if os.path.exists(SEVEN_DAYS_LATEST):
        with open(SEVEN_DAYS_LATEST, 'r', encoding='utf-8') as f:
            data7 = json.load(f)
        for it in data7.get('items', []):
            pid = str(it.get('buyma_product_id') or '')
            v = it.get('last_7days')
            if pid and v is not None:
                seven_days_by_pid[pid] = v
        log(f"최근 7일 조회수 로드: {len(seven_days_by_pid)}건 "
            f"(수집일시 {data7.get('collected_at', '-')})")
    else:
        log("최근 7일 조회수 파일이 아직 없음. 컬럼은 비워짐.", "WARN")

    # 1-2. 시장 데이터 (Phase 1: 인기순 순위 / 동일품번 제품수 / 1위 상품) 로드
    market_by_mid: Dict[str, Dict] = {}
    if os.path.exists(MARKET_LATEST):
        with open(MARKET_LATEST, 'r', encoding='utf-8') as f:
            datam = json.load(f)
        for it in datam.get('items', []):
            mid = it.get('model_id')
            if mid and 'error' not in it:
                market_by_mid[mid] = it
        log(f"시장 데이터 로드: {len(market_by_mid)}건 "
            f"(수집일시 {datam.get('collected_at', '-')})")
    else:
        log("시장 데이터 파일이 아직 없음. 인기순/동일품번 컬럼은 비워짐.", "WARN")

    # 2. DB 조회
    conn = pymysql.connect(**DB_CONFIG)
    try:
        raw_rows = fetch_raw_scraped(conn, limit=limit)
        log(f"raw_scraped_data: {len(raw_rows)}행 로드")

        # model_id 단위로 그룹핑
        by_model: Dict[str, List[Dict]] = defaultdict(list)
        for r in raw_rows:
            by_model[r['model_id']].append(r)
        log(f"품번(model_id) 그룹: {len(by_model)}개")

        # ace_products 매칭
        ace_rows = fetch_ace_products(conn, list(by_model.keys()))
        ace_by_model: Dict[str, List[Dict]] = defaultdict(list)
        for a in ace_rows:
            ace_by_model[a['model_no']].append(a)
        log(f"ace_products 매칭: {len(ace_rows)}건 "
            f"({len(ace_by_model)}개 품번에 ace 존재)")

        # 이미지
        ace_ids = [a['id'] for a in ace_rows]
        img_rows = fetch_ace_images(conn, ace_ids)
        img_by_ace: Dict[int, List[Dict]] = defaultdict(list)
        for img in img_rows:
            img_by_ace[img['ace_product_id']].append(img)
        log(f"이미지: {len(img_rows)}장")
    finally:
        conn.close()

    # 3. 머지: model_id 단위로 한 행 (같은 품번은 합침; 출처는 sources[]로 모달에서 보여줌)
    items: List[Dict] = []
    for model_id, raw_list in by_model.items():
        ace_list = ace_by_model.get(model_id, [])

        # 대표 raw: 가장 최근 updated_at
        raw0 = max(raw_list, key=lambda r: r.get('updated_at') or datetime.min)
        # 대표 ace: 출품된 것 우선, 없으면 첫 번째
        ace0 = None
        if ace_list:
            published = [a for a in ace_list if a.get('is_published') == 1]
            ace0 = published[0] if published else ace_list[0]

        # 바이마 셀러 통계 매칭 (ace0의 buyma_product_id 키로)
        bp_id = ace0.get('buyma_product_id') if ace0 else None
        bstats = buyma_stats_by_pid.get(str(bp_id)) if bp_id else None
        in_seller = bstats is not None

        status = determine_status(ace_list, raw_list, in_seller_listing=in_seller)
        db_mismatch_reason = detect_db_mismatch(in_seller, ace_list)

        # 출처 (중복 URL 제거)
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

        # 이미지
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

        item = {
            # 키
            'model_id': model_id,
            'buyma_product_id': str(bp_id) if bp_id else None,

            # 상태/이름
            'status': status,                                   # on_sale|waiting|no_lowest|sold_out|unknown
            'db_mismatch_reason': db_mismatch_reason,
            'name_ja': ace0.get('name') if ace0 else None,      # 일본어 가공본
            'name_ko': raw0.get('product_name') or raw0.get('p_name_full'),
            'brand_name_en': raw0.get('brand_name_en'),

            # 이미지
            'image_url': rep_image,
            'all_images': all_images,

            # 바이마 셀러 통계
            'access_count': bstats.get('access_count') if bstats else None,
            'cart_count': bstats.get('cart_count') if bstats else None,
            'favorite_count': bstats.get('favorite_count') if bstats else None,
            'access_7d': seven_days_by_pid.get(str(bp_id)) if bp_id else None,

            # 가격
            # ⑪ 바이마 최저가: 시장 검색 결과 기반 (우리 포함 동일품번 전체 최저가) 우선,
            #    없으면 ace_products.buyma_lowest_price (= 경쟁사 최저가) 폴백
            'buyma_lowest_price': (
                (market_by_mid.get(model_id) or {}).get('market_lowest_price')
                if (market_by_mid.get(model_id) or {}).get('market_lowest_price') is not None
                else (ace0.get('buyma_lowest_price') if ace0 else None)
            ),
            'our_market_price':   (market_by_mid.get(model_id) or {}).get('our_market_price'),
            # ⑫ 출품가능 최저가: 수집처 판매가(매입가) 기준 마진이 0이 되는 출품가(¥), 올림
            'available_lowest_price_jpy': (
                breakeven_price_jpy(ace0.get('purchase_price_krw'), ace0.get('expected_shipping_fee'))
                if ace0 else None
            ),
            'price_yen': bstats.get('price_yen') if bstats else (ace0.get('price') if ace0 else None),  # ⑬

            # 마진
            'margin_amount_krw': _to_float(ace0.get('margin_amount_krw')) if ace0 else None,
            'margin_rate': _to_float(ace0.get('margin_rate')) if ace0 else None,

            # 일시
            'price_updated_at': _iso(ace0.get('buyma_lowest_price_checked_at')) if ace0 else None,  # ㉒
            'source_updated_at': _iso(raw0.get('updated_at')),                                       # ㉓
            'registered_at': (
                bstats.get('registered_at') if bstats
                else _fmt_dt(ace0.get('buyma_registered_at')) if ace0 else None
            ),                                                                                       # ㉔
            'expire_at': (
                bstats.get('expire_at') if bstats
                else _fmt_dt(ace0.get('available_until'), '%Y/%m/%d') if ace0 else None
            ),                                                                                       # ㉖

            # 시장 데이터 (Phase 1)
            'same_count':       (market_by_mid.get(model_id) or {}).get('same_count'),
            'rank_position':    (market_by_mid.get(model_id) or {}).get('rank_position'),
            'our_ranks':        (market_by_mid.get(model_id) or {}).get('our_ranks'),
            'top1_link':        (market_by_mid.get(model_id) or {}).get('top1_link'),
            'top1_is_ours':     (market_by_mid.get(model_id) or {}).get('top1_is_ours'),
            'top1_seller_name': (market_by_mid.get(model_id) or {}).get('top1_seller_name'),
            'top1_seller_id':   (market_by_mid.get(model_id) or {}).get('top1_seller_id'),
            'top1_price':       (market_by_mid.get(model_id) or {}).get('top1_price'),
            'top1_name':        (market_by_mid.get(model_id) or {}).get('top1_name'),

            # 출처 (모달용)
            'sources': sources,

            # 디버그
            '_ace_count': len(ace_list),
            '_raw_count': len(raw_list),
        }
        # 기대마진(원)·마진율(%): 저장값이 아니라 (기준가 − 출품가능최저가) 실시간 계산
        item['expected_margin_krw'], item['expected_margin_rate'] = expected_margin(item)
        items.append(item)

    # 상태별 통계 로그
    by_status = defaultdict(int)
    for it in items:
        by_status[it['status']] += 1

    log("=" * 60)
    log(f"머지 완료: {len(items)}건 (품번 단위)")
    for s in STATUS_PRIORITY:
        log(f"  {s:12s}: {by_status.get(s, 0):>6,}건")
    log("=" * 60)

    return {
        'collected_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(items),
        'items': items,
    }


# =====================================================
# main
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='DB + 바이마 크롤 머지 데이터셋 생성')
    parser.add_argument('--limit', type=int, default=None,
                        help='테스트용: raw_scraped_data 행 수 제한 (기본: 전체)')
    args = parser.parse_args()

    payload = build_merged(limit=args.limit)

    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    with open(OUT_JS, 'w', encoding='utf-8') as f:
        f.write('window.STATS_DATA = ')
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        f.write(';\n')

    log(f"출력: {OUT_JSON}")
    log(f"      {OUT_JS}")


if __name__ == '__main__':
    main()