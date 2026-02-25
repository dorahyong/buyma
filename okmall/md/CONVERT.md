# raw_to_ace_converter.py 상세 분석

## 1. 개요

`raw_scraped_data` 테이블의 원본 데이터를 **바이마 API 형식에 맞는 `ace` 테이블 데이터로 변환**하는 스크립트입니다.

### 주요 특징
- **다중 테이블 변환**: 단일 raw 데이터를 ace_products, ace_product_options, ace_product_variants 3개 테이블로 분리 저장 (이미지는 image_collector_parallel.py에서 별도 처리)
- **매핑 데이터 활용**: mall_brands, mall_categories, shipping_config 테이블의 매핑 정보 활용
- **측정값 자동 변환**: 한국어 측정 키를 일본어로 변환하고, 너비→둘레 자동 계산 (x2)
- **카테고리별 사이즈 키 필터링**: size_details2.csv 기반으로 카테고리에 허용된 측정 키만 저장
- **텍스트 정제**: 유럽형 악센트 문자, 특수 기호 자동 정제 (바이마 API 거부 방지)
- **배치 번역 연동**: 변환 완료 후 자동으로 일본어 번역 실행 (convert_to_japanese_gemini.py)
- **UPSERT 지원**: `--upsert` 옵션으로 기존 데이터 업데이트 가능

---

## 2. 환경 설정 (라인 49-188)

### 2.1 의존성 라이브러리
| 라이브러리 | 용도 |
|-----------|------|
| `sqlalchemy` | DB 연결 및 ORM |
| `dotenv` | 환경변수 로드 |
| `unicodedata` | 텍스트 정규화 |
| `uuid` | 관리번호 생성 |

### 2.2 외부 모듈 의존성
```python
from convert_to_japanese_gemini import run_batch_translation
```
- 변환 완료 후 배치 번역 자동 실행

### 2.3 가격/환율 설정
| 설정 | 값 | 설명 |
|-----|-----|------|
| `BUYMA_SALES_FEE_RATE` | 0.055 | 바이마 판매수수료 5.5% |
| `VAT_REFUND_RATE` | 1/11 | 부가세 환급율 |
| `DEFAULT_SHIPPING_FEE` | 15000 | 기본 배송비 (원) |
| `EXCHANGE_RATE_WON_TO_YEN` | 9.2 | 원/엔 환율 |
| `EXCHANGE_RATE_FOR_REFERENCE_PRICE` | 0.1 | 정가용 환율 (KRW / 10) |
| `MIN_PRICE_JPY` | 500 | 최소 엔화 판매가 |
| `DEFAULT_AVAILABLE_DAYS` | 90 | 기본 구매 기한 |

### 2.4 바이마 API 고정값
```python
BUYMA_FIXED_VALUES = {
    'buying_area_id': '2002003000',     # 구매 지역 ID
    'shipping_area_id': '2002003000',   # 발송 지역 ID
    'theme_id': 98,                     # 테마 ID
    'duty': 'included',                 # 관세 정보
    'shipping_method_id': 1063035,      # 배송 방법 ID
}
```

### 2.5 측정 키 매핑 (MEASUREMENT_KEY_TO_JAPANESE)
한국어/영문 측정 키를 일본어로 변환:

| 원본 키 (한국어) | 원본 키 (영문) | 일본어 키 |
|-----------------|---------------|----------|
| 어깨, 어깨 너비 | shoulder | 肩幅 |
| 가슴, 가슴 너비 | chest | 胸囲 |
| 총장, 총기장 | total_length | 着丈 |
| 팔길이, 소매길이 | sleeve_length | 袖丈 |
| 허리, 허리 너비 | waist | ウエスト |
| 엉덩이 | hip | ヒップ |
| 밑위 | rise | 股上 |
| 안기장 | inseam | 股下 |
| 가로 | width | 横 |
| 세로 | - | 縦 |
| 높이 | height | 高さ |
| 두께 | depth | マチ |

### 2.6 너비→둘레 변환 키 (x2 계산)
```python
MEASUREMENT_KEYS_NEED_DOUBLE = {
    '가슴', '가슴 너비', '가슴너비', '가슴단면', 'chest',
    '허벅지', '허벅지 너비', '허벅지너비', '허벅지단면', 'thigh',
    '밑단', '밑단 너비', '밑단너비', '밑단단면', 'hem'
}
```
- 이 키들의 값은 자동으로 x2 계산됨 (단면 → 둘레)

---

## 3. 유틸리티 함수 (라인 192-500)

### 3.1 `sanitize_text(text)` - 텍스트 정제
**목적**: 바이마 API가 거부하는 특수문자 제거

**처리 순서**:
1. NFD 정규화로 악센트 분리 (e.g., `é` → `e` + `´`)
2. Mn(악센트 기호) 카테고리 문자 제거
3. 특수 기호 치환:
   - `'` `'` → `'`
   - `"` `"` → `"`
   - `–` `—` → `-`
   - `™` → `(TM)`
   - `®` → `(R)`
   - `©` → `(C)`

### 3.2 `extract_numeric_value(text)` - 숫자 추출
```python
"45cm 전후" → "45.0"
"720g 전후" → "720.0"
"54.5cm" → "54.5"
```

### 3.3 `convert_measurements_to_details(...)` - 측정값 변환
**입력**: raw measurements 딕셔너리
**출력**: BUYMA options.details 형식

```python
# 입력
{"어깨 너비": "45cm 전후", "가슴 너비": "54cm 전후"}

# 출력
[{"key": "肩幅", "value": "45.0"}, {"key": "胸囲", "value": "108.0"}]
```

**처리 로직**:
1. 키 → 일본어 변환 (MEASUREMENT_KEY_TO_JAPANESE)
2. 카테고리별 허용 키 필터링 (size_details2.csv)
3. 숫자값 추출
4. 너비→둘레 변환 (해당 키만 x2)

### 3.4 `convert_krw_to_jpy(krw_price)` - 원화→엔화 변환
```python
jpy_price = int(krw_price * EXCHANGE_RATE_KRW_TO_JPY)
jpy_price = ((jpy_price + 50) // 100) * 100  # 100엔 단위 반올림
return max(jpy_price, MIN_PRICE_JPY)         # 최소 500엔
```

### 3.5 `generate_reference_number()` - 관리번호 생성
```python
return str(uuid.uuid4())
# 예: "550e8400-e29b-41d4-a716-446655440000"
```

### 3.6 `format_buyma_product_name(...)` - 상품명 생성
```python
# 출력 형식
"送料・関税込 | BURBERRY | Mid-length wool car coat 8095541"
```

### 3.7 `generate_product_comments(...)` - 상품 설명 생성
**최대 3000자 제한**

생성 내용:
1. 브랜드 및 상품 정보
2. 모델번호
3. 카테고리 정보
4. 사이즈 옵션
5. 구매 안내 (정품 보장, 한국 발송 등)
6. 주의사항 (색상 차이, 실측 오차 등)

---

## 4. RawToAceConverter 클래스 (라인 506-1211)

### 4.1 초기화 및 캐시
```python
self._brand_mapping_cache = {}      # 브랜드 매핑
self._category_mapping_cache = {}   # 카테고리 매핑
self._shipping_config_cache = None  # 배송 설정
self._color_master_id_cache = {}    # 색상 마스터 ID
self._category_size_keys_cache = {} # 카테고리별 사이즈 키
```

### 4.2 `load_color_master_id_mapping()` - 색상 매핑 로드
**소스**: `colors.csv`

**매핑 예시**:
| 색상명 | 마스터 ID |
|--------|----------|
| WHITE, 화이트, 흰색 | (CSV에서 로드) |
| BLACK, 블랙, 검정 | (CSV에서 로드) |
| GREY, GRAY, 그레이, 회색 | (CSV에서 로드) |

### 4.3 `load_brand_mapping()` - 브랜드 매핑 로드
**SQL**:
```sql
SELECT mall_brand_name_en, mall_brand_name_ko, buyma_brand_id, buyma_brand_name
FROM mall_brands
WHERE mall_name = 'okmall' AND is_active = 1
```

### 4.4 `load_category_mapping()` - 카테고리 매핑 로드
**SQL**:
```sql
SELECT mc.full_path, mc.buyma_category_id, mc.depth1, mc.depth2, mc.depth3,
       bmcd.expected_shipping_fee
FROM mall_categories mc
LEFT JOIN buyma_master_categories_data bmcd ON mc.buyma_category_id = bmcd.buyma_category_id
WHERE mc.mall_name = 'okmall' AND mc.is_active = 1
```
- `expected_shipping_fee`: 카테고리별 예상 배송비 포함

### 4.5 `fetch_raw_data(...)` - 원본 데이터 조회
**조회 조건**:
- `upsert=False` (기본): 미변환 데이터만 (`ace_products`에 없는 것)
- `upsert=True`: 모든 데이터 (기존 데이터 업데이트용)
- `--brand`: 특정 브랜드만
- `--raw-id`: 특정 ID만
- `--limit`: 최대 건수

### 4.6 `convert_single_raw_to_ace(raw_data)` - 단일 변환
**출력 구조**:
```python
{
    'product': {                    # ace_products 테이블
        'raw_data_id': int,
        'reference_number': str,    # UUID
        'control': 'publish',
        'name': str,                # 바이마 상품명
        'comments': str,            # 상품 설명
        'brand_id': int,
        'category_id': int,
        'expected_shipping_fee': int,
        'original_price_krw': float,
        'purchase_price_krw': float,
        'original_price_jpy': int,
        'purchase_price_jpy': int,
        'price': 0,                 # 판매가 (최저가 수집기에서 결정)
        'reference_price': int,     # 정가 (엔화)
        'available_until': str,     # 구매 기한 (90일 후)
        'buying_area_id': str,
        'shipping_area_id': str,
        'buying_shop_name': str,    # "{브랜드}正規販売店"
        'colorsize_comments': str,  # 실측/혼용률 (한국어)
        'colorsize_comments_jp': None,  # 번역 후 채워짐
        ...
    },
    # 이미지는 image_collector_parallel.py에서 별도 수집
    'options': [...],               # ace_product_options 테이블
    'variants': [...]               # ace_product_variants 테이블
}
```

#### 4.6.1 ace_products 필드 출처

| 필드 | 출처 | 설명 |
|------|------|------|
| `raw_data_id` | `raw_data['id']` | raw_scraped_data PK |
| `source_site` | `raw_data['source_site']` | 기본값 'okmall' |
| `reference_number` | `generate_reference_number()` | UUID 자동 생성 |
| `control` | 고정값 | `'publish'` |
| `name` | `format_buyma_product_name()` | 브랜드 + 상품명 + 모델ID 조합 |
| `comments` | `generate_product_comments()` | 브랜드, 카테고리, 사이즈 등 조합 |
| `brand_id` | `mall_brands.buyma_brand_id` | 브랜드명으로 조회 |
| `brand_name` | `mall_brands.buyma_brand_name` | 브랜드명으로 조회 |
| `category_id` | `mall_categories.buyma_category_id` | 카테고리 경로로 조회 |
| `expected_shipping_fee` | `buyma_master_categories_data` | 카테고리별 예상 배송비 |
| `original_price_krw` | `raw_data['original_price']` | 원화 정가 |
| `purchase_price_krw` | `raw_data['raw_price']` | 원화 판매가 (매입가) |
| `original_price_jpy` | 계산 | `original_price_krw * 0.1` (100엔 단위 반올림) |
| `purchase_price_jpy` | 계산 | `purchase_price_krw / 9.2` |
| `price` | 고정값 | `0` (최저가 수집기에서 결정) |
| `reference_price` | 계산 | `original_price_jpy`와 동일 |
| `available_until` | 계산 | `현재일 + 90일` |
| `buying_area_id` | 고정값 | `'2002003000'` |
| `shipping_area_id` | 고정값 | `'2002003000'` |
| `buying_shop_name` | 계산 | `"{brand_name}正規販売店"` |
| `model_no` | `raw_data['model_id']` | 모델번호 |
| `theme_id` | 고정값 | `98` |
| `season_id` | `convert_season_to_id()` | 시즌 문자열 → ID 변환 (현재 모두 None) |
| `colorsize_comments` | `raw_json_data.measurements` + `composition` | 실측/혼용률 정보 조합 |
| `colorsize_comments_jp` | - | NULL (배치 번역에서 채움) |
| `duty` | 고정값 | `'included'` |
| `source_product_url` | `raw_data['product_url']` | 원본 상품 URL |
| `source_model_id` | `raw_data['model_id']` | 원본 모델번호 |
| `source_original_price` | `raw_data['original_price']` | 원본 정가 |
| `source_sales_price` | `raw_data['raw_price']` | 원본 판매가 |

#### 4.6.2 ace_product_options 필드 출처

| 필드 | 출처 | 설명 |
|------|------|------|
| `option_type` | 고정값 | `'color'` 또는 `'size'` |
| `value` | `raw_json_data.options[].color` 또는 `tag_size` | 단일사이즈는 'FREE'로 변환 |
| `master_id` | `colors.csv` 또는 `0` | 색상: CSV 매핑, 사이즈: 0 |
| `position` | 계산 | 동일 option_type 내 순번 |
| `details_json` | `raw_json_data.measurements[size]` | 사이즈별 측정값 (일본어 키로 변환) |
| `source_option_value` | `raw_json_data.options[].color` 또는 `tag_size` | 원본 값 |

**details_json 생성 로직**:
```
measurements[사이즈] → 각 측정 키를 일본어로 변환 → 숫자만 추출 → 너비→둘레 변환(x2)
```

#### 4.6.3 ace_product_variants 필드 출처

| 필드 | 출처 | 설명 |
|------|------|------|
| `color_value` | `raw_json_data.options[].color` | 색상값 (없으면 'FREE') |
| `size_value` | `raw_json_data.options[].tag_size` | 사이즈값 (단일사이즈는 'FREE') |
| `options_json` | 계산 | `[{type: 'color', value: ...}, {type: 'size', value: ...}]` |
| `stock_type` | `raw_json_data.options[].status` | `'in_stock'` → `'purchase_for_order'`, 그 외 → `'out_of_stock'` |
| `stocks` | 계산 | `purchase_for_order`면 1, 아니면 0 |
| `source_option_code` | `raw_json_data.options[].option_code` | 원본 옵션 코드 |
| `source_stock_status` | `raw_json_data.options[].status` | 원본 재고 상태 |

#### 4.6.4 데이터 흐름 다이어그램

```
raw_scraped_data
├── id ──────────────────────────────────────→ ace_products.raw_data_id
├── source_site ─────────────────────────────→ ace_products.source_site
├── brand_name_en ──┬─→ mall_brands 조회 ────→ ace_products.brand_id, brand_name
│                   └─→ format_buyma_product_name() → ace_products.name
├── product_name ───────→ format_buyma_product_name() → ace_products.name
├── model_id ───────────→ ace_products.model_no, source_model_id
├── category_path ──────→ mall_categories 조회 → ace_products.category_id
├── original_price ─────→ ace_products.original_price_krw, original_price_jpy
├── raw_price ──────────→ ace_products.purchase_price_krw, purchase_price_jpy
├── product_url ────────→ ace_products.source_product_url
│
└── raw_json_data (JSON)
    ├── options[] ──────→ ace_product_options (color/size)
    │   ├── color ──────→ value (color), master_id (colors.csv)
    │   ├── tag_size ───→ value (size)
    │   ├── status ─────→ ace_product_variants.stock_type
    │   └── option_code → ace_product_variants.source_option_code
    │
    ├── measurements ───→ ace_product_options.details_json (사이즈별)
    │                   → ace_products.colorsize_comments
    │
    ├── composition ────→ ace_products.colorsize_comments
    │
    └── season ─────────→ ace_products.season_id
```

### 4.7 옵션 처리 로직

**색상 옵션**:
```python
{
    'option_type': 'color',
    'value': '블랙',           # 한국어 원본 (배치 번역에서 처리)
    'master_id': 1,            # colors.csv에서 매핑
    'position': 1,
    'details_json': None,
    'source_option_value': '블랙'
}
```

**사이즈 옵션**:
```python
{
    'option_type': 'size',
    'value': 'M',
    'master_id': 0,
    'position': 1,
    'details_json': '[{"key": "肩幅", "value": "45.0"}, ...]',  # 측정값
    'source_option_value': 'M'
}
```

**단일 사이즈 변환**:
- `단일사이즈`, `단일 사이즈`, `단일`, `원사이즈`, `원 사이즈` → `FREE`

### 4.8 재고 타입 결정
```python
# 재고 있음 → 주문 후 매입
stock_type = 'purchase_for_order' if opt.get('status') == 'in_stock' else 'out_of_stock'
```

### 4.9 measurements 전용 사이즈 처리
- options에는 없고 measurements에만 있는 사이즈 자동 추가
- 해당 사이즈는 `out_of_stock` 상태로 저장
- 로그 출력: `"measurements에서 추가 사이즈 발견: XL (out_of_stock)"`

### 4.10 `save_ace_data(ace_data)` - 신규 저장
3개 테이블에 순차적 INSERT:
1. `ace_products` → `ace_product_id` 획득
2. `ace_product_options`
3. `ace_product_variants`

(이미지는 image_collector_parallel.py에서 ace_product_images에 별도 저장)

### 4.11 `update_ace_data(ace_data, existing_product)` - 업데이트
**업데이트 대상**:
- `ace_products`: colorsize_comments, colorsize_comments_jp만
- `ace_product_options`: 전체 삭제 후 재생성
- `ace_product_variants`: 전체 삭제 후 재생성

### 4.12 `run_conversion(...)` - 변환 실행
**실행 흐름**:
```
매핑 데이터 로드
    ↓
raw 데이터 조회
    ↓
각 raw 데이터 루프 ─────────────────┐
    ↓                               │
    기존 ace_product 존재 여부 확인  │
    ↓                               │
    존재 & upsert=True → 업데이트   │
    존재 & upsert=False → 스킵      │
    미존재 → 신규 INSERT            │
←──────────────────────────────────┘
    ↓
배치 번역 실행 (dry_run=False일 때)
    ↓
결과 반환
```

---

## 5. CLI 옵션 (라인 1213-1245)

| 옵션 | 타입 | 설명 |
|-----|------|------|
| `--dry-run` | flag | DB 저장 없이 테스트 |
| `--limit` | int | 처리할 최대 레코드 수 |
| `--brand` | str | 특정 브랜드만 처리 |
| `--raw-id` | int | 특정 raw_scraped_data ID만 처리 |
| `--upsert` | flag | 기존 데이터도 업데이트 |

### 5.1 `--raw-id` 옵션

**참조 테이블/컬럼**: `raw_scraped_data.id` (PK)

```sql
-- 생성되는 WHERE 조건
WHERE r.id = :raw_id
```

**동작**:
- 지정된 ID의 raw_scraped_data 레코드 1건만 처리
- `--upsert` 없이 사용 시: 이미 변환된 데이터면 스킵
- `--upsert`와 함께 사용 시: 이미 변환된 데이터도 업데이트

**사용 예시**:
```bash
# raw_scraped_data.id = 12345인 레코드만 처리
python raw_to_ace_converter.py --raw-id 12345

# 이미 변환된 데이터도 강제 업데이트
python raw_to_ace_converter.py --raw-id 12345 --upsert
```

### 5.2 `--brand` 옵션

**참조 테이블/컬럼**: `raw_scraped_data.brand_name_en`

```sql
-- 생성되는 WHERE 조건 (대소문자 무관)
WHERE UPPER(r.brand_name_en) = :brand
```

**동작**:
- 입력값은 자동으로 대문자로 변환되어 비교
- 예: `--brand burberry` → `UPPER('burberry')` = `'BURBERRY'`

**사용 예시**:
```bash
# BURBERRY 브랜드만 처리 (대소문자 무관)
python raw_to_ace_converter.py --brand BURBERRY
python raw_to_ace_converter.py --brand burberry  # 동일하게 동작
```

### 5.3 `--limit` 옵션

**참조**: SQL `LIMIT` 절

```sql
-- 생성되는 LIMIT 절
ORDER BY r.id LIMIT :limit
```

**동작**:
- `raw_scraped_data.id` 오름차순으로 정렬 후 상위 N건만 처리
- 다른 옵션과 조합 가능

**사용 예시**:
```bash
# 미변환 데이터 중 상위 100건만 처리
python raw_to_ace_converter.py --limit 100

# GUCCI 브랜드 중 상위 50건만 처리
python raw_to_ace_converter.py --brand GUCCI --limit 50
```

### 5.4 `--dry-run` 옵션

**동작**:
- 변환 로직은 실행하되 **DB에 저장하지 않음**
- 로그 출력은 정상적으로 수행
- 배치 번역도 실행하지 않음

**사용 예시**:
```bash
# 변환 결과만 확인 (DB 저장 안함)
python raw_to_ace_converter.py --brand PRADA --limit 10 --dry-run
```

### 5.5 `--upsert` 옵션

**동작 흐름**:

```
┌─────────────────────────────────────────────────────────────┐
│  --upsert 없음 (기본 모드)                                   │
├─────────────────────────────────────────────────────────────┤
│  1. raw_scraped_data에서 조회                                │
│     WHERE a.id IS NULL  ← ace_products에 없는 것만           │
│  2. 이미 변환된 데이터 → 스킵 (skipped 카운트 증가)           │
│  3. 미변환 데이터 → INSERT (success 카운트 증가)             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  --upsert 있음                                               │
├─────────────────────────────────────────────────────────────┤
│  1. raw_scraped_data에서 조회                                │
│     (ace_products 존재 여부 무관하게 전체 조회)               │
│  2. 이미 변환된 데이터 → UPDATE (updated 카운트 증가)        │
│  3. 미변환 데이터 → INSERT (success 카운트 증가)             │
└─────────────────────────────────────────────────────────────┘
```

**UPDATE 대상 (update_ace_data 함수)**:

| 테이블 | 동작 | 업데이트 필드 |
|--------|------|--------------|
| `ace_products` | UPDATE | `colorsize_comments`, `colorsize_comments_jp`, `updated_at` |
| `ace_product_options` | DELETE → INSERT | 전체 삭제 후 재생성 |
| `ace_product_variants` | DELETE → INSERT | 전체 삭제 후 재생성 |

**유지되는 필드** (업데이트 안함):
- `reference_number` (바이마 관리번호)
- `buyma_product_id` (바이마 상품 ID)
- `name`, `comments`, `brand_id`, `category_id`
- `price`, `buyma_lowest_price` 등 가격 관련 필드

**사용 예시**:
```bash
# GUCCI 브랜드의 기존 데이터도 업데이트 (옵션/재고 갱신)
python raw_to_ace_converter.py --brand GUCCI --upsert

# 특정 상품의 옵션/재고만 갱신
python raw_to_ace_converter.py --raw-id 12345 --upsert
```

### 5.6 옵션 조합 예시

| 명령어 | 동작 |
|--------|------|
| `--brand GUCCI` | GUCCI 미변환 데이터 전체 처리 |
| `--brand GUCCI --limit 10` | GUCCI 미변환 데이터 중 10건만 |
| `--brand GUCCI --upsert` | GUCCI 전체 데이터 (기존 포함) 업데이트 |
| `--raw-id 123` | ID=123인 레코드 1건 (미변환만) |
| `--raw-id 123 --upsert` | ID=123인 레코드 강제 업데이트 |
| `--limit 100 --dry-run` | 미변환 100건 테스트 (저장 안함) |

---

## 6. 사용 예시

```bash
# 전체 미변환 데이터 처리
python raw_to_ace_converter.py

# 특정 브랜드만 처리
python raw_to_ace_converter.py --brand BURBERRY

# 최대 100건만 처리
python raw_to_ace_converter.py --limit 100

# 테스트 실행 (DB 저장 안함)
python raw_to_ace_converter.py --dry-run

# 특정 raw_id만 처리
python raw_to_ace_converter.py --raw-id 12345

# 기존 데이터 업데이트 (colorsize_comments, options, variants)
python raw_to_ace_converter.py --brand GUCCI --upsert

# 조합
python raw_to_ace_converter.py --brand PRADA --limit 10 --dry-run
```

---

## 7. DB 테이블 의존성

### 읽기 (SELECT)
| 테이블 | 용도 |
|--------|------|
| `raw_scraped_data` | 원본 데이터 |
| `mall_brands` | 브랜드 매핑 |
| `mall_categories` | 카테고리 매핑 |
| `buyma_master_categories_data` | 카테고리별 배송비 |
| `shipping_config` | 기본 배송 설정 |
| `ace_products` | 기존 변환 데이터 확인 |

### 쓰기 (INSERT/UPDATE)
| 테이블 | 용도 |
|--------|------|
| `ace_products` | 상품 기본 정보 |
| `ace_product_options` | 색상/사이즈 옵션 |
| `ace_product_variants` | 옵션 조합별 재고 |

> `ace_product_images`는 image_collector_parallel.py에서 별도 처리

---

## 8. 외부 파일 의존성

| 파일 | 용도 |
|------|------|
| `.env` | DB 연결 정보 |
| `colors.csv` | 색상명 → 마스터 ID 매핑 |
| `size_details2.csv` | 카테고리별 허용 사이즈 키 |
| `convert_to_japanese_gemini.py` | 배치 번역 함수 |

---

## 9. 주요 특이사항

1. **이미지 별도 처리**: 이 스크립트에서는 이미지를 처리하지 않음 (image_collector_parallel.py에서 ace_product_images 테이블에 직접 저장)
2. **판매가(price) 초기값 0**: 최저가 수집기(buyma_lowest_price_collector.py)에서 결정
3. **한국어 원본 저장**: 색상, 사이즈, colorsize_comments는 한국어로 저장 후 배치 번역
4. **measurements 전용 사이즈**: options에 없어도 measurements에 있으면 out_of_stock으로 추가
5. **너비→둘레 자동 계산**: 가슴, 허벅지, 밑단 등 특정 키는 x2 계산
6. **카테고리별 사이즈 키 필터링**: 바이마 카테고리에 허용된 측정 키만 저장
7. **배치 번역 자동 실행**: 변환 완료 후 convert_to_japanese_gemini.py 호출
8. **UPSERT 모드**: `--upsert` 시 colorsize_comments, options, variants만 업데이트 (reference_number 유지)
