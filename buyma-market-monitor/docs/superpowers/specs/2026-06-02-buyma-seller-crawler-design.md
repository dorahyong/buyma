# BUYMA 한국 셀러 수집 크롤러 설계

## 목적

BUYMA 카테고리 목록 페이지(`https://www.buyma.com/r/-A2002003000_{N}/`)를 순회하며 등장하는 셀러를 발견하고, 각 셀러 페이지를 방문해 한국 셀러(또는 SHOP)만 필터링하여 JSON으로 누적 저장한다.

향후 셀러별 sales 페이지에서 추가 데이터를 파싱하는 확장이 예정되어 있어, 셀러 페이지 방문 URL은 `https://www.buyma.com/buyer/{id}/sales_1.html` 기준으로 한다.

## 스코프

**포함:**
- 카테고리 목록 페이지의 자동 페이지네이션 감지 (최초 1회) 및 config 저장
- 목록 페이지 순회를 통한 seller_id 수집
- 셀러 페이지 방문 및 필터링 (한국 personal shopper / shop만)
- 추출 데이터의 JSON 누적 저장 (전체 셀러 매 실행마다 재방문하여 통계 갱신)
- 에러 재시도 및 로깅

**제외:**
- 셀러별 sales 페이지의 상품 데이터 파싱 (다음 단계)
- 스케줄링 (cron 등은 OS 레벨에서 처리)
- 알림/대시보드/UI

## 기술 스택

- **언어:** Python 3.11+
- **HTTP 클라이언트 (목록 페이지):** `httpx` (HTTP/2, 정적 HTML이라 충분)
- **헤드리스 브라우저 (셀러 페이지):** `Playwright` (Chromium) — 팔로워수가 JS로 동적 로드되므로 필수
- **HTML 파서:** `BeautifulSoup4` + `lxml`
- **병렬 처리:** `concurrent.futures.ThreadPoolExecutor(max_workers=3)`
- **요청 간격:** 각 요청 후 `time.sleep(0.2)`
- **표준 라이브러리:** `json`, `re`, `logging`, `datetime`, `pathlib`

**⚠️ Hybrid client 설계 근거:**
- BUYMA 셀러 페이지의 팔로워수(`#js_fan_count`, `span.fan_text`)는 정적 HTML에서 비어 있고 페이지 로드 후 JS XHR로 채워진다.
- 셀러명/출품수/주문실적은 정적 HTML에 있어 Playwright 없이도 추출 가능하나, follower_count를 위해 Playwright 사용이 필요.
- 목록 페이지는 정적 anchor 태그만 사용하므로 httpx로 충분 (Playwright 오버헤드 회피).

## 디렉토리 구조

```
buyma market monitor/
├── main.py                  # CLI 엔트리포인트
├── requirements.txt         # httpx, beautifulsoup4, lxml
├── crawler/
│   ├── __init__.py
│   ├── client.py            # HTTP 클라이언트 (UA, 재시도, sleep)
│   ├── listing.py           # 목록 페이지 파싱 → seller_id 추출
│   ├── seller.py            # 셀러 페이지 파싱 (필터링 + 데이터 추출)
│   └── pagination.py        # max_pages 자동 감지
├── storage/
│   ├── __init__.py
│   └── store.py             # sellers.json / config.json 읽기·쓰기
├── data/                    # 자동생성 (gitignore)
│   ├── config.json
│   ├── sellers.json
│   └── errors.log
└── docs/superpowers/specs/
    └── 2026-06-02-buyma-seller-crawler-design.md
```

## 데이터 흐름

1회 실행 시 처리 순서:

```
[Step 1] config.json 로드
   ├─ max_pages 없음 → Step 2 실행 후 저장
   └─ max_pages 있음 → Step 3로 바로 진입

[Step 2] max_pages 자동 감지
   ├─ 1페이지 요청 → 페이지네이션 영역에서 마지막 페이지 번호 파싱
   └─ config.json에 저장

[Step 3] 목록 페이지 순회 (1 ~ max_pages)
   ├─ URL 패턴: https://www.buyma.com/r/-A2002003000_{N}/  (N=1,2,...)
   │   ※ 검증 완료: `_1` URL이 원본 진입 URL(`R120/`)과 동일 응답.
   │      R120은 results-per-page=120 모드 추정이나, 페이지네이션 클릭 시
   │      `_{N}` 패턴으로 전환되므로 default 40/page 모드로 통일.
   ├─ ThreadPoolExecutor(max_workers=3) 병렬
   ├─ 각 페이지에서 /buyer/{id}.html 링크 전부 추출
   └─ seller_id 집합으로 dedupe (set)

[Step 4] 셀러 페이지 순회 (전체 셀러 재방문)
   ├─ URL: https://www.buyma.com/buyer/{id}/sales_1.html
   ├─ ThreadPoolExecutor(max_workers=3) 병렬
   ├─ 필터링 후 데이터 추출
   └─ 매 요청 후 0.2초 sleep

[Step 5] sellers.json 저장
   ├─ seller_id를 키로 dict 머지 (덮어쓰기 = 업데이트)
   └─ first_seen_at은 기존 항목 보존
```

## 데이터 스키마

### `data/sellers.json`

key는 `seller_id` 문자열. 값은 셀러 객체.

```json
{
  "13053653": {
    "seller_id": "13053653",
    "seller_name": "KONNECT",
    "seller_type": "PERSONAL SHOPPER",
    "seller_url": "https://www.buyma.com/buyer/13053653/sales_1.html",
    "country": "한국",
    "follower_count": 334,
    "listing_count": 48739,
    "order_count": 286,
    "first_seen_at": "2026-06-02T16:55:23+09:00",
    "updated_at": "2026-06-02T16:55:23+09:00"
  },
  "12210564": {
    "seller_id": "12210564",
    "seller_name": "setof",
    "seller_type": "SHOP",
    "seller_url": "https://www.buyma.com/buyer/12210564/sales_1.html",
    "country": null,
    "follower_count": 985,
    "listing_count": 138,
    "order_count": 1179,
    "first_seen_at": "2026-06-02T16:55:23+09:00",
    "updated_at": "2026-06-02T16:55:23+09:00"
  }
}
```

**필드 규칙:**

| 필드 | 타입 | 비고 |
|---|---|---|
| `seller_id` | string | URL의 `/buyer/{id}/` 부분. 안정적 키 |
| `seller_name` | string | `<font>` 중첩 제거 후 strip |
| `seller_type` | string | `"PREMIUM PERSONAL SHOPPER"` / `"PERSONAL SHOPPER"` / `"SHOP"` 세 가지로 정규화 |
| `seller_url` | string | `sales_1.html` 형태로 저장 (향후 확장 대비) |
| `country` | string \| null | 한국이면 `"한국"`, SHOP이라 국가표시 없으면 `null` |
| `follower_count` | int | `\d+` 추출, 콤마 제거 |
| `listing_count` | int | 동일 |
| `order_count` | int | 동일 (예: `"286건"` → 286) |
| `first_seen_at` | string (ISO8601 +09:00) | 최초 발견 시각. 기존 항목 있으면 보존 |
| `updated_at` | string (ISO8601 +09:00) | 매 실행마다 갱신 |

### `data/config.json`

```json
{
  "max_pages": 100,
  "max_pages_detected_at": "2026-06-02T16:55:23+09:00",
  "last_run_at": "2026-06-02T17:10:11+09:00",
  "last_run_stats": {
    "pages_scanned": 100,
    "sellers_found_in_listing": 4203,
    "sellers_collected": 312,
    "sellers_filtered_out": 3891,
    "errors": 2
  }
}
```

### `data/errors.log`

라인당 JSON 한 줄 (jsonl 포맷):

```
{"timestamp": "2026-06-02T17:00:11+09:00", "stage": "seller", "url": "https://...", "status": 503, "reason": "max retries exceeded"}
```

## 파싱 셀렉터

### 목록 페이지 → seller_id 추출

- `a[href^="/buyer/"]` 태그 전부 수집
- 정규식 `/buyer/(\d+)\.html` 로 ID 추출 (목록 페이지에서는 `.html` 형태로 등장)
- set에 누적하여 dedupe

### 셀러 페이지 (`/buyer/{id}/sales_1.html`)

**전제: Playwright로 페이지 로드 + `#js_fan_count` 채워질 때까지 대기 후 HTML 추출.**
JS가 채우는 요소(`#js_fan_count`)가 비어있지 않을 때까지 `page.wait_for_selector("#js_fan_count:not(:empty)", timeout=5000)` 대기. 타임아웃 시 follower만 0으로 처리하고 나머지 필드는 정상 추출.

| 항목 | 셀렉터 / 추출 방법 |
|---|---|
| 셀러명 | `#buyer_name h1 a` 의 `get_text(strip=True)` |
| 셀러 구분 | `p.label` 안에서 판정 (아래 결정 트리) |
| 국가 | `#buyer_name h1 img` 의 `alt` 속성. 없으면 `null` |
| 팔로워수 | `#js_fan_count` 텍스트에서 `\d[\d,]*` 추출 후 콤마 제거 (Playwright 렌더링 후) |
| 출품수 | `span.syohin_cnt_text` 텍스트에서 첫 `\d[\d,]*` 추출 |
| 주문실적 | `p.buyer_eva_text` 텍스트에서 `\d[\d,]*` 추출 (`<h3>注文実績</h3>` 직후 등장하는 첫 `p.buyer_eva_text`) |

**검증 완료 (사용자 직접 확인):**
- SHOP 셀러 setof (12210564): seller_name="setof", followers=985, listings=138, orders=1179
- SHOP 페이지도 PERSONAL SHOPPER 페이지와 동일 DOM 사용 (분기 불필요)
- `buyer_eva_text`는 페이지에 3개 존재 (배송일/주문실적/성공률). `<h3>注文実績</h3>` 다음 항목을 선택해야 함.

**국가 표시 정규화:**
- BUYMA는 국가 flag의 `alt` 속성을 일본어로 렌더링 (`韓国`, `日本`)
- 출력 JSON에는 한국어 `"한국"` 으로 정규화 저장
- 매핑: `{"韓国": "한국", "한국": "한국"}` (한국어 alt도 혹시 모를 대비)

### 셀러 구분 판정 로직

```python
label_el = soup.select_one("p.label")
if label_el is None:
    seller_type = None  # 비정상 → errors.log + 스킵
elif label_el.select_one("span.label_shop"):
    seller_type = "SHOP"
elif label_el.select_one("span.label_premium"):
    seller_type = "PREMIUM PERSONAL SHOPPER"
else:
    seller_type = label_el.get_text(strip=True).upper()
    # 통상 "PERSONAL SHOPPER" 만 나옴
```

## 필터링 결정 트리

```
seller_type
├─ SHOP                        → 무조건 수집 (country=null)
├─ PREMIUM PERSONAL SHOPPER
│   ├─ country == "한국"        → 수집
│   └─ else                    → 스킵
├─ PERSONAL SHOPPER
│   ├─ country == "한국"        → 수집
│   └─ else                    → 스킵
└─ None / 기타                  → errors.log + 스킵
```

## HTTP 클라이언트 정책

- **User-Agent:** 고정 (Chrome 최신 데스크탑 UA 문자열 하나)
- **Accept-Language:** `ja,en-US;q=0.9,ko;q=0.8`
- **Timeout:** connect 10s, read 30s
- **재시도:** 최대 3회, exponential backoff (0.5s, 1.0s, 2.0s)
- **재시도 대상:** 5xx, 429, 네트워크 예외 (`httpx.RequestError`)
- **재시도 비대상:** 4xx (404 등) — 즉시 실패 처리
- **요청 간격:** 모든 요청 완료 후 `time.sleep(0.2)` (재시도 backoff와 별개)

## 병렬 처리 정책

- `ThreadPoolExecutor(max_workers=3)` 단일 인스턴스를 각 스테이지에서 재사용
- 각 워커 내에서 요청 완료 후 0.2초 sleep
- 결과 수집은 `as_completed` 패턴으로 도착 순 처리 (진행률 로그 용이)

## 에러 처리 정책

| 상황 | 처리 |
|---|---|
| HTTP 5xx / 429 / 네트워크 예외 | 3회 재시도 후 실패 → errors.log + 스킵 |
| HTTP 4xx (404 등) | 재시도 없이 errors.log + 스킵 |
| `p.label` 없음 (셀러 구분 판정 불가) | errors.log + 스킵 |
| 팔로워/출품수/주문실적 셀렉터 누락 | 해당 필드만 `0`, 셀러는 수집 |
| 셀러명 / 국가 추출 실패 | 셀러명 누락은 errors.log + 스킵, 국가 누락은 `null` |

전체 프로세스는 어떤 개별 실패에도 중단되지 않는다. 마지막에 `last_run_stats.errors` 카운트로 요약.

## 로깅

- 콘솔(stdout): 진행 상황 (스테이지 시작/완료, 페이지 진행률, 통계)
- `data/errors.log`: 개별 실패 jsonl
- 레벨: INFO 기본, `--verbose` 플래그로 DEBUG

## CLI 인터페이스

단일 엔트리포인트:

```bash
python main.py
```

옵션:

- `--reset-pagination` : config의 `max_pages`를 무시하고 재감지
- `--verbose` : DEBUG 로그 출력
- `--dry-run` : 데이터 파일 쓰지 않고 통계만 출력

## 모듈별 책임

### `crawler/client.py`
- `BuyMaClient` 클래스
- `get(url) -> httpx.Response`: 재시도 + sleep 적용된 단일 GET
- 내부적으로 `httpx.Client` 하나 유지 (connection pool 재사용)

### `crawler/pagination.py`
- `detect_max_pages(client) -> int`: 1페이지 요청 → 마지막 페이지 번호 파싱

### `crawler/listing.py`
- `build_listing_url(page: int) -> str`
- `parse_seller_ids(html: str) -> set[str]`
- `crawl_listing_pages(client, max_pages: int) -> set[str]`: ThreadPool 병렬

### `crawler/seller.py`
- `build_seller_url(seller_id: str) -> str` (sales_1.html 형식)
- `parse_seller_page(html: str, seller_id: str) -> dict | None`: None이면 필터 탈락 또는 파싱 실패
- `crawl_sellers(client, seller_ids: set[str]) -> list[dict]`: ThreadPool 병렬

### `storage/store.py`
- `load_config() -> dict` / `save_config(dict)`
- `load_sellers() -> dict[str, dict]` / `save_sellers(dict)`
- `merge_sellers(existing, new) -> dict`: first_seen_at 보존 + 나머지 덮어쓰기
- `append_error(stage, url, status, reason)`

### `main.py`
- argparse → orchestrate Step 1~5
- 시작/완료 로그, 통계 출력

## 테스트 가능성

- `parse_seller_ids`, `parse_seller_page`, `detect_max_pages` 등 순수 파싱 함수는 HTML fixture 기반 단위 테스트 가능
- HTTP 클라이언트는 `httpx.MockTransport`로 격리 테스트 가능
- 초기 구현에서는 단위 테스트 미작성, 실제 페이지로 end-to-end 검증 우선. 안정화 후 fixture 추출하여 회귀 테스트 추가

## 향후 확장 (스코프 외)

- 각 셀러의 `sales_1.html` 이후 sales 페이지에서 상품 데이터 파싱
- 셀러별 상품 변화 추적 (가격, 재고, 등록일)
- 카테고리 다중화 (`A2002003000` 외 카테고리 지원)
