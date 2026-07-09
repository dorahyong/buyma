# 1단계: 페이지 단위 목록 스캔 (고래 셀러 최적화) 설계

**작성일:** 2026-07-01
**상태:** 승인됨 (구현 대기)
**과제:** 통합 프로그램의 과제2 — ③a 목록 스캔에서 고래(대형) 셀러의 크롤링 비효율 제거.

## 목표

셀러 상품 목록 스캔(신규/가격/품절·삭제 발견)을 **셀러 단위 → `(셀러, 페이지)` 단위 글로벌 큐**로 전환해 고래 셀러의 수백 페이지를 모든 워커에 고르게 분산한다. 동시에 상세 enrich를 스캔에서 분리해 ③b 재방문이 전담하게 하여 ③a를 가볍게 만든다.

## 배경 / 문제

현재 `crawler/monitor.py::run_monitor`는 셀러 단위 워커 풀이다. 각 워커가 셀러 하나를 통째로:
1. `scan_seller_items`로 그 셀러의 출품목록 전 페이지(item_1..N.html)를 **직렬** 스캔,
2. 신규/stranded 아이템을 상세 enrich(fetch + parse),
3. 사라진 아이템 품절/삭제 판정.

**비효율:** 고래 셀러(수백 페이지·수만 상품)를 맡은 워커가 오래 독점하는 동안, 소형 셀러를 끝낸 다른 워커들이 후반부에 놀게 된다(tail latency). 풀크롤 후반 처리율이 5.3→1.6/s로 떨어졌던 원인.

## 핵심 결정 (브레인스토밍 합의)

| 항목 | 결정 |
|---|---|
| 분산 단위 | `(셀러, 페이지)` 글로벌 큐 (청크 대안 대비 부하 분산이 가장 고름) |
| enrich | ③a에서 분리 → ③b 재방문 전담 (신규 상품은 재방문 우선순위1 unobserved로 자동 흡수) |
| ③a의 범위 | 목록 스캔 + 신규 upsert + 가격이력 + 사라짐 판정(품절/삭제)만 |

## 설계

### 데이터 흐름 (한 번의 스캔 패스)

```
1) 부트스트랩(페이지 큐 구성):
   각 셀러의 page 1을 큐에 넣는다 (seller, 1).

2) 워커가 (seller, page) 태스크를 글로벌 큐에서 pull:
   - HTTP fetch(락 밖) → parse_seller_items로 아이템 파싱
   - page == 1 이면 parse_seller_items_max_page로 max_pages 파악 →
     (seller, 2)..(seller, max_pages)를 큐에 추가 등록
   - 파싱된 아이템을 셀러별 누적 버퍼에 모음(락 안)
   - 그 셀러의 "남은 페이지" 카운터 감소

3) 셀러 완료(모든 페이지 스캔 끝) 시 reconcile(락 안, 단일 트랜잭션):
   - 스캔된 아이템 전부 upsert_scanned_item (신규 여부 판정)
   - 신규거나 가격변동이면 record_price_observation
   - 이전 ACTIVE − 스캔됨 = 사라진 아이템 → 품절/삭제 판정 대상
   - (품절/삭제 판정은 상세 fetch가 필요 → 별도 처리, 아래)
```

**enrich 없음:** ③a는 상세를 fetch하지 않는다. 신규 아이템은 `detail_fetched_at IS NULL`로 남아 ③b 재방문의 우선순위1(unobserved)이 흡수한다.

### 고래 분산 효과

페이지가 글로벌 큐의 독립 태스크이므로, 고래 셀러의 500페이지가 5개 워커에 흩어진다. 소형 셀러 페이지와 섞여 처리돼 후반 tail이 사라진다. 워커는 셀러가 아니라 "다음 페이지"를 계속 집는다.

### 사라짐 판정 (품절/삭제)

목록에서 사라진 아이템(이전 ACTIVE인데 이번 스캔에 없음)은 상세 상태 확인이 필요하다(404→DELETED, 200→SOLD_OUT). 이는 상세 fetch를 요하므로:
- ③a는 "사라진 후보 id"만 집계한다(빠름, fetch 없음).
- 실제 품절/삭제 판정 fetch는 ③a의 마지막 단계에서 **아이템 단위 글로벌 큐**로 처리한다(고래 무관). 기존 `fetch_for_classification`+`apply_classification` 재사용.
- (대안: 판정도 ③b에 위임 가능하나, "사라짐"은 재방문 큐에 없을 수 있어 ③a에서 처리하는 것이 명확.)

### 빈 스캔 가드 (기존 원칙 유지)

한 셀러의 page 1 fetch가 실패해 아이템이 0이면(차단/오류 가능성), 그 셀러는 reconcile을 건너뛴다 → ACTIVE 상품을 잘못 "사라짐"으로 마킹하는 대참사 방지. (현 monitor의 empty-scan 가드와 동일 정신.)

### 동시성 규칙 (기존 유지)

- HTTP fetch는 db_lock 밖, DB 쓰기는 db_lock 안.
- 셀러별 누적 버퍼·완료 카운터는 별도 락으로 보호.
- CircuitBreaker로 차단 감지 시 워커 자발 종료.
- 시간 예산(deadline, `time.monotonic`) 지원 — 오케스트레이터의 ③a cap에 사용. 중단 시 완료된 셀러는 reconcile됨, 미완 셀러는 다음 실행이 재개(무손실).

## 구성요소 (파일)

| 파일 | 책임 |
|---|---|
| `crawler/page_scan.py` (신규) | 페이지 단위 글로벌 큐 스캔 오케스트레이션: `run_page_scan(db_path, sellers, client_factory, num_workers, now, on_error, max_hours=None, circuit_breaker=None)` → ScanSummary |
| `crawler/seller_items_crawler.py` (재사용) | `parse_seller_items`, `parse_seller_items_max_page`, `build_seller_items_url` |
| `storage/items_repo.py` (재사용) | `upsert_scanned_item`, `record_price_observation`, `get_active_item_ids_for_seller`, `mark_status` |
| `crawler/monitor.py` (재사용) | `fetch_for_classification`, `apply_classification`, `ItemStatus` |
| `monitor_cli.py` 또는 신규 CLI | 페이지 단위 스캔 실행 진입점 (오케스트레이터가 함수로 호출; CLI는 단독 테스트용) |

> 기존 `run_monitor`(셀러 단위)는 당장 삭제하지 않고 남겨두되, 신규 파이프라인은 `run_page_scan`을 사용한다. 오케스트레이터(2단계)가 `run_page_scan`을 ③a로 호출한다.

## 테스트 전략 (TDD)

- **페이지 큐 부트스트랩**: page 1 스캔 후 max_pages만큼 (seller,page) 태스크가 등록되는가.
- **고래 분산**: 큰 max_pages 셀러 + 여러 소형 셀러를 가짜 client로 스캔 시, 모든 페이지가 처리되고 아이템이 셀러별로 정확히 누적되는가.
- **reconcile 정확성**: 신규 upsert, 가격변동 기록, 이전 ACTIVE−스캔됨 = 사라진 후보 집계가 페이지 단위 누적에서도 정확한가(셀러의 모든 페이지 완료 후에만 판정).
- **빈 스캔 가드**: page 1 실패(0 아이템) 셀러는 reconcile 건너뛰어 기존 ACTIVE가 사라짐으로 마킹되지 않는가.
- **사라짐 판정**: 사라진 후보에 대해 404→DELETED, 200→SOLD_OUT.
- **시간 예산**: deadline 초과 시 진행 중 중단, 완료 셀러만 reconcile.
- **동시성**: 인메모리 SQLite + 가짜 client 멀티워커에서 무결성.

## 비범위 (YAGNI)

- 기존 `run_monitor` 리팩터/삭제(남겨둠, 신규 경로만 추가).
- 페이지 단위 enrich(분리 완료 — 재방문 전담).
- 오케스트레이터 통합(2단계 spec).
