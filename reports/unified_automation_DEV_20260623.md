# 통합 자동화 — 개발자 문서 (2026-06-23)

전 몰 BUYMA 자동화를 단일 엔진(`run_daily_unified.py` + `pipeline_engine.py`)으로 통합하고, 옛 "싼몰 죽이기(dedup)"를 "병합(reconcile)"으로 전환한 작업 전체 기록. 코드 기준 재검증됨.

관련 문서: `reports/model_merge_session_20260618.md`(reconcile 엔진), `reports/one_mall_cycle_process_20260623.md`(한 몰 사이클), `reports/unified_pipeline_engine_design_20260618.md`(엔진 설계), 메모리 `model_merge_reconcile.md`.

---

## 1. 목표 / 아키텍처

### 목표
여러 몰에 흩어진 같은 품번을 BUYMA 1개 출품으로 묶어, 제일 싼 몰에서 사게(winner), 살 수 있는 옵션(색·사이즈)을 다 모아(옵션 합집합) 올리고 유지. 옛 `dedup_corrector`(우선순위 몰만 남기고 나머지 `is_active=0` 죽이기)를 대체.

### 2층 데이터 구조
- **입력층(수집)**: `raw_scraped_data` → `ace_products`(몰별 상품) + `ace_product_variants`(옵션·재고) + `ace_product_images`.
- **출품층(reconcile가 채움)**: `buyma_listings`(병합 출품 1개) / `source_offerings`(이 그룹을 먹이는 몰들) / `source_offering_options`(멤버별 옵션) / `listing_options`(BUYMA로 보낸 합집합 옵션) / `listing_images`.

### reconcile (핵심 엔진, 기존 — okmall/)
- `reconcile_runner.py --mode auto --scope {new|published}`: 상품 그룹 선정 → 그룹락(GET_LOCK 서버전역) → `ensure_group`(즉석 빌드: 그룹핑+winner+옵션union) → 분류 → BUYMA push.
- 분류(게시멤버 수 n_pub): 0→CREATE / 1→EDIT / 2+→COLLAPSE(자동 안 함, 수동검토) / 마진X·전체품절+live→RETIRE(삭제).
- scope: `new`=미등록 그룹만(register 역할, CREATE) / `published`=등록 그룹(stock 역할, EDIT·RETIRE).
- 그룹핑 정규화는 `dedup_corrector_merge.canonicalize`(과병합 fuzzy 포함 — **이번 작업 범위 아님**, 그룹핑 규칙은 안 건드림).

---

## 2. 통합 엔진 — `pipeline_engine.py` (신규)

okmall `orchestrator.py`의 resume+유닛병렬 엔진을 몰/유닛 무관하게 일반화. 비즈니스 로직 0 — 기존 스크립트를 subprocess로 호출하는 실행기.

### 클래스 `PipelineEngine(run_mode, units, stage_plan, worker_resolver, stage_concurrency=None, site_access_stages=None, max_workers=None, dry_run=False)`
- **unit** = `{mall, unit_key, track('NEW'|'STOCK'), site_resource}`. 통합은 몰 단위(unit_key='_ALL_').
- **stage_plan** = `{'NEW':[...], 'STOCK':[...]}`.
- **worker_resolver(unit, stage) → List[List[str]]**: 실행할 subprocess 명령들. 빈 리스트면 no-op(DONE).
- **resume**: `pipeline_batches`(하루 1배치) + `pipeline_control`((batch,mall,unit_key,track,stage) 상태). 오늘 RUNNING 배치 있으면 이어받고, DONE stage 스킵. 어제 RUNNING은 FAILED 마감.
- **병렬**: 유닛마다 1 스레드가 자기 stage 체인 순차 진행. phase 배리어 없음 → 수집 끝난 유닛부터 다음 단계.
- **stage 세마포어**: `stage_concurrency`로 stage별 동시 상한(현재 미사용=무제한).
- **site 자원 잠금(★)**: `site_access_stages`에 든 stage는 `unit.site_resource` 세마포어(자원당 1개) 추가 획득. `site_resource=None`이면 잠금 없음. → naver 21몰만 `site_resource='naver'`(캡챠로 collector·stock 1개씩), 나머지는 None(전부 병렬). 락 순서 stage_sem→site_sem 일정 → 데드락 없음.
- **DB 연결**: 작업마다 새 연결(`_db()`). 옛 버전이 연결 1개를 수십 분 작업 내내 들고 있다 유휴 타임아웃으로 끊겨 상태기록 실패하던 버그 수정.
- **실패처리(resume)**: `run()`이 `any_failed` 추적 → 실패 유닛 있으면 `finish_batch` 안 불러 배치 **RUNNING 유지**(재실행 시 ERROR/미완 단계만 재시도, 성공 유닛 DONE 스킵). 전부 성공 시에만 COMPLETED.
- `finish_batch`: 모든 유닛 성공 시에만 호출되므로 `success_brands=len(units)`.

### 한 run = 한 바퀴
`python run_daily_unified.py` 1회 = 62유닛 전부 완주 시 종료(프로세스 자체는 무한루프 아님). "상시 가동"은 끝나면 재실행(수동→배치 루프).

---

## 3. 배선 — `run_daily_unified.py` (신규)

엔진에 설정 주입. **31몰 / 62유닛** = okmall 1 + 멀티소스 9(kasina·nextzennpack·labellusso·9tems·brickmansion·loromoda·milaneez·maisonparco·musinsa) + naver 21.

### stage_plan
- NEW = `COLLECT → CONVERT → PRICE → TRANSLATE → IMAGE → REGISTER`
- STOCK = `STOCK_REFRESH` (한 스크립트가 내부에서 재수집 + reconcile push 수행)

### worker_resolver 명령 (몰군별, 기존 스크립트 그대로)
| stage | okmall | 멀티소스 9 | naver 21 |
|---|---|---|---|
| COLLECT | `okmall_all_brands_collector.py --skip-existing` | `{mall}_collector.py --skip-existing` (+카테고리: brickmansion/loromoda/maisonparco `--categories`, milaneez `--map-categories`) | 3종 공용 collector `--source {mall} --skip-existing` (브랜드형 6 / 브랜드스토어 carpi·joharistore / 카테고리형 13) |
| CONVERT | `raw_to_ace_converter.py --source okmall --skip-translation` | `kasina/raw_to_converter_kasina.py --source-site {mall} --skip-translation` | 멀티소스와 동일(kasina converter) |
| PRICE | `buyma_lowest_price_collector.py --source {mall} --new-only` (공통) | | |
| TRANSLATE | `convert_to_japanese_gemini.py --source {mall} --price-checked-only` (공통) | | |
| IMAGE | `image_collector_parallel.py --source okmall --price-checked-only` + `r2_image_uploader.py --source okmall` | `r2_image_uploader.py --source {mall}` (업로드만) | 멀티소스와 동일(업로드만) |
| REGISTER | `reconcile_runner.py --mode auto --scope new --source {mall} --limit 100000 --execute --confirm-live` (공통) | | |
| STOCK_REFRESH | `okmall/stock_price_synchronizer_merge.py` | `{mall}/stock_price_synchronizer_{mall}_merge.py` | `naver/stock_price_synchronizer_naver_merge.py --source {mall}` (공용 1개) |

### site_resource
`build_units`: naver 몰 → `'naver'`, 그 외 → `None`. `SITE_ACCESS_STAGES={'COLLECT','STOCK_REFRESH'}`.

### CLI
`--plan`(명령만 출력, DB·실행 없음) / `--dry-run`(엔진 가동, 명령 no-op) / `--only {mall}` / `--track {new|stock|all}` / `--max-workers N`.

---

## 4. STOCK 2차 전환 — `*_merge.py` 11개

옛 stock(몰마다 BUYMA 직접 push)을 "재수집만 + 끝에 reconcile push"로 전환. 대상: okmall + kasina/nextzennpack/labellusso/9tems/brickmansion/loromoda/milaneez/maisonparco/musinsa + naver = 11개.

### 5가지 변경 (변환기 `_make_stock_merge.py`로 일괄)
1. 상단: win32 stdout wrap 제거 + `import reconcile_runner` + `sys.path.insert(0, .../'okmall')` (okmall 폴더는 sys.path 불필요).
2. 메서드 2개 추가: `_mark_all_out_of_stock(ace_id)`(변이 전부 out_of_stock), `_reconcile_published(products)`(이번 synced 그룹만 — model_no IN(...) + brand_id + canonical dedup → `process_one_group(scope='published')`).
3. 404/삭제/품절/흠집 분기: BUYMA 직접삭제 → `_mark_all_out_of_stock` + sync time(판단은 reconcile).
4. step 7/8: 항상 refresh + BUYMA push 제거(refresh-only).
5. run() 끝(세션정리 후): `if not dry_run: self._reconcile_published(products)`.

### 변환기 안전장치
`apply_one`이 각 hunk가 정확히 1회 매칭 안 되면 RuntimeError(파일 미생성). naver는 run() 끝에 `차단(중단)` 로그 줄이 더 있어 H5만 `H5_OVERRIDE['naver']`로 처리. 11개 전부 compile OK, 이중 push 잔존 0(검증됨).

---

## 5. 가격 정책 동기화 — `okmall/resolve_merge.py`

reconcile 가격계산이 운영 11개 파일(커밋 `10baa77`, 6/15)과 어긋나 있던 것 수정:
- 무경쟁 목표마진 `0.20 → 0.30` (L67 함수기본값, L185 호출부).
- 경쟁자 있을 때 `selling=competitor`(언더컷 없음) → **band-keep 언더컷**: 현재가가 `[competitor-9, competitor-1]` 범위면 유지, 아니면 `competitor - random(1~9)`. 신규(현재가 0)는 자동 언더컷 = register 동일.
- `import random` 추가. 새 로직 아님 — 운영 권위(`buyma_lowest_price_collector.py`)에 맞춘 복사본 동기화.

---

## 6. DB 스키마 변경 (`migrations/2026_pipeline_control_track.sql`, 전부 적용됨)

MariaDB 11.4.2. `pipeline_control`:
1. `track ENUM('NEW','STOCK') NOT NULL DEFAULT 'NEW'`, `unit_key VARCHAR(100)` 추가 + 43,015행 백필(unit_key=brand_name).
2. `stage` ENUM에 `STOCK_REFRESH`, `RECONCILE` 추가.
3. 유니크키 4컬럼→5컬럼: `uk_batch_brand_stage` DROP → `uk_batch_unit_track_stage(batch_id, mall_name, brand_name, track, stage)`. 적용 전 5컬럼 중복 0 확인, 원자적 단일 ALTER(키 없는 창 방지). 새 키는 옛 키 상위집합 → 기존 데이터 위반 없음, orchestrator(track 미지정→기본 NEW) 무영향.
4. `pipeline_batches`/`pipeline_control` `run_mode` ENUM에 `'UNIFIED'` 추가(통합러너 전용, 레거시 FULL 배치와 분리).

호환성: 기존 okmall orchestrator는 track 미기록 → DEFAULT 'NEW'. ON DUPLICATE KEY UPDATE는 5컬럼 키에서도 track='NEW'로 정상 매칭. **레거시 무영향 확인.**

---

## 7. 명령어

```bash
# 통합 자동화 (한 바퀴)
python run_daily_unified.py                          # 31몰 NEW+STOCK
python run_daily_unified.py --only kasina --track stock   # 특정 몰/트랙
python run_daily_unified.py --plan                   # 명령만 출력(안전)
python run_daily_unified.py --dry-run                # 엔진 가동, 명령 no-op

# reconcile 직접 (REGISTER 경로)
python okmall/reconcile_runner.py --mode auto --scope new --source 9tems --limit 100000 --execute --confirm-live

# stock _merge 직접 (소량 검증)
python {mall}/stock_price_synchronizer_{mall}_merge.py --limit 5

# naver 선행: 쿠키 로그인
python naver/premiumsneakers/premiumsneakers_collector.py --login
```

---

## 8. 운영 검증 실적 (실제 BUYMA)

- **9tems stock**: published 883 재수집 → reconcile EDIT **773 성공(전부 201)/실패 0/스킵 80**. 가격검증: 무경쟁 30%(¥94,776 정확), 경쟁자 −1~9엔 언더컷(¥224,434→¥224,431).
- **9tems new**: 181그룹 → CREATE **49 성공(201)/실패 0/스킵 132**(마진X품절 118 + 이미등록 14).
- kasina stock(--limit 5) end-to-end, dry-run/엔진 연동/resume/site-lock 격리 테스트 PASS.

---

## 9. 이번 작업에서 발견·수정한 버그

| 버그 | 원인 | 수정 |
|---|---|---|
| 가격 reconcile 20% (운영 30%) | 6/15 커밋이 11개 파일만 바꾸고 resolve_merge 누락 | 30%+언더컷 동기화 |
| REGISTER가 3건만 등록 | reconcile_runner 기본 `--limit=3`, 통합러너 누락 | `--limit 100000` 추가 |
| 긴 작업 후 상태기록 실패 | 엔진이 DB연결 1개를 수십 분 보유 → 유휴 타임아웃 | DB 작업마다 새 연결(`_db()`) |
| 유닛 실패해도 배치 COMPLETED → 이어돌기 안 됨 | finish_batch 무조건 호출 | any_failed면 RUNNING 유지 |
| finish_batch 완료수 오집계 | stage IN(NEW말,STOCK말) track 무시 | `len(units)` |
| 빈 units → ThreadPoolExecutor(0) ValueError | `min(len,8)` | `max(1, ...)` |
| (운영 사고) 9tems new REGISTER 실패 | 실행 중 git stash로 reconcile_runner.py 일시 제거 | 실행 중 git작업 금지 |

### 발견했으나 본 작업 범위 아님(타 개발자/별도)
- `9tems_brand_collector.py`·`musinsa_collector.py`가 `is_active=1` 자동설정(정책=시스템 NULL만). → 다른 개발자 처리.
- 옛 그룹핑 fuzzy 과병합(`dedup_corrector_merge.canonicalize`). → 별도 과제.

---

## 10. 남은 작업

### A. cutover (운영 전환 — 사용자 결정)
매일 돌리는 명령을 옛 `run_daily.py --source okmall` + `run_daily_multisource.py`(+naver) → **`run_daily_unified.py` 하나**로. 자동 스케줄러 없음(수동/추후 배치 루프). 롤백=옛 명령 복귀. ※okmall 매일 FAILED(354브랜드 못 끝냄+강제종료)는 통합으로 해소. naver 쿠키는 사용자 처리.
- **50k 일괄 불필요**: STOCK이 매 바퀴 published 전체(`get_products_to_sync` limit/날짜 필터 없음, `checked_at ASC` 정렬)를 돌므로 상시 가동이면 자동 소급 정리.

### B. 단일권위 마이그레이션 (구조 정리, 후순위·고위험)
BUYMA 식별/상태의 권위를 ace_products → buyma_listings로. **현 구도 = 쓰기는 양쪽(dual-write 완료), 읽기는 아직 ace_products.**
- 옮길 컬럼: buyma_product_id, reference_number, is_published, status, is_buyma_locked, locked_* (전부 buyma_listings에 이미 존재). 추가 필요: **`buyma_registered_at` 1컬럼**.
- 읽기 전환 대상(위험 역순): 관리페이지(`manage_server/products_api.py`, 표시용·저위험) → 통계(`buyma_stats/build_merged_dataset.py` 등, buyma_product_id가 조인 유일키) → 클리너(`buyma_orphan_cleaner.py` 등, 라이브 BUYMA 삭제판단·최고위험).
- 웹훅(`okmall_reference/server.py`): 이미 5이벤트 dual-write. 남은 결정 = `buyma_registered_at` 처리위치, `ace_product_api_logs` FK 귀속단위, 1:N fan-out(한 listing ↔ 여러 ace).
- 권고: cutover로 병합 구조 검증 후 착수. 관리페이지부터.

### deferred
naver N4 실검증(쿠키), 과병합 분해, get_or_create_batch 멀티프로세스 동시기동(GET_LOCK), 한글 옵션·이름 번역.

---

## 11. 파일 요약 (이번 작업 산출물, 미커밋 로컬)

신규: `pipeline_engine.py`, `run_daily_unified.py`, `_make_stock_merge.py`, `migrations/2026_pipeline_control_track.sql`, `*/stock_price_synchronizer_*_merge.py` 11개, reports/*.
수정: `okmall/resolve_merge.py`(30%+언더컷), `naver/stock_price_synchronizer_naver.py`(주석 11→21).
기존 재사용(안 건드림): reconcile 계열, buyma_new_product_register, 마진/필드 매핑.
