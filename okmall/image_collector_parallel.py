"""
W컨셉에서 이미지 URL 수집하여 ace_product_images 테이블에 저장하는 스크립트
Playwright 기반 - 멀티프로세싱 병렬 처리 버전

수정 로직 (parallel - multiprocessing):
- 멀티프로세싱으로 진정한 병렬 처리 (greenlet 충돌 없음)
- 각 프로세스가 독립적인 브라우저 사용
- 딜레이 최적화 (1~2초)
- 검색 결과가 없으면 추천 상품 수집하지 않음

사용법:
    python image_collector_parallel.py                      # 전체 실행
    python image_collector_parallel.py --brand="NIKE"       # 특정 브랜드만
    python image_collector_parallel.py --limit=10           # 최대 10개 상품만
    python image_collector_parallel.py --dry-run            # 테스트 (DB 저장 안함)
    python image_collector_parallel.py --headless=false     # 브라우저 표시 (디버깅용)
    python image_collector_parallel.py --workers=4          # 동시 처리 워커 수 (기본 4)

설치:
    pip install playwright sqlalchemy pymysql
    playwright install chromium

작성일: 2026-01-19
"""

import argparse
import re
import time
import random
import multiprocessing as mp
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

# 요청 간 딜레이 (초) - 최적화됨
REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.0

# 페이지 로딩 타임아웃 (밀리초)
PAGE_TIMEOUT = 30000

# 페이지 렌더링 대기 (밀리초)
PAGE_RENDER_WAIT = 2000

# 최대 이미지 수 (바이마 제한: 20장)
MAX_IMAGES = 20

# 최소 이미지 개수 기준
MIN_IMAGE_COUNT = 5

# NOT FOUND 표시
NOT_FOUND_VALUE = "not found"

# 동시 처리 워커 수 (기본값)
DEFAULT_WORKERS = 4


# =====================================================
# 데이터 클래스 (pickle 호환을 위해 모듈 레벨에 정의)
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
    status: str = "pending"
    error_message: Optional[str] = None


# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO", worker_id: int = None) -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    worker_tag = f"[W{worker_id}]" if worker_id is not None else ""
    print(f"[{timestamp}] [{level}] {worker_tag} {message}")


def random_delay() -> None:
    """랜덤 딜레이"""
    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
    time.sleep(delay)


def normalize_image_url(url: str) -> str:
    """이미지 URL 정규화"""
    if not url:
        return ""
    if url.startswith("//"):
        url = f"https:{url}"
    if "?thumbnail" in url:
        url = url.split("?")[0]
    return url


# =====================================================
# 워커 함수 (별도 프로세스에서 실행)
# =====================================================

def worker_process(worker_id: int, products: List[Dict], total: int, headless: bool, result_queue: mp.Queue):
    """
    워커 프로세스 - 독립적인 브라우저로 상품 수집
    """
    results = []

    try:
        with sync_playwright() as playwright:
            # 브라우저 시작
            browser = playwright.chromium.launch(
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                ]
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
                locale='ko-KR',
                timezone_id='Asia/Seoul',
            )

            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
                window.chrome = { runtime: {} };
            """)

            page = context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT)

            log(f"브라우저 시작 완료, {len(products)}개 상품 처리 예정", "BROWSER", worker_id)

            # 상품 처리
            for product in products:
                idx = product.get('_idx', 0)
                result = collect_single_product(page, product, worker_id, idx, total)
                results.append(result)

            # 브라우저 종료
            context.close()
            browser.close()

    except Exception as e:
        log(f"워커 오류: {e}", "ERROR", worker_id)

    # 결과 전송
    result_queue.put(results)


def collect_single_product(page: Page, product: Dict, worker_id: int, idx: int, total: int) -> ProductImageResult:
    """단일 상품 수집"""
    ace_product_id = product['id']
    model_no = product['model_no']

    log(f"[{idx+1}/{total}] model_no={model_no}, brand={product['brand_name']}", "DEBUG", worker_id)

    result = ProductImageResult(
        ace_product_id=ace_product_id,
        model_no=model_no
    )

    try:
        # 1. W컨셉 검색
        product_ids = search_wconcept_products(page, model_no, worker_id)
        random_delay()

        if not product_ids:
            result.status = "not_found"
            result.images = [ImageData(
                ace_product_id=ace_product_id,
                position=1,
                source_image_url=NOT_FOUND_VALUE
            )]
            log(f"=> 검색 결과 없음", "WARNING", worker_id)
            return result

        # 2. 최적의 상품 선택
        selected_id, image_urls = select_best_product(page, product_ids, worker_id)
        random_delay()

        if not selected_id or not image_urls:
            result.status = "not_found"
            result.images = [ImageData(
                ace_product_id=ace_product_id,
                position=1,
                source_image_url=NOT_FOUND_VALUE
            )]
            log(f"=> 이미지 없음", "WARNING", worker_id)
            return result

        result.wconcept_product_id = selected_id

        # 3. 이미지 데이터 구성
        for img_idx, url in enumerate(image_urls):
            result.images.append(ImageData(
                ace_product_id=ace_product_id,
                position=img_idx + 1,
                source_image_url=url
            ))

        result.status = "success"
        log(f"=> 성공: {len(result.images)}개 이미지", "SUCCESS", worker_id)
        return result

    except Exception as e:
        result.status = "error"
        result.error_message = str(e)
        result.images = [ImageData(
            ace_product_id=ace_product_id,
            position=1,
            source_image_url=NOT_FOUND_VALUE
        )]
        log(f"=> 오류: {e}", "ERROR", worker_id)
        return result


def search_wconcept_products(page: Page, model_no: str, worker_id: int) -> List[str]:
    """W컨셉 검색"""
    keyword = model_no
    search_url = f"{WCONCEPT_SEARCH_URL}?keyword={keyword}&type=direct"

    try:
        page.goto(search_url, wait_until='domcontentloaded')
        page.wait_for_timeout(PAGE_RENDER_WAIT)

        # 검색 결과 확인
        sorting_bar = page.query_selector('.search-results-sortingbar')
        if not sorting_bar:
            return []

        try:
            page.wait_for_selector('.product-item', timeout=10000)
        except:
            return []

        product_ids = []

        # 이미지에서 상품 ID 추출
        product_items = page.query_selector_all('.product-item img')
        for img in product_items:
            src = img.get_attribute('src') or ''
            match = re.search(r'/(\d{8,})_', src)
            if match:
                product_id = match.group(1)
                if product_id not in product_ids:
                    product_ids.append(product_id)
                    if len(product_ids) >= 2:
                        break

        # 링크에서 상품 ID 추출
        if len(product_ids) < 2:
            links = page.query_selector_all('a[href*="/Product/"]')
            for link in links:
                href = link.get_attribute('href') or ''
                match = re.search(r'/Product/(\d+)', href)
                if match:
                    product_id = match.group(1)
                    if product_id not in product_ids:
                        product_ids.append(product_id)
                        if len(product_ids) >= 2:
                            break

        return product_ids

    except Exception as e:
        log(f"검색 오류 ({model_no}): {e}", "ERROR", worker_id)
        return []


def get_product_images(page: Page, product_id: str, worker_id: int) -> List[str]:
    """상품 이미지 추출"""
    url = WCONCEPT_PRODUCT_URL.format(product_id=product_id)

    try:
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_timeout(PAGE_RENDER_WAIT)

        images = []

        try:
            page.wait_for_selector('#gallery', timeout=10000)
            gallery_items = page.query_selector_all('#gallery li a[data-zoom-image]')

            for item in gallery_items:
                zoom_url = item.get_attribute('data-zoom-image')
                if zoom_url:
                    normalized = normalize_image_url(zoom_url)
                    if normalized and normalized not in images:
                        images.append(normalized)
        except:
            pass

        return images[:MAX_IMAGES]

    except Exception as e:
        log(f"상세 페이지 오류 ({product_id}): {e}", "ERROR", worker_id)
        return []


def select_best_product(page: Page, product_ids: List[str], worker_id: int) -> Tuple[Optional[str], List[str]]:
    """최적의 상품 선택"""
    if not product_ids:
        return None, []

    first_id = product_ids[0]
    first_images = get_product_images(page, first_id, worker_id)

    if len(first_images) >= MIN_IMAGE_COUNT:
        return first_id, first_images

    if len(product_ids) < 2:
        return first_id, first_images

    random_delay()
    second_id = product_ids[1]
    second_images = get_product_images(page, second_id, worker_id)

    if len(second_images) > len(first_images):
        return second_id, second_images
    else:
        return first_id, first_images


# =====================================================
# 메인 수집기 클래스
# =====================================================

class WconceptImageCollectorParallel:
    """W컨셉 이미지 수집기 - 멀티프로세싱"""

    def __init__(self, db_url: str, headless: bool = True, num_workers: int = DEFAULT_WORKERS):
        self.engine = create_engine(db_url)
        self.headless = headless
        self.num_workers = num_workers

        log(f"WconceptImageCollectorParallel 초기화 (headless={headless}, workers={num_workers})", "INFO")

    def fetch_target_products(self, brand: str = None, limit: int = None) -> List[Dict]:
        """대상 상품 조회"""
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

    def batch_insert(self, results: List[ProductImageResult]) -> Dict:
        """DB 일괄 저장"""
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
                    conn.execute(text("""
                        DELETE FROM ace_product_images
                        WHERE ace_product_id = :ace_product_id
                    """), {'ace_product_id': result.ace_product_id})

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
        """전체 실행"""
        log("=" * 60)
        log("W컨셉 이미지 수집 시작 (멀티프로세싱)")
        log(f"동시 처리 워커 수: {self.num_workers}")
        log(f"딜레이: {REQUEST_DELAY_MIN}~{REQUEST_DELAY_MAX}초")
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

        total = len(products)

        # 인덱스 추가
        for idx, product in enumerate(products):
            product['_idx'] = idx

        # 상품을 워커에 분배
        chunks = [[] for _ in range(self.num_workers)]
        for idx, product in enumerate(products):
            chunks[idx % self.num_workers].append(product)

        log(f"상품 분배: {[len(c) for c in chunks]}")

        start_time = time.time()

        # 결과 큐
        result_queue = mp.Queue()

        # 워커 프로세스 시작
        processes = []
        for worker_id, chunk in enumerate(chunks):
            if chunk:
                p = mp.Process(
                    target=worker_process,
                    args=(worker_id, chunk, total, self.headless, result_queue)
                )
                p.start()
                processes.append(p)

        # 결과 수집
        all_results = []
        for _ in processes:
            results = result_queue.get()
            all_results.extend(results)

        # 프로세스 종료 대기
        for p in processes:
            p.join()

        elapsed = time.time() - start_time
        log(f"\n수집 완료: {len(all_results)}개 상품, 소요시간: {elapsed:.1f}초")

        # DB 저장
        if dry_run:
            stats = {
                'total_products': len(all_results),
                'success': sum(1 for r in all_results if r.status == "success"),
                'not_found': sum(1 for r in all_results if r.status == "not_found"),
                'error': sum(1 for r in all_results if r.status == "error"),
                'total_images': sum(len(r.images) for r in all_results)
            }
            log("\n[DB 저장 생략 - DRY RUN]", "WARNING")
        else:
            stats = self.batch_insert(all_results)

        # 결과 출력
        log("\n" + "=" * 60)
        log("수집 완료!")
        log(f"  총 상품: {stats['total_products']}건")
        log(f"  성공: {stats['success']}건")
        log(f"  검색 실패: {stats['not_found']}건")
        log(f"  오류: {stats['error']}건")
        log(f"  총 이미지: {stats['total_images']}개")
        if elapsed > 0:
            log(f"  소요시간: {elapsed:.1f}초 ({stats['total_products']/elapsed:.2f} 상품/초)")
        log("=" * 60)

        return stats


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(
        description='W컨셉 이미지 URL 수집 - 멀티프로세싱 병렬 처리'
    )
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    parser.add_argument('--dry-run', action='store_true', help='테스트 모드 (DB 저장 안함)')
    parser.add_argument('--headless', type=str, default='true', help='브라우저 숨김 여부')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS, help=f'동시 처리 워커 수 (기본 {DEFAULT_WORKERS})')

    args = parser.parse_args()
    headless = args.headless.lower() != 'false'

    try:
        collector = WconceptImageCollectorParallel(
            DB_URL,
            headless=headless,
            num_workers=args.workers
        )
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
