# -*- coding: utf-8 -*-
"""
메종파르코(maisonparco.com) 상품 수집 스크립트
- Cafe24 기반 쇼핑몰 → HTML 스크래핑
- 수집 기준: 브랜드 (전체상품보다 브랜드 리스트에 상품이 더 많음 → 브랜드가 더 완전)
  · mall_brands(maisonparco, mall_brand_no=cate_no) 순회 → 브랜드 list 페이지 페이지네이션
  · 상세 페이지엔 브랜드명이 없으므로, brand_name_en = mall_brands.raw_brand_name(영문, 이미 매핑됨)
  · 상품의 실제 카테고리는 브랜드/상세 어디에도 없음(.menuCategory 빈값, breadcrumd=전체상품뿐)
    → category_path 는 수집 시 빈값, 별도 --categories 패스로 채움
- raw_scraped_data 테이블에 source_site='maisonparco'로 저장
- 이미지는 raw_json_data.images에 저장 → ace_product_images 이관은 raw→ace 변환 단계에서

사용법:
    python maisonparco_collector.py                       # 전체 브랜드 raw 수집
    python maisonparco_collector.py --brand "ACNE STUDIOS"# 특정 브랜드만
    python maisonparco_collector.py --limit 50            # 브랜드당 최대 50개
    python maisonparco_collector.py --dry-run             # DB 저장 없이 테스트
    python maisonparco_collector.py --skip-existing       # 등록 완료 상품 스킵
    python maisonparco_collector.py --categories          # (raw 수집 후) category_path 채우기
    python maisonparco_collector.py --categories --dry-run# 카테고리 매핑 미리보기

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

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'okmall'))
import authority_flag  # 단일권위 전환 스위치 (ace → buyma_listings)
from sqlalchemy.exc import OperationalError

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
# pool_pre_ping: 끊긴 연결 자동 감지·재연결 / pool_recycle: MySQL wait_timeout 전에 갱신
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=280)

# ===========================================
# 상수
# ===========================================

BASE_URL = 'https://www.maisonparco.com'
SOURCE_SITE = 'maisonparco'
LIST_CATE_NO = '24'  # 전체상품
SESSION_REFRESH_INTERVAL = 30
MAX_CONSECUTIVE_TIMEOUTS = 5
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.8

# "2. 등록 상품사진" 경로가 포함된 이미지만 수집 대상
PRODUCT_IMAGE_MARKER = '등록 상품사진'

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
# 상품명 → 브랜드 / 모델번호 추출
# ===========================================

# 시즌 토큰(25SS 등) / 노이즈 토큰(3종 등) — 브랜드/모델 판정에서 제외
SEASON_RE = re.compile(r'^\d{2}(SS|FW|FA|AW|SU|CR|PF|RE)$', re.I)
NOISE_RE = re.compile(r'^\d+(종|개|입|색|컬러|사이즈|인치|차|단)$')


def extract_brand_from_name(product_name: str) -> str:
    """상품명 앞 한글 토큰을 브랜드명으로 추출 (시즌/노이즈 토큰은 건너뜀)

    예) "루이비통 다미에 ... N63143" → "루이비통"
        "25SS 우영미 ..."            → "우영미"
    ⚠️ 첫 토큰 기준이라 "메종 마르지엘라"처럼 띄어쓰기 브랜드는 첫 단어만. 추후 보정.
    """
    if not product_name:
        return ''
    for tok in product_name.strip().split():
        if SEASON_RE.match(tok) or NOISE_RE.match(tok):
            continue
        return tok
    return ''


def extract_model_id_from_name(product_name: str) -> str:
    """상품명에서 모델번호 추출 (한글 아닌 토큰의 연속 run 기반)

    - 시즌(25SS)·노이즈(3종)·뒤메모(>>) 제거
    - 한글 아닌 토큰 연속 run을 (숫자포함 > 끝쪽 > 길이)로 선택, 숫자없는 모델명도 4자+면 허용

    예) "... 남성 522915 DJ20T 1000"  → "522915 DJ20T 1000"
        "마르니 SAMS002708 00R12 ... 샌들" → "SAMS002708 00R12" (중간 모델)
        "막스마라 ... 자켓 3종 MSTTRIONFO" → "MSTTRIONFO" (숫자 없는 모델)
    """
    if not product_name:
        return ''
    name = re.sub(r'\s*>>.*$', '', product_name.strip())
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


# ===========================================
# 리스트 페이지 파싱
# ===========================================

def get_product_list_from_page(html: str) -> List[Dict]:
    """전체상품 리스트 페이지 HTML에서 상품 카드 추출

    maisonparco(Cafe24) 리스트 구조:
    - <ul class="prdList grid4"> > <li id="anchorBoxId_{product_no}">
    - 상세 링크: a[href^="/product/"] (slug/{product_no}/category/24/display/1/)
    - 상품명: strong.name a (숨김 "상품명 :" 라벨 제거)
    - 리스트 이미지: a img[src]
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

            # 상세 링크
            link = item.select_one('a[href^="/product/"]')
            detail_path = link.get('href', '') if link else ''
            detail_url = f"{BASE_URL}{detail_path}" if detail_path.startswith('/') else detail_path

            # 상품명 — strong.name a (숨김 라벨 제거)
            name_elem = item.select_one('strong.name a')
            product_name = ''
            if name_elem:
                for hidden in name_elem.select('.title, .displaynone'):
                    hidden.decompose()
                for br in name_elem.find_all('br'):
                    br.replace_with(' ')
                product_name = re.sub(r'\s+', ' ', name_elem.get_text(strip=True)).strip()

            # 리스트 이미지
            img_elem = item.select_one('a img')
            image_url = ''
            if img_elem:
                image_url = img_elem.get('src', '') or img_elem.get('ec-data-src', '')
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url

            if not product_name and not detail_url:
                continue

            products.append({
                'product_no': product_no,
                'product_name': product_name,
                'detail_url': detail_url,
                'list_image': image_url,
                'cate_no': LIST_CATE_NO,
            })
        except Exception as e:
            logger.warning(f"  리스트 아이템 파싱 오류: {e}")
            continue

    return products


def get_last_page(html: str) -> int:
    """페이지네이션에서 마지막 페이지 번호 추출 (a.last 우선, 없으면 숫자 링크 최대값)"""
    soup = BeautifulSoup(html, 'html.parser')
    max_page = 1

    last_link = soup.select_one('.xans-product-normalpaging a.last')
    if last_link:
        m = re.search(r'page=(\d+)', last_link.get('href', ''))
        if m:
            return int(m.group(1))

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

    maisonparco(Cafe24) 상세 구조:
    - 기본정보 테이블: div.xans-product-detaildesign > table, th 텍스트로 행 구분
      · 상품명 → td, 판매가 → td(#span_product_price_text)
    - 옵션: select.ProductOption0 / select[id^="product_option_id"]
    - 본문 이미지: ec-data-src(또는 src)에 "등록 상품사진" 포함된 것만
    - 브랜드/소재/실측 정보는 상세에 없음 (상품명에서 브랜드·모델만 추출)
    """
    soup = BeautifulSoup(html, 'html.parser')
    info = {
        'product_name': '',
        'price': 0,
        'options': [],
        'images': [],
        'sold_out': False,
    }

    # ----- 기본정보 테이블 -----
    for row in soup.select('.xans-product-detaildesign tr'):
        th = row.select_one('th')
        td = row.select_one('td')
        if not th or not td:
            continue
        header = th.get_text(strip=True).replace(' ', '')
        if '상품명' in header:
            info['product_name'] = _clean_text(td)
        elif '판매가' in header or '가격' in header:
            price_text = re.sub(r'[^0-9]', '', td.get_text())
            if price_text:
                info['price'] = int(price_text)

    # 가격 fallback — #span_product_price_text
    if not info['price']:
        price_elem = soup.select_one('#span_product_price_text')
        if price_elem:
            price_text = re.sub(r'[^0-9]', '', price_elem.get_text())
            if price_text:
                info['price'] = int(price_text)

    # ----- 품절 (상품 전체): is_soldout_icon=T 우선, 없으면 SOLD OUT 버튼 -----
    som = re.search(r"var\s+is_soldout_icon\s*=\s*'([^']*)'", html)
    if som:
        info['sold_out'] = som.group(1) == 'T'
    else:
        soldout_btn = soup.select_one('span.btnBlack')
        if soldout_btn and 'displaynone' not in (soldout_btn.get('class') or []):
            if 'SOLD OUT' in soldout_btn.get_text(strip=True).upper():
                info['sold_out'] = True

    # ----- 옵션: option_stock_data(전체 옵션+재고) 우선 -----
    #   select는 품절 옵션을 정상 옵션처럼 보여줘서 품절 판정이 부정확
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

    # fallback: option_stock_data 없을 때만 select
    if not info['options']:
        option_select = soup.select_one('select.ProductOption0, select[id^="product_option_id"]')
        if option_select:
            for opt in option_select.select('option'):
                value = opt.get('value', '')
                if not value or value in ('*', '**'):
                    continue
                opt_text = opt.get_text(strip=True)
                if re.fullmatch(r'[-=]{3,}', opt_text):
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

    # ----- 본문 등록 상품사진 이미지 -----
    for img in soup.find_all('img'):
        src = img.get('ec-data-src', '') or img.get('src', '')
        if not src or PRODUCT_IMAGE_MARKER not in src:
            continue
        if src.startswith('//'):
            src = 'https:' + src
        if src not in info['images']:
            info['images'].append(src)

    # ----- fallback: '등록 상품사진'이 없으면 카페24 기본 이미지 수집 -----
    #   maisonparco 상품 다수는 NAS '등록 상품사진' 없이 카페24 기본 이미지(/web/product/big/)만 보유.
    #   keyImg 메인 BigImage + addimage ThumbImage 수집. plain /product/small/ 는 /product/big/ 가
    #   서버에 없어 404 → 스킵(/product/extra/small/ → /extra/big/ 만 사용). 9tems/loromoda와 동일.
    if not info['images']:
        def _up(s: str) -> str:
            if s.startswith('//'):
                s = 'https:' + s
            return s.replace('/product/extra/small/', '/product/extra/big/').replace('/product/small/', '/product/big/')
        for img in soup.select('div.keyImg img.BigImage, div.thumbnail img.BigImage'):
            u = _up(img.get('src', ''))
            if u and u not in info['images']:
                info['images'].append(u)
        for img in soup.select('div.xans-product-addimage img.ThumbImage'):
            raw_src = img.get('src', '')
            if not raw_src or '/product/small/' in raw_src:
                continue
            u = _up(raw_src)
            if u and u not in info['images']:
                info['images'].append(u)

    return info


# ===========================================
# 데이터 변환
# ===========================================

def convert_to_raw_data(list_item: Dict, detail_info: Dict, brand_name_en: str,
                        category_path: str = '') -> Optional[Dict]:
    """리스트 + 상세 데이터를 raw_scraped_data 형식으로 변환

    brand_name_en: mall_brands.raw_brand_name(영문) — 브랜드 순회로 확정된 값.
    """

    product_no = list_item['product_no']
    # 상품명: 상세 테이블 우선, 없으면 리스트명
    product_name = detail_info.get('product_name') or list_item.get('product_name', '')
    if not product_name:
        return None

    brand_name = brand_name_en  # mall_brands 영문 브랜드 (변환 조인 키 raw_brand_name과 일치)
    brand_ko = extract_brand_from_name(product_name)  # 참고용 한글(상품명 앞단어)
    model_id = extract_model_id_from_name(product_name)
    if not model_id:
        return None  # 모델번호 없으면 스킵 (다른 수집기와 동일 정책)

    price = detail_info.get('price', 0)

    # 재고 상태
    options = detail_info.get('options', [])
    if detail_info.get('sold_out'):
        stock_status = 'out_of_stock'
    elif any(opt.get('status') == 'in_stock' for opt in options):
        stock_status = 'in_stock'
    elif not options:
        stock_status = 'in_stock'  # 옵션 없으면 일단 재고 있음으로
    else:
        stock_status = 'out_of_stock'

    raw_json = {
        'brand_ko': brand_ko,
        'options': options,
        'images': detail_info.get('images', []),
        'list_image': list_item.get('list_image', ''),
        'cate_no': list_item.get('cate_no', ''),
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': brand_name,  # 영문 브랜드 (mall_brands.raw_brand_name)
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': category_path,
        'original_price': price,
        'raw_price': price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': list_item.get('detail_url', ''),
    }


# ===========================================
# DB 조회/저장
# ===========================================

def get_brands_from_database(brand_filter: str = None, resume: bool = False) -> List[Dict]:
    """mall_brands(maisonparco)에서 수집 대상 브랜드 조회 (cate_no=mall_brand_no)

    resume=True: raw_scraped_data에 아직 한 건도 없는 브랜드만 — 중단 후 재개용.
                 (입력순 실행이라 알파벳 start로는 못 잡음. 빈 브랜드는 빠르게 0건 처리됨.)
    """
    with engine.connect() as conn:
        query = ("SELECT raw_brand_name, mall_brand_name_en, mall_brand_no FROM mall_brands mb "
                 "WHERE mall_name = :s AND mall_brand_no IS NOT NULL AND mall_brand_no <> ''")
        params = {'s': SOURCE_SITE}
        if brand_filter:
            query += " AND (UPPER(mall_brand_name_en) = :b OR UPPER(raw_brand_name) = :b)"
            params['b'] = brand_filter.upper()
        if resume:
            query += (" AND NOT EXISTS (SELECT 1 FROM raw_scraped_data r "
                      "WHERE r.source_site = :s AND r.brand_name_en = mb.raw_brand_name)")
        query += " ORDER BY mall_brand_name_en"
        rows = conn.execute(text(query), params)
        return [{'brand_en': (r[0] or r[1]), 'cate_no': r[2]} for r in rows]


def get_published_product_ids() -> set:
    """등록 완료된 상품의 mall_product_id 목록 조회"""
    with engine.connect() as conn:
        _reg = authority_flag.registered_sql('a') if authority_flag.use_listing_authority() else "a.is_published = 1"
        result = conn.execute(text(f"""
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = :site AND {_reg}
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
    for attempt in range(3):
        try:
            with engine.connect() as conn:
                for data in data_list:
                    conn.execute(insert_sql, data)
                conn.commit()
            return
        except OperationalError as e:
            logger.warning(f"  [DB] 저장 실패({attempt+1}/3) — 재연결 후 재시도: {str(e)[:80]}")
            engine.dispose()
            time.sleep(2 * (attempt + 1))
    logger.error("  [DB] 3회 재시도 실패 — 이 배치 저장 건너뜀")


# ===========================================
# 카테고리 파싱 (categories.html, 최대 4단계 중첩)
# ===========================================

def parse_category_nodes(html: str) -> List[Tuple[str, str, int]]:
    """categories.html ul.menu-dp1 재귀 파싱 → [(cate_no, path, depth)]

    전체상품(24)·새상품분류(25, 브랜드목록)는 제외. 예) "잡화 > 남성 > 가방 > 백팩"
    """
    soup = BeautifulSoup(html, 'html.parser')
    root = soup.select_one('ul.menu-dp1')
    if not root:
        return []
    exclude_top = {'전체상품', '새 상품분류', '새상품분류'}
    nodes = {}

    def cate_of(href):
        m = re.search(r'cate_no=(\d+)', href or '')
        return m.group(1) if m else None

    def walk(li, ancestors, depth):
        a = li.find('a', recursive=False) or li.find('a')
        if not a:
            return
        name = a.get_text(strip=True)
        cate = cate_of(a.get('href'))
        if depth == 1 and name in exclude_top:
            return
        parts = ancestors + [name]
        if cate and name:
            nodes[cate] = (' > '.join(parts), depth)
        sub = li.find('ul', recursive=False)
        if sub:
            for ch in sub.find_all('li', recursive=False):
                walk(ch, parts, depth + 1)

    for li in root.find_all('li', recursive=False):
        walk(li, [], 1)
    return [(c, p, d) for c, (p, d) in nodes.items()]


# ===========================================
# 메인 실행
# ===========================================

def collect_product_nos_for_cate(session_mgr: 'SessionManager', cate_no: str) -> List[Dict]:
    """cate_no 리스트 페이지 전체 페이지네이션 → 상품 카드 목록 (product_no/name/detail_url)"""
    first, error = session_mgr.fetch_page(f"{BASE_URL}/product/list.html?cate_no={cate_no}&page=1")
    if error or not first:
        return []
    last = get_last_page(first)
    items = list(get_product_list_from_page(first))
    seen = {it['product_no'] for it in items}
    for page in range(2, last + 1):
        if session_mgr.is_blocked:
            break
        html, err = session_mgr.fetch_page(f"{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}")
        if err or not html:
            break
        page_items = get_product_list_from_page(html)
        if not page_items:
            break
        for it in page_items:
            if it['product_no'] not in seen:
                seen.add(it['product_no'])
                items.append(it)
        time.sleep(random.uniform(0.2, 0.4))
    return items


def run_brand_collection(args):
    """브랜드 순회 → 상세 수집 → raw 저장 (category_path는 빈값, --categories로 채움)"""
    brands = get_brands_from_database(args.brand, getattr(args, 'resume', False))
    logger.info(f"대상 브랜드: {len(brands)}개")
    if not brands:
        logger.info("수집할 브랜드가 없습니다. (mall_brands에 maisonparco 브랜드 확인)")
        return

    published_ids = get_published_product_ids() if args.skip_existing else set()
    if args.skip_existing:
        logger.info(f"  등록 완료 상품 {len(published_ids)}개 — 스킵 대상")

    session_mgr = SessionManager()
    seen_products = set()      # 브랜드 간 product_no 중복 방지
    total_collected = 0
    total_skipped_no_model = 0

    try:
        for b_idx, brand in enumerate(brands, 1):
            if session_mgr.is_blocked:
                logger.error("  차단 감지 — 수집 중단")
                break
            brand_en = brand['brand_en']
            cate_no = brand['cate_no']
            logger.info(f"\n>>> [{b_idx}/{len(brands)}] {brand_en} (cate_no={cate_no})")

            items = collect_product_nos_for_cate(session_mgr, cate_no)
            # 신규 product_no만
            items = [it for it in items if it['product_no'] not in seen_products]
            for it in items:
                seen_products.add(it['product_no'])
            if args.skip_existing:
                items = [it for it in items if it['product_no'] not in published_ids]
            if args.limit:
                items = items[:args.limit]
            logger.info(f"  상품 {len(items)}개 (상세 수집)")

            batch_data = []
            for idx, list_item in enumerate(items, 1):
                if session_mgr.is_blocked:
                    logger.error("  차단 감지 — 상세 수집 중단")
                    break
                detail_html, error = session_mgr.fetch_page(list_item['detail_url'])
                if error:
                    logger.warning(f"  [{idx}/{len(items)}] 상세 실패: {error} | {list_item['product_name'][:30]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue
                detail_info = extract_detail_info(detail_html) if detail_html else {}
                data = convert_to_raw_data(list_item, detail_info, brand_en, '')
                if not data:
                    total_skipped_no_model += 1
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue
                total_collected += 1
                if idx <= 3 or idx % 100 == 0:
                    logger.info(f"    [{idx}/{len(items)}] {data['stock_status']:12} | {data['model_id']} | {data['raw_price']:>10,}원 | {data['product_name'][:34]}")
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
    logger.info("메종파르코 브랜드 수집 완료")
    logger.info(f"  총 수집: {total_collected}개 | model_id 없어 스킵: {total_skipped_no_model}개")
    if not args.dry_run:
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :s"), {'s': SOURCE_SITE}).scalar()
            logger.info(f"  DB 총 maisonparco raw: {count}개")
    logger.info("=" * 60)


def run_category_fill(args):
    """카테고리 페이지를 크롤해 raw_scraped_data.category_path 채움 (mall_categories는 안 건드림).

    product_no → 가장 구체적(깊은) 카테고리 경로. 이미 수집된 maisonparco raw만 UPDATE.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    nodes = parse_category_nodes(open(os.path.join(here, 'categories.html'), encoding='utf-8').read())
    nodes.sort(key=lambda x: -x[2])  # 깊은(구체적) 카테고리 우선 → 먼저 할당된 게 most-specific
    logger.info(f"카테고리 노드 {len(nodes)}개 (깊은 것 우선)")

    # 현재 maisonparco raw의 product_no 집합 (없는 건 UPDATE 안 함)
    with engine.connect() as conn:
        raw_ids = {str(r[0]) for r in conn.execute(text(
            "SELECT mall_product_id FROM raw_scraped_data WHERE source_site=:s"), {'s': SOURCE_SITE})}
    logger.info(f"현재 maisonparco raw: {len(raw_ids)}개")

    session_mgr = SessionManager()
    product_path = {}  # product_no -> path (most-specific-first 라 첫 할당 유지)
    try:
        for cate_no, path, depth in nodes:
            if session_mgr.is_blocked:
                logger.error("  차단 감지 — 카테고리 크롤 중단")
                break
            items = collect_product_nos_for_cate(session_mgr, cate_no)
            new = 0
            for it in items:
                pno = it['product_no']
                if pno in raw_ids and pno not in product_path:
                    product_path[pno] = path
                    new += 1
            logger.info(f"  [{path}] 상품 {len(items)} / 신규경로 {new} (누적 {len(product_path)})")
    finally:
        session_mgr.close()

    matched = len(product_path)
    logger.info(f"\ncategory_path 매칭: {matched}/{len(raw_ids)}개")
    if args.dry_run:
        logger.info("[DRY-RUN] UPDATE 생략")
        return
    updated = 0
    with engine.begin() as conn:
        for pno, path in product_path.items():
            if not path:
                continue
            conn.execute(text(
                "UPDATE raw_scraped_data SET category_path=:p, updated_at=NOW() "
                "WHERE source_site=:s AND mall_product_id=:n"), {'p': path, 's': SOURCE_SITE, 'n': pno})
            updated += 1
    logger.info(f"category_path UPDATE: {updated}개")


def run_image_refill(args):
    """이미지(raw_json_data.images)가 비어있는 maisonparco 상품만 골라 상세를 다시 받아 채움.
    (수집기 이미지 fallback 패치 후, 전체 재수집 없이 누락분만 보충용)
    """
    with engine.connect() as conn:
        q = ("SELECT mall_product_id, brand_name_en, product_name, category_path, product_url "
             "FROM raw_scraped_data WHERE source_site = :s "
             "AND (raw_json_data IS NULL OR raw_json_data NOT LIKE '%\"images\": [%' "
             "     OR raw_json_data LIKE '%\"images\": []%')")
        params = {'s': SOURCE_SITE}
        if args.brand:
            q += " AND brand_name_en = :b"
            params['b'] = args.brand
        rows = conn.execute(text(q), params).fetchall()

    if args.limit:
        rows = rows[:args.limit]
    logger.info(f"이미지 없는 상품: {len(rows)}개 (상세 재수집 대상)")
    if not rows:
        logger.info("보충할 상품이 없습니다.")
        return

    session_mgr = SessionManager()
    filled = 0
    still_empty = 0
    batch = []
    try:
        for i, row in enumerate(rows, 1):
            if session_mgr.is_blocked:
                logger.error("  차단 감지 — 중단")
                break
            product_no = str(row[0])
            brand_en = row[1]
            cat_path = row[3] or ''
            detail_url = row[4] or f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={LIST_CATE_NO}&display_group=1"

            detail_html, err = session_mgr.fetch_page(detail_url)
            if err or not detail_html:
                logger.warning(f"  [{i}/{len(rows)}] 상세 실패: {err} (no={product_no})")
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                continue

            detail_info = extract_detail_info(detail_html)
            list_item = {'product_no': product_no, 'product_name': row[2] or '',
                         'detail_url': detail_url, 'list_image': '', 'cate_no': LIST_CATE_NO}
            data = convert_to_raw_data(list_item, detail_info, brand_en, cat_path)
            if not data:
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                continue

            img_cnt = len(detail_info.get('images', []))
            if img_cnt > 0:
                filled += 1
            else:
                still_empty += 1
            if i <= 5 or i % 100 == 0:
                logger.info(f"  [{i}/{len(rows)}] 이미지 {img_cnt}장 | {data['model_id']} | {data['product_name'][:30]}")

            if not args.dry_run:
                batch.append(data)
                if len(batch) >= 10:
                    save_to_database(batch)
                    batch = []
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        if batch and not args.dry_run:
            save_to_database(batch)
    finally:
        session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info("이미지 보충 완료")
    logger.info(f"  이미지 채워진 상품: {filled} / 여전히 0장: {still_empty}")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='메종파르코 상품 수집기 (브랜드 기준)')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='브랜드당 최대 수집 상품 수 (테스트용)')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    parser.add_argument('--resume', action='store_true', help='raw에 아직 없는 브랜드만 수집 (중단 재개)')
    parser.add_argument('--categories', action='store_true', help='raw 수집 후 category_path 채우기')
    parser.add_argument('--refill-images', action='store_true', help='이미지(raw images) 없는 상품만 상세 재수집')
    args = parser.parse_args()

    logger.info("=" * 60)
    mode = 'REFILL-IMAGES' if args.refill_images else ('CATEGORIES' if args.categories else 'BRAND')
    logger.info(f"메종파르코 수집 ({mode}, {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    logger.info("=" * 60)

    if args.refill_images:
        run_image_refill(args)
    elif args.categories:
        run_category_fill(args)
    else:
        run_brand_collection(args)


if __name__ == "__main__":
    main()
