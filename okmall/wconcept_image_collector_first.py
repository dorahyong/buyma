"""
W컨셉에서 이미지 URL 수집하여 ace_product_images 테이블에 저장하는 스크립트

사용법:
    python wconcept_image_collector.py                      # 전체 실행
    python wconcept_image_collector.py --brand="NIKE"       # 특정 브랜드만
    python wconcept_image_collector.py --limit=10           # 최대 10개 상품만
    python wconcept_image_collector.py --dry-run            # 테스트 (DB 저장 안함)

작성일: 2026-01-19
"""

import argparse
import re
import time
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text

# =====================================================
# 설정
# =====================================================

DB_URL = "mysql+pymysql://block:1234@54.180.248.182:3306/buyma?charset=utf8mb4"

# W컨셉 URL
WCONCEPT_SEARCH_URL = "https://display.wconcept.co.kr/search"
WCONCEPT_PRODUCT_URL = "https://www.wconcept.co.kr/Product/{product_id}"

# 요청 헤더
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
}

# 요청 간 딜레이 (초) - 차단 방지
REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.0

# 최대 이미지 수 (바이마 제한: 20장)
MAX_IMAGES = 20

# NOT FOUND 표시
NOT_FOUND_VALUE = "not found"


# =====================================================
# 데이터 클래스
# =====================================================

@dataclass
class ImageData:
    """수집된 이미지 데이터"""
    ace_product_id: int
    position: int
    source_image_url: str
    is_uploaded: int = 0


@dataclass
class ProductImageResult:
    """상품별 이미지 수집 결과"""
    ace_product_id: int
    model_no: str
    wconcept_product_id: Optional[str] = None
    images: List[ImageData] = field(default_factory=list)
    status: str = "pending"  # pending, success, not_found, error
    error_message: Optional[str] = None


# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def random_delay() -> None:
    """랜덤 딜레이 (차단 방지)"""
    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
    time.sleep(delay)


def normalize_image_url(url: str) -> str:
    """이미지 URL 정규화"""
    if not url:
        return ""

    # // 로 시작하면 https: 추가
    if url.startswith("//"):
        return f"https:{url}"

    # 쿼리스트링 제거 (썸네일 크기 파라미터 등)
    if "?" in url:
        url = url.split("?")[0]

    return url


# =====================================================
# W컨셉 크롤링 클래스
# =====================================================

class WconceptImageCollector:
    """W컨셉 이미지 수집기"""

    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        log("WconceptImageCollector 초기화 완료")

    def fetch_target_products(self, brand: str = None, limit: int = None) -> List[Dict]:
        """
        이미지 수집 대상 상품 조회
        - ace_product_images에 아직 이미지가 없는 상품
        """
        with self.engine.connect() as conn:
            query = """
                SELECT ap.id, ap.model_no, ap.brand_name, ap.name
                FROM ace_products ap
                LEFT JOIN ace_product_images api ON ap.id = api.ace_product_id
                WHERE api.id IS NULL
                  AND ap.model_no IS NOT NULL
                  AND ap.model_no != ''
            """
            params = {}

            if brand:
                query += " AND UPPER(ap.brand_name) LIKE :brand"
                params['brand'] = f"%{brand.upper()}%"

            query += " ORDER BY ap.id"

            if limit:
                query += " LIMIT :limit"
                params['limit'] = limit

            result = conn.execute(text(query), params)

            products = []
            for row in result:
                products.append({
                    'id': row[0],
                    'model_no': row[1],
                    'brand_name': row[2],
                    'name': row[3]
                })

            log(f"이미지 수집 대상: {len(products)}개 상품")
            return products

    def search_wconcept(self, model_no: str) -> Optional[str]:
        """
        W컨셉에서 모델번호로 검색하여 첫 번째 상품 ID 반환

        Args:
            model_no: 모델번호 (예: 754443V3IV18425)

        Returns:
            W컨셉 상품 ID (없으면 None)
        """
        try:
            params = {
                'keyword': model_no,
                'type': 'direct'
            }

            response = self.session.get(
                WCONCEPT_SEARCH_URL,
                params=params,
                timeout=30
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # 검색 결과에서 첫 번째 상품 찾기
            # 방법 1: product-item 클래스에서 링크 추출
            product_items = soup.select('.product-item a[href*="/Product/"]')

            if not product_items:
                # 방법 2: 다른 선택자 시도
                product_items = soup.select('a[href*="/Product/"]')

            for item in product_items:
                href = item.get('href', '')
                # /Product/307698365 형태에서 ID 추출
                match = re.search(r'/Product/(\d+)', href)
                if match:
                    return match.group(1)

            # 방법 3: 이미지 URL에서 상품 ID 추출
            img_tags = soup.select('img[src*="product-image.wconcept.co.kr"]')
            for img in img_tags:
                src = img.get('src', '')
                # productimg/image/img9/65/307698365_GG10848.jpg 형태에서 ID 추출
                match = re.search(r'/(\d{9,})_', src)
                if match:
                    return match.group(1)

            return None

        except Exception as e:
            log(f"W컨셉 검색 오류 (model_no={model_no}): {e}", "ERROR")
            return None

    def get_product_images(self, product_id: str, model_no: str) -> List[str]:
        """
        W컨셉 상품 상세 페이지에서 이미지 URL 추출

        Args:
            product_id: W컨셉 상품 ID
            model_no: 모델번호 (상세 이미지 필터링용)

        Returns:
            이미지 URL 리스트
        """
        try:
            url = WCONCEPT_PRODUCT_URL.format(product_id=product_id)

            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            images = []

            # 1. 썸네일 이미지 (gallery_wrap에서 data-zoom-image)
            gallery = soup.select('#gallery li a[data-zoom-image]')
            for item in gallery:
                zoom_url = item.get('data-zoom-image', '')
                if zoom_url:
                    normalized = normalize_image_url(zoom_url)
                    if normalized and normalized not in images:
                        images.append(normalized)

            # 2. 상세 이미지 (divImageDetail에서 모델번호 alt만)
            detail_div = soup.select_one('#divImageDetail')
            if detail_div:
                detail_imgs = detail_div.select('img')
                for img in detail_imgs:
                    alt = img.get('alt', '')
                    src = img.get('src', '')

                    # PREFIX_INFO, SIZE_INFO 등 제외
                    if alt and alt not in ['PREFIX_INFO', 'SIZE_INFO', '']:
                        # 모델번호가 alt에 포함되어 있는지 확인
                        # 또는 모델번호 일부가 포함된 경우도 허용
                        model_parts = model_no.split('-') if '-' in model_no else [model_no]
                        is_model_image = any(part in alt for part in model_parts if len(part) > 3)

                        if is_model_image and src:
                            normalized = normalize_image_url(src)
                            if normalized and normalized not in images:
                                images.append(normalized)

            # 최대 이미지 수 제한
            return images[:MAX_IMAGES]

        except Exception as e:
            log(f"상품 상세 페이지 오류 (product_id={product_id}): {e}", "ERROR")
            return []

    def collect_single_product(self, product: Dict) -> ProductImageResult:
        """
        단일 상품의 이미지 수집

        Args:
            product: ace_products 레코드

        Returns:
            ProductImageResult
        """
        ace_product_id = product['id']
        model_no = product['model_no']

        result = ProductImageResult(
            ace_product_id=ace_product_id,
            model_no=model_no
        )

        try:
            # 1. W컨셉 검색
            wconcept_id = self.search_wconcept(model_no)
            random_delay()

            if not wconcept_id:
                # 검색 결과 없음 → not found
                result.status = "not_found"
                result.images = [ImageData(
                    ace_product_id=ace_product_id,
                    position=1,
                    source_image_url=NOT_FOUND_VALUE
                )]
                return result

            result.wconcept_product_id = wconcept_id

            # 2. 상세 페이지에서 이미지 추출
            image_urls = self.get_product_images(wconcept_id, model_no)
            random_delay()

            if not image_urls:
                # 이미지 추출 실패 → not found
                result.status = "not_found"
                result.images = [ImageData(
                    ace_product_id=ace_product_id,
                    position=1,
                    source_image_url=NOT_FOUND_VALUE
                )]
                return result

            # 3. 이미지 데이터 구성
            for idx, url in enumerate(image_urls):
                result.images.append(ImageData(
                    ace_product_id=ace_product_id,
                    position=idx + 1,
                    source_image_url=url
                ))

            result.status = "success"
            return result

        except Exception as e:
            result.status = "error"
            result.error_message = str(e)
            result.images = [ImageData(
                ace_product_id=ace_product_id,
                position=1,
                source_image_url=NOT_FOUND_VALUE
            )]
            return result

    def batch_collect(self, brand: str = None, limit: int = None) -> List[ProductImageResult]:
        """
        배치로 이미지 URL 수집 (DB 저장 전)

        Args:
            brand: 브랜드 필터
            limit: 최대 처리 건수

        Returns:
            수집 결과 리스트
        """
        products = self.fetch_target_products(brand=brand, limit=limit)

        if not products:
            log("수집 대상 상품이 없습니다.")
            return []

        results = []
        total = len(products)

        for idx, product in enumerate(products):
            log(f"[{idx+1}/{total}] 수집 중: model_no={product['model_no']}, "
                f"brand={product['brand_name']}")

            result = self.collect_single_product(product)
            results.append(result)

            if result.status == "success":
                log(f"  → 성공: {len(result.images)}개 이미지 (wconcept_id={result.wconcept_product_id})")
            elif result.status == "not_found":
                log(f"  → 검색 결과 없음", "WARNING")
            else:
                log(f"  → 오류: {result.error_message}", "ERROR")

        return results

    def batch_insert(self, results: List[ProductImageResult]) -> Dict:
        """
        수집된 이미지 데이터를 DB에 일괄 저장

        Args:
            results: 수집 결과 리스트

        Returns:
            저장 통계
        """
        stats = {
            'total_products': len(results),
            'success': 0,
            'not_found': 0,
            'error': 0,
            'total_images': 0
        }

        if not results:
            return stats

        with self.engine.connect() as conn:
            for result in results:
                try:
                    for img in result.images:
                        conn.execute(text("""
                            INSERT INTO ace_product_images (
                                ace_product_id, position, source_image_url, is_uploaded
                            ) VALUES (
                                :ace_product_id, :position, :source_image_url, :is_uploaded
                            )
                            ON DUPLICATE KEY UPDATE
                                source_image_url = VALUES(source_image_url),
                                updated_at = CURRENT_TIMESTAMP
                        """), {
                            'ace_product_id': img.ace_product_id,
                            'position': img.position,
                            'source_image_url': img.source_image_url,
                            'is_uploaded': img.is_uploaded
                        })

                        stats['total_images'] += 1

                    if result.status == "success":
                        stats['success'] += 1
                    elif result.status == "not_found":
                        stats['not_found'] += 1
                    else:
                        stats['error'] += 1

                except Exception as e:
                    log(f"DB 저장 오류 (ace_product_id={result.ace_product_id}): {e}", "ERROR")
                    stats['error'] += 1

            conn.commit()

        return stats

    def run(self, brand: str = None, limit: int = None, dry_run: bool = False) -> Dict:
        """
        전체 실행

        Args:
            brand: 브랜드 필터
            limit: 최대 처리 건수
            dry_run: True면 DB 저장 안함

        Returns:
            실행 통계
        """
        log("=" * 60)
        log("W컨셉 이미지 수집 시작")
        log("=" * 60)

        if brand:
            log(f"브랜드 필터: {brand}")
        if limit:
            log(f"최대 처리: {limit}건")
        if dry_run:
            log("*** DRY RUN 모드 - DB 저장 안함 ***", "WARNING")

        # 1. 배치 수집
        log("\n[Phase 1] 이미지 URL 수집")
        results = self.batch_collect(brand=brand, limit=limit)

        if not results:
            return {'total_products': 0}

        # 2. 배치 저장
        if dry_run:
            stats = {
                'total_products': len(results),
                'success': sum(1 for r in results if r.status == "success"),
                'not_found': sum(1 for r in results if r.status == "not_found"),
                'error': sum(1 for r in results if r.status == "error"),
                'total_images': sum(len(r.images) for r in results)
            }
            log("\n[Phase 2] DB 저장 (생략 - DRY RUN)")
        else:
            log("\n[Phase 2] DB 일괄 저장")
            stats = self.batch_insert(results)

        # 3. 결과 출력
        log("\n" + "=" * 60)
        log("수집 완료!")
        log(f"  총 상품: {stats['total_products']}건")
        log(f"  성공: {stats['success']}건")
        log(f"  검색 실패: {stats['not_found']}건")
        log(f"  오류: {stats['error']}건")
        log(f"  총 이미지: {stats['total_images']}개")
        log("=" * 60)

        return stats


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(
        description='W컨셉에서 이미지 URL 수집하여 ace_product_images 저장'
    )
    parser.add_argument(
        '--brand',
        type=str,
        default=None,
        help='특정 브랜드만 처리 (예: --brand="NIKE")'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='최대 처리 건수'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='테스트 모드 (DB 저장 안함)'
    )

    args = parser.parse_args()

    try:
        collector = WconceptImageCollector(DB_URL)
        stats = collector.run(
            brand=args.brand,
            limit=args.limit,
            dry_run=args.dry_run
        )

        if stats.get('error', 0) > 0:
            log("일부 오류가 발생했습니다.", "WARNING")

    except Exception as e:
        log(f"실행 오류: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
