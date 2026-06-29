# -*- coding: utf-8 -*-
"""
네이버 스마트스토어 브랜드/카테고리 스캔 → mall_brands, mall_categories INSERT

Playwright로 스토어 메인 페이지의 네비게이션 메뉴를 열어서
브랜드(depth=2)와 카테고리(depth=3)를 추출한 뒤 DB에 저장.

사용법:
    python scan_store_brands.py --store premiumsneakers --dry-run   # 미리보기
    python scan_store_brands.py --store premiumsneakers             # 실행
    python scan_store_brands.py --store premiumsneakers --insert-site  # mall_sites도 INSERT

작성일: 2026-04-09
"""

import os
import sys
import io
import re
import json
import asyncio
import argparse
import logging
from datetime import datetime
from typing import Dict, List

import pymysql
from dotenv import load_dotenv

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S'))
logger.addHandler(_handler)

DB_CONFIG = {
    'host': os.getenv('DB_HOST', '54.180.248.182'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'block'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': os.getenv('DB_NAME', 'buyma'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}

# 스토어 설정 (naver_smartstore_collector.py와 동일)
STORES = {
    'premiumsneakers': {
        'name': '프리미엄스니커즈',
        'source_site': 'premiumsneakers',
        'url': 'https://smartstore.naver.com/premiumsneakers',
        'type': 'smartstore',
    },
    'fabstyle': {
        'name': '팹스타일',
        'source_site': 'fabstyle',
        'url': 'https://smartstore.naver.com/fabstyle',
        'type': 'smartstore',
        'category_roots': {'MEN', 'WOMEN', 'BAG', 'ACC'},
        'brand_parent': 'BRAND',  # BRAND → 자음그룹 → 실제 브랜드 (2단계 호버)
    },
    'loutique': {
        'name': '루티크',
        'source_site': 'loutique',
        'url': 'https://smartstore.naver.com/loutique',
        'type': 'smartstore',
        'brands_at_top': True,  # 최상위 메뉴 자체가 브랜드 (호버 없이 수집)
        'brand_range': ('Thom Browne', 'Balenciaga'),  # 메뉴 순서상 시작..끝 범위
    },
    'carpi': {
        'name': '까르피',
        'source_site': 'carpi',
        'url': 'https://brand.naver.com/carpi',
        'type': 'brandstore',
        'category_roots': {'MAN', 'WOMAN', 'ACC', 'KIDS'},
        'brand_parent': 'BRAND',  # BRAND → A/B/... → 실제 브랜드 (2단계 호버)
    },
    'luxboy': {
        'name': '럭스보이',
        'source_site': 'luxboy',
        'url': 'https://brand.naver.com/luxboy',
        'type': 'brandstore',
    },
    'dmont': {
        'name': '디몬트',
        'source_site': 'dmont',
        'url': 'https://smartstore.naver.com/dmont',
        'type': 'smartstore',
        'category_roots': {'패션의류', '패션잡화', '스포츠/레저', '출산/육아', '생활/건강'},
    },
    't1global': {
        'name': '티원글로벌',
        'source_site': 't1global',
        'url': 'https://smartstore.naver.com/t1global',
        'type': 'smartstore',
        'brands_at_top': True,
        'brand_range': ('아디다스', '컨버스 | 반스'),
    },
    'tutto-bene': {
        'name': '뚜또베네',
        'source_site': 'tuttobene',
        'url': 'https://smartstore.naver.com/tutto-bene',
        'type': 'smartstore',
        # 최상위 '패션의류/패션잡화'는 껍데기 → 그 자식(남성의류 등)을 카테고리 root로 사용
        'category_root_parents': {'패션의류', '패션잡화'},
    },
    'joharistore': {
        'name': '조하리스토어',
        'source_site': 'joharistore',
        'url': 'https://brand.naver.com/joharistore',
        'type': 'brandstore',
        'brand_parent_prefix': 'Brand [',  # "Brand [A~Z]", "Brand [ㄱ]" ... 각 top이 브랜드 그룹
    },
    'thefactor2': {
        'name': '논현더팩토리',
        'source_site': 'thefactor2',
        'url': 'https://smartstore.naver.com/thefactor2',
        'type': 'smartstore',
        'category_roots': {'남성의류', '여성의류', '신발', '가방', '패션소품', '주얼리', '키즈'},
    },
    'vvano': {
        'name': '비비아노',
        'source_site': 'vvano',
        'url': 'https://smartstore.naver.com/vvano',
        'type': 'smartstore',
        'brand_prefix': 'Brand ',  # "Brand A", "Brand B" ... top menu만 호버해서 자식=브랜드로 수집
    },
    'veroshopmall': {
        'name': '베로샵',
        'source_site': 'veroshopmall',
        'url': 'https://smartstore.naver.com/veroshopmall',
        'type': 'smartstore',
        # #ㄱ ~ #ㅎ 자음 그룹이 각각 브랜드 부모 (팹스타일 BRAND_PARENT의 다중 버전)
        'brand_parents': ['#ㄱ', '#ㄴ', '#ㄷ', '#ㄹ', '#ㅁ', '#ㅂ',
                          '#ㅅ', '#ㅇ', '#ㅈ-ㅌ', '#ㅍ', '#ㅎ'],
    },
}

COOKIE_FILE = os.path.join(os.path.dirname(__file__), 'naver_cookies.json')


# =====================================================
# Phase 1: Playwright로 네비게이션 추출
# =====================================================

async def scan_navigation(store_id: str, store_config: dict) -> Dict[str, List[Dict]]:
    """스토어 네비게이션 메뉴에서 브랜드/카테고리 추출"""
    from playwright.async_api import async_playwright

    store_url = store_config['url']
    result = {'brands': [], 'categories': []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            locale='ko-KR',
        )

        # 쿠키 로드
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)

        page = await context.new_page()
        logger.info(f"스토어 페이지 로딩: {store_url}")
        await page.goto(store_url, timeout=30000)
        await page.wait_for_load_state('networkidle', timeout=15000)
        await asyncio.sleep(2)

        # 보안 확인 체크
        content = await page.content()
        if '보안 확인' in content:
            logger.error("캡챠 감지! naver_smartstore_collector.py --login으로 쿠키 갱신 필요")
            await browser.close()
            return result

        # 1차: 최상위 메뉴 항목 수집
        top_menus = await page.evaluate('''() => {
            const items = [];
            document.querySelectorAll('li[data-category-menu-key]').forEach(li => {
                const a = li.querySelector('a[data-shp-contents-dtl]');
                if (!a) return;
                try {
                    const dtl = JSON.parse(a.getAttribute('data-shp-contents-dtl'));
                    const depthObj = dtl.find(d => d.key === 'depth');
                    if (depthObj) {
                        items.push({
                            depth: parseInt(depthObj.value),
                            text: dtl.find(d => d.key === 'txt')?.value || '',
                            key: li.getAttribute('data-category-menu-key'),
                        });
                    }
                } catch(e) {}
            });
            return items;
        }''')
        logger.info(f"최상위 메뉴: {len(top_menus)}개")

        # 카테고리 1depth 허용 목록 (스토어별 override 가능)
        CATEGORY_ROOTS = store_config.get('category_roots', {'남성', '여성', '키즈', '가방', '지갑', '신발', '패션소품'})
        BRAND_PARENT = store_config.get('brand_parent')  # 예: 'BRAND' → 자음그룹 → 실제 브랜드
        BRAND_PARENTS = store_config.get('brand_parents')  # 다중 부모 (예: ['#ㄱ','#ㄴ',...]) — 각 top이 브랜드 그룹
        BRAND_PARENT_PREFIX = store_config.get('brand_parent_prefix')  # prefix로 시작하는 top 전부 브랜드 부모
        effective_brand_parents = list(BRAND_PARENTS) if BRAND_PARENTS else []  # 런타임에 채워질 부모 리스트
        CATEGORY_ROOT_PARENTS = store_config.get('category_root_parents', set())  # 최상위 껍데기 (자식이 실제 root)
        BRANDS_AT_TOP = store_config.get('brands_at_top', False)  # 최상위 메뉴 자체가 브랜드
        BRAND_PREFIX = store_config.get('brand_prefix')  # top menu text가 이 prefix로 시작할 때만 브랜드 그룹으로 호버

        # 호버 전/후 diff로 자식 항목 추출
        items = []
        seen_keys = set()

        async def get_all_keys():
            return set(await page.evaluate('''() =>
                Array.from(document.querySelectorAll('li[data-category-menu-key]'))
                    .map(li => li.getAttribute('data-category-menu-key'))
            '''))

        async def get_item_info(key):
            return await page.evaluate('''(k) => {
                const li = document.querySelector('li[data-category-menu-key="' + k + '"]');
                if (!li) return null;
                const a = li.querySelector('a[data-shp-contents-dtl]');
                if (!a) return null;
                try {
                    const dtl = JSON.parse(a.getAttribute('data-shp-contents-dtl'));
                    const txt = dtl.find(d => d.key === 'txt');
                    const url = dtl.find(d => d.key === 'url');
                    const hasChild = !!a.querySelector('.blind');
                    return {text: txt?.value || '', url: url?.value || '', hasChild};
                } catch(e) { return null; }
            }''', key)

        async def hover_and_diff(parent_key, parent_path, recurse_korean=False, recurse_all=False):
            """호버 전/후 diff로 자식만 수집.
            recurse_korean=True: 한글 자식만 재귀 (카테고리 depth 탐색)
            recurse_all=True: 모든 자식 재귀 (카테고리 영문+한글 모두)
            """
            before = await get_all_keys()
            try:
                li = page.locator(f'li[data-category-menu-key="{parent_key}"]').first
                await li.hover(timeout=3000)
                await asyncio.sleep(0.8)
            except:
                return

            after = await get_all_keys()
            new_keys = after - before

            for key in new_keys:
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                info = await get_item_info(key)
                if not info:
                    continue

                child_path = f"{parent_path} > {info['text']}"
                items.append({
                    'text': info['text'],
                    'url': info['url'],
                    'category_key': key,
                    'full_path': child_path,
                    'parent': parent_path,
                    'hasChild': info['hasChild'],
                })

                # 재귀 조건
                if info['hasChild']:
                    if recurse_all:
                        await hover_and_diff(key, child_path, recurse_all=True)
                    elif recurse_korean and re.search(r'[가-힣]', info['text']):
                        await hover_and_diff(key, child_path, recurse_korean=True)

        # 최상위 메뉴 자체가 브랜드인 스토어 (루티크 등)
        if BRANDS_AT_TOP:
            emoji_re = re.compile(
                r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]'
            )
            def clean_name(s: str) -> str:
                return emoji_re.sub('', s).strip()

            brand_range = store_config.get('brand_range')
            in_range = brand_range is None
            for menu in top_menus:
                text = clean_name(menu.get('text') or '')
                if not text:
                    continue
                if brand_range and not in_range and text == brand_range[0]:
                    in_range = True
                if not in_range:
                    continue
                info = await get_item_info(menu['key'])
                url = info['url'] if info else ''
                result['brands'].append({
                    'name': text,
                    'url': url,
                    'category_key': menu['key'],
                    'parent': '',
                    'full_path': text,
                })
                if brand_range and text == brand_range[1]:
                    break
            logger.info(f"brands_at_top 모드: {len(result['brands'])}개 브랜드 수집")
            await browser.close()
            return result

        # 최상위 메뉴 분류 후 호버
        for menu in top_menus:
            text = menu['text']
            key = menu['key']
            seen_keys.add(key)

            if BRAND_PARENT and text == BRAND_PARENT:
                # BRAND 메뉴: 자음그룹 → 실제 브랜드 (2단계 호버)
                logger.info(f"  브랜드 부모 호버: {text}")
                await hover_and_diff(key, text, recurse_all=True)
            elif BRAND_PARENTS and text in BRAND_PARENTS:
                # 다중 브랜드 부모: 해당 top만 1-depth 자식 호버 (자식=브랜드)
                logger.info(f"  브랜드 부모 호버: {text}")
                await hover_and_diff(key, text, recurse_korean=False)
            elif BRAND_PARENT_PREFIX and text.startswith(BRAND_PARENT_PREFIX):
                # prefix 매칭 브랜드 부모: 런타임에 effective_brand_parents에 추가
                effective_brand_parents.append(text)
                logger.info(f"  브랜드 부모 호버: {text}")
                await hover_and_diff(key, text, recurse_korean=False)
            elif CATEGORY_ROOT_PARENTS and text in CATEGORY_ROOT_PARENTS:
                # 껍데기 부모 호버 → 자식을 실제 카테고리 root로 사용
                logger.info(f"  카테고리 부모 호버 (자식=root): {text}")
                before = await get_all_keys()
                try:
                    li_parent = page.locator(f'li[data-category-menu-key="{key}"]').first
                    await li_parent.hover(timeout=3000)
                    await asyncio.sleep(0.8)
                except:
                    continue
                after = await get_all_keys()
                for child_key in after - before:
                    if child_key in seen_keys:
                        continue
                    seen_keys.add(child_key)
                    info = await get_item_info(child_key)
                    if not info:
                        continue
                    items.append({
                        'text': info['text'],
                        'url': info['url'],
                        'category_key': child_key,
                        'full_path': info['text'],
                        'parent': '',
                        'hasChild': info['hasChild'],
                    })
                    if info['hasChild']:
                        await hover_and_diff(child_key, info['text'], recurse_all=True)
            elif text in CATEGORY_ROOTS:
                # 카테고리: 모든 하위 재귀 호버
                logger.info(f"  카테고리 호버: {text}")
                before_count = len(items)
                await hover_and_diff(key, text, recurse_all=True)
                # 하위가 없는 leaf 카테고리 → 자기 자신을 카테고리로 추가
                if len(items) == before_count:
                    items.append({
                        'text': text, 'url': '', 'category_key': key,
                        'full_path': text, 'parent': '', 'hasChild': False,
                    })
            elif BRAND_PREFIX and text.startswith(BRAND_PREFIX):
                # 지정된 prefix의 top menu만 브랜드 그룹으로 호버 (예: "Brand A" → 자식=브랜드)
                logger.info(f"  브랜드 호버: {text}")
                await hover_and_diff(key, text, recurse_korean=False)
            elif not BRAND_PARENT and not BRAND_PREFIX and not re.search(r'[가-힣]', text):
                # 영문 알파벳 그룹 (A, B, ...): 1depth만 (브랜드명) — BRAND_PARENT/PREFIX 없을 때만
                logger.info(f"  브랜드 호버: {text}")
                await hover_and_diff(key, text, recurse_korean=False)
            # 그 외 (전체상품 등): 스킵

        logger.info(f"네비게이션 항목 (호버 포함): {len(items)}개")

        for item in items:
            entry = {
                'name': item['text'],
                'url': item.get('url', ''),
                'category_key': item['category_key'],
                'parent': item.get('parent', ''),
                'full_path': item.get('full_path', item['text']),
            }

            if BRAND_PARENT:
                # BRAND_PARENT 모드: full_path가 BRAND로 시작하면 브랜드, CATEGORY_ROOTS로 시작하면 카테고리
                fp = item.get('full_path', '')
                if fp.startswith(BRAND_PARENT + ' > '):
                    # 자음그룹(중간노드)은 제외, 최종 브랜드만
                    if not item.get('hasChild', False):
                        result['brands'].append(entry)
                elif any(fp.startswith(root + ' > ') or fp == root for root in CATEGORY_ROOTS):
                    result['categories'].append(entry)
            elif effective_brand_parents:
                # 다중 부모: full_path가 어느 부모로 시작하면 브랜드 (1-depth 호버라 hasChild 무관)
                fp = item.get('full_path', '')
                if any(fp.startswith(p + ' > ') for p in effective_brand_parents):
                    result['brands'].append(entry)
                elif any(fp.startswith(root + ' > ') or fp == root for root in CATEGORY_ROOTS):
                    result['categories'].append(entry)
            else:
                is_korean = bool(re.search(r'[가-힣]', item['text']))
                if not is_korean:
                    result['brands'].append(entry)
                else:
                    result['categories'].append(entry)

        await browser.close()

    return result


# =====================================================
# Phase 2: DB INSERT
# =====================================================

def get_connection():
    return pymysql.connect(**DB_CONFIG)


def insert_mall_site(source_site: str):
    """mall_sites INSERT (이미 있으면 스킵)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM mall_sites WHERE site_name = %s", (source_site,))
            if cur.fetchone():
                logger.info(f"mall_sites: '{source_site}' 이미 존재 → 스킵")
                return
            cur.execute(
                "INSERT INTO mall_sites (site_name, has_own_images, is_active) VALUES (%s, 1, 1)",
                (source_site,)
            )
            conn.commit()
            logger.info(f"mall_sites: '{source_site}' INSERT 완료")
    finally:
        conn.close()


def insert_mall_brands(source_site: str, brands: List[Dict], dry_run: bool = False):
    """mall_brands INSERT (ON DUPLICATE 처리 없음 — mall_name+brand_name 기준 스킵)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # 기존 브랜드 조회
            cur.execute(
                "SELECT raw_brand_name FROM mall_brands WHERE mall_name = %s",
                (source_site,)
            )
            existing = {r['raw_brand_name'] for r in cur.fetchall() if r['raw_brand_name']}

            inserted = 0
            skipped = 0
            for brand in brands:
                brand_name = brand['name'].strip()
                if brand_name in existing:
                    skipped += 1
                    continue

                if dry_run:
                    logger.info(f"  [DRY] mall_brands INSERT: {brand_name} (key={brand['category_key']})")
                    inserted += 1
                    continue

                cur.execute("""
                    INSERT INTO mall_brands
                    (mall_name, raw_brand_name, mall_brand_name_en,
                     mall_brand_url, mall_brand_no, is_active, is_mapped)
                    VALUES (%s, %s, %s, %s, %s, NULL, 0)
                """, (
                    source_site,
                    brand_name,
                    brand_name,
                    brand['url'],
                    brand['category_key'],
                ))
                inserted += 1

            if not dry_run:
                conn.commit()
            logger.info(f"mall_brands: {inserted}건 INSERT, {skipped}건 스킵 (기존)")
    finally:
        conn.close()


def reconcile_mall_brands(source_site: str, brands: List[Dict], dry_run: bool = False, deactivate: bool = False):
    """현재 사이트 기준 동기화(insert_mall_brands의 누적-only 한계 보완):
      - 신규(사이트O/DB X)   → INSERT (is_active=NULL, 검수대기)         ← 기본 동작
      - 기존(사이트O/DB O)   → mall_brand_url/no 갱신(해시 바뀐 것만)      ← 기본 동작
      - 사라짐(사이트X/DB O) → is_active=0  **deactivate=True일 때만(opt-in)**

    기본은 '추가 전용'(deactivate=False): 죽은 브랜드를 끄는 건 스캔 불완전·옛 잔재 데이터로
    오판이 잦고, collector 수정(manufacturer 신뢰+홈 가드) 이후엔 죽은 브랜드가 무해해서 불필요.
    끄기는 '완전 스캔'을 확인한 수동 정리 때만 --deactivate로.
    """
    import re as _rx
    norm = lambda s: ''.join((s or '').split()).upper()
    urlpath = lambda u: (u or '').split('?')[0].rstrip('/')  # 쿼리(?cp=2 등) 무시한 경로
    hangul = lambda s: bool(_rx.search(r'[가-힣]', s or ''))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT raw_brand_name, mall_brand_url, mall_brand_no, is_active "
                        "FROM mall_brands WHERE mall_name=%s", (source_site,))
            existing = {}
            for r in cur.fetchall():
                if r['raw_brand_name']:
                    existing[norm(r['raw_brand_name'])] = r
            scanned = {norm(b['name']): b for b in brands}
            if not scanned:
                logger.warning("스캔된 브랜드 0개 → 동기화 생략(캡챠/네비 실패 의심)")
                return
            # 매칭은 이름(norm)이 아니라 '카테고리 해시(mall_brand_no)' 우선 — 언어 무관.
            # 끌로에와 CHLOE는 같은 카테고리 페이지라 해시가 같음 → 같은 브랜드로 인식(중복 추가 안 함).
            existing_by_no = {r['mall_brand_no']: r for r in existing.values() if r['mall_brand_no']}
            catalog_has_kr = any(hangul(r['raw_brand_name']) for r in existing.values())

            inserted = updated = reappeared = retired = skipped_kr = 0
            seen_existing = set()  # 스캔에서 매칭된(해시 or 이름) 기존 브랜드 — 비활성 판단용
            # 1) 신규 INSERT / 기존 URL 갱신·재활성
            for k, b in scanned.items():
                name = b['name'].strip()
                cat = b.get('category_key')
                # 해시로 먼저 찾고(언어 무관), 없으면 이름으로
                row = (existing_by_no.get(cat) if cat else None) or existing.get(k)
                if row is None:
                    # 해시·이름 둘 다 기존에 없음 = 진짜 신규.
                    # 단 영문 카탈로그에 한글 신규는 KO↔EN 매칭 불가→중복위험이라 보류(번역/스윕 필요).
                    if hangul(name) and not catalog_has_kr:
                        if dry_run:
                            logger.info(f"  [스킵] 한글 신규 '{name}' — 해시 미매칭+영문 카탈로그라 보류")
                        skipped_kr += 1
                        continue
                    if dry_run:
                        logger.info(f"  [DRY] INSERT 신규: {name}")
                    else:
                        cur.execute("""INSERT INTO mall_brands
                            (mall_name, raw_brand_name, mall_brand_name_en, mall_brand_url, mall_brand_no, is_active, is_mapped)
                            VALUES (%s,%s,%s,%s,%s,NULL,0)""",
                            (source_site, name, name, b['url'], b['category_key']))
                    inserted += 1
                else:
                    seen_existing.add(row['raw_brand_name'])  # 해시/이름으로 매칭됨 = 살아있음
                    # 카테고리 해시(mall_brand_no)가 같고 경로가 같으면 동일한 것 — ?cp=2 같은 쿼리 차이는 무시
                    need_url = (urlpath(row['mall_brand_url']) != urlpath(b['url'])
                                or (row['mall_brand_no'] or '') != (b['category_key'] or ''))
                    if row['is_active'] == 0:
                        # 의도적으로 꺼둔 브랜드(HERNO 등 지재권/IP 차단)는 자동 재활성 금지.
                        # URL만 갱신하고 is_active=0 유지 — 되살릴지는 사람이 판단.
                        if need_url and not dry_run:
                            cur.execute("UPDATE mall_brands SET mall_brand_url=%s, mall_brand_no=%s "
                                        "WHERE mall_name=%s AND raw_brand_name=%s",
                                        (b['url'], b['category_key'], source_site, row['raw_brand_name']))
                        if dry_run:
                            logger.info(f"  [DRY] 재등장(비활성 유지, 수동검토): {name}")
                        reappeared += 1
                        continue
                    if not need_url:
                        continue
                    if dry_run:
                        logger.info(f"  [DRY] URL갱신: {name}")
                    else:
                        cur.execute("UPDATE mall_brands SET mall_brand_url=%s, mall_brand_no=%s "
                                    "WHERE mall_name=%s AND raw_brand_name=%s",
                                    (b['url'], b['category_key'], source_site, row['raw_brand_name']))
                    updated += 1

            # 2) 사이트에서 사라진 '활성' 브랜드 비활성화.
            #    핵심 안전장치: 메뉴 스캔은 100% 완전하지 않다(브랜드를 놓침). 그래서 "메뉴에 없음"만으로
            #    끄면 살아있는 브랜드를 죽인다(premiumsneakers 66개·carpi DIOR HOMME 사례).
            #    → '메뉴에 없음' AND '최근 ACTIVITY_DAYS일 내 상품 수집 0' 둘 다일 때만 비활성.
            ACTIVITY_DAYS = 60
            gone = [r for r in existing.values()
                    if r['raw_brand_name'] not in seen_existing and r['is_active'] == 1]
            if deactivate and gone:
                import re as _re
                from datetime import datetime, timedelta
                def bkey(s):
                    return _re.sub(r'[^A-Z0-9]', '', (s or '').upper())
                def same_family(a, b):  # 'BURBERRY' ⊂ 'BURBERRY KIDS' = 같은 계열
                    ka, kb = bkey(a), bkey(b)
                    if not ka or not kb:
                        return False
                    if ka == kb:
                        return True
                    sh, lo = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
                    return len(sh) >= 3 and sh in lo
                cutoff = datetime.now() - timedelta(days=ACTIVITY_DAYS)
                # 활동은 brand_name_en(오라벨 가능)이 아니라 manufacturer(진짜 브랜드)로 측정.
                # 예: 'DIOR HOMME' URL이 잡탕수집해 KLATTERMUSEN을 'DIOR HOMME'로 찍어도,
                #     그건 KLATTERMUSEN의 활동일 뿐 DIOR HOMME는 사라진 것.
                cur.execute("SELECT JSON_UNQUOTE(JSON_EXTRACT(raw_json_data,'$.manufacturer')) mfr, "
                            "MAX(updated_at) m FROM raw_scraped_data WHERE source_site=%s GROUP BY mfr",
                            (source_site,))
                recent_mfr = [rr['mfr'] for rr in cur.fetchall() if rr['m'] and rr['m'] >= cutoff and rr['mfr']]
                active_cnt = sum(1 for r in existing.values() if r['is_active'] == 1)
                if len(scanned) < max(3, active_cnt * 0.5):
                    logger.warning(f"⚠️ 스캔 {len(scanned)}개 < 기존활성 {active_cnt}개의 절반 "
                                   f"→ 비활성화 전체 생략(스캔 실패 의심). 후보 {len(gone)}개 유지")
                else:
                    protected = 0
                    for r in gone:
                        if any(same_family(r['raw_brand_name'], m) for m in recent_mfr):
                            if dry_run:
                                logger.info(f"  [보호] 비활성 보류: {r['raw_brand_name']} (최근 {ACTIVITY_DAYS}일 내 '진짜' 상품 수집됨)")
                            protected += 1
                            continue
                        if dry_run:
                            logger.info(f"  [DRY] 비활성(is_active=0): {r['raw_brand_name']} (메뉴에 없고 {ACTIVITY_DAYS}일+ 무수집)")
                        else:
                            cur.execute("UPDATE mall_brands SET is_active=0 WHERE mall_name=%s AND raw_brand_name=%s",
                                        (source_site, r['raw_brand_name']))
                        retired += 1
                    if protected:
                        logger.info(f"  비활성 보호(최근 수집 있어 살아있음): {protected}개")
            elif gone and not deactivate:
                logger.info(f"  (사라진 {len(gone)}개는 --no-deactivate라 유지)")

            if not dry_run:
                conn.commit()
            kr_note = f" / 한글스킵 {skipped_kr}" if skipped_kr else ""
            logger.info(f"동기화: 신규 {inserted} / URL갱신 {updated} / 재등장(비활성유지) {reappeared} / 비활성 {retired}{kr_note}")
    finally:
        conn.close()


def insert_mall_categories(source_site: str, categories: List[Dict], dry_run: bool = False):
    """mall_categories INSERT"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # 기존 카테고리 조회
            cur.execute(
                "SELECT full_path FROM mall_categories WHERE mall_name = %s",
                (source_site,)
            )
            existing = {r['full_path'] for r in cur.fetchall()}

            inserted = 0
            skipped = 0
            for cat in categories:
                full_path = cat.get('full_path', cat['name']).strip()

                if full_path in existing:
                    skipped += 1
                    continue

                # full_path에서 depth 분리 (예: "신발 > 남성신발 > 스니커즈")
                parts = [p.strip() for p in full_path.split('>')]
                depth1 = parts[0] if len(parts) > 0 else ''
                depth2 = parts[1] if len(parts) > 1 else ''
                depth3 = parts[2] if len(parts) > 2 else ''

                # gender 추론 (full_path 전체에서)
                combined = full_path.lower()
                gender = 'unisex'
                if any(k in combined for k in ['남성', 'men', 'man']):
                    gender = 'male'
                if any(k in combined for k in ['여성', 'women', 'woman']):
                    gender = 'female'
                if any(k in combined for k in ['키즈', 'kids', 'kid', '아동']):
                    gender = 'kids'
                if any(k in combined for k in ['공용']):
                    gender = 'unisex'

                if dry_run:
                    logger.info(f"  [DRY] mall_categories INSERT: {full_path} (gender={gender})")
                    inserted += 1
                    continue

                cur.execute("""
                    INSERT INTO mall_categories
                    (mall_name, category_id, gender, depth1, depth2, depth3, full_path, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
                """, (
                    source_site,
                    cat['category_key'],
                    gender,
                    depth1,
                    depth2,
                    depth3,
                    full_path,
                ))
                inserted += 1

            if not dry_run:
                conn.commit()
            logger.info(f"mall_categories: {inserted}건 INSERT, {skipped}건 스킵 (기존)")
    finally:
        conn.close()


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='네이버 스토어 브랜드/카테고리 스캔')
    parser.add_argument('--store', type=str, required=True, help='스토어 ID (예: premiumsneakers)')
    parser.add_argument('--dry-run', action='store_true', help='DB INSERT 없이 미리보기')
    parser.add_argument('--insert-site', action='store_true', help='mall_sites도 INSERT')
    parser.add_argument('--brands-only', action='store_true', help='브랜드만 스캔/INSERT')
    parser.add_argument('--categories-only', action='store_true', help='카테고리만 스캔/INSERT')
    parser.add_argument('--reconcile', action='store_true',
                        help='현재 사이트 기준 동기화(기본: 신규추가+URL갱신만). 누적-only INSERT 대신 사용')
    parser.add_argument('--deactivate', action='store_true',
                        help='[opt-in] 사라진 브랜드 is_active=0. 스캔이 완전할 때만 권장(부분 스캔이면 오판). 기본은 끄기 안 함')
    args = parser.parse_args()

    if args.brands_only and args.categories_only:
        logger.error("--brands-only 와 --categories-only 는 동시 사용 불가")
        return

    if args.store not in STORES:
        logger.error(f"알 수 없는 스토어: {args.store}")
        logger.info(f"사용 가능: {', '.join(STORES.keys())}")
        return

    store_config = STORES[args.store]
    source_site = store_config.get('source_site', args.store)

    logger.info("=" * 60)
    logger.info(f"스토어 스캔: {store_config['name']} ({args.store})")
    logger.info(f"Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'}")
    logger.info("=" * 60)

    # Phase 1: 네비게이션 추출
    data = asyncio.run(scan_navigation(args.store, store_config))

    brands = [] if args.categories_only else data['brands']
    categories = [] if args.brands_only else data['categories']

    logger.info(f"\n추출 결과:")
    logger.info(f"  브랜드: {len(brands)}개")
    for b in brands:
        logger.info(f"    {b['name']:<30} key={b['category_key'][:20]}...")
    logger.info(f"  카테고리: {len(categories)}개")
    for c in categories:
        fp = c.get('full_path', c['name'])
        logger.info(f"    {fp}")

    if not brands and not categories:
        logger.warning("추출된 데이터 없음. 캡챠이거나 네비 구조가 다를 수 있음.")
        return

    # Phase 2: DB INSERT
    if args.insert_site:
        if args.dry_run:
            logger.info(f"[DRY] mall_sites INSERT: {source_site}")
        else:
            insert_mall_site(source_site)

    if brands:
        if args.reconcile:
            logger.info(f"\nmall_brands 동기화 ({source_site}):")
            reconcile_mall_brands(source_site, brands, dry_run=args.dry_run,
                                  deactivate=args.deactivate)
        else:
            logger.info(f"\nmall_brands INSERT ({source_site}):")
            insert_mall_brands(source_site, brands, dry_run=args.dry_run)

    if categories:
        logger.info(f"\nmall_categories INSERT ({source_site}):")
        insert_mall_categories(source_site, categories, dry_run=args.dry_run)

    logger.info("\n완료!")


if __name__ == '__main__':
    main()
