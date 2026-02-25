# okmall_all_brands_collector.py 상세 분석

## 1. 개요

오케이몰(okmall.com)에서 **모든 활성 브랜드의 상품 정보를 자동 수집**하는 웹 스크래핑 스크립트입니다.

### 주요 특징
- **ld+json 우선 파싱**: HTML 직접 파싱보다 `<script type="application/ld+json">` 구조화 데이터를 우선 활용하여 정확도 향상
- **이중 재고 확인**: HTML 텍스트(`품절`)와 ld+json(`OutOfStock`) 두 가지 방식으로 재고 상태 교차 검증
- **품절 임박 예외 처리**: '품절 임박' 문구는 실제 품절이 아닌 재고 있음으로 판단
- **흠집특가상품 제외**: `item_scratch` 클래스가 있는 상품은 수집 대상에서 제외
- **의류/액세서리 실측 구조 대응**: `div.item_size_detail`(의류)과 `div#realSizeInfo_detail2`(가방/액세서리) 두 가지 HTML 구조 모두 파싱
- **배치 저장 (10개 단위)**: 메모리 효율성 및 중간 저장으로 장애 시 데이터 손실 최소화
- **UPSERT 전략**: `ON DUPLICATE KEY UPDATE`로 신규 INSERT 또는 기존 레코드 UPDATE 자동 처리
- **페이지네이션 자동 순회**: 브랜드별 최대 100페이지까지 자동으로 상품 목록 수집
- **유연한 수집 범위 조절**: `--brand`, `--limit`, `--dry-run` 옵션으로 테스트 및 부분 수집 지원

---

## 2. 환경 설정 (라인 24-51)

### 2.1 의존성 라이브러리
| 라이브러리 | 용도 |
|-----------|------|
| `requests` | HTTP 요청 |
| `BeautifulSoup` | HTML 파싱 |
| `dotenv` | 환경변수 로드 |
| `sqlalchemy` | DB 연결 |

### 2.2 환경변수 로드
```python
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
```
- 상위 디렉토리의 `.env` 파일에서 환경변수 로드

### 2.3 로깅 설정
```python
format='[%(asctime)s] %(levelname)s - %(message)s'
datefmt='%Y-%m-%d %H:%M:%S'
```

### 2.4 DB 연결
```python
DATABASE_URL = os.getenv('DATABASE_URL', 'mysql+pymysql://block:1234@54.180.248.182:3306/buyma')
```
- 환경변수 우선, 없으면 기본값 사용

### 2.5 스크래핑 설정
| 설정 | 값 | 설명 |
|-----|-----|------|
| `USER_AGENT` | Chrome 144.0 | 브라우저 위장 |
| `SCRAPING_DELAY` | 1.5초 | 요청 간 지연 시간 |
| `HEADERS` | Accept, Accept-Language, Referer 등 | HTTP 헤더 |

---

## 3. 데이터 추출 함수 (라인 57-310)

### 3.1 `extract_ld_json(soup)` - ld+json 파싱
**입력:** BeautifulSoup 객체
**출력:** `(product_data: Dict, breadcrumb_list: List)`

```
<script type="application/ld+json"> 태그에서:
├── @type == 'Product' → product_data
└── @type == 'BreadcrumbList' → breadcrumb_list
```

---

### 3.2 `extract_brand_info(product_data, soup)` - 브랜드 정보 추출
**출력:** `(brand_en, brand_kr)`

**추출 로직:**
1. ld+json의 `brand.name`에서 기본 추출
2. 한글 브랜드명: 괄호 앞 부분 `브랜드명(영문)` → `브랜드명`
3. 영문 브랜드명: 괄호 안 부분 또는 `.target_brand .prName_Brand` 선택자

---

### 3.3 `extract_product_name(soup)` - 상품명/모델번호 추출
**출력:** `(product_name, full_name, model_id, season)`

| 필드 | 소스 선택자 | 설명 |
|-----|------------|------|
| `full_name` | `h3#ProductNameArea` | 전체 상품명 |
| `season` | `.prd_name_season` | 시즌 정보 |
| `product_name` | `.prd_name` (괄호 앞) | 상품명 |
| `model_id` | `.prd_name` (괄호 안) | 모델번호 |

---

### 3.4 `extract_price_info(product_data, soup)` - 가격 정보 추출
**출력:** `(original_price, sales_price)`

| 가격 | 추출 방식 |
|-----|----------|
| **정가** | `.value_price .price` 선택자에서 숫자만 추출 |
| **판매가** | ld+json `offers.lowPrice` (AggregateOffer) 또는 `offers.price` (Offer) |

---

### 3.5 `extract_category_path(breadcrumb_list)` - 카테고리 경로 추출
**출력:** `"카테고리1 > 카테고리2 > 카테고리3"` 형태 문자열

---

### 3.6 `extract_options(soup, product_data)` - 옵션/재고 추출
**출력:** `List[Dict]` - 옵션 목록

**HTML 파싱 대상:**
```html
#ProductOPTList tbody tr[name="selectOption"]
```

**추출 필드:**
| 필드 | 소스 | 설명 |
|-----|------|------|
| `color` | 1번째 td | 색상 |
| `tag_size` | 2번째 td (`.size_notice` 제거) | 태그 사이즈 |
| `real_size` | 3번째 td | 실제 사이즈 |
| `option_code` | `sinfo` 속성의 마지막 `\|` 이후 | 옵션 코드 |
| `status` | 행 텍스트 분석 + ld+json | 재고 상태 |

**재고 상태 판단 로직:**
1. 행 텍스트에 '품절' 포함 & '품절 임박' 미포함 → `out_of_stock`
2. ld+json `offers.offers[].availability`에 `OutOfStock` 포함 → `out_of_stock`
3. 그 외 → `in_stock`

---

### 3.7 `extract_measurements(soup)` - 실측 정보 추출
**출력:** `Dict[사이즈명: Dict[항목명: 값]]`

**HTML 탐색 순서:**
1. `div.item_size_detail` (의류)
2. `div#realSizeInfo_detail2` (가방/액세서리)

**처리 로직:**
```
div 찾기 → display:none 아닌 ul 선택 → 각 li 순회
    ├── a 태그에서 사이즈명
    ├── p 태그에서 summary
    └── tbody/tr에서 항목별 값 추출
        └── 숫자 접두사 제거 (①, ②, 1., 2. 등)
```

**예시 출력:**
```json
{
  "S": {"summary": "가슴 100, 어깨 42", "가로": "30cm", "세로": "40cm"},
  "M": {"summary": "가슴 104, 어깨 44", "가로": "32cm", "세로": "42cm"}
}
```

---

### 3.8 `extract_composition(soup)` - 혼용률 추출
**출력:** `Dict[str, str]`

**HTML 대상:** `div#realSizeInfo_material`

**라벨 매핑:**
| 원본 라벨 | 매핑 키 |
|----------|--------|
| 겉감 | `outer` |
| 안감 | `lining` |
| 충전재 | `padding` |
| 소재 | `material` |
| 혼용률/혼용율 | `blend_ratio` |
| 기타 | 원본 라벨명 그대로 |

**빈 결과 대비:** 테이블 외 직접 텍스트 → `raw` 키로 저장

---

### 3.9 `extract_product_data(html, product_url)` - 전체 데이터 조합
**최종 출력 구조:**

```python
{
    # 기본 정보
    'source_site': 'okmall',
    'mall_product_id': str,      # ld+json sku 또는 URL의 no 파라미터
    'brand_name_en': str,
    'brand_name_kr': str,
    'product_name': str,
    'p_name_full': str,
    'model_id': str,
    'category_path': str,
    'original_price': int,
    'raw_price': int,
    'stock_status': str,         # 'in_stock' | 'out_of_stock'
    'product_url': str,

    # JSON 데이터 (문자열화)
    'raw_json_data': {
        'images': [],            # 비어있음 (이미지 수집 제외)
        'options': List[Dict],
        'season': str,
        'measurements': Dict,
        'composition': Dict,
        'ld_json_product': Dict,
        'rating': Dict,
        'scraped_at': ISO8601 timestamp
    }
}
```

**재고 상태 결정:**
```python
stock_status = 'in_stock' if any(opt['status'] == 'in_stock' for opt in options) else 'out_of_stock'
```

---

## 4. 수집 및 저장 로직 (라인 316-384)

### 4.1 `get_brands_from_database(brand_filter)` - 브랜드 목록 조회
**SQL:**
```sql
SELECT mall_brand_name_en, mall_brand_url
FROM mall_brands
WHERE mall_name = 'okmall' AND is_active = 1
-- brand_filter 있으면:
AND UPPER(mall_brand_name_en) = :brand
```

---

### 4.2 `get_product_urls_from_list(base_url, limit)` - 상품 URL 수집

**동작:**
1. 페이지 1부터 최대 100페이지까지 순회
2. 각 페이지에서 `.item_box[data-productno]` 선택
3. **흠집특가상품 제외** (`item_scratch` 클래스 필터링)
4. `data-productno` 속성으로 상품 URL 생성

**흠집특가상품 제외 로직:**
```python
for box in product_boxes:
    # 흠집특가상품 제외 (item_scratch 클래스)
    if 'item_scratch' in box.get('class', []):
        scratch_count += 1
        continue
    ...
```
- 흠집특가상품은 `class="item_box item_scratch"`로 구분됨
- 제외된 상품 수를 로깅하여 추적 가능

**URL 생성:**
```
https://www.okmall.com/products/view?no={product_no}
```

**제한사항:**
- `limit` 지정 시 해당 개수까지만 수집
- 페이지 간 0.5초 지연
- 중복 제거: `list(dict.fromkeys(all_urls))`

---

### 4.3 `save_to_database(data_list)` - DB 저장

**테이블:** `raw_scraped_data`

**INSERT 전략:** `ON DUPLICATE KEY UPDATE`
- 중복 시 기존 레코드 업데이트
- `updated_at = NOW()` 갱신

**배치 처리:** 개별 INSERT (트랜잭션 내)

---

## 5. 메인 함수 및 CLI 옵션 (라인 386-434)

### 5.1 CLI 인자

| 옵션 | 타입 | 설명 |
|-----|------|------|
| `--brand` | str | 특정 브랜드만 처리 (대소문자 무관) |
| `--limit` | int | 브랜드당 최대 수집 상품 수 |
| `--dry-run` | flag | DB 저장 없이 테스트 실행 |

### 5.2 실행 흐름

```
시작 로그 출력
    ↓
브랜드 목록 조회 (get_brands_from_database)
    ↓
각 브랜드별 루프 ─────────────────────────┐
    ↓                                      │
    상품 URL 수집 (get_product_urls_from_list)│
    ↓                                      │
    각 상품별 루프 ──────────────┐          │
        ↓                        │          │
        HTTP GET 요청            │          │
        ↓                        │          │
        데이터 추출              │          │
        ↓                        │          │
        dry-run? → 로그만 출력  │          │
        아니면 → batch_data에 추가│          │
        ↓                        │          │
        10개 모이면 DB 저장     │          │
        ↓                        │          │
        1.5초 지연               │          │
    ←────────────────────────────┘          │
    ↓                                      │
    남은 batch_data DB 저장                │
←──────────────────────────────────────────┘
    ↓
완료 로그 출력
```

### 5.3 배치 저장 로직
```python
if len(batch_data) >= 10:
    save_to_database(batch_data)
    batch_data = []
```
- 10개 단위로 DB 저장
- 루프 종료 후 남은 데이터도 저장

### 5.4 에러 처리
```python
except Exception as e:
    logger.error(f"  [{idx}/{len(product_urls)}] 오류: {e}")
```
- 개별 상품 실패 시 로그 출력 후 계속 진행

---

## 6. 사용 예시

```bash
# 전체 브랜드 수집
python okmall_all_brands_collector.py

# GUCCI 브랜드만 수집
python okmall_all_brands_collector.py --brand GUCCI

# 브랜드당 50개씩만 수집
python okmall_all_brands_collector.py --limit 50

# 테스트 실행 (DB 저장 안함)
python okmall_all_brands_collector.py --dry-run

# 조합
python okmall_all_brands_collector.py --brand PRADA --limit 10 --dry-run
```

---

## 7. DB 테이블 의존성

### 읽기 (SELECT)
| 테이블 | 컬럼 |
|--------|------|
| `mall_brands` | `mall_brand_name_en`, `mall_brand_url`, `mall_name`, `is_active` |

### 쓰기 (INSERT/UPDATE)
| 테이블 | 컬럼 |
|--------|------|
| `raw_scraped_data` | 13개 컬럼 (source_site, mall_product_id, brand_name_en 등) |

---

## 8. 주요 특이사항

1. **이미지 수집 제외**: `raw_json_data`에 `images` 키 없음 (별도 이미지 수집기에서 처리)
2. **흠집특가상품 제외**: `item_scratch` 클래스가 있는 상품은 목록 수집 단계에서 제외
3. **품절 임박 처리**: '품절 임박'은 재고 있음으로 처리
4. **실측 정보**: 의류와 가방/액세서리 두 가지 HTML 구조 모두 지원
5. **대소문자 무관**: `--brand` 옵션은 대소문자 구분 없이 매칭
6. **중복 방지**: URL 수집 시 `dict.fromkeys()`로 중복 제거
