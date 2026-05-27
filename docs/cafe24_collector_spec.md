# Cafe24 Collector 개발 명세서

새로운 cafe24 기반 쇼핑몰의 collector 스크립트를 작성할 때 따라야 할 규약을 정리한다.

**참고할 기존 구현 (가장 비슷한 두 케이스)**
- `nextzennpack/nextzennpack_collector.py` — wisacdn CDN 이미지 처리
- `labellusso/labellusso_collector.py` — ThumbImage (small URL 그대로) 이미지 처리

---

## 0. 큰 그림 — 이 스크립트의 역할

이 프로젝트의 데이터 파이프라인은 6단계다.

```
Collector ──► Converter ──► Price ──► Translate ──► Image ──► Register
   ↑              ↑           ↑           ↑           ↑          ↑
 (지금 만드는    공용 스크립트  공용       공용       공용      공용
  스크립트)     수정 안 함     수정 안 함  수정 안 함  수정 안 함 수정 안 함
```

Collector의 책임은 단 하나:

> **수집 사이트의 HTML을 긁어서 `raw_scraped_data` 테이블에 정해진 스키마로 INSERT(또는 UPDATE)** 하는 것.

뒤 단계는 모두 공용 스크립트가 처리한다. 그래서 Collector가 어긋난 데이터를 넣으면 뒤 전체가 망가진다. **이 문서가 정의하는 "출력 인터페이스(섹션 4)"를 정확히 지키는 것이 가장 중요하다.**

---

## 1. 사전 조건 — 코딩 시작 전 확인

### 1-1. 대상 사이트가 정말 cafe24인지 확인
- 페이지 소스에 `xans-element-` 클래스가 보임
- 상품 목록 URL이 `/product/list.html?cate_no=N` 형태
- 상품 상세 URL이 `/product/detail.html?product_no=N` 형태
- HTML에 `product_sale_price`, `product_price` JS 변수가 있음

위 4가지 중 3개 이상 맞으면 cafe24 기반이다. 아니면 이 명세는 적용하지 말 것.

### 1-2. DB 사전 등록 (코드 짜기 전에 끝내야 함)

새 사이트의 `source_site` 이름(영문 소문자, 예: `newsite`)을 정한 뒤 아래 3가지를 DB에 넣는다.

**(a) `mall_sites` — 사이트 자체 등록 (1회)**

```sql
INSERT INTO mall_sites (site_name, has_own_images, is_active)
VALUES ('newsite', 1, 1);
```

`has_own_images=1` → 사이트가 자체 상품 이미지를 보유 (cafe24 사이트는 모두 1).

**(b) `mall_brands` — 수집할 브랜드 목록 사전 등록**

cafe24의 "브랜드 카테고리 번호(`cate_no`)"를 `mall_brand_no`에 넣는다. 사이트의 브랜드 목록 페이지에서 손으로 또는 별도 스크립트로 수집해서 INSERT.

```sql
INSERT INTO mall_brands
  (mall_name, raw_brand_name, mall_brand_name_en, mall_brand_no, is_active)
VALUES
  ('newsite', '보테가베네타', 'BOTTEGA VENETA', '5595', 1);
```

- `raw_brand_name` — 사이트에 표시되는 한글/원어 이름 (수집한 brand_name과 매칭하는 키)
- `mall_brand_name_en` — 영문 이름 (없으면 한글 그대로 넣어도 되지만, 가능하면 영문)
- `mall_brand_no` — cate_no (collector가 리스트 페이지 URL 만들 때 사용)
- `buyma_brand_id` — 처음엔 NULL. 나중에 카테고리/브랜드 매핑 도구로 채움

**(c) `mall_categories` — 자동 INSERT됨 (수동 등록 불필요)**

수집 도중 만나는 `category_path`가 `mall_categories`에 없으면 collector가 자동으로 `buyma_category_id=NULL`로 INSERT 한다. 운영자가 나중에 buyma 카테고리에 수동 매핑한다.

---

## 2. 파일 구조

```
<source_site>/
  ├── <source_site>_collector.py        ← 이번에 작성할 파일
  ├── <source_site>_collect.md          ← 수집 대상 URL/셀렉터 메모 (선택)
  ├── index.html / list.html / product.html  ← 분석용 샘플 HTML (선택, .gitignore 대상)
  └── stock_price_synchronizer_<source_site>.py  ← 별도 작업 (이번엔 안 만듦)
```

스크립트는 프로젝트 루트가 아닌 **사이트 전용 디렉토리**에 둔다. 임포트는 환경변수 로딩 시 `os.path.dirname(os.path.dirname(__file__))` 패턴으로 루트 `.env`를 찾는다.

---

## 3. DB 입력 인터페이스 (Collector가 읽는 것)

### 3-1. 브랜드 목록 조회

```python
SELECT mall_brand_name_en, mall_brand_no
FROM mall_brands
WHERE mall_name = '<source_site>'
  AND is_active = 1
```

- `mall_brand_no`가 곧 cafe24의 `cate_no`다 — 리스트 URL을 만들 때 사용
- `is_active=0`은 운영자가 수집 제외한 브랜드 → 절대 수집하지 말 것
- `--brand <NAME>` 인자가 들어오면 `UPPER(mall_brand_name_en) = UPPER(:brand)` 필터 추가

### 3-2. 등록 완료 상품 조회 (`--skip-existing`용)

```python
SELECT r.mall_product_id
FROM raw_scraped_data r
INNER JOIN ace_products a ON r.id = a.raw_data_id
WHERE r.source_site = '<source_site>'
  AND a.is_published = 1
```

이미 buyma에 등록되어 운영 중인 상품의 `mall_product_id`를 set으로 받아서, 그 상품은 상세 페이지 fetch를 건너뛴다 (속도/봇 감지 이슈 둘 다 완화).

---

## 4. DB 출력 인터페이스 — `raw_scraped_data` (★ 가장 중요)

이 스키마는 **공용 converter (`kasina/raw_to_converter_kasina.py`)와의 계약**이다. 컬럼 이름·타입·의미를 한 글자도 바꾸면 안 된다.

### 4-1. INSERT SQL (반드시 이 패턴)

```sql
INSERT INTO raw_scraped_data
  (source_site, mall_product_id, brand_name_en,
   product_name, p_name_full, model_id, category_path,
   original_price, raw_price, stock_status, raw_json_data, product_url)
VALUES
  (:source_site, :mall_product_id, :brand_name_en,
   :product_name, :p_name_full, :model_id, :category_path,
   :original_price, :raw_price, :stock_status, :raw_json_data, :product_url)
ON DUPLICATE KEY UPDATE
  brand_name_en  = VALUES(brand_name_en),
  product_name   = VALUES(product_name),
  p_name_full    = VALUES(p_name_full),
  model_id       = VALUES(model_id),
  category_path  = VALUES(category_path),
  original_price = VALUES(original_price),
  raw_price      = VALUES(raw_price),
  stock_status   = VALUES(stock_status),
  raw_json_data  = VALUES(raw_json_data),
  product_url    = VALUES(product_url),
  updated_at     = NOW()
```

UNIQUE KEY는 `(source_site, mall_product_id)`. 같은 상품을 다음 날 다시 수집하면 UPDATE 된다.

### 4-2. 컬럼별 채움 규칙

| 컬럼 | 타입 | 채움 규칙 |
|---|---|---|
| `source_site` | varchar(50) | 사이트 식별자 (`'newsite'`). 코드 상단 상수로 정의 |
| `mall_product_id` | varchar(100) | cafe24 `product_no` (string으로) |
| `brand_name_en` | varchar(100) | `mall_brands.mall_brand_name_en` 그대로. 비어있으면 한글 이름 fallback |
| `product_name` | varchar(255) | 사이트의 상품명. **`[브랜드]` 접두어가 있으면 정규식으로 제거** (`re.sub(r'^\[.*?\]\s*', '', name)`) |
| `p_name_full` | text | 정제 전 원본 상품명. 보통 `product_name`과 동일하게 채워도 됨 |
| `model_id` | varchar(100) | **★ 모델번호. 없으면 그 상품은 INSERT하지 말고 스킵.** 빈 문자열 NULL 모두 금지 |
| `category_path` | varchar(255) | 사이트 내 카테고리 경로 (예: `"가방 > 크로스백"`). 깊이는 사이트 구조에 맞춰 |
| `original_price` | decimal(15,2) | 정가/소비자가 (KRW, 정수 그대로) |
| `raw_price` | decimal(15,2) | 판매가/할인가 (KRW). 둘이 같으면 같은 값 넣음 |
| `stock_status` | varchar(20) | `'in_stock'` 또는 `'out_of_stock'`. 옵션 중 하나라도 `in_stock`이면 `in_stock` |
| `raw_json_data` | longtext | JSON 문자열. 스키마는 섹션 4-3 참조 |
| `product_url` | text | 상세 페이지 절대 URL |

### 4-3. `raw_json_data` JSON 스키마 (converter가 파싱하는 구조)

```json
{
  "color": "베이지계열",
  "item_type": "크로스백(CROSS BAG)",
  "origin": "이탈리아",
  "material": "LAMB LEATHER(양가죽)",
  "composition": {
    "원산지": "이탈리아",
    "소재": "LAMB LEATHER(양가죽)",
    "구성품": "제품, 북렛, 더스트백"
  },
  "options": [
    {"color": "베이지계열", "tag_size": "FREE", "option_code": "...", "status": "in_stock"},
    {"color": "베이지계열", "tag_size": "S",    "option_code": "...", "status": "out_of_stock"}
  ],
  "measurements": {
    "UNI": {"가로": "18.5CM", "세로": "12CM", "폭": "4.5CM"}
  },
  "images": [
    "https://...wisacdn.com/brand/.../1.jpg",
    "https://...wisacdn.com/brand/.../2.jpg"
  ],
  "cate_no": "5595",
  "scraped_at": "2026-05-27T11:00:00"
}
```

**필드 의미와 규칙:**

- `options[].status` — 반드시 `'in_stock'` 또는 `'out_of_stock'` 정확히 이 두 문자열
- `options[].tag_size` — 사이트에 보이는 사이즈 표기 그대로 (`'S'`, `'M'`, `'XL'`, `'FREE'`, `'250'`...).
  - "단일사이즈", "원사이즈", "ONESIZE" → `'FREE'`로 통일
  - "S [품절]" → `'S'`로 정규화 (품절 여부는 `status`에 표현)
- `options[].option_code` — cafe24 옵션 value 그대로 (재고 동기화 시 매칭 키)
- `measurements` — `{사이즈명: {부위명: 값}}` 형태. 데이터 없으면 `{}`
- `images` — 절대 URL. `//cdn.../img.jpg` 형태면 `https:` 붙여서 정규화

---

## 5. 공통 수집 패턴 (모든 cafe24 collector가 똑같이)

### 5-1. URL 패턴

```python
BASE_URL = 'https://newsite.com'
LIST_URL = f'{BASE_URL}/product/list.html?cate_no={cate_no}&page={page}'
DETAIL_URL = f'{BASE_URL}/product/detail.html?product_no={product_no}&cate_no={cate_no}&display_group=1'
```

### 5-2. SessionManager (봇 감지 우회) — `nextzennpack_collector.py:120-198` 그대로 가져다 써라

핵심 동작:
- 30 요청마다 새 세션 생성 + 메인 페이지 방문 (쿠키 갱신)
- 랜덤 브라우저 프로필 (Chrome 120/121 Windows + Chrome 120 macOS 3종)
- 첫 요청은 `Referer: google.com`으로, 이후는 `Referer: BASE_URL`로
- 403 응답 → 즉시 `is_blocked = True` 설정 후 중지
- 타임아웃 5회 연속 → `is_blocked = True`
- 요청 간 0.3~0.8초 랜덤 sleep

이 모듈은 **그대로 복사**해서 `BASE_URL`, `SOURCE_SITE`만 바꾼다. 손대지 말 것.

### 5-3. 가격 추출 — JS 변수에서 가져온다 (HTML 텍스트 신뢰 X)

```python
sale_match = re.search(r"product_sale_price\s*=\s*(\d+)", html)
price_match = re.search(r"product_price\s*=\s*'(\d+)'", html)
```

리스트 페이지 가격은 할인 시 정확하지 않을 수 있다 → 상세 페이지 JS 변수 우선. JS 변수 없으면 `#span_product_price_custom` 등 fallback.

### 5-4. 모델번호 추출

cafe24는 `table.detail` 안에 `<tr><th>모 델 명 (MODEL)</th><td>730848 VMAY1 7671</td></tr>` 형태.

```python
# 1순위: table.detail의 th 텍스트에 '모델명' 또는 'MODEL'
# 2순위: buy-scroll-box 영역의 th='모델' 행
# 3순위: 상품명 끝의 괄호 (예: "상품명 (ABC-123)")
```

세 곳 다 비면 그 상품은 **스킵 (return None)**. 절대 빈 `model_id`로 INSERT하지 말 것 — dead row가 되어 뒤 단계가 무한히 실패한다.

### 5-5. 옵션/재고 추출

```python
# 우선: select#product_option_id1, select#product_option_id2
# fallback: JS 변수 option_stock_data (JSON)
```

`option_stock_data`는 옵션이 1개일 때 dict, 여러 개일 때 list로 온다 → `isinstance(x, dict)` 분기 필수.

`select#product_option_id1` 의 option text가 `----` 같은 구분선이면 스킵.

### 5-6. 실측 사이즈 (`table.size`)

첫 번째 `<tr>`이 header (SIZE, 가로, 세로, ...), 나머지가 데이터.
"EU / IT 40" → "40"으로 정규화 (옵션 사이즈와 키 통일).
값이 `-`이면 `{}`에서 제외.

### 5-7. 배치 저장

상세 페이지 10개마다 `save_to_database(batch_data)` 호출. 마지막에 잔여분도 flush. 한 상품씩 commit하면 너무 느리다.

---

## 6. 사이트별로 다른 부분 — 분기 결정 필요

### 6-1. ★ 이미지 처리 — 이게 가장 다르다

cafe24 상품 페이지의 썸네일/상세 이미지 위치는 사이트 운영자가 어떻게 설정했냐에 따라 천차만별이다. 새 사이트는 다음 셋 중 하나로 판단한다.

**케이스 A — ThumbImage가 살아있는 사이트 (labellusso 패턴)**
```python
thumb_area = soup.select_one('.xans-product-addimage')
for img in thumb_area.select('img.ThumbImage'):
    src = img.get('src', '')  # 보통 /web/product/small/... 870x870
```
실제 브라우저로 ThumbImage URL을 열어서 **이미지가 뜨면** + **해상도가 800px 이상**이면 이 패턴 사용.

**케이스 B — ThumbImage가 죽어있고 본문 CDN을 써야 하는 사이트 (nextzennpack 패턴)**
```python
detail_area = soup.select_one('#prdDetail')
for img in detail_area.select('img'):
    src = img.get('ec-data-src', '') or img.get('src', '')  # lazy loading
    if 'wisacdn.com/brand/' not in src:  # 사이트마다 CDN 호스트 다름
        continue
```
ThumbImage URL이 **404거나 220px 같은 저해상도**면 이 패턴. CDN 호스트(wisacdn 등)는 그 사이트 본문 이미지를 직접 보고 결정한다.

**케이스 C — JSON-LD fallback**
위 둘 다 안 되면 `<script type="application/ld+json">`에서 `Product.image` 배열을 fallback으로 사용 (labellusso 567-578줄 참고).

**판단 절차 (반드시 손으로):**
1. 새 사이트 상품 1개의 상세 페이지를 브라우저에서 열기
2. ThumbImage URL을 새 탭에 열어본다 → 정상 표시 & 800px+ 인가?
3. Yes → 케이스 A로 구현
4. No → `#prdDetail` 안의 `<img>` URL을 본다 → CDN 호스트가 무엇인지 확인 → 케이스 B로 구현
5. 둘 다 안 되면 케이스 C

### 6-2. 카테고리 구조

서브카테고리가 있는 사이트는 브랜드 페이지 사이드바(`ul.menuCategory`)에서 추출 (`nextzennpack_collector.py:274-316`). 없는 사이트는 브랜드 전체 페이지 1개만 순회.

서브카테고리 순회 시에는 `product_no` 기준 dedup 필수 — 같은 상품이 여러 카테고리에 노출되는 경우 있음.

### 6-3. 모델번호 위치

`table.detail`의 `<th>` 텍스트가 "모델명", "MODEL", "Model No.", "품번" 등 사이트마다 다를 수 있다. 정규식으로 여러 패턴 OR 매칭.

---

## 7. 명령줄 인터페이스 (반드시 지원)

```bash
python <source_site>_collector.py                     # 전체 수집
python <source_site>_collector.py --brand "BRAND"     # 특정 브랜드만
python <source_site>_collector.py --limit 10          # 브랜드당 최대 10개
python <source_site>_collector.py --dry-run           # DB 저장 없이 흐름만 확인
python <source_site>_collector.py --skip-existing     # 등록 완료 상품 스킵 (운영용)
```

`run_daily_multisource.py` 가 이 4개 인자를 가정하고 호출하므로 인자 이름·동작 반드시 일치.

---

## 8. 로그 포맷

```python
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
```

브랜드 진입 시: `>>> [3/45] 브랜드: BOTTEGA VENETA (cate_no=5595)`
상품 수집 시: `[12/120] 730848-VMAY1 | 4,200,000원 | 가방 > 크로스백 | 보테가베네타 미니 백`
스킵 시: `[12/120] SKIP (no model_id) | 상품명...`

---

## 9. 작동 검증 체크리스트

코드 작성 후 PR 올리기 전에 아래를 직접 확인할 것.

### 9-1. dry-run 흐름 검증

```bash
python <source_site>_collector.py --brand "<한 브랜드>" --limit 3 --dry-run
```

- [ ] 브랜드 1개의 상품 3개를 끝까지 처리 (에러 없이)
- [ ] 로그에 `model_id`, 가격, 카테고리 경로가 모두 표시됨
- [ ] `SKIP (no model_id)`이 너무 많지 않음 (3개 중 0~1개 정도가 정상)

### 9-2. 실제 1건 DB 적재 + raw_json 검증

```bash
python <source_site>_collector.py --brand "<한 브랜드>" --limit 1
```

DB 검증 쿼리 (꼭 직접 돌려볼 것):

```sql
SELECT mall_product_id, brand_name_en, model_id, raw_price, stock_status,
       LENGTH(raw_json_data) AS json_len, product_url
FROM raw_scraped_data
WHERE source_site = '<source_site>'
ORDER BY id DESC LIMIT 1;

-- raw_json_data 의 옵션/이미지 확인
SELECT JSON_EXTRACT(raw_json_data, '$.options'),
       JSON_EXTRACT(raw_json_data, '$.images'),
       JSON_EXTRACT(raw_json_data, '$.measurements')
FROM raw_scraped_data
WHERE source_site = '<source_site>'
ORDER BY id DESC LIMIT 1;
```

체크:
- [ ] `model_id`가 NULL/빈문자열이 아님
- [ ] `images` 배열에 1개 이상, 모두 `https://`로 시작
- [ ] 이미지 URL을 브라우저로 열어서 **실제 상품 이미지가 표시됨 & 해상도 800px+**
- [ ] `options[].status`가 `'in_stock'` 또는 `'out_of_stock'` (오타 금지)

### 9-3. converter 연결 검증

```bash
python kasina/raw_to_converter_kasina.py --source-site <source_site> --limit 1
```

- [ ] 에러 없이 통과 → `ace_products`에 row 1개 생성됨
- [ ] `SELECT * FROM ace_products WHERE source_site='<source_site>' ORDER BY id DESC LIMIT 1;` 으로 확인

여기까지 통과하면 뒤 단계(price/translate/image/register)는 공용 코드가 알아서 한다.

### 9-4. 봇 감지/안정성

- [ ] `--brand` 없이 전체 수집을 1회 돌려서 마지막까지 완주 (중간에 403/타임아웃으로 죽지 않음)
- [ ] 30요청마다 `[세션] 새 세션 시작` 로그가 보임
- [ ] 동일 사이트를 짧은 간격으로 2회 돌렸을 때 차단되지 않음 (간격을 두고 테스트)

---

## 10. 자주 빠지는 함정

| 함정 | 결과 | 예방 |
|---|---|---|
| `model_id` 없는 상품을 빈 문자열로 INSERT | dead row 누적, 뒤 단계 무한 실패 | `if not model_id: return None`으로 스킵 |
| `[브랜드명]` 접두어를 product_name에 남김 | converter가 일본어 번역 시 브랜드명 중복 | `re.sub(r'^\[.*?\]\s*', '', name)` 정규화 |
| ThumbImage URL이 220px인데 그대로 사용 | buyma 등록 후 저화질로 노출 → 클레임 | 6-1 케이스 B/C로 대체 |
| `option_stock_data` 단일옵션 dict 케이스 미처리 | TypeError | `isinstance(x, dict): x = [x]` |
| 가격을 리스트 페이지에서 가져옴 | 할인가가 부정확 | 상세 페이지 JS 변수 사용 |
| WARP/IP 차단된 상태에서 수집 | 403 즉시 | buyma 차단 이슈와 별개로, 사이트별로 차단 정책 다름. 차단되면 IP 변경 |
| `mall_brands.is_active=0`인 브랜드 수집 | 운영자가 명시적으로 제외한 브랜드를 다시 등록함 | WHERE 절에 반드시 `is_active=1` |
| 한 상품씩 commit | 수집 시간 10배 느려짐 | 10개 batch로 묶어서 commit |

---

## 11. 참고 코드 위치 (잘라쓰기 좋은 부분)

| 기능 | 파일:라인 |
|---|---|
| SessionManager 전체 | `nextzennpack/nextzennpack_collector.py:120-198` |
| 리스트 페이지 파싱 | `nextzennpack/nextzennpack_collector.py:204-271` |
| 서브카테고리 추출 | `nextzennpack/nextzennpack_collector.py:274-316` |
| 상세 페이지 파싱 (모델/옵션/실측) | `nextzennpack/nextzennpack_collector.py:338-509` |
| 이미지 케이스 A (ThumbImage) | `labellusso/labellusso_collector.py:557-580` |
| 이미지 케이스 B (CDN 본문) | `nextzennpack/nextzennpack_collector.py:483-498` |
| `raw_scraped_data` UPSERT | `nextzennpack/nextzennpack_collector.py:627-655` |
| `convert_to_raw_data` 변환기 | `nextzennpack/nextzennpack_collector.py:524-591` |
| 메인 루프 (브랜드→카테고리→상세→저장) | `nextzennpack/nextzennpack_collector.py:662-859` |
| 공용 메모리 노트 | `~/.claude/projects/<...>/memory/cafe24_collector_pattern.md` |

---

## 12. PR 제출 시 포함할 것

1. `<source_site>/<source_site>_collector.py` — 본 코드
2. `<source_site>/<source_site>_collect.md` — 분석한 URL/셀렉터 메모 (선택이지만 권장)
3. 9-2의 DB 검증 쿼리 결과 (raw_json 1건 풀 dump)
4. 9-3의 converter 통과 결과 (`ace_products` 1건 row)
5. 9-4의 전체 수집 1회 완주 로그 (앞 50줄 + 뒤 50줄)
6. `mall_sites` / `mall_brands` 사전 등록한 SQL 또는 캡처

위 6개 없이는 리뷰 시작하지 않는다.
