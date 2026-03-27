# -*- coding: utf-8 -*-
"""
라벨루쏘(labellusso.com) 상품 수집 스크립트
- Cafe24 기반 쇼핑몰 → HTML 스크래핑
- 리스트 페이지: /product/list.html?cate_no={mall_brand_no}&page={n}
- 상세 페이지: /product/detail.html?product_no={no}&cate_no={cate_no}
- raw_scraped_data 테이블에 source_site='labellusso'로 저장

사용법:
    python labellusso_collector.py                        # 전체 실행
    python labellusso_collector.py --brand "A.P.C"        # 특정 브랜드만
    python labellusso_collector.py --limit 10             # 브랜드당 최대 10개
    python labellusso_collector.py --dry-run              # DB 저장 없이 테스트
    python labellusso_collector.py --skip-existing        # 등록 완료 상품 스킵

★ 봇 감지 방지:
- 30개마다 세션 교체 + 메인 페이지 방문
- 랜덤 브라우저 프로필
- 타임아웃 연속 5회 시 차단 감지 및 중지
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

BASE_URL = 'https://www.labellusso.com'
SOURCE_SITE = 'labellusso'
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

            logger.info(f"  [세션] 새 세션 준비 완료 (쿠키 획득됨)")
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
# 리스트 페이지 파싱
# ===========================================

def get_product_list_from_page(html: str, cate_no: str) -> List[Dict]:
    """리스트 페이지 HTML에서 상품 기본 정보 추출

    labellusso 리스트 구조:
    - <li id="anchorBoxId_{product_no}">
    - 상품명: <strong class="name"><a> 내부 (br 태그로 구분)
    - 가격: data-prod-custom (소비자가), data-prod-price (판매가)
    - 이미지: <img class="thumb">
    """
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    items = soup.select('ul.prdList li[id^="anchorBoxId_"]')
    for item in items:
        try:
            # product_no
            item_id = item.get('id', '')
            product_no = item_id.replace('anchorBoxId_', '')
            if not product_no:
                continue

            # 상품명 — [BRAND]<br>시즌 설명<br>MODEL_NO 형태
            name_elem = item.select_one('strong.name a')
            if not name_elem:
                name_elem = item.select_one('p.name a span')
            raw_name = ''
            if name_elem:
                # displaynone 요소 제거 (숨겨진 "상품명 :" 라벨)
                for hidden in name_elem.select('.displaynone'):
                    hidden.decompose()
                # br 태그를 공백으로 치환하여 텍스트 추출
                for br in name_elem.find_all('br'):
                    br.replace_with(' ')
                raw_name = name_elem.get_text(strip=True)

            # [브랜드명] 접두어 제거
            product_name = re.sub(r'^\[.*?\]\s*', '', raw_name).strip()

            # 가격 — data 속성에서 추출
            discount_elem = item.select_one('span.discount_rate')
            original_price = 0
            sale_price = 0
            if discount_elem:
                custom = discount_elem.get('data-prod-custom', '')
                price = discount_elem.get('data-prod-price', '')
                if custom:
                    original_price = int(re.sub(r'[^0-9]', '', custom) or 0)
                if price:
                    sale_price = int(re.sub(r'[^0-9]', '', price) or 0)

            # fallback: p.price, p.sale
            if not original_price:
                price_elem = item.select_one('p.price')
                if price_elem:
                    price_text = re.sub(r'[^0-9]', '', price_elem.get_text(strip=True))
                    if price_text:
                        original_price = int(price_text)
            if not sale_price:
                sale_elem = item.select_one('p.sale')
                if sale_elem:
                    sale_text = sale_elem.contents[0] if sale_elem.contents else ''
                    if hasattr(sale_text, 'get_text'):
                        sale_text = sale_text.get_text(strip=True)
                    else:
                        sale_text = str(sale_text).strip()
                    price_match = re.sub(r'[^0-9]', '', sale_text)
                    if price_match:
                        sale_price = int(price_match)

            # 이미지
            img_elem = item.select_one('img.thumb')
            if not img_elem:
                img_elem = item.select_one('a.prdImg img')
            image_url = ''
            if img_elem:
                image_url = img_elem.get('src', '') or img_elem.get('data-original', '')
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url

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


def extract_subcategories(html: str) -> List[Dict]:
    """브랜드 리스트 페이지의 사이드바에서 서브카테고리 추출

    labellusso 구조: ul.menuCategory > li.xans-product-displaycategory
    [여성] 가방 하위에 크로스, 토트 등
    [여성] → WOMEN, [남성] → MEN, [공용] → WOMEN 으로 변환
    수집처 데이터 기준 그대로 저장
    """
    soup = BeautifulSoup(html, 'html.parser')
    menu = soup.select_one('ul.menuCategory')
    if not menu:
        return []

    gender_map = {'여성': 'WOMEN', '남성': 'MEN', '공용': 'WOMEN'}
    categories = []

    for top_li in menu.select('li.xans-product-displaycategory'):
        top_a = top_li.find('a', recursive=False)
        if not top_a:
            continue
        top_text = top_a.get_text(strip=True)
        m = re.match(r'\[(.+?)\]\s*(.+?)(?:\s*\(\d+\))?$', top_text)
        if not m:
            continue
        gender_prefix = gender_map.get(m.group(1).strip(), 'WOMEN')
        depth1 = m.group(2).strip()

        children_ul = top_li.find('ul', class_='xans-product-children')
        if children_ul:
            for child_li in children_ul.find_all('li', recursive=False):
                child_a = child_li.find('a')
                if child_a:
                    child_text = child_a.get_text(strip=True)
                    depth2 = re.sub(r'\s*\(\d+\)$', '', child_text).strip()
                    url = child_a.get('href', '')
                    categories.append({
                        'name': f"{gender_prefix} > {depth1} > {depth2}",
                        'url': url,
                    })
        else:
            url = top_a.get('href', '')
            categories.append({
                'name': f"{gender_prefix} > {depth1}",
                'url': url,
            })

    return categories


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

def extract_detail_info(html: str) -> Dict[str, Any]:
    """상세 페이지에서 모델번호, 브랜드, 옵션, 이미지 등 추출

    labellusso 상세 페이지 특징:
    - 모델명: prd_model_css 클래스 또는 상품코드 영역
    - 브랜드: prd_brand_css 클래스
    - 옵션: option_stock_data JS 변수 (JSON)
    - 이미지: xans-product-addimage 영역
    """
    soup = BeautifulSoup(html, 'html.parser')
    info = {
        'model_id': '',
        'brand_name': '',
        'item_type': '',
        'origin': '',
        'material': '',
        'color': '',
        'set': '',
        'options': [],
        'measurements': {},
        'images': [],
    }

    # 브랜드 — prd_brand_css (item_cont에서 값만 추출)
    brand_elem = soup.select_one('.prd_brand_css .item_cont')
    if not brand_elem:
        brand_elem = soup.select_one('tr.prd_brand_css td')
    if brand_elem:
        info['brand_name'] = brand_elem.get_text(strip=True)

    # 모델명 — prd_model_css (item_cont에서 값만 추출)
    model_elem = soup.select_one('.prd_model_css .item_cont')
    if not model_elem:
        model_elem = soup.select_one('tr.prd_model_css td')
    if model_elem:
        info['model_id'] = model_elem.get_text(strip=True)

    # 상품 정보 테이블 (grp_product_price 영역)
    for row in soup.select('.xans-product-detaildesign tr, table.detail tr'):
        th = row.select_one('th')
        td = row.select_one('td')
        if not th or not td:
            continue
        header = th.get_text(strip=True).replace(' ', '')
        value = td.get_text(strip=True)

        if not info['model_id'] and ('모델명' in header or header.upper() in ('MODEL',)):
            info['model_id'] = value
        elif '종류' in header or 'ITEM' in header.upper():
            info['item_type'] = value
        elif '원산지' in header or 'ORIGN' in header.upper():
            info['origin'] = value
        elif '소재' in header or 'MATERIAL' in header.upper():
            info['material'] = value
        elif '색상' in header or 'COLOR' in header.upper():
            info['color'] = value
        elif '구성' in header or 'SET' in header.upper():
            info['set'] = value

    # #desctable에서 색상, 사이즈, 모델명 추출 (없으면 수집 스킵 대상)
    desctable = soup.select_one('#desctable')
    desctable_sizes = []
    if desctable:
        for row in desctable.select('tr'):
            tds = row.select('td')
            if len(tds) < 2:
                continue
            header = tds[0].get_text(strip=True).replace(' ', '')
            value = tds[1].get_text(strip=True)

            if '모델명' in header and not info['model_id']:
                info['model_id'] = value
            elif '색상' in header and value:
                info['color'] = value
            elif '사이즈' in header and value:
                # "XXS,XS,S,M,L,XL,XXL,XXXL" → 리스트로 분리
                desctable_sizes = [s.strip() for s in value.split(',') if s.strip()]

    # option_stock_data JS 변수 파싱 (재고 정보)
    stock_data = {}
    stock_match = re.search(r"option_stock_data\s*=\s*'(.*?)'", html, re.DOTALL)
    if stock_match:
        try:
            # \" → " 치환 (HTML 내 JS 이스케이프)
            raw_stock = stock_match.group(1).replace('\\"', '"')
            stock_data = json.loads(raw_stock)
        except (json.JSONDecodeError, AttributeError):
            pass

    # 옵션 생성: desctable 사이즈 기준 + option_stock_data 재고 매칭
    if desctable_sizes:
        # option_stock_data에서 option_value → 재고 상태 매핑
        stock_by_size = {}
        for opt_code, opt_info in stock_data.items():
            opt_value = opt_info.get('option_value', '').strip()
            is_selling = opt_info.get('is_selling', 'F') == 'T'
            is_display = opt_info.get('is_display', 'F') == 'T'
            stock_num = opt_info.get('stock_number', 0)
            in_stock = is_selling and is_display and stock_num > 0
            stock_by_size[opt_value] = {
                'status': 'in_stock' if in_stock else 'out_of_stock',
                'option_code': opt_code,
            }

        for size in desctable_sizes:
            norm_size = size.strip()
            if norm_size in ['단일사이즈', '단일 사이즈', 'ONE SIZE', 'ONESIZE', '원사이즈']:
                norm_size = 'FREE'

            # option_stock_data에서 매칭 (정확 매칭 우선, 없으면 포함 매칭)
            matched = stock_by_size.get(norm_size)
            if not matched:
                for k, v in stock_by_size.items():
                    if norm_size in k or k in norm_size:
                        matched = v
                        break

            info['options'].append({
                'color': info.get('color', ''),
                'tag_size': norm_size,
                'option_code': matched['option_code'] if matched else '',
                'status': matched['status'] if matched else 'in_stock',
            })
    elif stock_data:
        # desctable에 사이즈 없으면 option_stock_data만으로 옵션 생성
        for opt_code, opt_info in stock_data.items():
            opt_value = opt_info.get('option_value', '')
            is_selling = opt_info.get('is_selling', 'F') == 'T'
            is_display = opt_info.get('is_display', 'F') == 'T'
            stock_num = opt_info.get('stock_number', 0)

            if opt_value in ['단일사이즈', '단일 사이즈', 'ONE SIZE', 'ONESIZE', '원사이즈']:
                opt_value = 'FREE'

            status = 'in_stock' if (is_selling and is_display and stock_num > 0) else 'out_of_stock'
            info['options'].append({
                'color': info.get('color', ''),
                'tag_size': opt_value,
                'option_code': opt_code,
                'status': status,
            })

    # fallback: select 옵션
    if not info['options']:
        option_select = soup.select_one('select[option_title]')
        if option_select:
            for opt in option_select.select('option'):
                opt_value = opt.get('value', '')
                if not opt_value or opt_value == '*':
                    continue
                opt_text = opt.get_text(strip=True)
                if re.match(r'^[-=]{3,}$', opt_text.strip()):
                    continue

                is_soldout = '품절' in opt_text or opt.get('disabled') is not None
                clean_size = re.sub(r'\s*\[품절\]\s*', '', opt_text).strip()
                if clean_size in ['단일사이즈', '단일 사이즈', 'ONE SIZE', 'ONESIZE', '원사이즈']:
                    clean_size = 'FREE'

                info['options'].append({
                    'color': info.get('color', ''),
                    'tag_size': clean_size,
                    'option_code': opt_value,
                    'status': 'out_of_stock' if is_soldout else 'in_stock',
                })

    # 실측 사이즈 (table.size)
    size_table = soup.select_one('table.size')
    if size_table:
        headers = []
        header_row = size_table.select_one('tr')
        if header_row:
            for th in header_row.select('th'):
                headers.append(th.get_text(strip=True))

        data_rows = size_table.select('tr')[1:]
        for row in data_rows:
            cells = row.select('td')
            if not cells:
                continue
            size_name = cells[0].get_text(strip=True)
            size_name = re.sub(r'^EU\s*/\s*IT\s+', '', size_name).strip()
            size_data = {}
            for i, header in enumerate(headers[1:], 1):
                if i < len(cells):
                    val = cells[i].get_text(strip=True)
                    if val and val != '-':
                        size_data[header] = val
            if size_data:
                info['measurements'][size_name] = size_data

    # 상품 이미지 — ThumbImage (small → big 변환)
    thumb_area = soup.select_one('.xans-product-addimage')
    if thumb_area:
        for img in thumb_area.select('img.ThumbImage'):
            src = img.get('src', '')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                big_src = src.replace('/small/', '/big/').replace('/extra/small/', '/extra/big/')
                if big_src not in info['images']:
                    info['images'].append(big_src)

    # fallback: JSON-LD에서 이미지
    if not info['images']:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                ld = json.loads(script.string)
                if ld.get('@type') == 'Product' and 'image' in ld:
                    imgs = ld['image'] if isinstance(ld['image'], list) else [ld['image']]
                    info['images'] = imgs
                    break
            except (json.JSONDecodeError, TypeError):
                pass

    # 가격 추출
    # product_price = 판매가 (raw_price)
    price_match = re.search(r"product_price\s*=\s*'(\d+)'", html)
    if price_match:
        info['sale_price_js'] = int(price_match.group(1))

    # product_sale_price가 별도로 있으면 그것이 판매가
    sale_match = re.search(r"product_sale_price\s*=\s*(\d+)", html)
    if sale_match and int(sale_match.group(1)) > 0:
        info['sale_price_js'] = int(sale_match.group(1))

    # 소비자가 = #span_product_price_custom (정가/소비자가)
    custom_elem = soup.select_one('#span_product_price_custom')
    if custom_elem:
        custom_text = re.sub(r'[^0-9]', '', custom_elem.get_text(strip=True))
        if custom_text:
            info['original_price_js'] = int(custom_text)

    return info


# ===========================================
# 데이터 변환
# ===========================================

def extract_model_id_from_name(product_name: str) -> str:
    """상품명에서 모델번호 추출

    labellusso 상품명 형식: FW25 여성 데미 룬 숄더백 PXCAX F61997 LZZ
    → 마지막 대문자+숫자 패턴이 모델번호일 가능성
    """
    # 상품명 뒤쪽에서 영문+숫자 모델번호 패턴 추출
    match = re.search(r'([A-Z][A-Z0-9][\w\s\-\.]*[0-9][\w\s\-\.]*)\s*$', product_name)
    if match:
        return match.group(1).strip()
    return ''


def convert_to_raw_data(list_item: Dict, detail_info: Dict, brand_name_en: str, brand_name_ko: str, category_path: str = '') -> Optional[Dict]:
    """리스트 + 상세 데이터를 raw_scraped_data 형식으로 변환"""

    product_no = list_item['product_no']
    product_name = list_item.get('product_name', '')

    # 모델번호: 상세 페이지 prd_model_css 우선, 없으면 상품명에서 추출
    model_id = detail_info.get('model_id', '')
    if not model_id:
        model_id = extract_model_id_from_name(product_name)

    # model_id 없으면 스킵
    if not model_id:
        return None

    # 가격: 상세 페이지 JS 변수 우선
    original_price = detail_info.get('original_price_js', list_item.get('original_price', 0))
    sale_price = detail_info.get('sale_price_js', list_item.get('sale_price', 0))
    if not sale_price:
        sale_price = original_price

    # 재고 상태
    options = detail_info.get('options', [])
    stock_status = 'out_of_stock'
    if any(opt.get('status') == 'in_stock' for opt in options):
        stock_status = 'in_stock'
    elif not options:
        stock_status = 'in_stock'

    # composition
    composition = {}
    if detail_info.get('origin'):
        composition['원산지'] = detail_info['origin']
    if detail_info.get('material'):
        composition['소재'] = detail_info['material']
    if detail_info.get('set'):
        composition['구성품'] = detail_info['set']

    # raw_json_data
    raw_json = {
        'color': detail_info.get('color', ''),
        'item_type': detail_info.get('item_type', ''),
        'origin': detail_info.get('origin', ''),
        'material': detail_info.get('material', ''),
        'composition': composition,
        'options': options,
        'measurements': detail_info.get('measurements', {}),
        'images': detail_info.get('images', []),
        'cate_no': list_item.get('cate_no', ''),
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
        query = "SELECT mall_brand_name_en, mall_brand_name_ko, mall_brand_no FROM mall_brands WHERE mall_name = :site AND is_active = 1"
        params = {'site': SOURCE_SITE}
        if brand_filter:
            query += " AND UPPER(mall_brand_name_en) = :brand"
            params['brand'] = brand_filter.upper()
        result = conn.execute(text(query), params)
        return [{'name_en': r[0], 'name_ko': r[1], 'cate_no': r[2]} for r in result]


def get_published_product_ids(brand_name: str = None) -> set:
    """등록 완료된 상품의 mall_product_id 목록 조회"""
    with engine.connect() as conn:
        query = f"""
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = :site
            AND a.is_published = 1
        """
        params = {'site': SOURCE_SITE}
        if brand_name:
            query += " AND (UPPER(r.brand_name_en) = :brand OR UPPER(r.brand_name_kr) = :brand)"
            params['brand'] = brand_name.upper()
        result = conn.execute(text(query), params)
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

def main():
    parser = argparse.ArgumentParser(description='라벨루쏘 상품 수집기')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='브랜드당 최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"라벨루쏘 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    if args.skip_existing:
        logger.info("  신규+미등록 상품 수집 모드 (--skip-existing)")
    logger.info("=" * 60)

    brands = get_brands_from_database(args.brand)
    logger.info(f"대상 브랜드: {len(brands)}개")

    if not brands:
        logger.info("수집할 브랜드가 없습니다.")
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

            # 1) 브랜드 리스트 페이지 방문 → 서브카테고리 추출
            brand_url = f"{BASE_URL}/product/list.html?cate_no={cate_no}"
            html, error = session_mgr.fetch_page(brand_url)
            if error:
                logger.warning(f"  브랜드 페이지 수집 실패: {error}")
                continue
            if not html:
                continue

            subcategories = extract_subcategories(html)
            if not subcategories:
                logger.info(f"  서브카테고리 없음 — 브랜드 전체 페이지로 수집")
                subcategories = [{'name': '', 'url': f'/product/list.html?cate_no={cate_no}'}]

            logger.info(f"  서브카테고리 {len(subcategories)}개: {[s['name'] for s in subcategories[:5]]}{'...' if len(subcategories) > 5 else ''}")

            # 2) 서브카테고리별 리스트 수집 (product_no 기준 dedup)
            all_list_items = []
            seen_product_nos = set()

            for subcat in subcategories:
                if session_mgr.is_blocked:
                    break

                cat_name = subcat['name']
                cat_url = subcat['url']
                cat_no_match = re.search(r'/(\d+)/?$', cat_url)
                cat_no = cat_no_match.group(1) if cat_no_match else cate_no

                first_url = f"{BASE_URL}{cat_url}" if cat_url.startswith('/') else cat_url
                if '?' in first_url:
                    first_page_url = f"{first_url}&page=1"
                else:
                    first_page_url = f"{first_url}?page=1"

                page_html, error = session_mgr.fetch_page(first_page_url)
                if error:
                    logger.warning(f"    [{cat_name}] 수집 실패: {error}")
                    continue
                if not page_html:
                    continue

                last_page = get_last_page(page_html)
                items = get_product_list_from_page(page_html, cat_no)
                cat_items = list(items)

                for page in range(2, last_page + 1):
                    if session_mgr.is_blocked:
                        break
                    if '?' in first_url:
                        page_url = f"{first_url}&page={page}"
                    else:
                        page_url = f"{first_url}?page={page}"
                    page_html, error = session_mgr.fetch_page(page_url)
                    if error:
                        continue
                    if not page_html:
                        break
                    items = get_product_list_from_page(page_html, cat_no)
                    if not items:
                        break
                    cat_items.extend(items)
                    time.sleep(random.uniform(0.3, 0.8))

                new_count = 0
                for item in cat_items:
                    if item['product_no'] not in seen_product_nos:
                        seen_product_nos.add(item['product_no'])
                        all_list_items.append((item, cat_name))
                        new_count += 1

                logger.info(f"    [{cat_name}] {len(cat_items)}개 수집, 신규 {new_count}개 (누적: {len(all_list_items)}개)")
                time.sleep(random.uniform(0.3, 0.8))

            logger.info(f"  리스트 수집 완료: {len(all_list_items)}개 (중복 제거됨)")

            if not all_list_items:
                continue

            # limit 적용
            if args.limit and len(all_list_items) > args.limit:
                all_list_items = all_list_items[:args.limit]

            # skip-existing
            if args.skip_existing:
                published_ids = get_published_product_ids(brand_name_en)
                before = len(all_list_items)
                all_list_items = [(item, cat) for item, cat in all_list_items if item['product_no'] not in published_ids]
                skipped = before - len(all_list_items)
                if skipped > 0:
                    logger.info(f"  등록 완료 스킵: {skipped}개, 수집 대상: {len(all_list_items)}개")

            # 3) 상세 페이지 수집 + 변환 + 저장
            batch_data = []
            skipped_no_model = 0
            total = len(all_list_items)

            for idx, (list_item, category_path) in enumerate(all_list_items, 1):
                if session_mgr.is_blocked:
                    logger.error("  차단 감지됨 — 상세 수집 중단")
                    break

                product_no = list_item['product_no']
                detail_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={cate_no}&display_group=1"

                detail_html, error = session_mgr.fetch_page(detail_url)
                if error:
                    logger.warning(f"  [{idx}/{total}] 상세 수집 실패: {error} | {list_item['product_name'][:30]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                detail_info = extract_detail_info(detail_html) if detail_html else {}

                data = convert_to_raw_data(list_item, detail_info, brand_name_en, brand_name_ko, category_path)
                if not data:
                    skipped_no_model += 1
                    logger.info(f"  [{idx}/{total}] SKIP (no model_id) | {list_item['product_name'][:50]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                logger.info(f"  [{idx}/{total}] {data['model_id']} | {data['raw_price']:>12,}원 | {category_path} | {data['product_name'][:40]}")
                total_collected += 1

                if not args.dry_run:
                    batch_data.append(data)

                if len(batch_data) >= 10:
                    save_to_database(batch_data)
                    logger.info(f"  DB 저장: {len(batch_data)}개")
                    batch_data = []

                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            # 잔여분 저장
            if batch_data and not args.dry_run:
                save_to_database(batch_data)
                logger.info(f"  DB 저장(잔여): {len(batch_data)}개")

            total_skipped_no_model += skipped_no_model
            logger.info(f"  {brand_name_en} 완료: model_id 없어서 스킵 {skipped_no_model}개")

    finally:
        session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info(f"라벨루쏘 수집 완료")
    logger.info(f"  총 수집: {total_collected}개")
    logger.info(f"  model_id 없어서 스킵: {total_skipped_no_model}개")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :site"
            ), {'site': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 labellusso 상품: {count}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
