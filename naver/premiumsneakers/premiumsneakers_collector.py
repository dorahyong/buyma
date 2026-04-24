# -*- coding: utf-8 -*-
"""
프리미엄스니커즈(premiumsneakers) 상품 수집기

데이터 소스:
  1. 리스트 페이지: https://smartstore.naver.com/premiumsneakers/category/{mall_brand_no}?page=N&size=40
     → 각 상품의 channelProductNo를 data-shp-contents-id 속성에서 추출
  2. 상품 JSON API: https://smartstore.naver.com/i/v2/channels/{channelUid}/products/{productNo}?withWindow=false
     → 완벽히 구조화된 상품 객체 (productImages, optionCombinations, category, salePrice, ...)
  3. 혜택 API (쿠폰 적용가): POST .../product-benefits/{productNo}
     → optimalDiscount.totalDiscountResult.summary.totalPayAmount (나의 할인가)

흐름:
  - Playwright(headless=False, 로그인 쿠키 로드) 단일 컨텍스트 사용
  - 브랜드별(mall_brands) 카테고리 페이지 순회 → channelProductNo 목록
  - 각 상품 상세 페이지 방문 → page.on('response')로 두 XHR(products GET, product-benefits POST) 자동 캡처
  - raw_scraped_data row로 변환 → DB 저장

사용법:
    python premiumsneakers_collector.py --login                      # 쿠키 갱신
    python premiumsneakers_collector.py --brand "Balenciaga"         # 특정 브랜드만
    python premiumsneakers_collector.py --limit 5 --dry-run          # 소량 테스트
    python premiumsneakers_collector.py                              # 전체 수집
    python premiumsneakers_collector.py --skip-existing              # 기존 수집 상품 제외
"""

import os
import sys
import io
import re
import json
import random
import logging
import asyncio
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    'DATABASE_URL',
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)
engine = create_engine(DATABASE_URL, echo=False)

# --source CLI 인자로 덮어씀 (기본값: premiumsneakers)
STORE_ID = 'premiumsneakers'
SOURCE_SITE = 'premiumsneakers'
STORE_HOME = f'https://smartstore.naver.com/{STORE_ID}'


def set_source(source: str) -> None:
    """CLI --source 인자 처리 — mall_sites.site_url에서 STORE_HOME 조회 (smartstore/brand.naver.com 둘 다 지원)"""
    global STORE_ID, SOURCE_SITE, STORE_HOME
    STORE_ID = source
    SOURCE_SITE = source
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT site_url FROM mall_sites WHERE site_name = :s AND is_active = 1"),
            {'s': source}
        ).fetchone()
    if row and row[0]:
        STORE_HOME = row[0].rstrip('/')
    else:
        STORE_HOME = f'https://smartstore.naver.com/{STORE_ID}'

# naver_cookies.json은 scan_store_brands.py와 공용 (상위 naver/ 디렉토리)
COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'naver_cookies.json')

LIST_DELAY = (1.0, 2.0)
DETAIL_DELAY = (1.0, 3.0)  # 네이버 XHR 연속 호출 시 차단 → 충분한 간격 필요
DETAIL_MAX_RETRIES = 2


# =====================================================
# DB
# =====================================================

def get_brands(brand_filter: Optional[str] = None) -> List[Dict]:
    """mall_brands에서 활성 브랜드 조회"""
    with engine.connect() as conn:
        q = """
            SELECT mall_brand_name_en, mall_brand_url, mall_brand_no
            FROM mall_brands
            WHERE mall_name = :site AND is_active = 1
        """
        params = {'site': SOURCE_SITE}
        if brand_filter:
            q += " AND UPPER(mall_brand_name_en) = :brand"
            params['brand'] = brand_filter.upper()
        rows = conn.execute(text(q), params).fetchall()
        return [{'name': r[0], 'url': r[1], 'brand_no': r[2]} for r in rows]


def get_existing_product_ids() -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT mall_product_id FROM raw_scraped_data WHERE source_site = :site"
        ), {'site': SOURCE_SITE}).fetchall()
        return {str(r[0]) for r in rows}


def save_rows(rows: List[Dict]):
    if not rows:
        return
    sql = text("""
        INSERT INTO raw_scraped_data
        (source_site, mall_product_id, brand_name_en, brand_name_kr,
         product_name, p_name_full, model_id, category_path,
         original_price, raw_price, stock_status, raw_json_data, product_url)
        VALUES
        (:source_site, :mall_product_id, :brand_name_en, :brand_name_kr,
         :product_name, :p_name_full, :model_id, :category_path,
         :original_price, :raw_price, :stock_status, :raw_json_data, :product_url)
        ON DUPLICATE KEY UPDATE
            product_name = VALUES(product_name),
            p_name_full = VALUES(p_name_full),
            category_path = VALUES(category_path),
            original_price = VALUES(original_price),
            raw_price = VALUES(raw_price),
            stock_status = VALUES(stock_status),
            raw_json_data = VALUES(raw_json_data),
            product_url = VALUES(product_url),
            updated_at = NOW()
    """)
    with engine.connect() as conn:
        for r in rows:
            conn.execute(sql, r)
        conn.commit()
    logger.info(f"DB 저장: {len(rows)}건")


# =====================================================
# 유틸
# =====================================================

_COLOR_WORDS = {
    'BLACK', 'WHITE', 'NAVY', 'GREY', 'GRAY', 'RED', 'BLUE', 'GREEN', 'BROWN',
    'BEIGE', 'PINK', 'CREAM', 'KHAKI', 'ORANGE', 'YELLOW', 'IVORY', 'CAMEL',
    'CHARCOAL', 'SILVER', 'GOLD', 'BURGUNDY', 'OLIVE', 'TAN', 'SAND', 'NATURAL',
}


def is_valid_model_id(s: str) -> bool:
    s = (s or '').strip()
    if len(s) <= 3:
        return False
    if re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', s):
        return False
    parts = re.split(r'[\s/\-]+', s.upper())
    non_color = [p for p in parts if p and p not in _COLOR_WORDS]
    return len(''.join(non_color)) > 3


def extract_model_from_name(name: str) -> str:
    if not name:
        return ''
    m = re.search(r'([A-Z0-9][A-Z0-9\-_./]{3,}[A-Z0-9])\s*$', name.strip(), re.IGNORECASE)
    if m and is_valid_model_id(m.group(1)):
        return m.group(1)
    for c in reversed(re.findall(r'[A-Z0-9][A-Z0-9\-_./\s]{3,}[A-Z0-9]', name, re.IGNORECASE)):
        c = c.strip()
        if is_valid_model_id(c):
            return c
    return ''


def absolute_url(path_or_url: str) -> str:
    if path_or_url.startswith('http'):
        return path_or_url
    if path_or_url.startswith('/'):
        u = urlparse(STORE_HOME)
        return f"{u.scheme}://{u.netloc}{path_or_url}"
    return path_or_url


# =====================================================
# Playwright: 로그인
# =====================================================

async def login_and_save_cookies():
    from playwright.async_api import async_playwright
    logger.info("네이버 로그인 페이지를 엽니다. 로그인 후 스마트스토어로 이동해주세요.")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1280, 'height': 900}, locale='ko-KR')
        page = await context.new_page()
        await page.goto('https://nid.naver.com/nidlogin.login')
        try:
            await page.wait_for_url('**/smartstore.naver.com/**', timeout=300000)
        except Exception:
            input(">>> 로그인 완료했으면 Enter: ")
        cookies = await context.cookies()
        with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info(f"쿠키 저장: {COOKIE_FILE} ({len(cookies)}개)")
        await browser.close()


# =====================================================
# Phase 1: 리스트 페이지 → channelProductNo
# =====================================================

async def collect_product_list(page, brands: List[Dict], limit: Optional[int],
                               skip_ids: set, count_only: bool = False) -> List[Dict]:
    """브랜드별 카테고리 페이지 순회 → [{'product_no','brand_name'}...]"""
    results = []
    seen = set()

    for brand in brands:
        brand_name = brand['name']
        brand_url = absolute_url(brand['url']) if brand.get('url') else ''
        if not brand_url:
            logger.warning(f"[{brand_name}] URL 없음 → 스킵")
            continue

        logger.info(f"\n>>> 브랜드: {brand_name}")
        page_num = 1
        brand_count = 0
        PAGE_SIZE = 40
        max_pages = None

        # DOM 추출 스크립트 (pnos + total + soldout 스킵)
        EXTRACT_JS = '''() => {
            const s = [];
            const added = new Set();
            let soldOutSkipped = 0;
            const isSoldOut = (card) => {
                if (!card) return false;
                return Array.from(card.querySelectorAll('span, em, strong')).some(el => {
                    const t = (el.textContent || '').trim();
                    return t === '품절' || t === '일시품절';
                });
            };
            const findCard = (el) => el.closest('li') || el.closest('[class*="product"]') || el.parentElement;
            document.querySelectorAll('[data-shp-contents-type="chnl_prod_no"]').forEach(el => {
                const id = el.getAttribute('data-shp-contents-id');
                if (!id || !/^\\d+$/.test(id) || added.has(id)) return;
                if (isSoldOut(findCard(el))) { soldOutSkipped++; return; }
                added.add(id); s.push(id);
            });
            document.querySelectorAll('a[href*="/products/"]').forEach(a => {
                const m = a.href.match(/\\/products\\/(\\d+)/);
                if (!m || added.has(m[1])) return;
                if (isSoldOut(findCard(a))) { soldOutSkipped++; return; }
                added.add(m[1]); s.push(m[1]);
            });
            let total = null;
            for (const el of document.querySelectorAll('span, strong, em')) {
                const t = (el.textContent || '').trim();
                const m = t.match(/총\\s*([0-9,]+)\\s*개/);
                if (m) { total = parseInt(m[1].replace(/,/g,''), 10); break; }
            }
            return { pnos: s, total, soldOutSkipped };
        }'''

        try:
            # p1: 직접 navigate
            await page.goto(brand_url, timeout=30000)
            await page.wait_for_load_state('domcontentloaded', timeout=10000)
            await asyncio.sleep(1.0)
            if '보안 확인' in await page.content():
                logger.error("캡챠 감지! --login으로 쿠키 갱신 필요")
                return results

            while page_num <= 100:
                data = await page.evaluate(EXTRACT_JS)
                pnos = data.get('pnos') or []
                total = data.get('total')
                first_id = pnos[0] if pnos else None

                if page_num == 1 and total is not None:
                    max_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                    logger.info(f"  총 {total}개 → {max_pages}페이지 예상")
                    if count_only:
                        results.append({'_brand_count': True, 'brand_name': brand_name, 'total': total})
                        break

                new_cnt = 0
                page_seen_overlap = 0
                for pno in pnos:
                    if pno in seen:
                        page_seen_overlap += 1
                        continue
                    seen.add(pno)
                    if pno in skip_ids:
                        continue
                    results.append({'product_no': pno, 'brand_name': brand_name})
                    brand_count += 1
                    new_cnt += 1

                sold_out = data.get('soldOutSkipped') or 0
                logger.info(f"  p{page_num}: {len(pnos)}개 발견, {new_cnt}개 신규, 품절스킵 {sold_out}개 (누적 {brand_count})")

                if limit and len(results) >= limit:
                    return results[:limit]
                if max_pages is not None and page_num >= max_pages:
                    break
                if not pnos or (page_num > 1 and page_seen_overlap == len(pnos)):
                    break

                # 다음 페이지로 이동: 숫자 버튼 우선, 없으면 "다음" 버튼
                next_num = page_num + 1
                num_sel = f'a[data-shp-contents-type="pgn"][data-shp-contents-id="{next_num}"]'
                clicked = False
                btn = await page.query_selector(num_sel)
                if btn:
                    await btn.click()
                    clicked = True
                else:
                    next_btn = await page.query_selector('a[role="button"]:has-text("다음")')
                    if next_btn:
                        await next_btn.click()
                        clicked = True
                if not clicked:
                    logger.warning(f"  p{next_num} 버튼 없음 → 종료")
                    break

                # DOM 업데이트 대기: 첫 상품 ID가 바뀔 때까지
                try:
                    await page.wait_for_function(
                        '''(prev) => {
                            const el = document.querySelector('[data-shp-contents-type="chnl_prod_no"]');
                            return el && el.getAttribute('data-shp-contents-id') !== prev;
                        }''',
                        arg=first_id,
                        timeout=10000,
                    )
                except Exception:
                    logger.warning(f"  p{next_num} DOM 업데이트 대기 타임아웃")

                await asyncio.sleep(random.uniform(*LIST_DELAY))
                page_num += 1
        except Exception as e:
            logger.warning(f"  p{page_num} 오류: {e}")

        logger.info(f"<<< [{brand_name}] 완료: {brand_count}개")

    return results


# =====================================================
# Phase 2: 상세 — XHR 응답 가로채기 (products + product-benefits)
# =====================================================

async def fetch_detail(page, product_no: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    """상품 상세 페이지 방문 → 브라우저가 자동 발사하는 두 XHR 가로채기:
      1. GET /i/v2/channels/{uid}/products/{pno}?withWindow=false → 상품 JSON
      2. POST /i/v2/channels/{uid}/product-benefits/{pno}         → 쿠폰 적용가 JSON
    """
    captured = []  # Response 객체 저장

    product_re = re.compile(rf'/i/v2/channels/[^/]+/products/{product_no}(\?|$)')
    benefits_re = re.compile(rf'/i/v2/channels/[^/]+/product-benefits/{product_no}(\?|$)')

    def on_response(response):
        url = response.url
        if product_re.search(url) or benefits_re.search(url):
            captured.append(response)

    page.on('response', on_response)
    try:
        detail_url = f"{STORE_HOME}/products/{product_no}"
        try:
            await page.goto(detail_url, timeout=30000)
            await page.wait_for_load_state('domcontentloaded', timeout=10000)
            try:
                await page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"  상세 페이지 로드 실패: {e}")
            return None, None

        # 캡챠 체크 (타이틀 기반 — '보안 확인'이 본문 텍스트에 들어가는 오탐 방지)
        page_title = await page.title()
        if '보안' in page_title or 'captcha' in page_title.lower():
            logger.error(f"캡챠 감지! title={page_title!r}")
            return None, None

        # 캡처된 응답 파싱
        product = None
        benefits = None
        for resp in captured:
            url = resp.url
            try:
                if benefits_re.search(url):
                    if resp.status == 200:
                        benefits = await resp.json()
                elif product_re.search(url):
                    if resp.status == 200:
                        product = await resp.json()
            except Exception:
                pass

        return product, benefits
    finally:
        page.remove_listener('response', on_response)


# =====================================================
# 매핑: product JSON → raw_scraped_data row
# =====================================================

def map_to_row(product: Dict, benefits: Optional[Dict],
               brand_from_list: str, product_no: str) -> Optional[Dict]:
    if not product:
        return None

    product_name = (product.get('name') or '').strip()
    if not product_name:
        return None

    nsi = product.get('naverShoppingSearchInfo') or {}
    # mall_brands(brand_from_list)가 authoritative source — 판매자 입력 변덕(PARAJUMPERS vs 파라점퍼스) 방지
    brand_name = (brand_from_list
                  or nsi.get('manufacturerName') or nsi.get('brandName')
                  or product.get('brandName') or '').strip()

    # 모델번호
    model_id = (nsi.get('modelName') or product.get('modelName') or '').strip()
    if not model_id or not is_valid_model_id(model_id):
        model_id = extract_model_from_name(product_name)
    if not model_id:
        return None

    # 가격
    original_price = int(product.get('salePrice') or 0)
    sale_price = original_price
    if benefits:
        try:
            pay = (((benefits.get('optimalDiscount') or {})
                    .get('totalDiscountResult') or {})
                    .get('summary') or {}).get('totalPayAmount')
            if pay and pay > 0:
                sale_price = int(pay)
        except Exception:
            pass
    if sale_price == original_price:
        bv = product.get('benefitsView') or {}
        d = bv.get('discountedSalePrice') or 0
        if d and 0 < d < original_price:
            sale_price = int(d)

    # 카테고리
    cat = product.get('category') or {}
    category_path = (cat.get('wholeCategoryName') or '').replace('\u003e', '>').replace('>', ' > ')
    category_path = re.sub(r'\s*>\s*', ' > ', category_path).strip()

    # 이미지 (네이버 productImages는 동일 URL 중복 포함되는 경우 있음)
    images = []
    _seen_imgs = set()
    for _img in (product.get('productImages') or []):
        _u = _img.get('url')
        if _u and _u not in _seen_imgs:
            _seen_imgs.add(_u)
            images.append(_u)

    # 옵션 - groupName으로 optionName1/2가 색상인지 사이즈인지 판별
    # product.options: [{optionType, groupName: '색상'|'사이즈'|...}, ...]
    # product.optionCombinations: [{id, optionName1, optionName2?, stockQuantity, ...}, ...]
    opt_groups = product.get('options') or []
    group_types = []  # ['color', 'size', 'skip'] — skip은 모델명 등 실사용 안 하는 옵션
    for g in opt_groups:
        gname = (g.get('groupName') or '').strip()
        gname_up = gname.upper()
        if '색상' in gname or '컬러' in gname or 'COLOR' in gname_up:
            group_types.append('color')
        elif '모델' in gname or 'MODEL' in gname_up or '품번' in gname or '스타일' in gname:
            group_types.append('skip')
        else:
            # '사이즈', '신발사이즈' 등은 모두 size로 처리
            group_types.append('size')

    def _normalize_size(s: str) -> str:
        s = (s or '').strip()
        if s.upper() in {'ONE SIZE', 'ONESIZE', '단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈', 'UNI', 'FREE'}:
            return 'FREE'
        return s

    combos = product.get('optionCombinations') or []
    options = []
    for i, c in enumerate(combos):
        n1 = (c.get('optionName1') or '').strip()
        n2 = (c.get('optionName2') or '').strip()
        names = [n for n in [n1, n2] if n]

        color_val = ''
        size_val = ''
        for idx, name in enumerate(names):
            gtype = group_types[idx] if idx < len(group_types) else 'size'
            if gtype == 'color':
                color_val = name
            elif gtype == 'skip':
                continue
            else:
                size_val = _normalize_size(name)

        if not size_val and not color_val:
            size_val = 'FREE'
        if not size_val:
            size_val = 'FREE'

        stock = int(c.get('stockQuantity') or 0)
        options.append({
            'color': color_val or '',
            'tag_size': size_val,
            'option_code': str(c.get('id', i)),
            'status': 'in_stock' if stock > 0 else 'out_of_stock',
        })

    if options:
        stock_status = 'in_stock' if any(o['status'] == 'in_stock' for o in options) else 'out_of_stock'
    else:
        stock_status = 'in_stock' if int(product.get('stockQuantity') or 0) > 0 else 'out_of_stock'

    # 원산지/소재 (productInfoProvidedNoticeView — 카테고리별 필드 다름)
    pipn = product.get('productInfoProvidedNoticeView') or {}
    origin = ''
    material = ''
    if isinstance(pipn, dict):
        origin = (pipn.get('origin') or pipn.get('originArea') or '')
        material = (pipn.get('material') or pipn.get('materialInfo') or '')

    raw_json = {
        'channel_no': (product.get('channel') or {}).get('channelNo', ''),
        'brand_name': brand_name,
        'brand_id': nsi.get('brandId', ''),
        'model_name': nsi.get('modelName', '') or product.get('modelName', ''),
        'origin': origin,
        'material': material,
        'manufacturer': nsi.get('manufacturerName', ''),
        'options': options,
        'images': images,
        'category': category_path,
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': brand_name,
        'brand_name_kr': brand_name,
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': category_path,
        'original_price': original_price,
        'raw_price': sale_price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': f"{STORE_HOME}/products/{product_no}",
    }


# =====================================================
# 오케스트레이션
# =====================================================

async def run(brand_filter: Optional[str], limit: Optional[int],
              skip_existing: bool, dry_run: bool, dump: bool = False, count_only: bool = False):
    from playwright.async_api import async_playwright

    brands = get_brands(brand_filter)
    logger.info(f"대상 브랜드: {len(brands)}개")
    if not brands:
        logger.warning("mall_brands에 브랜드 없음. scan_store_brands.py로 먼저 수집하세요.")
        return

    skip_ids = get_existing_product_ids() if skip_existing else set()
    if skip_existing:
        logger.info(f"기존 수집: {len(skip_ids)}개 (스킵)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            locale='ko-KR',
            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/146.0.0.0 Safari/537.36'),
        )
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                await context.add_cookies(json.load(f))
            logger.info(f"쿠키 로드: {COOKIE_FILE}")

        page = await context.new_page()

        # Phase 1
        logger.info("\n== Phase 1: 리스트 수집 ==")
        items = await collect_product_list(page, brands, limit, skip_ids, count_only=count_only)

        if count_only:
            await browser.close()
            logger.info("\n=== 브랜드별 상품 수 집계 ===")
            grand_total = 0
            for it in items:
                if it.get('_brand_count'):
                    logger.info(f"  {it['brand_name']}: {it['total']}개")
                    grand_total += it['total']
            logger.info(f"총합: {grand_total}개 ({len(items)}개 브랜드)")
            return

        logger.info(f"수집 대상: {len(items)}개")

        if not items:
            await browser.close()
            return

        # Phase 2
        logger.info("\n== Phase 2: 상세 수집 ==")
        rows = []
        total = len(items)
        for i, item in enumerate(items, 1):
            pno = item['product_no']
            product, benefits = None, None
            for attempt in range(DETAIL_MAX_RETRIES + 1):
                product, benefits = await fetch_detail(page, pno)
                if product:
                    break
                if attempt < DETAIL_MAX_RETRIES:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"[{i}/{total}] {pno} 재시도 {attempt + 1} — {wait}초 대기")
                    await asyncio.sleep(wait)
            row = map_to_row(product, benefits, item['brand_name'], pno)
            if not row:
                if product:
                    logger.info(f"[{i}/{total}] {pno} 스킵: 모델번호 없음 (모음전 추정)")
                else:
                    logger.warning(f"[{i}/{total}] {pno} 매핑 실패 (product={bool(product)}, benefits={bool(benefits)})")
                continue
            rows.append(row)
            raw = json.loads(row['raw_json_data'])
            logger.info(
                f"[{i}/{total}] {row['brand_name_en']} | {row['model_id']} | "
                f"₩{row['raw_price']:,} (정가 ₩{row['original_price']:,}) | "
                f"img:{len(raw['images'])} | opt:{len(raw['options'])}"
            )
            await asyncio.sleep(random.uniform(*DETAIL_DELAY))

        await browser.close()

    logger.info(f"\n== 수집 완료: {len(rows)}/{len(items)} ==")

    if dump and rows:
        logger.info("\n=== 첫 행 전체 덤프 ===")
        r0 = rows[0]
        for k, v in r0.items():
            if k == 'raw_json_data':
                logger.info(f"{k}:")
                print(json.dumps(json.loads(v), ensure_ascii=False, indent=2))
            else:
                logger.info(f"{k}: {v}")

    if rows and not dry_run:
        save_rows(rows)
    elif dry_run:
        logger.info("(DRY-RUN: DB 저장 생략)")


def main():
    parser = argparse.ArgumentParser(description='네이버 스마트스토어 상품 수집기')
    parser.add_argument('--source', type=str, default='premiumsneakers',
                        help='스마트스토어 ID = mall_name = source_site (예: premiumsneakers, fabstyle)')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 (UPPER 매칭)')
    parser.add_argument('--limit', type=int, help='최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='기존 수집 상품 스킵')
    parser.add_argument('--login', action='store_true', help='네이버 로그인 → 쿠키 갱신')
    parser.add_argument('--dump', action='store_true', help='수집된 첫 행의 raw_json_data 전체 출력 (dry-run 전용)')
    parser.add_argument('--count', action='store_true', help='Phase 1만 실행 — 브랜드별 상품 수만 집계 후 종료')
    args = parser.parse_args()

    set_source(args.source)

    if args.login:
        asyncio.run(login_and_save_cookies())
        return

    logger.info("=" * 60)
    logger.info(f"{SOURCE_SITE} 수집 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    logger.info("=" * 60)

    asyncio.run(run(args.brand, args.limit, args.skip_existing, args.dry_run, args.dump, args.count))


if __name__ == '__main__':
    main()
