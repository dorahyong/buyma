"""
W컨셉에서 이미지 URL 수집하여 ace_product_images 테이블에 저장하는 스크립트
Playwright 기반 - CSR 페이지 대응 및 봇 차단 우회

수정 로직 (v5):
- 검색 결과에서 첫 번째 상품의 썸네일 이미지 개수가 5개 이상이면 첫 번째 상품 선택
- 5개 미만이면 두 번째 상품(있다면)의 썸네일 이미지 개수 확인
- 첫 번째와 두 번째 중 썸네일 이미지가 더 많은 상품의 이미지를 수집

사용법:
    python wconcept_image_collector_5.py                      # 전체 실행
    python wconcept_image_collector_5.py --brand="NIKE"       # 특정 브랜드만
    python wconcept_image_collector_5.py --limit=10           # 최대 10개 상품만
    python wconcept_image_collector_5.py --dry-run            # 테스트 (DB 저장 안함)
    python wconcept_image_collector_5.py --headless=false     # 브라우저 표시 (디버깅용)

설치:
    pip install playwright sqlalchemy pymysql
    playwright install chromium

작성일: 2026-01-19
"""

import argparse
import re
import time
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from sqlalchemy import create_engine, text

# =====================================================
# 설정
# =====================================================

DB_URL = "mysql+pymysql://block:1234@54.180.248.182:3306/buyma?charset=utf8mb4"

# W컨셉 URL
WCONCEPT_SEARCH_URL = "https://display.wconcept.co.kr/search"
WCONCEPT_PRODUCT_URL = "https://www.wconcept.co.kr/Product/{product_id}"

# 요청 간 딜레이 (초) - 차단 방지
REQUEST_DELAY_MIN = 2.0
REQUEST_DELAY_MAX = 4.0

# 페이지 로딩 타임아웃 (밀리초)
PAGE_TIMEOUT = 30000

# 최대 이미지 수 (바이마 제한: 20장)
MAX_IMAGES = 20

# 최소 이미지 개수 기준 (이 개수 이상이면 첫 번째 상품 선택)
MIN_IMAGE_COUNT = 5

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
        url = f"https:{url}"

    # 썸네일 쿼리스트링 제거 (예: ?thumbnail=60x80)
    if "?thumbnail" in url:
        url = url.split("?")[0]

    return url


# =====================================================
# W컨셉 Playwright 크롤러
# =====================================================

class WconceptImageCollector:
    """W컨셉 이미지 수집기 - Playwright 기반"""

    def __init__(self, db_url: str, headless: bool = True):
        """
        초기화

        Args:
            db_url: 데이터베이스 연결 URL
            headless: True면 브라우저 숨김, False면 표시 (디버깅용)
        """
        self.engine = create_engine(db_url)
        self.headless = headless
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        log(f"WconceptImageCollector 초기화 (headless={headless})", "INFO")

    def _start_browser(self) -> None:
        """Playwright 브라우저 시작"""
        log("브라우저 시작 중...", "BROWSER")

        self.playwright = sync_playwright().start()

        # Chromium 브라우저 실행 (봇 탐지 우회 설정)
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )

        # 브라우저 컨텍스트 (실제 사용자처럼 보이게)
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            locale='ko-KR',
            timezone_id='Asia/Seoul',
        )

        # 자동화 탐지 우회 스크립트
        self.context.add_init_script("""
            // webdriver 속성 숨기기
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // plugins 속성 설정
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // languages 속성 설정
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ko-KR', 'ko', 'en-US', 'en']
            });

            // Chrome 객체 추가
            window.chrome = {
                runtime: {}
            };
        """)

        self.page = self.context.new_page()
        self.page.set_default_timeout(PAGE_TIMEOUT)

        log("브라우저 시작 완료", "BROWSER")

    def _stop_browser(self) -> None:
        """브라우저 종료"""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

        log("브라우저 종료", "BROWSER")

    def fetch_target_products(self, brand: str = None, limit: int = None) -> List[Dict]:
        """
        이미지 수집 대상 상품 조회
        - ace_product_images에 아직 이미지가 없거나 'not found'인 상품
        """
        with self.engine.connect() as conn:
            query = """
                SELECT ap.id, ap.model_no, ap.brand_name, ap.name
                FROM ace_products ap
                LEFT JOIN ace_product_images api ON ap.id = api.ace_product_id
                WHERE (api.id IS NULL OR api.source_image_url = 'not found')
                  AND ap.model_no IS NOT NULL
                  AND ap.model_no != ''
            """
            params = {}

            if brand:
                query += " AND UPPER(ap.brand_name) LIKE :brand"
                params['brand'] = f"%{brand.upper()}%"

            query += " GROUP BY ap.id ORDER BY ap.id"

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

    def search_wconcept_products(self, model_no: str) -> List[str]:
        """
        W컨셉에서 모델번호로 검색하여 상품 ID 목록 반환 (최대 2개)
        Playwright로 페이지 렌더링 완료 후 데이터 추출

        Args:
            model_no: 모델번호

        Returns:
            W컨셉 상품 ID 리스트 (최대 2개)
        """
        # 모델명 전체를 검색어로 사용
        keyword = model_no
        search_url = f"{WCONCEPT_SEARCH_URL}?keyword={keyword}&type=direct"

        log(f"  검색 중: {keyword}", "DEBUG")

        try:
            # 페이지 이동 (domcontentloaded: DOM 로드 완료 시점)
            self.page.goto(search_url, wait_until='domcontentloaded')

            # 추가 대기: 페이지가 완전히 렌더링될 시간
            self.page.wait_for_timeout(3000)

            # 검색 결과가 실제로 있는지 확인 (search-results-sortingbar가 있어야 실제 검색 결과)
            # 검색 결과가 없으면 "이 상품을 찾으셨나요?" 추천 섹션만 있고,
            # 이 섹션에도 .product-item이 있어서 구분이 필요함
            sorting_bar = self.page.query_selector('.search-results-sortingbar')
            if not sorting_bar:
                # search-results-sortingbar가 없으면 검색 결과가 없는 것
                log(f"  검색 결과 없음 (sortingbar 없음): {keyword}", "WARNING")
                return []

            # 상품 목록이 로드될 때까지 대기 (최대 10초)
            try:
                self.page.wait_for_selector('.product-item', timeout=10000)
            except:
                # 검색 결과 없음
                log(f"  검색 결과 없음: {keyword}", "WARNING")
                return []

            product_ids = []

            # 방법 1: product-item에서 이미지 URL로 상품 ID 추출
            product_items = self.page.query_selector_all('.product-item img')
            for img in product_items:
                src = img.get_attribute('src') or ''
                # productimg/image/img9/65/307698365_GG10848.jpg
                match = re.search(r'/(\d{8,})_', src)
                if match:
                    product_id = match.group(1)
                    if product_id not in product_ids:
                        product_ids.append(product_id)
                        if len(product_ids) >= 2:
                            break

            # 방법 2: 링크에서 /Product/ID 추출 (product_ids가 부족한 경우)
            if len(product_ids) < 2:
                links = self.page.query_selector_all('a[href*="/Product/"]')
                for link in links:
                    href = link.get_attribute('href') or ''
                    match = re.search(r'/Product/(\d+)', href)
                    if match:
                        product_id = match.group(1)
                        if product_id not in product_ids:
                            product_ids.append(product_id)
                            if len(product_ids) >= 2:
                                break

            if product_ids:
                log(f"  검색 결과: {len(product_ids)}개 상품 발견 - {product_ids}", "DEBUG")
            else:
                log(f"  상품 ID를 찾을 수 없음", "WARNING")

            return product_ids

        except Exception as e:
            log(f"  검색 오류: {e}", "ERROR")
            return []

    def get_product_images(self, product_id: str) -> List[str]:
        """
        W컨셉 상품 상세 페이지에서 썸네일 이미지 URL 추출
        - gallery_wrap 내 #gallery li a[data-zoom-image] 에서 추출

        Args:
            product_id: W컨셉 상품 ID

        Returns:
            이미지 URL 리스트
        """
        url = WCONCEPT_PRODUCT_URL.format(product_id=product_id)

        try:
            # 상세 페이지 이동
            self.page.goto(url, wait_until='domcontentloaded')

            # 페이지 렌더링 대기
            self.page.wait_for_timeout(3000)

            images = []

            # 썸네일 이미지 (gallery_wrap에서 data-zoom-image)
            try:
                self.page.wait_for_selector('#gallery', timeout=10000)
                gallery_items = self.page.query_selector_all('#gallery li a[data-zoom-image]')

                for item in gallery_items:
                    zoom_url = item.get_attribute('data-zoom-image')
                    if zoom_url:
                        normalized = normalize_image_url(zoom_url)
                        if normalized and normalized not in images:
                            images.append(normalized)
            except:
                log(f"  갤러리를 찾을 수 없음 (product_id={product_id})", "WARNING")

            return images[:MAX_IMAGES]

        except Exception as e:
            log(f"  상세 페이지 오류 (product_id={product_id}): {e}", "ERROR")
            return []

    def select_best_product(self, product_ids: List[str]) -> Tuple[Optional[str], List[str]]:
        """
        검색 결과에서 최적의 상품 선택
        - 첫 번째 상품의 썸네일 이미지가 5개 이상이면 첫 번째 상품 선택
        - 5개 미만이면 두 번째 상품과 비교하여 이미지가 더 많은 상품 선택

        Args:
            product_ids: 상품 ID 리스트 (최대 2개)

        Returns:
            (선택된 상품 ID, 이미지 URL 리스트)
        """
        if not product_ids:
            return None, []

        # 첫 번째 상품 이미지 수집
        first_id = product_ids[0]
        first_images = self.get_product_images(first_id)
        log(f"  [1번 상품] ID={first_id}, 이미지={len(first_images)}개", "DEBUG")

        # 첫 번째 상품 이미지가 5개 이상이면 바로 반환
        if len(first_images) >= MIN_IMAGE_COUNT:
            log(f"  -> 1번 상품 선택 (이미지 {len(first_images)}개 >= {MIN_IMAGE_COUNT}개)", "INFO")
            return first_id, first_images

        # 두 번째 상품이 없으면 첫 번째 상품 반환
        if len(product_ids) < 2:
            log(f"  -> 1번 상품 선택 (2번 상품 없음, 이미지 {len(first_images)}개)", "INFO")
            return first_id, first_images

        # 두 번째 상품 이미지 수집
        random_delay()
        second_id = product_ids[1]
        second_images = self.get_product_images(second_id)
        log(f"  [2번 상품] ID={second_id}, 이미지={len(second_images)}개", "DEBUG")

        # 이미지가 더 많은 상품 선택
        if len(second_images) > len(first_images):
            log(f"  -> 2번 상품 선택 (이미지 {len(second_images)}개 > {len(first_images)}개)", "INFO")
            return second_id, second_images
        else:
            log(f"  -> 1번 상품 선택 (이미지 {len(first_images)}개 >= {len(second_images)}개)", "INFO")
            return first_id, first_images

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
            # 1. W컨셉 검색 (상품 ID 목록 가져오기)
            product_ids = self.search_wconcept_products(model_no)
            random_delay()

            if not product_ids:
                # 검색 결과 없음 → not found
                result.status = "not_found"
                result.images = [ImageData(
                    ace_product_id=ace_product_id,
                    position=1,
                    source_image_url=NOT_FOUND_VALUE
                )]
                return result

            # 2. 최적의 상품 선택 (이미지 개수 비교)
            selected_id, image_urls = self.select_best_product(product_ids)
            random_delay()

            if not selected_id or not image_urls:
                # 이미지 추출 실패 → not found
                result.status = "not_found"
                result.images = [ImageData(
                    ace_product_id=ace_product_id,
                    position=1,
                    source_image_url=NOT_FOUND_VALUE
                )]
                return result

            result.wconcept_product_id = selected_id

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

        log("DB 저장 시작...", "DB")

        with self.engine.connect() as conn:
            for result in results:
                try:
                    # 기존 데이터 삭제 (재수집 시)
                    conn.execute(text("""
                        DELETE FROM ace_product_images
                        WHERE ace_product_id = :ace_product_id
                    """), {'ace_product_id': result.ace_product_id})

                    # 새 데이터 삽입
                    for img in result.images:
                        conn.execute(text("""
                            INSERT INTO ace_product_images (
                                ace_product_id, position, source_image_url, is_uploaded
                            ) VALUES (
                                :ace_product_id, :position, :source_image_url, :is_uploaded
                            )
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

        log(f"DB 저장 완료: {stats['total_images']}개 이미지", "DB")
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
        log("W컨셉 이미지 수집 시작 (Playwright) - v5")
        log(f"최소 이미지 기준: {MIN_IMAGE_COUNT}개")
        log("=" * 60)

        if brand:
            log(f"브랜드 필터: {brand}")
        if limit:
            log(f"최대 처리: {limit}건")
        if dry_run:
            log("*** DRY RUN 모드 - DB 저장 안함 ***", "WARNING")

        # 대상 상품 조회
        products = self.fetch_target_products(brand=brand, limit=limit)

        if not products:
            log("수집 대상 상품이 없습니다.")
            return {'total_products': 0}

        try:
            # 브라우저 시작
            self._start_browser()

            # 수집 실행
            results = []
            total = len(products)

            for idx, product in enumerate(products):
                log(f"\n[{idx+1}/{total}] model_no={product['model_no']}, brand={product['brand_name']}")

                result = self.collect_single_product(product)
                results.append(result)

                if result.status == "success":
                    log(f"  => 성공: {len(result.images)}개 이미지 (wconcept_id={result.wconcept_product_id})", "SUCCESS")
                elif result.status == "not_found":
                    log(f"  => 검색 결과 없음", "WARNING")
                else:
                    log(f"  => 오류: {result.error_message}", "ERROR")

        finally:
            # 브라우저 종료
            self._stop_browser()

        # DB 저장
        if dry_run:
            stats = {
                'total_products': len(results),
                'success': sum(1 for r in results if r.status == "success"),
                'not_found': sum(1 for r in results if r.status == "not_found"),
                'error': sum(1 for r in results if r.status == "error"),
                'total_images': sum(len(r.images) for r in results)
            }
            log("\n[DB 저장 생략 - DRY RUN]", "WARNING")
        else:
            stats = self.batch_insert(results)

        # 결과 출력
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
        description='W컨셉에서 이미지 URL 수집하여 ace_product_images 저장 (Playwright) - v5'
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
    parser.add_argument(
        '--headless',
        type=str,
        default='true',
        help='브라우저 숨김 여부 (true/false)'
    )

    args = parser.parse_args()
    headless = args.headless.lower() != 'false'

    try:
        collector = WconceptImageCollector(DB_URL, headless=headless)
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
