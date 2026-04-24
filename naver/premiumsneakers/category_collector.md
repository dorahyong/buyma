# 네이버 전체상품 페이지 수집기 (`premiumsneakers_category_collector.py`)

브랜드 리스트가 없거나 카테고리 URL 필터링이 동작하지 않는 스마트스토어용 수집기.

---

## 1. 대상 스토어

| 스토어 | 이유 | 전체상품 |
|---|---|---:|
| **dmont** | 브랜드 리스트 없음 + 카테고리 URL 무시됨 | ~1,000+ (추정) |
| **tuttobene** | 브랜드 리스트 없음 + 카테고리 URL 무시됨 | ~1,000+ (추정) |
| **thefactor2** | 브랜드 리스트 있으나 카테고리 탐색이 더 명확 | **1,634** |

처음엔 `scan_store_brands.py`의 카테고리 리스트로 순회(`/category/{hash}`)하려 했으나 dmont/tuttobene는 카테고리 필터가 동작하지 않고 매 카테고리가 동일한 일부 상품만 반환. 그래서 전체상품 페이지(`/category/ALL` 등) 기반으로 전환.

## 2. 전체상품 URL 패턴 (하드코딩)

```python
STORE_ALL_PRODUCT_URLS = {
    'dmont':      'https://smartstore.naver.com/dmont/category/ALL?st=POPULAR&dt=IMAGE&page=1&size=80&filters=oa',
    'thefactor2': 'https://smartstore.naver.com/thefactor2/category/0802efb236ac45b9bdd736ee3c31152d?st=POPULAR&dt=BIG_IMAGE&page=1&size=80&filters=oa',
    'tuttobene':  'https://smartstore.naver.com/tutto-bene/category/ALL?st=POPULAR&dt=GALLERY&page=1&size=80&filters=oa',
}
```

### 공통 쿼리 파라미터
- `size=80` — 한 페이지 80개 (기본 40보다 빠름)
- `filters=oa` — **품절 제외 필터**
- `st=POPULAR` — 인기순 정렬
- `dt=` — 디스플레이 타입(스토어별 다름, 동작에 영향 없음)

### 스토어별 경로
- `/category/ALL` — 대부분 스토어
- `/category/{hash}` — thefactor2는 '전체상품' 가상 해시 있음

## 3. 브랜드 기반 collector와의 차이

| 항목 | 브랜드 기반 | **전체상품 기반** |
|---|---|---|
| Phase 1 단위 | mall_brands 여러 건 | **전체상품 URL 1건** |
| brand 정보 출처 | mall_brands.mall_brand_name_en (authoritative) | **상품 상세 `naverShoppingSearchInfo`** |
| category 정보 | mall_brands 메타 | **상품 상세 `category.wholeCategoryName`** |
| 신규 brand 발견 시 | N/A (사전 등록) | **mall_brands에 auto-INSERT** |
| 신규 category 발견 시 | N/A | **mall_categories에 auto-INSERT** |

## 4. 자동 INSERT 로직

### `ensure_mall_brand(brand_name_en)`
상품 상세에서 brand 추출 → mall_brands에 없으면 INSERT:
```sql
INSERT INTO mall_brands
  (mall_name, mall_brand_name_en, is_active, mapping_level, is_mapped)
VALUES
  (:site, :en, 1, 0, 0)
```
- `buyma_brand_id=NULL` → 이후 `brand_update.xlsx`로 수동 매핑 대기
- 한글 브랜드명도 그대로 저장 (dmont/tuttobene는 한글 입력 우세)

### `ensure_mall_category(full_path)`
상품 상세 카테고리 → mall_categories에 없으면 INSERT:
```sql
INSERT INTO mall_categories
  (mall_name, category_id, gender, depth1..depth4, full_path, buyma_category_id, is_active)
VALUES
  (:site, :full_path, :gender, ..., :full_path, NULL, NULL)
```
- `buyma_category_id=NULL, is_active=NULL` → `category_cleaner.py match`로 Gemini 매칭 대기
- gender 자동 추출: '여성'/'WOMEN' → female, '남성'/'MEN' → male, '유아'/'키즈'/'KIDS' → kids

## 5. 스토어별 상품명 정리

```python
NAME_CLEANUP_PATTERNS = {
    'dmont': [
        r'^\s*디몬트\s+',           # '디몬트 ' prefix 제거
    ],
    'tuttobene': [
        r'\[국내배송\]\s*',
        r'\[[0-9]+%중복쿠폰\]\s*',
    ],
}
```

`clean_product_name()`이 `product_name`과 `p_name_full`에 적용. 필요 시 추가 스토어/패턴 확장.

## 6. 구조

```
premiumsneakers_category_collector.py
  ├── STORE_ALL_PRODUCT_URLS           # 하드코딩 dict
  ├── NAME_CLEANUP_PATTERNS            # 스토어별 prefix 제거 규칙
  ├── clean_product_name()
  ├── get_all_products_url()
  ├── ensure_mall_brand()
  ├── ensure_mall_category()
  ├── collect_product_list_all()       # 단일 URL + 클릭 페이지네이션
  └── run() / main()
```

**import 재사용 (기존 파일 수정 0)**:
- `set_source`, `fetch_detail`, `map_to_row`, `save_rows`
- `get_existing_product_ids`, `absolute_url`, `login_and_save_cookies`
- `COOKIE_FILE`, `DETAIL_MAX_RETRIES`

## 7. 사용법

```bash
# 총 상품 수 집계 (전체상품 페이지 1페이지만 로드해 '총 N개' 추출)
python naver/premiumsneakers/premiumsneakers_category_collector.py --source thefactor2 --count
python naver/premiumsneakers/premiumsneakers_category_collector.py --source dmont --count
python naver/premiumsneakers/premiumsneakers_category_collector.py --source tuttobene --count

# 파일럿 (5건 dry-run)
python naver/premiumsneakers/premiumsneakers_category_collector.py --source thefactor2 --limit 5 --dry-run --dump

# 전수 수집 (이미 수집한 상품은 스킵)
python naver/premiumsneakers/premiumsneakers_category_collector.py --source thefactor2 --skip-existing
```

## 8. 딜레이 (브랜드 collector와 독립)

```python
LIST_DELAY = (0.5, 1.0)
DETAIL_DELAY = (0.8, 1.5)
```

전체상품 기반은 브랜드 루프 없이 단일 URL 순회라 페이지 전환 횟수가 적음. Phase 2 상세 수집 시간이 주요 비중이며, 상품 1,600개 × 평균 1.2초 = 약 30분.

## 9. 검증 결과 (2026-04-17)

| mall | 총 상품 수 | 파일럿 | DB 저장 누적 | brand_name | 이미지 |
|---|---:|---|---:|---|---|
| thefactor2 | 1,634 | ✓ 5/5 | 1,169 | 영문(ADIDAS/PRADA/CELINE 등) | 10장 |
| dmont | — | ✓ 5/5 | 52 | 한글(보보쇼즈/스톤아일랜드) | 1장 |
| tuttobene | — | ✓ 10/10 | 134 | 한글+영문 혼재 | 6~10장 |

## 10. 남은 이슈 / 개선 과제

1. **한글 브랜드 매핑** (dmont/tuttobene)
   - 수집 후 mall_brands에 `buyma_brand_id=NULL`로 쌓임
   - `brand_update.xlsx` 패턴으로 수동 영문/BUYMA 매핑 필요

2. **dmont 이미지 1장 문제**
   - 상품 JSON `productImages`가 대표 1장만
   - 상세 HTML에서 추가 이미지(`detailContentImage`) 추출 로직 필요

3. **카테고리 매핑 자동 대기열**
   - `ensure_mall_category`로 INSERT된 신규 카테고리는 `is_active=NULL`
   - `category_cleaner.py match` 돌려 Gemini 매칭 필요

4. **페이지네이션 검증**
   - thefactor2 파일럿은 1페이지만 확인
   - 전수 수집(21페이지) 실제 동작 검증 필요
