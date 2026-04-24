# -*- coding: utf-8 -*-
"""
네이버 브랜드스토어(brand.naver.com) 상품 수집기

대상: carpi, joharistore 등 brand.naver.com 도메인 스토어

스마트스토어(premiumsneakers_collector.py)와 차이:
  - 도메인: brand.naver.com (vs smartstore.naver.com)
  - XHR API 경로: /n/v2/... (vs /i/v2/...)
  - 나머지(리스트 페이지, 상세 페이지, 페이지네이션, map_to_row)는 동일

공유 컴포넌트 재사용:
  - set_source, login_and_save_cookies, map_to_row, save_rows
  - get_brands, get_existing_product_ids, absolute_url, COOKIE_FILE
  - collect_product_list (브랜드 URL 순회 + 클릭 페이지네이션)
  - DETAIL_MAX_RETRIES

사용법:
    python brand_store_collector.py --source carpi --limit 5 --dry-run
    python brand_store_collector.py --source joharistore --skip-existing
"""

import os
import sys
import re
import json
import random
import logging
import asyncio
import argparse
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from premiumsneakers_collector import (
    engine,
    set_source,
    login_and_save_cookies,
    get_brands,
    get_existing_product_ids,
    save_rows,
    map_to_row,
    collect_product_list,
    COOKIE_FILE,
    DETAIL_MAX_RETRIES,
)
import premiumsneakers_collector as base

# 브랜드스토어 전용 딜레이 (기존 brand collector 건드리지 않음, 보수적 단축)
LIST_DELAY = (0.5, 1.0)
DETAIL_DELAY = (0.8, 1.5)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)


# =====================================================
# Phase 2: 상세 — XHR 가로채기 (brandstore 버전 /n/v2/)
# =====================================================

async def fetch_detail_brand_store(page, product_no: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    """brandstore 상품 상세에서 두 XHR 가로채기:
      1. GET /n/v2/channels/{uid}/products/{pno}?withWindow=false → 상품 JSON
      2. POST /n/v2/channels/{uid}/product-benefits/{pno}         → 쿠폰 적용가 JSON
    (스마트스토어는 /i/v2/, 브랜드스토어는 /n/v2/)
    """
    captured = []

    product_re = re.compile(rf'/n/v2/channels/[^/]+/products/{product_no}(\?|$)')
    benefits_re = re.compile(rf'/n/v2/channels/[^/]+/product-benefits/{product_no}(\?|$)')

    def on_response(response):
        url = response.url
        if product_re.search(url) or benefits_re.search(url):
            captured.append(response)

    page.on('response', on_response)
    try:
        detail_url = f"{base.STORE_HOME}/products/{product_no}"
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

        page_title = await page.title()
        if '보안' in page_title or 'captcha' in page_title.lower():
            logger.error(f"캡챠 감지! title={page_title!r}")
            return None, None

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
# 오케스트레이션 (premiumsneakers_collector.run의 brandstore 버전)
# =====================================================

async def run(brand_filter: Optional[str], limit: Optional[int],
              skip_existing: bool, dry_run: bool, dump: bool = False, count_only: bool = False):
    from playwright.async_api import async_playwright

    brands = get_brands(brand_filter)
    logger.info(f"대상 브랜드: {len(brands)}개")
    if not brands:
        logger.warning(f"mall_brands에 '{base.SOURCE_SITE}' 브랜드 없음.")
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

        logger.info("\n== Phase 1: 리스트 수집 (브랜드 기반) ==")
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

        logger.info("\n== Phase 2: 상세 수집 (brandstore /n/v2/) ==")
        rows = []
        total = len(items)
        for i, item in enumerate(items, 1):
            pno = item['product_no']
            product, benefits = None, None
            for attempt in range(DETAIL_MAX_RETRIES + 1):
                product, benefits = await fetch_detail_brand_store(page, pno)
                if product:
                    break
                if attempt < DETAIL_MAX_RETRIES:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"[{i}/{total}] {pno} 재시도 {attempt + 1} — {wait}초 대기")
                    await asyncio.sleep(wait)

            row = map_to_row(product, benefits, item['brand_name'], pno)
            if not row:
                if product:
                    logger.info(f"[{i}/{total}] {pno} 스킵: 모델번호 없음")
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
    parser = argparse.ArgumentParser(description='네이버 브랜드스토어 상품 수집기')
    parser.add_argument('--source', type=str, required=True,
                        help='수집 대상 스토어 예: carpi, joharistore')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 (UPPER 매칭)')
    parser.add_argument('--limit', type=int, help='최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='기존 수집 상품 스킵')
    parser.add_argument('--login', action='store_true', help='네이버 로그인 → 쿠키 갱신')
    parser.add_argument('--dump', action='store_true', help='수집된 첫 행의 raw_json_data 전체 출력')
    parser.add_argument('--count', action='store_true', help='Phase 1만 실행 — 브랜드별 상품 수 집계')
    args = parser.parse_args()

    set_source(args.source)

    if args.login:
        asyncio.run(login_and_save_cookies())
        return

    logger.info(f"=== {base.SOURCE_SITE} 브랜드스토어 수집 "
                f"(Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'}) ===")
    logger.info(f"STORE_HOME: {base.STORE_HOME}")
    asyncio.run(run(args.brand, args.limit, args.skip_existing, args.dry_run, args.dump, args.count))


if __name__ == '__main__':
    main()
