# 데이터 수집 확장 (Project 1) — 설계 문서

작성일: 2026-06-11
프로젝트: BUYMA market monitor — 상품 상세 데이터 수집 확장

## 목표

상품 상세 페이지(`/item/{id}/`)에서 현재 수집하지 않는 데이터를 추가로 정규화 수집하고, 용량의 53%를 차지하는 `raw_meta_json`을 제거한다.

추가 수집 대상:
- 전체 이미지 (현재 대표 1장 → 전체)
- description 전체 본문 (현재 og 잘린 607자 → ProductGroup 전체 931자)
- 색·사이즈 섹션 텍스트 (size_guide)
- 찜 수, 조회수 (현재값 + 시계열 기반)
- 브랜드 품번 (品番)
- 테마/태그
- 색×사이즈×재고×가격 표 (variants)
- 사이즈 실측표 (치수)

## 배경 / 검증된 사실

샘플 상품 `https://www.buyma.com/item/133033222/` (POLO RALPH LAUREN)에서 정적 HTML로 모두 검증 완료. **Playwright 불필요** (HttpClient 정적 fetch로 충분).

### 현재 문제

- `raw_meta_json`: 항목당 약 23KB (json_ld 21KB가 대부분, ProductGroup.hasVariant가 19KB). 3,605개 enriched 상태에서 이미 79.5MB로 DB(149MB)의 53%. 121만 개 전부 보강 시 약 26GB로 추정.
- `brand`, `category_path`, `description`이 컬럼과 raw_meta_json에 **중복** 저장됨.
- 이미지: og:image 1장만 저장. 실제로는 ProductGroup.hasVariant[].image[]에 다수 존재 (샘플: 112개 ref → dedup 14장).
- description: og:description(607자, BUYMA가 자른 요약)만 저장. ProductGroup.description은 931자 전체.

### 데이터 소스 (검증된 셀렉터)

| 데이터 | 소스 | 검증 결과 |
|---|---|---|
| 전체 이미지 | JSON-LD `ProductGroup.hasVariant[].image[]` | dedup 14장 (org.jpg) |
| description 전체 | JSON-LD `ProductGroup.description` | 931자 |
| 조회수 | `span.ac_count` | "3" → 3 |
| 찜 수 | `span.fav_count` | "0人" → 0 |
| 브랜드 품번 | `<dt>品番</dt>` 다음 `<dd>` | "MNPOSWM17620534-410-26 MNPOSWM17620535-020-26" |
| 테마/태그 | `タグ` 영역 `a` 텍스트 | ['ユニセックス','ロゴ', ...] ('もっと見る'/'閉じる' UI 텍스트 섞임) |
| variants | JSON-LD `ProductGroup.hasVariant[]` | color/size/sku/price/availability (8개: 2색×4사이즈) |
| size_guide | `<h3>色・サイズ` 섹션 | 샘플은 BUYMA 표준 위젯이었으나 셀러 텍스트 가능성 있어 수집 |
| 사이즈 실측표 | HTML `<table>` (サイズの名称 + 치수) | S→ウエスト68cm/ヒップ110cm 등 |

variant 예시:
```
color=DARK GREY size=S sku=133033222762226263 price=18900 availability=InStock
color=NAVY      size=S sku=133033222762226267 price=18900 availability=InStock
```
재고 수량(stock_min/max)은 샘플엔 없었으나 일부 상품은 `offers`에 QuantitativeValue(minValue/maxValue) 제공 → 있으면 수집.

### 수집 불가 확정

- **장바구니 수**: BUYMA가 페이지에 노출하지 않음 (count icon은 access(조회)만 존재). 수집 대상에서 제외.

## 아키텍처

기존 2단계 파이프라인 구조 유지. 변경은 **Stage B(enrich)** 의 파서·저장 계층에 집중.

- `crawler/item_detail.py` — 파서. 반환 dict에 신규 필드 추가, raw_meta_json 제거.
- `storage/db.py` — 스키마. items 컬럼 변경 + 신규 테이블 3개. SCHEMA_VERSION 2.
- `storage/items_repo.py` — 저장 함수. update_detail_fields 변경 + 신규 3함수.
- `crawler/monitor.py` — `apply_enrich`가 신규 저장 함수들을 한 트랜잭션으로 호출.

데이터 모델은 정규화 우선. 1:N 데이터(이미지, variants, 통계 이력)는 별도 테이블, 1:1 구조화 데이터(사이즈 실측표, 테마)는 items의 JSON 컬럼.

## 스키마 (SCHEMA_VERSION = 2)

### items 테이블

변경 사항:
- **제거**: `raw_meta_json`
- **변경**: `description` 의미 변경 (og 잘린 본문 → ProductGroup 전체 본문). 컬럼 타입(TEXT) 동일.
- **추가**:
  - `size_guide_text TEXT` — 色・サイズ 섹션 텍스트 (없으면 NULL)
  - `view_count INTEGER` — 조회수 현재값
  - `fav_count INTEGER` — 찜 수 현재값
  - `brand_model_number TEXT` — 品番 원본 문자열 (여러 개면 원문 그대로, 없으면 NULL)
  - `themes TEXT` — 태그 JSON 배열 문자열 (예: `["ユニセックス","ロゴ"]`, 없으면 NULL)
  - `size_chart_json TEXT` — 사이즈 실측표 JSON (예: `{"S":{"ウエスト":"68.0cm","ヒップ":"110.0cm"}}`, 없으면 NULL)

전체 items DDL:
```sql
CREATE TABLE IF NOT EXISTS items (
  item_id             TEXT PRIMARY KEY,
  seller_id           TEXT NOT NULL,
  name                TEXT NOT NULL,
  current_price       INTEGER,
  brand               TEXT,
  category_path       TEXT,
  origin_country      TEXT,
  image_url           TEXT,            -- 대표 1장 (og:image)
  description         TEXT,            -- ProductGroup 전체 본문
  size_guide_text     TEXT,
  view_count          INTEGER,
  fav_count           INTEGER,
  brand_model_number  TEXT,
  themes              TEXT,            -- JSON 배열
  size_chart_json     TEXT,            -- JSON 객체
  status              TEXT NOT NULL,
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  sold_out_at         TEXT,
  deleted_at          TEXT,
  detail_fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_seller ON items(seller_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
```

### price_history (변경 없음)
```sql
CREATE TABLE IF NOT EXISTS price_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  price        INTEGER NOT NULL,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_price_history_item ON price_history(item_id);
```

### monitor_runs (변경 없음)
기존 그대로 유지.

### item_images (신규)
```sql
CREATE TABLE IF NOT EXISTS item_images (
  item_id    TEXT NOT NULL,
  position   INTEGER NOT NULL,   -- 0,1,2,... 등장 순서
  image_url  TEXT NOT NULL,
  PRIMARY KEY (item_id, position)
);
CREATE INDEX IF NOT EXISTS idx_item_images_item ON item_images(item_id);
```
dedup 후 순서 보존. variant별 색상 매핑은 저장하지 않음 (같은 이미지가 여러 variant에 중복 등장하기 때문). 재방문 시 전체 삭제 후 재삽입.

### stats_history (신규)
```sql
CREATE TABLE IF NOT EXISTS stats_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  view_count   INTEGER,
  fav_count    INTEGER,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_stats_history_item ON stats_history(item_id);
```
**매 관측마다 1행 기록 (무변동도 기록).** "결측(관측 안 함)"과 "무변동(관측했으나 동일)"을 구분하기 위함. 이는 Project 2(적응형 관측 스케줄러)의 velocity 분석 기반이 된다. price_history와 달리 변동 여부와 무관하게 enrich 시 항상 1행 INSERT (`INSERT OR IGNORE`로 동일 timestamp 중복만 방지).

### item_variants (신규)
```sql
CREATE TABLE IF NOT EXISTS item_variants (
  item_id       TEXT NOT NULL,
  variant_sku   TEXT NOT NULL,
  color         TEXT,
  size          TEXT,
  price         INTEGER,
  availability  TEXT,            -- "InStock" / "OutOfStock" 등 (schema.org 마지막 세그먼트)
  stock_min     INTEGER,         -- offers의 재고 범위 (있으면)
  stock_max     INTEGER,
  PRIMARY KEY (item_id, variant_sku)
);
CREATE INDEX IF NOT EXISTS idx_item_variants_item ON item_variants(item_id);
```
재방문 시 전체 삭제 후 재삽입.

## 파서 변경 (`crawler/item_detail.py`)

`parse_item_detail(html) -> dict` 반환 키 변경.

**제거**: `raw_meta_json`

**변경**:
- `description`: og.get("description") 우선 → **ProductGroup.description 우선**으로 변경. 즉 ProductGroup.description이 있으면 그것을, 없으면 og fallback.

**추가 키**:
- `image_urls: list[str]` — 모든 ProductGroup.hasVariant[].image[]를 평탄화 후 dedup(등장 순서 보존). image가 str/list 양쪽 케이스 처리.
- `view_count: int | None` — `span.ac_count` 텍스트에서 정수 파싱 (`[\d,]+`)
- `fav_count: int | None` — `span.fav_count` 텍스트에서 정수 파싱 ("0人" → 0)
- `brand_model_number: str | None` — `<dt>` 중 텍스트에 "品番" 포함하는 것의 다음 `<dd>` 텍스트
- `themes: list[str]` — 태그 영역 `a` 텍스트 목록. UI 텍스트('もっと見る','閉じる','もっと見る' 등) 제외
- `variants: list[dict]` — ProductGroup.hasVariant[] 각각에서 `{variant_sku, color, size, price, availability, stock_min, stock_max}` 추출. sku는 str로 변환. availability는 schema.org URL의 마지막 세그먼트(예 "https://schema.org/InStock" → "InStock"). price는 offers.price 또는 offers.lowPrice 정수. 재고 범위는 offers의 inventoryLevel/QuantitativeValue minValue/maxValue (없으면 None).
- `size_guide_text: str | None` — `<h3>` 중 "色・サイズ" 포함 섹션의 텍스트. **plan 단계에서 정밀 셀렉터 결정** (샘플 상품은 BUYMA 위젯이라 명확한 본문이 없었음; 실제 셀러 텍스트가 있는 상품으로 추가 검증 필요).
- `size_chart: dict | None` — 사이즈 실측표 HTML `<table>` 파싱. `{사이즈명: {측정항목: 값}}` 구조. "サイズの名称" 헤더를 가진 table을 찾아 행/열 매핑. **plan 단계에서 table 식별 셀렉터 및 색×사이즈 재고 매트릭스 table과의 구분 로직 정밀화.**

기존 키 유지: `name`, `brand`, `category_path`, `origin_country`, `image_url`.

파싱 견고성 원칙(기존과 동일): 어떤 필드든 누락/형식오류 시 None(또는 빈 list)을 반환하고 예외를 던지지 않는다.

## 저장 변경

### storage/items_repo.py

`update_detail_fields()` 시그니처 변경:
- **제거**: `raw_meta_json` 파라미터
- **추가**: `size_guide_text`, `view_count`, `fav_count`, `brand_model_number`, `themes`(JSON 문자열), `size_chart_json`(JSON 문자열)
- UPDATE 문에 신규 컬럼 반영

신규 함수:
- `replace_item_images(conn, item_id, image_urls: list[str])` — 기존 행 DELETE 후 position 0..N-1로 INSERT
- `replace_item_variants(conn, item_id, variants: list[dict])` — 기존 행 DELETE 후 INSERT
- `record_stats_observation(conn, item_id, view_count, fav_count, observed_at)` — stats_history에 `INSERT OR IGNORE`

### crawler/monitor.py

`apply_enrich(conn, item_id, html, now)`:
- `parse_item_detail(html)` 결과에서 신규 필드를 꺼내 `update_detail_fields` 호출 (themes/size_chart는 `json.dumps`로 직렬화, None이면 None 유지)
- `replace_item_images(conn, item_id, meta["image_urls"])`
- `replace_item_variants(conn, item_id, meta["variants"])`
- `record_stats_observation(conn, item_id, meta["view_count"], meta["fav_count"], now)`
- 전부 동일 트랜잭션 컨텍스트 (호출자가 db_lock 보유)

## 마이그레이션

- **DB 전체 리셋**: 기존 `data/items.db`(+ WAL/SHM 파일) 삭제 후 새 스키마로 재생성.
- 기존 3,605개 enriched 데이터는 폐기 (전체의 0.3%, 재수집 비용 최소).
- 절차: 사용자가 수동으로 `data/items.db*` 삭제 후 `monitor_cli.py` 재실행하면 init_schema가 SCHEMA_VERSION=2로 새로 생성. 또는 CLI에 `--reset-db` 플래그 추가 검토(plan에서 결정).

## 테스트 전략

- 파서: 기존 `tests/fixtures/item_detail_normal.html` 재활용 + 신규 필드 검증 테스트 추가. variants/image_urls/view/fav/brand_model_number/themes 추출을 fixture 기준 exact assert. size_guide/size_chart는 fixture에 실제 표가 있으므로 검증 가능.
- 저장: 신규 repo 함수 각각 TDD (in-memory SQLite).
- 통합: apply_enrich가 4개 테이블(items, item_images, item_variants, stats_history)에 모두 쓰는지 검증.
- 회귀: 기존 64+2 테스트 무손상.

## 범위 밖 (명시적 제외)

- **적응형 관측 스케줄러** (Project 2): 인기도 기반 가변 재방문 주기. 본 프로젝트는 stats_history 기반만 구축하고, 재방문은 기존대로 신규 1회 enrich 유지.
- **장바구니 수**: BUYMA 미공개로 수집 불가.
- **variant별 이미지 색상 매핑**: 같은 이미지가 여러 variant에 중복 등장하여 1:1 매핑이 모호. 전체 이미지 목록만 순서 보존.
- 기존 가격 추적(price_history), 품절/삭제 판정, CircuitBreaker, stranded 보수 등은 변경 없음.

## Plan 단계에서 정밀화할 항목

1. `size_guide_text` 셀렉터 — 실제 셀러 텍스트가 있는 상품으로 추가 검증 후 확정.
2. `size_chart` table 식별 — 재고 매트릭스 table(색×사이즈 ○/×)과 사이즈 실측표 table(치수)을 구분하는 로직.
3. `themes` UI 텍스트 필터 목록 확정.
4. DB 리셋 방식 — 수동 삭제 vs `--reset-db` 플래그.
