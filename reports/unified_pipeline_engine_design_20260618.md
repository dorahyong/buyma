# 통합 파이프라인 엔진 설계 (okmall + multisource 합치기, resume, 병렬, register/stock 동시)

작성 2026-06-18. 요구 #1(합치기)·#2(resume)·#3(병렬·배리어제거)·#4(register/stock 동시)를 한 엔진으로.

---

## 0. 배경 / 현황 (DB 확인)

- `pipeline_batches` / `pipeline_control` 은 **라이브**. 오늘(6/18) RUNNING 배치 존재, pipeline_control 42,976행, mall_name = {`_ALL_`, `kasina`, `okmall`}.
- ⚠️ **최근 10일 배치 전부 FAILED, success_brands=0** — okmall 등록(orchestrator) 파이프라인이 매일 실패 중. 본 rework 와 별개로 원인 파악 필요(아마 이 통합으로 자연 해소).
- 스키마 변경은 **하위호환**(ADD COLUMN + DEFAULT)만. 돌고 있는 orchestrator 깨지면 안 됨.

## 1. 두 아키텍처 (현재)

| | okmall `orchestrator.py` (좋음) | `run_daily_multisource(_merge).py` (고칠 것) |
|---|---|---|
| 단위 | 브랜드(mall_brands) | 몰 전체, phase 일괄 |
| 병렬 | 유닛 파이프라인 병렬 + stage 세마포어 | **phase 배리어** (느린 몰이 전체 막음) |
| resume | DB(pipeline_batches/control), DONE 스킵 | 없음 |
| stock | register 뒤 별도 | Phase3에 끼임 |

목표: **multisource 를 orchestrator 엔진 위로 올리고, 그 엔진을 재사용 모듈로 추출**.

## 2. 엔진 모듈 `pipeline_engine.py` (신규, okmall/ 또는 repo root)

orchestrator.py 의 검증된 로직을 몰/유닛 무관하게 일반화. 핵심 클래스:

```
class PipelineEngine:
    def __init__(self, run_mode, units, stage_plan, worker_resolver, max_workers=None)
    def run()                       # 배치 get_or_create → 유닛별 파이프라인 병렬 → 마감
    # 내부: get_or_create_batch / get_stage_status / update_stage_status /
    #       run_unit_pipeline(unit) / acquire stage semaphore
```

- **units**: `[{mall, unit_key, track, ...}]` — 처리 단위. okmall=브랜드(unit_key=브랜드명, mall=okmall), multisource=몰(unit_key='_ALL_', mall=몰명).
- **stage_plan**: track 별 stage 순서. 예) NEW=`[COLLECT,CONVERT,PRICE,TRANSLATE,IMAGE,REGISTER]`, STOCK=`[STOCK_REFRESH,RECONCILE]`.
- **worker_resolver(mall, unit_key, stage) -> [cmd, ...]**: 실행할 subprocess 명령 리스트(몰별 분기). 현 orchestrator.execute_worker 의 if/elif 를 이 함수로 분리.
- **resume**: run_unit_pipeline 이 각 stage 전 `get_stage_status`→DONE이면 스킵(orchestrator 그대로).
- **병렬/배리어 제거**: 유닛마다 1 스레드가 자기 stage 체인 순차 진행. 유닛 간엔 배리어 없음 → 수집 끝난 유닛부터 다음 단계(#3). stage별 `Semaphore(n)` 로 리소스 보호(예: COLLECT=2, REGISTER=4).
- **register/stock 동시(#4)**: NEW 트랙 유닛들과 STOCK 트랙 유닛들을 **같은 ThreadPoolExecutor 에 동시 제출**. 안전성은 reconcile 그룹락+scope 게이트가 보장(아래 §5).

## 3. 스키마 마이그레이션 (하위호환)

`pipeline_control` 에 컬럼 추가 (기존 orchestrator 는 default 로 무영향):

```sql
ALTER TABLE pipeline_control
  ADD COLUMN unit_key VARCHAR(100) NULL AFTER brand_name,   -- 일반 단위키(브랜드명 or '_ALL_')
  ADD COLUMN track ENUM('NEW','STOCK') NOT NULL DEFAULT 'NEW' AFTER run_mode;
-- stage ENUM 확장 (STOCK 트랙용)
ALTER TABLE pipeline_control
  MODIFY COLUMN stage ENUM('COLLECT','CONVERT','PRICE','MERGE','TRANSLATE','IMAGE','REGISTER',
                           'STOCK_REFRESH','RECONCILE') NOT NULL;
```

- 유니크키: 기존 `(batch_id, mall_name, brand_name, stage)` → track 추가 필요.
  - **무중단 전략**: 돌고 있는 배치 끝난 뒤(또는 점검창) `DROP INDEX uk_batch_brand_stage; ADD UNIQUE (batch_id, mall_name, brand_name, track, stage)`.
  - 신규 엔진은 brand_name 에 unit_key 를 그대로 기록(okmall=브랜드, multisource=몰명)하여 기존 컬럼 재활용 → unit_key 컬럼은 선택(가독성용). track 만 유니크키에 필수.
- run_mode ENUM 은 그대로(FULL/PARTIAL). STOCK 트랙도 run_mode 는 FULL.

## 4. 통합 run 파일 `run_daily_unified.py` (얇은 설정층)

엔진에 넘길 "설정+배선"만:

```
UNITS_NEW   = okmall 브랜드들(mall_brands is_active) + 8몰(unit_key='_ALL_')   # NEW 트랙
UNITS_STOCK = 같은 몰 집합(stock 대상)                                          # STOCK 트랙
STAGE_PLAN  = {NEW:[...REGISTER], STOCK:[STOCK_REFRESH,RECONCILE]}
WORKER = worker_resolver(mall, unit_key, stage):
   # NEW:    okmall→ 기존 okmall 워커 / 8몰→ collector·converter·price·translate·image / REGISTER→ reconcile auto --scope new
   # STOCK:  STOCK_REFRESH→ */stock_*_merge.py(--refresh-only 모드) / RECONCILE→ reconcile auto --scope published
engine = PipelineEngine('FULL', UNITS_NEW+UNITS_STOCK, STAGE_PLAN, WORKER)
engine.run()
```

- REGISTER stage = `reconcile_runner --mode auto --scope new`(이미 1차 cutover 와 동일).
- STOCK 트랙: 현재 `*_merge.py` 가 "refresh + run끝 reconcile" 를 한 프로세스에 묶어둠 → 엔진에선 stage 2개로 분리하거나, 당분간 `*_merge.py` 1콜을 RECONCILE stage 로 통째 매핑(가장 작은 변경). 분리는 후속.
- 인자: `--phase` 대신 resume 가 대체. `--source`(특정 몰), `--unit`(특정 브랜드/몰), `--track new|stock|all`, `--dry-run`.

## 5. register/stock 동시 안전성 (#4) — 근거

- scope 분리: register=`scope=new`(n_pub==0 그룹만 CREATE), stock=`scope=published`(n_pub≥1 EDIT/RETIRE). 그룹집합 분리.
- 같은 그룹이 new→published 로 전이하는 순간도 **그룹락(GET_LOCK 서버전역) + 락후 n_pub 재확인** 으로 직렬화 → 중복생성/충돌수정 불가. (multi-PC 샤딩과 동일 안전성, 이미 검증)
- COLLAPSE(n_pub≥2)는 auto 가 **자동 안 하고 스킵** → mass 동시실행에도 안전.
- 트레이드오프: 이번 회차 신규등록 상품은 다음 회차에 stock refresh(등록시 최신데이터라 무손실).

## 6. 롤아웃 순서

1. (병행) 50k 일괄적용 dry-run 리포트 → 점진 적용. [독립]
2. `pipeline_engine.py` 추출 + 단위테스트(okmall 1브랜드 dry-run 으로 기존 orchestrator 와 동작 일치 확인).
3. 스키마 마이그레이션(track 컬럼 + 유니크키, 점검창).
4. `run_daily_unified.py` 작성 → dry-run → 소수 유닛 실검증.
5. cron 교체: run_daily.py(okmall)·run_daily_multisource(_merge).py → run_daily_unified.py.
6. (후속) naver 도 같은 엔진 채택 / STOCK 트랙 stage 분리 / 단일권위 마이그레이션.

## 7. 위험 / 메모

- okmall 10일째 FAILED 원인 — 통합 전 한 번 확인(워커 어느 stage 에서 죽는지 pipeline_control.error_msg 조회).
- 스키마 유니크키 변경은 라이브 배치 종료 후.
- `*_merge.py` 는 이미 refresh+reconcile 묶음 → 엔진 STOCK 트랙에 그대로 얹으면 최소변경.
- okmall stock(okmall/stock_price_synchronizer_merge.py)도 STOCK 트랙에 포함(8몰 + okmall).
