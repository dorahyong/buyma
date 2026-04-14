# -*- coding: utf-8 -*-
"""
빠른 최저가 업데이트 스크립트

source mall 접근 없이, 바이마 최저가 크롤링 + DB의 기존 purchase_price_krw만으로
최저가를 확보하거나, 마진 마이너스 시 삭제하는 스크립트.

처리 흐름:
1. DB에서 바이마 등록 상품 조회 (is_published=1, is_active=1)
2. 바이마 검색 → 경쟁자 최저가 조회
3. 내가 최저가 → 스킵
4. 내가 최저가 아님 → DB의 purchase_price_krw 기준 마진 계산
   - 마진 + → 가격 인하 (바이마 API 수정)
   - 마진 - → 바이마 API 삭제

사용법:
    python fast_price_updater.py                      # 전체 실행
    python fast_price_updater.py --dry-run             # 변경 대상만 확인
    python fast_price_updater.py --brand NIKE           # 특정 브랜드만
    python fast_price_updater.py --source okmall        # 특정 소스만
    python fast_price_updater.py --limit 100            # 최대 N건
    python fast_price_updater.py --count                # 건수만 확인

작성일: 2026-04-06
"""

import os
import sys
import io
import json
import time
import random
import re
import argparse
import urllib.parse
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests
from bs4 import BeautifulSoup
import pymysql
from dotenv import load_dotenv

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=True)

import unicodedata
from datetime import timedelta

# 바이마 API 고정값
BUYMA_FIXED_VALUES = {
    'buying_area_id': '2002003000',
    'shipping_area_id': '2002003000',
    'theme_id': 98,
    'duty': 'included',
    'shipping_methods': [1063035],
}


def _buyma_width(s: str) -> int:
    w = 0
    for c in s:
        eaw = unicodedata.east_asian_width(c)
        w += 2 if eaw in ('F', 'W', 'A') else 1
    return w


def truncate_option_value(text, max_limit=26):
    if not text:
        return ""
    if _buyma_width(text) <= max_limit:
        return text
    if '+' in text:
        parts = [p.strip() for p in text.split('+') if p.strip()]
        if len(parts) > 1:
            first = parts[0]
            suffix = f' 外{len(parts) - 1}色'
            combined = first + suffix
            if _buyma_width(combined) <= max_limit:
                return combined
    result = ""
    current_length = 0
    limit = max_limit - 3
    for char in text:
        eaw = unicodedata.east_asian_width(char)
        char_width = 2 if eaw in ('F', 'W', 'A') else 1
        if current_length + char_width > limit:
            break
        result += char
        current_length += char_width
    return result + "..."


def truncate_buyma_name(text, max_limit=60):
    if not text:
        return ""
    if _buyma_width(text) <= max_limit:
        return text
    result = ""
    current_length = 0
    for char in text:
        eaw = unicodedata.east_asian_width(char)
        char_width = 2 if eaw in ('F', 'W', 'A') else 1
        if current_length + char_width > max_limit:
            break
        result += char
        current_length += char_width
    return result


def generate_model_no_variants(model_no: str) -> list:
    if not model_no:
        return []
    variants = [model_no]
    no_space = model_no.replace(' ', '')
    if no_space != model_no:
        variants.append(no_space)
    hyphen = model_no.replace(' ', '-')
    if hyphen not in variants:
        variants.append(hyphen)
    return variants


# category_id별 size_details 허용 키
SIZE_DETAILS_KEYS_BY_CATEGORY = {
    'shoes': ['width', 'heel_height'],
    'clothing': ['shoulder_width', 'chest', 'sleeve_length', 'length', 'waist', 'hip'],
    'bags': ['width', 'height', 'depth', 'strap_length'],
}

def filter_details_by_category(details, category_id: int):
    """카테고리에 맞지 않는 size details 키 제거"""
    if not details:
        return details
    return details


# =====================================================
# 설정값
# =====================================================

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

# 바이마 API 설정
BUYMA_MODE = int(os.getenv('BUYMA_MODE', 1))
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')
BUYMA_BUYER_ID = os.getenv('BUYMA_BUYER_ID', '')
API_BASE_URL = BUYMA_API_BASE_URL if BUYMA_MODE == 1 else BUYMA_SANDBOX_URL

# 바이마 검색 URL (-O3: 가격 낮은 순)
BUYMA_SEARCH_URL = "https://www.buyma.com/r/-O3/{model_no}/"

# 마진 계산 상수
EXCHANGE_RATE = 9.2
SALES_FEE_RATE = 0.055
DEFAULT_SHIPPING_FEE = 15000

# 속도 설정
REQUEST_DELAY_MIN = 0.1
REQUEST_DELAY_MAX = 0.3
API_CALL_DELAY = 0.1
DEFAULT_WORKERS = 3

# HTTP 요청 헤더
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Referer': 'https://www.buyma.com/',
}


# 로그 파일 설정 (실행 시 1회 생성)
LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)
_LOG_FILE_PATH = os.path.join(LOGS_DIR, f"fast_price_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
_LOG_FILE = open(_LOG_FILE_PATH, 'a', encoding='utf-8')


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    _LOG_FILE.write(line + '\n')
    _LOG_FILE.flush()


def decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def parse_price(price_text: str) -> Optional[int]:
    """가격 문자열에서 숫자 추출 (예: "¥ 261,800" → 261800)"""
    if not price_text:
        return None
    numbers = re.findall(r'[\d,]+', price_text)
    if not numbers:
        return None
    try:
        return int(numbers[0].replace(',', ''))
    except ValueError:
        return None


# =====================================================
# 마진 계���
# =====================================================

def calculate_margin(price_jpy: int, purchase_price_krw: float,
                     shipping_fee_krw: int = DEFAULT_SHIPPING_FEE) -> Dict:
    """마진 계산 (stock_price_synchronizer.py와 동일)"""
    sales_price_krw = price_jpy * EXCHANGE_RATE
    sales_fee_krw = sales_price_krw * SALES_FEE_RATE
    net_income_krw = sales_price_krw - sales_fee_krw
    total_cost_krw = purchase_price_krw + shipping_fee_krw
    margin_before_vat = net_income_krw - total_cost_krw
    vat_refund = purchase_price_krw / 11
    final_margin_krw = margin_before_vat + vat_refund
    margin_rate = (final_margin_krw / sales_price_krw) * 100 if sales_price_krw > 0 else 0

    return {
        'is_profitable': final_margin_krw > 0,
        'margin_krw': round(final_margin_krw, 0),
        'margin_rate': round(margin_rate, 2),
        'sales_price_krw': round(sales_price_krw, 0),
    }


# =====================================================
# 메인 클래스
# =====================================================

class FastPriceUpdater:

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get_connection(self) -> pymysql.Connection:
        return pymysql.connect(**DB_CONFIG)

    # -------------------------------------------------
    # 1. DB 조회
    # -------------------------------------------------
    def get_products(self, limit: int = None, brand: str = None,
                     source: str = None, product_id: int = None) -> List[Dict]:
        """바이마 등록 상품 중 최저가 확인 대상 조회"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                sql = """
                    SELECT ap.id, ap.buyma_product_id, ap.reference_number,
                           ap.model_no, ap.brand_name, ap.name,
                           ap.category_id, ap.price, ap.source_site,
                           ap.purchase_price_krw, ap.expected_shipping_fee,
                           ap.buyma_lowest_price, ap.is_lowest_price
                    FROM ace_products ap
                    WHERE ap.is_published = 1
                      AND ap.buyma_product_id IS NOT NULL
                      AND ap.is_active = 1
                      AND ap.model_no IS NOT NULL
                      AND ap.model_no != ''
                """
                params = []

                if product_id:
                    sql += " AND ap.id = %s"
                    params.append(product_id)

                if brand:
                    sql += " AND UPPER(ap.brand_name) LIKE %s"
                    params.append(f"%{brand.upper()}%")

                if source:
                    sql += " AND ap.source_site = %s"
                    params.append(source.lower())

                sql += " ORDER BY ap.buyma_lowest_price_checked_at ASC, ap.id ASC"

                if limit:
                    sql += " LIMIT %s"
                    params.append(limit)

                cursor.execute(sql, params)
                return cursor.fetchall()
        finally:
            conn.close()

    # -------------------------------------------------
    # 2. 바이마 최저가 조회
    # -------------------------------------------------
    def get_buyma_lowest_price(self, model_no: str) -> Tuple[Optional[int], Optional[str]]:
        """바이마에서 경쟁자 최저가 조회 (내 상품/중고 제외)"""
        if not model_no:
            return None, "모델번호 없음"

        encoded = urllib.parse.quote(model_no, safe='')
        url = BUYMA_SEARCH_URL.format(model_no=encoded)

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            products = soup.find_all('li', class_='product')
            if not products:
                return None, "검색 결과 없음"

            for product in products:
                # 중고 제외
                used_tag = product.find('span', class_='product_used_tag')
                if used_tag:
                    continue

                # 내 상품 제외
                buyer_elem = product.select_one('.product_Buyer a')
                if buyer_elem:
                    href = buyer_elem.get('href', '')
                    buyer_match = re.search(r'/buyer/(\d+)', href)
                    if buyer_match:
                        buyer_id = buyer_match.group(1)
                        if BUYMA_BUYER_ID and buyer_id == BUYMA_BUYER_ID:
                            continue

                # 가격 추출
                price_elem = product.find('span', class_='Price_Txt')
                if price_elem:
                    price = parse_price(price_elem.get_text(strip=True))
                    if price:
                        return price, None

            return None, "경쟁자 없음"

        except requests.exceptions.Timeout:
            return None, "요청 타임아웃"
        except requests.exceptions.RequestException as e:
            return None, f"요청 오류: {str(e)}"
        except Exception as e:
            return None, f"파싱 오류: {str(e)}"

    # -------------------------------------------------
    # 3. 배송비 조회
    # -------------------------------------------------
    def get_shipping_fee(self, category_id: int) -> int:
        if not category_id:
            return DEFAULT_SHIPPING_FEE
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT expected_shipping_fee
                    FROM buyma_master_categories_data
                    WHERE buyma_category_id = %s
                """, (category_id,))
                row = cursor.fetchone()
                if row and row.get('expected_shipping_fee'):
                    return int(row['expected_shipping_fee'])
                return DEFAULT_SHIPPING_FEE
        except:
            return DEFAULT_SHIPPING_FEE
        finally:
            conn.close()

    # -------------------------------------------------
    # 4. 바이마 API 호출
    # -------------------------------------------------
    def call_buyma_api(self, request_data: Dict) -> Dict:
        url = f"{API_BASE_URL}api/v1/products"
        headers = {
            "Content-Type": "application/json",
            "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN
        }
        try:
            response = requests.post(url, headers=headers, json=request_data, timeout=30)
            if response.status_code in [200, 201, 202]:
                return {"success": True, "status_code": response.status_code}
            else:
                return {"success": False, "status_code": response.status_code, "error": response.text[:200]}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": str(e)}

    # -------------------------------------------------
    # 5. 바이마 API 요청 구성 (가격 수정용 — full data 필요)
    # -------------------------------------------------
    def get_product_data_for_api(self, ace_product_id: int) -> Dict:
        """stock_price_synchronizer와 동일 — API 호출에 필요한 전체 데이터 조회"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, buyma_product_id, reference_number, name, brand_id, brand_name,
                           category_id, price, original_price_jpy, buying_shop_name,
                           buyma_model_id, colorsize_comments_jp, available_until,
                           expected_shipping_fee, purchase_price_krw, model_no,
                           source_product_url, source_site,
                           is_buyma_locked,
                           locked_name, locked_brand_id, locked_category_id, locked_reference_number
                    FROM ace_products WHERE id = %s
                """, (ace_product_id,))
                product = cursor.fetchone()

                cursor.execute("""
                    SELECT position, cloudflare_image_url
                    FROM ace_product_images
                    WHERE ace_product_id = %s AND cloudflare_image_url IS NOT NULL
                    ORDER BY position LIMIT 20
                """, (ace_product_id,))
                images = cursor.fetchall()

                cursor.execute("""
                    SELECT option_type, value, master_id, position, details_json
                    FROM ace_product_options
                    WHERE ace_product_id = %s
                    ORDER BY option_type DESC, position
                """, (ace_product_id,))
                options = cursor.fetchall()

                cursor.execute("""
                    SELECT color_value, size_value, stock_type, stocks
                    FROM ace_product_variants
                    WHERE ace_product_id = %s
                """, (ace_product_id,))
                variants = cursor.fetchall()

                return {'product': product, 'images': images, 'options': options, 'variants': variants}
        finally:
            conn.close()

    def build_buyma_request_update(self, data: Dict, new_price_jpy: int) -> Dict:
        """가격 수정용 바이마 API 요청 (stock_price_synchronizer.build_buyma_request와 동일 구조)"""
        product = data['product']
        images = data['images']
        options = data['options']
        variants = data['variants']

        # 모델명 변형
        model_no_list = generate_model_no_variants(product.get('model_no', ''))
        model_no_text = '\n'.join(model_no_list)
        style_numbers = [{"number": num, "memo": ""} for num in model_no_list]

        # 고정 공지사항
        fixed_comments = """☆☆☆ ご購入前にご確認ください ☆☆☆

◆商品は直営店をはじめ、 デパート、 公式オンラインショップ、ショッピングモールなどの正規品を取り扱う店舗にて買い付けております。100％正規品ですのでご安心ください。

◆「あんしんプラス」へご加入の場合、「サイズがあわない」「イメージと違う」場合に「返品補償制度」をご利用頂けます。
※「返品対象商品」に限ります。詳しくは右記URLをご参照ください。https://qa.buyma.com/trouble/5206.html

◆ご注文～お届けまで
手元在庫有：【ご注文確定】 →【梱包】 → 【発送】 → 【お届け】
手元在庫無し：【ご注文確定】 →【買付】 →【検品】 →【梱包】 →【発送】→【お届け】

◆配送方法/日数
通常国際便（OCS）：【商品準備2-5日 】+ 【発送～お届け5-9日】
※平常時の目安です。繁忙期/非常時はお届け日が前後する場合もございます。詳しくはお問合せください。
※当店では検品時に不良/不具合がある場合は良品に交換をしてお送りしております。当理由でお時間を頂戴する場合は都度ご報告させて頂いております。

◆「お荷物追跡番号あり」にて配送しますので、随時、配送状況をご確認いただけます。
◆土・日・祝日は発送は休務のため、休み明けに順次発送となります。

◆海外製品は「MADE IN JAPAN」の製品に比べて、若干見劣りする場合もございます。
返品・交換にあたる不具合の条件に関しては「お取引について」をご確認ください。

◆当店では、日本完売品、日本未入荷アイテム、限定品、
メンズ、レディース、キッズの シューズ（スニーカー等）や衣類をメインに取り扱っております。
(カップル,ファミリー、ペアルック、親子リンク)
韓国の最新トレンドや新作アイテムを順次出品しており、

◆交換・返品・キャンセル
返品と交換に関する規定は、バイマ規定によりお客様の理由による返品はお受けいたしかねますので、ご購入には慎重にお願いいたします。
不良品・誤配送は交換、または返品が可能です。
モニター環境による色違い、サイズ測定方法による1~3cm程度の誤差、糸くず、糸の始末などは欠陥でみなされません。
製品の大きさは測定方法によって1~3cm程度の誤差が生じることがありますが、欠陥ではございません。

◆不良品について
検品は行っておりますが、海外製品は日本商品よりも検品基準が低いです。
下記の理由は返品や交換の原因にはなりません。
- 縫製の粗さ
- 縫い終わり部分の糸が切れていないで残っている
- 生地の色ムラ
- ミリ段位の傷
- 若干の汚れ、シミ
- 製造過程での接着剤の付着など"""

        colorsize_footer_sections = [
            """

★最安値に挑戦中！★
本商品は、私たちKONNECT（コネクト）が
お客様に少しでもお安く提供できるよう、
最安値での出品に努めた商品です。
出品時の市場価格調査はもちろん、
定期的にも価格チェックを行っております。
（※ただし、価格はリアルタイムで変動するため、
タイミングによっては最安値ではなくなる場合もございます。
あらかじめご了承ください。）""",
            """

★追加料金は一切なし！★
BUYMAでの決済金額以外、追加費用は一切かかりませんのでご安心ください。
関税・消費税・送料はすべて商品価格に含まれております。お客様が追加で支払う必要はございません。""",
            """

★安心の追跡付き発送★
KONNECT（コネクト）では、すべて追跡可能な配送方法でお届けいたします。
商品発送後、1～2日ほどでBUYMA上にて追跡番号をご確認いただけます""",
            """

★ご購入前の在庫確認のお願い★
在庫状況はリアルタイムではなく、人気の商品は注文時す
でに《欠品》となっている可能性もございます。
確実でスピーディーなお取引と、注文確定後のキャンセル
によるお客様のご負担をなくすため、ご注文手続きの前に
【在庫確認】のご協力をお願いしております。
ご検討されている方も、お気軽にお問い合わせ欄からお声
掛け下さいませ。""",
            """

※ 上記参考価格は現地参考価格を10KRW ＝ 1.1円で換算したものです
※仕入れはデパートや公式オンラインショップなど、100％正規品のみ扱っております"""
        ]

        # available_until
        available_until = product.get('available_until')
        if available_until:
            if isinstance(available_until, str):
                available_until_str = available_until.replace('-', '/')
            else:
                available_until_str = available_until.strftime('%Y/%m/%d')
        else:
            available_until_str = (datetime.now() + timedelta(days=90)).strftime('%Y/%m/%d')

        # images
        images_arr = [{"path": row['cloudflare_image_url'], "position": row['position']} for row in images]

        # variants에서 유효 color/size 추출
        valid_sizes = set()
        valid_colors = set()
        for v in variants:
            if v['color_value']:
                valid_colors.add(v['color_value'])
            if v['size_value']:
                valid_sizes.add(v['size_value'])

        # options
        options_arr = []
        for row in options:
            if row['option_type'] == 'size' and row['value'] not in valid_sizes:
                continue
            if row['option_type'] == 'color' and row['value'] not in valid_colors:
                continue
            opt = {
                "type": row['option_type'],
                "value": truncate_option_value(row['value']),
                "position": row['position'],
                "master_id": row['master_id'] or 0
            }
            if row['option_type'] == 'size' and row.get('details_json'):
                try:
                    details = json.loads(row['details_json'])
                    if details:
                        cat_id = product.get('locked_category_id') or product['category_id']
                        if cat_id:
                            details = filter_details_by_category(details, int(cat_id))
                        if details:
                            opt['details'] = details
                except:
                    pass
            options_arr.append(opt)

        # variants
        variants_arr = []
        for v in variants:
            is_in_stock = v['stock_type'] != 'out_of_stock' and (v['stocks'] is None or v['stocks'] > 0)
            variant = {
                "options": [],
                "stock_type": "purchase_for_order" if is_in_stock else "out_of_stock"
            }
            if v['color_value']:
                variant["options"].append({"type": "color", "value": truncate_option_value(v['color_value'])})
            if v['size_value']:
                variant["options"].append({"type": "size", "value": truncate_option_value(v['size_value'])})
            variants_arr.append(variant)

        # shipping
        shipping_methods = [{"shipping_method_id": sm_id} for sm_id in BUYMA_FIXED_VALUES['shipping_methods']]

        # 잠금 필드 처리
        if product.get('is_buyma_locked') == 1:
            api_name = product.get('locked_name') or product['name']
            api_brand_id = product.get('locked_brand_id') or product['brand_id']
            api_category_id = product.get('locked_category_id') or product['category_id']
            api_reference_number = product.get('locked_reference_number') or product['reference_number']
        else:
            api_name = product['name']
            api_brand_id = product['brand_id']
            api_category_id = product['category_id']
            api_reference_number = product['reference_number']

        request_data = {
            "control": "publish",
            "id": product['buyma_product_id'],
            "reference_number": api_reference_number,
            "name": truncate_buyma_name(api_name),
            "comments": f"{model_no_text}\n\n{fixed_comments}" if model_no_text else fixed_comments,
            "brand_id": int(api_brand_id) if api_brand_id else 0,
            "category_id": int(api_category_id),
            "price": new_price_jpy,
            "available_until": available_until_str,
            "buying_area_id": BUYMA_FIXED_VALUES['buying_area_id'],
            "shipping_area_id": BUYMA_FIXED_VALUES['shipping_area_id'],
            "shipping_methods": shipping_methods,
            "images": images_arr,
            "options": options_arr,
            "variants": variants_arr,
            "order_quantity": random.randint(90, 100),
            "theme_id": BUYMA_FIXED_VALUES['theme_id'],
            "duty": BUYMA_FIXED_VALUES['duty'],
        }

        if not api_brand_id or api_brand_id == 0:
            if product.get('brand_name'):
                request_data['brand_name'] = product['brand_name']
        else:
            request_data['style_numbers'] = style_numbers

        if product.get('buying_shop_name'):
            request_data['buying_shop_name'] = truncate_buyma_name(product['buying_shop_name'], max_limit=30)
        if product.get('original_price_jpy'):
            ref_price = int(product['original_price_jpy'])
            if ref_price > new_price_jpy:
                request_data['reference_price'] = ref_price
        if product.get('buyma_model_id'):
            request_data['model_id'] = product['buyma_model_id']

        if product.get('source_product_url'):
            request_data['shop_urls'] = [{
                "url": product['source_product_url'],
                "label": product.get('source_site', ''),
                "description": ""
            }]

        # colorsize_comments 글자수 제한 (1000자)
        COLORSIZE_LIMIT = 1000
        base_colorsize = product.get('colorsize_comments_jp') or ""
        remaining = COLORSIZE_LIMIT - len(base_colorsize)
        end_idx = 0
        cumulative_len = 0
        for i in range(len(colorsize_footer_sections)):
            section_len = len(colorsize_footer_sections[i])
            if cumulative_len + section_len <= remaining:
                cumulative_len += section_len
                end_idx = i + 1
            else:
                break
        colorsize_footer = ''.join(colorsize_footer_sections[:end_idx])
        request_data['colorsize_comments'] = base_colorsize + colorsize_footer

        return {"product": request_data}

    def build_buyma_request_delete(self, reference_number: str) -> Dict:
        """삭제용 바이마 API 요청 구성"""
        return {
            "product": {
                "control": "delete",
                "reference_number": reference_number,
            }
        }

    # -------------------------------------------------
    # 6. DB 업데이트
    # -------------------------------------------------
    def update_price_in_db(self, ace_product_id: int, new_price_jpy: int,
                           competitor_lowest_price: int, margin_rate: float,
                           margin_amount_krw: float) -> None:
        """가격 인하 후 DB 업데이트"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ace_products
                    SET price = %s,
                        buyma_lowest_price = %s,
                        is_lowest_price = 1,
                        margin_rate = %s,
                        margin_amount_krw = %s,
                        margin_calculated_at = NOW(),
                        buyma_lowest_price_checked_at = NOW()
                    WHERE id = %s
                """, (new_price_jpy, competitor_lowest_price, margin_rate,
                      margin_amount_krw, ace_product_id))
                conn.commit()
        finally:
            conn.close()

    def update_after_delete(self, ace_product_id: int) -> None:
        """삭제 후 DB 비활성화"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ace_products
                    SET is_active = 0,
                        is_published = 0,
                        status = 'deleted',
                        updated_at = NOW()
                    WHERE id = %s
                """, (ace_product_id,))
                conn.commit()
        finally:
            conn.close()

    def update_lowest_price_checked(self, ace_product_id: int,
                                     competitor_lowest_price: Optional[int]) -> None:
        """최저가인 경우 checked_at만 업데이트"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ace_products
                    SET buyma_lowest_price = %s,
                        is_lowest_price = 1,
                        buyma_lowest_price_checked_at = NOW()
                    WHERE id = %s
                """, (competitor_lowest_price, ace_product_id))
                conn.commit()
        finally:
            conn.close()

    def update_api_log(self, ace_product_id: int, request_data: Dict, response: Dict) -> None:
        """API 호출 로그 저장"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                new_status = 'pending' if response.get('success') else 'api_error'
                cursor.execute("""
                    UPDATE ace_products SET status = %s WHERE id = %s
                """, (new_status, ace_product_id))
                cursor.execute("""
                    INSERT INTO ace_product_api_logs (ace_product_id, api_request_json, api_response_json, last_api_call_at)
                    VALUES (%s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE api_request_json = VALUES(api_request_json),
                                            api_response_json = VALUES(api_response_json),
                                            last_api_call_at = NOW()
                """, (
                    ace_product_id,
                    json.dumps(request_data, ensure_ascii=False, default=decimal_to_float),
                    json.dumps(response, ensure_ascii=False)
                ))
                conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------
    # 7. 단일 상품 처리
    # -------------------------------------------------
    def process_single_product(self, product: Dict, idx: int, total: int,
                                dry_run: bool, stats: Dict, stats_lock: threading.Lock) -> None:
        ace_id = product['id']
        model_no = product['model_no']
        brand = product['brand_name'] or ''
        current_price = product.get('price') or 0
        purchase_price_krw = float(product.get('purchase_price_krw') or 0)

        prefix = f"[{idx}/{total}] ace_id={ace_id} | {brand} | {model_no}"

        # 매입가 없으면 스킵
        if not purchase_price_krw or purchase_price_krw <= 0:
            log(f"{prefix} [스킵] 매입가 없음")
            with stats_lock:
                stats['skipped'] += 1
            return

        # 바이마 최저가 조회
        competitor_price, error = self.get_buyma_lowest_price(model_no)
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        # 경쟁자 없음 → 이미 최저가, 스킵
        if error:
            if "경쟁자 없음" in error or "검색 결과 없음" in error:
                self.update_lowest_price_checked(ace_id, None)
                log(f"{prefix} [스킵] 경쟁자 없음 (최저가)")
                with stats_lock:
                    stats['already_lowest'] += 1
                return
            else:
                log(f"{prefix} [오류] 최저가 조회 실패: {error}", "WARNING")
                with stats_lock:
                    stats['error'] += 1
                return

        # 내가 이미 최저가인지 확인
        if current_price <= competitor_price:
            price_range_min = competitor_price - 9
            price_range_max = competitor_price - 1

            if price_range_min <= current_price <= price_range_max:
                # 적정 범위 내 → 스킵
                self.update_lowest_price_checked(ace_id, competitor_price)
                log(f"{prefix} [스킵] 이미 최저가 (내 ¥{current_price:,} | 경쟁자 ¥{competitor_price:,} | 범위 내)")
                with stats_lock:
                    stats['already_lowest'] += 1
                return

            # gap이 큼 → 2순위 바로 아래로 가격 조정
            new_price_jpy = competitor_price - random.randint(1, 9)
            log(f"{prefix} [조정] 최저가지만 gap 큼: ¥{current_price:,}→¥{new_price_jpy:,} (경쟁자 ¥{competitor_price:,})")

            if dry_run:
                with stats_lock:
                    stats['price_lowered'] += 1
                return

            shipping_fee = product.get('expected_shipping_fee') or self.get_shipping_fee(product.get('category_id'))
            margin_info = calculate_margin(new_price_jpy, purchase_price_krw, shipping_fee)
            self.update_price_in_db(ace_id, new_price_jpy, competitor_price,
                                     margin_info['margin_rate'], margin_info['margin_krw'])

            api_data = self.get_product_data_for_api(ace_id)
            request_json = self.build_buyma_request_update(api_data, new_price_jpy)
            result = self.call_buyma_api(request_json)
            self.update_api_log(ace_id, request_json, result)

            if result.get('success'):
                with stats_lock:
                    stats['price_lowered'] += 1
                    stats['api_called'] += 1
            else:
                log(f"  → API 실패: {result.get('error', 'Unknown')}", "ERROR")
                with stats_lock:
                    stats['api_failed'] += 1

            time.sleep(API_CALL_DELAY)
            return

        # 내가 최저가 아님 → 가격 인하 가능 여부 확인
        new_price_jpy = competitor_price - random.randint(1, 9)
        shipping_fee = product.get('expected_shipping_fee') or self.get_shipping_fee(product.get('category_id'))
        margin_info = calculate_margin(new_price_jpy, purchase_price_krw, shipping_fee)

        if margin_info['is_profitable']:
            # 마진 + → 가격 인하
            log(f"{prefix} [인하] ¥{current_price:,}→¥{new_price_jpy:,} (경쟁자 ¥{competitor_price:,}) | "
                f"마진 ₩{margin_info['margin_krw']:,.0f} ({margin_info['margin_rate']:.1f}%)")

            if dry_run:
                with stats_lock:
                    stats['price_lowered'] += 1
                return

            # DB 업데이트
            self.update_price_in_db(ace_id, new_price_jpy, competitor_price,
                                     margin_info['margin_rate'], margin_info['margin_krw'])

            # 바이마 API 호출 (가격 수정)
            api_data = self.get_product_data_for_api(ace_id)
            request_json = self.build_buyma_request_update(api_data, new_price_jpy)
            result = self.call_buyma_api(request_json)
            self.update_api_log(ace_id, request_json, result)

            if result.get('success'):
                with stats_lock:
                    stats['price_lowered'] += 1
                    stats['api_called'] += 1
            else:
                log(f"  → API 실패: {result.get('error', 'Unknown')}", "ERROR")
                with stats_lock:
                    stats['api_failed'] += 1

            time.sleep(API_CALL_DELAY)

        else:
            # 마진 - → 삭제
            log(f"{prefix} [삭제] 마진 마이너스 ₩{margin_info['margin_krw']:,.0f} (경쟁자 ¥{competitor_price:,})", "WARNING")

            if dry_run:
                with stats_lock:
                    stats['to_delete'] += 1
                return

            # 바이마 API 삭제
            reference_number = product.get('reference_number')
            request_json = self.build_buyma_request_delete(reference_number)
            result = self.call_buyma_api(request_json)
            self.update_api_log(ace_id, request_json, result)

            if result.get('success'):
                self.update_after_delete(ace_id)
                with stats_lock:
                    stats['deleted'] += 1
                    stats['api_called'] += 1
            else:
                log(f"  → 삭제 API 실패: {result.get('error', 'Unknown')}", "ERROR")
                with stats_lock:
                    stats['api_failed'] += 1

            time.sleep(API_CALL_DELAY)

    # -------------------------------------------------
    # 8. 메인 실행
    # -------------------------------------------------
    def run(self, limit: int = None, brand: str = None, source: str = None,
            product_id: int = None, dry_run: bool = False, count_only: bool = False) -> Dict:

        log("=" * 60)
        log("빠른 최저가 업데이트 시작")
        log(f"  옵션: brand={brand}, source={source}, limit={limit}, dry_run={dry_run}")
        log(f"  병렬 처리: {DEFAULT_WORKERS}개 스레드")
        log("=" * 60)

        products = self.get_products(limit=limit, brand=brand, source=source, product_id=product_id)
        log(f"대상 상품: {len(products)}건")

        if count_only:
            # source_site별 집계
            site_counts = {}
            for p in products:
                site = p.get('source_site') or '(없음)'
                site_counts[site] = site_counts.get(site, 0) + 1
            for site, cnt in sorted(site_counts.items(), key=lambda x: -x[1]):
                log(f"  {site}: {cnt}건")
            return {'total': len(products)}

        if not products:
            log("대상 상품이 없습니다.")
            return {'total': 0}

        if dry_run:
            log("*** DRY-RUN 모드 — 실제 API 호출 안함 ***", "WARNING")

        stats = {
            'total': len(products),
            'already_lowest': 0,
            'price_lowered': 0,
            'deleted': 0,
            'to_delete': 0,
            'skipped': 0,
            'error': 0,
            'api_called': 0,
            'api_failed': 0,
        }
        stats_lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as executor:
            futures = []
            for idx, product in enumerate(products):
                future = executor.submit(
                    self.process_single_product,
                    product, idx + 1, len(products),
                    dry_run, stats, stats_lock
                )
                futures.append(future)

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    log(f"스레드 오류: {e}", "ERROR")
                    with stats_lock:
                        stats['error'] += 1

        log("")
        log("=" * 60)
        if dry_run:
            log(f"[DRY-RUN] 결과:")
            log(f"  총 대상: {stats['total']}건")
            log(f"  이미 최저가: {stats['already_lowest']}건")
            log(f"  가격 인하 대상: {stats['price_lowered']}건")
            log(f"  삭제 대상 (마진-): {stats['to_delete']}건")
            log(f"  매입가 없음 스킵: {stats['skipped']}건")
            log(f"  조회 오류: {stats['error']}건")
        else:
            log(f"완료:")
            log(f"  총 대상: {stats['total']}건")
            log(f"  이미 최저가: {stats['already_lowest']}건")
            log(f"  가격 인하: {stats['price_lowered']}건")
            log(f"  삭제: {stats['deleted']}건")
            log(f"  매입가 없음 스킵: {stats['skipped']}건")
            log(f"  API 호출: {stats['api_called']}건")
            log(f"  API 실패: {stats['api_failed']}건")
            log(f"  조회 오류: {stats['error']}건")
        log("=" * 60)

        return stats


def main():
    parser = argparse.ArgumentParser(description='빠른 최저가 업데이트 (source mall 접근 없음)')
    parser.add_argument('--count', action='store_true', help='대상 건수만 확인')
    parser.add_argument('--dry-run', action='store_true', help='실제 API 호출 없이 결과만 확인')
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만')
    parser.add_argument('--source', type=str, default=None, help='특정 소스만 (okmall, kasina 등)')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    parser.add_argument('--id', type=int, default=None, help='특정 상품 ID')
    args = parser.parse_args()

    updater = FastPriceUpdater()
    updater.run(
        limit=args.limit,
        brand=args.brand,
        source=args.source,
        product_id=args.id,
        dry_run=args.dry_run,
        count_only=args.count,
    )


if __name__ == "__main__":
    main()
