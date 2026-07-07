# -*- coding: utf-8 -*-
"""
롯데온(lotteon.com 롯데백화점 mall_no=2) 상품 수집기

롯데온은 전체상품·브랜드 메뉴가 없고 카테고리 메뉴만 있다 → mall_categories 기준으로 수집.
네이버(브라우저 필요)와 달리 무신사처럼 순수 HTTP/JSON API로 수집 가능(Imperva 현재 비차단).

데이터 소스 (전부 비로그인 GET, 200 확인):
  1) 목록(+페이지넘김): GET https://www.lotteon.com/csearch/render/category?
        &u2={offset}&u3=60&u16=ranking.desc&u37=true&u39=0&render=nqapi&platform=pc
        &collection_id=201&u9=navigateProduct&u4={LE카테고리소문자}&login=Y&mallId=2
     → HTML 안 상품 JSON 배열. u2를 60씩 늘려 페이지넘김(겹침 0).
       상품 1개: productId, productName, brandName(한글), brandId, priceInfo, productLink(sitmNo), productImage
  2) 상세: GET https://pbf.lotteon.com/product/v2/detail/search/base/sitm/{sitmNo}?sitmNo={sitmNo}&mall_no=2
     → data.imgInfo.imageList(이미지), optionInfo.optionList(색/사이즈), basicInfo(mdlNo/원산지/브랜드/상태), artlInfo(소재)

매핑:
  - raw_scraped_data : source_site='lotte', mall_product_id=productId, model_id=상품명 끝 추출(+mdlNo 폴백)
  - mall_brands      : 한글 brandName 그대로 저장(영문변환은 다음 단계), mall_brand_no=brandId(P2146)
  - 브랜드명은 raw 단계에서 한글 유지 — brand_name_en 자리에 한글 저장

사용법:
    python lotte_collector.py --category LE30010104 --limit 5 --dry-run --dump   # 소량 테스트
    python lotte_collector.py --category LE30010104                              # 특정 카테고리만
    python lotte_collector.py                                                    # is_active=1 카테고리 전체
    python lotte_collector.py --skip-existing                                    # 등록완료 상품 스킵
"""

import os
import re
import json
import time
import random
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL', f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4")
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=280)

# ===========================================
# 상수
# ===========================================

SOURCE_SITE = 'lotte'
MALL_ID = 2                       # 롯데백화점
PAGE_SIZE = 60
LIST_URL = 'https://www.lotteon.com/csearch/render/category'
DETAIL_API = 'https://pbf.lotteon.com/product/v2/detail/search/base/sitm/{sitm}?sitmNo={sitm}&mall_no=2'
IMAGE_HOST = 'https://contents.lotteon.com/itemimage'
PRODUCT_URL = 'https://www.lotteon.com/p/product/{pid}?sitmNo={sitm}&mall_no=2'

REQUEST_DELAY_MIN = 0.25
REQUEST_DELAY_MAX = 0.6
HTTP_TIMEOUT = 25
MAX_RETRY = 3
MAX_PAGES = 200                   # 카테고리당 안전 상한 (60*200=12,000)

USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')

_COLOR_WORDS = {'BLACK', 'WHITE', 'NAVY', 'GREY', 'GRAY', 'RED', 'BLUE', 'GREEN', 'BROWN',
                'BEIGE', 'PINK', 'CREAM', 'KHAKI', 'IVORY', 'CAMEL', 'SILVER', 'GOLD', 'TAN'}


# ===========================================
# HTTP
# ===========================================

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': USER_AGENT,
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': 'https://www.lotteon.com/',
    })
    return s


def fetch(session: requests.Session, url: str, as_json: bool) -> Optional[object]:
    """GET. as_json=True면 JSON(pbf), False면 텍스트(목록 HTML). 실패 시 None."""
    accept = 'application/json' if as_json else 'text/html,application/xhtml+xml'
    for attempt in range(MAX_RETRY):
        try:
            resp = session.get(url, headers={'Accept': accept}, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json() if as_json else resp.text
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 * (attempt + 1)
                logger.warning(f"  [HTTP {resp.status_code}] 재시도({attempt+1}/{MAX_RETRY}) {wait}s")
                time.sleep(wait)
                continue
            return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            wait = 2 * (attempt + 1)
            logger.warning(f"  [HTTP 예외] 재시도({attempt+1}/{MAX_RETRY}) {wait}s — {str(e)[:60]}")
            time.sleep(wait)
        except ValueError:
            return None
    return None


# ===========================================
# 목록 파싱
# ===========================================

def list_page_url(le_code: str, offset: int) -> str:
    return (f"{LIST_URL}?&u2={offset}&u3={PAGE_SIZE}&u16=ranking.desc&u37=true&u39=0"
            f"&render=nqapi&platform=pc&collection_id=201&u9=navigateProduct"
            f"&u4={le_code.lower()}&login=Y&mallId={MALL_ID}")


def parse_list(html: str) -> Tuple[List[Dict], Optional[int]]:
    """목록 HTML → ([{productId, sitmNo, brandName, brandId, productName, original, final, image, soldout}], totalCount)"""
    tc = re.search(r'"totalCount"\s*:\s*(\d+)', html)
    total = int(tc.group(1)) if tc else None

    items = []
    # productId 위치마다 다음 productId 전까지를 한 상품 윈도우로 잘라 필드 추출
    ids = [(m.group(1), m.start()) for m in re.finditer(r'"productId"\s*:\s*"([^"]+)"', html)]
    for i, (pid, pos) in enumerate(ids):
        end = ids[i + 1][1] if i + 1 < len(ids) else min(len(html), pos + 6000)
        w = html[pos:end]

        link = re.search(r'"productLink"\s*:\s*"([^"]*sitmNo=([^&"]+)[^"]*)"', w)
        sitm = link.group(2) if link else ''
        bnm = re.search(r'"brandName"\s*:\s*"([^"]*)"', w)
        bid = re.search(r'"brandId"\s*:\s*"([^"]*)"', w)
        pnm = re.search(r'"productName"\s*:\s*"([^"]*)"', w)
        img = re.search(r'"productImage"\s*:\s*"([^"]*)"', w)
        orig = re.search(r'"type"\s*:\s*"original"\s*,\s*"num"\s*:\s*(\d+)', w)
        fin = re.search(r'"type"\s*:\s*"final"\s*,\s*"num"\s*:\s*(\d+)', w)
        sold = re.search(r'"isSoutStkTemp"\s*:\s*true', w)

        if not sitm or not pnm:
            continue
        items.append({
            'productId': pid,
            'sitmNo': sitm,
            'brandName': (bnm.group(1) if bnm else '').strip(),
            'brandId': (bid.group(1) if bid else '').strip(),
            'productName': pnm.group(1).strip(),
            'original': int(orig.group(1)) if orig else 0,
            'final': int(fin.group(1)) if fin else 0,
            'image': img.group(1) if img else '',
            'soldout': bool(sold),
        })
    return items, total


# ===========================================
# 모델번호 추출 (상품명 끝 — 괄호 안팎 모두, 순수 짧은숫자/한글 제외)
# ===========================================

def is_valid_model(s: str) -> bool:
    s = (s or '').strip()
    if len(s) <= 3 or re.search(r'[가-힣]', s):
        return False
    parts = re.split(r'[\s/\-]+', s.upper())
    non = [p for p in parts if p and p not in _COLOR_WORDS]
    return len(''.join(non)) > 3


def extract_model(name: str) -> str:
    if not name:
        return ''
    s = name.strip()
    cands = []
    m = re.search(r'[\(\[]([^()\[\]]+)[\)\]]\s*$', s)               # 끝 괄호 안
    if m:
        cands.append(m.group(1))
    s2 = re.sub(r'[\(\[][^()\[\]]*[\)\]]\s*$', '', s).strip()       # 괄호 떼고
    m = re.search(r'_([A-Za-z0-9][A-Za-z0-9\-./]{3,})$', s2)        # 끝 _뒤
    if m:
        cands.append(m.group(1))
    m = re.search(r'([A-Z0-9][A-Z0-9\-_./ ]{3,}[A-Z0-9])\s*$', s2, re.I)  # 끝 공백토막
    if m:
        cands.append(m.group(1).strip())
    for c in cands:
        c = c.strip()
        if re.search(r'[가-힣]', c):
            continue
        comp = re.sub(r'[\s\-/_]', '', c)
        if len(comp) < 5:
            continue
        if not (re.search(r'[A-Za-z]', comp) and re.search(r'\d', comp)):
            continue
        return c
    return ''


# ===========================================
# 상세 파싱
# ===========================================

def build_images(img_info: Dict) -> List[str]:
    """imgInfo.imageList → 절대 이미지 URL 리스트 (정사각 갤러리, 원본크기)"""
    out = []
    for it in (img_info.get('imageList') or []):
        if not isinstance(it, dict):
            continue
        if it.get('epsrTypCd') and it.get('epsrTypCd') != 'IMG':
            continue
        rte = it.get('imgRteNm') or ''
        fn = it.get('imgFileNm') or ''
        if rte and fn:
            url = f"{IMAGE_HOST}{rte}{fn}"
            if url not in out:
                out.append(url)
        elif it.get('origImgFileNm'):
            out.append(it['origImgFileNm'])
    return out


def parse_options(opt_info: Dict) -> List[Dict]:
    """optionInfo.optionList(색상축/사이즈축) → 색×사이즈 조합 [{color, tag_size, option_code, status}].
    disabled=True인 값은 품절. 축 단위 품절만 반영(조합별 정밀재고는 v2)."""
    colors, sizes = [], []
    for axis in (opt_info.get('optionList') or []):
        title = (axis.get('title') or '')
        vals = axis.get('options') or []
        is_color = ('색상' in title) or ('컬러' in title) or ('color' in title.lower())
        for v in vals:
            label = (v.get('label') or '').strip()
            if not label:
                continue
            entry = {'label': label, 'value': v.get('value', ''), 'disabled': bool(v.get('disabled'))}
            (colors if is_color else sizes).append(entry)

    options = []
    if colors and sizes:
        for c in colors:
            for s in sizes:
                options.append({
                    'color': c['label'], 'tag_size': s['label'],
                    'option_code': f"{c['value']}/{s['value']}",
                    'status': 'out_of_stock' if (c['disabled'] or s['disabled']) else 'in_stock',
                })
    elif colors:
        for c in colors:
            options.append({'color': c['label'], 'tag_size': 'FREE', 'option_code': c['value'],
                            'status': 'out_of_stock' if c['disabled'] else 'in_stock'})
    elif sizes:
        for s in sizes:
            options.append({'color': '', 'tag_size': s['label'], 'option_code': s['value'],
                            'status': 'out_of_stock' if s['disabled'] else 'in_stock'})
    return options


def parse_artl(artl_info: Dict) -> Dict[str, str]:
    """artlInfo.pdItmsArtlJsn → {항목명: 값} (소재/원산지 등)"""
    out = {}
    lst = None
    if isinstance(artl_info, dict):
        lst = artl_info.get('pdItmsArtlJsn') or artl_info.get('artlList')
        if not lst:
            for v in artl_info.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    lst = v
                    break
    for it in (lst or []):
        if isinstance(it, dict):
            k = it.get('pdArtlCdNm') or it.get('artlNm')
            val = it.get('pdArtlCnts') or it.get('artlCnts')
            if k and val:
                out[k] = val
    return out


def convert_to_raw(item: Dict, detail: Dict, full_path: str) -> Optional[Dict]:
    data = (detail or {}).get('data') or {}
    basic = data.get('basicInfo') or {}

    # 상품명: 상세 pdNm(완전한 정식명) 우선, 없으면 목록명(절단 가능)
    product_name = basic.get('pdNm') or item['productName'] or ''
    if not product_name:
        return None

    # 모델: 상품명 끝 추출 → 실패시 mdlNo. 앞에 붙은 사이즈(짧은 숫자) 제거
    model_id = extract_model(product_name)
    if not model_id:
        model_id = (basic.get('mdlNo') or '').strip()
    model_id = re.sub(r'^\d{1,3}\s+', '', model_id).strip()   # "36 CDS62 B4MPL" → "CDS62 B4MPL"

    brand_name = item['brandName'] or basic.get('brdNm') or ''   # 한글 유지

    final = item['final'] or item['original'] or 0
    original = item['original'] or final                          # 할인 없으면 정가=판매가
    stock = 'out_of_stock' if item['soldout'] else 'in_stock'

    images = build_images(data.get('imgInfo') or {})
    if not images and item.get('image'):
        images = [item['image']]
    options = parse_options(data.get('optionInfo') or {})
    artl = parse_artl(data.get('artlInfo') or {})

    # 옵션이 전부 품절이면 품절로 보정
    if options and all(o['status'] == 'out_of_stock' for o in options):
        stock = 'out_of_stock'

    def _clean(v):
        v = (v or '').strip()
        return '' if ('참조' in v or v in ('-', '상세설명참조')) else v
    # 원산지: artl 제조국 우선(구체적) → oplcCdNm 폴백
    origin = _clean(artl.get('제조국')) or _clean(artl.get('원산지')) or _clean(basic.get('oplcCdNm'))
    material = _clean(artl.get('소재')) or _clean(artl.get('제품소재')) or _clean(artl.get('재질'))

    raw_json = {
        'brand_name_kr': brand_name,
        'brand_id': item['brandId'],
        'sitm_no': item['sitmNo'],
        'mdl_no': basic.get('mdlNo') or '',
        'product_status': basic.get('pdStatCdNm') or '',
        'origin': origin,
        'material': material,
        'std_category': ' > '.join([basic.get(f'stdCatLevel{i}Nm') or '' for i in (1, 2, 3, 4)]).strip(' >'),
        'options': options,
        'images': images,
        'artl_info': artl,
        'discount_rate': None,
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': item['productId'],
        'brand_name_en': brand_name,           # 한글 그대로(영문변환은 다음 단계)
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': full_path,
        'original_price': original,
        'raw_price': final,
        'stock_status': stock,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': PRODUCT_URL.format(pid=item['productId'], sitm=item['sitmNo']),
    }


# ===========================================
# DB
# ===========================================

def get_active_categories(only: Optional[str], all_cats: bool, root: Optional[str]) -> List[Dict]:
    """수집 대상 카테고리 (leaf만; 상위코드는 자식으로 대체돼 중복수집 방지).
    우선순위: --category(그것만) > --all/--root(is_active 무관 전체/샵) > 기본(is_active=1)."""
    with engine.connect() as conn:
        base = "SELECT category_id, full_path FROM mall_categories WHERE mall_name=:m"
        params = {'m': SOURCE_SITE}
        if only:
            q = base + " AND category_id=:c"
            params['c'] = only
        elif root:
            q = base + " AND category_id LIKE :r"
            params['r'] = root.upper() + '%'
        elif all_cats:
            q = base
        else:
            q = base + " AND is_active=1"
        rows = conn.execute(text(q + " ORDER BY category_id"), params).fetchall()
    cats = [{'category_id': r[0], 'full_path': r[1]} for r in rows]
    # leaf만: 다른 코드의 상위(prefix-parent)인 코드는 제외 (LE30010100은 LE30010104의 부모 → 제외)
    codes = {c['category_id'] for c in cats}
    def is_parent(code):
        aa, bb, cc = code[4:6], code[6:8], code[8:10]
        children = []
        if cc == '00' and bb != '00':
            children = [code[:8] + f'{i:02d}' for i in range(1, 100)]
        elif bb == '00' and aa != '00':
            children = [code[:6] + f'{i:02d}00' for i in range(1, 100)]
        return any(ch in codes for ch in children)
    return [c for c in cats if not is_parent(c['category_id'])]


def get_published_product_ids() -> set:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.mall_product_id FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site=:s AND a.is_published=1
        """), {'s': SOURCE_SITE})
        return {str(r[0]) for r in rows}


def get_collected_product_ids() -> set:
    """이미 raw_scraped_data에 저장된 mall_product_id (--skip-collected 재개용)."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT mall_product_id FROM raw_scraped_data WHERE source_site=:s"), {'s': SOURCE_SITE})
        return {str(r[0]) for r in rows}


def load_existing_brands() -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT mall_brand_name_en FROM mall_brands WHERE mall_name=:s AND mall_brand_name_en IS NOT NULL"),
            {'s': SOURCE_SITE})
        return {(r[0] or '').strip() for r in rows}


def ensure_brand(conn, brand_name: str, brand_id: str, seen: set) -> None:
    """mall_brands 자동 등록 — 한글 brandName 그대로, mall_brand_no=brandId(P2146). is_active=NULL 검수대기."""
    brand_name = (brand_name or '').strip()
    if not brand_name or brand_name in seen:
        return
    conn.execute(text("""
        INSERT INTO mall_brands
          (mall_name, mall_brand_name_en, raw_brand_name, mall_brand_no, mall_brand_url,
           buyma_brand_id, buyma_brand_name, mapping_level, is_mapped, is_active)
        VALUES
          (:m, :nm, :nm, :no, NULL, NULL, NULL, 0, 0, NULL)
    """), {'m': SOURCE_SITE, 'nm': brand_name, 'no': brand_id or None})
    seen.add(brand_name)
    logger.info(f"    + 신규 브랜드 등록(검수대기): {brand_name} ({brand_id})")


def save_rows(rows: List[Dict]):
    if not rows:
        return
    sql = text("""
        INSERT INTO raw_scraped_data
        (source_site, mall_product_id, brand_name_en, product_name, p_name_full, model_id,
         category_path, original_price, raw_price, stock_status, raw_json_data, product_url)
        VALUES
        (:source_site, :mall_product_id, :brand_name_en, :product_name, :p_name_full, :model_id,
         :category_path, :original_price, :raw_price, :stock_status, :raw_json_data, :product_url)
        ON DUPLICATE KEY UPDATE
          brand_name_en=VALUES(brand_name_en), product_name=VALUES(product_name),
          p_name_full=VALUES(p_name_full), model_id=VALUES(model_id),
          category_path=VALUES(category_path), original_price=VALUES(original_price),
          raw_price=VALUES(raw_price), stock_status=VALUES(stock_status),
          raw_json_data=VALUES(raw_json_data), product_url=VALUES(product_url), updated_at=NOW()
    """)
    for attempt in range(3):
        try:
            with engine.connect() as conn:
                for r in rows:
                    conn.execute(sql, r)
                conn.commit()
            return
        except OperationalError as e:
            logger.warning(f"  [DB] 저장 실패({attempt+1}/3) 재연결 재시도: {str(e)[:80]}")
            engine.dispose()
            time.sleep(2 * (attempt + 1))


# ===========================================
# 메인
# ===========================================

def run(args):
    session = make_session()
    cats = get_active_categories(args.category, args.all, args.root)
    if not cats:
        if args.category:
            logger.warning(f"카테고리 {args.category} 없음 (mall_categories에 lotte로 등록돼 있나 확인)")
        elif args.all or args.root:
            logger.warning("등록된 lotte 카테고리 없음.")
        else:
            logger.warning("활성 카테고리(is_active=1) 없음. --all/--root로 전체 수집하거나 카테고리를 켜세요.")
        return
    logger.info(f"대상 카테고리 {len(cats)}개")

    skip_ids = set()
    if args.skip_existing:
        skip_ids = get_published_product_ids()
        logger.info(f"  등록완료 {len(skip_ids)}개 스킵")
    if args.skip_collected:
        skip_ids |= get_collected_product_ids()
        logger.info(f"  이미 수집된 상품 스킵 (누적 스킵 대상 {len(skip_ids)}개)")
    seen_brands = load_existing_brands()
    nb0 = len(seen_brands)

    seen_products = set()
    total = 0
    batch = []
    first_dump = None
    stop = False

    for ci, cat in enumerate(cats, 1):
        le = cat['category_id']
        full_path = cat['full_path']
        logger.info(f"\n=== [{ci}/{len(cats)}] {le} | {full_path} ===")
        offset = 0
        cat_total = None
        page = 0
        while page < MAX_PAGES:
            html = fetch(session, list_page_url(le, offset), as_json=False)
            if not html:
                logger.warning(f"  목록 실패 offset={offset}")
                break
            items, tc = parse_list(html)
            if cat_total is None and tc is not None:
                cat_total = tc
                logger.info(f"  총 {tc}개")
            if not items:
                break

            for it in items:
                pid = it['productId']
                if pid in seen_products:
                    continue
                seen_products.add(pid)
                if args.skip_existing and pid in skip_ids:
                    continue

                detail = fetch(session, DETAIL_API.format(sitm=it['sitmNo']), as_json=True)
                data = convert_to_raw(it, detail or {}, full_path)
                if not data:
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                total += 1
                raw = json.loads(data['raw_json_data'])
                if total <= 8 or total % 100 == 0:
                    logger.info(f"    [{total}] {data['stock_status']:12} | {data['brand_name_en'][:14]:14} | "
                                f"{(data['model_id'] or '-'):18} | {int(data['raw_price']):>9,}원 | "
                                f"img:{len(raw['images'])} opt:{len(raw['options'])} | {data['product_name'][:26]}")
                if first_dump is None:
                    first_dump = data

                if not args.dry_run:
                    if not args.skip_mapping and data['brand_name_en']:
                        try:
                            with engine.begin() as conn:
                                ensure_brand(conn, it['brandName'], it['brandId'], seen_brands)
                        except OperationalError as e:
                            logger.warning(f"  [DB] 브랜드 등록 실패: {str(e)[:60]}")
                    batch.append(data)
                    if len(batch) >= 10:
                        save_rows(batch)
                        batch = []

                if args.limit and total >= args.limit:
                    stop = True
                    break
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            if stop:
                break
            offset += PAGE_SIZE
            page += 1
            if cat_total is not None and offset >= cat_total:
                break
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
        if stop:
            break

    if batch and not args.dry_run:
        save_rows(batch)

    logger.info("\n" + "=" * 60)
    logger.info(f"롯데온 수집 완료 — 총 {total}개 | 신규 브랜드 {len(seen_brands) - nb0}개")
    if args.dump and first_dump:
        logger.info("=== 첫 행 덤프 ===")
        for k, v in first_dump.items():
            if k == 'raw_json_data':
                print(json.dumps(json.loads(v), ensure_ascii=False, indent=2))
            else:
                logger.info(f"  {k}: {v}")
    if not args.dry_run:
        with engine.connect() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM raw_scraped_data WHERE source_site=:s"),
                               {'s': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 lotte raw: {cnt}개")
    logger.info("=" * 60)


def main():
    p = argparse.ArgumentParser(description='롯데온 상품 수집기')
    p.add_argument('--category', type=str, help='특정 LE 카테고리코드만 (활성무관, 테스트용)')
    p.add_argument('--all', action='store_true', help='등록된 lotte 카테고리 전체 (is_active 무관)')
    p.add_argument('--root', type=str, help='특정 샵 루트만 (예: LE30=해외패션, LE20=가방/패션ACC)')
    p.add_argument('--limit', type=int, help='최대 수집 상품 수')
    p.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    p.add_argument('--dump', action='store_true', help='첫 수집 행 전체 출력')
    p.add_argument('--skip-existing', action='store_true', help='등록완료(출품된) 상품 스킵')
    p.add_argument('--skip-collected', action='store_true', help='이미 raw 수집된 상품 스킵 (중단 후 재개용)')
    p.add_argument('--skip-mapping', action='store_true', help='mall_brands 자동등록 건너뛰기')
    args = p.parse_args()

    logger.info("=" * 60)
    logger.info(f"롯데온 수집 시작 (dry_run={args.dry_run}, category={args.category or '활성전체'})")
    logger.info("=" * 60)
    run(args)


if __name__ == '__main__':
    main()
