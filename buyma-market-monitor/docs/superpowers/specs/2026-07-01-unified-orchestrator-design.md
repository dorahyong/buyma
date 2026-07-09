# 2단계: 통합 오케스트레이터 (단일 프로세스 supervisor) 설계

**작성일:** 2026-07-01
**상태:** 승인됨 (구현 대기, 1단계 이후)
**과제:** 통합 프로그램의 과제1 — 3개 프로그램을 하나의 데몬으로. 1·2는 하루 1회, 3은 24시간 budget 반복. 각 작업 budget 설정 + 나머지 재방문 분배.

## 목표

기존 파이프라인(셀러 수집·주문 수집·상품 목록 스캔·상세 재방문)을 **단일 프로세스 데몬**이 함수로 순차 호출하며, 매일 배치(①②③a)는 시간 cap을 두고, 남는 시간 전부를 상세 재방문(③b)에 배분한다. 예산·차단·쿨다운·정지를 한 곳에서 공유한다.

## 대상 프로그램

| 코드 | 활동 | 기존 진입 | 주기 |
|---|---|---|---|
| ① | 셀러 수집 | `main.py::run_crawl_sellers` | 매일 1회 |
| ② | 주문 실적 | `main.py::run_crawl_orders` / `crawl_all_orders_with_factory` | 매일 1회 |
| ③a | 상품 목록 스캔(신규/가격/품절) | `crawler/page_scan.py::run_page_scan` (1단계 산출물) | 매일 1회 |
| ③b | 상세 재방문(조회/찜/문의 시계열) | `crawler/revisit.py::run_revisit(loop=True)` | 남는 시간 전부 |

## 핵심 결정 (브레인스토밍 합의)

| 항목 | 결정 |
|---|---|
| 통합 구조 | A. 단일 프로세스 supervisor (함수 순차 호출, 예산/차단/정지 공유) |
| 실행 순서 | ①→②→③a 순차(같은 IP → 병렬 대신 순차로 차단 위험↓) → ③b 남는 시간 |
| 예산 | 배치작업 각각 시간 cap + 나머지 전부 ③b |
| 차단 대응 | 전체 일시정지 → 쿨다운 → 중단 지점부터 자동 재개(무손실) |
| 정지 | SIGINT graceful, 단일 로그 |

## 설계

### 사이클 루프

```
orchestrator --loop:
  while not stopped:
      cycle_start = now
      run_stage(①, cap=sellers_cap)     # 완료 or cap 초과 시 부분완료로 중단
      run_stage(②, cap=orders_cap)
      run_stage(③a, cap=scan_cap)
      remaining = cycle_hours*3600 - (now - cycle_start)
      run_stage(③b revisit loop, max_hours=remaining)   # 남는 시간 전부
      # ③b가 remaining 소진하고 반환 → 다음 사이클로
```
- `cycle_hours` 기본 24. cap 초과 시 그 작업은 부분완료로 멈추고(모두 resumable) 다음 단계로. 남는 시간은 항상 ③b가 흡수.
- cap 값은 설정 파일/CLI 인자. 초기값 예시: ① 1h, ② 3h, ③a 6h(1단계 고래 수정으로 단축 기대). 실제 실행으로 보정.

### 예산(cap) 메커니즘

각 배치 스테이지는 deadline(`time.monotonic() + cap`)을 받아 작업 경계(셀러/페이지 사이)에서 초과 여부를 확인하고 자발 종료한다. 필요한 deadline 지원:
- ③a `run_page_scan`: 1단계에서 `max_hours` 지원(설계됨).
- ③b `run_revisit`: `max_hours`/`loop` 지원(구현됨).
- ① `run_crawl_sellers`, ② `crawl_all_orders_with_factory`: **deadline 파라미터 추가**(셀러 경계에서 확인). 초과 시 남은 셀러는 다음 사이클(watermark로 재개).

### 차단(403/429) → 쿨다운 → 재개

- 공유 `CircuitBreaker` 또는 공유 "IP 차단" 신호를 supervisor가 관리. 어느 스테이지든 차단 감지(BlockedByServer/CB open)로 조기 종료하면, supervisor가:
  1. 모든 활동 정지,
  2. 쿨다운 대기(기본 45분, 인터럽트 가능),
  3. **같은 스테이지를 중단 지점부터 재개**(resumable: ① 재스캔, ② watermark, ③a 완료셀러 skip, ③b next_revisit_at).
- 프로그램②는 기존 CB threshold=1(첫 403 abort)을 유지하되, abort가 supervisor의 쿨다운을 트리거하도록 신호 연결.

### 정지·안정성

- SIGINT → 진행 중 작업 graceful 종료 후 데몬 정지(무손실). 재실행 시 이어감.
- 단일 로그(스테이지 태그: `[sellers] [orders] [scan] [revisit]`), 단일 errors.log.
- 머신 재부팅 시 멈춤 → 재실행 필요(완전 무중단은 launchd 추가, 미범위).

## 구성요소 (파일)

| 파일 | 책임 |
|---|---|
| `orchestrator.py` (신규) | supervisor 루프: 사이클, 스테이지 순차 호출+cap, 공유 차단신호+쿨다운, SIGINT, 단일 로그. CLI: `--cycle-hours --sellers-cap --orders-cap --scan-cap --cooldown-minutes --workers --sleep` |
| `main.py` (수정) | `run_crawl_sellers`, `crawl_all_orders_with_factory`에 `deadline`/`stop_event` 지원 추가(셀러 경계 확인) |
| `crawler/page_scan.py` (1단계) | `run_page_scan(..., max_hours)` ③a |
| `crawler/revisit.py` (재사용) | `run_revisit(loop=True, max_hours=remaining, stop_event, circuit_breaker)` ③b |

## 테스트 전략 (TDD)

- **사이클 순차**: ①→②→③a→③b 순서로 호출되는가(가짜 스테이지 함수로 호출 순서·인자 검증).
- **cap 강제**: 스테이지가 cap(deadline) 초과 시 중단되고 다음으로 넘어가는가; 남은 시간이 ③b `max_hours`로 정확히 전달되는가.
- **차단→쿨다운→재개**: 스테이지가 차단 신호로 조기 종료 시 supervisor가 쿨다운(짧게 목업) 후 같은 스테이지 재호출하는가.
- **SIGINT**: stop_event로 루프가 깔끔히 종료(무한 대기 없음).
- **①②의 deadline**: 셀러 경계에서 deadline 초과 시 남은 셀러 건너뛰고 반환.
- 스테이지 함수는 주입(가짜)해서 supervisor 로직을 실제 네트워크 없이 단위 테스트.

## 비범위 (YAGNI)

- launchd 재부팅 자동복구(추후).
- 스테이지 병렬 실행(순차 채택).
- 요청수 기반 예산(시간 기반 채택).
- 웹 대시보드/알림(로그로 충분).
