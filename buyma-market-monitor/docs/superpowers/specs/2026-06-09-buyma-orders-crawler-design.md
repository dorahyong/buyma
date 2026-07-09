# BUYMA 주문실적 증분 수집 크롤러 설계

## 목적

기존 [셀러 크롤러](2026-06-02-buyma-seller-crawler-design.md)가 수집한 401명의 한국 셀러들에 대해, 각 셀러의 sales 페이지(`/buyer/{id}/sales_N.html`)를 순회하여 **주문 발생 시점부터 누적 모니터링**한다.

매 실행 시 직전 실행 이후의 신규 주문만 증분으로 수집하여 `orders.jsonl`에 append-only로 저장한다.

## 스코프

**포함:**
- 셀러별 sales 페이지의 페이지네이션 순회 (sales_1, sales_2, ...)
- 직전 실행 시점 이후 신규 주문만 증분 수집
- 시그니처 시퀀스 기반 워터마크로 중복 없는 정확한 컷오프
- JSONL append-only 저장 (실행 중간 종료 시 데이터 손실 최소)
- 셀러 단위 워터마크 즉시 갱신

**제외:**
- 상품 페이지(`/item/{id}/`) 방문 및 상품 상세정보 파싱 (별도 프로젝트 가능성, 현재 스코프 아님)
- 셀러 목록 갱신 (기존 `crawl-sellers` 책임)
- 분석 / 대시보드 / 알림

## 기술 스택

기존 셀러 크롤러와 동일:
- Python 3.11+
- Playwright (Chromium) — sales 페이지는 정적 HTML로 작동하지만 셀러 페이지와 동일 origin이라 일관성 위해 Playwright 사용
- BeautifulSoup4 + lxml
- `concurrent.futures` + `threading.Lock` (3 워커 병렬, JSONL append 직렬화)

## 디렉토리 구조 (기존 프로젝트 확장)

```
buyma market monitor/
├── main.py                              # argparse subcommand 도입 (crawl-sellers / crawl-orders)
├── crawler/
│   ├── client.py                        # (기존) 그대로 사용
│   ├── listing.py, pagination.py        # (기존, 셀러 크롤 전용)
│   ├── seller.py                        # (기존)
│   └── orders.py                        # NEW: sales 페이지 파싱 + 증분 순회 + 워터마크 매칭
├── storage/
│   ├── store.py                         # (기존, 셀러 메타 전용)
│   └── orders_store.py                  # NEW: orders.jsonl append + 워터마크 관리
└── data/
    ├── sellers.json                     # (기존)
    ├── config.json                      # (기존, 셀러 크롤 설정)
    ├── orders.jsonl                     # NEW: 모든 주문 누적
    ├── orders_config.json               # NEW: 셀러별 워터마크 + 통계
    └── errors.log                       # (기존, 양 크롤러 공유)
```

## 실행 시 처리 흐름

`python main.py crawl-orders` 1회 실행:

```
[Step 1] sellers.json 로드 → seller_id 리스트 추출
   └─ 파일 없으면 종료 (안내 메시지: "Run crawl-sellers first")

[Step 2] orders_config.json 로드 → 셀러별 워터마크 dict
   └─ 신규 셀러는 워터마크 없음 (빈 리스트로 처리, 전체 페이지 순회)

[Step 3] 셀러 페이지 순회 (Playwright, 3 워커, queue 기반)
   각 셀러에 대해 crawl_seller_orders 호출:
   ├─ sales_1.html 페치 → entry 리스트 (페이지 순서 유지)
   ├─ 워터마크 시퀀스가 entry 리스트에 등장하는 경계 위치 검색
   │     발견 → 경계 이전 entry = 신규, 수집 후 종료
   │     미발견 → sales_2.html 추가 페치, entry 리스트 누적, 다시 검색
   ├─ 최대 --max-pages (기본 10) 도달까지 반복
   ├─ 끝까지 시퀀스 미매치 → 경고 로그 + 모든 entry 수집 (안전한 over-collection)
   └─ 새 워터마크 = sales_1의 상위 30 entry로 갱신

[Step 4] 셀러 단위 즉시 저장
   ├─ 수집된 신규 주문 → orders.jsonl append (lock으로 직렬화)
   ├─ 갱신된 워터마크 → orders_config.json 저장
   └─ 중간 종료에도 이미 끝낸 셀러는 보존

[Step 5] 최종 통계 로그 + errors.log 카운트
```

## 데이터 스키마

### `data/orders.jsonl`

한 줄당 한 주문 (JSON Lines 포맷):

```jsonl
{"seller_id":"1415418","item_id":"109595879","item_name":"[STUSSY] STOCK DOG TEE","item_url":"https://www.buyma.com/item/109595879/","qty":1,"sale_date":"2026/06/09","collected_at":"2026-06-09T16:46:00+09:00"}
{"seller_id":"4861259","item_id":"88379479","item_name":"...","item_url":"https://www.buyma.com/item/88379479/","qty":1,"sale_date":"2026/06/09","collected_at":"2026-06-09T16:46:00+09:00"}
```

**필드 규칙:**

| 필드 | 타입 | 비고 |
|---|---|---|
| `seller_id` | string | sellers.json 키와 동일 (조인 키) |
| `item_id` | string | BUYMA 상품 ID |
| `item_name` | string | sales 페이지의 상품명 텍스트 (strip) |
| `item_url` | string | `https://www.buyma.com/item/{item_id}/` (포맷 고정) |
| `qty` | int | 주문 수량 |
| `sale_date` | string | `YYYY/MM/DD` (BUYMA 원본 포맷 보존) |
| `collected_at` | string | ISO8601 +09:00 (수집 시각) |

### `data/orders_config.json`

```json
{
  "watermarks": {
    "1415418": {
      "signature": [
        ["2026/06/09", "109595879", 1],
        ["2026/06/09", "119166289", 1],
        ["2026/06/09", "115110426", 1]
      ],
      "last_run_at": "2026-06-09T16:46:00+09:00",
      "pages_scanned_last_run": 1,
      "orders_added_last_run": 5
    },
    "4861259": { "...": "..." }
  },
  "last_run_at": "2026-06-09T16:46:00+09:00",
  "last_run_stats": {
    "sellers_processed": 401,
    "sellers_with_new_orders": 87,
    "total_new_orders": 234,
    "errors": 0,
    "max_pages_reached_warnings": 0
  }
}
```

**`signature` 규칙:**
- 길이 = 최대 30 (즉 sales_1 페이지 한 장 분량)
- 순서 = 페이지 등장 순 (날짜 내림차순)
- 요소 = `[sale_date, item_id, qty]` 배열 (JSON 호환을 위해 tuple 대신 list)
- 신규 셀러: 빈 리스트 `[]` 또는 키 부재

## 시그니처 시퀀스 워터마크 매칭

**알고리즘 (`find_watermark_boundary`):**

```python
def find_watermark_boundary(page_entries: list[tuple], watermark: list[tuple]) -> int:
    """페이지 entry 리스트에서 워터마크 시퀀스가 연속으로 등장하는 시작 위치 반환.

    매칭 안 되면 -1 반환.

    예:
      page_entries = [E1, E2, E3, W1, W2, W3, W4, ...]
      watermark    = [W1, W2, W3, W4, W5]
      → 반환: 3 (W1의 위치)
      → 호출자는 page_entries[:3] = [E1, E2, E3]을 신규로 수집
    """
    if not watermark:
        return -1  # 매칭 불가 (신규 셀러)
    L = len(watermark)
    for i in range(len(page_entries) - L + 1):
        if page_entries[i:i+L] == watermark:
            return i
    return -1
```

**증분 수집 알고리즘 (`crawl_seller_orders`):**

```python
def crawl_seller_orders(client, seller_id, watermark, max_pages=10):
    accumulated_entries = []
    warnings = []
    for page_num in range(1, max_pages + 1):
        url = build_sales_url(seller_id, page_num)
        html = client.get(url).text
        page_entries = parse_sales_page(html)  # list of (sale_date, item_id, qty, item_name)
        if not page_entries:
            break  # 빈 페이지 = 끝
        accumulated_entries.extend(page_entries)
        # 워터마크 시퀀스 (sale_date, item_id, qty) 기준 매칭
        signature_only = [(e.sale_date, e.item_id, e.qty) for e in accumulated_entries]
        boundary = find_watermark_boundary(signature_only, watermark)
        if boundary >= 0:
            new_orders = accumulated_entries[:boundary]
            new_watermark = signature_only[:30]
            return new_orders, new_watermark, warnings
    # 최대 페이지 도달, 매칭 못 함 → 모두 수집 (over-collection)
    warnings.append(f"max_pages={max_pages} reached without watermark match for seller {seller_id}")
    new_watermark = [(e.sale_date, e.item_id, e.qty) for e in accumulated_entries[:30]]
    return accumulated_entries, new_watermark, warnings
```

**왜 안전한가:**
- 같은 날 같은 상품 같은 수량의 별개 주문(검증 단계에서 실제 발생 확인)도 시퀀스 안에 그대로 보존 → 매칭 정확
- 페이지 상단에 새 entry 추가돼도 시퀀스는 그대로 아래로 밀려서 발견 가능
- 매칭 성공 시 0건 중복 보장
- 매칭 실패는 30건 초과 신규 발생한 드문 경우만 발생 (활성 셀러도 일 수십 건 미만 → 일 단위 실행이면 안전)

## 파싱 셀렉터

### sales 페이지 entry (검증된 DOM 구조)

```html
<li class="buyeritemtable_info">
  <p class="buyeritem_name">
    <a href="/item/{item_id}/" data-vt="/vt/buyer/sales_tab/item_images">
      {item_name}
    </a>
  </p>
  <p>注文数：{qty}個</p>
  <p>成約日：{YYYY/MM/DD}</p>
</li>
```

| 항목 | 추출 방법 |
|---|---|
| 컨테이너 | `li.buyeritemtable_info` |
| item_id | `li p.buyeritem_name a[href]` → `r"/item/(\d+)/"` 정규식 |
| item_name | 동일 anchor의 `get_text(strip=True)` |
| qty | `li` 안 `<p>` 중 `r"注文\s*数\s*[：:]\s*(\d+)"` 매치되는 것에서 추출 |
| sale_date | `li` 안 `<p>` 중 `r"成約日\s*[：:]\s*(\d{4}/\d{2}/\d{2})"` 매치되는 것에서 추출 |

**주의: BUYMA는 일본어 전각 콜론 `：` (U+FF1A) 사용.** 정규식에 `[：:]` 양쪽 모두 포함.

## CLI 인터페이스

`main.py`에 argparse subcommand 도입.

```bash
# 기존 (셀러 크롤)
python main.py crawl-sellers
python main.py crawl-sellers --verbose --dry-run

# 신규 (주문 크롤)
python main.py crawl-orders
python main.py crawl-orders --verbose
python main.py crawl-orders --max-pages 5         # 페이지 한도 변경
python main.py crawl-orders --seller-id 1415418   # 특정 셀러만 (디버깅용)
python main.py crawl-orders --dry-run             # 저장 안 함
```

기존 기능 호환을 위해 인자 없이 `python main.py` 호출 시 `crawl-sellers`를 기본으로 실행할지는 결정 사항 — 명시적 subcommand 강제 권장 (향후 사용자 실수 방지).

## 모듈별 책임

### `crawler/orders.py`
- `OrderEntry` dataclass (sale_date, item_id, qty, item_name, item_url)
- `build_sales_url(seller_id, page) -> str`
- `parse_sales_page(html: str) -> list[OrderEntry]` — 페이지 등장 순서 그대로 반환
- `find_watermark_boundary(page_entries, watermark) -> int`
- `crawl_seller_orders(client, seller_id, watermark, max_pages=10) -> (new_orders, new_watermark, warnings)`
- `crawl_all_orders_with_factory(client_factory, seller_watermarks: dict, on_seller_done, on_error, max_pages, num_workers=3) -> stats`

`on_seller_done(seller_id, new_orders, new_watermark)` 콜백을 통해 셀러 단위 즉시 저장 가능 (orders.jsonl append + 워터마크 갱신).

### `storage/orders_store.py`
- `OrdersStore(data_dir: Path)`
- `append_orders(orders: list[dict])` — `orders.jsonl`에 lock으로 직렬화 append
- `load_orders_config() -> dict` (워터마크 + 통계)
- `save_orders_config(config: dict)` — 전체 저장
- `update_seller_watermark(seller_id, watermark, stats)` — 셀러 1명 끝나면 호출. 메모리 dict 갱신 + 즉시 디스크 flush (orders_config.json 전체 재작성). 401 셀러 × ~1.4KB 쓰기는 부담 없음. 중간 종료에도 끝낸 셀러는 영구 보존.

### `main.py` (수정)
- argparse subparser 추가 (`crawl-sellers`, `crawl-orders`)
- 기존 `run()` 로직을 `run_crawl_sellers()` 로 추출
- 신규 `run_crawl_orders(args)` 추가:
  - sellers.json 존재 확인
  - orders_config 로드
  - PlaywrightClient factory로 워커 시작
  - 셀러 단위 진행 로그
  - 종료 시 통계 출력

## 에러 처리 정책

| 상황 | 처리 |
|---|---|
| sales 페이지 5xx / 429 / 네트워크 예외 | client 내부에서 3회 재시도 (기존 정책 그대로). 실패 시 셀러 단위 errors.log 기록 후 다음 셀러로 |
| sales 페이지 404 | 셀러 비활성화 또는 ID 변경 가능성. errors.log 기록 후 다음 셀러, 워터마크는 그대로 유지 |
| 페이지 entry 0건 | sales_1부터 entry가 없으면 그 셀러는 주문 없음. 워터마크 빈 리스트로 유지. errors.log 기록 안 함. |
| 파싱 실패 (item_id 추출 안 됨 등) | 해당 entry만 스킵, 다음 entry 계속. 페이지의 일부만 파싱돼도 진행. |
| max_pages 도달하고 워터마크 미매치 | 경고 로그 + 카운트 + 모든 entry 수집 (over-collection 허용) |
| sellers.json 없음 | 종료 코드 1, 메시지 출력 |
| 셀러 페이지 처리 중 KeyboardInterrupt | 진행 중 셀러는 미저장, 이미 완료된 셀러는 보존 |

## 운영 고려사항

- **실행 빈도:** 일 1회 가정. cron이나 수동 실행. 시그니처 워터마크 30건 한도는 일 30건 미만의 주문 발생 가정 → 매우 활발한 셀러도 안전.
- **풀 재수집 (`--full-rescan`):** 명시적으로 워터마크 초기화하고 전체 수집. 데이터 무결성 의심 시 사용.
- **셀러 단위 저장:** 워터마크 갱신과 orders.jsonl append를 셀러 1명 단위로 즉시 수행 → 22분 크롤 도중 중단돼도 끝낸 셀러는 영구 보존.

## 향후 확장 (스코프 외)

- 상품 페이지(`/item/{id}/`) 크롤러 — 가격/브랜드/카테고리 등 메타 수집. orders와 item_id로 조인.
- 시계열 분석 — sale_date별 집계, 셀러 랭킹 변화 추적
- 알림 — 새로 발견된 트렌딩 상품 / 셀러 활성도 변화
