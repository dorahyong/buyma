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
  - set_source, fetch_detail, map_to_row, save_rows, get_existing_product_ids
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
    get_existing_product_ids,
    save_rows,
    fetch_detail,
    map_to_row,
    absolute_url,
    COOKIE_FILE,
    DETAIL_MAX_RETRIES,
)
import premiumsneakers_collector as base

# category_collector 전용 딜레이 (브랜드 collector의 값과 독립) — 보수적 단축
LIST_DELAY = (0.3, 0.8)
DETAIL_DELAY = (0.8, 1.2)

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
}

PAGE_SIZE = 80


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
    'larlashoes': [r'\(국내매장판\)\s*'],
    'luxlimit':   [r'\[국내당일\]\s*'],
}

# 전역 시즌코드 제거 (모든 스토어 상품명에 적용): 26SS, 25FW, 26S, 24SU 등
SEASON_PATTERN = r'(?i)\b2[0-9](?:SS|FW|SU|AW|WT|SP|S|F|W)\b\s*'


# 스토어별 제외 키워드 (category_path 또는 product_name에 포함 시 수집 스킵)
STORE_EXCLUDE_KEYWORDS = {
    'luxlimit': {'category': ['향수'], 'name': ['향수']},
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


def ensure_mall_brand(brand_name_en: str) -> None:
    """mall_brands에 brand_name_en이 없으면 INSERT (is_active=1, buyma_brand_id=NULL)"""
    if not brand_name_en:
        return
    with engine.begin() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM mall_brands
            WHERE mall_name = :site
              AND UPPER(mall_brand_name_en) = UPPER(:en)
            LIMIT 1
        """), {'site': base.SOURCE_SITE, 'en': brand_name_en}).fetchone()
        if exists:
            return
        conn.execute(text("""
            INSERT INTO mall_brands
              (mall_name, mall_brand_name_en, is_active, mapping_level, is_mapped)
            VALUES
              (:site, :en, 1, 0, 0)
        """), {'site': base.SOURCE_SITE, 'en': brand_name_en})
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
    max_pages = None

    try:
        await page.goto(all_products_url, timeout=30000)
        await page.wait_for_load_state('domcontentloaded', timeout=10000)
        await asyncio.sleep(1.0)
        if '보안 확인' in await page.content():
            logger.error("캡챠 감지! --login으로 쿠키 갱신 필요")
            return results

        while page_num <= 10000:
            data = await page.evaluate(EXTRACT_JS)
            pnos = data.get('pnos') or []
            total = data.get('total')
            first_id = pnos[0] if pnos else None

            if page_num == 1 and total is not None:
                max_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                logger.info(f"  총 {total}개 → {max_pages}페이지 예상 (size={PAGE_SIZE})")
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
            if max_pages is not None and page_num >= max_pages:
                break
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


# =====================================================
# 오케스트레이션
# =====================================================

async def run(limit: Optional[int], skip_existing: bool, dry_run: bool,
              dump: bool = False, count_only: bool = False):
    from playwright.async_api import async_playwright

    all_products_url = get_all_products_url()
    if not all_products_url:
        logger.error(f"STORE_ALL_PRODUCT_URLS에 '{base.SOURCE_SITE}' 미등록. 코드에 URL 추가 필요.")
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

        logger.info("\n== Phase 1: 리스트 수집 (전체상품 URL) ==")
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
        total = len(items)
        new_brands_seen = set()
        new_categories_seen = set()
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
            raw = json.loads(row['raw_json_data'])
            logger.info(
                f"[{i}/{total}] {row['brand_name_en']} | {row['model_id']} | "
                f"₩{row['raw_price']:,} (정가 ₩{row['original_price']:,}) | "
                f"img:{len(raw['images'])} | opt:{len(raw['options'])}"
            )
            await asyncio.sleep(random.uniform(*DETAIL_DELAY))

        await browser.close()

    logger.info(f"\n== 수집 완료: {len(rows)}/{len(items)} ==")
    logger.info(f"신규 발견 브랜드 수: {len(new_brands_seen)}")
    logger.info(f"신규 발견 카테고리 수: {len(new_categories_seen)}")

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
    parser = argparse.ArgumentParser(description='네이버 스마트스토어 전체상품 페이지 기반 수집기')
    parser.add_argument('--source', type=str, required=True,
                        help='수집 대상 스토어 예: dmont, tuttobene, thefactor2')
    parser.add_argument('--limit', type=int, help='최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='기존 수집 상품 스킵')
    parser.add_argument('--login', action='store_true', help='네이버 로그인 → 쿠키 갱신')
    parser.add_argument('--dump', action='store_true', help='수집된 첫 행의 raw_json_data 전체 출력')
    parser.add_argument('--count', action='store_true', help='Phase 1만 실행 — 총 상품 수 집계')
    args = parser.parse_args()

    set_source(args.source)

    if args.login:
        asyncio.run(login_and_save_cookies())
        return

    logger.info(f"=== {base.SOURCE_SITE} 전체상품 수집 "
                f"(Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'}) ===")
    logger.info(f"STORE_HOME: {base.STORE_HOME}")
    asyncio.run(run(args.limit, args.skip_existing, args.dry_run, args.dump, args.count))


if __name__ == '__main__':
    main()
