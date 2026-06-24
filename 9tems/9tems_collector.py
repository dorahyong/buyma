# -*- coding: utf-8 -*-
"""
구템즈(9tems.com) 상품 수집 스크립트
- Cafe24 기반 쇼핑몰 → HTML 스크래핑
- 수집 기준: 브랜드 (전체상품 메뉴 없음, 브랜드 메뉴 있음)
  · mall_brands(9tems, is_active=1) 의 mall_brand_no(cate_no) 기준으로 순회
  · → raw 에 있는 브랜드는 항상 mall_brands 에 존재 (수집 기준이므로)
- 카테고리: 브랜드 list 페이지의 .menuCategory 에서 depth1(li.dm2) > depth2(li.dm3) 추출
  · category_path = "남성의류 > 패딩/점퍼" (브랜드명 카테고리는 제외)
  · 수집 중 mall_categories(9tems)에 없으면 INSERT, 있으면 skip
- raw_scraped_data 테이블에 source_site='9tems' 로 저장
- 이미지: keyImg BigImage(메인) + listImg ThumbImage(small→big 업스케일) → raw_json_data.images

★ 선행 조건: 9tems_brand_collector.py 로 mall_brands 가 먼저 채워져 있어야 함

사용법:
    python 9tems_collector.py                       # 전체 브랜드
    python 9tems_collector.py --brand "ACNE"        # 특정 브랜드만
    python 9tems_collector.py --limit 10            # 브랜드당 최대 10개
    python 9tems_collector.py --dry-run             # DB 저장 없이 테스트
    python 9tems_collector.py --skip-existing       # 등록 완료 상품 스킵

★ 봇 감지 방지: 30개마다 세션 교체 + 메인 방문, 랜덤 프로필 (nextzennpack/labellusso 패턴)
"""

import os
import re
import json
import time
import random
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

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
engine = create_engine(DATABASE_URL, echo=False)

# ===========================================
# 상수
# ===========================================

BASE_URL = 'https://www.9tems.com'
SOURCE_SITE = '9tems'
SESSION_REFRESH_INTERVAL = 30
MAX_CONSECUTIVE_TIMEOUTS = 5
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.8

BROWSER_PROFILES = [
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'sec-ch-ua-platform': '"Windows"',
    },
    {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'sec-ch-ua-platform': '"macOS"',
    },
]


# ===========================================
# 세션 관리 (okmall/nextzennpack 패턴)
# ===========================================

class SessionManager:
    def __init__(self):
        self.session = None
        self.profile = None
        self.request_count = 0
        self.consecutive_timeout_count = 0
        self.is_blocked = False

    def _create_new_session(self) -> Tuple[bool, Optional[str]]:
        try:
            if self.session:
                self.session.close()

            self.session = requests.Session()
            self.profile = random.choice(BROWSER_PROFILES).copy()

            main_headers = self.profile.copy()
            main_headers['Referer'] = 'https://www.google.com/'
            self.session.headers.update(main_headers)

            logger.info("  [세션] 새 세션 시작 - 메인 페이지 방문 중...")
            response = self.session.get(f'{BASE_URL}/index.html', timeout=15)
            if response.status_code != 200:
                return False, f"메인 페이지 접속 실패: {response.status_code}"

            product_headers = self.profile.copy()
            product_headers['Referer'] = f'{BASE_URL}/'
            self.session.headers.update(product_headers)

            self.request_count = 0
            time.sleep(random.uniform(0.5, 1.5))
            logger.info("  [세션] 새 세션 준비 완료 (쿠키 획득됨)")
            return True, None
        except requests.exceptions.Timeout:
            return False, "메인 페이지 타임아웃"
        except Exception as e:
            return False, f"세션 생성 오류: {str(e)}"

    def fetch_page(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        if self.is_blocked:
            return None, "차단됨"

        if self.session is None or self.request_count >= SESSION_REFRESH_INTERVAL:
            success, error = self._create_new_session()
            if not success:
                return None, error

        try:
            response = self.session.get(url, timeout=30)
            self.request_count += 1

            if response.status_code == 403:
                self.is_blocked = True
                return None, "접근 차단됨 (403)"

            response.raise_for_status()
            self.consecutive_timeout_count = 0
            return response.text, None
        except requests.exceptions.Timeout:
            self.consecutive_timeout_count += 1
            logger.warning(f"  [타임아웃] 연속 {self.consecutive_timeout_count}회")
            if self.consecutive_timeout_count >= MAX_CONSECUTIVE_TIMEOUTS:
                self.is_blocked = True
                return None, "타임아웃 차단 감지"
            return None, "요청 타임아웃"
        except requests.exceptions.RequestException as e:
            self.consecutive_timeout_count = 0
            return None, f"요청 오류: {str(e)}"

    def close(self):
        if self.session:
            self.session.close()


# ===========================================
# 카테고리 (.menuCategory) 파싱
# ===========================================

def gender_of(depth1: str) -> str:
    """depth1 카테고리명에서 성별 추출."""
    if '남성' in depth1:
        return 'male'
    if '여성' in depth1:
        return 'female'
    if '키즈' in depth1 or '아동' in depth1 or '베이비' in depth1:
        return 'kids'
    return 'unisex'


def extract_leaf_categories(html: str) -> List[Dict]:
    """브랜드 list 페이지 .menuCategory 에서 수집 대상 리프 카테고리 추출.

    Returns: [{'cate_no','depth1','depth2','full_path','gender','count'}, ...]
    - depth1 = li.dm2, depth2 = li.dm3
    - 상품수(앵커 텍스트 '(N)')가 0인 리프는 제외
    - dm3 자식이 없는 dm2 는 depth1 단독 리프로 처리
    """
    soup = BeautifulSoup(html, 'html.parser')
    menu = soup.select_one('ul.menuCategory')
    if not menu:
        return []

    def parse_anchor(li):
        a = li.find('a')
        if not a:
            return None, None, 0
        m = re.search(r'cate_no=(\d+)', a.get('href', ''))
        cate_no = m.group(1) if m else None
        raw = a.get_text(strip=True)
        cnt_match = re.search(r'\((\d+)\)\s*$', raw)
        count = int(cnt_match.group(1)) if cnt_match else 0
        name = re.sub(r'\(\d+\)\s*$', '', raw).strip()
        return cate_no, name, count

    leaves = []
    for li1 in menu.find_all('li', class_='dm2', recursive=False) or menu.select('li.dm2'):
        d1_cate, depth1, d1_count = parse_anchor(li1)
        if not depth1:
            continue
        dm3_items = li1.select('li.dm3')
        if dm3_items:
            for li2 in dm3_items:
                d2_cate, depth2, d2_count = parse_anchor(li2)
                if not depth2 or not d2_cate:
                    continue
                if d2_count <= 0:
                    continue
                leaves.append({
                    'cate_no': d2_cate,
                    'depth1': depth1,
                    'depth2': depth2,
                    'full_path': f"{depth1} > {depth2}",
                    'gender': gender_of(depth1),
                    'count': d2_count,
                })
        elif d1_count > 0 and d1_cate:
            # dm3 자식이 없는 depth1 단독 카테고리
            leaves.append({
                'cate_no': d1_cate,
                'depth1': depth1,
                'depth2': '',
                'full_path': depth1,
                'gender': gender_of(depth1),
                'count': d1_count,
            })
    return leaves


# ===========================================
# 리스트 페이지 파싱
# ===========================================

def get_product_nos_from_list(html: str) -> List[str]:
    """리스트 페이지에서 product_no 목록 추출 (li#anchorBoxId_29460)."""
    soup = BeautifulSoup(html, 'html.parser')
    nos = []
    for li in soup.select('ul.prdList > li.xans-record-'):
        item_id = li.get('id', '')
        if item_id.startswith('anchorBoxId_'):
            nos.append(item_id.replace('anchorBoxId_', ''))
    return nos


def get_last_page(html: str) -> int:
    """페이지네이션에서 마지막 페이지 번호 추출."""
    soup = BeautifulSoup(html, 'html.parser')
    max_page = 1
    for a in soup.select('.xans-product-normalpaging a, .ec-base-paginate a'):
        m = re.search(r'page=(\d+)', a.get('href', ''))
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


# ===========================================
# 상세 페이지 파싱
# ===========================================

def _first_int(text_value: str) -> int:
    m = re.search(r'[\d,]+', text_value or '')
    return int(m.group(0).replace(',', '')) if m else 0


def extract_model_id(product_name: str) -> str:
    """한글 상품명 끝의 영문/숫자 모델 토막 추출.
    '아크네 자수 나일론 자켓 B90715 DH2' → 'B90715 DH2'
    """
    last_ko = None
    for i, ch in enumerate(product_name):
        if '가' <= ch <= '힣':
            last_ko = i
    tail = product_name[last_ko + 1:] if last_ko is not None else product_name
    tail = tail.strip()
    # 앞쪽에 남은 기호 제거
    tail = re.sub(r'^[^A-Za-z0-9]+', '', tail).strip()
    return tail


def _upscale_image(src: str) -> str:
    if src.startswith('//'):
        src = 'https:' + src
    src = src.replace('/product/extra/small/', '/product/extra/big/')
    src = src.replace('/product/small/', '/product/big/')
    return src


def parse_detail(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    info: Dict[str, Any] = {
        'product_name': '',
        'original_price': 0,
        'sale_price': 0,
        'options': [],
        'images': [],
    }

    # 상품명
    name_el = soup.select_one('span#share_title') or soup.select_one('tr.product_name_css td span')
    if name_el:
        info['product_name'] = name_el.get_text(' ', strip=True)

    # 가격: 소비자가(정가) / 판매가
    cust = soup.select_one('tr.product_custom_css #span_product_price_custom strike')
    if cust:
        info['original_price'] = _first_int(cust.get_text(strip=True))
    sale = soup.select_one('tr.product_price_css #span_product_price_text')
    if sale:
        info['sale_price'] = _first_int(sale.get_text(' ', strip=True))
    if not info['sale_price']:
        info['sale_price'] = info['original_price']
    if not info['original_price']:
        info['original_price'] = info['sale_price']

    # 옵션 (사이즈) — select#product_option_id1
    option_select = soup.select_one('select#product_option_id1')
    if option_select:
        for opt in option_select.select('option'):
            val = opt.get('value', '')
            if not val or val in ('*', '**'):
                continue
            opt_text = opt.get_text(strip=True)
            if re.match(r'^[-=]{3,}$', opt_text):
                continue
            # 품절: 텍스트에 [품절] 접미사 (예: '46 [품절]')
            is_soldout = '품절' in opt_text or opt.get('disabled') is not None
            clean_size = re.sub(r'\s*\[품절\]\s*', '', opt_text).strip()
            if clean_size in ('단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈', 'ONESIZE', 'FREE'):
                clean_size = 'FREE'
            info['options'].append({
                'tag_size': clean_size,
                'option_code': val,
                'status': 'out_of_stock' if is_soldout else 'in_stock',
            })

    # 이미지: keyImg BigImage(메인) + listImg ThumbImage(extra/small→extra/big 업스케일)
    seen = set()
    for img in soup.select('div.keyImg img.BigImage, div.thumbnail img.BigImage'):
        src = _upscale_image(img.get('src', ''))
        if src and src not in seen:
            seen.add(src)
            info['images'].append(src)
    for img in soup.select('div.xans-product-addimage img.ThumbImage'):
        raw = img.get('src', '')
        # plain /product/small/ 썸네일은 /product/big/ 버전이 서버에 없어 404 발생 → 스킵.
        # (/product/extra/small/ 만 /extra/big/ 로 존재. plain small에 해당하는 메인은 keyImg에서 이미 수집됨)
        if '/product/small/' in raw:
            continue
        src = _upscale_image(raw)
        if src and src not in seen:
            seen.add(src)
            info['images'].append(src)

    return info


# ===========================================
# 데이터 변환
# ===========================================

def convert_to_raw_data(product_no: str, detail: Dict, brand_name_en: str,
                        category_path: str, leaf_cate_no: str) -> Optional[Dict]:
    product_name = detail.get('product_name', '')
    if not product_name:
        return None

    # 홍보/이벤트 노이즈 단어 제거 (9tems "럭키찬스") — 따옴표 동반·공백 정리 포함
    product_name = re.sub(r"\s*럭키찬스'?\s*", " ", product_name)
    product_name = re.sub(r"\s+", " ", product_name).strip()

    model_id = extract_model_id(product_name)

    options = detail.get('options', [])
    if any(o.get('status') == 'in_stock' for o in options):
        stock_status = 'in_stock'
    elif options:
        stock_status = 'out_of_stock'   # 옵션 전부 [품절]
    else:
        stock_status = 'in_stock'       # 옵션 없으면 일단 재고 있음

    raw_json = {
        'options': options,
        'images': detail.get('images', []),
        'cate_no': leaf_cate_no,
        'category_path': category_path,
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    product_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={leaf_cate_no}&display_group=1"

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': brand_name_en,
        'product_name': product_name[:255],
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': category_path,
        'original_price': detail.get('original_price', 0),
        'raw_price': detail.get('sale_price', 0),
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': product_url,
    }


# ===========================================
# DB 조회/저장
# ===========================================

def get_brands_from_database(brand_filter: str = None) -> List[Dict]:
    with engine.connect() as conn:
        query = ("SELECT mall_brand_name_en, mall_brand_no FROM mall_brands "
                 "WHERE mall_name = :s AND is_active = 1 "
                 "AND mall_brand_no IS NOT NULL AND mall_brand_no <> ''")
        params = {'s': SOURCE_SITE}
        if brand_filter:
            query += " AND UPPER(mall_brand_name_en) = :brand"
            params['brand'] = brand_filter.upper()
        result = conn.execute(text(query), params)
        return [{'name_en': r[0], 'cate_no': r[1]} for r in result]


def get_published_product_ids() -> set:
    """등록 완료(is_published=1)된 상품의 mall_product_id 목록."""
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = :s AND a.is_published = 1
        """), {'s': SOURCE_SITE})
        return {str(r[0]) for r in result}


def load_existing_category_paths() -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT full_path FROM mall_categories WHERE mall_name = :s"
        ), {'s': SOURCE_SITE})
        return {r[0] for r in rows if r[0]}


def insert_mall_category(leaf: Dict):
    """mall_categories 에 카테고리 INSERT (호출부에서 full_path 중복 체크)."""
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO mall_categories
            (mall_name, category_id, gender, depth1, depth2, full_path,
             mall_category_url, is_active, created_at)
            VALUES (:m, :cid, :g, :d1, :d2, :fp, :url, NULL, NOW())
        """), {
            'm': SOURCE_SITE, 'cid': leaf['cate_no'], 'g': leaf['gender'],
            'd1': leaf['depth1'], 'd2': leaf['depth2'] or None,
            'fp': leaf['full_path'],
            'url': f"/product/list.html?cate_no={leaf['cate_no']}",
        })


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
    with engine.begin() as conn:
        for data in data_list:
            conn.execute(insert_sql, data)


# ===========================================
# 메인 실행
# ===========================================

def main():
    parser = argparse.ArgumentParser(description='구템즈 상품 수집기')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='브랜드당 최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"구템즈 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    logger.info("=" * 60)

    brands = get_brands_from_database(args.brand)
    logger.info(f"대상 브랜드: {len(brands)}개")
    if not brands:
        logger.info("수집할 브랜드가 없습니다. (먼저 9tems_brand_collector.py 실행 필요)")
        return

    existing_paths = load_existing_category_paths()
    published_ids = get_published_product_ids() if args.skip_existing else set()

    session_mgr = SessionManager()
    total_collected = 0
    total_categories_added = 0

    try:
        for brand_idx, brand in enumerate(brands, 1):
            if session_mgr.is_blocked:
                logger.error("  차단 감지됨 — 수집 중단")
                break

            brand_name_en = brand['name_en']
            brand_cate_no = brand['cate_no']
            logger.info(f"\n>>> [{brand_idx}/{len(brands)}] {brand_name_en} (cate_no={brand_cate_no})")

            # 1) 브랜드 페이지 → 리프 카테고리 추출
            brand_url = f"{BASE_URL}/product/list.html?cate_no={brand_cate_no}"
            html, error = session_mgr.fetch_page(brand_url)
            if error or not html:
                logger.warning(f"  브랜드 페이지 실패: {error}")
                continue

            leaves = extract_leaf_categories(html)
            if not leaves:
                logger.warning("  리프 카테고리 없음 — 브랜드 전체로 수집")
                leaves = [{'cate_no': brand_cate_no, 'depth1': '', 'depth2': '',
                           'full_path': '', 'gender': 'unisex', 'count': 1}]
            logger.info(f"  리프 카테고리 {len(leaves)}개: {[l['full_path'] for l in leaves]}")

            # 2) 리프별 product_no 수집 (dedup, 최초 발견 path 유지)
            product_path: Dict[str, Tuple[str, str]] = {}  # no -> (full_path, leaf_cate_no)
            for leaf in leaves:
                if session_mgr.is_blocked:
                    break
                cate_no = leaf['cate_no']
                first_url = f"{BASE_URL}/product/list.html?cate_no={cate_no}&page=1"
                page_html, error = session_mgr.fetch_page(first_url)
                if error or not page_html:
                    logger.warning(f"    [{leaf['full_path']}] 실패: {error}")
                    continue

                last_page = get_last_page(page_html)
                nos = get_product_nos_from_list(page_html)
                for page in range(2, last_page + 1):
                    if session_mgr.is_blocked:
                        break
                    page_html, error = session_mgr.fetch_page(
                        f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}")
                    if error or not page_html:
                        break
                    page_nos = get_product_nos_from_list(page_html)
                    if not page_nos:
                        break
                    nos.extend(page_nos)
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

                new_cnt = 0
                for no in nos:
                    if no not in product_path:
                        product_path[no] = (leaf['full_path'], cate_no)
                        new_cnt += 1

                # mall_categories insert-if-missing
                fp = leaf['full_path']
                if fp and fp not in existing_paths:
                    if not args.dry_run:
                        insert_mall_category(leaf)
                    existing_paths.add(fp)
                    total_categories_added += 1
                    logger.info(f"    [카테고리 추가] {fp}")

                logger.info(f"    [{leaf['full_path']}] {len(nos)}개, 신규 {new_cnt}개 (누적 {len(product_path)})")
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            if not product_path:
                continue

            items = list(product_path.items())
            if args.limit:
                items = items[:args.limit]
            if args.skip_existing:
                before = len(items)
                items = [(no, p) for no, p in items if no not in published_ids]
                if before - len(items) > 0:
                    logger.info(f"  등록 완료 스킵: {before - len(items)}개")

            # 3) 상세 수집 → 변환 → 저장
            batch = []
            total = len(items)
            for idx, (product_no, (cat_path, leaf_cate_no)) in enumerate(items, 1):
                if session_mgr.is_blocked:
                    logger.error("  차단 감지됨 — 상세 수집 중단")
                    break

                detail_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={leaf_cate_no}&display_group=1"
                detail_html, error = session_mgr.fetch_page(detail_url)
                if error or not detail_html:
                    logger.warning(f"  [{idx}/{total}] 상세 실패: {error} (no={product_no})")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                detail = parse_detail(detail_html)
                data = convert_to_raw_data(product_no, detail, brand_name_en, cat_path, leaf_cate_no)
                if not data:
                    logger.info(f"  [{idx}/{total}] SKIP (상품명 없음) no={product_no}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                logger.info(f"  [{idx}/{total}] {data['stock_status']:12} | {data['raw_price']:>10,.0f}원 | {cat_path} | {data['product_name'][:38]}")
                total_collected += 1
                if not args.dry_run:
                    batch.append(data)
                    if len(batch) >= 10:
                        save_to_database(batch)
                        logger.info(f"  DB 저장: {len(batch)}개")
                        batch = []
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            if batch and not args.dry_run:
                save_to_database(batch)
                logger.info(f"  DB 저장(잔여): {len(batch)}개")

    finally:
        session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info("구템즈 수집 완료")
    logger.info(f"  총 수집: {total_collected}개")
    logger.info(f"  mall_categories 신규: {total_categories_added}개")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :s"
            ), {'s': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 9tems raw 상품: {count}개")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
