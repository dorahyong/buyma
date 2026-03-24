"""
trenbe 글로벌에서 이미지 URL 수집하여 ace_product_images 테이블에 저장하는 스크립트
Playwright 기반 - 멀티프로세싱 병렬 처리 버전

수정 로직 (parallel - multiprocessing):
- 멀티프로세싱으로 진정한 병렬 처리 (greenlet 충돌 없음)
- 각 프로세스가 독립적인 브라우저 사용
- 검색: Playwright로 trenbe 검색 페이지 (sort_by=rating)
- 이미지: Shopify JSON API (/products/{handle}.json)
- 서비스 배너, 사이즈표, 중복 이미지 자동 필터링

사용법:
    python image_collector_parallel_trenbe.py                      # 전체 실행
    python image_collector_parallel_trenbe.py --brand="NIKE"       # 특정 브랜드만
    python image_collector_parallel_trenbe.py --model-no="ABC123"  # 특정 모델번호만
    python image_collector_parallel_trenbe.py --limit=10           # 최대 10개 상품만
    python image_collector_parallel_trenbe.py --dry-run            # 테스트 (DB 저장 안함)
    python image_collector_parallel_trenbe.py --headless=false     # 브라우저 표시 (디버깅용)
    python image_collector_parallel_trenbe.py --workers=4          # 동시 처리 워커 수 (기본 4)

설치:
    pip install playwright sqlalchemy pymysql
    playwright install chromium

작성일: 2026-03-12
"""

import argparse
import json
import re
import time
import random
import sys
import io
import multiprocessing as mp
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import os
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# 표준 출력 인코딩 설정 (윈도우 환경 대응 - 일본어 출력용)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# =====================================================
# 설정
# =====================================================

DB_URL = os.getenv('DATABASE_URL', f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4")

# trenbe 글로벌 URL
TRENBE_SEARCH_URL = "https://global.trenbe.com/search"
TRENBE_PRODUCT_JSON_URL = "https://global.trenbe.com/products/{handle}.json"

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

# 워커별 중간 저장 단위
BATCH_SAVE_SIZE = 10


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
    trenbe_product_handle: Optional[str] = None
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
    print(f"[{timestamp}] [{level}] {worker_tag} {message}", flush=True)


def random_delay() -> None:
    """랜덤 딜레이"""
    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
    time.sleep(delay)


def normalize_image_url(url: str) -> str:
    """이미지 URL 정규화 - 쿼리스트링 제거"""
    if not url:
        return ""
    if url.startswith("//"):
        url = f"https:{url}"
    if "?" in url:
        url = url.split("?")[0]
    return url


def is_product_image(url: str) -> bool:
    """제품 이미지인지 판별 (서비스 배너, 사이즈표 등 제외)

    trenbe 제품 이미지 패턴: {13자리타임스탬프}_{32자리해시}_{순번}.jpg
    이 패턴이 아닌 이미지는 사이즈표/서비스 배너 등으로 판단하여 제외.
    """
    filename = url.split("/")[-1].lower()
    # 서비스 배너
    if "global_uv_service_info" in filename:
        return False
    # 사이즈표 (파일명에 size 포함)
    if "_size_" in filename or "_size." in filename:
        return False
    # 서비스/인포 이미지
    if "service_info" in filename:
        return False
    # trenbe 제품 이미지 패턴: {13자리숫자}_{32자리hex}_{순번}
    # 이 패턴이 아닌 짧은 파일명은 사이즈표 등 비제품 이미지
    if not re.match(r'^\d{13}_[0-9a-f]{32}_\d', filename):
        return False
    return True


def deduplicate_images(urls: List[str]) -> List[str]:
    """중복 이미지 제거 (UUID 변형만 다른 것)
    예: ..._0.jpg 와 ..._0_4a399558-3f8d-4811-a111-db3e99716ba8.jpg 은 중복
    """
    result = []
    seen_bases = set()
    for url in urls:
        filename = url.split("/")[-1]
        # 확장자 분리
        name, ext = os.path.splitext(filename)
        # UUID 패턴 제거하여 base 추출 (_{hex8-hex4-hex4-hex4-hex12} 패턴)
        base = re.sub(r'_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', '', name)
        if base not in seen_bases:
            seen_bases.add(base)
            result.append(url)
    return result


# =====================================================
# 워커 함수 (별도 프로세스에서 실행)
# =====================================================

def worker_process(worker_id: int, products: List[Dict], total: int, headless: bool, dry_run: bool, result_queue: mp.Queue):
    """
    워커 프로세스 - 독립적인 브라우저로 상품 수집
    50개마다 중간 저장
    """
    from sqlalchemy import create_engine, text
    
    # 워커별 DB 연결 (각 프로세스가 독립적으로 연결)
    engine = create_engine(DB_URL)
    
    # 통계
    stats = {
        'success': 0,
        'not_found': 0,
        'error': 0,
        'total_images': 0
    }
    
    # 중간 저장용 버퍼
    buffer = []

    def save_buffer():
        """버퍼에 쌓인 결과를 DB에 저장"""
        nonlocal buffer
        if not buffer:
            return
        
        # dry_run이면 저장 안하고 통계만 업데이트
        if dry_run:
            for result in buffer:
                if result.status == "success":
                    stats['success'] += 1
                elif result.status == "not_found":
                    stats['not_found'] += 1
                else:
                    stats['error'] += 1
                stats['total_images'] += len(result.images)
            log(f"[DRY-RUN] 저장 스킵: {len(buffer)}개 상품", "DB", worker_id)
            buffer = []
            return
        
        try:
            with engine.connect() as conn:
                for result in buffer:
                    # 기존 이미지 삭제
                    conn.execute(text("""
                        DELETE FROM ace_product_images
                        WHERE ace_product_id = :ace_product_id
                    """), {'ace_product_id': result.ace_product_id})

                    # 새 이미지 저장
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

                    # 통계 업데이트
                    if result.status == "success":
                        stats['success'] += 1
                    elif result.status == "not_found":
                        stats['not_found'] += 1
                    else:
                        stats['error'] += 1

                conn.commit()
            
            log(f"중간 저장 완료: {len(buffer)}개 상품", "DB", worker_id)
            buffer = []
            
        except Exception as e:
            log(f"중간 저장 실패: {e}", "ERROR", worker_id)

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
            for i, product in enumerate(products):
                idx = product.get('_idx', 0)
                result = collect_single_product(page, product, worker_id, idx, total)
                buffer.append(result)
                
                # 50개마다 중간 저장
                if len(buffer) >= BATCH_SAVE_SIZE:
                    save_buffer()

            # 남은 버퍼 저장
            save_buffer()

            # 브라우저 종료
            context.close()
            browser.close()

    except Exception as e:
        log(f"워커 오류: {e}", "ERROR", worker_id)
        # 오류 발생해도 버퍼에 있는 것은 저장 시도
        save_buffer()

    # 통계만 전송 (결과는 이미 DB에 저장됨)
    result_queue.put(stats)


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
        # 1. trenbe 검색
        product_handles = search_trenbe_products(page, model_no, worker_id)
        random_delay()

        if not product_handles:
            result.status = "not_found"
            result.images = [ImageData(
                ace_product_id=ace_product_id,
                position=1,
                source_image_url=NOT_FOUND_VALUE
            )]
            log(f"=> 검색 결과 없음", "WARNING", worker_id)
            return result

        # 2. 최적의 상품 선택
        selected_handle, image_urls = select_best_product(page, product_handles, worker_id)
        random_delay()

        if not selected_handle or not image_urls:
            result.status = "not_found"
            result.images = [ImageData(
                ace_product_id=ace_product_id,
                position=1,
                source_image_url=NOT_FOUND_VALUE
            )]
            log(f"=> 이미지 없음", "WARNING", worker_id)
            return result

        result.trenbe_product_handle = selected_handle

        # 3. 이미지 데이터 구성
        for img_idx, url in enumerate(image_urls):
            result.images.append(ImageData(
                ace_product_id=ace_product_id,
                position=img_idx + 1,
                source_image_url=url
            ))

        result.status = "success"
        log(f"=> 성공: {len(result.images)}개 이미지 (handle={selected_handle})", "SUCCESS", worker_id)
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


def search_trenbe_products(page: Page, model_no: str, worker_id: int) -> List[str]:
    """trenbe 글로벌 검색 - 상품 handle 목록 반환 (sort_by=rating)

    Note: 검색 결과가 없을 때 추천 상품이 표시될 수 있음.
    model_no가 handle에 포함되는지 검증하여 추천 상품을 제외함.
    """
    keyword = model_no
    search_url = f"{TRENBE_SEARCH_URL}?q={keyword}&sort_by=rating&type=product"

    # model_no를 handle 비교용으로 변환 (소문자, 공백→하이픈)
    model_no_normalized = model_no.lower().replace(' ', '-')

    try:
        page.goto(search_url, wait_until='domcontentloaded')
        page.wait_for_timeout(PAGE_RENDER_WAIT)

        # JS 렌더링 대기 - 상품 링크가 나타날 때까지
        try:
            page.wait_for_selector('a[href*="/products/"]', timeout=10000)
        except:
            return []

        product_handles = []

        # /products/ 링크에서 handle 추출
        links = page.query_selector_all('a[href*="/products/"]')
        for link in links:
            href = link.get_attribute('href') or ''
            match = re.search(r'/products/([^?/]+)', href)
            if match:
                handle = match.group(1)
                # model_no가 handle에 포함되는지 검증 (추천 상품 제외)
                if model_no_normalized not in handle.lower():
                    continue
                if handle not in product_handles:
                    product_handles.append(handle)
                    if len(product_handles) >= 2:
                        break

        return product_handles

    except Exception as e:
        log(f"검색 오류 ({model_no}): {e}", "ERROR", worker_id)
        return []


def get_product_images(page: Page, handle: str, worker_id: int) -> List[str]:
    """trenbe 상품 이미지 추출 (JSON API)"""
    url = TRENBE_PRODUCT_JSON_URL.format(handle=handle)

    try:
        response = page.goto(url, wait_until='domcontentloaded')
        page.wait_for_timeout(500)

        # JSON 파싱
        body_text = page.inner_text('body')
        data = json.loads(body_text)

        product = data.get('product', {})
        raw_images = product.get('images', [])

        images = []
        for img in raw_images:
            src = img.get('src', '')
            if not src:
                continue
            normalized = normalize_image_url(src)
            if normalized and is_product_image(normalized) and normalized not in images:
                images.append(normalized)

        # 중복 제거 (UUID 변형)
        images = deduplicate_images(images)

        return images[:MAX_IMAGES]

    except Exception as e:
        log(f"상세 페이지 오류 ({handle}): {e}", "ERROR", worker_id)
        return []


def select_best_product(page: Page, product_handles: List[str], worker_id: int) -> Tuple[Optional[str], List[str]]:
    """최적의 상품 선택"""
    if not product_handles:
        return None, []

    first_handle = product_handles[0]
    first_images = get_product_images(page, first_handle, worker_id)

    if len(first_images) >= MIN_IMAGE_COUNT:
        return first_handle, first_images

    if len(product_handles) < 2:
        return first_handle, first_images

    random_delay()
    second_handle = product_handles[1]
    second_images = get_product_images(page, second_handle, worker_id)

    if len(second_images) > len(first_images):
        return second_handle, second_images
    else:
        return first_handle, first_images


# =====================================================
# 메인 수집기 클래스
# =====================================================

class TrenbeImageCollectorParallel:
    """trenbe 글로벌 이미지 수집기 - 멀티프로세싱"""

    def __init__(self, db_url: str, headless: bool = True, num_workers: int = DEFAULT_WORKERS):
        self.engine = create_engine(db_url)
        self.headless = headless
        self.num_workers = num_workers

        log(f"TrenbeImageCollectorParallel 초기화 (headless={headless}, workers={num_workers})", "INFO")

    def fetch_target_products(self, brand: str = None, model_no: str = None, limit: int = None, price_checked_only: bool = False) -> List[Dict]:
        """대상 상품 조회"""
        with self.engine.connect() as conn:
            query = """
                SELECT ap.id, ap.model_no, ap.brand_name, ap.name
                FROM ace_products ap
                LEFT JOIN ace_product_images api ON ap.id = api.ace_product_id
                LEFT JOIN mall_sites ms ON ap.source_site = ms.site_name
                WHERE (api.id IS NULL OR api.source_image_url = 'not found')
                  AND ap.model_no IS NOT NULL
                  AND ap.model_no != ''
                  AND COALESCE(ms.has_own_images, 0) = 0
            """
            params = {}

            if price_checked_only:
                query += " AND ap.buyma_lowest_price_checked_at IS NOT NULL"

            if brand:
                query += " AND UPPER(ap.brand_name) LIKE :brand"
                params['brand'] = f"%{brand.upper()}%"

            if model_no:
                query += " AND UPPER(ap.model_no) LIKE :model_no"
                params['model_no'] = f"%{model_no.upper()}%"

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

    def run(self, brand: str = None, model_no: str = None, limit: int = None, dry_run: bool = False, price_checked_only: bool = False) -> Dict:
        """전체 실행"""
        log("=" * 60)
        log("trenbe 글로벌 이미지 수집 시작 (멀티프로세싱)")
        log(f"동시 처리 워커 수: {self.num_workers}")
        log(f"딜레이: {REQUEST_DELAY_MIN}~{REQUEST_DELAY_MAX}초")
        log("=" * 60)

        if brand:
            log(f"브랜드 필터: {brand}")
        if model_no:
            log(f"모델번호 필터: {model_no}")
        if limit:
            log(f"최대 처리: {limit}건")
        if dry_run:
            log("*** DRY RUN 모드 - DB 저장 안함 ***", "WARNING")

        # 대상 상품 조회
        products = self.fetch_target_products(brand=brand, model_no=model_no, limit=limit, price_checked_only=price_checked_only)

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
                    args=(worker_id, chunk, total, self.headless, dry_run, result_queue)
                )
                p.start()
                processes.append(p)

        # 결과 수집 (각 워커의 통계)
        total_stats = {
            'total_products': total,
            'success': 0,
            'not_found': 0,
            'error': 0,
            'total_images': 0
        }
        
        for _ in processes:
            worker_stats = result_queue.get()
            total_stats['success'] += worker_stats['success']
            total_stats['not_found'] += worker_stats['not_found']
            total_stats['error'] += worker_stats['error']
            total_stats['total_images'] += worker_stats['total_images']

        # 프로세스 종료 대기
        for p in processes:
            p.join()

        elapsed = time.time() - start_time
        log(f"\n수집 완료: {total}개 상품, 소요시간: {elapsed:.1f}초")

        # dry_run일 때는 이미 저장 안 했으므로 그대로
        if dry_run:
            log("\n[DRY RUN 모드에서는 중간 저장이 비활성화됩니다]", "WARNING")
        
        stats = total_stats

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
        description='trenbe 글로벌 이미지 URL 수집 - 멀티프로세싱 병렬 처리'
    )
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만 처리')
    parser.add_argument('--model-no', type=str, default=None, help='특정 모델번호만 처리')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    parser.add_argument('--dry-run', action='store_true', help='테스트 모드 (DB 저장 안함)')
    parser.add_argument('--headless', type=str, default='true', help='브라우저 숨김 여부')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS, help=f'동시 처리 워커 수 (기본 {DEFAULT_WORKERS})')
    parser.add_argument('--price-checked-only', action='store_true', help='최저가 확인된 상품만 이미지 수집')

    args = parser.parse_args()
    headless = args.headless.lower() != 'false'

    try:
        collector = TrenbeImageCollectorParallel(
            DB_URL,
            headless=headless,
            num_workers=args.workers
        )
        stats = collector.run(
            brand=args.brand,
            model_no=args.model_no,
            limit=args.limit,
            dry_run=args.dry_run,
            price_checked_only=args.price_checked_only
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
