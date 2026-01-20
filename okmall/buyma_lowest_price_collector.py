# -*- coding: utf-8 -*-
"""
바이마 최저가 수집기

ace_products 테이블의 model_no를 기반으로 바이마에서 최저가를 검색하여
buyma_lowest_price, is_lowest_price, buyma_lowest_price_checked_at 컬럼에 저장

사용법:
    python buyma_lowest_price_collector.py [--limit N] [--brand BRAND] [--dry-run]

옵션:
    --limit N: 처리할 최대 상품 수
    --brand BRAND: 특정 브랜드만 처리
    --dry-run: 실제 저장하지 않고 결과만 출력
"""

import re
import time
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
            상품 목록 [{id, model_no, price, brand_name}, ...]
        """
        conn = self.get_connection()
        try:
            cur = conn.cursor(pymysql.cursors.DictCursor)

            query = """
                SELECT id, model_no, price, brand_name
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

    def update_lowest_price(self, product_id: int, lowest_price: Optional[int],
                           my_price: int, error_msg: str = None) -> bool:
        """
        DB에 최저가 정보 업데이트

        Args:
            product_id: ace_products.id
            lowest_price: 바이마 최저가 (없으면 None)
            my_price: 내 판매가
            error_msg: 에러 메시지 (있으면 로그용)

        Returns:
            성공 여부
        """
        conn = self.get_connection()
        try:
            cur = conn.cursor()

            # 최저가 여부 판단
            is_lowest = 0
            if lowest_price is not None and my_price <= lowest_price:
                is_lowest = 1

            cur.execute("""
                UPDATE ace_products
                SET buyma_lowest_price = %s,
                    is_lowest_price = %s,
                    buyma_lowest_price_checked_at = NOW()
                WHERE id = %s
            """, (lowest_price, is_lowest, product_id))

            conn.commit()
            return True

        except Exception as e:
            log(f"DB 업데이트 실패 (id={product_id}): {str(e)}", "ERROR")
            conn.rollback()
            return False
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

            log(f"[{idx+1}/{len(products)}] 검색 중: id={product_id}, model_no={model_no}")

            # 바이마 검색
            lowest_price, error_msg = self.search_buyma_lowest_price(model_no)

            if error_msg:
                if "검색 결과 없음" in error_msg:
                    log(f"  → 검색 결과 없음")
                    not_found_count += 1
                else:
                    log(f"  → 실패: {error_msg}", "WARNING")
                    failed_count += 1
            else:
                # 최저가 비교
                price_diff = my_price - lowest_price if lowest_price else 0
                is_lowest = "예" if my_price <= lowest_price else "아니오"

                log(f"  → 바이마 최저가: {lowest_price:,}엔 | 내 가격: {my_price:,}엔 | "
                    f"차이: {price_diff:,}엔 | 최저가 여부: {is_lowest}")
                success_count += 1

            # DB 저장
            if not dry_run:
                self.update_lowest_price(product_id, lowest_price, my_price, error_msg)

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
