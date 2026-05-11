# -*- coding: utf-8 -*-
"""
라프리마(laprima.co.kr) 상품 수집 스크립트
- Cafe24 기반 쇼핑몰 → HTML 스크래핑
- 리스트 페이지: /product/list.html?cate_no={cate_no}&page={n}
- 상세 페이지:   /product/.../{product_no}/category/{cate_no}/display/1/
                또는 /product/detail.html?product_no={no}&cate_no={cate_no}
- raw_scraped_data 테이블에 source_site='laprima'로 저장
- mall_brands 미등록 브랜드는 수집 도중 auto-INSERT (buyma_brand_id=NULL)

사용법:
    python laprima_collector.py                        # 전체 실행 (mall_categories 리프 순회)
    python laprima_collector.py --category 446         # 특정 cate_no만
    python laprima_collector.py --limit 10             # 카테고리당 최대 10개
    python laprima_collector.py --dry-run              # DB 저장 없이 테스트
    python laprima_collector.py --skip-existing        # 등록 완료 상품 스킵
    python laprima_collector.py --dump                 # 변환 결과 stdout 출력

★ 봇 감지 방지:
- 30개마다 세션 교체 + 메인 페이지 방문
- 랜덤 브라우저 프로필
- 타임아웃 연속 5회 시 차단 감지 및 중지

★ nextzennpack/labellusso/trendmecca 패턴과 동일하지만, 라프리마는 브랜드 카테고리가 없어
   mall_categories 리프를 순회하고 상세 페이지에서 브랜드를 추출 → mall_brands에 auto-INSERT.
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

DATABASE_URL = os.getenv(
    'DATABASE_URL',
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)
engine = create_engine(DATABASE_URL, echo=False)

# ===========================================
# 상수
# ===========================================

BASE_URL = 'https://laprima.co.kr'
SOURCE_SITE = 'laprima'
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
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'Cache-Control': 'max-age=0',
    },
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cache-Control': 'max-age=0',
    },
]


# ===========================================
# 세션 관리 (nextzennpack 패턴 그대로)
# ===========================================

class SessionManager:
    def __init__(self):
        self.session: Optional[requests.Session] = None
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
    """리스트 페이지 HTML에서 상품 기본 정보 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    # ul.prdList > li.item.xans-record-, id="anchorBoxId_<product_no>"
    items = soup.select('ul.prdList li.item.xans-record-')
    for item in items:
        try:
            item_id = item.get('id', '')
            product_no = item_id.replace('anchorBoxId_', '') if item_id.startswith('anchorBoxId_') else ''
            if not product_no:
                continue

            # 상품 상세 URL (data-url 이용 — slug 포함)
            inner = item.select_one('div.prdList__item')
            data_url = (inner.get('data-url') if inner else '') or ''

            # 상품명: strong.name span (마지막 span)
            name_elem = item.select_one('strong.name a')
            product_name = ''
            if name_elem:
                spans = name_elem.find_all('span', recursive=True)
                # 마지막 span 또는 텍스트 노드 합본
                product_name = name_elem.get_text(' ', strip=True)
                # 중복된 "상품명" 같은 라벨 제거
                product_name = re.sub(r'\s*상품명\s*', ' ', product_name).strip()

            # 리스트 썸네일 이미지
            img_elem = item.select_one('div.prdImg img')
            list_image = ''
            if img_elem:
                list_image = img_elem.get('src', '') or ''
                if list_image.startswith('//'):
                    list_image = 'https:' + list_image

            # 품절 표시 (리스트 카드의 div.sold > img alt="품절")
            sold_elem = item.select_one('div.sold img[alt="품절"]')
            list_sold = bool(sold_elem)

            products.append({
                'product_no': product_no,
                'product_name': product_name,
                'data_url': data_url,
                'list_image': list_image,
                'list_sold': list_sold,
                'cate_no': cate_no,
            })
        except Exception as e:
            logger.warning(f"  리스트 아이템 파싱 오류: {e}")
            continue

    return products


def get_last_page(html: str) -> int:
    """페이지네이션에서 마지막 페이지 번호 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    paging = soup.select('.xans-product-normalpaging ol li a, .xans-product-normalpaging a')
    max_page = 1
    for a in paging:
        href = a.get('href', '') or ''
        match = re.search(r'page=(\d+)', href)
        if match:
            page_num = int(match.group(1))
            if page_num > max_page:
                max_page = page_num
    return max_page


# ===========================================
# 상세 페이지 파싱
# ===========================================

def _strip_won(text_value: str) -> int:
    """'367,000원' → 367000"""
    if not text_value:
        return 0
    digits = re.sub(r'[^0-9]', '', text_value)
    return int(digits) if digits else 0


def _is_korean(s: str) -> bool:
    return bool(re.search(r'[가-힣]', s or ''))


def _is_latin_brand(s: str) -> bool:
    """라틴 문자(영문) 위주의 브랜드명. 한글이 1자라도 있으면 False."""
    s = (s or '').strip()
    if not s or _is_korean(s):
        return False
    return bool(re.search(r'[A-Za-z]', s))


# 카테고리/광고 키워드로 자주 등장하는 한글 단어 (meta keywords 폴백용 stop list)
# model_id 폴백 시 제외할 영문 단어 (색상/사이즈/광고용)
_MODEL_TOKEN_STOPWORDS = {
    # 색상 (영문)
    'BLACK', 'WHITE', 'RED', 'BLUE', 'GREEN', 'YELLOW', 'PINK', 'PURPLE',
    'ORANGE', 'GREY', 'GRAY', 'NAVY', 'BEIGE', 'BROWN', 'KHAKI', 'IVORY',
    'NUDE', 'GOLD', 'SILVER', 'CREAM', 'TAN', 'OLIVE', 'BURGUNDY', 'CHARCOAL',
    'CAMEL', 'MULTI', 'MINT', 'WINE', 'CORAL', 'TAUPE', 'SAND', 'STONE',
    'LIGHT', 'DARK', 'MEDIUM', 'BRIGHT',
    # 색상 (이태리/프랑스/스페인)
    'NERO', 'BIANCO', 'ROSSO', 'BLU', 'GIALLO', 'VERDE', 'MARRONE', 'GRIGIO',
    'NOIR', 'BLANC', 'ROUGE', 'BLEU', 'JAUNE', 'VERT', 'GRIS',
    # 사이즈
    'XS', 'XXS', 'XXXS', 'XL', 'XXL', 'XXXL', 'XXXXL', 'FREE', 'ONESIZE', 'ONE',
    # 라프리마 자체 noise
    'LAPRIMA', 'NEW', 'SALE', 'HOT', 'BEST', 'EVENT',
    # 성별/사용자 (영문)
    'WOMEN', 'MEN', 'UNISEX', 'KIDS', 'LADIES', 'BOYS', 'GIRLS', 'BABY',
    # 사이즈 prefix
    'EU', 'US', 'UK', 'KR', 'IT', 'JP',
}

# product_title 폴백 시 첫 한글 토큰이 사이트명/광고일 때 거름
_KO_KEYWORD_STOPWORDS = {
    # 사이트명/판매자명/운영사
    '라프리마', '라프리마온', '포니링크', '라스트원', '젬백스링크', '젬백스',
    # [해외] [라스트원] 같은 prefix 단어
    '해외', '국내',
    # 광고 prefix
    '명품', '정품', '신상', '특가', '세일', '할인',
}


def _extract_bottom_info_table(soup: BeautifulSoup) -> Dict[str, str]:
    """상세 하단의 회색 테이블에서 브랜드/품번/컬러/소재/제조국/상품설명 추출.

    다음 키 형태로 반환: {'브랜드': 'VIVIENNE WESTWOOD', '품번': '4C03000CU-S000D-N403', ...}
    """
    result: Dict[str, str] = {}
    # detailCnt 영역 안에 있는 테이블만 본다 (다른 테이블과 충돌 방지)
    detail_cnt = soup.select_one('#detailCnt')
    candidates = []
    if detail_cnt:
        candidates = detail_cnt.find_all('table')
    if not candidates:
        candidates = soup.find_all('table')

    for tbl in candidates:
        rows = tbl.find_all('tr')
        if not rows:
            continue
        for row in rows:
            th = row.find('th')
            td = row.find('td')
            if not th or not td:
                continue
            header = th.get_text(' ', strip=True)
            value = td.get_text(' ', strip=True)
            if not header or not value:
                continue
            # 키워드 후보 — 품번/모델은 alias 통합해서 '품번' 한 키로 저장
            model_aliases = ('품번', '아이템코드', '아이템 코드', '모델명', '모델 명', '스타일코드', '스타일 코드', '모델번호', 'STYLE CODE', 'ITEM CODE')
            matched = False
            for alias in model_aliases:
                if header == alias or header.replace(' ', '') == alias.replace(' ', ''):
                    if '품번' not in result:
                        result['품번'] = value
                    matched = True
                    break
            if matched:
                continue
            for key in ('브랜드', '컬러', '색상', '소재', '제조국', '원산지', '상품설명', '사이즈', '시즌'):
                if key == header or header.startswith(key):
                    if key not in result:
                        result[key] = value
                    break
        # 브랜드+품번이 둘 다 있으면 충분 — 더 보지 않음
        if '브랜드' in result and '품번' in result:
            break

    return result


def _extract_basic_info_table(soup: BeautifulSoup) -> Dict[str, str]:
    """기본 정보 테이블 (상품명/브랜드[ko]/정상가/판매가/회원혜택가)"""
    result: Dict[str, str] = {}
    for tbl in soup.find_all('table'):
        cap = tbl.find('caption')
        if not cap or '기본 정보' not in cap.get_text(strip=True):
            continue
        for row in tbl.find_all('tr'):
            th = row.find('th')
            td = row.find('td')
            if not th or not td:
                continue
            header = th.get_text(' ', strip=True)
            # td 안에 strike/strong 등 인라인 태그가 많으므로 텍스트만
            value = td.get_text(' ', strip=True)
            for key in ('상품명', '브랜드', '정상가', '판매가', '회원 혜택가', '회원혜택가', '배송비'):
                if header == key:
                    result[key] = value
                    break
        break  # 첫 매칭 테이블만
    return result


def _extract_brand_ko_from_title(title: str) -> str:
    """product_title에서 한글 브랜드 추출.

    1) mall_brands(_BRAND_KO_TO_EN)에 등록된 ko 중 title에 substring으로 들어있는
       가장 긴 매칭을 반환 (라프리마 표기 비일관성을 mall_brands 등록값으로 통일)
    2) 등록된 ko 매칭이 없으면 (신규 브랜드 첫 발견) 첫 한글 토큰으로 폴백.
       영문/숫자 prefix가 공백 1개로 붙어있으면 같이 묶음 (D1 밀라노, MM6 메종마르지엘라)

    예) '몽클레어 로고 패치 ...'  → '몽클레어' (mall_brands에 '몽클레어' 등록)
        '버버리 쏘니 벨트 백'    → '버버리' (mall_brands에 '버버리' 등록)
        '[해외] D1 밀라노 워치'  → 'D1 밀라노' (폴백, 영문 prefix 합치기)
        '신규브랜드XYZ 백'       → '신규브랜드XYZ' (폴백)
    """
    if not title:
        return ''
    # 1. mall_brands 등록 ko 중 title에 들어있는 가장 긴 매칭
    matches = [ko for ko in _BRAND_KO_TO_EN if ko and ko in title]
    if matches:
        return max(matches, key=len)
    # 2. 폴백: 첫 한글 토큰 (+ 영문 prefix 합치기)
    pattern = re.compile(r'[A-Za-z0-9]*[가-힣]+[A-Za-z0-9가-힣]*')
    for m in pattern.finditer(title):
        cand = m.group(0)
        if cand in _KO_KEYWORD_STOPWORDS or len(cand) < 2 or len(cand) > 16:
            continue
        before = title[:m.start()]
        pm = re.search(r'(\b[A-Z0-9]{1,5})\s+$', before)
        if pm:
            return f'{pm.group(1)} {cand}'
        return cand
    return ''


def _extract_brand_from_image_cdn(soup: BeautifulSoup) -> str:
    """상세 본문 이미지 CDN URL의 폴더명에서 영문 브랜드 추정.

    예: 'biztrend.co.kr/company_img_folder/laprima01/CAMPER/...' → 'CAMPER'
        '_goods/vivienne westwood/...' → 'VIVIENNE WESTWOOD'
    """
    detail_cnt = soup.select_one('#detailCnt')
    if not detail_cnt:
        return ''
    EXCLUDE = {'img_500', 'img_300', 'img_700', 'img', 'img_main',
               'common', 'banner', 'icon', 'admin', 'main', 'top',
               'notice', 'info', 'event', 'brand'}
    candidates = []
    for img in detail_cnt.find_all('img'):
        src = img.get('ec-data-src') or img.get('src') or ''
        if not src:
            continue
        # company_img_folder/<mall>/<BRAND>/...
        m = re.search(r'company_img_folder/[^/]+/([^/]+)/', src)
        if m:
            cand = m.group(1).strip()
            if cand.lower() not in EXCLUDE and re.search(r'[A-Za-z]', cand) and not _is_korean(cand):
                candidates.append(cand.upper())
                continue
        # _goods/<brand>/...
        m = re.search(r'/_goods/([^/]+)/', src)
        if m:
            cand = m.group(1).strip()
            if cand.lower() not in EXCLUDE and re.search(r'[A-Za-z]', cand) and not _is_korean(cand):
                candidates.append(cand.upper())
    if not candidates:
        return ''
    # 가장 흔한 후보 (img_500 같은 거 섞여있을 때 진짜 브랜드 폴더가 다수)
    from collections import Counter
    return Counter(candidates).most_common(1)[0][0]


def _parse_option_stock_data(html: str) -> Dict[str, Dict]:
    """`option_stock_data = '{...}'` JS 변수 파싱.

    Returns:
        {option_value: {'is_selling': 'T'/'F', 'stock_number': int, 'is_display': 'T'/'F', 'item_code': str}, ...}
    """
    result: Dict[str, Dict] = {}
    m = re.search(r"var\s+option_stock_data\s*=\s*'([^']+)'", html)
    if not m:
        return result
    raw = m.group(1)
    # JS escape decoding (예: \" → ", \uXXXX → 한글)
    try:
        decoded = raw.encode('latin-1', errors='ignore').decode('unicode_escape')
        data = json.loads(decoded)
    except Exception:
        try:
            # fallback: backslash 처리만 단순 replace
            decoded = raw.replace('\\"', '"').replace("\\'", "'")
            decoded = re.sub(r'\\u([0-9a-fA-F]{4})', lambda mo: chr(int(mo.group(1), 16)), decoded)
            data = json.loads(decoded)
        except Exception:
            return result
    if not isinstance(data, dict):
        return result
    for item_code, info in data.items():
        if not isinstance(info, dict):
            continue
        ov = info.get('option_value')
        if isinstance(ov, list):
            ov = ' / '.join(str(x) for x in ov)
        if ov is None:
            continue
        result[str(ov)] = {
            'is_selling': info.get('is_selling', ''),
            'is_display': info.get('is_display', ''),
            'stock_number': info.get('stock_number'),
            'use_soldout': info.get('use_soldout', ''),
            'item_code': item_code,
        }
    return result


def _extract_options(soup: BeautifulSoup, html: str) -> Tuple[List[Dict], bool]:
    """상품 옵션 추출.

    재고 판정 우선순위:
      1) option_stock_data JS 변수의 (is_selling=T) AND (stock_number>0)
      2) option 텍스트의 '[품절]' 접미어 또는 disabled 속성

    Returns:
        (options_list, has_options)
        - options_list: [{'color': '', 'tag_size': 'S', 'option_code': 'S', 'status': 'in_stock'}, ...]
        - has_options: select#product_option_id1 이 존재했는지 여부
    """
    options: List[Dict] = []
    select1 = soup.select_one('select#product_option_id1, select[name="option1"]')
    has_options = select1 is not None

    stock_map = _parse_option_stock_data(html) if has_options else {}

    if select1 is not None:
        for opt in select1.find_all('option'):
            opt_value = (opt.get('value') or '').strip()
            opt_text = opt.get_text(' ', strip=True)
            if not opt_value or opt_value in ('*', '**'):
                continue
            # 구분선
            if re.match(r'^[-=]{3,}$', opt_text):
                continue
            clean_size = re.sub(r'\s*\[.*?\]\s*', '', opt_text).strip()
            if clean_size in ('단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈', 'ONESIZE', 'ONE SIZE', 'FREE'):
                clean_size = 'FREE'

            # 1) option_stock_data 우선 (정확)
            status = None
            stock_info = stock_map.get(opt_value)
            if stock_info is not None:
                is_selling = (stock_info.get('is_selling') or '').upper()
                stock_n = stock_info.get('stock_number')
                if is_selling == 'T' and (stock_n is None or (isinstance(stock_n, int) and stock_n > 0)):
                    status = 'in_stock'
                else:
                    status = 'out_of_stock'

            # 2) 폴백: 텍스트/disabled
            if status is None:
                is_soldout = ('품절' in opt_text) or (opt.get('disabled') is not None)
                status = 'out_of_stock' if is_soldout else 'in_stock'

            options.append({
                'color': '',
                'tag_size': clean_size,
                'option_code': opt_value,
                'status': status,
            })

    return options, has_options


def _extract_images(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """상세 페이지에서 이미지 수집.

    1순위: div.thumbnail > img.BigImage (대표 이미지)
    2순위: 상품 추가이미지 (xans-product-addimage 영역)
    3순위: 상세설명 본문(#detailCnt) 안의 _goods/ 경로 이미지 (ec-data-src)
    """
    images: List[Dict[str, str]] = []
    seen = set()

    def add(url: str, kind: str):
        if not url:
            return
        if url.startswith('//'):
            url = 'https:' + url
        if url in seen:
            return
        seen.add(url)
        images.append({'url': url, 'kind': kind})

    def _is_ui_chrome(url: str) -> bool:
        # 스킨/UI 아이콘 제외 (/wib/img/, /web/upload/icon_, .gif 등)
        if '/wib/img/' in url:
            return True
        if '/web/upload/icon_' in url:
            return True
        if 'echosting.cafe24.com' in url:
            return True
        return False

    # 1) 대표
    for img in soup.select('div.thumbnail img.BigImage, div.keyImg img.BigImage'):
        url = img.get('src') or img.get('ec-data-src') or ''
        if url and not _is_ui_chrome(url):
            add(url, 'main')

    # 2) 추가 썸네일 — img.ThumbImage 만
    for img in soup.select('div.xans-product-addimage img.ThumbImage, div.listImg img.ThumbImage'):
        url = img.get('src') or img.get('ec-data-src') or ''
        if url and not _is_ui_chrome(url):
            add(url, 'thumb')

    # 3) 본문 _goods/ 이미지 (laprima1.godohosting.com 형태)
    detail_cnt = soup.select_one('#detailCnt')
    if detail_cnt:
        for img in detail_cnt.find_all('img'):
            src = img.get('ec-data-src') or img.get('src') or ''
            if not src:
                continue
            if _is_ui_chrome(src):
                continue
            # _goods/ 경로의 상품 이미지만 (top.jpg, notice.jpg, brand/xxx.jpg 같은 공통 배너 제외)
            if '/_goods/' in src or '/goods/' in src:
                add(src, 'detail')

    return images


def extract_detail_info(html: str) -> Dict[str, Any]:
    """상세 페이지 HTML에서 모든 필드 추출"""
    soup = BeautifulSoup(html, 'html.parser')
    info: Dict[str, Any] = {
        'product_title': '',
        'brand_name_en': '',
        'brand_name_ko': '',
        'model_id': '',
        'color': '',
        'material': '',
        'origin': '',
        'description': '',
        'options': [],
        'has_options': False,
        'single_stock_number': None,
        'images': [],
        'price_normal': 0,        # 정상가
        'price_sale': 0,          # 판매가
        'price_optimum': 0,       # 회원 혜택가
        'category_no_in_url': '',
    }

    # h1.product_title
    h1 = soup.select_one('h1.product_title')
    if h1:
        info['product_title'] = h1.get_text(' ', strip=True)

    # 하단 정보 테이블 — 브랜드 후보, 품번, 컬러, 소재, 원산지(제조국)
    bottom = _extract_bottom_info_table(soup)
    bottom_brand = bottom.get('브랜드', '').strip()
    if bottom.get('품번'):
        info['model_id'] = bottom['품번'].strip()
    if bottom.get('컬러') or bottom.get('색상'):
        info['color'] = (bottom.get('컬러') or bottom.get('색상') or '').strip()
    if bottom.get('소재'):
        info['material'] = bottom['소재'].strip()
    if bottom.get('제조국') or bottom.get('원산지'):
        info['origin'] = (bottom.get('제조국') or bottom.get('원산지') or '').strip()
    if bottom.get('상품설명'):
        info['description'] = bottom['상품설명'].strip()

    # 기본 정보 테이블 — 가격용 (basic_brand는 사용 안 함)
    basic = _extract_basic_info_table(soup)

    # === 브랜드 추출 (단순화) ===
    # ko: product_name 첫 한글 단어 (라프리마 상품명이 99% 한글 브랜드로 시작)
    # en: 하단 정보 테이블의 영문 → CDN 폴더명 → 메모리 dict (mall_brands ko→en) lookup
    info['brand_name_ko'] = _extract_brand_ko_from_title(info['product_title'])
    en = ''
    if bottom_brand and _is_latin_brand(bottom_brand):
        en = bottom_brand.upper()
    if not en:
        en = _extract_brand_from_image_cdn(soup)
    if not en and info['brand_name_ko']:
        en = _BRAND_KO_TO_EN.get(info['brand_name_ko'], '')
    info['brand_name_en'] = en

    if basic.get('정상가'):
        info['price_normal'] = _strip_won(basic['정상가'])
    if basic.get('판매가'):
        info['price_sale'] = _strip_won(basic['판매가'])
    optimum = basic.get('회원 혜택가') or basic.get('회원혜택가')
    if optimum:
        # "348,650원 ( 18,350원 할인)" 같이 부가 텍스트가 같이 잡힘 — 첫 가격만
        m = re.search(r'([\d,]+)\s*원', optimum)
        if m:
            info['price_optimum'] = _strip_won(m.group(1))

    # 가격 폴백: JS 변수
    if not info['price_sale']:
        m = re.search(r"product_price\s*=\s*'([\d]+)'", html)
        if m:
            info['price_sale'] = int(m.group(1))
    if not info['price_optimum']:
        m = re.search(r"product_sale_price\s*=\s*([\d]+)", html)
        if m:
            info['price_optimum'] = int(m.group(1))

    # 옵션 (사이즈)
    options, has_options = _extract_options(soup, html)
    info['options'] = options
    info['has_options'] = has_options

    # 단일 옵션 stock_number
    m = re.search(r"single_option_stock_data\s*=\s*'([^']+)'", html)
    if m:
        try:
            decoded = m.group(1).encode().decode('unicode_escape')
            data = json.loads(decoded)
            sn = data.get('stock_number')
            if isinstance(sn, int):
                info['single_stock_number'] = sn
        except Exception:
            try:
                m2 = re.search(r'"stock_number"\s*:\s*(\d+)', m.group(1))
                if m2:
                    info['single_stock_number'] = int(m2.group(1))
            except Exception:
                pass

    # 이미지
    info['images'] = _extract_images(soup)

    # model_id 폴백: h1 title 안에서 추출
    # 전략:
    #   1) 끝의 노이즈 정리: '/1 (12207)', '(676594)' 등 떼냄
    #   2) 영문/숫자 토큰 후보 추출 (색상/사이즈 stop list 제외)
    #   3) 공백 1자 이내로 이어지는 토큰들 그룹핑
    #   4) 첫 멤버에 알파벳이 있는 그룹 중 토큰 수 최대 그룹 선택
    if not info['model_id'] and info['product_title']:
        title = info['product_title']
        title_clean = re.sub(r'\s*\([^)]*\)\s*$', '', title)
        title_clean = re.sub(r'\s*/\s*\d+\s*$', '', title_clean)

        # 1단계: 영문/숫자 토큰 모두 (3자 이상, stopwords 제외)
        tokens_all: List[Tuple[int, int, str]] = []
        for m in re.finditer(r'(?<![A-Za-z0-9])([A-Z0-9][A-Z0-9\-/]{2,})(?![A-Za-z0-9])', title_clean):
            cand = m.group(1)
            if cand.upper() in _MODEL_TOKEN_STOPWORDS:
                continue
            tokens_all.append((m.start(), m.end(), cand))

        # 2단계: 공백 1자 이내 연속 토큰 그룹핑 (한글 사이에 끼면 분리)
        if tokens_all:
            groups: List[List[Tuple[int, int, str]]] = [[tokens_all[0]]]
            for prev, cur in zip(tokens_all, tokens_all[1:]):
                gap = title_clean[prev[1]:cur[0]]
                # 공백 1~2자 이내 + 한글 없음 → 같은 그룹
                if len(gap) <= 2 and gap.strip() == '' and not _is_korean(gap):
                    groups[-1].append(cur)
                else:
                    groups.append([cur])

            # 3단계: 첫 멤버 valid 조건 — 알파벳 포함 OR 5자+ 순수 숫자
            def _is_valid_lead_token(tok: str) -> bool:
                if re.search(r'[A-Z]', tok):
                    return True
                # 순수 숫자(하이픈/슬래시 허용) — 5자 이상이면 모델 코드로 인정
                if re.match(r'^[\d\-/]+$', tok) and len(re.sub(r'[\-/]', '', tok)) >= 5:
                    return True
                return False

            valid = [g for g in groups if _is_valid_lead_token(g[0][2])]
            if valid:
                # 토큰 수 max → 동률이면 마지막 그룹 (일반적으로 모델은 상품명 후반)
                best = max(valid, key=lambda g: (len(g), g[0][0]))
                cand = ' '.join(t[2] for t in best)
                # 단일 토큰이면 길이 5 이상
                if len(best) > 1 or len(cand.replace('-', '').replace('/', '')) >= 5:
                    info['model_id'] = cand

    # 카테고리 번호 (상품 URL에 들어있는 /category/<no>/ — 상세 HTML 자체에 없을 수도 있음)
    canonical = soup.select_one('link[rel="canonical"]')
    href = (canonical.get('href') if canonical else '') or ''
    m = re.search(r'/category/(\d+)/', href)
    if m:
        info['category_no_in_url'] = m.group(1)

    return info


# ===========================================
# 재고 판정
# ===========================================

def determine_stock_status(detail: Dict[str, Any], list_sold: bool, html: str) -> str:
    """재고 판정.

    1) 옵션이 있으면: 옵션 중 하나라도 in_stock이면 in_stock
    2) 옵션이 없으면: single_stock_number > 0 → in_stock
    3) 폴백: 리스트 카드 또는 상세에서 SOLD OUT 매칭
    """
    options = detail.get('options', [])
    has_options = detail.get('has_options', False)

    if has_options and options:
        if any(o.get('status') == 'in_stock' for o in options):
            return 'in_stock'
        return 'out_of_stock'

    sn = detail.get('single_stock_number')
    if isinstance(sn, int):
        return 'in_stock' if sn > 0 else 'out_of_stock'

    # SOLD OUT 폴백
    if list_sold:
        return 'out_of_stock'
    if re.search(r'<span[^>]*class="[^"]*\bsub_sold\b[^"]*"[^>]*>\s*SOLD\s*OUT\s*</span>', html, re.IGNORECASE):
        # displaynone이 붙어있는지 확인
        m = re.search(r'<span[^>]*class="([^"]*\bsub_sold\b[^"]*)"', html)
        if m and 'displaynone' not in m.group(1):
            return 'out_of_stock'
    if re.search(r'<div[^>]*class="[^"]*\bsoldicon\b[^"]*"[^>]*>\s*<img[^>]*alt="품절"', html):
        return 'out_of_stock'

    return 'in_stock'


# ===========================================
# 데이터 변환
# ===========================================

def build_raw_data(list_item: Dict, detail: Dict, category_path: str, html: str) -> Optional[Dict]:
    product_no = list_item['product_no']

    # 모델번호 필수
    model_id = detail.get('model_id') or ''
    if not model_id:
        return None

    # 가격: 회원 혜택가 > 판매가 > 정상가
    price_optimum = detail.get('price_optimum') or 0
    price_sale = detail.get('price_sale') or 0
    price_normal = detail.get('price_normal') or 0

    raw_price = price_optimum or price_sale or price_normal
    original_price = price_normal or price_sale or raw_price

    stock_status = determine_stock_status(detail, list_item.get('list_sold', False), html)

    # 상품명: h1 우선, 없으면 리스트
    product_name = detail.get('product_title') or list_item.get('product_name', '')

    # 상품 URL: 리스트의 data-url(slug) > 폴백 query string
    data_url = list_item.get('data_url') or ''
    if data_url and data_url.startswith('/'):
        product_url = f"{BASE_URL}{data_url}"
    else:
        cate = list_item.get('cate_no', '')
        product_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={cate}&display_group=1"

    # composition
    composition: Dict[str, str] = {}
    if detail.get('origin'):
        composition['원산지'] = detail['origin']
    if detail.get('material'):
        composition['소재'] = detail['material']
    if detail.get('description'):
        composition['상품설명'] = detail['description']

    raw_json = {
        'color': detail.get('color', ''),
        'origin': detail.get('origin', ''),
        'material': detail.get('material', ''),
        'description': detail.get('description', ''),
        'composition': composition,
        'options': detail.get('options', []),
        'has_options': detail.get('has_options', False),
        'single_stock_number': detail.get('single_stock_number'),
        'images': detail.get('images', []),
        'cate_no': list_item.get('cate_no', ''),
        'price_normal': price_normal,
        'price_sale': price_sale,
        'price_optimum': price_optimum,
        'scraped_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    return {
        'source_site': SOURCE_SITE,
        'mall_product_id': product_no,
        'brand_name_en': (detail.get('brand_name_en') or '').strip(),
        'brand_name_kr': (detail.get('brand_name_ko') or '').strip(),
        'product_name': product_name,
        'p_name_full': product_name,
        'model_id': model_id,
        'category_path': category_path,
        'original_price': original_price,
        'raw_price': raw_price,
        'stock_status': stock_status,
        'raw_json_data': json.dumps(raw_json, ensure_ascii=False),
        'product_url': product_url,
    }


# ===========================================
# DB 조회/저장
# ===========================================

def get_leaf_categories(category_filter: Optional[str] = None) -> List[Dict]:
    """laprima의 leaf 카테고리 목록 (depth2 != '' 인 것).

    leaf: full_path 가 'WOMEN > 가방 > 숄더백' 처럼 3단인 경우.
    """
    with engine.connect() as conn:
        if category_filter:
            sql = """
                SELECT category_id, full_path, gender, mall_category_url
                FROM mall_categories
                WHERE mall_name = 'laprima' AND category_id = :cate_no
                LIMIT 1
            """
            rows = conn.execute(text(sql), {'cate_no': str(category_filter)}).fetchall()
        else:
            sql = """
                SELECT category_id, full_path, gender, mall_category_url
                FROM mall_categories
                WHERE mall_name = 'laprima'
                  AND full_path LIKE '% > % > %'
                ORDER BY full_path
            """
            rows = conn.execute(text(sql)).fetchall()
        return [{
            'cate_no': r[0],
            'full_path': r[1] or '',
            'gender': r[2] or '',
            'mall_category_url': r[3] or '',
        } for r in rows]


def get_published_product_ids() -> set:
    """등록 완료된 laprima 상품의 mall_product_id 집합"""
    with engine.connect() as conn:
        sql = """
            SELECT r.mall_product_id
            FROM raw_scraped_data r
            INNER JOIN ace_products a ON r.id = a.raw_data_id
            WHERE r.source_site = 'laprima'
              AND a.is_published = 1
        """
        rows = conn.execute(text(sql)).fetchall()
        return {str(r[0]) for r in rows}


def save_to_database(rows: List[Dict]):
    if not rows:
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
        for data in rows:
            conn.execute(insert_sql, data)
        conn.commit()


# 메모리 ko→en lookup dict (collector 시작 시 mall_brands에서 로드)
_BRAND_KO_TO_EN: Dict[str, str] = {}


def load_brand_lookup_dict() -> int:
    """mall_brands(laprima)에서 (ko → en) 매핑을 메모리 dict로 로드.

    상품 수집 중 페이지에 영문 브랜드명이 없을 때 ko로 lookup해 채워 넣기 위함.
    한 번 로드 후 collector 실행 동안 메모리에서 사용.
    """
    global _BRAND_KO_TO_EN
    _BRAND_KO_TO_EN = {}
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT mall_brand_name_ko, mall_brand_name_en
            FROM mall_brands
            WHERE mall_name = 'laprima'
              AND mall_brand_name_ko IS NOT NULL AND mall_brand_name_ko <> ''
              AND mall_brand_name_en IS NOT NULL AND mall_brand_name_en <> ''
              AND mall_brand_name_en REGEXP '^[A-Za-z0-9 .&\\\\-]+$'
        """)).fetchall()
    for ko, en in rows:
        if ko and en and ko not in _BRAND_KO_TO_EN:
            _BRAND_KO_TO_EN[ko] = en
    return len(_BRAND_KO_TO_EN)


def ensure_mall_brand(brand_name_en: str, brand_name_ko: str) -> bool:
    """mall_brands에 (laprima, brand_name_en) 없으면 INSERT.

    Returns: True 면 신규 INSERT, False 면 기존
    """
    if not brand_name_en:
        return False
    with engine.begin() as conn:
        exists = conn.execute(text("""
            SELECT 1 FROM mall_brands
            WHERE mall_name = 'laprima'
              AND UPPER(mall_brand_name_en) = UPPER(:en)
            LIMIT 1
        """), {'en': brand_name_en}).fetchone()
        if exists:
            return False
        conn.execute(text("""
            INSERT INTO mall_brands
              (mall_name, mall_brand_name_en, mall_brand_name_ko,
               buyma_brand_id, buyma_brand_name, mapping_level, is_mapped,
               mall_brand_url, is_active, mall_brand_no)
            VALUES
              ('laprima', :en, :ko,
               NULL, NULL, 0, 0,
               NULL, 1, NULL)
        """), {'en': brand_name_en, 'ko': brand_name_ko or None})
        # 메모리 dict에도 추가 (다음 상품에서 즉시 활용 가능)
        if brand_name_ko:
            _BRAND_KO_TO_EN.setdefault(brand_name_ko, brand_name_en)
        return True


# ===========================================
# 메인 실행
# ===========================================

def main():
    parser = argparse.ArgumentParser(description='라프리마(Cafe24) 상품 수집기')
    parser.add_argument('--category', type=str, help='특정 cate_no만 처리')
    parser.add_argument('--limit', type=int, help='카테고리당 최대 수집 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='DB 저장 없이 테스트')
    parser.add_argument('--skip-existing', action='store_true', help='등록 완료 상품 스킵')
    parser.add_argument('--dump', action='store_true', help='첫 상품 변환 결과 stdout 출력')
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"라프리마 수집 시작 (Mode: {'DRY-RUN' if args.dry_run else 'NORMAL'})")
    if args.skip_existing:
        logger.info("  신규+미등록 상품 수집 모드 (--skip-existing)")
    logger.info("=" * 60)

    # 브랜드 ko→en lookup dict 로딩 (페이지에 영문 없는 상품의 en 폴백용)
    n_lookup = load_brand_lookup_dict()
    logger.info(f"  brand ko→en lookup dict 로딩: {n_lookup}개")

    categories = get_leaf_categories(args.category)
    logger.info(f"대상 카테고리: {len(categories)}개")
    if not categories:
        logger.info("수집할 카테고리가 없습니다 (mall_categories 확인).")
        return

    published_ids: set = set()
    if args.skip_existing and not args.dry_run:
        published_ids = get_published_product_ids()
        logger.info(f"등록 완료 상품 수: {len(published_ids)}")

    session_mgr = SessionManager()
    seen_product_nos: set = set()
    total_collected = 0
    total_skipped_no_model = 0
    new_brands: set = set()

    try:
        for cat_idx, cat in enumerate(categories, 1):
            cate_no = str(cat['cate_no'])
            full_path = cat['full_path']

            logger.info(f"\n>>> [{cat_idx}/{len(categories)}] 카테고리: {full_path} (cate_no={cate_no})")

            if session_mgr.is_blocked:
                logger.error("  차단 감지됨 — 수집 중단")
                break

            # 1) 카테고리 첫 페이지
            list_url = f"{BASE_URL}/product/list.html?cate_no={cate_no}"
            html, error = session_mgr.fetch_page(list_url)
            if error or not html:
                logger.warning(f"  카테고리 페이지 수집 실패: {error}")
                continue

            last_page = get_last_page(html)
            list_items = get_product_list_from_page(html, cate_no)

            for page in range(2, last_page + 1):
                if session_mgr.is_blocked:
                    break
                page_url = f"{list_url}&page={page}"
                page_html, perr = session_mgr.fetch_page(page_url)
                if perr or not page_html:
                    continue
                more = get_product_list_from_page(page_html, cate_no)
                if not more:
                    break
                list_items.extend(more)
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            # dedup (카테고리간 중복)
            unique_items = []
            for it in list_items:
                pno = it['product_no']
                if pno in seen_product_nos:
                    continue
                seen_product_nos.add(pno)
                unique_items.append(it)

            logger.info(f"  리스트 수집: {len(list_items)}개 (카테고리 내), 신규 {len(unique_items)}개 (전체 dedup 후)")

            if args.limit and len(unique_items) > args.limit:
                unique_items = unique_items[:args.limit]

            if args.skip_existing and published_ids:
                before = len(unique_items)
                unique_items = [it for it in unique_items if it['product_no'] not in published_ids]
                if before - len(unique_items):
                    logger.info(f"  등록 완료 스킵: {before - len(unique_items)}개")

            if not unique_items:
                continue

            # 2) 상세 수집 + 변환 + 저장
            batch: List[Dict] = []
            skipped_no_model = 0
            total = len(unique_items)

            for idx, list_item in enumerate(unique_items, 1):
                if session_mgr.is_blocked:
                    logger.error("  차단 감지됨 — 상세 수집 중단")
                    break

                product_no = list_item['product_no']
                if list_item.get('data_url'):
                    detail_url = f"{BASE_URL}{list_item['data_url']}"
                else:
                    detail_url = f"{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={cate_no}&display_group=1"

                detail_html, derror = session_mgr.fetch_page(detail_url)
                if derror or not detail_html:
                    logger.warning(f"  [{idx}/{total}] 상세 수집 실패: {derror} | {list_item.get('product_name','')[:30]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                detail = extract_detail_info(detail_html)
                row = build_raw_data(list_item, detail, full_path, detail_html)
                if not row:
                    skipped_no_model += 1
                    logger.info(f"  [{idx}/{total}] SKIP (no model_id) | {list_item.get('product_name','')[:50]}")
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    continue

                # mall_brands auto-INSERT
                en = row['brand_name_en']
                ko = row['brand_name_kr']
                if en and en.upper() not in new_brands:
                    new_brands.add(en.upper())
                    if not args.dry_run:
                        if ensure_mall_brand(en, ko):
                            logger.info(f"  [mall_brands] 신규 INSERT: en={en!r} ko={ko!r}")

                logger.info(
                    f"  [{idx}/{total}] {row['model_id']} | {int(row['raw_price']):>12,}원 | {row['stock_status']} | "
                    f"{(row['brand_name_en'] or '?')[:18]:<18} | {row['product_name'][:40]}"
                )
                total_collected += 1

                if args.dump and total_collected == 1:
                    print(json.dumps(row, ensure_ascii=False, indent=2, default=str))

                if not args.dry_run:
                    batch.append(row)

                if len(batch) >= 10:
                    save_to_database(batch)
                    logger.info(f"  DB 저장: {len(batch)}개")
                    batch = []

                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            if batch and not args.dry_run:
                save_to_database(batch)
                logger.info(f"  DB 저장(잔여): {len(batch)}개")

            total_skipped_no_model += skipped_no_model
            logger.info(f"  카테고리 완료: {full_path} (no model_id 스킵 {skipped_no_model}개)")

    finally:
        session_mgr.close()

    logger.info("\n" + "=" * 60)
    logger.info(f"라프리마 수집 완료")
    logger.info(f"  총 수집: {total_collected}개")
    logger.info(f"  model_id 없어서 스킵: {total_skipped_no_model}개")
    logger.info(f"  신규 발견 브랜드: {len(new_brands)}개")
    if not args.dry_run:
        with engine.connect() as conn:
            cnt = conn.execute(text(
                "SELECT COUNT(*) FROM raw_scraped_data WHERE source_site = :site"
            ), {'site': SOURCE_SITE}).scalar()
            mb = conn.execute(text(
                "SELECT COUNT(*) FROM mall_brands WHERE mall_name = 'laprima'"
            )).scalar()
            logger.info(f"  DB raw_scraped_data laprima: {cnt}개")
            logger.info(f"  DB mall_brands laprima: {mb}개")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()