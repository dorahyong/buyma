# -*- coding: utf-8 -*-
"""
브릭맨션(brickmansion.co.kr) 상품 수집 스크립트
- Cafe24 기반 쇼핑몰 → HTML 스크래핑
- 브랜드별 순회 방식 (mall_brands.mall_brand_no = 공급사 코드 SxxxxxxX)
  · 리스트: /supply/index.html?supplier_code={code}&page={n}
  · 상세:   /product/{slug}/{product_no}/category/{cate_no}/display/1/  (리스트 a href 그대로)
  · 브랜드명은 순회 루프에서 확정 (영문). 상세 #supplyLink에도 영문 브랜드 있음.
- raw_scraped_data 테이블에 source_site='brickmansion'으로 저장
- 이미지는 raw_json_data.images에 저장 → ace_product_images 이관은 raw→ace 변환 단계에서

사용법:
    python brickmansion_collector.py                       # 전체 브랜드
    python brickmansion_collector.py --brand "ADIDAS"      # 특정 브랜드만
    python brickmansion_collector.py --limit 10            # 브랜드당 최대 10개
    python brickmansion_collector.py --dry-run             # DB 저장 없이 테스트
    python brickmansion_collector.py --skip-existing       # 등록 완료 상품 스킵

★ 봇 감지 방지: labellusso 수집기와 동일 (30개마다 세션 교체 + 메인 방문, 랜덤 프로필)
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
from collections import Counter

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

BASE_URL = 'https://brickmansion.co.kr'
SOURCE_SITE = 'brickmansion'
SESSION_REFRESH_INTERVAL = 30
MAX_CONSECUTIVE_TIMEOUTS = 5
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.8

BROWSER_PROFILES = [
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cache-Control': 'max-age=0',
    },
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cache-Control': 'max-age=0',
    },
    {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'Cache-Control': 'max-age=0',
    },
]


# ===========================================
# 세션 관리 (labellusso 수집기와 동일)
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

            logger.info("  [세션] 새 세션 시작 - 메인 페이지 방문 중...")
            response = self.session.get(f'{BASE_URL}/index.html', timeout=15)

            if response.status_code != 200:
                return False, f"메인 페이지 접속 실패: {response.status_code}"

            product_headers = self.profile.copy()
            product_headers['Referer'] = f'{BASE_URL}/'
            product_headers['Sec-Fetch-Site'] = 'same-origin'
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
# 공통 유틸
# ===========================================

def _parse_price(text_value: str) -> int:
    """가격 문자열에서 첫 숫자 그룹만 정수로 추출"""
    if not text_value:
        return 0
    m = re.search(r'[\d,]{2,}', text_value)
    return int(m.group().replace(',', '')) if m else 0


# 시즌 토큰(25SS 등) / 노이즈 토큰(3종 등) — 모델번호 판정에서 제외
SEASON_RE = re.compile(r'^\d{2}(SS|FW|FA|AW|SU|CR|PF|RE)$', re.I)
NOISE_RE = re.compile(r'^\d+(종|개|입|색|컬러|사이즈|인치|차|단)$')


def extract_model_id_from_name(product_name: str) -> str:
    """상품명에서 모델번호 추출

    - `[LAST SIZE]` `[본사공식]` 등 대괄호 태그, `>>` 뒤 메모 제거
    - 끝 괄호 안 우선 (NIKE/ADIDAS "(848386-001)", 뉴발란스 "(UTRNAF)", 우포스 "(OORIGINAL-BLACK)")
    - 없으면 한글 아닌 토큰의 연속 run을 (숫자포함 > 끝쪽 > 길이)로 선택
      → 하루타 "158-BLACK"처럼 중간에 있는 {코드}-{색상}도 포착, 숫자 없는 모델명도 허용

    예) "나이키 에어 리프트 BR (848386-001)"      → "848386-001"
        "하루타 158-BLACK 여성 가죽 홀스빗 로퍼"   → "158-BLACK"
        "[BRAND SALE] 뉴발란스 런닝화 프레쉬폼 (UTRNAF)" → "UTRNAF"
    """
    if not product_name:
        return ''
    # 대괄호 태그 + 뒤쪽 메모 제거
    name = re.sub(r'\[[^\]]*\]', ' ', product_name)
    name = re.sub(r'\s*>>.*$', '', name).strip()

    # 1) 끝 괄호 안 (숫자 포함 또는 4자 이상이면 모델로 인정)
    m = re.search(r'\(([^()]+)\)\s*$', name)
    if m:
        inner = m.group(1).strip()
        if re.search(r'\d', inner) or len(inner) >= 4:
            return inner
    name = re.sub(r'\([^()]*\)', ' ', name)  # 남은 괄호 제거

    # 2) 한글 아닌 토큰 연속 run
    tokens = [t for t in name.split() if not SEASON_RE.match(t) and not NOISE_RE.match(t)]
    runs, cur = [], []
    for t in tokens:
        if re.search(r'[가-힣]', t):
            if cur:
                runs.append(cur)
                cur = []
        else:
            cur.append(t)
    if cur:
        runs.append(cur)

    best, best_key = None, None
    for i, run in enumerate(runs):
        joined = ' '.join(run)
        has_digit = bool(re.search(r'\d', joined))
        if not has_digit and len(joined) < 4:
            continue
        key = (has_digit, i == len(runs) - 1, len(run))
        if best_key is None or key > best_key:
            best_key, best = key, joined
    return best or ''


def extract_brand_from_name(product_name: str) -> str:
    """상품명 한글 앞단어를 브랜드로 추출 (#supplyLink 비었을 때 fallback)

    예) "나이키 에어 리프트 BR (848386-001)" → "나이키"
    ⚠️ 첫 토큰 기준 (대괄호 태그/시즌 제외). 추후 보정.
    """
    if not product_name:
        return ''
    name = re.sub(r'\[[^\]]*\]', ' ', product_name)
    for tok in name.strip().split():
        if SEASON_RE.match(tok) or NOISE_RE.match(tok):
            continue
        return tok
    return ''


# ===========================================
# 리스트 페이지 파싱
# ===========================================

def get_product_list_from_page(html: str) -> List[Dict]:
    """공급사 리스트 페이지 HTML에서 상품 카드 추출

    brickmansion(Cafe24) 리스트 구조:
    - <ul class="prdList ..."> > <li id="anchorBoxId_{product_no}">
    - 상세 링크: a[href^="/product/"] (slug/{no}/category/{cate}/display/1/)
    - 상품명: strong.name a
    - 이미지: img.lazy[data-src] (지연로딩, src는 base64 placeholder)
    """
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    items = soup.select('ul.prdList li[id^="anchorBoxId_"]')
    for item in items:
        try:
            item_id = item.get('id', '')
            product_no = item_id.replace('anchorBoxId_', '')
            if not product_no:
                continue

            link = item.select_one('strong.name a[href^="/product/"]') or item.select_one('a[href^="/product/"]')
            detail_path = link.get('href', '') if link else ''
            detail_url = f"{BASE_URL}{detail_path}" if detail_path.startswith('/') else detail_path

            name_elem = item.select_one('strong.name a')
            product_name = ''
            if name_elem:
                for hidden in name_elem.select('.title, .displaynone'):
                    hidden.decompose()
                for br in name_elem.find_all('br'):
                    br.replace_with(' ')
                product_name = re.sub(r'\s+', ' ', name_elem.get_text(strip=True)).strip()

            img_elem = item.select_one('img.lazy') or item.select_one('.thumbnail img')
            image_url = ''
            if img_elem:
                image_url = img_elem.get('data-src', '') or img_elem.get('src', '')
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url
                if image_url.startswith('data:'):
                    image_url = ''

            if not product_name and not detail_url:
                continue

            products.append({
                'product_no': product_no,
                'product_name': product_name,
                'detail_url': detail_url,
                'list_image': image_url,
            })
        except Exception as e:
            logger.warning(f"  리스트 아이템 파싱 오류: {e}")
            continue

    return products


def get_last_page(html: str) -> int:
    """페이지네이션에서 마지막 페이지 번호 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    max_page = 1
    for a in soup.select('.xans-product-normalpaging a'):
        m = re.search(r'page=(\d+)', a.get('href', ''))
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


# ===========================================
# 상세 페이지 파싱
# ===========================================

def _clean_text(node) -> str:
    for br in node.find_all('br'):
        br.replace_with(' ')
    return re.sub(r'\s+', ' ', node.get_text(strip=True)).strip()


def extract_detail_info(html: str) -> Dict[str, Any]:
    """상세 페이지에서 상품명/가격/옵션/이미지 추출

    brickmansion(Cafe24) 상세 구조:
    - 상품명: div.infoArea h1.name
    - 판매가: #span_product_price_text / 소비자가: #span_product_price_custom strike
    - 브랜드: #supplyLink (영문, 정적)
    - 옵션: option_stock_data JS변수 (button/select는 품절옵션 누락) / 전체품절 is_soldout_icon
    - 이미지: div.prdImgView img.ThumbImage (슬라이드)
    """
    soup = BeautifulSoup(html, 'html.parser')
    info = {
        'product_name': '',
        'brand': '',
        'price': 0,
        'original_price': 0,
        'options': [],
        'images': [],
        'sold_out': False,
    }

    # 상품명 — infoArea 내부 우선 (info_name 쪽엔 가격 div가 섞여 있음)
    name_elem = soup.select_one('div.infoArea h1.name') or soup.select_one('h1.name')
    if name_elem:
        for junk in name_elem.select('div, span.custom_pro'):
            junk.decompose()
        info['product_name'] = _clean_text(name_elem)

    # 브랜드 — 공급사 링크(#supplyLink, 영문)
    sl = soup.select_one('#supplyLink')
    if sl:
        info['brand'] = sl.get_text(strip=True)

    # 가격
    price_elem = soup.select_one('#span_product_price_text')
    if price_elem:
        info['price'] = _parse_price(price_elem.get_text())
    custom_elem = soup.select_one('#span_product_price_custom strike') or soup.select_one('#span_product_price_custom')
    if custom_elem:
        info['original_price'] = _parse_price(custom_elem.get_text())

    # 상품 전체 품절 여부 (is_soldout_icon=T)
    som = re.search(r"var\s+is_soldout_icon\s*=\s*'([^']*)'", html)
    info['sold_out'] = bool(som) and som.group(1) == 'T'

    # 옵션 — option_stock_data(전체 옵션+재고) 우선.
    #   button/select는 '판매중 사이즈'만 보여줘서 품절 옵션이 누락됨
    m = re.search(r"var\s+option_stock_data\s*=\s*'([^']*)'", html)
    if m:
        try:
            stock = json.loads(m.group(1).replace('\\"', '"'))
            for code, opt in stock.items():
                val = (opt.get('option_value', '') or '').strip()
                selling = opt.get('is_selling', 'T') == 'T'
                num = int(opt.get('stock_number', 0) or 0)
                in_stock = selling and num > 0 and not info['sold_out']
                if val in ('단일사이즈', '단일 사이즈', 'ONE', 'ONE SIZE', 'ONESIZE', '원사이즈'):
                    val = 'FREE'
                info['options'].append({
                    'tag_size': val,
                    'option_code': code,
                    'status': 'in_stock' if in_stock else 'out_of_stock',
                })
        except (json.JSONDecodeError, AttributeError, ValueError):
            pass

    # fallback: option_stock_data 없을 때만 버튼/select
    if not info['options']:
        seen_opt = set()
        button_opts = soup.select('ul.ec-product-button li[option_value]')
        if button_opts:
            for li in button_opts:
                value = li.get('option_value', '')
                if not value or value in ('*', '**') or value in seen_opt:
                    continue
                seen_opt.add(value)
                label = li.get('title', '') or li.get_text(strip=True)
                is_soldout = ('품절' in label) or ('soldout' in (li.get('class') or []))
                clean_size = re.sub(r'\s*\(?품절\)?\s*', '', label).strip()
                if clean_size in ('단일사이즈', '단일 사이즈', 'ONE', 'ONE SIZE', 'ONESIZE', '원사이즈', 'FREE'):
                    clean_size = 'FREE'
                info['options'].append({
                    'tag_size': clean_size,
                    'option_code': value,
                    'status': 'out_of_stock' if is_soldout else 'in_stock',
                })
        else:
            option_select = soup.select_one('select.ProductOption0, select[id^="product_option_id"]')
            if option_select:
                for opt in option_select.select('option'):
                    value = opt.get('value', '')
                    if not value or value in ('*', '**'):
                        continue
                    opt_text = opt.get_text(strip=True)
                    if re.fullmatch(r'[-=]{3,}', opt_text) or opt_text == 'empty':
                        continue
                    is_soldout = ('품절' in opt_text) or (opt.get('disabled') is not None)
                    clean_size = re.sub(r'\s*\(?품절\)?\s*', '', opt_text).strip()
                    if clean_size in ('단일사이즈', '단일 사이즈', 'ONE', 'ONE SIZE', 'ONESIZE', '원사이즈', 'FREE'):
                        clean_size = 'FREE'
                    info['options'].append({
                        'tag_size': clean_size,
                        'option_code': value,
                        'status': 'out_of_stock' if is_soldout else 'in_stock',
                    })

    # 이미지 — 상세 이미지 슬라이드
    for img in soup.select('div.prdImgView img.ThumbImage, .xans-product-mobileimage img.ThumbImage'):
        src = img.get('src', '')
        if src.startswith('//'):
            src = 'https:' + src
        if src and not src.startswith('data:') and src not in info['images']:
            info['images'].append(src)

    return info


# ===========================================
# 데이터 변환
# ===========================================

def convert_to_raw_data(list_item: Dict, detail_info: Dict, category_path: str = '') -> Optional[Dict]:
    """리스트 + 상세 데이터를 raw_scraped_data 형식으로 변환 (브랜드=#supplyLink 영문)"""

    product_no = list_item['product_no']
    product_name = detail_info.get('product_name') or list_item.get('product_name', '')
    if not product_name:
        return None
    if '리퍼브' in product_name:
        return None  # [리퍼브] 제품 제외

    model_id = extract_model_id_from_name(product_name)
    if not model_id:
        return None

    # 브랜드: 상세 #supplyLink(영문) 우선, 없으면 상품명 한글앞단어
    brand_name = detail_info.get('brand') or extract_brand_from_name(product_name)

    price = detail_info.get('price', 0)
    original_price = detail_info.get('original_price', 0) or price

    options = detail_info.get('options', [])
    if detail_info.get('sold_out'):
        stock_status = 'out_of_stock'
    elif any(opt.get('status') == 'in_stock' for opt in options):
        stock_status = 'in_stock'
    elif not options:
        stock_status = 'in_stock'
    else:
        stock_status = 'out_of_stock'

    raw_json = {
        'options': options,
        'images': detail_info.get('images', []),
        'list_image': list_item.get('list_image', ''),
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': brand_name,
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': category_path,
        'original_price': original_price,
        'raw_price': price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': list_item.get('detail_url', ''),
    }


# ===========================================
# DB 조회/저장
# ===========================================

def get_brands_from_database(brand_filter: str = None) -> List[Dict]:
    with engine.connect() as conn:
        query = "SELECT mall_brand_name_en, mall_brand_no, mall_brand_url FROM mall_brands WHERE mall_name = :site AND is_active = 1"
        params = {'site': SOURCE_SITE}
        if brand_filter:
            query += " AND UPPER(mall_brand_name_en) = :brand"
            params['brand'] = brand_filter.upper()
        result = conn.execute(text(query), params)
        return [{'name_en': r[0], 'supplier_code': r[1], 'brand_url': r[2]} for r in result]


def get_published_product_ids() -> set:
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = :site AND a.is_published = 1
        """), {'site': SOURCE_SITE})
        return {str(r[0]) for r in result}


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
    with engine.connect() as conn:
        for data in data_list:
            conn.execute(insert_sql, data)
        conn.commit()


# ===========================================
# 카테고리 채우기 (--categories 모드)
# ===========================================
# brickmansion은 공급사별로 수집해 category_path가 빈 채로 저장됨. 사이트 카테고리 페이지를
# 따로 돌아 product_no→경로를 만들고 raw_scraped_data.category_path만 UPDATE한다.
# (categories.html 하단 JSON으로 카테고리 트리 파싱. NEW/SALE/EVENT/BRAND 제외)

def parse_category_nodes(html: str) -> List[Tuple[str, str, int]]:
    """categories.html 하단 JSON → [(cate_no, path, spec)] (spec: 2=하위, 1=상위)"""
    m = re.search(r'\[\s*\{.*\}\s*\]', html, re.DOTALL)
    if not m:
        return []
    data = json.loads(m.group(0))
    exclude = {'NEW', 'SALE', 'EVENT', 'BRAND'}
    tops = {d['cate_no']: d['name'] for d in data
            if d['parent_cate_no'] == 1 and d['name'].upper() not in exclude}
    nodes = {}
    for cate, name in tops.items():
        nodes[str(cate)] = (name, 1)
    for d in data:
        if d['parent_cate_no'] in tops:
            nodes[str(d['cate_no'])] = (f"{tops[d['parent_cate_no']]} > {d['name']}", 2)
    return [(c, p, s) for c, (p, s) in nodes.items()]


def get_collected_product_ids() -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT mall_product_id FROM raw_scraped_data WHERE source_site = :s"
        ), {'s': SOURCE_SITE})
        return {str(r[0]) for r in rows}


def _crawl_category_pnos(sm: 'SessionManager', cate_no: str) -> List[str]:
    pnos = []
    first, err = sm.fetch_page(f"{BASE_URL}/product/list.html?cate_no={cate_no}&page=1")
    if err or not first:
        return pnos
    last = get_last_page(first)
    for page in range(1, last + 1):
        html = first if page == 1 else sm.fetch_page(
            f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}")[0]
        if not html:
            continue
        items = get_product_list_from_page(html)
        if not items:
            break
        pnos.extend(it['product_no'] for it in items)
        if page > 1:
            time.sleep(random.uniform(0.2, 0.4))
    return pnos


def fill_categories(dry_run: bool = True):
    """카테고리 페이지를 크롤해 category_path를 채운다 (하위→상위 순, 가장 구체적 우선)"""
    here = os.path.dirname(os.path.abspath(__file__))
    nodes = parse_category_nodes(open(os.path.join(here, 'categories.html'), encoding='utf-8').read())
    nodes.sort(key=lambda x: -x[2])
    logger.info("=" * 60)
    logger.info(f"브릭맨션 카테고리 채우기 (Mode: {'DRY-RUN' if dry_run else 'APPLY'})")
    logger.info(f"카테고리 노드 {len(nodes)}개")

    collected = get_collected_product_ids()
    logger.info(f"raw_scraped_data brickmansion 상품 {len(collected)}개")

    sm = SessionManager()
    product_path = {}
    try:
        for cate_no, path, spec in nodes:
            if sm.is_blocked:
                logger.error("차단 감지 — 중단")
                break
            pnos = _crawl_category_pnos(sm, cate_no)
            new = sum(1 for p in pnos if p not in product_path)
            for p in pnos:
                product_path.setdefault(p, path)
            logger.info(f"  [{path}] {len(pnos)}개 (신규배정 {new})")
            time.sleep(random.uniform(0.2, 0.4))
    finally:
        sm.close()

    matched = {p: path for p, path in product_path.items() if p in collected}
    unmatched = collected - set(matched)
    logger.info("\n" + "=" * 60)
    logger.info(f"category_path 배정: {len(matched)}/{len(collected)}개 (미배정 {len(unmatched)})")
    for path, n in Counter(matched.values()).most_common(15):
        logger.info(f"  {n:4}  {path}")

    if dry_run:
        logger.info("\n[미리보기 모드] DB 변경 없음. 적용하려면 --categories (--dry-run 빼고)")
        return

    update_sql = text("""
        UPDATE raw_scraped_data SET category_path = :path, updated_at = NOW()
        WHERE source_site = :site AND mall_product_id = :pno
    """)
    with engine.connect() as conn:
        cnt = 0
        for pno, path in matched.items():
            conn.execute(update_sql, {'path': path, 'site': SOURCE_SITE, 'pno': pno})
            cnt += 1
            if cnt % 200 == 0:
                conn.commit()
        conn.commit()
    logger.info(f"\n✅ category_path UPDATE 완료: {len(matched)}개")


# ===========================================
# 메인 실행
# ===========================================

def _build_list_url(brand: Dict, page: int) -> str:
    path = brand.get('brand_url') or f"/supply/index.html?supplier_code={brand['supplier_code']}"
    if not path.startswith('http'):
        path = f"{BASE_URL}{path}"
    sep = '&' if '?' in path else '?'
    return f"{path}{sep}page={page}"


def main():
    parser = argparse.ArgumentParser(description='브릭맨션 상품 수집기 (카테고리 전체 스윕)')
    parser.add_argument('--limit', type=int, help='수집할 최대 상품 수 (테스트용)')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트 / 카테고리 미리보기')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    parser.add_argument('--categories', action='store_true', help='상품 수집 대신 category_path만 채우기')
    args = parser.parse_args()

    if args.categories:
        fill_categories(dry_run=args.dry_run)
        return

    logger.info("=" * 60)
    logger.info(f"브릭맨션 전체 카테고리 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    logger.info("=" * 60)

    # 1) 카테고리 노드 (하위→상위, 가장 구체적 우선)
    here = os.path.dirname(os.path.abspath(__file__))
    nodes = parse_category_nodes(open(os.path.join(here, 'categories.html'), encoding='utf-8').read())
    nodes.sort(key=lambda x: -x[2])
    logger.info(f"카테고리 노드 {len(nodes)}개")

    published_ids = get_published_product_ids() if args.skip_existing else set()
    if args.skip_existing:
        logger.info(f"  등록 완료 상품 {len(published_ids)}개 — 스킵 대상")

    session_mgr = SessionManager()
    product_map = {}          # product_no -> {'item': list_item, 'path': category_path}
    total_collected = 0
    total_skipped_no_model = 0

    try:
        # 2) 전 카테고리 스윕 → product_no별 (list_item, 가장 구체적 category_path)
        for cate_no, path, spec in nodes:
            if session_mgr.is_blocked:
                logger.error("  차단 감지 — 리스트 수집 중단")
                break
            first, error = session_mgr.fetch_page(f"{BASE_URL}/product/list.html?cate_no={cate_no}&page=1")
            if error or not first:
                continue
            last = get_last_page(first)
            cat_new = 0
            for page in range(1, last + 1):
                if session_mgr.is_blocked:
                    break
                html = first if page == 1 else session_mgr.fetch_page(
                    f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}")[0]
                if not html:
                    continue
                items = get_product_list_from_page(html)
                if not items:
                    break
                for it in items:
                    if '리퍼브' in it.get('product_name', ''):
                        continue  # [리퍼브] 제품 제외
                    if it['product_no'] not in product_map:
                        product_map[it['product_no']] = {'item': it, 'path': path}
                        cat_new += 1
                if page > 1:
                    time.sleep(random.uniform(0.2, 0.4))
            logger.info(f"  [{path}] 신규 {cat_new} (누적 {len(product_map)})")

        logger.info(f"\n리스트 스윕 완료: 전체 {len(product_map)}개 상품")

        # 3) 상품별 상세 수집 → 변환 → 저장
        targets = list(product_map.items())
        if args.skip_existing:
            targets = [(p, v) for p, v in targets if p not in published_ids]
        if args.limit:
            targets = targets[:args.limit]
        total = len(targets)
        batch_data = []
        for idx, (pno, info) in enumerate(targets, 1):
            if session_mgr.is_blocked:
                logger.error("  차단 감지 — 상세 수집 중단")
                break
            list_item, path = info['item'], info['path']

            detail_html, error = session_mgr.fetch_page(list_item['detail_url'])
            if error:
                logger.warning(f"  [{idx}/{total}] 상세 실패: {error} | {list_item['product_name'][:30]}")
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                continue

            detail_info = extract_detail_info(detail_html) if detail_html else {}
            data = convert_to_raw_data(list_item, detail_info, path)
            if not data:
                total_skipped_no_model += 1
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                continue

            total_collected += 1
            if idx <= 5 or idx % 50 == 0:
                logger.info(f"  [{idx}/{total}] {data['brand_name_en']} | {data['model_id']} | {data['raw_price']:>10,}원 | {path}")

            if not args.dry_run:
                batch_data.append(data)
                if len(batch_data) >= 10:
                    save_to_database(batch_data)
                    batch_data = []

            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        if batch_data and not args.dry_run:
            save_to_database(batch_data)

    finally:
        session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info("브릭맨션 수집 완료")
    logger.info(f"  총 수집: {total_collected}개")
    logger.info(f"  model_id 없어서 스킵: {total_skipped_no_model}개")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :site"
            ), {'site': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 brickmansion 상품: {count}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
