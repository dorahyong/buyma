import argparse
import re
import time
import random
import json
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text

# =====================================================
# 설정
# =====================================================
DB_URL = "mysql+pymysql://block:1234@54.180.248.182:3306/buyma?charset=utf8mb4"

# 403 에러 방지를 위해 일반 검색 페이지 URL 사용
WCONCEPT_SEARCH_URL = "https://display.wconcept.co.kr/search"
WCONCEPT_PRODUCT_URL = "https://www.wconcept.co.kr/Product/{product_id}"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://www.wconcept.co.kr/',
}

REQUEST_DELAY_MIN = 1.5
REQUEST_DELAY_MAX = 2.5
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
    is_uploaded: int = 0

@dataclass
class ProductImageResult:
    ace_product_id: int
    model_no: str
    wconcept_product_id: Optional[str] = None
    images: List[ImageData] = field(default_factory=list)
    status: str = "pending"

def normalize_image_url(url: str) -> str:
    if not url: return ""
    if url.startswith("//"): url = f"https:{url}"
    return url.split("?")[0]

# =====================================================
# 수집 클래스
# =====================================================
class WconceptImageCollector:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        log("WconceptImageCollector 초기화 완료 (정규식 추출 모드)")

    def fetch_target_products(self, brand: str = None, limit: int = None) -> List[Dict]:
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

    def search_wconcept(self, model_no: str, is_retry: bool = False) -> Optional[str]:
        """403 방지를 위해 일반 GET 검색을 수행하고 텍스트 전체에서 ID 추출"""
        try:
            params = {'keyword': model_no, 'type': 'direct'}
            response = self.session.get(WCONCEPT_SEARCH_URL, params=params, timeout=20)
            
            if response.status_code != 200:
                log(f"페이지 접근 실패: {response.status_code}", "ERROR")
                return None

            html_content = response.text
            
            # 방법 1: "itemCd":"307698365" 패턴 검색 (JSON 데이터 타겟)
            item_cd_match = re.search(r'["\']itemCd["\']\s*:\s*["\'](\d{9})["\']', html_content)
            if item_cd_match:
                product_id = item_cd_match.group(1)
                log(f"  [Match] JSON 데이터에서 ID 추출 성공: {product_id}", "SUCCESS")
                return product_id

            # 방법 2: 이미지 경로 내의 ID 추출 (예: /307698365_GG10848.jpg)
            img_id_match = re.search(r'/(\d{9})_', html_content)
            if img_id_match:
                product_id = img_id_match.group(1)
                log(f"  [Match] 이미지 경로에서 ID 추출 성공: {product_id}", "SUCCESS")
                return product_id

            # 방법 3: /Product/307698365 형태의 링크 검색
            link_id_match = re.search(r'/Product/(\d{9})', html_content)
            if link_id_match:
                product_id = link_id_match.group(1)
                log(f"  [Match] 링크 경로에서 ID 추출 성공: {product_id}", "SUCCESS")
                return product_id

            # 실패 시 재시도 (모델번호 첫 단어만 추출)
            if not is_retry and ' ' in model_no:
                retry_kwd = model_no.split(' ')[0]
                log(f"  [Retry] '{model_no}' 결과 없음. '{retry_kwd}'로 재검색...", "WARNING")
                return self.search_wconcept(retry_kwd, is_retry=True)

            return None
        except Exception as e:
            log(f"검색 중 에러: {str(e)}", "ERROR")
            return None

    def get_product_images(self, product_id: str, model_no: str) -> List[str]:
        """상세 페이지 이미지 추출 및 수집 URL 로그 출력"""
        try:
            url = WCONCEPT_PRODUCT_URL.format(product_id=product_id)
            res = self.session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, 'html.parser')
            images = []

            # 1. 썸네일 이미지 (gallery_wrap)
            gallery = soup.select('#gallery li a[data-zoom-image]')
            for a in gallery:
                img_url = normalize_image_url(a.get('data-zoom-image', ''))
                if img_url and img_url not in images:
                    images.append(img_url)
                    log(f"    🔎 발견(썸네일): {img_url}", "DEBUG")

            # 2. 상세 본문 이미지 (divImageDetail)
            detail_div = soup.select_one('#divImageDetail')
            if detail_div:
                match_kwd = model_no.replace(' ', '')[:6]
                detail_imgs = detail_div.select('img')
                for img in detail_imgs:
                    alt = img.get('alt', '')
                    src = img.get('src', '')
                    # 모델번호 매칭 (샘플 HTML 구조 반영)
                    if alt not in ['PREFIX_INFO', 'SIZE_INFO', ''] and (match_kwd in alt or match_kwd in src):
                        img_url = normalize_image_url(src)
                        if img_url and img_url not in images:
                            images.append(img_url)
                            log(f"    🔎 발견(상세): {img_url}", "DEBUG")

            return images[:MAX_IMAGES]
        except Exception as e:
            log(f"이미지 추출 에러: {str(e)}", "ERROR")
            return []

    def collect_single_product(self, product: Dict) -> ProductImageResult:
        res = ProductImageResult(ace_product_id=product['id'], model_no=product['model_no'])
        
        # 1. 검색 페이지에서 상품 ID 확보
        w_id = self.search_wconcept(res.model_no)
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        if not w_id:
            log(f"결과: {res.model_no} - 상품 ID를 찾을 수 없음 (Skip)", "WARNING")
            res.images = [ImageData(product['id'], 1, NOT_FOUND_VALUE)]
            return res

        res.wconcept_product_id = w_id
        
        # 2. 상세 페이지에서 이미지 수집
        image_urls = self.get_product_images(w_id, res.model_no)
        if not image_urls:
            log(f"결과: {res.model_no} - 이미지를 찾을 수 없음", "WARNING")
            res.images = [ImageData(product['id'], 1, NOT_FOUND_VALUE)]
        else:
            log(f"결과: {res.model_no} - 총 {len(image_urls)}개 이미지 확보 완료", "SUCCESS")
            res.status = "success"
            res.images = [ImageData(product['id'], i+1, url) for i, url in enumerate(image_urls)]
        
        return res

    def batch_insert(self, results: List[ProductImageResult]):
        log("데이터베이스 저장을 시작합니다.", "DB")
        success_cnt = 0
        with self.engine.connect() as conn:
            for r in results:
                try:
                    # 기존 이미지 데이터 청소 후 재입력
                    conn.execute(text("DELETE FROM ace_product_images WHERE ace_product_id = :pid"), {'pid': r.ace_product_id})
                    for img in r.images:
                        conn.execute(text("""
                            INSERT INTO ace_product_images (ace_product_id, position, source_image_url, is_uploaded)
                            VALUES (:pid, :pos, :url, :up)
                        """), {'pid': img.ace_product_id, 'pos': img.position, 'url': img.source_image_url, 'up': img.is_uploaded})
                    if r.status == "success": success_cnt += 1
                except Exception as e:
                    log(f"DB 오류 (ID:{r.ace_product_id}): {str(e)}", "ERROR")
            conn.commit()
        log(f"DB 저장 완료 (성공 상품: {success_cnt}건)", "DB")

    def run(self, brand: str = None, limit: int = None):
        targets = self.fetch_target_products(brand, limit)
        log(f"수집 대상 상품 수: {len(targets)}")
        if not targets: return

        results = [self.collect_single_product(item) for item in targets]
        self.batch_insert(results)
        log("모든 작업 프로세스 종료")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--brand', type=str, default=None)
    parser.add_argument('--limit', type=int, default=None)
    args = parser.parse_args()

    collector = WconceptImageCollector(DB_URL)
    collector.run(brand=args.brand, limit=args.limit)

if __name__ == "__main__":
    main()