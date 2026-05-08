# -*- coding: utf-8 -*-
"""
바이마 시장 데이터 수집기 (Phase 1)

대상: merged_latest.json의 status=on_sale 행 → 그 model_id로 인기순 검색
검색 URL: https://www.buyma.com/r/-O1/{quoted_model_id}/   ( -O1 = 人気順 )

수집 컬럼 (기획문서 ⑮⑯⑱):
  - same_count          : 동일품번 제품 수 (검색결과 li.product 갯수, 중고 제외)
  - rank_position       : 우리(KONNECT)의 인기순 첫 등장 위치 (없으면 None)
  - our_ranks           : 우리 셀러가 등장하는 모든 위치 list
  - top1_link/seller/price/name
  - top1_is_ours        : 1위가 우리인지

⑰ 동일품번 총조회수, ⑱ 1위 상품 access/문의수/문의일은 검색 카드에 없어서 Phase 2.

출력: buyma_market_latest.json / .js
사용법:
    python3 buyma_market_collector.py                  # 전체
    python3 buyma_market_collector.py --limit 100      # 테스트
    python3 buyma_market_collector.py --workers 5
    python3 buyma_market_collector.py --resume         # 누락분만
"""

import os
import sys
import re
import json
import time
import argparse
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import requests as req_lib
from bs4 import BeautifulSoup
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(os.path.dirname(SCRIPT_DIR), '.env'))

BUYMA_BASE_URL = "https://www.buyma.com"
SEARCH_URL_TEMPLATE = BUYMA_BASE_URL + "/r/-O1/{q}/"
BUYMA_BUYER_ID = os.getenv('BUYMA_BUYER_ID', '13053653')  # KONNECT

COOKIE_FILE = os.path.normpath(
    os.path.join(SCRIPT_DIR, '..', 'buyma_cleaners', 'buyma_cookies.json')
)
MERGED_LATEST = os.path.join(SCRIPT_DIR, 'merged_latest.json')
OUT_LATEST = os.path.join(SCRIPT_DIR, 'buyma_market_latest.json')
OUT_LATEST_JS = os.path.join(SCRIPT_DIR, 'buyma_market_latest.js')

PER_REQUEST_SLEEP = 0.5
REQUEST_TIMEOUT = 20


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def make_session() -> req_lib.Session:
    s = req_lib.Session()
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            for c in json.load(f):
                s.cookies.set(c['name'], c['value'])
    s.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'ja,en;q=0.9',
        'Referer': BUYMA_BASE_URL,
    })
    return s


def parse_search(html: str) -> List[Dict]:
    """검색 결과 페이지 → 상품 리스트 파싱 (인기순 순위 그대로)"""
    soup = BeautifulSoup(html, 'html.parser')
    products = soup.select('li.product')

    out: List[Dict] = []
    for p in products:
        # 중고 제외 (기획상 우리 마켓 비교 대상 아님)
        if p.select_one('.product_used_tag'):
            continue

        # 셀러 ID + 닉네임
        buyer_id, buyer_name = None, None
        ba = p.select_one('.product_Buyer a')
        if ba:
            href = ba.get('href', '')
            m = re.search(r'/buyer/(\d+)', href)
            if m:
                buyer_id = m.group(1)
            buyer_name = ba.get_text(strip=True)

        # 상품 링크 + 상품ID
        link, product_id = None, None
        la = p.select_one('a[href*="/item/"]')
        if la:
            link = la.get('href', '') or ''
            if link and not link.startswith('http'):
                link = BUYMA_BASE_URL + link
            m = re.search(r'/item/(\d+)', link)
            if m:
                product_id = m.group(1)

        # 가격
        price = None
        pe = p.select_one('.Price_Txt')
        if pe:
            txt = re.sub(r'[^\d]', '', pe.get_text())
            if txt:
                try:
                    price = int(txt)
                except ValueError:
                    pass

        # 상품명
        name_el = p.select_one('.product_name')
        name = name_el.get_text(strip=True) if name_el else None

        out.append({
            'buyer_id': buyer_id,
            'buyer_name': buyer_name,
            'product_id': product_id,
            'link': link,
            'price': price,
            'name': name,
        })
    return out


def normalize_search_query(model_id: str) -> str:
    """
    검색 쿼리 정규화. 슬래시(/, \\)는 URL path 분리자로 오해되어
    바이마가 부분 매칭하므로 공백으로 치환. 연속 공백은 하나로.
    예: '4900/97MA' → '4900 97MA'
    """
    if not model_id:
        return ''
    q = re.sub(r'[/\\]+', ' ', str(model_id))
    q = re.sub(r'\s+', ' ', q).strip()
    return q


def fetch_one(session: req_lib.Session, model_id: str) -> Dict:
    search_q = normalize_search_query(model_id)
    enc = urllib.parse.quote(search_q, safe='')
    url = SEARCH_URL_TEMPLATE.format(q=enc)
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return {'model_id': model_id, 'error': f'HTTP {r.status_code}'}
        products = parse_search(r.text)
        return {'model_id': model_id, 'products': products}
    except Exception as e:
        return {'model_id': model_id, 'error': str(e)[:120]}


def aggregate_one(it: Dict) -> Dict:
    """raw 결과 → 화면 컬럼 형태로 가공"""
    if 'error' in it:
        return {'model_id': it['model_id'], 'error': it['error']}
    products = it.get('products', [])
    same_count = len(products)
    our_ranks = [i + 1 for i, p in enumerate(products) if p.get('buyer_id') == BUYMA_BUYER_ID]
    rank_position = our_ranks[0] if our_ranks else None
    top1 = products[0] if products else None

    # 시장 최저가 (우리 포함 모든 동일품번 중 최저가) — 기획문서 ⑪
    prices = [p['price'] for p in products if p.get('price') is not None]
    market_lowest_price = min(prices) if prices else None
    our_prices = [p['price'] for p in products
                  if p.get('buyer_id') == BUYMA_BUYER_ID and p.get('price') is not None]
    our_market_price = min(our_prices) if our_prices else None

    return {
        'model_id': it['model_id'],
        'same_count': same_count,
        'market_lowest_price': market_lowest_price,   # ⑪ 시장 최저가 (우리 포함)
        'our_market_price': our_market_price,         # 참고: 우리 셀러의 최저가
        'rank_position': rank_position,
        'our_ranks': our_ranks,
        'top1_link': top1['link'] if top1 else None,
        'top1_product_id': top1['product_id'] if top1 else None,
        'top1_is_ours': bool(top1 and top1.get('buyer_id') == BUYMA_BUYER_ID),
        'top1_seller_id': top1['buyer_id'] if top1 else None,
        'top1_seller_name': top1['buyer_name'] if top1 else None,
        'top1_price': top1['price'] if top1 else None,
        'top1_name': top1['name'] if top1 else None,
    }


def load_target_models() -> List[str]:
    """merged_latest.json의 on_sale 행에서 model_id 추출"""
    if not os.path.exists(MERGED_LATEST):
        log(f"merged_latest.json 없음: {MERGED_LATEST}", "ERROR")
        log("먼저 build_merged_dataset.py 를 실행해주세요.")
        sys.exit(1)
    with open(MERGED_LATEST, 'r', encoding='utf-8') as f:
        d = json.load(f)
    seen, models = set(), []
    for it in d.get('items', []):
        if it.get('status') != 'on_sale':
            continue
        mid = it.get('model_id')
        if mid and mid not in seen:
            seen.add(mid)
            models.append(mid)
    log(f"대상 model_id (on_sale): {len(models)}개")
    return models


def load_existing() -> Dict[str, Dict]:
    if not os.path.exists(OUT_LATEST):
        return {}
    with open(OUT_LATEST, 'r', encoding='utf-8') as f:
        d = json.load(f)
    return {it['model_id']: it for it in d.get('items', []) if 'error' not in it}


def save(items_by_mid: Dict[str, Dict]) -> None:
    items = list(items_by_mid.values())
    payload = {
        'collected_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(items),
        'items': items,
    }
    with open(OUT_LATEST, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(OUT_LATEST_JS, 'w', encoding='utf-8') as f:
        f.write('window.MARKET_DATA = ')
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write(';\n')


def _worker(session: req_lib.Session, mid: str) -> Dict:
    res = fetch_one(session, mid)
    time.sleep(PER_REQUEST_SLEEP)
    return aggregate_one(res)


def main():
    p = argparse.ArgumentParser(description='바이마 시장 데이터 (Phase 1) 수집')
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--workers', type=int, default=5)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--save-every', type=int, default=2000)
    args = p.parse_args()

    log("=" * 60)
    log("바이마 시장 데이터 수집 (Phase 1: 인기순 검색)")
    log(f"  buyer_id={BUYMA_BUYER_ID}, workers={args.workers}, limit={args.limit}, resume={args.resume}")
    log("=" * 60)

    session = make_session()
    models = load_target_models()
    if args.limit:
        models = models[:args.limit]

    existing = load_existing() if args.resume else {}
    if args.resume:
        log(f"기존 결과: {len(existing)}건. 누락분만 처리.")
        models = [m for m in models if m not in existing]
        log(f"이번 처리 대상: {len(models)}건")

    if not models:
        log("처리할 항목 없음")
        return

    items_by_mid: Dict[str, Dict] = dict(existing)
    done, err = 0, 0
    started = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_worker, session, m) for m in models]
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                items_by_mid[res['model_id']] = res
                if res.get('error'):
                    err += 1
            done += 1

            if done % 200 == 0 or done == len(models):
                elapsed = time.time() - started
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(models) - done) / rate if rate > 0 else 0
                log(f"  진행 {done:>6,}/{len(models):,} (에러 {err}) · "
                    f"{rate:.1f} req/s · 남은 {eta/60:.1f}분")

            if done % args.save_every == 0:
                save(items_by_mid)
                log(f"  중간 저장: {len(items_by_mid):,}건")

    save(items_by_mid)

    log("=" * 60)
    log(f"완료: 누적 {len(items_by_mid):,}건 (이번 처리 {done:,}건, 에러 {err}건)")
    log(f"  → {OUT_LATEST}")
    log("=" * 60)


if __name__ == '__main__':
    main()
