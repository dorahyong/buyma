# -*- coding: utf-8 -*-
"""
바이마 최저가 수집기 + 마진율 계산

ace_products 테이블의 model_no를 기반으로 바이마에서 최저가를 검색하여
buyma_lowest_price, is_lowest_price, buyma_lowest_price_checked_at 컬럼에 저장
추가로 마진율(margin_rate_percent)을 계산하여 저장

사용법:
    python buyma_lowest_price_collector.py [--limit N] [--brand BRAND] [--dry-run]

옵션:
    --limit N: 처리할 최대 상품 수
    --brand BRAND: 특정 브랜드만 처리
    --dry-run: 실제 저장하지 않고 결과만 출력
"""

import re
import time
import random
import argparse
import urllib.parse
from datetime import datetime
from typing import Optional, Dict, List, Tuple

import requests
from bs4 import BeautifulSoup
import pymysql

# =====================================================
# 설정값
# =====================================================

# DB 연결 정보
DB_CONFIG = {
    'host': '54.180.248.182',
    'port': 3306,
    'user': 'block',
    'password': '1234',
    'database': 'buyma',
    'charset': 'utf8mb4'
}

# 바이마 검색 URL 템플릿 (-O3: 가격이 싼 순)
BUYMA_SEARCH_URL = "https://www.buyma.com/r/-O3/{model_no}/"

# HTTP 요청 헤더
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7,ja;q=0.6',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Referer': 'https://www.buyma.com/',
    'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
}

# 요청 간 대기 시간 (초)
REQUEST_DELAY = 1.5

# 마진 계산 상수
EXCHANGE_RATE = 9.2          # 환율 (원/엔) - 고정값
SALES_FEE_RATE = 0.055       # 바이마 판매수수료 5.5%
DEFAULT_SHIPPING_FEE = 15000 # 기본 예상 배송비 (원)

# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def parse_price(price_text: str) -> Optional[int]:
    """
    가격 문자열에서 숫자만 추출

    예: "¥ 261,800" -> 261800
    """
    if not price_text:
        return None

    # 숫자와 콤마만 추출
    numbers = re.findall(r'[\d,]+', price_text)
    if not numbers:
        return None

    # 첫 번째 숫자 그룹에서 콤마 제거 후 정수 변환
    try:
        return int(numbers[0].replace(',', ''))
    except ValueError:
        return None


def calculate_margin_rate(buyma_price_jpy: int, purchase_price_krw, 
                          shipping_fee_krw: int = DEFAULT_SHIPPING_FEE) -> Tuple[Optional[float], Optional[float]]:
    """
    마진율 및 마진액 계산

    계산 공식 (사용자 제공 7단계):
    1. 바이마 판매가 (원) = 바이마 최저가 (엔) × 환율(9.2)
    2. 판매수수료 (원) = 바이마 판매가 (원) × 5.5%
    3. 실수령액 (원) = 바이마 판매가 (원) - 판매수수료
    4. 총 원가 (원) = 구매가 + 예상 배송비
    5. 마진 (환급X) = 실수령액 - 총 원가
    6. 부가세 환급액 = 구매가 ÷ 11
    7. 마진 (환급포함) = 마진 (환급X) + 부가세 환급액
    8. 마진율 (%) = 마진 (환급포함) ÷ 바이마 판매가 (원) × 100

    Args:
        buyma_price_jpy: 바이마 최저가 (엔)
        purchase_price_krw: 구매가 (원)
        shipping_fee_krw: 예상 배송비 (원)

    Returns:
        (마진율(%), 마진액(원))
    """
    try:
        buyma_price_jpy = int(buyma_price_jpy) if buyma_price_jpy else 0
        purchase_price_krw = float(purchase_price_krw) if purchase_price_krw else 0
        shipping_fee_krw = int(shipping_fee_krw) if shipping_fee_krw else DEFAULT_SHIPPING_FEE
    except (ValueError, TypeError):
        return None, None

    if not buyma_price_jpy or buyma_price_jpy <= 0 or not purchase_price_krw or purchase_price_krw <= 0:
        return None, None

    # 1. 바이마 판매가 (원)
    buyma_price_krw = float(buyma_price_jpy) * EXCHANGE_RATE

    # 2. 판매수수료 (원)
    sales_fee_krw = buyma_price_krw * SALES_FEE_RATE

    # 3. 실수령액 (원)
    net_income_krw = buyma_price_krw - sales_fee_krw

    # 4. 총 원가 (원)
    total_cost_krw = purchase_price_krw + float(shipping_fee_krw)

    # 5. 마진 (환급X)
    margin_without_refund = net_income_krw - total_cost_krw

    # 6. 부가세 환급액 (구매가 / 11)
    vat_refund = purchase_price_krw / 11.0

    # 7. 총 마진액 (환급포함)
    total_margin_krw = margin_without_refund + vat_refund

    # 8. 마진율 (%)
    margin_rate = (total_margin_krw / buyma_price_krw) * 100.0

    return round(margin_rate, 2), round(total_margin_krw, 0)


# =====================================================
# 바이마 최저가 수집 클래스
# =====================================================

class BuymaLowestPriceCollector:
    """바이마 최저가 수집기"""

    def __init__(self, db_config: Dict):
        self.db_config = db_config
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get_connection(self) -> pymysql.Connection:
        """DB 연결 생성"""
        return pymysql.connect(**self.db_config)

    def fetch_products_to_check(self, limit: int = None, brand: str = None) -> List[Dict]:
        """
        최저가 확인이 필요한 상품 목록 조회

        Args:
            limit: 최대 조회 건수
            brand: 특정 브랜드만 조회

        Returns:
            상품 목록 [{id, model_no, price, brand_name, source_sales_price, category_id}, ...]
        """
        conn = self.get_connection()
        try:
            cur = conn.cursor(pymysql.cursors.DictCursor)

            query = """
                SELECT id, model_no, price, brand_name, source_sales_price, category_id
                FROM ace_products
                WHERE model_no IS NOT NULL
                  AND model_no != ''
                  AND is_active = 1
            """
            params = []

            if brand:
                query += " AND UPPER(brand_name) LIKE %s"
                params.append(f"%{brand.upper()}%")

            query += " ORDER BY buyma_lowest_price_checked_at ASC, id ASC"

            if limit:
                query += " LIMIT %s"
                params.append(limit)

            cur.execute(query, params)
            products = cur.fetchall()

            log(f"최저가 확인 대상 상품 {len(products)}건 조회")
            return products

        finally:
            conn.close()

    def search_buyma_lowest_price(self, model_no: str) -> Tuple[Optional[int], Optional[str]]:
        """
        바이마에서 model_no로 검색하여 최저가 조회

        Args:
            model_no: 모델번호 (예: "776523 V0VG4 8803")

        Returns:
            (최저가, 에러메시지) - 성공시 (가격, None), 실패시 (None, 에러메시지)
        """
        # URL 인코딩
        encoded_model_no = urllib.parse.quote(model_no, safe='')
        url = BUYMA_SEARCH_URL.format(model_no=encoded_model_no)

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            # HTML 파싱
            soup = BeautifulSoup(response.text, 'html.parser')

            # 첫 번째 상품 찾기 (li.product)
            first_product = soup.find('li', class_='product')
            if not first_product:
                # 검색 결과 없음
                return None, "검색 결과 없음"

            # Price_Txt 클래스에서 가격 추출
            price_elem = first_product.find('span', class_='Price_Txt')
            if not price_elem:
                return None, "가격 요소 없음"

            price_text = price_elem.get_text(strip=True)
            price = parse_price(price_text)

            if price is None:
                return None, f"가격 파싱 실패: {price_text}"

            return price, None

        except requests.exceptions.Timeout:
            return None, "요청 타임아웃"
        except requests.exceptions.RequestException as e:
            return None, f"요청 오류: {str(e)}"
        except Exception as e:
            return None, f"파싱 오류: {str(e)}"

    def get_shipping_fee(self, category_id: int) -> int:
        """
        카테고리별 예상 배송비 조회

        Args:
            category_id: ace_products.category_id (buyma_category_id)

        Returns:
            예상 배송비 (원), 없으면 기본값 15000
        """
        if not category_id:
            return DEFAULT_SHIPPING_FEE

        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT expected_shipping_fee
                FROM buyma_master_categories_data
                WHERE buyma_category_id = %s
            """, (category_id,))
            row = cur.fetchone()
            if row and row[0] is not None:
                # Decimal, int, float 모두 처리하여 int로 변환
                return int(float(row[0]))
            return DEFAULT_SHIPPING_FEE
        except Exception as e:
            # 테이블이 없거나 조회 실패 시 기본값 반환 (로그는 한 번만 출력)
            if "doesn't exist" not in str(e):
                log(f"배송비 조회 실패 (category_id={category_id}): {str(e)}", "WARNING")
            return DEFAULT_SHIPPING_FEE
        finally:
            conn.close()

    def update_lowest_price(self, product_id: int, lowest_price: Optional[int],
                           my_price: int, source_sales_price: int = None,
                           category_id: int = None, error_msg: str = None) -> Tuple[bool, Optional[float], Optional[float]]:
        """
        DB에 최저가 정보, 내 판매가 및 마진 정보 업데이트

        Args:
            product_id: ace_products.id
            lowest_price: 바이마 최저가 (없으면 None)
            my_price: 내 판매가 (바이마 판매 예정가)
            source_sales_price: 구매가 (오케이몰 판매가)
            category_id: 카테고리 ID (배송비 조회용)

        Returns:
            (성공 여부, 마진율, 마진액)
        """
        conn = self.get_connection()
        try:
            cur = conn.cursor()

            # 마진율 및 마진액 계산
            margin_rate, margin_amount = None, None
            purchase_price_jpy = None

            if lowest_price and source_sales_price:
                shipping_fee = self.get_shipping_fee(category_id)
                margin_rate, margin_amount = calculate_margin_rate(
                    buyma_price_jpy=lowest_price,
                    purchase_price_krw=source_sales_price,
                    shipping_fee_krw=shipping_fee
                )

            # 구매가(원) → 엔화 변환
            if source_sales_price:
                purchase_price_jpy = round(float(source_sales_price) / EXCHANGE_RATE)

            # 최저가 여부 판단
            is_lowest = 1 if lowest_price and my_price <= lowest_price else 0

            cur.execute("""
                UPDATE ace_products
                SET buyma_lowest_price = %s,
                    price = %s,
                    is_lowest_price = %s,
                    buyma_lowest_price_checked_at = NOW(),
                    margin_rate = %s,
                    margin_amount_krw = %s,
                    margin_calculated_at = CASE WHEN %s IS NOT NULL THEN NOW() ELSE margin_calculated_at END,
                    purchase_price_jpy = %s
                WHERE id = %s
            """, (lowest_price, my_price, is_lowest, margin_rate, margin_amount, margin_rate, purchase_price_jpy, product_id))

            conn.commit()
            return True, margin_rate, margin_amount

        except Exception as e:
            log(f"DB 업데이트 실패 (id={product_id}): {str(e)}", "ERROR")
            conn.rollback()
            return False, None, None
        finally:
            conn.close()

    def run(self, limit: int = None, brand: str = None, dry_run: bool = False) -> Dict:
        """
        최저가 수집 실행

        Args:
            limit: 최대 처리 건수
            brand: 특정 브랜드만 처리
            dry_run: True면 실제 저장하지 않음

        Returns:
            처리 결과 통계
        """
        log("=" * 60)
        log("바이마 최저가 수집 시작")
        log("=" * 60)

        if dry_run:
            log("*** DRY RUN 모드 - 실제 저장하지 않음 ***", "WARNING")

        # 대상 상품 조회
        products = self.fetch_products_to_check(limit=limit, brand=brand)

        if not products:
            log("처리할 상품이 없습니다.")
            return {'total': 0, 'success': 0, 'not_found': 0, 'failed': 0}

        # 통계
        success_count = 0
        not_found_count = 0
        failed_count = 0

        for idx, product in enumerate(products):
            product_id = product['id']
            model_no = product['model_no']
            my_price = product['price']
            brand_name = product.get('brand_name', '')
            source_sales_price = product.get('source_sales_price')
            category_id = product.get('category_id')

            log(f"[{idx+1}/{len(products)}] 검색 중: id={product_id}, model_no={model_no}")

            # 바이마 검색
            lowest_price, error_msg = self.search_buyma_lowest_price(model_no)

            margin_rate, margin_amount = None, None
            if error_msg:
                if "검색 결과 없음" in error_msg:
                    log(f"  → 검색 결과 없음")
                    not_found_count += 1
                else:
                    log(f"  → 실패: {error_msg}", "WARNING")
                    failed_count += 1
            else:
                # 내 판매가 결정: 최저가 - (1~9 랜덤 엔)
                my_price = lowest_price - random.randint(1, 9)
                
                # 마진 정보 계산 (로그 출력용)
                if source_sales_price:
                    shipping_fee = self.get_shipping_fee(category_id)
                    margin_rate, margin_amount = calculate_margin_rate(lowest_price, source_sales_price, shipping_fee)

                margin_str = f"{margin_rate:.2f}% ({margin_amount:,.0f}원)" if margin_rate is not None else "계산불가"
                log(f"  → 바이마 최저가: {lowest_price:,}엔 | 내 판매가: {my_price:,}엔 | 마진: {margin_str}")
                success_count += 1

            # DB 저장
            if not dry_run:
                self.update_lowest_price(
                    product_id=product_id,
                    lowest_price=lowest_price,
                    my_price=my_price,
                    source_sales_price=source_sales_price,
                    category_id=category_id,
                    error_msg=error_msg
                )

            # 요청 간 대기 (서버 부하 방지)
            if idx < len(products) - 1:
                time.sleep(REQUEST_DELAY)

        # 결과 출력
        log("=" * 60)
        log("수집 완료!")
        log(f"  총 처리: {len(products)}건")
        log(f"  성공: {success_count}건")
        log(f"  검색결과 없음: {not_found_count}건")
        log(f"  실패: {failed_count}건")
        log("=" * 60)

        return {
            'total': len(products),
            'success': success_count,
            'not_found': not_found_count,
            'failed': failed_count
        }


# =====================================================
# 메인 실행
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='바이마 최저가 수집기')
    parser.add_argument('--limit', type=int, default=None, help='처리할 최대 상품 수')
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만 처리')
    parser.add_argument('--dry-run', action='store_true', help='실제 저장하지 않고 결과만 출력')

    args = parser.parse_args()

    try:
        collector = BuymaLowestPriceCollector(DB_CONFIG)
        result = collector.run(
            limit=args.limit,
            brand=args.brand,
            dry_run=args.dry_run
        )

        if result['failed'] > 0:
            log("일부 수집이 실패했습니다.", "WARNING")

    except Exception as e:
        log(f"실행 중 오류 발생: {str(e)}", "ERROR")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
