# 통합 자동화 — 3개 러너 → `run_daily_unified.py` 1개 통합 (전체 정리)

> 작성 2026-06-29. 옛 일일 자동화 3개 명령을 `run_daily_unified.py` 한 파일로 합치면서 바뀐 모든 것을
> **기능적(무엇이 어떻게 좋아졌나)** + **기술적(코드가 어떻게 동작하나)** 으로 정리.
> 비개발자 요약은 `reports/unified_automation_OVERVIEW_20260623.md`, 엔진 설계는 `reports/unified_pipeline_engine_design_20260618.md` 참조.

---

## 0. 한눈에 — 무엇을 합쳤나

| 옛날 (3개 명령 따로) | 지금 (1개 명령) |
|---|---|
| `okmall/run_daily.py --source=okmall` | |
| `run_daily_multisource.py` (멀티소스 9몰) | **`run_daily_unified.py`** |
| `run_daily_naver.py` / `run_phase6_naver_sequential.py` (네이버 21몰) | |

- **31몰** = okmall 1 + 멀티소스 9(kasina·nextzennpack·labellusso·9tems·brickmansion·loromoda·milaneez·maisonparco·musinsa) + 네이버 21.
- 한 번 실행 = **한 바퀴**(31몰 전 단계). 끝나면 또 실행 → 상시 자동화.
- 분리 실행 옵션: `--no-naver`(okmall+멀티소스만), `--only-naver`(네이버만). 둘은 **다른 배치(run_mode)** 라 동시에 돌려도 충돌 없음.

대표 변경 5가지(이 문서 본문):
1. **중복 고도화** — "죽이기" → "병합(reconcile)"
2. **자동화 고도화** — phase 배리어식 3러너 → 유닛 파이프라인 엔진(resume·병렬·이력)
3. **상품 delete API → 재고 API** — 품절을 삭제가 아닌 "출품정지중"으로
4. **stock 파일의 역할 변화** — BUYMA 직접 수정/삭제 → DB refresh만 + reconcile에 위임
5. **그 외** — 가격통일·등록 방식·네이버 통합·dry-run 의미 등 (§6)

---

## 1. 옛 구조 (왜 합쳤나)

### 1-1. 명령이 3개로 쪼개져 있었다
- okmall: `run_daily.py` → `orchestrator.py`(6단계) → `stock_price_synchronizer.py`.
- 멀티소스: `run_daily_multisource.py` → Phase1 수집 → Phase2 변환+Dedup → Phase3 가격→번역→이미지→재고 → Phase4 등록.
- 네이버: `run_daily_naver.py` → Phase1~6 (수집 직렬 → 변환+Dedup → 가격+이미지 → 번역 → 등록 → 재고). 캡챠 때문에 Phase6는 `run_phase6_naver_sequential.py`로 mall 1개씩 따로 돌려야 했음.

### 1-2. 옛 구조의 문제 (그냥 넘기던 것들)
- **이력 없음**: "지금 어느 몰이 어디까지 됐나"를 알 수 없음.
- **중단되면 처음부터**: Phase 도중 꺼지면 그 Phase를 다시. okmall은 354브랜드를 하루에 못 끝내고 매일 강제종료 → 매일 FAILED.
- **Phase 배리어 병목**: "전 몰이 Phase1 끝나야 Phase2 시작". 느린 몰 하나가 전체를 막음. 특히 네이버는 중복검증용 수집 병목을 의도적으로 넣어 더 느림.
- **신규/재고 따로**: 신규등록 한 바퀴, 재고동기화 또 한 바퀴.
- **중복은 "죽이기"**: 더 싼 소싱처를 비활성화해 비싸게 마진계산 + 옵션 못 모음(§2).

---

## 2. 중복 고도화 — "죽이기" → "병합(reconcile)"

### 기능적
같은 품번(model_no)이 여러 소싱처에서 수집될 때:
- **옛날(죽이기)**: 우선순위 1몰만 남기고 나머지 `is_active=0`. → ①더 싼 몰을 죽여 비싸게 마진계산 ②몰마다 다른 색·사이즈를 못 합침.
- **지금(병합/reconcile)**: 같은 품번을 **BUYMA 출품 1건으로 병합**.
  - **제일 싼 소싱처를 winner**로 골라 그 데이터로 출품·마진계산.
  - 출품 가능한 **옵션(색·사이즈)을 전 소싱처에서 합집합**으로.
  - 마진 안 나거나 전체품절이면 출품 안 함 / 내림.
- **신규 등록이든 재고관리든 같은 병합 로직** 사용.

### 기술적
- 그룹핑 기준: **같은 브랜드 + 품번 정규화값 일치**. 정규화 = 끝 괄호코드 제거 → `A/B`는 뒤만 → 시즌접두 제거 → 공백·하이픈·슬래시·따옴표 제거+대문자. (`dedup_corrector_merge.py canonicalize()`)
- merge 전용 테이블 5개: `buyma_listings`(출품 1건=그룹), `source_offerings`(소싱처별 멤버), `source_offering_options`, `listing_options`(합집합 옵션), `listing_images`(합친 이미지). FK CASCADE.
- 엔진: `okmall/reconcile_runner.py` — 그룹별로 winner 선정(최저 매입가+마진OK) → ensure_group(멤버/옵션/이미지 적재) → BUYMA push(`reconcile_buyma_push.py`).
- 등록(REGISTER) = `reconcile_runner --mode auto --scope new` (미등록 그룹만 CREATE).
- 재고(STOCK) = stock이 refresh 후 **이번에 건드린 그룹만** reconcile(scope published)로 push(§5).

---

## 3. 자동화 고도화 — Phase 배리어식 3러너 → 유닛 파이프라인 엔진

핵심 = **`pipeline_engine.py`** (okmall orchestrator의 resume+병렬 엔진을 몰/유닛 무관하게 일반화).
`run_daily_unified.py`는 "무엇을 어떻게 돌릴지"(몰 목록·단계·명령)만 주입하는 **설정층**(비즈니스 로직 0).

### 3-1. 유닛(unit) 단위 + 트랙(track) 동시
- **유닛** = (몰, 트랙). 트랙은 **NEW**(COLLECT→CONVERT→PRICE→TRANSLATE→IMAGE→REGISTER)와 **STOCK**(STOCK_REFRESH) 둘.
- 31몰 × 2트랙 = 유닛 목록. **NEW(신규등록)와 STOCK(재고관리)을 동시 진행**.
- *기능적*: 신규 채우기와 기존 재고갱신이 한 바퀴에 같이 돈다.

### 3-2. Phase 배리어 제거 (병목 해소)
- 옛날: "전 몰 Phase1 끝 → Phase2". 지금: **유닛마다 독립 파이프라인**. A몰이 PRICE일 때 B몰은 아직 COLLECT여도 됨.
- *기술적*: `run_unit_pipeline(unit)`이 그 유닛의 stage 체인을 순차 진행, 유닛끼리는 ThreadPoolExecutor로 병렬. 전역 배리어 없음.

### 3-3. Resume (이어돌기 — 이미 한 일은 다시 안 함)
- *기능적*: 중간에 꺼져도 다시 켜면 끝낸 단계는 건너뛰고 멈춘 데서 이어감.
- *기술적*:
  - `pipeline_batches`(한 바퀴=한 배치) + `pipeline_control`(몰×트랙×단계별 상태 PENDING/RUNNING/DONE/ERROR).
  - `get_or_create_batch()`: **RUNNING 배치가 있으면 날짜 무관 이어받기**(자정 넘겨도 OK), 없으면 새 배치. 오래된 RUNNING stray는 FAILED 정리.
  - stage가 `DONE`이면 스킵. **실패 유닛이 있으면 배치를 COMPLETED로 안 닫고 RUNNING 유지** → 재실행 시 실패/미완 단계만 재시도.
  - DB 연결을 **작업마다 새로 열고 닫음**(수십 분 subprocess 도중 유휴 타임아웃으로 상태기록 실패하던 버그 방지).

### 3-4. 이력 (현황 파악)
- `pipeline_control`에 어느 몰이 어느 트랙·단계까지 됐는지/어디서 실패했는지 기록.

### 3-5. 네이버만 직렬 — 사이트 접속 자원 락
- *기능적*: 네이버는 캡챠 때문에 브라우저 2개↑ 동시 접속하면 막힘 → **사이트 접속(수집·재고)은 한 번에 하나씩**. 나머지 몰은 전부 병렬.
- *기술적*:
  - 유닛에 `site_resource` 부여. 네이버 21몰은 전부 `'naver'`(쿠키·캡챠 공유), 독립 9몰+okmall은 `None`(잠금 없음).
  - **메인 풀(비-naver)** + **naver 전용 풀**을 동시 가동. naver 풀에서도 단계는 병렬(누구는 convert, 누구는 price…)이되 **`SITE_ACCESS_STAGES={COLLECT, STOCK_REFRESH}` 단계만 `site_sem`(자원당 1개)로 1개씩 직렬**.
  - → "한 collector 끝나면 다음 collector"로 사이트 레인이 늘 차고, 비-naver 슬롯이 굶지 않음(옛 단일풀 맨끝 몰림 해소).
  - 락 순서 항상 stage_sem → site_sem 으로 고정 → 데드락 없음.

### 3-6. dry-run / plan 의미
- `--plan`: DB·실행 0, **명령만 출력**.
- `--dry-run`: **엔진은 가동(배치·상태기록 = bookkeeping 테이블 씀)**, 실제 워커 명령만 no-op 로그. (즉 dry-run도 `pipeline_batches/pipeline_control`은 기록 — 운영 데이터는 안 건드림.)

---

## 4. 상품 delete API → 재고 API (commit 81a0a72, 2026-06-26)

### 기능적
품절·마진미달로 상품을 **내릴 때, "삭제"가 아니라 "출품정지중(전 옵션 품절)"** 으로 변경.

| | 옛날 | 지금 |
|---|---|---|
| 방식 | 상품 삭제(상품수정 API `control:delete`) | 재고 API(`variants.json`) 전옵션 out_of_stock + order_quantity:0 |
| buyma_product_id | 사라짐 | **유지** |
| 등록일(게시일수) | 초기화 | **유지** |
| 재입고 시 | 신규로 다시 등록 | **같은 id로 자동 부활** |

- 바이마 사무국 권고("품절은 상품수정 API 말고 재고 API 써라")와 일치. 실제 BUYMA 품절↔부활 왕복 + id/등록일 불변 POC 검증 완료.
- **예외(진짜 삭제 유지)**: 중복(COLLAPSE)·흠집 상품은 의도적으로 delete.

### 기술적
- `reconcile_buyma_push.execute_retire`: `control:delete` → `reg.call_buyma_variants_soldout(ref, opts)`.
- `buyma_new_product_register.call_buyma_variants_soldout()` 신규(재고 API 품절 호출, order_quantity:0 필수).
- 웹훅 `okmall_reference/server.py`: `product/update status='buyer_suspended'` → `is_published=0, status='soldout'` (ace_products + buyma_listings dual-write). 실제 출품정지중 전이는 이 웹훅이 반영.

---

## 5. stock 파일의 역할 변화 (가장 중요한 구조 변화)

### 기능적
옛 stock = "수집처 재수집해서 **BUYMA를 직접 수정/삭제**". 지금 stock(`_merge`) = "**DB만 최신화(refresh)**, BUYMA 반영은 reconcile에 맡김".

### 기술적 — `stock_price_synchronizer_*_merge.py` 11개 동작
1. 상품별로 수집처에서 가격/재고 재수집 → 최저가 수집 → 마진 계산.
2. **결과를 `ace_products`/variants에 refresh만** 함. (마진 안 나도 refresh — reconcile이 판단하도록 최신화)
   - okmall이 품절/삭제/흠집이어도 **BUYMA 직접 안 건드림**, okmall 옵션만 재고0 표시 → 다른 몰 있으면 winner 이동, 없으면 reconcile이 retire.
3. **`process_single_product`에서 BUYMA push 생략**(라인 1659). `self.call_buyma_api(` 호출 = **11개 전부 0건**.
4. `run()` 끝에서 `_reconcile_published(products)` — **이번 회차에 refresh한 상품들의 그룹만** reconcile(옵션합침+싼몰+수정/삭제 판단)으로 BUYMA push. 그룹락으로 multi-PC 안전.

### 결과
- unified에서 **품절로 `control:delete`가 나가는 경우 없음** — 모든 BUYMA push는 reconcile 경유(retire = §4 재고 API).
- *주의(잔재)*: `_merge`에 build_buyma_request의 `control:delete`가 **dead code**로 남아있음(호출 안 됨). 옛 비-merge synchronizer들은 여전히 control:delete지만 **unified에서 미참조**(롤백 시에만 노출).

---

## 6. 그 외 변경

### 6-1. 가격 통일 (마진 20% → 30% + 언더컷)
- 경쟁자 있으면 **최저가−1~9엔**(범위 안이면 유지), 없으면 **매입가 기준 마진 30%** 재계산.
- *기술적*: `resolve_merge.py`/`stock_*_merge.py` 분모 `(1−0.055)−0.30 = 0.645`.

### 6-2. 등록 방식 = reconcile (개별 register 아님)
- 옛날: 몰별 `buyma_new_product_register.py --source <mall>`.
- 지금: `reconcile_runner --mode auto --scope new --source <mall> --limit 100000 --execute --confirm-live`.
  - ★ `--limit` 크게 필수: reconcile_runner 기본 limit=3이라 안 주면 3건만 등록됨.

### 6-3. 네이버 통합 (별도 standalone 불필요)
- 옛 `run_phase6_naver_sequential.py`(Phase6 mall별 순차)가 하던 "캡챠 회피 직렬"을 엔진의 site_resource 락이 대체.
- `--only-naver`는 `run_mode='UNIFIED_NAVER'`(별도 배치) → `--no-naver`(run_mode='UNIFIED')와 **동시 실행해도 배치 충돌 없음**.
- 네이버 stock도 공용 1스크립트 `stock_price_synchronizer_naver_merge.py --source <mall>`.

### 6-4. 멀티소스 카테고리 채우기 보존
- brickmansion/loromoda/maisonparco는 `--categories`, milaneez는 `--map-categories` 단계를 수집 직후 1회 더(CATEGORY_FILL). 옛 멀티소스와 동일.

### 6-5. 이미지 단계 분기
- okmall만 이미지 수집(`image_collector_parallel`)+업로드(`r2_image_uploader`). 멀티소스/네이버는 collector가 이미 이미지URL 확보 → **업로드만**.

---

## 7. 옛 명령 → 새 명령 매핑

| 옛 명령 | 새 명령 |
|---|---|
| `okmall/run_daily.py --source=okmall` | `run_daily_unified.py --only okmall` |
| `run_daily_multisource.py` | `run_daily_unified.py --no-naver` (okmall 포함) 또는 `--only <mall>` |
| `run_daily_naver.py` + `run_phase6_naver_sequential.py` | `run_daily_unified.py --only-naver` |
| (전부) | `run_daily_unified.py` (31몰 한 바퀴) |

기타 플래그: `--track new|stock|all`(기본 all), `--plan`, `--dry-run`, `--max-workers`.

---

## 8. 검증 상태 & 남은 잔재

### 검증됨
- 등록~업데이트 전체 사이클(mall 1개) 실제 BUYMA 반영 확인.
- resume·병렬·네이버 직렬 안전장치 통과.
- 3축 전수검증(코드동치 + SQL불변식 + 결정시뮬)으로 "돌렸다가 대량삭제" 우려 없음.
- 품절↔부활 재고 API POC end-to-end.

### 남은 잔재 (운영 무영향)
- `_merge` stock에 `control:delete` dead code 잔존.
- 옛 비-merge synchronizer들 control:delete(롤백용, unified 미사용).
- 수천~수만 건 품절 시 reconcile retire 레이트리밋 점검 미완.

### cutover 후 남은 작업 순서
1. **게시일수 기록**(설계확정·미착수) — `reports/listing_days_tracking_design_20260609.md`
2. **ace_products 한글 정리**(name 26,390건 재번역 대기)
3. **단일권위 마이그레이션**(is_published 등 권위 ace_products→buyma_listings, 고위험·맨 마지막) — DEV §10-B
- 별건: 브랜드 오염 근본수정(죽은 URL 리다이렉트) — 다른 개발자 핸드오프.
