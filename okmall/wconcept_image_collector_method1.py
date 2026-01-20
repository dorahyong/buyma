import argparse
import re
import time
import random
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright
# 임포트 오류를 방지하기 위해 stealth_sync 함수를 직접 가져옵니다.
from playwright_stealth import stealth_sync
from sqlalchemy import create_engine, text

# =====================================================
# 설정
# =====================================================
DB_URL = "mysql+pymysql://block:1234@54.180.248.182:3306/buyma?charset=utf8mb4"

WCONCEPT_SEARCH_URL = "https://display.wconcept.co.kr/search?keyword={keyword}&type=direct"
WCONCEPT_PRODUCT_URL = "https://www.wconcept.co.kr/Product/{product_id}"

MAX_IMAGES = 20
NOT_FOUND_VALUE = "not found"

# =====================================================
# 로깅 및 데이터 구조
# =====================================================
def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    emoji = {"INFO": "ℹ️", "ERROR": "❌", "WARNING": "⚠️", "DEBUG": "🔍", "SUCCESS": "✅", "DB": "💾"}
    print(f"[{timestamp}] {emoji.get(level, '  ')} [{level}] {message}")

@dataclass
class ImageData:
    ace_product_id: int
    position: int
    source_image_url: str

@dataclass
class ProductImageResult:
    ace_product_id: int
    model_no: str
    wconcept_product_id: Optional[str] = None
    images: List[ImageData] = field(default_factory=list)
    status: str = "pending"

# =====================================================
# Playwright 수집 클래스
# =====================================================
class WconceptBrowserCollector:
    def __init__(self, db_url: str, headless: bool = True):
        self.engine = create_engine(db_url)
        self.headless = headless
        log(f"Collector 초기화 (Headless={headless})")

    def fetch_target_products(self, brand: str = None, limit: int = None) -> List[Dict]:
        """DB에서 수집 대상 조회"""
        with self.engine.connect() as conn:
            query = """
                SELECT ap.id, ap.model_no, ap.brand_name
                FROM ace_products ap
                LEFT JOIN ace_product_images api ON ap.id = api.ace_product_id
                WHERE api.id IS NULL AND ap.model_no IS NOT NULL AND ap.model_no != ''
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
            return [{'id': r[0], 'model_no': r[1], 'brand_name': r[2]} for r in result]

    def solve_product_id(self, page, model_no: str) -> Optional[str]:
        """검색 페이지에서 렌더링 대기 후 상품 ID 추출"""
        try:
            url = WCONCEPT_SEARCH_URL.format(keyword=model_no)
            # 페이지 응답이 오기 시작하면 즉시 진행 (무한 대기 방지)
            page.goto(url, wait_until="commit", timeout=30000)
            
            # 동적 상품 요소가 로드될 때까지 최대 7초 대기
            try:
                page.wait_for_selector(".product-item, .search-result-title", timeout=7000)
            except:
                pass

            content = page.content()
            
            # 텍스트 내에서 상품 ID 추출 (/Product/9자리숫자)
            match = re.search(r'/Product/(\d{9})', content)
            if match:
                pid = match.group(1)
                log(f"  [Search] 상품 ID 발견: {pid}", "SUCCESS")
                return pid

            # 결과가 없는 경우 공백 제거 후 재시도
            if ' ' in model_no:
                short_kwd = model_no.split(' ')[0]
                log(f"  [Retry] '{model_no}' 결과 없음. '{short_kwd}'로 재시도...", "WARNING")
                return self.solve_product_id(page, short_kwd)

            return None
        except Exception as e:
            log(f"  [Search] 에러: {str(e)}", "ERROR")
            return None

    def get_images(self, page, product_id: str, model_no: str) -> List[str]:
        """상세 페이지 렌더링 후 이미지 수집"""
        try:
            url = WCONCEPT_PRODUCT_URL.format(product_id=product_id)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # 갤러리 로드 대기
            try:
                page.wait_for_selector("#gallery, #divImageDetail", timeout=5000)
            except:
                pass

            image_urls = []

            # 1. 썸네일 수집 (data-zoom-image)
            thumbs = page.query_selector_all("#gallery li a")
            for t in thumbs:
                zoom_url = t.get_attribute("data-zoom-image")
                if zoom_url:
                    full_url = f"https:{zoom_url}" if zoom_url.startswith("//") else zoom_url
                    clean_url = full_url.split('?')[0]
                    if clean_url not in image_urls:
                        image_urls.append(clean_url)
                        log(f"    🔎 발견(썸네일): {clean_url}", "DEBUG")

            # 2. 본문 상세 이미지 수집 (모델번호 필터링)
            match_kwd = model_no.replace(' ', '')[:6]
            detail_imgs = page.query_selector_all("#divImageDetail img")
            for img in detail_imgs:
                alt = (img.get_attribute("alt") or "").upper()
                src = img.get_attribute("src") or ""
                
                # 공통 안내 이미지 제외
                if any(x in alt for x in ['PREFIX', 'SIZE', 'INFO']): continue
                
                if match_kwd in alt or match_kwd in src:
                    full_url = f"https:{src}" if src.startswith("//") else src
                    clean_url = full_url.split('?')[0]
                    if clean_url not in image_urls:
                        image_urls.append(clean_url)
                        log(f"    🔎 발견(상세): {clean_url}", "DEBUG")

            return image_urls[:MAX_IMAGES]
        except Exception as e:
            log(f"  [Detail] 에러: {str(e)}", "ERROR")
            return []

    def run(self, brand: str = None, limit: int = None):
        targets = self.fetch_target_products(brand, limit)
        if not targets:
            log("수집 대상이 없습니다.")
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080}
            )
            
            page = context.new_page()
            
            # stealth_sync 함수를 직접 호출합니다.
            stealth_sync(page)

            results = []
            for idx, item in enumerate(targets):
                log(f"[{idx+1}/{len(targets)}] 처리 시작: {item['model_no']}")
                
                res = ProductImageResult(ace_product_id=item['id'], model_no=item['model_no'])
                w_id = self.solve_product_id(page, res.model_no)
                
                if w_id:
                    res.wconcept_product_id = w_id
                    urls = self.get_images(page, w_id, res.model_no)
                    if urls:
                        res.images = [ImageData(item['id'], i+1, url) for i, url in enumerate(urls)]
                        res.status = "success"
                        log(f"    ✅ 확보 완료: {len(urls)}개 이미지")
                    else:
                        res.status = "not_found"
                else:
                    res.status = "not_found"
                    log("    ⚠️ 건너뜀: 상품을 찾을 수 없음", "WARNING")

                results.append(res)
                # 차단 방지를 위한 랜덤 지연
                page.wait_for_timeout(random.randint(2000, 4000))

            self.batch_save(results)
            browser.close()

    def batch_save(self, results: List[ProductImageResult]):
        log("DB 저장 프로세스 시작...", "DB")
        with self.engine.connect() as conn:
            for r in results:
                if r.status != "success": continue
                try:
                    conn.execute(text("DELETE FROM ace_product_images WHERE ace_product_id = :pid"), {'pid': r.ace_product_id})
                    for img in r.images:
                        conn.execute(text("""
                            INSERT INTO ace_product_images (ace_product_id, position, source_image_url)
                            VALUES (:pid, :pos, :url)
                        """), {'pid': img.ace_product_id, 'pos': img.position, 'url': img.source_image_url})
                except Exception as e:
                    log(f"DB 저장 오류 (ID {r.ace_product_id}): {str(e)}", "ERROR")
            conn.commit()
        log("모든 작업 완료")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--brand', type=str, default=None)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--headed', action='store_true', help="브라우저 화면을 보면서 실행")
    args = parser.parse_args()

    collector = WconceptBrowserCollector(DB_URL, headless=not args.headed)
    collector.run(brand=args.brand, limit=args.limit)