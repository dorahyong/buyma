# 네이버 브랜드스토어 수집기 (`brand_store_collector.py`)

네이버 `brand.naver.com/{store}` 도메인용 상품 수집기.

---

## 1. 대상 스토어

| 스토어 | URL | mall_brands | 비고 |
|---|---|---:|---|
| **carpi** | https://brand.naver.com/carpi | 106건 매핑 | 럭셔리 편집숍 스타일, 이미지 1장만 제공 |
| **joharistore** | https://brand.naver.com/joharistore | 57건 매핑 (76건 중) | DIESEL 등, 이미지 4~5장 |

## 2. 스마트스토어(`premiumsneakers_collector.py`)와의 차이

| 항목 | smartstore | **brandstore** |
|---|---|---|
| 도메인 | `smartstore.naver.com/{store}` | `brand.naver.com/{store}` |
| 상품 JSON API | `/i/v2/channels/{uid}/products/{pno}` | **`/n/v2/channels/{uid}/products/{pno}`** |
| 혜택 API (POST) | `/i/v2/channels/{uid}/product-benefits/{pno}` | **`/n/v2/channels/{uid}/product-benefits/{pno}`** |
| 리스트 페이지 | `/{store}/category/{hash}?page=N&size=40` | `/{store}/category/{hash}?cp=N` |
| 상품 상세 URL | `/products/{pno}` | 동일 |
| 페이지네이션 | DOM 클릭 (URL 무시) | DOM 클릭 (URL 무시) |

**핵심 차이는 API 경로 `/i/v2/` → `/n/v2/`뿐.** `fetch_detail_brand_store`의 regex만 변경하면 나머지는 동일 재사용 가능.

## 3. 구조

```
brand_store_collector.py
  ├── fetch_detail_brand_store()   # /n/v2/ regex, 로컬 정의
  ├── run()                         # brandstore 전용 오케스트레이션
  └── main()                        # CLI
```

**`premiumsneakers_collector.py`에서 import (재사용)**:
- `set_source`, `login_and_save_cookies`, `get_brands`, `get_existing_product_ids`
- `collect_product_list` (브랜드 URL 순회 + 클릭 페이지네이션)
- `map_to_row`, `save_rows`, `absolute_url`, `COOKIE_FILE`, `DETAIL_MAX_RETRIES`

**기존 `premiumsneakers_collector.py`는 0 수정.**

## 4. 사용법

```bash
# 쿠키 갱신 (모든 네이버 스토어 공용)
python naver/premiumsneakers/brand_store_collector.py --source carpi --login

# 파일럿 (5건 dry-run)
python naver/premiumsneakers/brand_store_collector.py --source carpi --limit 5 --dry-run --dump

# 특정 브랜드만
python naver/premiumsneakers/brand_store_collector.py --source carpi --brand "LARDINI" --limit 5

# 총 상품 수 집계
python naver/premiumsneakers/brand_store_collector.py --source joharistore --count

# 전수 수집
python naver/premiumsneakers/brand_store_collector.py --source carpi --skip-existing
python naver/premiumsneakers/brand_store_collector.py --source joharistore --skip-existing
```

## 5. 딜레이 (브랜드 collector와 독립)

```python
LIST_DELAY = (0.5, 1.0)     # 페이지 클릭 간
DETAIL_DELAY = (0.8, 1.5)   # 상품 상세 간
```

`premiumsneakers_collector.py`의 (1~2, 1~3)보다 단축 — 충분히 관찰된 네이버 안정성 범위 내.

## 6. 검증 결과 (2026-04-17 파일럿)

### carpi 10건 수집
| 항목 | 상태 |
|---|---|
| brand_name_en | ✓ 영문 (LARDINI 8, LORO PIANA 1, LOEWE 1) |
| model_id | ✓ 정확 (공백 토큰 OK) |
| category_path | ✓ `패션의류 > 남성의류 > 정장세트` 등 정확 |
| 이미지 | ⚠ **대부분 1장** (productImages 자체가 짧음) |
| 옵션 | 1~4개 |

### joharistore 10건 수집
| 항목 | 상태 |
|---|---|
| brand_name_en | ✓ 영문 (DIESEL 10/10) |
| model_id | ✓ 하이픈 포함 정확 |
| **이미지** | ✓ **4~5장** |
| 옵션 | 0~5개 (color+size) |

## 7. 남은 이슈 / 개선 과제

- **carpi 이미지 1장 문제**: 상품 JSON의 `productImages`가 짧음. 상세 HTML에서 `detailContentImage` 추가 추출 필요 (추후)
- **joharistore brand_id 매핑 57/76** (21건 buyma_brand_id 미매핑): brand_update.xlsx로 수동 매핑 필요
- **페이지네이션 검증**: 파일럿은 1페이지만 확인. 많은 상품 브랜드에서 click 동작 재검증 필요
