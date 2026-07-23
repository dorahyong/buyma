# -*- coding: utf-8 -*-
"""
네이버 스마트스토어 상품 수집기 — 전체상품 페이지 기반

대상: 브랜드 리스트 없음 + 카테고리 URL 필터링이 동작 안 하는 스토어
      (dmont, tuttobene, thefactor2)

방식:
  - 스토어별 '전체상품' URL (`/category/ALL` 또는 해시 경로) 1개만 페이지네이션 순회
  - `size=80&filters=oa` (품절 제외, 한 페이지 80개)
  - 상품 상세에서 brand/category를 추출 → mall_brands/mall_categories 자동 INSERT

공유 컴포넌트는 premiumsneakers_collector에서 import:
  - set_source, fetch_detail, map_to_row, save_rows, get_published_product_ids
  - absolute_url, login_and_save_cookies, COOKIE_FILE, DETAIL_DELAY, LIST_DELAY, DETAIL_MAX_RETRIES

사용법:
    python premiumsneakers_category_collector.py --source thefactor2 --limit 5 --dry-run
    python premiumsneakers_category_collector.py --source dmont --skip-existing
    python premiumsneakers_category_collector.py --source tuttobene
"""

import os
import sys
import json
import random
import logging
import asyncio
import argparse
from typing import Dict, List, Optional

from sqlalchemy import text

# 공유 컴포넌트 재사용 (기존 파일 절대 수정 안 함)
from premiumsneakers_collector import (
    engine,
    set_source,
    login_and_save_cookies,
    get_published_product_ids,
    save_rows,
    fetch_detail,
    map_to_row,
    absolute_url,
    COOKIE_FILE,
    DETAIL_MAX_RETRIES,
)
# brand.naver.com 도메인 전체상품 collector 용 /n/v2/ fetcher
from brand_store_collector import fetch_detail_brand_store
import premiumsneakers_collector as base

# category_collector 전용 딜레이 (브랜드 collector의 값과 독립) — 보수적 단축
LIST_DELAY = (0.2, 0.5)
DETAIL_DELAY = (0.5, 1.0)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)


# =====================================================
# 스토어별 '전체상품' URL (size=80, 품절제외 filters=oa)
# =====================================================

STORE_ALL_PRODUCT_URLS = {
    'dmont':      'https://smartstore.naver.com/dmont/category/ALL?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'thefactor2': 'https://smartstore.naver.com/thefactor2/category/0802efb236ac45b9bdd736ee3c31152d?st=POPULAR&dt=BIG_IMAGE&page=1&size=80&filters=oa',
    'tuttobene':  'https://smartstore.naver.com/tutto-bene/category/ALL?st=POPULAR&dt=GALLERY&page=1&size=80&filters=oa',
    # 2026-04-21: 11개 네이버 스마트스토어 추가
    'maniaon':    'https://smartstore.naver.com/maniaon/category/64da291c96134f9ab518de04ce6d1a08?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'bblue':      'https://smartstore.naver.com/bblue/category/ALL?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'euroline':   'https://smartstore.naver.com/euroline/category/b4faac568c4e4f0caa620e7022d3b858?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'unico':      'https://smartstore.naver.com/unicoselectshop/category/2x8wQfzcssxk4zLN38r9c_ALL_PRODUCT?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'kometa':     'https://smartstore.naver.com/shinsegaejeju01/category/ALL?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    # lovegrande: 제외 (스톤아일랜드 modelName이 색상코드만 들어와 model_id 품질 불량)
    # 'lovegrande': 'https://smartstore.naver.com/fsrs/category/2sWDwiTbo5sxFgR2EEnww_ALL_PRODUCT?...',
    'larlashoes': 'https://smartstore.naver.com/larlashoes/category/ALL?st=RECENT&dt=BIG_IMAGE&page=1&size=80&filters=oa',
    'thegrande':  'https://smartstore.naver.com/thegrande/category/96db6b37f40742028c3babc59a541669?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'upset':      'https://smartstore.naver.com/upset/category/43565b8658a8424ead3abf5a6f2b323e?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'luxlimit':   'https://smartstore.naver.com/luxlimit/category/618b4b385c0b41f7a81f8c0bd16e32e4?st=POPULAR&dt=BIG_IMAGE&page=1&size=80&filters=oa',
    'pano':       'https://smartstore.naver.com/panokorea/category/ALL?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'shinsegae':  'https://smartstore.naver.com/ssg01/category/ALL?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    # brand.naver.com 도메인 (fetch는 /n/v2/ 사용)
    'trendmecca': 'https://brand.naver.com/trendmecca/category/af9ae952a4054de0bc4762485e779b02?st=RECENT&dt=IMAGE&page=1&size=80',
}

PAGE_SIZE = 80

# 목록 JSON API 캡처 방식으로 Phase 1을 돌릴 스토어 (대형몰: 클릭 페이지네이션 + 응답 가로채기)
#  - 직접 API GET은 429로 막힘 → 브라우저가 스스로 부른 /categories/{cat}/products 응답을 가로챔
#  - 목록 JSON에 category가 있어 STORE_EXCLUDE_KEYWORDS(식품 등)를 목록 단계에서 제외 → 상세 fetch 절감
API_LIST_STORES = {'shinsegae'}


# =====================================================
# 스토어별 상품명 정리
# =====================================================

# 스토어별 제거 패턴 (정규식 리스트). 필요 시 이곳에 추가.
NAME_CLEANUP_PATTERNS = {
    'dmont': [
        r'^\s*디몬트\s+',           # 앞에 붙은 '디몬트 ' 접두사
    ],
    'tuttobene': [
        r'\[국내배송\]\s*',
        r'\[[0-9]+%중복쿠폰\]\s*',
    ],
    'maniaon':    [r'\s*매니아온\s*$', r'\[국내배송\]\s*'],       # 끝의 '매니아온' suffix + [국내배송]
    'unico':      [r'\s+\d{2}[A-Z]\s*$'],                         # 끝의 시즌코드 '26S' (unico 특정)
    'lovegrande': [r'^\s*\d{2}년\s*\d+월\d+째주[_\s]*', r'\[국내신상\]\s*'],  # 앞의 '26년 4월3째주' + [국내신상]
    'pano':       [r'\[국내신상\]\s*'],
    'larlashoes': [r'[\[(]\s*(?:국내매장판|국내매장|국냄매장판)\s*[\])]\s*'],           # 국내매장판/매장/오타 국냄매장판 (괄호무관, 브랜드명 제외)
    'luxlimit':   [r'[\[(]\s*(?:국내백화점|국내매장판|국내매장|국내당일|관부가세포함)\s*[\])]\s*'],  # 국내백화점/매장판/매장/당일/관부가세포함 (괄호무관, 브랜드명 제외)
}

# 전역 시즌코드 제거 (모든 스토어 상품명에 적용): 26SS, 25FW, 26S, 24SU 등
SEASON_PATTERN = r'(?i)\b2[0-9](?:SS|FW|SU|AW|WT|SP|S|F|W)\b\s*'

# 전역 국내판매 마커 제거 (모든 스토어): [국내...]/(국내...) 괄호토큰 + 관부가세포함 + 국냄매장판(오타).
#   국내백화점/매장판/매장/당일/판/배송/신상/매장발송 등 '국내~' 전부. 브랜드명은 국내로 시작 안 함 → 안전.
#   ★일본어 번역 변형(国内店舗版/国内正規品/韓国百貨店/国内当日 등)은 이 한글 마커를 지우면 원천 발생 안 함.
GLOBAL_DOMESTIC_PATTERN = r'[\[(]\s*(?:국내[^\]\)]*|관부가세포함|국냄매장판)\s*[\])]\s*'


# 스토어별 제외 키워드 (category_path 또는 product_name에 포함 시 수집 스킵)
STORE_EXCLUDE_KEYWORDS = {
    'luxlimit': {'category': ['향수'], 'name': ['향수']},
    'shinsegae': {'category': ['식품'], 'name': []},
}


def clean_product_name(source_site: str, name: str) -> str:
    """스토어별 prefix/불필요 태그 제거 + 전역 시즌코드 제거"""
    import re as _re
    if not name:
        return name
    patterns = NAME_CLEANUP_PATTERNS.get(source_site, [])
    cleaned = name
    for pat in patterns:
        cleaned = _re.sub(pat, '', cleaned)
    # 전역: 국내판매 마커 ([국내배송]/[국내매장판]/(국내백화점) 등) 제거 — 모든 스토어
    cleaned = _re.sub(GLOBAL_DOMESTIC_PATTERN, '', cleaned)
    # 전역: 시즌코드 (26SS, 25FW 등) 제거
    cleaned = _re.sub(SEASON_PATTERN, '', cleaned)
    # 중복 공백 정리
    cleaned = _re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


# =====================================================
# DB
# =====================================================

def get_all_products_url() -> Optional[str]:
    """현재 SOURCE_SITE의 '전체상품' URL 반환"""
    return STORE_ALL_PRODUCT_URLS.get(base.SOURCE_SITE)


def get_collected_ids(source: str) -> set:
    """이미 raw_scraped_data에 있는 mall_product_id — 인터리브 수집 재실행 시 이어가기(중복 스킵)용"""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT mall_product_id FROM raw_scraped_data WHERE source_site=:s"),
            {'s': source})
        return {str(r[0]) for r in rows}


def ensure_mall_brand(brand_name_en: str) -> None:
    """mall_brands에 raw_brand_name이 없으면 INSERT (is_active=NULL = 검수 대기, buyma_brand_id=NULL)"""
    if not brand_name_en:
        return
    with engine.begin() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM mall_brands
            WHERE mall_name = :site
              AND raw_brand_name = :name
            LIMIT 1
        """), {'site': base.SOURCE_SITE, 'name': brand_name_en}).fetchone()
        if exists:
            return
        conn.execute(text("""
            INSERT INTO mall_brands
              (mall_name, raw_brand_name, mall_brand_name_en,
               is_active, mapping_level, is_mapped)
            VALUES
              (:site, :raw, :en, NULL, 0, 0)
        """), {
            'site': base.SOURCE_SITE,
            'raw': brand_name_en,
            'en': brand_name_en,
        })
        logger.info(f"  [mall_brands] 신규 브랜드 INSERT: {brand_name_en}")


def ensure_mall_category(full_path: str, gender: str = 'unisex') -> None:
    """mall_categories에 full_path가 없으면 INSERT (buyma_category_id=NULL, is_active=NULL — 신규 대기)"""
    if not full_path:
        return
    with engine.begin() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM mall_categories
            WHERE mall_name = :site AND full_path = :path
            LIMIT 1
        """), {'site': base.SOURCE_SITE, 'path': full_path}).fetchone()
        if exists:
            return
        parts = [p.strip() for p in full_path.split(' > ')]
        depth1 = parts[0] if len(parts) > 0 else ''
        depth2 = parts[1] if len(parts) > 1 else ''
        depth3 = parts[2] if len(parts) > 2 else ''
        depth4 = parts[3] if len(parts) > 3 else ''
        # gender 자동 추출
        g = 'unisex'
        if any(k in full_path for k in ['여성', 'WOMEN']):
            g = 'female'
        elif any(k in full_path for k in ['남성', 'MEN']):
            g = 'male'
        elif 'KIDS' in full_path or '유아' in full_path or '키즈' in full_path:
            g = 'kids'
        conn.execute(text("""
            INSERT INTO mall_categories
              (mall_name, category_id, gender, depth1, depth2, depth3, depth4, full_path, buyma_category_id, is_active)
            VALUES
              (:site, :full_path, :gender, :d1, :d2, :d3, :d4, :full_path, NULL, NULL)
        """), {
            'site': base.SOURCE_SITE, 'full_path': full_path, 'gender': g,
            'd1': depth1, 'd2': depth2, 'd3': depth3, 'd4': depth4,
        })
        logger.info(f"  [mall_categories] 신규 카테고리 INSERT: {full_path}")


# =====================================================
# Phase 1: 전체상품 페이지 → channelProductNo
# =====================================================

async def collect_product_list_all(page, all_products_url: str,
                                    limit: Optional[int],
                                    skip_ids: set,
                                    count_only: bool = False) -> List[Dict]:
    """전체상품 URL 1개에서 페이지네이션 클릭 순회 → [{'product_no'}...]

    size=80 고정, filters=oa (품절 제외, 네이버 URL 파라미터)
    페이지 이동은 URL `?page=N`이 무시되므로 DOM 숫자 버튼 클릭 방식.
    """
    results = []
    seen = set()

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

    logger.info(f"\n>>> 전체상품 URL: {all_products_url}")
    page_num = 1
    # max_pages는 로그 표시용 추정치만. 실제 종료는 자연 종료 조건(빈 페이지/중복/다음 버튼 없음)에 일임.

    try:
        await page.goto(all_products_url, timeout=30000)
        await page.wait_for_load_state('domcontentloaded', timeout=10000)
        # 무거운 스토어(예: shinsegae 48만개)는 상품 렌더가 늦어 1초 고정 대기로는
        # (a) 상품 0개 오집계 (b) content() 네비게이션 중 크래시가 발생 → 상품 그리드가
        # 실제로 뜰 때까지 대기(최대 12초), 그 후 안전하게 캡챠 검사.
        try:
            await page.wait_for_selector(
                '[data-shp-contents-type="chnl_prod_no"], a[href*="/products/"]',
                timeout=12000,
            )
        except Exception:
            logger.warning("  상품 그리드 대기 타임아웃 — 계속 진행(빈 페이지일 수 있음)")
        await asyncio.sleep(1.0)
        try:
            page_title = await page.title()
        except Exception:
            page_title = ''
        if '보안' in page_title or 'captcha' in page_title.lower():
            logger.error(f"캡챠 감지! title={page_title!r} — --login으로 쿠키 갱신 필요")
            return results

        while page_num <= 10000:
            data = await page.evaluate(EXTRACT_JS)
            pnos = data.get('pnos') or []
            total = data.get('total')
            first_id = pnos[0] if pnos else None

            if page_num == 1 and total is not None:
                # 안내용 예상치만 (첫 페이지 실제 발견 개수 기반, break 조건 아님)
                est_size = len(pnos) or PAGE_SIZE
                est_pages = max(1, (total + est_size - 1) // est_size)
                logger.info(f"  총 {total}개 → 약 {est_pages}페이지 예상 (size={est_size}, 추정)")
                if count_only:
                    results.append({'_count': True, 'total': total})
                    return results

            new_cnt = 0
            page_seen_overlap = 0
            for pno in pnos:
                if pno in seen:
                    page_seen_overlap += 1
                    continue
                seen.add(pno)
                if pno in skip_ids:
                    continue
                results.append({'product_no': pno})
                new_cnt += 1

            sold_out = data.get('soldOutSkipped') or 0
            logger.info(f"  p{page_num}: {len(pnos)}개 발견, {new_cnt}개 신규, 품절스킵 {sold_out}개 (누적 {len(results)})")

            if limit and len(results) >= limit:
                return results[:limit]
            # max_pages 기반 종료 제거 — 자연 종료(빈 페이지/이전 페이지와 100% 중복/다음 버튼 없음)에 일임
            if not pnos or (page_num > 1 and page_seen_overlap == len(pnos)):
                break

            # 페이지 이동: 숫자 버튼 우선, 없으면 "다음"
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

    logger.info(f"<<< Phase 1 완료: {len(results)}개")
    return results


async def collect_product_list_api(page, all_products_url: str,
                                   limit: Optional[int],
                                   skip_ids: set,
                                   count_only: bool = False) -> List[Dict]:
    """대형몰(신세계 등) 전용 Phase 1 — 목록 JSON API '가로채기' 방식.

    페이지 버튼을 클릭하면 브라우저가 스스로 부르는
      GET /i/v2/channels/{uid}/categories/{cat}/products?...&page=N&pageSize=80
    응답(simpleProducts 80개)을 page.on('response')로 가로챈다. (직접 GET은 429)
    응답 JSON의 category.wholeCategoryName으로 STORE_EXCLUDE_KEYWORDS(식품 등)를
    목록 단계에서 제외 → 상세(Phase 2) fetch 수를 줄인다.

    ※ 로그인 필수(비로그인은 1페이지만 나옴). page=N 주소 직접이동은 무시되므로 클릭만 유효.
    """
    import re as _re
    results = []
    seen = set()
    captured = []  # 최근 목록 API Response 객체
    food_skipped = 0

    list_api_re = _re.compile(r'/categories/[^/]+/products(\?|$)')

    def on_resp(resp):
        u = resp.url
        if list_api_re.search(u) and 'product-benefits' not in u and resp.status == 200:
            captured.append(resp)

    page.on('response', on_resp)

    excl = STORE_EXCLUDE_KEYWORDS.get(base.SOURCE_SITE) or {}
    excl_cats = excl.get('category', [])

    def is_excluded_cat(catname: str) -> bool:
        return any(k in (catname or '') for k in excl_cats)

    DOM_JS = r'''() => {
        const ids=[]; const s=new Set();
        document.querySelectorAll('[data-shp-contents-type="chnl_prod_no"]').forEach(el=>{
            const id=el.getAttribute('data-shp-contents-id');
            if(id&&/^\d+$/.test(id)&&!s.has(id)){s.add(id);ids.push(id);}
        });
        let total=null;
        for(const el of document.querySelectorAll('span,strong,em')){
            const m=(el.textContent||'').trim().match(/총\s*([0-9,]+)\s*개/);
            if(m){total=parseInt(m[1].replace(/,/g,''),10);break;}
        }
        return {ids, total};
    }'''

    logger.info(f"\n>>> 전체상품 URL(API캡처): {all_products_url}")
    try:
        await page.goto(all_products_url, timeout=30000)
        await page.wait_for_selector('[data-shp-contents-type="chnl_prod_no"], a[href*="/products/"]', timeout=15000)
        await asyncio.sleep(1.5)
        try:
            title = await page.title()
        except Exception:
            title = ''
        if '보안' in title or 'captcha' in title.lower():
            logger.error(f"캡챠 감지! title={title!r} — --login으로 쿠키 갱신 필요")
            return results

        dom = await page.evaluate(DOM_JS)
        total = dom.get('total')
        if total:
            est_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            logger.info(f"  총 {total}개 → 약 {est_pages}페이지 (size={PAGE_SIZE})")
        if count_only:
            results.append({'_count': True, 'total': total})
            return results

        # 로그인 안 됐으면 1페이지만 나오고 클릭이 안 먹음 → 조기 경고 힌트
        if not total:
            logger.warning("  '총 N개' 표기 못읽음 — 로그인/렌더 확인 필요")

        # 페이지 1: DOM ids (목록 API는 초기 로드 땐 안 뜸/서버렌더). 카테고리 없어 식품은 Phase2 키워드가 백업.
        p1_ids = dom.get('ids', [])
        for pid in p1_ids:
            if pid in seen:
                continue
            seen.add(pid)
            if pid in skip_ids:
                continue
            results.append({'product_no': pid})
        logger.info(f"  p1: {len(p1_ids)}개(DOM) → 누적 {len(results)}")

        page_num = 1
        while True:
            if limit and len(results) >= limit:
                return results[:limit]

            next_num = page_num + 1
            captured.clear()
            btn = await page.query_selector(
                f'a[data-shp-contents-type="pgn"][data-shp-contents-id="{next_num}"]')
            if not btn:
                logger.info(f"  p{next_num} 버튼 없음 → 종료 (마지막 페이지)")
                break
            await btn.click()

            # 클릭으로 발사된 목록 API 응답 대기 (최대 ~12초)
            resp = None
            for _ in range(60):
                if captured:
                    resp = captured[-1]
                    break
                await asyncio.sleep(0.2)
            if resp is None:
                logger.warning(f"  p{next_num} 목록 API 응답 못잡음 → 종료")
                break
            try:
                data = await resp.json()
            except Exception:
                logger.warning(f"  p{next_num} JSON 파싱 실패 → 종료")
                break

            prods = data.get('simpleProducts') or []
            new_cnt = 0
            page_food = 0
            overlap = 0
            for it in prods:
                pid = str(it.get('id') or '')
                if not pid:
                    continue
                if pid in seen:
                    overlap += 1
                    continue
                seen.add(pid)
                catname = ((it.get('category') or {}).get('wholeCategoryName') or '')
                if is_excluded_cat(catname):
                    food_skipped += 1
                    page_food += 1
                    continue
                if pid in skip_ids:
                    continue
                results.append({'product_no': pid})
                new_cnt += 1

            logger.info(f"  p{next_num}: {len(prods)}개, 신규 {new_cnt}, 식품스킵 {page_food} "
                        f"(누적 {len(results)}, 식품누적 {food_skipped})")

            if not prods or (len(prods) > 0 and overlap == len(prods)):
                logger.info(f"  p{next_num} 전부 중복 → 종료")
                break

            page_num += 1
            await asyncio.sleep(random.uniform(*LIST_DELAY))
    except Exception as e:
        logger.warning(f"  Phase1(API) 오류: {e}")
    finally:
        try:
            page.remove_listener('response', on_resp)
        except Exception:
            pass

    logger.info(f"<<< Phase 1(API) 완료: {len(results)}개 (식품 {food_skipped}개 제외)")
    return results


async def collect_interleaved_api(page, all_products_url: str, limit: Optional[int],
                                  skip_ids: set, dry_run: bool, dump: bool = False):
    """대형몰(신세계 등) 전체상품 스윕 — 목록 API 캡처 + 페이지마다 상세 즉시 수집/저장(인터리브).

    Phase1(목록 전량) → Phase2(상세) 분리 방식은 48만개 스윕 시 '목록만 수 시간 → 중단 시 전량 손실'
    문제가 있어, 페이지 단위로 목록→상세→저장을 붙인다. 10개마다 저장되어 중단돼도 그때까지 남고,
    skip_ids(이미 수집/발행분)로 재실행 시 이어서 진행한다.
    반환: (저장수, 식품/제외수, first_row)
    """
    import re as _re
    list_api_re = _re.compile(r'/categories/[^/]+/products(\?|$)')
    captured = []

    def on_resp(resp):
        u = resp.url
        if list_api_re.search(u) and 'product-benefits' not in u and resp.status == 200:
            captured.append(resp)

    page.on('response', on_resp)

    # 상세는 별도 탭에서 가져온다 — 목록 탭(page)이 상품 페이지로 이동해버리면 다음 페이지 버튼을
    # 못 누른다. 목록 탭은 리스트에 머무르고, 상세 탭(detail_page)만 상품 페이지를 오간다.
    detail_page = await page.context.new_page()

    excl = STORE_EXCLUDE_KEYWORDS.get(base.SOURCE_SITE) or {}
    excl_cats = excl.get('category', [])
    excl_names = excl.get('name', [])
    is_brandstore = base.STORE_HOME.startswith('https://brand.naver.com')

    seen = set()
    batch = []
    saved = 0
    skipped_food = 0
    skipped_nomodel = 0
    resume_skipped = 0
    first_row = None
    brands_seen = set()
    cats_seen = set()

    DOM_JS = r'''() => {
        const ids=[]; const s=new Set();
        document.querySelectorAll('[data-shp-contents-type="chnl_prod_no"]').forEach(el=>{
            const id=el.getAttribute('data-shp-contents-id');
            if(id&&/^\d+$/.test(id)&&!s.has(id)){s.add(id);ids.push(id);}
        });
        let total=null;
        for(const el of document.querySelectorAll('span,strong,em')){
            const m=(el.textContent||'').trim().match(/총\s*([0-9,]+)\s*개/);
            if(m){total=parseInt(m[1].replace(/,/g,''),10);break;}
        }
        return {ids, total};
    }'''

    async def handle_products(entries, page_label):
        """entries: [{'id','cat'}]. 상세 수집+저장. limit 도달 시 True 반환."""
        nonlocal saved, skipped_food, skipped_nomodel, resume_skipped, first_row, batch
        for entry in entries:
            if limit and saved >= limit:
                return True
            pid = str(entry.get('id') or '')
            if not pid or pid in seen:
                continue
            seen.add(pid)
            catname = entry.get('cat') or ''
            # 목록 단계 식품/제외 (카테고리 기준) — 상세 안 열고 스킵
            if catname and any(k in catname for k in excl_cats):
                skipped_food += 1
                continue
            if pid in skip_ids:
                resume_skipped += 1
                continue
            # 상세
            product, benefits = None, None
            for attempt in range(DETAIL_MAX_RETRIES + 1):
                if is_brandstore:
                    product, benefits = await fetch_detail_brand_store(detail_page, pid)
                else:
                    product, benefits = await fetch_detail(detail_page, pid)
                if product:
                    break
                if attempt < DETAIL_MAX_RETRIES:
                    await asyncio.sleep(5 * (attempt + 1))
            # shinsegae 브랜드 우선
            if base.SOURCE_SITE == 'shinsegae' and product:
                nsi = product.get('naverShoppingSearchInfo')
                if isinstance(nsi, dict) and nsi.get('brandName'):
                    nsi['manufacturerName'] = ''
            row = map_to_row(product, benefits, '', pid)
            if not row:
                skipped_nomodel += 1
                await asyncio.sleep(random.uniform(*DETAIL_DELAY))
                continue
            row['product_name'] = clean_product_name(base.SOURCE_SITE, row.get('product_name', ''))
            row['p_name_full'] = clean_product_name(base.SOURCE_SITE, row.get('p_name_full', ''))
            # 컬럼 길이 초과 방지 — 사이즈옵션이 model_id로 잘못 들어가는 등 초장문 값이 저장을 크래시시킴
            # (varchar: model_id/brand_name_en 100, product_name/category_path 255). 한 줄 때문에 전체 수집이 죽던 문제.
            for _f, _lim in (('model_id', 100), ('brand_name_en', 100),
                             ('product_name', 255), ('category_path', 255)):
                _v = row.get(_f)
                if _v and len(_v) > _lim:
                    row[_f] = _v[:_lim]
            # 상세 단계 제외 키워드 (카테고리/이름) — 목록에 카테고리 없던 p1 백업
            cat = row.get('category_path') or ''
            nm = (row.get('product_name') or '') + ' ' + (row.get('p_name_full') or '')
            if any(k in cat for k in excl_cats) or any(k in nm for k in excl_names):
                skipped_food += 1
                await asyncio.sleep(random.uniform(*DETAIL_DELAY))
                continue
            b = (row.get('brand_name_en') or '').strip()
            if b and b not in brands_seen:
                brands_seen.add(b)
                if not dry_run:
                    ensure_mall_brand(b)
            if cat and cat not in cats_seen:
                cats_seen.add(cat)
                if not dry_run:
                    ensure_mall_category(cat)
            batch.append(row)
            saved += 1
            if first_row is None:
                first_row = row
            raw = json.loads(row['raw_json_data'])
            logger.info(f"  [{saved}] {page_label} {row['brand_name_en']} | {row['model_id']} | "
                        f"₩{row['raw_price']:,} | img:{len(raw['images'])} opt:{len(raw['options'])}")
            if not dry_run and len(batch) >= 10:
                save_rows(batch)
                batch = []
            await asyncio.sleep(random.uniform(*DETAIL_DELAY))
        return False

    logger.info(f"\n>>> 전체상품 인터리브 수집: {all_products_url}")
    try:
        await page.goto(all_products_url, timeout=30000)
        await page.wait_for_selector('[data-shp-contents-type="chnl_prod_no"], a[href*="/products/"]', timeout=15000)
        await asyncio.sleep(1.5)
        try:
            title = await page.title()
        except Exception:
            title = ''
        if '보안' in title or 'captcha' in title.lower():
            logger.error(f"캡챠 감지! title={title!r} — --login으로 쿠키 갱신 필요")
            return saved, skipped_food, first_row

        dom = await page.evaluate(DOM_JS)
        total = dom.get('total')
        if total:
            logger.info(f"  총 {total}개 (약 {(total + PAGE_SIZE - 1)//PAGE_SIZE}페이지). 10개마다 저장·재실행 이어가기.")

        done = await handle_products([{'id': i, 'cat': ''} for i in dom.get('ids', [])], 'p1')
        page_num = 1
        while not done:
            next_num = page_num + 1
            captured.clear()
            # 다음 페이지 버튼 찾기 — 페이지 넘긴 직후 번호버튼 재렌더가 늦어(특히 10단위 블록 경계)
            # 한 번에 못 찾는 경우가 있어, 하단 스크롤 + 재시도로 렌더지연을 흡수한다.
            sel = f'a[data-shp-contents-type="pgn"][data-shp-contents-id="{next_num}"]'
            btn = None
            for _try in range(6):
                btn = await page.query_selector(sel)
                if btn:
                    break
                try:
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                except Exception:
                    pass
                await asyncio.sleep(1.0)
            if not btn:
                # 진단용: 멈추는 순간 실제로 어떤 페이지버튼들이 떠있는지 기록(렌더지연 vs 진짜 끝 판별)
                try:
                    pgn = await page.evaluate(
                        '''() => [...document.querySelectorAll('[data-shp-contents-type="pgn"]')]'''
                        '''.map(a => (a.getAttribute("data-shp-contents-id")||"")+":"+(a.textContent||"").trim()).join(" ")''')
                except Exception:
                    pgn = "(덤프실패)"
                logger.info(f"  p{next_num} 버튼 없음(6회 재시도) → 종료. 현재 페이지버튼=[{pgn}]")
                break
            await btn.click()
            # 목록 응답 받기 — 빈 페이지/무응답이 일시적(순단·레이트리밋)일 수 있어 재요청 반복.
            # (482 '버튼없음'처럼 첫 실패에 조기종료하던 실수 방지) 5회 재요청해도 비면 진짜 끝으로 판단.
            prods = None
            for _attempt in range(5):
                resp = None
                for _ in range(60):
                    if captured:
                        resp = captured[-1]
                        break
                    await asyncio.sleep(0.2)
                if resp is not None:
                    try:
                        p = (await resp.json()).get('simpleProducts') or []
                    except Exception:
                        p = []
                    if p:
                        prods = p
                        break
                # 빈/무응답 → 대기 후 같은 페이지 재요청: 앞 페이지 클릭 후 다시 이 페이지로(확실한 재로딩)
                logger.info(f"  p{next_num} 빈/무응답 — 재시도 {_attempt+1}/5")
                await asyncio.sleep(3.0 + _attempt * 2)
                prev_btn = await page.query_selector(
                    f'a[data-shp-contents-type="pgn"][data-shp-contents-id="{max(1, next_num-1)}"]')
                if prev_btn:
                    try:
                        await prev_btn.click()
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass
                captured.clear()  # 이 페이지 응답만 잡히도록
                cur_btn = await page.query_selector(
                    f'a[data-shp-contents-type="pgn"][data-shp-contents-id="{next_num}"]')
                if cur_btn:
                    try:
                        await cur_btn.click()
                    except Exception:
                        pass
            if not prods:
                try:
                    pgn = await page.evaluate(
                        '''() => [...document.querySelectorAll('[data-shp-contents-type="pgn"]')]'''
                        '''.map(a => (a.getAttribute("data-shp-contents-id")||"")+":"+(a.textContent||"").trim()).join(" ")''')
                except Exception:
                    pgn = "(덤프실패)"
                logger.info(f"  p{next_num} 상품 없음(5회 재요청 후) → 종료. 현재 페이지버튼=[{pgn}]")
                break
            entries = [{'id': it.get('id'),
                        'cat': ((it.get('category') or {}).get('wholeCategoryName') or '')}
                       for it in prods]
            page_num += 1
            saved_before = saved
            done = await handle_products(entries, f'p{page_num}')
            if page_num % 20 == 0:
                logger.info(f"  ··· 진행 {page_num}p | 저장 {saved} | 식품/제외 {skipped_food} | "
                            f"이어가기스킵 {resume_skipped} | 모델없음 {skipped_nomodel}")
            # 이 페이지에서 새로 저장한 게 있으면(=상세 열었으면) 네이버 딜레이, 전부 스킵(이미 수집분)이면
            # 딜레이 없이 바로 다음 페이지 → 재실행 시 '이미 받은 페이지 다시 넘기기'가 훨씬 빨라짐.
            if saved > saved_before:
                await asyncio.sleep(random.uniform(*LIST_DELAY))
    except Exception as e:
        logger.warning(f"  인터리브 수집 오류: {e}")
    finally:
        try:
            page.remove_listener('response', on_resp)
        except Exception:
            pass
        try:
            await detail_page.close()
        except Exception:
            pass
        if batch and not dry_run:
            save_rows(batch)

    logger.info(f"<<< 인터리브 완료: 저장 {saved} | 식품/제외 {skipped_food} | "
                f"이어가기스킵 {resume_skipped} | 모델없음 {skipped_nomodel} | "
                f"신규브랜드 {len(brands_seen)} 신규카테고리 {len(cats_seen)}")
    if dump and first_row:
        logger.info("=== 첫 행 덤프 ===")
        for k, v in first_row.items():
            if k == 'raw_json_data':
                print(json.dumps(json.loads(v), ensure_ascii=False, indent=2))
            else:
                logger.info(f"{k}: {v}")
    return saved, skipped_food, first_row


# =====================================================
# 오케스트레이션
# =====================================================

def build_category_url(category_id: str) -> str:
    """STORE_HOME 기준 특정 카테고리 전체상품 URL (size=80, 품절제외). 대/중/소분류 ID 모두 동일.
    전체상품(ALL)은 ~1250p(≈10만)에서 막히지만, 카테고리는 각자 창이 있어 나눠 담으면 더 커버됨."""
    home = base.STORE_HOME.rstrip('/')
    return f"{home}/category/{category_id}?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa"


async def run(limit: Optional[int], skip_existing: bool, dry_run: bool,
              dump: bool = False, count_only: bool = False,
              mall_product_id: Optional[str] = None,
              categories: Optional[List[str]] = None):
    from playwright.async_api import async_playwright

    # mall_product_id 지정 시 Phase 1 스킵하고 그 1건만 처리 (검증/디버깅용)
    if mall_product_id:
        logger.info(f"단일 상품 모드: mall_product_id={mall_product_id} (Phase 1 스킵)")
        all_products_url = None
    else:
        all_products_url = get_all_products_url()
        if not all_products_url:
            logger.error(f"STORE_ALL_PRODUCT_URLS에 '{base.SOURCE_SITE}' 미등록. 코드에 URL 추가 필요.")
            return

    skip_ids = get_published_product_ids() if skip_existing else set()
    if skip_existing:
        logger.info(f"기존 수집: {len(skip_ids)}개 (스킵)")

    UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')

    async with async_playwright() as p:
        # === 대형몰(API_LIST_STORES) 인터리브 스윕 — 카테고리마다 크롬을 통째로 새로 켜서 격리 ===
        # 컨텍스트만 새로 여는 걸론 부족(크롬 프로세스가 몇 시간 뒤 맛이 가서 list.remove 에러로
        # 뒤 카테고리가 줄줄이 실패). 브라우저 자체를 카테고리마다 재기동해야 완전 격리됨.
        if (not mall_product_id) and (not count_only) and (base.SOURCE_SITE in API_LIST_STORES):
            resume_ids = get_collected_ids(base.SOURCE_SITE)
            if resume_ids:
                logger.info(f"재실행 이어가기: 이미 수집된 {len(resume_ids)}개는 건너뜀")
            all_skip = skip_ids | resume_ids
            # 수집 대상: --category 지정 시 그 카테고리들, 아니면 전체상품(ALL)
            if categories:
                targets = [(cid, build_category_url(cid)) for cid in categories]
            else:
                targets = [('ALL', all_products_url)]
            grand = 0
            grand_food = 0
            for ci, (cid, url) in enumerate(targets, 1):
                logger.info(f"\n== [{ci}/{len(targets)}] 카테고리 '{cid}' 인터리브 수집 ==")
                cat_browser = await p.chromium.launch(headless=False)
                try:
                    cat_ctx = await cat_browser.new_context(
                        viewport={'width': 1280, 'height': 900}, locale='ko-KR', user_agent=UA)
                    if os.path.exists(COOKIE_FILE):
                        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                            await cat_ctx.add_cookies(json.load(f))
                    cat_page = await cat_ctx.new_page()
                    saved, food, _first = await collect_interleaved_api(
                        cat_page, url, limit, all_skip, dry_run, dump)
                except Exception as ex:
                    logger.warning(f"  카테고리 '{cid}' 수집 오류(다음 카테고리 계속): {str(ex)[:100]}")
                    saved, food = 0, 0
                finally:
                    try:
                        await cat_browser.close()  # 크롬 프로세스 통째 종료 → 다음 카테고리는 새 크롬
                    except Exception:
                        pass
                grand += saved
                grand_food += food
                # 다음 카테고리에서 방금 저장분도 건너뛰도록 스킵셋 갱신
                if not dry_run and saved:
                    all_skip = all_skip | get_collected_ids(base.SOURCE_SITE)
            logger.info(f"\n== 전체 수집 완료: 저장 {grand}개 (식품/제외 {grand_food}개) ==")
            return

        # === 그 외 경로(단일상품/count/비-API 스토어): 공용 브라우저 1개 ===
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900}, locale='ko-KR', user_agent=UA)
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                await context.add_cookies(json.load(f))
            logger.info(f"쿠키 로드: {COOKIE_FILE}")
        page = await context.new_page()

        if mall_product_id:
            # 단일 상품 모드: Phase 1 스킵
            items = [{'product_no': str(mall_product_id)}]
            logger.info(f"\n== Phase 1 스킵: 지정된 mall_product_id 1건 처리 ==")
        else:
            logger.info("\n== Phase 1: 리스트 수집 (전체상품 URL) ==")
            if base.SOURCE_SITE in API_LIST_STORES:
                # 대형몰: 목록 JSON API 가로채기 방식 (클릭 페이지네이션 + 식품 목록단계 제외)
                items = await collect_product_list_api(page, all_products_url, limit, skip_ids,
                                                       count_only=count_only)
            else:
                items = await collect_product_list_all(page, all_products_url, limit, skip_ids,
                                                        count_only=count_only)

        if count_only:
            await browser.close()
            for it in items:
                if it.get('_count'):
                    logger.info(f"총 상품 수: {it['total']}개")
            return

        logger.info(f"수집 대상: {len(items)}개")

        if not items:
            await browser.close()
            return

        logger.info("\n== Phase 2: 상세 수집 ==")
        rows = []
        first_row = None      # dump용 (batch save로 rows 비워져도 보존)
        total_collected = 0   # 누적 수집 카운트
        total = len(items)
        new_brands_seen = set()
        new_categories_seen = set()
        for i, item in enumerate(items, 1):
            pno = item['product_no']
            product, benefits = None, None
            # 도메인별 fetch 분기: brand.naver.com은 /n/v2/, smartstore.naver.com은 /i/v2/
            is_brandstore = base.STORE_HOME.startswith('https://brand.naver.com')
            for attempt in range(DETAIL_MAX_RETRIES + 1):
                if is_brandstore:
                    product, benefits = await fetch_detail_brand_store(page, pno)
                else:
                    product, benefits = await fetch_detail(page, pno)
                if product:
                    break
                if attempt < DETAIL_MAX_RETRIES:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"[{i}/{total}] {pno} 재시도 {attempt + 1} — {wait}초 대기")
                    await asyncio.sleep(wait)

            # unico pre-clean: 시즌코드 제거 + modelName 비우기 → map_to_row가 name에서 멀티토큰 추출하도록
            if base.SOURCE_SITE == 'unico' and product:
                import re as _re
                if product.get('name'):
                    product['name'] = _re.sub(r'\s+\d{2}[A-Z]\s*$', '', product['name']).strip()
                product['modelName'] = ''
                if isinstance(product.get('naverShoppingSearchInfo'), dict):
                    product['naverShoppingSearchInfo']['modelName'] = ''

            # upset pre-clean: modelName 비우기 → extract_model_from_name이 product_name의 괄호 안 값 추출
            # 예: "크록스 REALTREE ECHO CLOG (208232-2Y2)" → "208232-2Y2"
            # 예: "뉴발란스 740 화이트 실버 (U740WN2 NBPDGS102S)" → "U740WN2 NBPDGS102S"
            if base.SOURCE_SITE == 'upset' and product:
                product['modelName'] = ''
                if isinstance(product.get('naverShoppingSearchInfo'), dict):
                    product['naverShoppingSearchInfo']['modelName'] = ''

            # shinsegae: 브랜드 우선 규칙 — brandName이 있으면 그걸 사용.
            # 백화점은 제조사가 모회사(F&F, 삼성물산 등)라 brandName이 실제 브랜드다.
            # map_to_row는 manufacturerName을 우선하므로, brandName이 있을 때 manufacturerName을
            # 비워 brandName으로 폴백시킨다. 예: 제조사 F&F / 브랜드 디스커버리 → 디스커버리
            if base.SOURCE_SITE == 'shinsegae' and product:
                nsi = product.get('naverShoppingSearchInfo')
                if isinstance(nsi, dict) and nsi.get('brandName'):
                    nsi['manufacturerName'] = ''

            # 브랜드는 상품 상세에서 추출 (brand_from_list='')
            row = map_to_row(product, benefits, '', pno)
            if not row:
                if product:
                    logger.info(f"[{i}/{total}] {pno} 스킵: 모델번호 없음 (모음전 추정)")
                else:
                    logger.warning(f"[{i}/{total}] {pno} 매핑 실패")
                continue

            # 스토어별 상품명 정리
            row['product_name'] = clean_product_name(base.SOURCE_SITE, row.get('product_name', ''))
            row['p_name_full'] = clean_product_name(base.SOURCE_SITE, row.get('p_name_full', ''))

            # unico 전용: 정리된 상품명에서 멀티토큰 모델번호 재추출 (pattern 1 greedy 우회)
            if base.SOURCE_SITE == 'unico':
                import re as _re
                nm = (row.get('product_name') or '').strip()
                m = _re.search(r'([A-Z0-9]{2,}(?:\s+[A-Z0-9][A-Z0-9\-./]{1,})+)\s*$', nm)
                if m and not _re.search(r'[가-힣]', m.group(1)):
                    multi = m.group(1).strip()
                    row['model_id'] = multi
                    # raw_json_data의 model_name도 동기화
                    try:
                        raw_j = json.loads(row['raw_json_data'])
                        raw_j['model_name'] = multi
                        row['raw_json_data'] = json.dumps(raw_j, ensure_ascii=False)
                    except Exception:
                        pass

            # 스토어별 제외 키워드 필터 (category_path 또는 product_name 포함 시 스킵)
            excl = STORE_EXCLUDE_KEYWORDS.get(base.SOURCE_SITE)
            if excl:
                cat = row.get('category_path') or ''
                nm = (row.get('product_name') or '') + ' ' + (row.get('p_name_full') or '')
                if any(k in cat for k in excl.get('category', [])) or any(k in nm for k in excl.get('name', [])):
                    logger.info(f"[{i}/{total}] {pno} 스킵: {base.SOURCE_SITE} 제외 키워드 매칭")
                    continue

            # 신규 브랜드/카테고리 auto-insert
            b = (row.get('brand_name_en') or '').strip()
            if b and b not in new_brands_seen:
                new_brands_seen.add(b)
                if not dry_run:
                    ensure_mall_brand(b)

            cat = (row.get('category_path') or '').strip()
            if cat and cat not in new_categories_seen:
                new_categories_seen.add(cat)
                if not dry_run:
                    ensure_mall_category(cat)

            rows.append(row)
            total_collected += 1
            if first_row is None:
                first_row = row
            raw = json.loads(row['raw_json_data'])
            logger.info(
                f"[{i}/{total}] {row['brand_name_en']} | {row['model_id']} | "
                f"₩{row['raw_price']:,} (정가 ₩{row['original_price']:,}) | "
                f"img:{len(raw['images'])} | opt:{len(raw['options'])}"
            )
            # batch save (10개마다 — 긴 작업 중간 손실 방지)
            if not dry_run and len(rows) >= 10:
                save_rows(rows)
                rows = []
            await asyncio.sleep(random.uniform(*DETAIL_DELAY))

        await browser.close()

    logger.info(f"\n== 수집 완료: {total_collected}/{len(items)} ==")
    logger.info(f"신규 발견 브랜드 수: {len(new_brands_seen)}")
    logger.info(f"신규 발견 카테고리 수: {len(new_categories_seen)}")

    if dump and first_row:
        logger.info("\n=== 첫 행 전체 덤프 ===")
        r0 = first_row
        for k, v in r0.items():
            if k == 'raw_json_data':
                logger.info(f"{k}:")
                print(json.dumps(json.loads(v), ensure_ascii=False, indent=2))
            else:
                logger.info(f"{k}: {v}")

    # 마지막 남은 batch
    if rows and not dry_run:
        save_rows(rows)
    elif dry_run:
        logger.info("(DRY-RUN: DB 저장 생략)")


def main():
    parser = argparse.ArgumentParser(description='네이버 스마트스토어 전체상품 페이지 기반 수집기')
    parser.add_argument('--source', type=str, required=True,
                        help='수집 대상 스토어 예: dmont, tuttobene, thefactor2')
    parser.add_argument('--limit', type=int, help='최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품만 스킵 (신규+미등록 상품 수집)')
    parser.add_argument('--login', action='store_true', help='네이버 로그인 → 쿠키 갱신')
    parser.add_argument('--dump', action='store_true', help='수집된 첫 행의 raw_json_data 전체 출력')
    parser.add_argument('--count', action='store_true', help='Phase 1만 실행 — 총 상품 수 집계')
    parser.add_argument('--mall-product-id', type=str, default=None,
                        help='Phase 1 스킵하고 특정 mall_product_id(channelProductNo) 1건만 fetch (검증/디버깅용)')
    parser.add_argument('--category', type=str, default=None,
                        help='특정 카테고리ID만 수집(콤마로 여러개). 예 50000000=패션의류,50000001=패션잡화. '
                             '미지정 시 전체상품(ALL). 전체상품이 10만에서 막혀 더 받을 때 사용')
    args = parser.parse_args()

    set_source(args.source)

    if args.login:
        asyncio.run(login_and_save_cookies())
        return

    logger.info(f"=== {base.SOURCE_SITE} 전체상품 수집 "
                f"(Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'}) ===")
    logger.info(f"STORE_HOME: {base.STORE_HOME}")
    cats = [c.strip() for c in args.category.split(',') if c.strip()] if args.category else None
    asyncio.run(run(args.limit, args.skip_existing, args.dry_run, args.dump, args.count,
                    mall_product_id=args.mall_product_id, categories=cats))


if __name__ == '__main__':
    main()
