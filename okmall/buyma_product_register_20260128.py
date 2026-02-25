# -*- coding: utf-8 -*-
"""
바이마 상품 등록 스크립트

ace_products 테이블의 데이터를 바이마 API를 통해 상품으로 등록합니다.
바이마 API는 비동기로 동작하며, 등록 결과는 Webhook을 통해 수신됩니다.

사용법:
    python buyma_product_register.py [--limit N] [--brand BRAND] [--dry-run] [--product-id ID]

옵션:
    --limit N: 처리할 최대 상품 수 (기본: 10)
    --brand BRAND: 특정 브랜드만 처리
    --dry-run: 실제 API 호출하지 않고 요청 데이터만 출력
    --product-id ID: 특정 ace_product ID만 처리 (테스트용)

작성일: 2026-01-22
"""

import os
import sys
import json
import time
import argparse
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from decimal import Decimal

import requests
import pymysql
import re
from dotenv import load_dotenv

def clean_text(text: str) -> str:
    """한국어 및 허용되지 않는 특수문자 제거 (안전장치)"""
    if not text: return ""
    # 한글(가-힣, ㄱ-ㅎ, ㅏ-ㅣ) 제거
    cleaned = re.sub(r'[가-힣ㄱ-ㅎㅏ-ㅣ]+', '', text)
    # 바이마에서 거절할 수 있는 기타 특수문자(아랍어 등) 제거 (일본어, 영어, 숫자, 기본 기호만 허용)
    # 허용 범위: \u3040-\u309F (히라가나), \u30A0-\u30FF (가타카나), \u4E00-\u9FFF (한자), \uFF00-\uFFEF (전각기호)
    #           A-Z, a-z, 0-9, 공백, 일반 기호
    return cleaned

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# =====================================================
# 설정값
# =====================================================

# DB 연결 정보
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '54.180.248.182'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'block'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': os.getenv('DB_NAME', 'buyma'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

# 바이마 API 설정
BUYMA_MODE = int(os.getenv('BUYMA_MODE', 1))  # 1: 본환경, 2: 샌드박스
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')

# 환경에 따른 API URL 선택
API_BASE_URL = BUYMA_API_BASE_URL if BUYMA_MODE == 1 else BUYMA_SANDBOX_URL

# 바이마 API 고정값
BUYMA_FIXED_VALUES = {
    'buying_area_id': '2002003000',       # 구매 지역 ID (한국)
    'shipping_area_id': '2002003000',     # 발송 지역 ID (한국)
    'theme_id': 98,                       # 테마 ID
    'duty': 'included',                   # 관세 포함
    'shipping_methods': [1063035],        # 배송 방법 ID
}

# 마진 계산 상수
EXCHANGE_RATE = 9.2          # 환율 (원/엔)
SALES_FEE_RATE = 0.055       # 바이마 판매수수료 5.5%
DEFAULT_SHIPPING_FEE = 15000 # 기본 예상 배송비 (원)

# API 호출 간격 (초)
API_CALL_DELAY = 1.5

# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def get_db_connection():
    """DB 연결 생성"""
    return pymysql.connect(**DB_CONFIG)


def decimal_to_float(obj):
    """Decimal을 float로 변환 (JSON 직렬화용)"""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# =====================================================
# 마진 계산 함수
# =====================================================

def calculate_margin(price_jpy: int, purchase_price_krw: float, shipping_fee_krw: int = DEFAULT_SHIPPING_FEE) -> Dict:
    """
    등록 직전 마진 재계산
    """
    # 1. 바이마 판매가 (원화)
    sales_price_krw = price_jpy * EXCHANGE_RATE

    # 2. 판매수수료 (원화)
    sales_fee_krw = sales_price_krw * SALES_FEE_RATE

    # 3. 실수령액 (원화)
    net_income_krw = sales_price_krw - sales_fee_krw

    # 4. 총 원가 (원화)
    total_cost_krw = purchase_price_krw + shipping_fee_krw

    # 5. 마진 (부가세 환급 전)
    margin_before_vat = net_income_krw - total_cost_krw

    # 6. 부가세 환급액
    vat_refund = purchase_price_krw / 11

    # 7. 최종 마진 (부가세 환급 포함)
    final_margin_krw = margin_before_vat + vat_refund

    # 8. 마진율
    margin_rate = (final_margin_krw / sales_price_krw) * 100 if sales_price_krw > 0 else 0

    return {
        'is_profitable': final_margin_krw > 0,
        'margin_krw': round(final_margin_krw, 0),
        'margin_rate': round(margin_rate, 2),
        'sales_price_krw': round(sales_price_krw, 0),
        'net_income_krw': round(net_income_krw, 0),
        'total_cost_krw': round(total_cost_krw, 0),
    }


# =====================================================
# 데이터 조회 함수
# =====================================================

def get_products_to_register(conn, limit: int = 10, brand: str = None, product_id: int = None) -> List[Dict]:
    """
    등록/수정 대상 상품 조회
    """
    with conn.cursor() as cursor:
        sql = """
            SELECT
                ap.id,
                ap.raw_data_id,
                ap.reference_number,
                ap.name,
                ap.comments,
                ap.brand_id,
                ap.brand_name,
                ap.category_id,
                ap.price,
                ap.original_price_jpy,
                ap.purchase_price_krw,
                ap.expected_shipping_fee,
                ap.available_until,
                ap.buying_shop_name,
                ap.model_no,
                ap.buyma_model_id,
                ap.colorsize_comments_jp,
                ap.source_product_url,
                ap.is_published,
                ap.buyma_product_id
            FROM ace_products ap
            WHERE ap.is_active = 1
            
              AND EXISTS (
                  SELECT 1 FROM ace_product_images img
                  WHERE img.ace_product_id = ap.id
                    AND img.cloudflare_image_url IS NOT NULL
              )
        """

        params = []

        if product_id:
            sql += " AND ap.id = %s"
            params.append(product_id)

        if brand:
            sql += " AND ap.brand_name LIKE %s"
            params.append(f"%{brand}%")

        sql += " ORDER BY ap.id"

        if limit:
            sql += " LIMIT %s"
            params.append(limit)

        cursor.execute(sql, params)
        return cursor.fetchall()


def get_product_images(conn, ace_product_id: int) -> List[Dict]:
    """상품 이미지 조회"""
    with conn.cursor() as cursor:
        sql = """
            SELECT position, cloudflare_image_url
            FROM ace_product_images
            WHERE ace_product_id = %s
              AND cloudflare_image_url IS NOT NULL
            ORDER BY position
            LIMIT 20
        """
        cursor.execute(sql, (ace_product_id,))
        return cursor.fetchall()


def get_product_options(conn, ace_product_id: int) -> List[Dict]:
    """상품 옵션 조회 (color, size) - details_json 포함"""
    with conn.cursor() as cursor:
        sql = """
            SELECT option_type, value, master_id, position, details_json
            FROM ace_product_options
            WHERE ace_product_id = %s
            ORDER BY option_type DESC, position
        """
        cursor.execute(sql, (ace_product_id,))
        return cursor.fetchall()


def get_product_variants(conn, ace_product_id: int) -> List[Dict]:
    """상품 재고(variant) 조회"""
    with conn.cursor() as cursor:
        sql = """
            SELECT color_value, size_value, stock_type, stocks
            FROM ace_product_variants
            WHERE ace_product_id = %s
              AND stock_type != 'out_of_stock'
        """
        cursor.execute(sql, (ace_product_id,))
        return cursor.fetchall()


# =====================================================
# API 요청 데이터 구성
# =====================================================

def build_images_array(image_rows: List[Dict]) -> List[Dict]:
    """images 배열 구성"""
    return [
        {"path": row['cloudflare_image_url'], "position": row['position']}
        for row in image_rows
    ]


def build_options_array(option_rows: List[Dict]) -> List[Dict]:
    """options 배열 구성 (size인 경우 details 포함)"""
    options = []
    for row in option_rows:
        option = {
            "type": row['option_type'],
            "value": row['value'],
            "position": row['position'],
            "master_id": row['master_id'] or 0
        }

        # size 옵션이고 details_json이 있으면 details 추가
        if row['option_type'] == 'size' and row.get('details_json'):
            try:
                details = json.loads(row['details_json'])
                if details:  # 빈 배열이 아닌 경우만 추가
                    option['details'] = details
            except (json.JSONDecodeError, TypeError):
                pass  # 파싱 실패 시 무시

        options.append(option)
    return options


def build_variants_array(variant_rows: List[Dict]) -> List[Dict]:
    """variants 배열 구성 (랜덤 재고 및 purchase_for_order 적용)"""
    variants = []
    for row in variant_rows:
        # 오케이몰 재고 상태 확인
        is_in_stock = row['stock_type'] != 'out_of_stock' and (row['stocks'] is None or row['stocks'] > 0)
        
        variant = {
            "options": [],
            "stock_type": "purchase_for_order" if is_in_stock else "out_of_stock"
        }
        # purchase_for_order일 때는 개별 stocks를 보내면 에러가 나므로 제외합니다. (바이마 API 필수 규칙)
        
        if row['color_value']:
            variant["options"].append({"type": "color", "value": row['color_value']})
        if row['size_value']:
            variant["options"].append({"type": "size", "value": row['size_value']})
        variants.append(variant)
    return variants


def build_request_json(product: Dict, images: List[Dict], options: List[Dict], variants: List[Dict]) -> Dict:
    """바이마 API 요청 JSON 구성 (등록/수정/삭제 통합)"""

    # 0. 전체 품절 여부 확인 (모든 variant가 out_of_stock이면 삭제)
    all_out_of_stock = all(v['stock_type'] == 'out_of_stock' for v in variants)
    
    if all_out_of_stock:
        return {
            "product": {
                "control": "delete",
                "reference_number": product['reference_number']
            }
        }

    # 1. 고정 공지사항 (comments) - 한국어 완벽 제거
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

    # 2. 색상/사이즈 보충 정보 푸터 (colorsize_comments footer) - 한국어 완벽 제거
    colorsize_footer = """

★ご購入前の在庫確認のお願い★

在庫状況はリアルタイムではなく、人気の商品は注文時す
でに《欠品》となっている可能性もございます。

確実でスピーディーなお取引と、注文確定後のキャンセル
によるお客様のご負担をなくすため、ご注文手続きの前に
【在庫確認】のご協力をお願いしております。

ご検討されている方も、お気軽にお問い合わせ欄からお声
掛け下さいませ。

※ 上記参考価格は現地参考価格を10KRW ＝ 1.1円で換算したものです
※仕入れはデパートや公式オンラインショップなど、100％正規品のみ扱っております"""

    # available_until 포맷 변환 (DATE → YYYY/MM/DD)
    available_until = product['available_until']
    if available_until:
        if isinstance(available_until, str):
            available_until_str = available_until.replace('-', '/')
        else:
            available_until_str = available_until.strftime('%Y/%m/%d')
    else:
        # 기본값: 30일 후
        available_until_str = (datetime.now() + timedelta(days=30)).strftime('%Y/%m/%d')

    # 배송 방법 배열 구성 (객체 배열 형식으로 복구)
    shipping_methods = [
        {"shipping_method_id": sm_id} for sm_id in BUYMA_FIXED_VALUES['shipping_methods']
    ]

    request_data = {
        # 필수 필드
        "control": "publish",
        "name": product['name'],
        "comments": fixed_comments,
        "brand_id": int(product['brand_id']) if product.get('brand_id') else 0,
        "category_id": int(product['category_id']),
        "price": int(product['price']),
        "available_until": available_until_str,
        "buying_area_id": BUYMA_FIXED_VALUES['buying_area_id'],
        "shipping_area_id": BUYMA_FIXED_VALUES['shipping_area_id'],
        "shipping_methods": shipping_methods,
        "images": build_images_array(images),
        "options": build_options_array(options),
        "variants": variants,  # 이미 밖에서 구성됨
        "order_quantity": random.randint(90, 100), # purchase_for_order 사용 시 필수 항목

        # 선택 필드
        "reference_number": product['reference_number'],
        "theme_id": BUYMA_FIXED_VALUES['theme_id'],
        "duty": BUYMA_FIXED_VALUES['duty'],
    }

    # 수정(PATCH) 요청인 경우 id 추가
    if product.get('is_published') == 1 and product.get('buyma_product_id'):
        request_data['id'] = product['buyma_product_id']

    # 선택 필드 추가 (값이 있는 경우만)
    if product.get('buying_shop_name'):
        request_data['buying_shop_name'] = product['buying_shop_name']

    if product.get('original_price_jpy'):
        request_data['reference_price'] = int(product['original_price_jpy'])

    if product.get('buyma_model_id'):
        request_data['model_id'] = product['buyma_model_id']

    # colorsize_comments_jp에 푸터 추가하여 전송
    base_colorsize = product.get('colorsize_comments_jp') or ""
    request_data['colorsize_comments'] = base_colorsize + colorsize_footer

    # 최상위를 'product' 키로 감싸서 반환 (바이마 API 필수 규격)
    return {"product": request_data}


# =====================================================
# 바이마 API 호출
# =====================================================

def call_buyma_api(request_data: Dict) -> Dict:
    """
    바이마 상품 등록 API 호출
    """
    url = f"{API_BASE_URL}api/v1/products"

    headers = {
        "Content-Type": "application/json",
        "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=request_data,
            timeout=30
        )

        log(f"API 응답 코드: {response.status_code}")

        if response.status_code in [200, 201, 202]:
            return {
                "success": True,
                "status_code": response.status_code,
                "response": response.json() if response.text else {},
                "headers": dict(response.headers)
            }
        else:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
                "headers": dict(response.headers)
            }

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timeout"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


# =====================================================
# DB 업데이트
# =====================================================

def update_product_after_request(conn, product_id: int, request_data: Dict, response: Dict) -> None:
    """API 요청 후 상품 상태 업데이트"""
    with conn.cursor() as cursor:
        if response.get('success'):
            # 요청 성공 - pending 상태로 변경
            sql = """
                UPDATE ace_products
                SET status = 'pending',
                    api_request_json = %s,
                    api_response_json = %s,
                    last_api_call_at = NOW()
                WHERE id = %s
            """
            cursor.execute(sql, (
                json.dumps(request_data, ensure_ascii=False, default=decimal_to_float),
                json.dumps(response, ensure_ascii=False),
                product_id
            ))
        else:
            # 요청 실패 - 에러 로그 저장
            sql = """
                UPDATE ace_products
                SET status = 'api_error',
                    api_request_json = %s,
                    api_response_json = %s,
                    last_api_call_at = NOW()
                WHERE id = %s
            """
            cursor.execute(sql, (
                json.dumps(request_data, ensure_ascii=False, default=decimal_to_float),
                json.dumps(response, ensure_ascii=False),
                product_id
            ))

        conn.commit()


# =====================================================
# 메인 로직
# =====================================================

def process_product(conn, product: Dict, dry_run: bool = False) -> bool:
    """
    단일 상품 처리
    """
    product_id = product['id']
    log(f"상품 처리 시작: ID={product_id}, 이름={product['name'][:30]}...")

    # 1. 마진 재계산 (최종 체크)
    price_jpy = product['price']
    purchase_price_krw = float(product['purchase_price_krw'] or 0)
    shipping_fee = product['expected_shipping_fee'] or DEFAULT_SHIPPING_FEE

    margin_info = calculate_margin(price_jpy, purchase_price_krw, shipping_fee)

    log(f"  - 판매가: ¥{price_jpy:,} (₩{margin_info['sales_price_krw']:,.0f})")
    log(f"  - 매입가: ₩{purchase_price_krw:,.0f}, 배송비: ₩{shipping_fee:,}")
    log(f"  - 마진: ₩{margin_info['margin_krw']:,.0f} ({margin_info['margin_rate']:.1f}%)")

    if not margin_info['is_profitable']:
        log(f"  - 마진 부족 (손해), 스킵", "WARN")
        return False

    # 2. 관련 데이터 조회
    images = get_product_images(conn, product_id)
    options = get_product_options(conn, product_id)
    variants = get_product_variants(conn, product_id)

    if not images:
        log(f"  - 이미지 없음, 스킵", "WARN")
        return False

    if not variants:
        log(f"  - 재고 없음, 스킵", "WARN")
        return False

    log(f"  - 이미지: {len(images)}개, 옵션: {len(options)}개, 재고: {len(variants)}개")

    # 3. API 요청 데이터 구성 (재고 데이터를 바이마 규격에 맞게 변환)
    formatted_variants = build_variants_array(variants)
    request_data = build_request_json(product, images, options, formatted_variants)

    if dry_run:
        log(f"  - [DRY-RUN] API 요청 데이터:")
        print(json.dumps(request_data, indent=2, ensure_ascii=False, default=decimal_to_float))
        return True

    # 4. 바이마 API 호출
    log(f"  - API 호출 중...")
    
    # 상세 데이터 로그 (Pretty Print) - 사장님이 직접 확인 가능
    print("\n>>> SENT JSON DATA:")
    print(json.dumps(request_data, indent=2, ensure_ascii=False, default=decimal_to_float))
    print("-" * 40)
    
    response = call_buyma_api(request_data)

    # 5. DB 업데이트
    update_product_after_request(conn, product_id, request_data, response)

    if response.get('success'):
        log(f"  - API 요청 성공 (결과는 Webhook으로 수신 예정)")
        return True
    else:
        log(f"  - API 요청 실패: {response.get('error', 'Unknown error')}", "ERROR")
        return False


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description='바이마 상품 등록 스크립트')
    parser.add_argument('--limit', type=int, help='처리할 최대 상품 수 (미지정시 전체)')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--dry-run', action='store_true', help='실제 API 호출 없이 테스트')
    parser.add_argument('--product-id', type=int, help='특정 상품 ID만 처리')
    args = parser.parse_args()

    log("=" * 60)
    log("바이마 상품 등록 시작")
    log(f"환경: {'본환경' if BUYMA_MODE == 1 else '샌드박스'}")
    log(f"API URL: {API_BASE_URL}")
    log(f"옵션: limit={args.limit}, brand={args.brand}, dry_run={args.dry_run}")
    log("=" * 60)

    if not BUYMA_ACCESS_TOKEN:
        log("BUYMA_ACCESS_TOKEN이 설정되지 않았습니다.", "ERROR")
        return

    conn = get_db_connection()

    try:
        # 등록 대상 상품 조회
        products = get_products_to_register(
            conn,
            limit=args.limit,
            brand=args.brand,
            product_id=args.product_id
        )

        log(f"등록 대상 상품: {len(products)}개")

        if not products:
            log("등록할 상품이 없습니다.")
            return

        success_count = 0
        fail_count = 0
        skip_count = 0

        for i, product in enumerate(products, 1):
            log(f"\n[{i}/{len(products)}] 처리 중...")

            try:
                result = process_product(conn, product, dry_run=args.dry_run)
                if result:
                    success_count += 1
                else:
                    skip_count += 1
            except Exception as e:
                log(f"상품 처리 중 오류: {e}", "ERROR")
                fail_count += 1

            # API 호출 간격 유지 (dry-run 제외)
            if not args.dry_run and i < len(products):
                time.sleep(API_CALL_DELAY)

        log("\n" + "=" * 60)
        log("처리 완료")
        log(f"성공: {success_count}, 스킵: {skip_count}, 실패: {fail_count}")
        log("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
