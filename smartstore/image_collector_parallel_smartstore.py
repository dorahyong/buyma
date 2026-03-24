"""
네이버 스마트스토어(adorelux)에서 이미지 URL 수집하여 ace_product_images 테이블에 저장하는 스크립트
Playwright 기반 - 기존 Chrome에 CDP 연결 (봇 차단 완전 우회)

수집 로직:
- Chrome을 디버그 모드로 실행 → Playwright가 CDP로 연결
- 자동화 플래그 없음 → 네이버가 일반 사용자로 인식
- 검색: smartstore.naver.com/adorelux/search?q={model_no}
- 이미지: 상품 상세 페이지 HTML에서 representativeImageUrl + optionalImageUrls 추출

사용법:
    # 1단계: Chrome 디버그 모드 실행 (최초 1회)
    python image_collector_parallel_smartstore.py --start-chrome

    # 2단계: 이미지 수집
    python image_collector_parallel_smartstore.py --model-no="ABC123" --dry-run
    python image_collector_parallel_smartstore.py --brand="NIKE" --limit=10
    python image_collector_parallel_smartstore.py --price-checked-only

설치:
    pip install playwright sqlalchemy pymysql
    playwright install chromium

작성일: 2026-03-18
"""

import argparse
import json
import re
import time
import random
import sys
import io
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import os
from playwright.sync_api import sync_playwright, Page
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

# Chrome 디버그 모드 설정
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_DEBUG_PORT = 9222
CHROME_DEBUG_URL = f"http://localhost:{CHROME_DEBUG_PORT}"
CHROME_PROFILE_DIR = os.path.join(os.path.dirname(__file__), 'smartstore_profile')

# 스마트스토어 URL
SMARTSTORE_SEARCH_URL = "https://smartstore.naver.com/adorelux/search"
SMARTSTORE_PRODUCT_URL = "https://smartstore.naver.com/adorelux/products/{product_no}"

# 요청 간 딜레이 (초)
REQUEST_DELAY_MIN = 1.5
REQUEST_DELAY_MAX = 3.0

# 페이지 로딩 타임아웃 (밀리초)
PAGE_TIMEOUT = 30000

# 페이지 렌더링 대기 (밀리초)
PAGE_RENDER_WAIT = 3000

# 최대 이미지 수 (바이마 제한: 20장)
MAX_IMAGES = 20

# 최소 이미지 개수 기준
MIN_IMAGE_COUNT = 5

# NOT FOUND 표시
NOT_FOUND_VALUE = "not found"

# 중간 저장 단위
BATCH_SAVE_SIZE = 10


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
    smartstore_product_no: Optional[str] = None
    images: List[ImageData] = field(default_factory=list)
    status: str = "pending"
    error_message: Optional[str] = None


# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def random_delay() -> None:
    """랜덤 딜레이"""
    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
    time.sleep(delay)


def normalize_image_url(url: str) -> str:
    """이미지 URL 정규화 - 쿼리스트링 제거, 유니코드 이스케이프 처리"""
    if not url:
        return ""
    url = url.replace("\\u002F", "/")
    if url.startswith("//"):
        url = f"https:{url}"
    if "?" in url:
        url = url.split("?")[0]
    return url


def start_chrome_debug():
    """Chrome을 디버그 모드로 실행"""
    log("Chrome을 디버그 모드로 실행합니다...", "BROWSER")
    log(f"  포트: {CHROME_DEBUG_PORT}")
    log(f"  프로필: {CHROME_PROFILE_DIR}")

    cmd = [
        CHROME_PATH,
        f'--remote-debugging-port={CHROME_DEBUG_PORT}',
        f'--user-data-dir={CHROME_PROFILE_DIR}',
        'https://smartstore.naver.com/adorelux',
    ]

    subprocess.Popen(cmd)
    log("Chrome이 실행되었습니다.", "SUCCESS")
    log("")
    log("브라우저에서 네이버 로그인 후, 수집 명령을 실행하세요:")
    log("  python image_collector_parallel_smartstore.py --model-no=XXX --dry-run")
    log("")
    log(f"※ Chrome을 닫으면 디버그 모드가 종료됩니다.")
    log(f"※ 다시 실행: python image_collector_parallel_smartstore.py --start-chrome")


def is_chrome_debug_running() -> bool:
    """Chrome 디버그 모드 실행 여부 확인"""
    try:
        import urllib.request
        urllib.request.urlopen(f'{CHROME_DEBUG_URL}/json/version', timeout=2)
        return True
    except:
        return False


# =====================================================
# 수집 함수
# =====================================================

def collect_single_product(page: Page, product: Dict, idx: int, total: int) -> ProductImageResult:
    """단일 상품 수집"""
    ace_product_id = product['id']
    model_no = product['model_no']

    log(f"[{idx+1}/{total}] model_no={model_no}, brand={product['brand_name']}", "DEBUG")

    result = ProductImageResult(
        ace_product_id=ace_product_id,
        model_no=model_no
    )

    try:
        # 1. 스마트스토어 검색
        product_nos = search_smartstore_products(page, model_no)
        random_delay()

        if not product_nos:
            result.status = "not_found"
            result.images = [ImageData(
                ace_product_id=ace_product_id,
                position=1,
                source_image_url=NOT_FOUND_VALUE
            )]
            log(f"=> 검색 결과 없음", "WARNING")
            return result

        # 2. 최적의 상품 선택
        selected_no, image_urls = select_best_product(page, product_nos)
        random_delay()

        if not selected_no or not image_urls:
            result.status = "not_found"
            result.images = [ImageData(
                ace_product_id=ace_product_id,
                position=1,
                source_image_url=NOT_FOUND_VALUE
            )]
            log(f"=> 이미지 없음", "WARNING")
            return result

        result.smartstore_product_no = selected_no

        # 3. 이미지 데이터 구성
        for img_idx, url in enumerate(image_urls):
            result.images.append(ImageData(
                ace_product_id=ace_product_id,
                position=img_idx + 1,
                source_image_url=url
            ))

        result.status = "success"
        log(f"=> 성공: {len(result.images)}개 이미지 (productNo={selected_no})", "SUCCESS")
        return result

    except Exception as e:
        result.status = "error"
        result.error_message = str(e)
        result.images = [ImageData(
            ace_product_id=ace_product_id,
            position=1,
            source_image_url=NOT_FOUND_VALUE
        )]
        log(f"=> 오류: {e}", "ERROR")
        return result


def search_smartstore_products(page: Page, model_no: str) -> List[str]:
    """스마트스토어 검색 - productNo 목록 반환"""
    keyword = model_no
    search_url = f"{SMARTSTORE_SEARCH_URL}?q={keyword}"

    try:
        page.goto(search_url, wait_until='domcontentloaded')
        page.wait_for_timeout(PAGE_RENDER_WAIT)

        # 캡차 감지
        if _detect_captcha(page):
            log("캡차 감지! 브라우저에서 수동으로 해결해주세요 (3분 대기)...", "WARNING")
            try:
                page.wait_for_selector('a[href*="/products/"]', timeout=180000)
                log("캡차 해결 확인!", "SUCCESS")
                page.wait_for_timeout(2000)
            except:
                log("캡차 미해결 - 검색 결과 없음으로 처리", "WARNING")
                return []

        # 검색 결과 없음 감지
        no_result = page.query_selector('#content')
        if no_result:
            content_text = no_result.inner_text()
            if '검색 결과가 없습니다' in content_text:
                return []

        # 검색 결과 영역(#content) 내 상품 링크만 추출
        try:
            page.wait_for_selector('#content a[href*="/products/"]', timeout=10000)
        except:
            return []

        product_nos = []

        links = page.query_selector_all('#content a[href*="/products/"]')
        for link in links:
            href = link.get_attribute('href') or ''
            match = re.search(r'/products/(\d+)', href)
            if match:
                product_no = match.group(1)
                if product_no not in product_nos:
                    product_nos.append(product_no)
                    if len(product_nos) >= 2:
                        break

        return product_nos

    except Exception as e:
        log(f"검색 오류 ({model_no}): {e}", "ERROR")
        return []


def get_product_images(page: Page, product_no: str) -> List[str]:
    """스마트스토어 상품 이미지 추출

    3단계 fallback:
    1. window.__PRELOADED_STATE__ JS 객체에서 추출
    2. HTML 소스에서 정규식으로 추출
    3. DOM img 태그에서 추출
    """
    url = SMARTSTORE_PRODUCT_URL.format(product_no=product_no)

    try:
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_timeout(PAGE_RENDER_WAIT)

        # 캡차 감지
        if _detect_captcha(page):
            log("캡차 감지! 브라우저에서 수동으로 해결해주세요 (3분 대기)...", "WARNING")
            try:
                page.wait_for_function(
                    '() => !document.querySelector("img[alt*=\\"캡차\\"]") && !document.body.innerText.includes("보안 확인")',
                    timeout=180000
                )
                log("캡차 해결 확인!", "SUCCESS")
                page.wait_for_timeout(PAGE_RENDER_WAIT)
            except:
                log("캡차 미해결 - 이미지 추출 실패로 처리", "WARNING")
                return []

        # --- 방법 1: __PRELOADED_STATE__ JS 객체 ---
        image_data = page.evaluate("""
            () => {
                const state = window.__PRELOADED_STATE__;
                if (!state) return null;

                const product = state.product && state.product.A;
                if (!product) return null;

                return {
                    representativeImageUrl: product.representativeImageUrl || null,
                    optionalImageUrls: product.optionalImageUrls || []
                };
            }
        """)

        if image_data and image_data.get('representativeImageUrl'):
            return _parse_image_data(image_data)

        # --- 방법 2: HTML 소스에서 정규식 추출 ---
        html = page.content()
        images = _extract_images_from_html(html)
        if images:
            return images

        # --- 방법 3: DOM img 태그에서 추출 ---
        images = _extract_images_from_dom(page)
        if images:
            return images

        return []

    except Exception as e:
        log(f"상세 페이지 오류 ({product_no}): {e}", "ERROR")
        return []


def _detect_captcha(page: Page) -> bool:
    """캡차 페이지 감지"""
    try:
        captcha_img = page.query_selector('img[alt*="캡차"]')
        if captcha_img:
            return True
        body_text = page.evaluate('() => (document.body.innerText || "").substring(0, 200)')
        if '보안 확인을 완료해 주세요' in body_text:
            return True
    except:
        pass
    return False


def _parse_image_data(image_data: dict) -> List[str]:
    """__PRELOADED_STATE__에서 추출한 이미지 데이터 파싱"""
    images = []

    rep_url = image_data.get('representativeImageUrl')
    if rep_url:
        normalized = normalize_image_url(rep_url)
        if normalized:
            images.append(normalized)

    for opt_url in image_data.get('optionalImageUrls', []):
        if opt_url:
            normalized = normalize_image_url(opt_url)
            if normalized and normalized not in images:
                images.append(normalized)

    return images[:MAX_IMAGES]


def _extract_images_from_html(html: str) -> List[str]:
    """HTML 소스에서 이미지 URL 정규식 추출"""
    images = []

    rep_match = re.search(r'"representativeImageUrl"\s*:\s*"([^"]+)"', html)
    if rep_match:
        url = normalize_image_url(rep_match.group(1))
        if url:
            images.append(url)

    opt_match = re.search(r'"optionalImageUrls"\s*:\s*\[([^\]]*?)\]', html)
    if opt_match:
        urls_str = opt_match.group(1)
        for url_match in re.findall(r'"([^"]+)"', urls_str):
            url = normalize_image_url(url_match)
            if url and url not in images:
                images.append(url)

    return images[:MAX_IMAGES]


def _extract_images_from_dom(page: Page) -> List[str]:
    """DOM에서 상품 이미지 추출 (최후 수단)"""
    try:
        img_urls = page.evaluate("""
            () => {
                const imgs = document.querySelectorAll('img[src*="shop-phinf.pstatic.net"]');
                return Array.from(imgs)
                    .map(i => i.src)
                    .filter(src => !src.includes('sprite') && !src.includes('icon'));
            }
        """)
        images = []
        for url in img_urls:
            normalized = normalize_image_url(url)
            if normalized and normalized not in images:
                images.append(normalized)
        return images[:MAX_IMAGES]
    except:
        return []


def select_best_product(page: Page, product_nos: List[str]) -> Tuple[Optional[str], List[str]]:
    """최적의 상품 선택"""
    if not product_nos:
        return None, []

    first_no = product_nos[0]
    first_images = get_product_images(page, first_no)

    if len(first_images) >= MIN_IMAGE_COUNT:
        return first_no, first_images

    if len(product_nos) < 2:
        return first_no, first_images

    random_delay()
    second_no = product_nos[1]
    second_images = get_product_images(page, second_no)

    if len(second_images) > len(first_images):
        return second_no, second_images
    else:
        return first_no, first_images


# =====================================================
# 메인 수집기 클래스
# =====================================================

class SmartstoreImageCollector:
    """스마트스토어 이미지 수집기 - CDP 연결 기반"""

    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        log(f"SmartstoreImageCollector 초기화", "INFO")

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

    def _save_results(self, results: List[ProductImageResult], dry_run: bool) -> Dict:
        """결과 DB 저장"""
        stats = {'success': 0, 'not_found': 0, 'error': 0, 'total_images': 0}

        if dry_run:
            for result in results:
                if result.status == "success":
                    stats['success'] += 1
                elif result.status == "not_found":
                    stats['not_found'] += 1
                else:
                    stats['error'] += 1
                stats['total_images'] += len(result.images)
            log(f"[DRY-RUN] 저장 스킵: {len(results)}개 상품", "DB")
            return stats

        try:
            with self.engine.connect() as conn:
                for result in results:
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

                conn.commit()
            log(f"중간 저장 완료: {len(results)}개 상품", "DB")
        except Exception as e:
            log(f"DB 저장 실패: {e}", "ERROR")

        return stats

    def run(self, brand: str = None, model_no: str = None, limit: int = None, dry_run: bool = False, price_checked_only: bool = False) -> Dict:
        """전체 실행"""
        log("=" * 60)
        log("스마트스토어(adorelux) 이미지 수집 시작 (CDP 연결)")
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

        # Chrome 디버그 모드 확인
        if not is_chrome_debug_running():
            log(f"Chrome 디버그 모드가 실행되어 있지 않습니다.", "ERROR")
            log(f"먼저 실행해주세요: python image_collector_parallel_smartstore.py --start-chrome", "ERROR")
            return {'total_products': 0}

        # 대상 상품 조회
        products = self.fetch_target_products(brand=brand, model_no=model_no, limit=limit, price_checked_only=price_checked_only)

        if not products:
            log("수집 대상 상품이 없습니다.")
            return {'total_products': 0}

        total = len(products)
        start_time = time.time()

        total_stats = {
            'total_products': total,
            'success': 0,
            'not_found': 0,
            'error': 0,
            'total_images': 0
        }

        buffer = []

        with sync_playwright() as playwright:
            log(f"Chrome에 CDP 연결 중... ({CHROME_DEBUG_URL})", "BROWSER")
            browser = playwright.chromium.connect_over_cdp(CHROME_DEBUG_URL)

            # 새 탭에서 작업
            context = browser.contexts[0]
            page = context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT)

            log(f"연결 완료, {total}개 상품 처리 예정", "BROWSER")

            # 상품 처리
            for idx, product in enumerate(products):
                result = collect_single_product(page, product, idx, total)
                buffer.append(result)

                if len(buffer) >= BATCH_SAVE_SIZE:
                    stats = self._save_results(buffer, dry_run)
                    total_stats['success'] += stats['success']
                    total_stats['not_found'] += stats['not_found']
                    total_stats['error'] += stats['error']
                    total_stats['total_images'] += stats['total_images']
                    buffer = []

            if buffer:
                stats = self._save_results(buffer, dry_run)
                total_stats['success'] += stats['success']
                total_stats['not_found'] += stats['not_found']
                total_stats['error'] += stats['error']
                total_stats['total_images'] += stats['total_images']

            # 작업 탭만 닫기 (Chrome 자체는 유지)
            page.close()
            browser.close()

        elapsed = time.time() - start_time

        log("\n" + "=" * 60)
        log("수집 완료!")
        log(f"  총 상품: {total_stats['total_products']}건")
        log(f"  성공: {total_stats['success']}건")
        log(f"  검색 실패: {total_stats['not_found']}건")
        log(f"  오류: {total_stats['error']}건")
        log(f"  총 이미지: {total_stats['total_images']}개")
        if elapsed > 0:
            log(f"  소요시간: {elapsed:.1f}초 ({total_stats['total_products']/elapsed:.2f} 상품/초)")
        log("=" * 60)

        return total_stats


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(
        description='스마트스토어(adorelux) 이미지 URL 수집 - CDP 연결 기반'
    )
    parser.add_argument('--start-chrome', action='store_true', help='Chrome 디버그 모드 실행 (최초 1회)')
    parser.add_argument('--brand', type=str, default=None, help='특정 브랜드만 처리')
    parser.add_argument('--model-no', type=str, default=None, help='특정 모델번호만 처리')
    parser.add_argument('--limit', type=int, default=None, help='최대 처리 건수')
    parser.add_argument('--dry-run', action='store_true', help='테스트 모드 (DB 저장 안함)')
    parser.add_argument('--price-checked-only', action='store_true', help='최저가 확인된 상품만 이미지 수집')

    args = parser.parse_args()

    if args.start_chrome:
        start_chrome_debug()
        return

    try:
        collector = SmartstoreImageCollector(DB_URL)
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
