# -*- coding: utf-8 -*-
"""
구템즈(9tems.com) 상품 수집 스크립트 (Cafe24)
- 브랜드 리스트 페이지: /_wg/import/brand.html → <ul class="brand_list"> li a[href*="cate_no="]
- 리스트 페이지: /product/list.html?cate_no={mall_brand_no}&page={n}
- 상세 페이지: /product/detail.html?product_no={no}&cate_no={cate_no}
- raw_scraped_data 테이블에 source_site='9tems'로 저장

추출 전략 (nextzennpack과 차이):
- 상세 테이블 `prd_brand_css/prd_model_css`는 일부 상품에만 있음 → 공통 사용 불가
- **대신 상세 페이지의 script 변수 사용**:
  - productNo, productName, productPrice
  - option_stock_data (옵션+재고 JSON)
  - is_soldout_icon (전체 품절 플래그)
- 이미지: detailArea > img.BigImage + xans-product-addimage li.swiper-slide img.ThumbImage
- brand_name은 mall_brands에서 가져옴 (사전 스캔 완료 가정)
- model_id는 상품명 끝 대문자/숫자 토큰 추출 (nextzennpack과 동일 패턴)

사용법:
    python 9tems_collector.py                       # 전체 실행
    python 9tems_collector.py --brand "STONE ISLAND"
    python 9tems_collector.py --limit 10
    python 9tems_collector.py --dry-run
    python 9tems_collector.py --skip-existing
    python 9tems_collector.py --scan-brands         # _wg/import/brand.html에서 브랜드 추출 (DB 저장 미리보기)
"""

import os
import re
import json
import time
import random
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

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

BASE_URL = 'https://9tems.com'
SOURCE_SITE = '9tems'
SESSION_REFRESH_INTERVAL = 30
MAX_CONSECUTIVE_TIMEOUTS = 5
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.8

BROWSER_PROFILES = [
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cache-Control': 'max-age=0',
    },
]


# ===========================================
# 세션 관리
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
            main_headers['Sec-Fetch-Site'] = 'cross-site'
            self.session.headers.update(main_headers)

            logger.info(f"  [세션] 새 세션 시작 - 메인 페이지 방문 중...")
            response = self.session.get(f'{BASE_URL}/index.html', timeout=15)

            if response.status_code != 200:
                return False, f"메인 페이지 접속 실패: {response.status_code}"

            product_headers = self.profile.copy()
            product_headers['Referer'] = f'{BASE_URL}/'
            product_headers['Sec-Fetch-Site'] = 'same-origin'
            self.session.headers.update(product_headers)

            self.request_count = 0
            time.sleep(random.uniform(0.5, 1.5))

            logger.info(f"  [세션] 새 세션 준비 완료")
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
# 브랜드 스캔 (선택 기능 — mall_brands 선수집용)
# ===========================================

def scan_brands_from_page(html: str) -> List[Dict]:
    """_wg/import/brand.html에서 브랜드명/cate_no 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    brands = []
    seen = set()
    # 브랜드 링크: <a href="http://www.9tems.com/product/list.html?cate_no=N"><img><span>BRAND</span></a>
    for a in soup.find_all('a', href=re.compile(r'cate_no=\d+')):
        href = a.get('href', '')
        m = re.search(r'cate_no=(\d+)', href)
        if not m:
            continue
        cate_no = m.group(1)
        span = a.find('span')
        if not span:
            continue
        name = span.get_text(strip=True)
        if not name or name in seen:
            continue
        seen.add(name)
        brands.append({'name_en': name, 'cate_no': cate_no, 'url': f'/product/list.html?cate_no={cate_no}'})
    return brands


# ===========================================
# 리스트 페이지 파싱
# ===========================================

def get_product_list_from_page(html: str, cate_no: str) -> List[Dict]:
    """리스트 페이지 HTML에서 상품 기본 정보 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    items = soup.select('li[id^="anchorBoxId_"]')
    for item in items:
        try:
            item_id = item.get('id', '')
            product_no = item_id.replace('anchorBoxId_', '') if item_id.startswith('anchorBoxId_') else ''
            if not product_no:
                continue

            # 상품명: p.name a span (마지막 span이 상품명)
            name_elem = item.select_one('p.name a')
            product_name = ''
            if name_elem:
                # title span은 "상품명 :" 같은 라벨이라 제외
                spans = name_elem.find_all('span')
                candidates = [s.get_text(strip=True) for s in spans if 'title' not in (s.get('class') or [])]
                if candidates:
                    product_name = candidates[-1]
            # [브랜드] prefix 제거
            product_name = re.sub(r'^\[.*?\]\s*', '', product_name)

            # 가격: div.discount_rate[data-price][data-sale] 가장 정확
            original_price = 0
            sale_price = 0
            dr = item.select_one('.discount_rate')
            if dr:
                try:
                    original_price = int(dr.get('data-price', 0) or 0)
                    sale_price = int(dr.get('data-sale', 0) or 0)
                except (ValueError, TypeError):
                    pass
            # fallback: .description ec-data-custom / ec-data-price
            if not sale_price:
                desc = item.select_one('.description')
                if desc:
                    try:
                        original_price = original_price or int(desc.get('ec-data-custom', 0) or 0)
                        sale_price = int(desc.get('ec-data-price', 0) or 0)
                    except (ValueError, TypeError):
                        pass

            # 이미지 (medium → big으로 치환 시도)
            image_url = ''
            img_elem = item.select_one('.thumbnail a img')
            if img_elem:
                image_url = img_elem.get('src', '')
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url
                image_url = image_url.replace('/product/medium/', '/product/big/')

            products.append({
                'product_no': product_no,
                'product_name': product_name,
                'original_price': original_price,
                'sale_price': sale_price,
                'image_url': image_url,
                'cate_no': cate_no,
            })
        except Exception as e:
            logger.warning(f"  리스트 아이템 파싱 오류: {e}")
            continue

    return products


def get_category_path(html: str) -> str:
    """리스트 페이지 breadcrumb에서 카테고리 경로 추출
    예: '홈 > 브랜드 > STONE ISLAND' 에서 '브랜드 > STONE ISLAND' 반환
    """
    soup = BeautifulSoup(html, 'html.parser')
    crumbs = soup.select('.xans-product-headcategory.path ol li a')
    parts = []
    for a in crumbs:
        txt = a.get_text(strip=True)
        if not txt or txt == '홈':
            continue
        parts.append(txt)
    return ' > '.join(parts)


def get_last_page(html: str) -> int:
    """페이지네이션에서 마지막 페이지 번호 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    paging = soup.select('.xans-product-normalpaging ol li a')
    max_page = 1
    for a in paging:
        href = a.get('href', '')
        match = re.search(r'page=(\d+)', href)
        if match:
            page_num = int(match.group(1))
            if page_num > max_page:
                max_page = page_num
    return max_page


# ===========================================
# 상세 페이지 파싱
# ===========================================

def extract_js_var(html: str, var_name: str) -> Optional[str]:
    """HTML의 script에서 `var {var_name} = '{value}';` 패턴 추출"""
    # var xxx = '...'; 또는 var xxx = "...";
    m = re.search(rf"var\s+{re.escape(var_name)}\s*=\s*['\"]([^'\"]*)['\"]", html)
    if m:
        return m.group(1)
    return None


def extract_detail_info(html: str) -> Dict[str, Any]:
    """상세 페이지에서 가격/옵션/이미지 추출 (script 변수 우선)"""
    soup = BeautifulSoup(html, 'html.parser')
    info = {
        'product_name_js': '',
        'sale_price_js': 0,
        'is_soldout': False,
        'options': [],
        'images': [],
        'brand_from_table': '',
        'model_from_table': '',
    }

    # 1) script 변수: productName, productPrice, is_soldout_icon
    info['product_name_js'] = extract_js_var(html, 'productName') or ''
    price_raw = extract_js_var(html, 'productPrice')
    if price_raw:
        m = re.search(r'\d+', price_raw)
        if m:
            info['sale_price_js'] = int(m.group(0))
    soldout_raw = extract_js_var(html, 'is_soldout_icon')
    if soldout_raw and soldout_raw.upper() == 'T':
        info['is_soldout'] = True

    # 2) option_stock_data JSON (script 안의 {..})
    m = re.search(r"var\s+option_stock_data\s*=\s*'(\{.*?\})'\s*;", html)
    if m:
        try:
            raw_json = m.group(1)
            # JSON 안에 이스케이프된 유니코드가 있음 → json.loads로 파싱
            stock_data = json.loads(raw_json)
            for opt_code, opt in stock_data.items():
                opt_value = opt.get('option_value', '')
                stock_number = int(opt.get('stock_number', 0) or 0)
                is_selling = opt.get('is_selling', 'T') == 'T'
                is_soldout_orig = opt.get('use_soldout_original', 'F') == 'T'
                # "ONESIZE" 계열은 FREE로 통일
                clean_size = opt_value.strip()
                if clean_size.upper() in ('ONESIZE', 'ONE SIZE', '단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈', 'FREE'):
                    clean_size = 'FREE'
                info['options'].append({
                    'color': '',
                    'tag_size': clean_size,
                    'option_code': opt_code,
                    'status': 'in_stock' if (stock_number > 0 and is_selling) else 'out_of_stock',
                })
        except Exception as e:
            logger.debug(f"  option_stock_data 파싱 실패: {e}")

    # 3) 옵션 없으면 select#product_option_id1에서 fallback
    if not info['options']:
        option_select = soup.select_one('select#product_option_id1')
        if option_select:
            for opt in option_select.select('option'):
                opt_value = opt.get('value', '')
                if not opt_value or opt_value in ('*', '**'):
                    continue
                opt_text = opt.get_text(strip=True)
                if re.match(r'^[-=]{3,}$', opt_text.strip()):
                    continue
                is_soldout = '품절' in opt_text or opt.get('disabled') is not None
                clean_size = re.sub(r'\s*\[품절\]\s*', '', opt_text).strip()
                if clean_size.upper() in ('ONESIZE', 'ONE SIZE', '단일사이즈', '단일', 'FREE'):
                    clean_size = 'FREE'
                info['options'].append({
                    'color': '',
                    'tag_size': clean_size,
                    'option_code': opt_value,
                    'status': 'out_of_stock' if is_soldout else 'in_stock',
                })

    # 4) 이미지 추출 (detailArea > img.BigImage + xans-product-addimage)
    seen_imgs = set()
    # 메인 큰 이미지
    for img in soup.select('.detailArea img.BigImage'):
        src = img.get('src', '')
        if not src:
            continue
        if src.startswith('//'):
            src = 'https:' + src
        if src not in seen_imgs:
            seen_imgs.add(src)
            info['images'].append(src)
    # 추가 이미지 (ThumbImage는 small → big으로 치환)
    for img in soup.select('.xans-product-addimage img.ThumbImage'):
        src = img.get('src', '')
        if not src:
            continue
        src = src.replace('/product/small/', '/product/big/').replace('/product/extra/small/', '/product/extra/big/')
        if src.startswith('//'):
            src = 'https:' + src
        if src not in seen_imgs:
            seen_imgs.add(src)
            info['images'].append(src)

    # 5) 상세 테이블 (일부 상품만) — brand/model fallback
    for row in soup.select('table.fix_info tr, table.detail tr'):
        cls = ' '.join(row.get('class') or [])
        th = row.select_one('th')
        td = row.select_one('td')
        if not th or not td:
            continue
        th_text = th.get_text(strip=True)
        td_text = td.get_text(strip=True)
        if 'prd_brand_css' in cls or th_text == '브랜드':
            info['brand_from_table'] = td_text
        elif 'prd_model_css' in cls or th_text == '모델명':
            info['model_from_table'] = td_text

    return info


# ===========================================
# 데이터 변환
# ===========================================

def extract_model_id_from_name(product_name: str) -> str:
    """상품명 끝에서 모델번호 추출
    예: '26SS 스톤아일랜드 와펜 패치 스웨트 셔츠 L1S156100057 S0B50 V0029'
        → 'L1S156100057 S0B50 V0029'
    패턴: 영문대문자+숫자+공백의 마지막 연속 토큰들
    """
    # 괄호 안 모델 먼저 시도: "... (MODEL)"
    m = re.search(r'\(([A-Za-z0-9\s\-_.]+)\)\s*$', product_name)
    if m:
        return m.group(1).strip()
    # 마지막 공백 구분 토큰들 중 영문+숫자 패턴만 모아서 모델 추출
    tokens = product_name.split()
    model_tokens = []
    for tok in reversed(tokens):
        # 영문 대문자+숫자+일부 기호 구성
        if re.fullmatch(r'[A-Z0-9][A-Z0-9\-_.]*', tok):
            model_tokens.insert(0, tok)
        else:
            break
    if len(model_tokens) >= 1:
        return ' '.join(model_tokens)
    return ''


def convert_to_raw_data(list_item: Dict, detail_info: Dict, brand_name_en: str,
                        brand_name_ko: str, category_path: str = '') -> Optional[Dict]:
    """리스트 + 상세 데이터를 raw_scraped_data 형식으로 변환"""
    product_no = list_item['product_no']
    # 상품명은 script의 productName 우선 (더 깨끗함), 없으면 리스트 상품명
    product_name = detail_info.get('product_name_js') or list_item.get('product_name', '')
    # 혹시 '[브랜드] ' prefix가 남아있으면 제거
    product_name = re.sub(r'^\[.*?\]\s*', '', product_name).strip()

    # 모델번호: 상세 테이블 → 상품명 끝 토큰
    model_id = detail_info.get('model_from_table', '')
    if not model_id:
        model_id = extract_model_id_from_name(product_name)
    if not model_id:
        return None

    # 가격: 상세 script → 리스트 fallback
    original_price = list_item.get('original_price', 0) or 0
    sale_price = detail_info.get('sale_price_js', 0) or list_item.get('sale_price', 0) or 0
    if not original_price:
        original_price = sale_price

    # 재고: is_soldout_icon 이 T면 전체 품절, 아니면 options 기반
    options = detail_info.get('options', [])
    if detail_info.get('is_soldout'):
        stock_status = 'out_of_stock'
    elif options:
        stock_status = 'in_stock' if any(o.get('status') == 'in_stock' for o in options) else 'out_of_stock'
    else:
        stock_status = 'in_stock'

    raw_json = {
        'color': '',
        'options': options,
        'images': detail_info.get('images', []),
        'category': category_path,
        'cate_no': list_item.get('cate_no', ''),
        'brand_from_table': detail_info.get('brand_from_table', ''),
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    product_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={list_item.get('cate_no', '')}"

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': brand_name_en,
        'brand_name_kr': brand_name_ko,
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': category_path,
        'original_price': original_price,
        'raw_price': sale_price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': product_url,
    }


# ===========================================
# DB 조회/저장
# ===========================================

def get_brands_from_database(brand_filter: str = None) -> List[Dict]:
    with engine.connect() as conn:
        query = "SELECT mall_brand_name_en, mall_brand_name_ko, mall_brand_no FROM mall_brands WHERE mall_name = '9tems' AND is_active = 1"
        params = {}
        if brand_filter:
            query += " AND UPPER(mall_brand_name_en) = :brand"
            params['brand'] = brand_filter.upper()
        result = conn.execute(text(query), params)
        return [{'name_en': r[0], 'name_ko': r[1], 'cate_no': r[2]} for r in result]


def get_published_product_ids(brand_name: str = None) -> set:
    with engine.connect() as conn:
        query = """
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = '9tems' AND a.is_published = 1
        """
        if brand_name:
            query += " AND (UPPER(r.brand_name_en) = :brand OR UPPER(r.brand_name_kr) = :brand)"
            result = conn.execute(text(query), {"brand": brand_name.upper()})
        else:
            result = conn.execute(text(query))
        return {str(r[0]) for r in result}


def save_to_database(data_list: List[Dict]):
    if not data_list:
        return
    insert_sql = text("""
        INSERT INTO raw_scraped_data
        (source_site, mall_product_id, brand_name_en, brand_name_kr,
         product_name, p_name_full, model_id, category_path,
         original_price, raw_price, stock_status, raw_json_data, product_url)
        VALUES
        (:source_site, :mall_product_id, :brand_name_en, :brand_name_kr,
         :product_name, :p_name_full, :model_id, :category_path,
         :original_price, :raw_price, :stock_status, :raw_json_data, :product_url)
        ON DUPLICATE KEY UPDATE
        brand_name_en = VALUES(brand_name_en),
        brand_name_kr = VALUES(brand_name_kr),
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
    with engine.connect() as conn:
        for data in data_list:
            conn.execute(insert_sql, data)
        conn.commit()


# ===========================================
# 메인 실행
# ===========================================

def cmd_scan_brands(args):
    """_wg/import/brand.html에서 브랜드 리스트 추출 + mall_brands INSERT"""
    session_mgr = SessionManager()
    try:
        html, error = session_mgr.fetch_page(f'{BASE_URL}/_wg/import/brand.html')
        if error:
            logger.error(f"브랜드 페이지 수집 실패: {error}")
            return
        brands = scan_brands_from_page(html)
        logger.info(f"\n추출된 브랜드: {len(brands)}개")
        for b in brands[:20]:
            logger.info(f"  {b['name_en']:30s} cate_no={b['cate_no']}")
        if len(brands) > 20:
            logger.info(f"  ... +{len(brands)-20}개 더")

        if args.dry_run:
            logger.info("\n(--dry-run: DB 저장 생략)")
            return

        # mall_brands INSERT (이미 있으면 스킵)
        inserted = 0
        skipped = 0
        with engine.begin() as conn:
            for b in brands:
                exists = conn.execute(text("""
                    SELECT 1 FROM mall_brands
                    WHERE mall_name = '9tems'
                      AND (mall_brand_no = :cate_no OR UPPER(mall_brand_name_en) = UPPER(:en))
                    LIMIT 1
                """), {'cate_no': b['cate_no'], 'en': b['name_en']}).fetchone()
                if exists:
                    skipped += 1
                    continue
                conn.execute(text("""
                    INSERT INTO mall_brands
                      (mall_name, mall_brand_name_en, mall_brand_url, mall_brand_no,
                       is_active, mapping_level, is_mapped)
                    VALUES
                      ('9tems', :en, :url, :cate_no, 1, 0, 0)
                """), {'en': b['name_en'], 'url': b['url'], 'cate_no': b['cate_no']})
                inserted += 1
        logger.info(f"\nmall_brands INSERT: {inserted}건 (기존 {skipped}건 스킵)")
    finally:
        session_mgr.close()


def main():
    parser = argparse.ArgumentParser(description='구템즈(9tems) 상품 수집기')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='브랜드당 최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    parser.add_argument('--scan-brands', action='store_true', help='_wg/import/brand.html에서 브랜드 리스트만 출력')
    args = parser.parse_args()

    if args.scan_brands:
        cmd_scan_brands(args)
        return

    logger.info("=" * 60)
    logger.info(f"9tems 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    logger.info("=" * 60)

    brands = get_brands_from_database(args.brand)
    logger.info(f"대상 브랜드: {len(brands)}개")

    if not brands:
        logger.warning("수집할 브랜드가 없습니다. `--scan-brands`로 먼저 확인하세요.")
        return

    session_mgr = SessionManager()
    total_collected = 0
    total_skipped_no_model = 0

    try:
        for brand_idx, brand in enumerate(brands, 1):
            brand_name_en = brand['name_en']
            brand_name_ko = brand['name_ko']
            cate_no = brand['cate_no']

            logger.info(f"\n>>> [{brand_idx}/{len(brands)}] 브랜드: {brand_name_en} (cate_no={cate_no})")

            if session_mgr.is_blocked:
                logger.error("  차단 감지됨 — 수집 중단")
                break

            # 1) 브랜드 리스트 페이지 1페이지 (breadcrumb + 상품 + 마지막 페이지)
            first_url = f"{BASE_URL}/product/list.html?cate_no={cate_no}&page=1"
            html, error = session_mgr.fetch_page(first_url)
            if error:
                logger.warning(f"  브랜드 페이지 수집 실패: {error}")
                continue
            if not html:
                continue

            category_path = get_category_path(html) or brand_name_en
            last_page = get_last_page(html)
            items = get_product_list_from_page(html, cate_no)
            logger.info(f"  카테고리 경로: {category_path} | 페이지 {last_page}개 | p1 {len(items)}개")

            all_items = list(items)
            seen_pnos = {it['product_no'] for it in items}

            # 2) 나머지 페이지 순회
            for page in range(2, last_page + 1):
                if session_mgr.is_blocked:
                    break
                page_url = f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}"
                page_html, error = session_mgr.fetch_page(page_url)
                if error:
                    logger.warning(f"  p{page} 수집 실패: {error}")
                    continue
                if not page_html:
                    break
                page_items = get_product_list_from_page(page_html, cate_no)
                new_cnt = 0
                for it in page_items:
                    if it['product_no'] not in seen_pnos:
                        seen_pnos.add(it['product_no'])
                        all_items.append(it)
                        new_cnt += 1
                logger.info(f"    p{page}: {len(page_items)}개 발견, 신규 {new_cnt}개 (누적 {len(all_items)})")
                if not page_items:
                    break
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            logger.info(f"  리스트 수집 완료: {len(all_items)}개")
            if not all_items:
                continue

            # limit 적용
            if args.limit and len(all_items) > args.limit:
                all_items = all_items[:args.limit]

            # skip-existing
            if args.skip_existing:
                published_ids = get_published_product_ids(brand_name_en)
                before = len(all_items)
                all_items = [it for it in all_items if it['product_no'] not in published_ids]
                skipped = before - len(all_items)
                if skipped > 0:
                    logger.info(f"  등록 완료 스킵: {skipped}개, 수집 대상: {len(all_items)}개")

            # 3) 상세 수집 + 변환 + 저장
            batch_data = []
            skipped_no_model = 0
            total = len(all_items)

            for idx, list_item in enumerate(all_items, 1):
                if session_mgr.is_blocked:
                    logger.error("  차단 감지됨 — 상세 수집 중단")
                    break

                product_no = list_item['product_no']
                detail_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={cate_no}&display_group=1"

                detail_html, error = session_mgr.fetch_page(detail_url)
                if error:
                    logger.warning(f"  [{idx}/{total}] 상세 수집 실패: {error}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                detail_info = extract_detail_info(detail_html) if detail_html else {}

                data = convert_to_raw_data(list_item, detail_info, brand_name_en, brand_name_ko, category_path)
                if not data:
                    skipped_no_model += 1
                    logger.info(f"  [{idx}/{total}] SKIP (no model_id) | {list_item.get('product_name', '')[:50]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                raw = json.loads(data['raw_json_data'])
                logger.info(
                    f"  [{idx}/{total}] {data['model_id']:30s} | {data['raw_price']:>10,}원 | "
                    f"img:{len(raw['images'])} opt:{len(raw['options'])} | {data['product_name'][:40]}"
                )
                total_collected += 1

                if not args.dry_run:
                    batch_data.append(data)

                if len(batch_data) >= 10:
                    save_to_database(batch_data)
                    logger.info(f"  DB 저장: {len(batch_data)}개")
                    batch_data = []

                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            if batch_data and not args.dry_run:
                save_to_database(batch_data)
                logger.info(f"  DB 저장(잔여): {len(batch_data)}개")

            total_skipped_no_model += skipped_no_model
            logger.info(f"  {brand_name_en} 완료: model_id 없어서 스킵 {skipped_no_model}개")

    finally:
        session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info(f"9tems 수집 완료")
    logger.info(f"  총 수집: {total_collected}개")
    logger.info(f"  model_id 없어서 스킵: {total_skipped_no_model}개")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :site"
            ), {'site': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 9tems 상품: {count}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
