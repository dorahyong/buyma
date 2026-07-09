# 적응형 관측 스케줄러 (Project 2) 설계

**작성일:** 2026-06-29
**상태:** 승인됨 (구현 대기)

## 목표

확정된 인기 절대 3-티어(HOT/WARM/COLD)를 시드로, 상품별 상세 재방문을
**예산(시간) 기반 우선순위 스케줄링**으로 차등화한다. 재방문마다 view/fav/inquiry를
`stats_history`에 누적해 시계열을 만들고, 정규화된 velocity(일평균 증가율)로
"최근 주목받기 시작한 상품(급등)"을 탐지한다.

## 배경 / 확정된 전제

- **풀크롤 완료**: 전체 상품 1,307,213 / enrich 99.26% / 셀러 477.
  `stats_history`는 현재 상품당 1관측뿐 → velocity 측정 불가. 본 프로젝트가 재방문으로 시계열을 누적한다.
- **인기 절대 3-티어 (2026-06-28 데이터 분석으로 확정, `scripts/analyze_popularity.py`):**
  - HOT: `fav≥50 OR view≥2000 OR inquiry≥5` (~7만)
  - WARM: `fav≥10 OR view≥500 OR inquiry≥1` (~25만)
  - COLD: 나머지 (~100만)
  - 근거: orders 매칭 28,350개를 ground truth로, 판매 lift가 단조 증가
    (inquiry≥1 → 5.5x, fav≥50 → 7.7x, view≥2000 → 8.7x). 전체 판매율 base 2.18%.
- **운영 방식: 수동 실행** (사용자가 필요할 때 실행). 관측 간격이 불규칙하므로
  velocity는 절대 증가량이 아니라 **단위 시간당 증가율(Δ/Δ일)**로 정규화한다.

## 핵심 결정 사항 (브레인스토밍 합의)

| 항목 | 결정 |
|---|---|
| 운영 | 수동 실행 |
| 주기 정책 | 하이브리드 — 티어 목표간격(soft) + 급등 시 일시 승급 |
| 관측 범위 | HOT+WARM 정밀 + COLD 저빈도 |
| 실행 구조 | 재방문 전용 별도 CLI (`revisit_cli.py`). 기존 monitor는 신규 발견 담당 유지 |
| 예산 단위 | **시간 기준 (`--max-hours`)** |
| 스키마 | items 불변, 새 테이블 `revisit_state`만 추가 |

## 아키텍처 & 데이터 흐름

기존 `monitor`(신규 발견·가격·생존 추적)는 그대로 두고, 재방문 전용 `revisit_cli`를 신설.
두 파이프라인은 독립 실행된다.

```
[monitor_cli]  셀러 listing 스캔 → 신규/가격/품절·삭제   (가끔 실행, 기존)
[revisit_cli]  due 상품 상세 재fetch → 지표 시계열 누적   (자주 실행, 신규)
```

**revisit_cli 한 실행의 단계:**

```
0단계 (fetch 없음, DB만): 초기 시드 / 스케줄 편입
   revisit_state에 없지만 detail_fetched_at 있는 ACTIVE 상품을 일괄 등록.
   기존 items 지표로 base_tier 계산, last_observed_at = 마지막 stats observed_at,
   next_revisit_at = last_observed_at + interval(base_tier). HTTP fetch 불필요.
   → 첫 실행 때 기존 130만 개가 스케줄에 편입된다 (이게 없으면 재방문 대상이 0).

deadline = 실행 시작 + max-hours

1순위 (fetch): 신규로 채워야 하는 상품 (detail_fetched_at IS NULL인 ACTIVE)
   monitor가 listing에서 발견했으나 아직 첫 상세 fetch를 못 한 stranded.
   첫 상세 fetch → enrich + stats_history 첫 관측 + revisit_state 시드.
   데이터 공백부터 메움. 여기서 예산(시간)이 먼저 소비됨.

2순위 (fetch): 남는 룸으로 재방문 → urgency 높은 순
          urgency = (마지막 관측 후 경과시간) / (티어 목표간격)
          · urgency ≥ 1  = 목표 지남(밀림) → 우선
          · 룸 남으면 urgency < 1도 앞당겨 처리
          · 룸 부족하면 urgency 최상위만, 나머지는 다음 실행
          · 급등(velocity↑) 상품은 목표간격을 짧게 → urgency 가산
```

**예산 기반 효과:**
- 신규가 많은 날 → 신규 먼저 채우고 재방문은 자동으로 줄어듦
- 신규가 적은 날 → 남는 룸 전부 재방문에 투입, urgency 낮은 것까지 앞당김
- 용량이 놀지 않고 항상 "가장 가치 있는 것부터" 채움

**신규 상품 진입:** monitor가 발견·enrich한 상품은 첫 관측이 `stats_history`에 1행 남는다.
revisit_cli가 그것을 시드로 base_tier를 매기고 `revisit_state`에 등록한다.

## 정책 수치

### 티어별 목표 간격 (urgency 분모, soft target)

| 티어 | 목표 간격 |
|---|---|
| HOT | 1일 |
| WARM | 4일 |
| COLD | 30일 |

목표 간격은 마감(deadline)이 아니라 urgency 점수의 분모. HOT은 분모가 작아 같은 경과시간에도
urgency가 높게 나와 우선 처리된다.

### base_tier 재평가 (관측마다)

관측할 때마다 현재 view/fav/inquiry로 위 절대 3-티어 기준을 재계산한다.
인기가 오르면 자연히 상위 티어로 올라가 더 자주 관측된다.

### velocity 산출

`stats_history`의 **최근 2개 관측**으로 정규화:
- `fav_velocity = (fav_now − fav_prev) / Δ일`, view도 동일
- 관측이 1개뿐이면 velocity = None (승급 없이 base_tier로만)
- 대표 지표는 **fav_velocity** (가장 강한 판매 신호, 노이즈 적음), 보조로 view_velocity
- Δ일 = 0 (동일 시각 관측) 가드: velocity = None 처리

### 급등 시 일시 승급

base_tier 위에 velocity가 임계 초과면 **한 단계 승급** (COLD→WARM→HOT), 목표간격이 짧아져 urgency↑:
- 급등 정의(초기 휴리스틱): `fav_velocity ≥ 5/일` **또는** `view_velocity ≥ 100/일`
- **일시적**: 다음 관측에서 velocity가 식으면 base_tier로 복귀

> 급등 임계는 현재 시계열 데이터가 없어 휴리스틱이다. 관측이 몇 사이클 쌓이면
> velocity 분포를 다시 분석해 보정하는 것을 전제로 한다.

### COLD 저빈도

COLD는 목표간격 30일로 거의 갱신만 한다. due가 되면 절대지표를 갱신해 승급(WARM/HOT) 기회를 포착한다.
HOT+WARM이 정밀 시계열, COLD는 저빈도 절대지표 갱신.

## 스키마 추가 (기존 테이블 불변)

```sql
CREATE TABLE IF NOT EXISTS revisit_state (
  item_id          TEXT PRIMARY KEY,
  tier             TEXT,      -- 현재 적용 티어 (승급 반영) HOT/WARM/COLD
  base_tier        TEXT,      -- 절대지표 기준 티어 (승급 전 원래 티어)
  last_observed_at TEXT,      -- 마지막 재방문 시각 (ISO)
  next_revisit_at  TEXT,      -- 다음 due 시각 (ISO) = last_observed_at + interval(tier)
  obs_count        INTEGER,   -- 누적 관측 횟수
  last_velocity    REAL       -- 최근 일평균 증가율 (대표 지표 = fav_velocity)
);
CREATE INDEX IF NOT EXISTS idx_revisit_next ON revisit_state(next_revisit_at);
```

기존 `db.py`는 `CREATE TABLE IF NOT EXISTS`만 쓰고 컬럼 ALTER가 없어, items에 컬럼을 더하지 않고
별도 테이블로 두는 것이 기존 130만 행을 건드리지 않고 안전하다.

## 종료 (시간 예산)

- 실행 시작 시각 + `--max-hours`를 deadline으로 설정.
- 각 워커는 다음 상품을 집기 전 deadline 확인 → 지났으면 자발 종료.
- 상품 단위가 독립적이고 관측 적재가 트랜잭션이라 **중단해도 무손실**. 다 못 돈 due는
  다음 실행이 urgency 순으로 이어받는다 (monitor의 셀러 단위 중단 내성과 동일 원리).

## 산출물 — 급등 상품 조회

velocity·티어가 `revisit_state`에 쌓이므로 간단한 조회 명령을 제공한다 (데이터 축적이 본질, 리포트는 얇게 시작):
- **급등 TOP N**: `last_velocity` (fav_velocity) 내림차순
- 티어 분포 / 관측 커버리지 (몇 %가 2회 이상 관측됐나)
- 비개발자도 쓸 수 있게 표 형태 출력. 나중에 확장.

## 에러처리 (monitor 패턴 재사용)

- 병렬 워커 + `CircuitBreaker`(403/429 누적 시 자발 종료), `on_error` → errors.log(jsonl)
- 재방문 중 404 → DELETED, 품절 페이지 → SOLD_OUT 판정 (기존 `classify_status_from_response` 재사용).
  즉 재방문이 생존 추적도 겸한다.
- fetch는 db_lock 밖, DB 쓰기는 db_lock 안 (기존 규칙 유지)

## 파일 구조

| 파일 | 책임 |
|---|---|
| `crawler/revisit_scheduler.py` | urgency·base_tier·velocity·우선순위 선정 (순수 로직, 테스트 핵심) |
| `crawler/revisit.py` | 실행 오케스트레이션 (워커, fetch, 적재) — monitor.py 패턴 차용 |
| `storage/revisit_repo.py` | revisit_state CRUD, due 조회, 신규(미관측) 조회 |
| `revisit_cli.py` | 진입점 (`--max-hours --workers --sleep --cb-threshold --cb-window-seconds`) |
| `storage/db.py` | `revisit_state` 테이블 추가 |

## 테스트 전략 (TDD)

순수 함수부터:
- `urgency 계산` (경과/간격, 급등 가산)
- `base_tier 판정` (절대 3-티어 경계값)
- `velocity 산출` (2관측 Δ/Δ일, 1관측 시 None, 동일시각 0-division 가드)
- `due/우선순위 선정` (신규 1순위 → urgency 순, 시간 예산 경계)
- `revisit_state upsert` (신규 시드 / 재평가 갱신)
- `초기 시드 backfill` (revisit_state 없는 enriched 상품을 base_tier로 일괄 등록, 이미 등록된 건 건드리지 않음)
- 통합: 인메모리 SQLite + 가짜 client로 한 사이클 end-to-end (0→1→2단계 순서, 시간 예산 경계 무손실 중단)

## 추가: 상주(daemon) 연속 모드 (2026-06-30 구현)

수동 단일패스(`--max-hours N`)에 더해, "한 번 켜두면 계속 도는" 상주 모드(`--loop`)를 추가했다.
- 시작 시 seed_backfill 1회 → 무한 라운드 루프: 매 라운드 미관측 + **due(next_revisit_at ≤ now)** 만 처리 → 큐가 비면 idle 대기(`--idle-minutes`, 기본 10분, 인터럽트 가능) 후 재확인.
- 재방문으로 next_revisit_at이 미래로 밀리고, 시간이 지나 다시 due되면 또 방문 → 영구 순환.
- **관측 시각**: 단일패스는 고정 `now`, 상주 모드는 매 관측 시점의 실제 시각(`clock=now_iso`)으로 찍는다 — 장기 실행 시 velocity Δt 정확성을 위해 필수.
- 정지: SIGINT(Ctrl+C) graceful(진행 중 항목 마치고 종료, 무손실), CircuitBreaker 트립 시 자동 종료. 머신 재부팅 시엔 멈춤(재실행 필요; 완전 무중단은 launchd 추가 — 미구현).
- `get_revisit_queue(conn, limit, due_before=None)`: due_before 주면 due-only 필터.

## 비범위 (YAGNI)

- 완전 동적 velocity 피드백 루프 (자가조정 연속 주기) — 하이브리드로 충분
- 카테고리/가격대 상대 백분위 보정 — 절대 기준 채택
- cron 자동화 — 수동 실행. 추후 필요 시 추가
- 정교한 리포트 UI — 얇은 조회 명령으로 시작
