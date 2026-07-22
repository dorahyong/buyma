# -*- coding: utf-8 -*-
"""
ABC마트 / 그랜드스테이지 (a-rt.com) 상품 수집 스크립트

같은 플랫폼(a-rt.com) 위 채널 2개. 수집기는 1개, --channel 로 분기.
DB에는 source_site 를 각각 따로 넣는다 (abcmart / grandstage).

수집 기준: 브랜드 (전체상품 메뉴 없음). mall_brands 는 이미 수집돼 있어야 함.
  → 그 브랜드로만 상품을 돌기 때문에 raw 의 브랜드가 mall_brands 에 없을 수 없음.
  → mall_categories 는 수집 중 없으면 INSERT / 있으면 skip (브랜드 기준이라 미리 못 채움).

데이터 흐름 (전부 비로그인 200 확인):
  1) 목록  : GET /display/search-word/result/list  (HTML 조각)
             필수: searchPageGubun=brsearch, brandNo, searchBrandNo, channel(10001/10002), page, perPage=30
             → li.prod-item[data-product-no] = prdtNo. page 늘리다 0개면 종료.
  2) 상세  : GET /product/info?prdtNo={prdtNo}  (JSON)
             → productOption[](옵션·재고), productPrice(정가/판매가), productImage/Extra(이미지),
               brandNo, styleInfo(모델), prdtName/engPrdtName, genderGbnCode
  3) 카테고리: GET /product/new?prdtNo={prdtNo}  (HTML) → #prdtCtgrCrumb breadcrumb
             → "KIDS>신발>스니커즈>라이프스타일" (성별/연령 포함. JSON-LD/히든필드는 성별 빠져서 안 씀)

매핑:
  - raw_scraped_data : source_site=abcmart/grandstage, mall_product_id=prdtNo, model_id=styleInfo
  - mall_categories  : full_path(성별 포함)로 중복판별, 없으면 INSERT
  - mall_brands      : 이미 수집됨. 수집기는 읽기만 함 (등록 안 함).

사용법:
    python abc_collector.py --channel abcmart                 # abcmart 전체 브랜드 raw 수집
    python abc_collector.py --channel grandstage              # grandstage
    python abc_collector.py --channel abcmart --brand 000003  # 특정 브랜드만
    python abc_collector.py --channel abcmart --limit 5 --dry-run   # 5개만, DB 저장 X (테스트)
"""

import os
import re
import json
import time
import random
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# ===========================================
# 환경 설정
# ===========================================

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL', f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4")
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=280)

# ===========================================
# 상수
# ===========================================

# 채널: source_site → (도메인, channel 파라미터값)
CHANNELS = {
    'abcmart':    ('https://abcmart.a-rt.com',    '10001'),
    'grandstage': ('https://grandstage.a-rt.com', '10002'),
}

LIST_PATH = '/display/search-word/result/list'
INFO_PATH = '/product/info'
DETAIL_PATH = '/product/new'

PAGE_SIZE = 30
REQUEST_DELAY_MIN = 0.2
REQUEST_DELAY_MAX = 0.5
HTTP_TIMEOUT = 20
MAX_RETRY = 3

# breadcrumb 첫 크럼(성별/연령) → 정규화 gender
# 주의: a-rt breadcrumb는 성별을 영어 대문자(MEN/WOMEN/KIDS)로 표기. 한글은 혹시 몰라 같이 둠.
GENDER_MAP = {
    'MEN': 'men', 'MAN': 'men', '남성': 'men',
    'WOMEN': 'women', 'WOMAN': 'women', '여성': 'women',
    'KIDS': 'kids', 'KID': 'kids', '키즈': 'kids', '유아': 'kids', '아동': 'kids',
    'UNISEX': 'unisex', '공용': 'unisex',
}


def to_gender(label: str) -> str:
    return GENDER_MAP.get(label) or GENDER_MAP.get((label or '').upper()) or 'unisex'

# 판매상태: 10001=판매중, 10002=품절
SELL_STATUS_ON = '10001'

USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')


# ===========================================
# HTTP
# ===========================================

def make_session(domain: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': USER_AGENT,
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': domain + '/',
    })
    return s


def _get(session: requests.Session, url: str, params: Optional[Dict] = None, referer: Optional[str] = None):
    headers = {'Referer': referer} if referer else None
    for attempt in range(MAX_RETRY):
        try:
            r = session.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                return r
            logger.warning(f"  [HTTP {r.status_code}] {url} (시도 {attempt+1}/{MAX_RETRY})")
        except requests.RequestException as e:
            logger.warning(f"  [HTTP 오류] {url}: {str(e)[:80]} (시도 {attempt+1}/{MAX_RETRY})")
        time.sleep(1.5 * (attempt + 1))
    return None


def _sleep():
    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


# ===========================================
# 1) 목록 API — 브랜드별 prdtNo 수집
# ===========================================

def get_product_ids(session: requests.Session, domain: str, channel: str, brand_no: str) -> List[str]:
    """브랜드의 모든 prdtNo. page 1부터 상품 0개 나올 때까지."""
    referer = f"{domain}/product/brand/page?brandNo={brand_no}&tChnnlNo={channel[:4]}0{channel[-1]}"
    # 실제 tChnnlNo도 channel과 동일(10001/10002)
    referer = f"{domain}/product/brand/page?brandNo={brand_no}&tChnnlNo={channel}"
    ids: List[str] = []
    seen = set()
    page = 1
    while True:
        params = {
            'searchPageGubun': 'brsearch',
            'searchPageType': 'brand',
            'brandNo': brand_no,
            'searchBrandNo': brand_no,
            'channel': channel,
            'page': page,
            'perPage': PAGE_SIZE,
            'pageColumn': 4,
            'tabGubun': 'total',
            'deviceCode': '10000',
            'firstSearchYn': 'Y',
            'searchRcmdYn': 'N',
            'brandPrdtArtDispYn': 'Y',
        }
        r = _get(session, domain + LIST_PATH, params=params, referer=referer)
        if r is None:
            logger.warning(f"    [목록] brand={brand_no} page={page} 실패 — 중단")
            break
        soup = BeautifulSoup(r.text, 'lxml')
        cards = soup.select('li.prod-item[data-product-no]')
        page_ids = [c['data-product-no'] for c in cards if c.get('data-product-no')]
        page_ids = [p for p in page_ids if p not in seen]
        if not page_ids:
            break
        for p in page_ids:
            seen.add(p)
            ids.append(p)
        page += 1
        _sleep()
    return ids


# ===========================================
# 2) 상세 — /product/info (JSON)
# ===========================================

def get_info(session: requests.Session, domain: str, prdt_no: str) -> Optional[Dict]:
    referer = f"{domain}/product/new?prdtNo={prdt_no}"
    r = _get(session, domain + INFO_PATH, params={'prdtNo': prdt_no, 'xramdom': random.random()}, referer=referer)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def parse_options(info: Dict) -> List[Dict]:
    """productOption[] → 옵션 리스트. 품절/재고0 제외.
    신발 = 1차원(optnName=사이즈), 의류 = 2차원(optnName=색상, addOptn2Text=사이즈)."""
    out = []
    for o in info.get('productOption') or []:
        stock = o.get('totalStockQty') or 0
        sell_stat = str(o.get('sellStatCode') or '')
        if sell_stat != SELL_STATUS_ON or stock <= 0:
            continue
        name = (o.get('optnName') or '').strip()
        size2 = (o.get('addOptn2Text') or '').strip()  # 의류만 존재(사이즈)
        if size2:  # 2차원: 색상 + 사이즈
            color, size = name, size2
        else:      # 1차원: 사이즈만
            color, size = '', name
        out.append({
            'optn_no': o.get('prdtOptnNo'),
            'color': color,
            'size': size,
            'stock': stock,
        })
    return out


def build_images(info: Dict) -> List[str]:
    urls = []
    for k in ('productImage', 'productImageExtra'):
        for x in info.get(k) or []:
            u = x.get('imageUrl')
            if u and u not in urls:
                urls.append(u)
    return urls


# ===========================================
# 3) 카테고리 — /product/new breadcrumb (성별 포함)
# ===========================================

def get_breadcrumb(session: requests.Session, domain: str, prdt_no: str) -> Optional[Dict]:
    """#prdtCtgrCrumb → {'full_path','gender','depths'}. HOME 제외, select는 selected만."""
    r = _get(session, domain + DETAIL_PATH, params={'prdtNo': prdt_no}, referer=domain + '/')
    if r is None:
        return None
    soup = BeautifulSoup(r.text, 'lxml')
    crumb = soup.select_one('#prdtCtgrCrumb ol.breadcrumb-list')
    if not crumb:
        return None
    parts = []
    for li in crumb.select('li.crumb'):
        if 'home' in (li.get('class') or []):
            continue
        sel = li.select_one('select option[selected]')
        if sel:
            parts.append(sel.get_text(strip=True))
        else:
            parts.append(li.get_text(strip=True))
    parts = [p for p in parts if p]
    if not parts:
        return None
    gender = to_gender(parts[0])
    return {'full_path': '>'.join(parts), 'gender': gender, 'depths': parts}


# ===========================================
# DB — 카테고리 자동 등록 + raw 저장
# ===========================================

def load_brands(channel_site: str, only_brand: Optional[str]) -> List[Dict]:
    """수집 대상 브랜드 (mall_brands 에서 읽기만)."""
    sql = ("SELECT mall_brand_no, mall_brand_name_en FROM mall_brands "
           "WHERE mall_name = :m AND mall_brand_no IS NOT NULL "
           "AND (is_active = 1 OR is_active IS NULL)")
    params = {'m': channel_site}
    if only_brand:
        sql += " AND mall_brand_no = :b"
        params['b'] = only_brand
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [{'no': r[0], 'en': r[1]} for r in rows]


def load_existing_category_paths(channel_site: str) -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT full_path FROM mall_categories WHERE mall_name = :s AND full_path IS NOT NULL"),
            {'s': channel_site})
        return {r[0] for r in rows}


def ensure_category(conn, channel_site: str, cat: Dict, seen: set) -> None:
    """없으면 INSERT / 있으면 skip. 성별은 breadcrumb 첫 크럼에서."""
    fp = cat['full_path']
    if not fp or fp in seen:
        return
    d = (cat['depths'] + [None, None, None, None])[:4]
    conn.execute(text("""
        INSERT INTO mall_categories
          (mall_name, category_id, gender, depth1, depth2, depth3, depth4,
           full_path, mall_category_url, is_active, created_at)
        VALUES
          (:m, NULL, :g, :d1, :d2, :d3, :d4, :fp, NULL, NULL, NOW())
        ON DUPLICATE KEY UPDATE
          gender = VALUES(gender),
          depth1 = VALUES(depth1), depth2 = VALUES(depth2),
          depth3 = VALUES(depth3), depth4 = VALUES(depth4)
    """), {
        'm': channel_site, 'g': cat['gender'],
        'd1': d[0], 'd2': d[1], 'd3': d[2], 'd4': d[3], 'fp': fp,
    })
    seen.add(fp)


def convert_to_raw_data(channel_site: str, domain: str, prdt_no: str,
                        info: Dict, options: List[Dict], cat: Optional[Dict]) -> Optional[Dict]:
    product_name = (info.get('prdtName') or info.get('engPrdtName') or '').strip()
    if not product_name:
        return None
    pp = info.get('productPrice') or {}
    raw_price = pp.get('sellAmt') or pp.get('normalAmt') or 0
    original_price = pp.get('normalAmt') or raw_price
    stock_status = 'in_stock' if options else 'out_of_stock'
    images = build_images(info)

    raw_json = {
        'brand_no': info.get('brandNo') or '',
        'style_info': info.get('styleInfo') or '',
        'color_info': info.get('prdtColorInfo') or '',
        'gender_gbn_code': info.get('genderGbnCode') or '',
        'gender': cat['gender'] if cat else '',
        'eng_product_name': info.get('engPrdtName') or '',
        'options': options,
        'images': images,
        'thumbnail': images[0] if images else '',
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }
    return {
        'source_site': channel_site,
        'mall_product_id': str(prdt_no),
        'brand_name_en': None,   # ensure 단계에서 mall_brands 조인. 아래 run에서 채움.
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': info.get('styleInfo') or '',
        'category_path': cat['full_path'] if cat else None,
        'original_price': original_price,
        'raw_price': raw_price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': f"{domain}/product/new?prdtNo={prdt_no}",
    }


def save_to_database(data_list: List[Dict]):
    if not data_list:
        return
    insert_sql = text("""
        INSERT INTO raw_scraped_data
        (source_site, mall_product_id, brand_name_en,
         product_name, p_name_full, model_id, category_path,
         original_price, raw_price, stock_status, raw_json_data, product_url)
        VALUES
        (:source_site, :mall_product_id, :brand_name_en,
         :product_name, :p_name_full, :model_id, :category_path,
         :original_price, :raw_price, :stock_status, :raw_json_data, :product_url)
        ON DUPLICATE KEY UPDATE
        brand_name_en = VALUES(brand_name_en),
        product_name = VALUES(product_name),
        p_name_full = VALUES(p_name_full),
        model_id = VALUES(model_id),
        category_path = VALUES(category_path),
        original_price = VALUES(original_price),
        raw_price = VALUES(raw_price),
        stock_status = VALUES(stock_status),
        raw_json_data = VALUES(raw_json_data),
        product_url = VALUES(product_url),
        updated_at = NOW()
    """)
    for attempt in range(MAX_RETRY):
        try:
            with engine.connect() as conn:
                for data in data_list:
                    conn.execute(insert_sql, data)
                conn.commit()
            return
        except OperationalError as e:
            logger.warning(f"  [DB] 저장 실패({attempt+1}/{MAX_RETRY}) — 재연결 후 재시도: {str(e)[:80]}")
            engine.dispose()
            time.sleep(2 * (attempt + 1))


# ===========================================
# 메인 수집
# ===========================================

def run_collection(args):
    channel_site = args.channel
    domain, channel = CHANNELS[channel_site]
    session = make_session(domain)

    brands = load_brands(channel_site, args.brand)
    if not brands:
        logger.error(f"[{channel_site}] mall_brands 에 브랜드가 없음. 먼저 브랜드 수집 필요.")
        return
    logger.info(f"[{channel_site}] 대상 브랜드 {len(brands)}개")

    cat_seen = load_existing_category_paths(channel_site)
    total_saved = 0
    processed = 0          # 처리한 상품 수 (--limit 판정용, dry-run 포함)
    buffer: List[Dict] = []

    for bi, brand in enumerate(brands, 1):
        brand_no, brand_en = brand['no'], brand['en']
        prdt_ids = get_product_ids(session, domain, channel, brand_no)
        logger.info(f"  ({bi}/{len(brands)}) {brand_en}({brand_no}): 상품 {len(prdt_ids)}개")

        for pi, prdt_no in enumerate(prdt_ids, 1):
            if args.limit and processed >= args.limit:
                break
            processed += 1
            info = get_info(session, domain, prdt_no)
            if not info:
                continue
            options = parse_options(info)
            cat = get_breadcrumb(session, domain, prdt_no)
            _sleep()
            raw = convert_to_raw_data(channel_site, domain, prdt_no, info, options, cat)
            if not raw:
                continue
            raw['brand_name_en'] = brand_en  # 브랜드 기준 수집이므로 확정

            if args.dry_run:
                logger.info(f"    [DRY] {prdt_no} | {brand_en} | {raw['product_name']} | "
                            f"{raw['raw_price']}원 | {raw['category_path']} | 옵션 {len(options)}개 | 이미지 {len(json.loads(raw['raw_json_data'])['images'])}장")
            else:
                # 카테고리 없으면 INSERT
                if cat:
                    with engine.connect() as conn:
                        ensure_category(conn, channel_site, cat, cat_seen)
                        conn.commit()
                buffer.append(raw)
                if len(buffer) >= 20:
                    save_to_database(buffer)
                    total_saved += len(buffer)
                    buffer = []

        if args.limit and processed >= args.limit:
            logger.info(f"  --limit {args.limit} 도달 — 중단")
            break

    if buffer and not args.dry_run:
        save_to_database(buffer)
        total_saved += len(buffer)

    logger.info(f"[{channel_site}] 완료 — raw 저장 {total_saved}건 (dry-run={args.dry_run})")


def main():
    ap = argparse.ArgumentParser(description='ABC마트/그랜드스테이지 상품 수집')
    ap.add_argument('--channel', required=True, choices=list(CHANNELS.keys()),
                    help='수집 채널 (abcmart / grandstage)')
    ap.add_argument('--brand', default=None, help='특정 브랜드 번호만 (예: 000003)')
    ap.add_argument('--limit', type=int, default=None, help='최대 상품 수 (테스트용)')
    ap.add_argument('--dry-run', action='store_true', help='DB 저장 없이 출력만')
    args = ap.parse_args()
    run_collection(args)


if __name__ == '__main__':
    main()
