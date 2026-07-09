# ③a 가치 기준 우선순위 목록 스캔 재설계

**작성일:** 2026-07-01
**상태:** 승인 대기 (검토 요청)
**과제:** 과제2(고래 크롤링 비효율)의 근본 해법 — ③a 목록 스캔을 균등 전수 → **셀러 가치 기준 주기 차등**으로.

## 배경 / 근거 (30분 캘리브레이션 결과)

- ③a 총 작업량: **목록 42,210페이지** (ACTIVE 1,259,370 ÷ 30, 482셀러).
- 처리량: 워커6 첫1분 108장(1.8/s), 워커15 첫1분 236장(3.9/s) — 정상 페이지만이면 15워커로 전체 ~3h.
- **병목**: 고래 셀러 일부 깊은 페이지가 수 분~11분씩 hang(전부 200 OK, 차단 아님 — read=30s 타임아웃도 트리클 응답을 못 끊음). 워커가 매달려 평균 처리량 붕괴 + cap 초과.
- **사용자 원칙**: 느리다/고래라는 이유로 페이지를 **스킵하면 안 됨**. 기준은 상품 **가치**. 가치 우선순위는 **상품(③)에만** 적용 — **①셀러·②주문은 매 사이클 전부 수집**.

균등 전수 스캔은 죽은 재고 고래의 수백 페이지를 팔리는 상품 셀러와 똑같이 매 사이클 훑어 역량을 낭비한다. 이를 가치 기준 차등으로 바꾼다.

## 핵심 결정

| 항목 | 결정 |
|---|---|
| 우선순위 범위 | 상품(③)만. ①②는 전수(불변) |
| ③a 단위 | 셀러 단위 스캔 주기 차등(실행기는 기존 페이지 단위 분산 재사용) |
| 가치 신호 | **복합** = 상품가치(revisit_state의 HOT/WARM 수·velocity) + 최근 주문(orders) |
| 느린 페이지 | 스킵 아님 — 바운드 후 **지연 재시도**(다음 due, 고가치는 곧 재시도). 콘텐츠 손실 0 |

## 설계

### 셀러 가치 티어 (복합 점수)

각 셀러에 대해 (매 ③a 실행 시작 시 SQL로 재계산, 482행이라 저렴):
- **상품가치 성분**: 그 셀러의 items 중 `revisit_state.tier IN ('HOT','WARM')` 개수(`hot_warm`), 및 `last_velocity > 0` 인 상품 수/합.
- **주문 성분**: 최근 30일 `orders`(sale_date 기준) 건수(`recent_orders`).
- **티어 판정(초기 임계, 보정 예정)**:
  - **HIGH**: `hot_warm >= 5` OR `recent_orders >= 3`
  - **MID**: `hot_warm >= 1` OR `recent_orders >= 1`
  - **LOW**: 그 외
  - **NEW**(scan 상태 없음): 초기 스캔 대상(즉시 due)

### 스캔 주기 (티어별, soft, 보정 예정)

| 티어 | 목표 스캔 간격 |
|---|---|
| HIGH | 1일 |
| MID | 4일 |
| LOW | 21일 (기본 주기 보장 — 신규 상품 발견 누락 방지) |

`next_scan_at = last_scanned_at + 간격`. LOW도 반드시 주기적으로 스캔되어 신규 상품이 결국 발견됨.

### 스키마 추가 (기존 불변)

```sql
CREATE TABLE IF NOT EXISTS seller_scan_state (
  seller_id       TEXT PRIMARY KEY,
  value_tier      TEXT,      -- HIGH / MID / LOW
  value_score     INTEGER,   -- 디버깅/정렬용 복합 점수
  last_scanned_at TEXT,
  next_scan_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_seller_scan_next ON seller_scan_state(next_scan_at);
```

### 실행 흐름 (한 번의 ③a 실행)

```
1) 가치 재계산: 모든 셀러의 value_tier/score를 SQL로 산출.
   seller_scan_state upsert (신규 셀러 → next_scan_at = now = 즉시 due).
2) due 선정: next_scan_at <= now 인 셀러를 (티어 우선 + 오래된 순)으로 정렬,
   시간 cap 한도까지 선택.
3) 스캔: 선택된 셀러 목록을 기존 run_page_scan(page_scan.py)으로 처리
   (페이지 단위 글로벌 큐 분산 + 신규/가격/사라짐 판정 그대로).
4) 완료 셀러: next_scan_at = now + 간격(value_tier) 갱신.
   미완(느린 페이지로 skip된) 셀러: next_scan_at 갱신 안 함 → 다음 실행에서 재시도
   (HIGH는 곧 다시 due). 콘텐츠 손실 없음.
```

### 느린 페이지(hang) 처리 — 스킵 아님

- ③a HTTP fetch에 **바운드된 총 요청 시간**(예: 25초; 기존 read=30s는 트리클을 못 끊으므로 total 개념 도입)을 적용. 초과 시 그 페이지 fetch 중단 → (기존 가드대로) 그 셀러는 이번 실행에서 reconcile 스킵.
- **그 셀러는 다음 due에 재시도**(HIGH면 곧). 반복 실패 시 재시도에서 타임아웃을 상향(느리지만 가치 있는 페이지를 끝내 수집). → **영구 폐기 없음.**
- 가치 우선순위로 저가치 고래 스캔 빈도가 급감하므로 hang 노출 자체가 크게 감소.

### 오케스트레이터 연결

- 2단계 orchestrator의 ③a 스테이지가 균등 `run_page_scan(all sellers)` 대신 **이 스케줄러**(due 셀러 선정 → run_page_scan)를 호출.
- ①셀러·②주문 스테이지는 변경 없음(전수).

## 구성요소 (파일)

| 파일 | 책임 |
|---|---|
| `storage/db.py` (수정) | `seller_scan_state` 테이블 추가(스키마 v5) |
| `crawler/scan_scheduler.py` (신규) | 가치 티어 산출(순수 로직: 임계값→티어), 간격→next_scan_at |
| `storage/scan_repo.py` (신규) | seller_scan_state upsert, 가치 재계산 SQL(items+revisit_state+orders 조인), due 셀러 조회 |
| `crawler/page_scan.py` (재사용/소폭) | 실행기 유지. 필요 시 바운드 타임아웃 클라이언트 주입 |
| `scan_cli.py` / `orchestrator.py` (수정) | ③a가 스케줄러 경유로 due 셀러만 스캔 |

## 테스트 전략 (TDD)

- **가치 티어 판정**(순수): hot_warm/recent_orders 임계 경계값 → HIGH/MID/LOW.
- **간격→next_scan_at**: 티어별 간격 덧셈(KST ISO).
- **가치 재계산 SQL**: items+revisit_state(HOT/WARM 카운트)+orders(최근 N일) 조인 결과 정확성(인메모리 fixture).
- **due 선정**: next_scan_at<=now 필터 + 티어 우선 정렬 + cap.
- **완료/미완 갱신**: 완료 셀러 next_scan_at 전진, skip 셀러 미전진(재시도 보장).
- **NEW 셀러**: 상태 없으면 즉시 due, 초기 스캔 후 편입.
- 통합: 인메모리 SQLite + 가짜 client로 due 셀러만 스캔되는지.

## 비범위 (YAGNI)

- ①②의 우선순위화(전수 유지).
- 페이지 단위 가치 우선순위(셀러 단위로 충분; 한 셀러는 통째 스캔).
- 완전 동적 velocity 피드백(티어는 임계 기반; 재계산은 매 실행).
- 임계·간격 값의 정밀 튜닝(초기값 후 운영 로그로 보정).
