# 실제 게시일수 기록 — 설계 제안서 (2026-06-09)

> ⚠️ **구현 완료 (2026-06-29, commit 7cde1c1). 단 아래 본문 설계와 핵심이 달라짐 — 실제 구현이 우선.**
> - **키가 ace_products 컬럼 → `buyma_product_id` 전용 테이블**로 변경. 사유: 그새 merge/cutover로 라이브 정체성이 갈림(옛 54,961=ace, 신규 2,717=buyma_listings, ref 겹침 0). ace 트리거 1개론 merge 출품을 놓침.
> - 실제: `buyma_listing_days`(카운터) + `buyma_listing_events`(이력) + `v_listing_days`(뷰), **트리거 2개**(ace_products + buyma_listings). 파일 `migrations/2026_listing_days_*.sql`, `migrations/backfill_listing_days.py`.
> - 백필 57,675건(listed_since=등록일), 관리페이지 "실제 게시일수" 컬럼(`products_api.py`/`products.html`). 초단위 적분 유지.
> - fast_price 하드삭제→재고API 전환(commit d56f7e2)이 게시일수 보존의 선행작업.
>
> 아래는 원 설계(2026-06-09) 기록 — 의사결정 배경 참고용.

> 상태: ~~설계 확정(2026-06-09). 코드/스키마 변경 0건 — 아직 미착수.~~ → **구현 완료(위 배너 참조).** 결정 사항은 §6.
> 짝꿍 기능인 "출품 유효기간 자동 관리"(available_until)는 이미 운영 중 — 본 문서 범위 밖.

---

## 1. 요구사항 (원문 요지)

- 상품이 **올라온 날 / 내려간 날**을 그때그때 기록해 **실제 출품일수**를 계산.
- **품절로 내려갔다 재등록돼도 같은 상품으로 이어서 누적 합산** → 누적 일수가 정확.
  - 예: 90일 출품 → 30일 내려감 → 오늘 다시 올라옴 → 다음날 기준 **91일**로 집계.
- 최종 목적: **5만 개 한도 안에서, "이 상품 며칠째 게시중/누적 며칠"** 같은 데이터에 근거해
  정리(삭제·교체)할 상품을 고른다.

---

## 2. 현황

- `ace_products`에 게시일수 이력 컬럼 **없음**. 있는 건:
  - `is_published`(0/1) — 현재 게시 상태.
  - `buyma_registered_at` — **최초** 등록 시각. webhook에서 `COALESCE(buyma_registered_at, NOW())`로
    세팅 → **재등록해도 안 바뀜**(최초값 고정). 따라서 "현재 출품 시작 시각"으로는 쓸 수 없음.
- 올라간 날/내려간 날 이력, 누적 일수, 재등록 합산 로직 **전무**. 관련 설계 문서도 없음(신규).

---

## 3. 핵심 발견 — up/down 신호가 어디서 발생하나 (코드 전수조사)

게시일수는 결국 `is_published`의 **0→1(up) / 1→0(down) 전이**를 잡으면 된다.
전이를 일으키는 `SET is_published` **쓰기** 지점을 전부 조사한 결과:

### UP (0→1) — **1곳뿐, webhook으로 집중**
- `okmall_reference/server.py:64` — `product/create|update` webhook에서 buyma_id 수신 시 `is_published=1`.

### DOWN (1→0) — **5곳에 흩어짐**
- `okmall_reference/server.py:54` — webhook `status='buyer_deleted'`(품절·구매자삭제 확정) → `is_published=0`.
- `okmall_reference/server.py:92` — webhook `fail_to_create`(`商品IDは不正な値です`/`削除できない商品です`) → `is_published=0`.
- `buyma_cleaners/buyma_orphan_cleaner.py:456` — 유령 상품(DB=1인데 BUYMA에 없음) 정리 → `is_published=0`.
- `fast_price_updater.py:728` — 가격 갱신 중 삭제 판정분 → `is_published=0`.
- `okmall/buyma_new_product_register.py:981` — 등록 단계 비활성화(`is_active=0, is_published=0`).
- (참고) `buyma_cleaners/buyma_unpublished_cleaner.py:142` — 이미 `is_published=0`인 것을 BUYMA에서 지우는 후처리(전이 아님).

> **결론**: down은 한 군데로 안 모인다. 각 스크립트에 기록 코드를 심으면 5곳 + 향후 추가분까지
> 계속 누락 위험. → **`ace_products`에 AFTER UPDATE 트리거 1개**를 걸어 전이를 잡으면
> **어느 스크립트가 바꾸든, 앞으로 새 경로가 생겨도** 자동 포착된다. 권장 방식.
>
> 참고: stock sync의 `control='delete'`(전옵션 품절)는 DB의 `is_published`를 직접 바꾸지 않고
> BUYMA에 delete만 보낸 뒤, 실제 0 전이는 webhook `buyer_deleted`로 들어온다
> (`update_product_after_api_call`은 `status`만 변경). 즉 그 경로도 위 5곳 안에서 잡힌다.

---

## 4. 설계

### 4.1 기록 메커니즘 — DB 트리거 (writer-무관)

`ace_products`에 `AFTER UPDATE` 트리거. **`OLD.is_published <> NEW.is_published`일 때만** 동작
(stock sync는 가격/재고로 매일 수많은 UPDATE를 날리지만 대부분 is_published 불변이므로 무시됨).

- `0 → 1` (up): 새 게시 구간 시작.
- `1 → 0` (down): 직전 게시 구간 종료 → 그 길이를 누적에 더함.

> INSERT(신규 등록)는 webhook가 먼저 row를 만들고(`is_published=0`) 이후 UPDATE로 1을 찍으므로
> UPDATE 트리거로 충분. 단 백필(4.4)에서 기존 게시분 시드 처리는 별도.

### 4.2 스키마 — **C안 확정** (집계 컬럼 + 이벤트 로그 둘 다)

빠른 조회는 집계 컬럼(B)이, 정확/복구는 이벤트 로그(A)가 담당. 트리거가 둘을 동시에 갱신.

**(B) 집계 컬럼 — ace_products에 추가 (빠른 조회용)**
```sql
ALTER TABLE ace_products
  ADD COLUMN listed_since        DATETIME NULL  COMMENT '현재 게시 구간 시작(미게시면 NULL)',
  ADD COLUMN accumulated_seconds BIGINT NOT NULL DEFAULT 0 COMMENT '과거 게시 구간 누적(초, 현재 구간 제외)';
```
- 트리거 up(0→1): `listed_since = NOW()` (NULL일 때만).
- 트리거 down(1→0): `accumulated_seconds = accumulated_seconds + TIMESTAMPDIFF(SECOND, listed_since, NOW())`, `listed_since = NULL`.
- 상품당 숫자 2개 → "며칠째?" 조회 O(1). 용량 ≈ 16바이트 × 5만 ≈ 0.8MB.

**(A) 이벤트 로그 — 전이 순간만 1줄씩 (정확/복구용)**
```sql
CREATE TABLE ace_listing_events (
  id               BIGINT PRIMARY KEY AUTO_INCREMENT,
  ace_product_id   INT NOT NULL,
  buyma_product_id BIGINT NULL,
  event            ENUM('up','down') NOT NULL,
  event_at         DATETIME NOT NULL,
  INDEX idx_ace_event (ace_product_id, event_at)
);
```
- **매 UPDATE가 아니라 깃발이 뒤집힌 순간만** INSERT → 증가 느림(5만×월1회 순환 ≈ 50MB/년, `ace_product_api_logs`보다 작음).
- 카운터(B)가 틀어지면 이 로그로 언제든 재계산. false-delete 사고 이력 대비 안전망.

**(조회용) 총 게시일수 뷰**
```sql
CREATE VIEW v_listing_days AS
SELECT id, buyma_product_id, is_published,
       (accumulated_seconds
         + IF(listed_since IS NULL, 0, TIMESTAMPDIFF(SECOND, listed_since, NOW())))
       / 86400.0 AS total_listed_days
FROM ace_products;
```
- "N일 이상 게시중 정리대상" = `WHERE total_listed_days >= N`. 5만 행 정수 연산이라 수 ms.

### 4.3 일수 계산 규약 — **초 단위 경과 시간 확정** (DATEDIFF 폐기)

- 시간을 **초로 적분**(`TIMESTAMPDIFF(SECOND,...)`), 표시할 때만 `÷86400`으로 일 환산.
- 이유: 달력 날짜 차이(DATEDIFF)는 **하루 안에 올라갔다 내려가는 상품(예: 00:30↑ 23:30↓)을 매번 0일로 처리해 영원히 0이 되는 버그**가 있음. 초 적분이면 23시간≈0.96일로 정확히 누적.
- "90일 → 30일 down → 재등록 → 24시간 뒤 91일" 예시도 그대로 충족(달력이 바뀌면 +1이 아니라 실제 24시간이 차면 +1).

### 4.4 백필(seeding) — 기존 46,080건

트리거는 "앞으로의 전이"만 잡으므로, **이미 게시중인 상품의 현재 구간 시작점**을 한 번 심어야 함.
- `listed_since` ← **`buyma_registered_at`** (현재 게시중이고 한 번도 안 내려간 상품엔 정확한 시작값.
  webhook COALESCE 덕에 최초 등록 시각이 보존돼 있음).
- `accumulated_days` ← **0** (과거 down 이력을 모르므로 0에서 시작; 이력 누적은 트리거 가동 시점부터).
- 미게시(`is_published=0`) 상품: `listed_since=NULL, accumulated_days=0`.
- 1회성 백필 스크립트(`--dry-run` 지원)로 일괄 세팅. **이건 코드 작성 단계**.

---

## 5. 엣지케이스 / 주의

- **트리거 발화 조건**을 반드시 `OLD.is_published <> NEW.is_published`로 한정 — 매일 수만 건의
  stock/price UPDATE에 끌려가면 안 됨.
- **재등록 후 `buyma_product_id` 변경**: register는 재등록 대상의 pid를 NULL→새값으로 바꿈.
  이벤트 로그(A)는 이벤트 시점 pid를 그대로 박제하므로 무관. 집계(B)는 ace_product_id 기준이라 무관.
- **fail_to_create(商品ID不正)로 인한 0 전이**: 사실상 "이미 내려가 있던 것" 확정 신호.
  이걸 down 1건으로 셀지(=직전 up 종료) 여부는 4.3 규약과 함께 확정. 보통 down으로 처리해도 무방.
- **트리거 누락분 복구**: 옵션 C면 A 로그로 B를 언제든 재계산하는 보정 배치 작성 가능(안전망).
- **타임존**: 모든 시각 서버 `NOW()` 단일 기준 유지(서버 cron과 동일 환경).

---

## 6. 결정 사항 (2026-06-09 확정)

1. **스키마: C안** — 집계 컬럼(B) + 이벤트 로그(A) + 조회 뷰. (§4.2)
2. **일수 규약: 초 단위 경과 시간** — `TIMESTAMPDIFF(SECOND)` 적분, DATEDIFF 폐기(하루내 순환 0버그 회피). (§4.3)
3. **fail_to_create 0전이는 down으로 집계.**
4. **백필: 기존 게시분 `listed_since = buyma_registered_at`, `accumulated_seconds = 0`부터.** 과거 down 이력은 복원 불가하므로 트리거 가동 시점부터 정확 누적.

---

## 7. 확정 후 작업 항목 (참고 — 지금은 미착수)

1. 스키마 적용(A/B/C 결정분) — `CREATE TABLE` / `ALTER TABLE`.
2. `ace_products` AFTER UPDATE 트리거 작성(전이 한정 + 로그/카운터 갱신).
3. 백필 1회성 스크립트(`--dry-run`) — 기존 46,080건 `listed_since=buyma_registered_at` 시드.
4. (옵션 C) A→B 재계산 보정 배치 — 안전망.
5. 조회 뷰/쿼리 — "누적 게시일수 TOP / N일 이상 게시중" 정리대상 추출용.
6. PROJECT_OVERVIEW.md 반영.

> 각 항목은 **사용자 확인 후** 진행. 본 문서 단계에선 어떤 파일도 수정하지 않음.
