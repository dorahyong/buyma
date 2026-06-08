# -*- coding: utf-8 -*-
"""
구템즈(9tems.com) 브랜드 수집 스크립트 → mall_brands 적재

- 브랜드 인덱스 페이지: https://9tems.com/_wg/import/brand.html
- div.brand 안 <!-- 영문 --> 섹션의 live 브랜드만 사용
  · 한글 섹션은 통째로 HTML 주석 처리됨 → 자동 제외
  · 영업중단 브랜드도 <!-- --> 로 막혀 있어 BeautifulSoup가 자동 제외
- 앵커 텍스트 "ACNE 아크네" → 첫 한글 글자 기준으로 영문/한글 분리
- URL 도메인(9tems.com / gitems.cafe24.com / m.9tems.com)·`?cate_no=220?pn=1` 형태 무시,
  cate_no 숫자만 정규식으로 추출
- mall_brands 에 (mall_name='9tems', mall_brand_no) 기준 upsert
  · BUYMA 매핑(buyma_brand_id/is_mapped/mapping_level)은 이후 단계 → INSERT 시 is_mapped=0

사용법:
    python 9tems_brand_collector.py --dry-run   # DB 저장 없이 파싱 결과만 출력
    python 9tems_brand_collector.py             # mall_brands upsert
"""

import os
import re
import argparse
import logging

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL', f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4")
engine = create_engine(DATABASE_URL, echo=False)

BASE_URL = 'https://www.9tems.com'
SOURCE_SITE = '9tems'
BRAND_INDEX_URL = 'https://9tems.com/_wg/import/brand.html'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': f'{BASE_URL}/',
}


def split_en_ko(text_value: str):
    """ "ACNE 아크네" → ('ACNE', '아크네'). 첫 한글 글자 기준 분리. """
    m = re.search(r'[가-힣]', text_value)
    if m:
        return text_value[:m.start()].strip(), text_value[m.start():].strip()
    return text_value.strip(), ''


def parse_brands(html: str):
    """div.brand 영문 섹션에서 (en, ko, cate_no) 리스트 반환 (주석 자동 제외)."""
    soup = BeautifulSoup(html, 'html.parser')
    # 영문 브랜드 인덱스는 div.brand > div.section(id=go_X) 안에 있음.
    # categorySub brand(카테고리 네비) 와 구분하기 위해 div.section 으로 한정.
    anchors = soup.select('div.brand div.section a[href]')
    if not anchors:
        anchors = soup.select('div.brand a[href]')

    brands = []
    seen = set()
    for a in anchors:
        m = re.search(r'cate_no=(\d+)', a.get('href', ''))
        if not m:
            continue
        cate_no = m.group(1)
        if cate_no in seen:
            continue
        en, ko = split_en_ko(a.get_text(strip=True))
        if not en:
            continue
        seen.add(cate_no)
        brands.append({'en': en, 'ko': ko, 'cate_no': cate_no})
    return brands


def fetch_brand_index() -> str:
    resp = requests.get(BRAND_INDEX_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def upsert_brands(brands):
    inserted = updated = 0
    with engine.begin() as conn:
        for b in brands:
            url_path = f'/product/list.html?cate_no={b["cate_no"]}'
            existing = conn.execute(text(
                "SELECT COUNT(*) FROM mall_brands WHERE mall_name=:s AND mall_brand_no=:no"
            ), {'s': SOURCE_SITE, 'no': b['cate_no']}).scalar()

            if existing:
                conn.execute(text("""
                    UPDATE mall_brands
                    SET mall_brand_name_en=:en, raw_brand_name=:en,
                        mall_brand_url=:url, is_active=1, updated_at=NOW()
                    WHERE mall_name=:s AND mall_brand_no=:no
                """), {'en': b['en'], 'url': url_path, 's': SOURCE_SITE, 'no': b['cate_no']})
                updated += 1
            else:
                conn.execute(text("""
                    INSERT INTO mall_brands
                    (mall_name, mall_brand_name_en, raw_brand_name, mall_brand_no,
                     mall_brand_url, is_active, is_mapped, created_at, updated_at)
                    VALUES (:s, :en, :en, :no, :url, 1, 0, NOW(), NOW())
                """), {'s': SOURCE_SITE, 'en': b['en'], 'no': b['cate_no'], 'url': url_path})
                inserted += 1
    return inserted, updated


def main():
    parser = argparse.ArgumentParser(description='구템즈 브랜드 수집기 → mall_brands')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 파싱 결과만 출력')
    args = parser.parse_args()

    logger.info("브랜드 인덱스 페이지 수집 중...")
    html = fetch_brand_index()
    brands = parse_brands(html)
    logger.info(f"파싱된 영문 브랜드: {len(brands)}개")
    for b in brands[:10]:
        logger.info(f"  cate_no={b['cate_no']:>6} | en={b['en']!r} ko={b['ko']!r}")
    if len(brands) > 10:
        logger.info(f"  ... 외 {len(brands) - 10}개")

    if args.dry_run:
        logger.info("[DRY-RUN] DB 저장 생략")
        return

    inserted, updated = upsert_brands(brands)
    logger.info(f"mall_brands 적재 완료: INSERT {inserted} / UPDATE {updated}")
    with engine.connect() as conn:
        total = conn.execute(text(
            "SELECT COUNT(*) FROM mall_brands WHERE mall_name=:s AND is_active=1"
        ), {'s': SOURCE_SITE}).scalar()
        logger.info(f"DB 내 9tems 활성 브랜드: {total}개")


if __name__ == '__main__':
    main()
