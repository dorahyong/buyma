import time
import json
import re
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine, text
from datetime import datetime

# DB 연결
DB_URL = "mysql+pymysql://root:hyong@localhost:3306/local?charset=utf8mb4"
engine = create_engine(DB_URL)

def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def extract_data_v15(page):
    """ld+json과 HTML을 분석하여 필수 데이터 추출"""
    
    # 1. ld+json 파싱 (Breadcrumb 및 기본 정보용)
    scripts = page.query_selector_all("script[type='application/ld+json']")
    ld_prod, ld_bread = {}, []
    for s in scripts:
        try:
            content = json.loads(s.inner_text())
            if isinstance(content, dict):
                if content.get("@type") == "Product": ld_prod = content
                elif content.get("@type") == "BreadcrumbList": ld_bread = content.get("itemListElement", [])
        except: continue

    # 2. 브랜드명 (EN/KR 분리) - "스톤아일랜드(STONE ISLAND)" 처리
    raw_brand = ld_prod.get("brand", {}).get("name", "")
    brand_ko = re.split(r'\(', raw_brand)[0].strip() if '(' in raw_brand else raw_brand
    brand_en = ""
    en_match = re.search(r'\((.*?)\)', raw_brand)
    if en_match:
        brand_en = en_match.group(1).strip()
    else:
        # ld+json에 없으면 상세 레이어에서 재시도
        brand_en = page.inner_text(".target_brand .prName_Brand").strip() if page.query_selector(".target_brand .prName_Brand") else ""

    # 3. 상품명 및 모델명
    p_name_full = page.inner_text("h3#ProductNameArea").replace("\n", " ").strip()
    prd_name_text = page.inner_text("h3#ProductNameArea .prd_name").strip()
    
    # 모델명 추출: 첫 번째 괄호() 안의 값
    model_id = ""
    model_match = re.search(r'\((.*?)\)', prd_name_text)
    if model_match:
        model_id = model_match.group(1)
    
    p_name_clean = prd_name_text.split('(')[0].strip()

    # 4. 가격 정보 (정가: .value_price .price / 판매가: ld+json)
    origin_price = 0
    origin_elem = page.query_selector(".value_price .price")
    if origin_elem:
        origin_price = int(re.sub(r'[^0-9]', '', origin_elem.inner_text()))
    
    sales_price = int(ld_prod.get("offers", {}).get("lowPrice", 0))

    # 5. 카테고리 경로
    category_path = " > ".join([b.get("name", "") for b in ld_bread if b.get("name")])

    # 6. 옵션 정보 (#ProductOPTList 테이블)
    options = []
    opt_rows = page.query_selector_all("#ProductOPTList tbody tr")
    for row in opt_rows:
        cols = row.query_selector_all("td")
        if len(cols) >= 4:
            options.append({
                "color": cols[0].inner_text().strip(),         # 색상: Black
                "tag_size": cols[1].inner_text().strip(),
                "real_size": cols[2].inner_text().strip(),  # 실측 데이터 추가
                "status": "out_of_stock" if "품절" in row.inner_text() else "in_stock"
            })

    return {
        "brand_en": brand_en, "brand_ko": brand_ko,
        "p_name": p_name_clean, "p_name_full": p_name_full, "model_id": model_id,
        "original_price": origin_price, "sales_price": sales_price,
        "category_path": category_path, "options": options
    }

def main():
    brand_url = "https://www.okmall.com/products/list?brand=%EC%8A%A4%ED%86%A4%EC%95%84%EC%9D%BC%EB%9E%9C%EB%93%9C%28STONE+ISLAND%29"
    collected_results = []

    with sync_playwright() as p:
        log("=== 스톤아일랜드 Bulk 수집 테스트 시작 ===")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.new_page()
        
        page.goto(brand_url, wait_until="networkidle")
        page.keyboard.press("End")
        time.sleep(2)

        items = page.query_selector_all(".item_box[data-productno]")
        all_urls = [f"https://www.okmall.com/products/view?no={i.get_attribute('data-productno')}" for i in items]
        targets = list(dict.fromkeys(all_urls))[:10]

        for idx, url in enumerate(targets):
            log(f"[{idx+1}/10] 수집 시도: {url}")
            try:
                page.goto(url, wait_until="load", timeout=30000)
                # 요소가 나타날 때까지 대기 강화
                page.wait_for_selector("h3#ProductNameArea", state="visible", timeout=15000)
                
                res = extract_data_v15(page)
                
                # 리스트에 추가 (DB 저장 전 단계)
                data_item = {
                    "site": "okmall", "p_id": url.split("no=")[-1].split("&")[0],
                    "brand_en": res['brand_en'], "brand_ko": res['brand_ko'],
                    "p_name": res['p_name'], "p_name_full": res['p_name_full'],
                    "model_id": res['model_id'], "cat_path": res['category_path'],
                    "o_price": res['original_price'], "s_price": res['sales_price'],
                    "stock": "in_stock" if any(o['status'] == 'in_stock' for o in res['options']) else "out_of_stock",
                    "json_data": json.dumps(res, ensure_ascii=False), "url": url
                }
                collected_results.append(data_item)
                log(f"   - 완료: {res['brand_ko']} | {res['p_name']} | 정가:{res['original_price']}")

            except Exception as e:
                log(f"   - 실패: {url} | 사유: {str(e)[:50]}")
            time.sleep(1.5)

        # 3. 한 번에 DB 저장
        if collected_results:
            log(f"\n[DB 저장] {len(collected_results)}건 Bulk Insert 시작...")
            with engine.connect() as conn:
                for item in collected_results:
                    conn.execute(text("""
                        INSERT INTO raw_scraped_data 
                        (source_site, mall_product_id, brand_name_en, brand_name_kr, product_name, p_name_full, model_id, category_path, original_price, raw_price, stock_status, raw_json_data, product_url)
                        VALUES (:site, :p_id, :brand_en, :brand_ko, :p_name, :p_name_full, :model_id, :cat_path, :o_price, :s_price, :stock, :json_data, :url)
                        ON DUPLICATE KEY UPDATE 
                        raw_price=:s_price, stock_status=:stock, raw_json_data=:json_data, updated_at=NOW()
                    """), item)
                conn.commit()
            log("Bulk Insert 완료!")
        else:
            log("수집된 데이터가 없어 저장을 건너뜁니다.")

if __name__ == "__main__":
    main()