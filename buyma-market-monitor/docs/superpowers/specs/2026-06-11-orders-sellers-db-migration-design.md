# Orders / Sellers JSON → SQLite 마이그레이션 설계

작성일: 2026-06-11

## 배경 / 문제

현재 저장 방식이 데이터별로 혼재되어 있다.

| 데이터 | 현재 방식 | 크기 | 문제 |
|--------|-----------|------|------|
| items | SQLite (`data/items.db`) | 156MB | 없음 (검증된 패턴) |
| orders | `data/orders.jsonl` (append-only) | 12MB / 47,811행 | 조회 불가, 중복관리 코드 외부 의존 |
| orders 워터마크 | `data/orders_config.json` | 853KB | **셀러마다 전체 파일 락 잡고 재작성** (멀티워커 병목/위험) |
| sellers | `data/sellers.json` | 156KB | 전체 load→merge→재작성, items와 JOIN 불가 |

가장 큰 실질적 문제는 `orders_config.json`이다. [orders_store.py](../../../storage/orders_store.py)의
`update_seller_watermark`가 셀러 워터마크 하나를 갱신할 때마다 853KB 파일 전체를 락을 잡고 재작성한다.
3워커 환경에서 셀러 수만큼 반복된다.

## 목표

orders, sellers, orders 워터마크를 기존 `items.db`로 이전한다. 기존 데이터는 전부 마이그레이션하고,
기존 JSON 파일은 백업(`.bak`) 후 코드에서 제거한다.

## 비목표 (이번 범위 밖)

- `data/config.json` (모니터 앱 설정), `data/errors.log` (에러 로그)는 파일로 유지한다.
- orders 조회/리포팅 기능 자체는 만들지 않는다. 스키마/인덱스만 그 가능성을 열어둔다.

## 설계

### 1. 저장 위치 — 기존 `items.db`에 테이블 3개 추가

별도 db 파일을 만들지 않는다. items가 이미 `seller_id`로 인덱싱되어 있어 JOIN이 가능하고,
연결·WAL·스레드 설정([db.py](../../../storage/db.py)의 `connect`)을 그대로 재사용한다.

```sql
-- 주문: append-only. 자연 고유키가 없으므로 자동증가 PK. 중복방지는 워터마크가 담당.
CREATE TABLE IF NOT EXISTS orders (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  seller_id    TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  item_name    TEXT,
  item_url     TEXT,
  qty          INTEGER,
  sale_date    TEXT NOT NULL,    -- "2026/06/09" 원본 형식 유지
  collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_seller    ON orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_orders_sale_date ON orders(sale_date);
CREATE INDEX IF NOT EXISTS idx_orders_item      ON orders(item_id);

-- 워터마크: 853KB 통째 재작성 → 셀러당 한 행 upsert
CREATE TABLE IF NOT EXISTS order_watermarks (
  seller_id              TEXT PRIMARY KEY,
  signature_json         TEXT NOT NULL,   -- 기존 signature(튜플 리스트)를 JSON 직렬화
  last_run_at            TEXT,
  pages_scanned_last_run INTEGER,
  orders_added_last_run  INTEGER
);

-- orders 실행 메타: orders_config.json의 top-level last_run_at/last_run_stats 대체.
-- 단일 행(최신 실행만 보관)으로 기존 동작을 그대로 유지한다.
CREATE TABLE IF NOT EXISTS order_run_meta (
  id                  INTEGER PRIMARY KEY CHECK (id = 1),
  last_run_at         TEXT,
  last_run_stats_json TEXT
);

-- 셀러: sellers.json 대체. first_seen_at 보존 로직은 upsert로 이식
CREATE TABLE IF NOT EXISTS sellers (
  seller_id      TEXT PRIMARY KEY,
  seller_name    TEXT,
  seller_type    TEXT,
  seller_url     TEXT,
  country        TEXT,
  follower_count INTEGER,
  listing_count  INTEGER,
  order_count    INTEGER,
  first_seen_at  TEXT,
  updated_at     TEXT
);
```

**핵심 결정 — orders에 UNIQUE 제약을 두지 않는다.** orders 레코드는
`sale_date`, `item_id`, `qty`, `item_name`, `item_url`, `seller_id`, `collected_at`로 구성되며
자연 고유키가 없다. 같은 상품이 같은 날 여러 번 팔리면 동일 필드의 별개 주문이므로,
고유키를 강제하면 정상 주문이 사라진다. 중복 차단은 기존처럼 워터마크 `signature`가 담당한다
(역할 분리 유지).

### 2. 코드 구조 — `items_repo` 패턴(연결 객체에 동작하는 함수들)을 따른다

- **`storage/db.py`**: 위 3개 테이블 DDL을 `_DDL`에 추가, `SCHEMA_VERSION`을 2로 올린다.
- **`storage/orders_repo.py`** (신규):
  - `insert_orders(conn, orders: list[dict]) -> None`
  - `get_watermark(conn, seller_id) -> dict | None`
  - `upsert_watermark(conn, seller_id, signature, last_run_at, pages_scanned, orders_added) -> None`
  - `load_all_watermarks(conn) -> dict[str, dict]`
  - `save_run_meta(conn, last_run_at, stats: dict) -> None` / `load_run_meta(conn) -> dict`
    — `order_run_meta` 단일 행 갱신/조회
- **`storage/sellers_repo.py`** (신규):
  - `upsert_sellers(conn, sellers: dict[str, dict]) -> None` — 기존
    [store.py](../../../storage/store.py) `merge_sellers`의 first_seen_at 보존 로직을 이식
  - `load_sellers(conn) -> dict[str, dict]`
  - `get_seller(conn, seller_id) -> dict | None`
- **`storage/orders_store.py` 제거.**
- **`storage/store.py`**: `load_sellers`/`save_sellers`/`merge_sellers` 제거.
  `load_config`/`save_config`/`append_error`는 유지(파일 기반, 범위 밖).
- **`main.py` / `monitor_cli.py`**: 공유 connection을 열어 repo 함수 호출로 교체한다.
  watermark 읽기/쓰기, orders append, sellers load/merge/save가 모두 DB 호출로 바뀐다.

### 3. 일회성 마이그레이션 — `scripts/migrate_json_to_db.py`

1. `data/items.db`에 새 스키마 적용(`init_schema`).
2. `data/orders.jsonl` 47,811행 → `orders` 적재.
3. `data/sellers.json` 401명 → `sellers` 적재.
4. `data/orders_config.json`의 `watermarks` → `order_watermarks` 적재,
   top-level `last_run_at`/`last_run_stats` → `order_run_meta`(단일 행) 적재.
5. 적재 행 수를 원본과 대조 검증.
6. 검증 통과 시 세 원본 파일을 `.bak`으로 백업(rename).

마이그레이션은 재실행 안전성을 위해 적재 전 대상 테이블이 비어있는지 확인하고,
비어있지 않으면 중단한다(중복 적재 방지).

### 4. 데이터 흐름

```
crawl-orders 실행
  → connect(items.db)
  → load_all_watermarks(conn)            # 셀러별 signature 로드
  → 워커들이 크롤 → on_seller_done:
      insert_orders(conn, new_orders)    # append, 자동증가 PK
      upsert_watermark(conn, sid, ...)   # 한 행 update (853KB 재작성 제거)

crawl-sellers 실행
  → connect(items.db)
  → upsert_sellers(conn, new_sellers)    # first_seen_at 보존
```

### 5. 에러 처리

- 마이그레이션 적재 중 예외 발생 시 원본 파일은 그대로 두고(`.bak` 전환 안 함) 중단, 에러 출력.
- DB 쓰기 실패는 SQLite 트랜잭션(WAL)으로 원자성 보장. 부분 적재로 깨지지 않음.

### 6. 테스트 (TDD)

- `tests/test_orders_repo.py`: insert/조회, watermark upsert·load, signature JSON 왕복.
- `tests/test_sellers_repo.py`: upsert, first_seen_at 보존, load/get.
- `tests/test_migration.py`: 임시 JSON 파일 → DB 적재 행 수·필드 검증, 재실행 차단.
- 기존 `test_db.py`/`test_items_repo.py` 패턴(임시 db, in-memory 또는 tmp_path)을 따른다.

## 진행 순서

요청대로 순차 진행한다.

1. **워터마크 테이블화** — `order_watermarks` + repo 함수 + main.py 연결. 가장 큰 효과(853KB 재작성 제거).
2. **orders 테이블 + 적재** — `orders` + `insert_orders` + main.py 연결.
3. **sellers 테이블** — `sellers` + sellers_repo + main.py/monitor_cli.py 연결.
4. **마이그레이션 스크립트 실행 + 기존 파일 백업.**
