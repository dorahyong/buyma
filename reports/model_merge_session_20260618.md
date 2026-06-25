# model-merge / reconcile — 세션 핸드오프 (2026-06-18)

## 0. 목표 (한 줄)
여러 몰에 흩어진 **같은 상품을 BUYMA에 1개로**, **제일 싼 몰**에서 사게, **팔 수 있는 옵션(색·사이즈)은 다 모아서** 올리고 계속 그 상태로 유지. 옛 `dedup_corrector`(싼몰 죽이기)를 대체.

---

## 1. 핵심 아키텍처

### 2층 구조 (최종 목표)
- **입력층 (그대로 유지)**: `raw_scraped_data` → `ace_products`(몰별 상품) + `ace_product_variants`(옵션·재고) + `ace_product_images`. 수집기/컨버터가 채움.
- **출품층 (새 테이블, reconcile 가 채움)**: `buyma_listings`(출품=그룹 1개) / `source_offerings`(멤버=어느 몰들이 먹이는지) / `source_offering_options`(멤버별 옵션·재고) / `listing_options`(실제 BUYMA 보낸 합집합 옵션) / `listing_images`.
- 출품층은 입력층 **위에 얹힌 층**. 대체 아님.

### reconcile = 한 그룹의 이상적 상태 계산 → BUYMA push. 4모드(게시멤버 is_published=1 수로 분류)
- 0개 → **CREATE** (신규 등록)
- 1개 → **EDIT** (가격·옵션합침·재고·소싱 수정)
- 2+개 → **COLLAPSE** (같은 상품 여러 출품 → 1개로, loser 삭제)
- 출품불가(마진X/전체품절) + 이미 live → **RETIRE** (삭제). 신규면 등록 안 함.
  - RETIRE=삭제는 **기존 stock 과 동일** (마진X/전체품절→control=delete).

### register / stock 분담 (옛 분담 그대로, 속만 새 엔진)
- **register** = 미등록 그룹만 (`--scope new`) → CREATE
- **stock** = 등록된 그룹 (`--scope published`) → EDIT/RETIRE + 새 멤버 흡수
- 새 상품이 이미 바이마에 있는 그룹과 중복 → register는 스킵("stock 담당"), **stock 이 ensure_group 으로 흡수**(winner 재선정/옵션 추가).
- 중복검증은 따로 도는 단계가 아니라 **register/stock 안에 내장**. 그룹락(GET_LOCK, 서버전역)으로 PC 몇 대든 같은 그룹 충돌 안 함.

### ensure_group (배치→내재 전환의 핵심)
- 한 model_no 주면 **그 그룹만 즉석 빌드**: 그룹핑(증분 편입) + offerings upsert + 옵션 + winner/옵션union(resolve) + 이미지.
- 그룹핑은 **전역 재계산 아님** → 기존 listing 에 편입(배치 클러스터 보존). 전역 fuzzy 재계산은 스코프 좁히면 발산(검증으로 확인).
- 기존 배치 4스크립트(dedup_corrector_merge / offering_options_loader_merge / resolve_merge / image_union_loader_merge) 로직을 "한 그룹만" 버전으로 재사용.

---

## 2. 파일 (okmall/ 기준)

### 새로 만든 것
- **reconcile_buyma_push.py** — 빌더(검증된 buyma_new_product_register 함수 재사용) + 실행기.
  - `group_lock(conn, group_key)` = GET_LOCK 서버전역 락(multi-PC 안전).
  - `execute_create_safe` / `execute_edit_safe` = 락+락후재확인+push.
  - `execute_retire` = 마진X/품절 → control=delete (기존 stock 동일).
  - `build_create_request` / `build_edit_request`, 옵션 표기통합(_canonical_map)+그리드채움, master_id/details 는 소싱 ace 에서.
- **reconcile_ensure_group.py** — `ensure_group(conn, model_no, brand_id, dry_run)`. compute_group_members(증분 편입, 기존 listing 찾아 seed 합류) + upsert + load_options + resolve_listing + pick_donor 재사용. `--listing-ids` 로 배치 대비 검증 가능.
- **reconcile_runner.py** — 진입점.
  - `--mode auto` (권장, 내재형): 상품 선정 → 그룹락 → ensure_group → 분류 → push.
  - `--scope new|published|all`: register/stock 분담.
  - `--no-push`: (폐기 권장) merge/winner 만 기록, BUYMA push 생략. ※소싱교정 단독용으로 만들었으나, stock standalone 깨져서 안 씀. 본 2차는 방식 A(아래) 사용.
  - `--mode create|edit`: 레거시(배치 테이블 기반). auto 로 대체됨.
  - `process_one_group()` = 락+ensure_group+scope게이트+분류+push. `select_groups_to_process()` = ace_products 기준 선정(한글 model_no 제외 등 기존 register 필터 복제).
- **reconcile_collapse.py** — COLLAPSE(진짜중복 정리). keeper=buyma_product_stats 누적가치 최고(판매>매출>찜>장바구니>조회, 동점이면 오래된 id), loser=delete. A(순수표기차·안전)/B(변종·보류)/과병합 분류.
- **reconcile_engine.py** — 분류/영향 리포트(읽기전용).
- **stock_price_synchronizer_merge.py** (okmall stock 사본) — 2차 템플릿. process_single_product=refresh만(BUYMA push 제거), 404/품절/흠집→`_mark_all_out_of_stock`(BUYMA 직접삭제 대신 okmall 재고0 표시), run() 끝에 `_reconcile_published(products)`=이번 refresh 그룹만 reconcile push.
- **../run_daily_multisource_merge.py** (오케스트레이터 사본) — 1차 cutover. Phase2 dedup_corrector 제거 / Phase4 register→`reconcile_runner --mode auto --scope new`. Phase3 Stock 은 아직 옛 방식(2차 대상).

### 수정한 것
- **okmall_reference/server.py** (웹훅, 이전 세션) — 5개 이벤트 분기에 `buyma_listings` UPDATE 추가(ace_products 미러). 배포됨.

### 안 건드린 것 (라이브러리로 생존)
- **buyma_new_product_register.py** — reconcile 이 빌더 함수 import. 오케스트레이터 직접 호출만 빠짐.

---

## 3. 운영 검증 실적 (실제 BUYMA, 전부 그룹락)
- **CREATE 374** (신규 등록) — 이전 세션 + 본 세션
- **EDIT 30** (기존 출품에 옵션합침 수정, status=public 확인) — **이게 "기존 상품 옵션union push"의 검증**
- **COLLAPSE 24** (순수표기차 진짜중복, keeper 유지 + loser 삭제 201)
- **auto(create/edit/retire) 검증** — MONCLER 등, 분류·push·retire 정상
- **소싱교정 merge-only(--no-push) 10** (winner 기록, BUYMA 미변경) ※방식 폐기
- 실패 0.

---

## 4. 명령어

```bash
# register (미등록 그룹 신규등록, 중복검증 내장)
python okmall/reconcile_runner.py --mode auto --scope new --execute --confirm-live

# stock (등록 그룹 수정/삭제 + 옵션흡수) — 단독 실행
python okmall/reconcile_runner.py --mode auto --scope published --execute --confirm-live

# 미리보기(쓰기 없음)
python okmall/reconcile_runner.py --mode auto --scope new
# 샤딩(여러 PC 분담; 안전은 락이, 효율은 샤딩이)
... --shard 0/3   # PC1 등
# 특정 1건
... --model-no "8C00029 89AUO 034"

# COLLAPSE 분석/실행
python okmall/reconcile_collapse.py --list
python okmall/reconcile_collapse.py --execute --confirm-live   # A(안전) 그룹만

# ensure_group 그룹핑 검증(배치 대비)
python okmall/reconcile_ensure_group.py --listing-ids 1,5,9305

# 2차 stock 템플릿 (okmall) — 실제 = okmall 스크랩 + 운영 push
python okmall/stock_price_synchronizer_merge.py --limit 5    # 소량 검증부터
```

---

## 5. 이번 세션 주요 결정 / 교정 (중요)
1. **구조는 per-그룹 내재**가 맞다(전역 배치 아님) → ensure_group. (배치 재실행 스코프축소는 fuzzy 발산으로 실패 → 증분 편입으로 해결.)
2. **과병합 발견**: 옛 그룹핑 fuzzy contains 가 과함(STONE ISLAND `V0020`(5자 색상코드)로 63개 오병합 등). winner 있는 12,188 중 진짜 과병합 23건, **운영 푸시된 건 0(라이브 안전)**. COLLAPSE 88 중 진짜중복은 표기차 69(A 24 처리/B 45 보류), 19는 과병합. → 과병합 수정은 **별개 과제**(ensure_group 은 일단 기존 그룹핑 보존).
3. **RETIRE/필터는 기존 stock/register 그대로 복제**(마진X/품절→삭제, 한글 model_no 스킵 등). 새 정책 안 만듦.
4. **2차 방식 A 채택**: stock 8개의 중복 push 로직 → reconcile 1개로 통합. stock은 refresh만, reconcile 이 push. `--no-push`(소싱만) 안은 standalone 깨져서 폐기.
5. **register/stock 분담 기준**: "상품이 바이마에 있나" → "그 상품의 **그룹이** 바이마에 있나". 미등록그룹=register, 등록그룹=stock.

---

## 6. cutover 상태
- **1차** (사본 run_daily_multisource_merge.py, dry-run 검증, 원본 미변경): dedup 제거 + register→reconcile auto --scope new. ✅ 구조완성
- **2차** (방식 A): ✅ **완료(2026-06-18)**. stock 8개 전부 *_merge.py (okmall + 멀티소스 7) 생성·검증. 오케스트레이터 Phase3 STOCK_SCRIPTS 8개 전부 *_merge.py 로 교체완료.
  - okmall(stock_price_synchronizer_merge.py): --limit5 end-to-end 검증(이전).
  - kasina(stock_price_synchronizer_kasina_merge.py): --limit5 실검증 → refresh 5 + reconcile push 5 EDIT(201)/0실패/0스킵. ✅
  - 나머지 6(nextzennpack/labellusso/9tems/brickmansion/loromoda/milaneez/maisonparco): `_make_stock_merge.py` 변환기로 일괄생성(각 hunk 정확히 1회 치환 검증) + compile + dry-run(import경로·stdout·reconcile배선) 통과. 실 push 는 동일변환·동일엔진이라 kasina 로 대표검증.
  - ★멀티소스 8몰은 okmall/ 외부폴더 → hunk1 에 `sys.path.insert(0, .../'okmall')` 추가(okmall 템플릿과 유일한 차이). 변환기에 반영됨.
  - run_daily_multisource_merge.py 헤더(2차 변경점)·Phase3 docstring 갱신, py_compile + phase3 dry-run 으로 8개 _merge 호출 확인.

---

## 7. 남은 작업 (전부 "적용/갈아타기", 엔진은 완성)

1. **기존 50k에 옵션합침+소싱 일괄 적용** (일회성): `reconcile_runner --mode auto --scope published --execute --confirm-live` 를 배치로. EDIT 경로라 검증됨. → 가치 최대.
2. **신규등록 갈아타기**: 사본 run_daily_multisource_merge.py → 원본 교체/cron. (전 며칠 `--scope new` 수동 검증 추천)
3. ~~**2차 stock 완성**~~ ✅ **완료(2026-06-18)**: 8개 *_merge.py 복제·검증 + 오케스트레이터 Phase3 교체. (위 §6 참조)
4. **단일권위 마이그레이션** (맨 끝): 5만개 BUYMA 식별자(buyma_product_id/is_published/locked_*/reference_number)를 ace_products → buyma_listings 로 이전, ace 는 순수 소싱, 웹훅·통계·관리페이지를 buyma_listings 로 전환.

**deferred**: 무거운 옵션-union(이미 EDIT 로 됨, 추가 최적화) / COLLAPSE B 45(변종 수동검토) / 과병합 23 그룹분해 / 한글 옵션·이름 번역.

---

## 8. 2차 stock 템플릿 — ✅ 8개 전부 복제·교체 완료(2026-06-18)
**완료**: okmall + 멀티소스 7개 모두 *_merge.py 생성, Phase3 교체. 복제는 `_make_stock_merge.py`(repo root) 변환기가 5개 hunk 를 각 1회 치환(검증 포함)으로 적용. 멀티소스는 hunk1 에 sys.path okmall 추가가 유일한 차이. 아래는 그 5변경 패턴(참조용).

**okmall: refresh 5건 + reconcile push 4 EDIT(201)/0실패/1스킵, stdout정상, scope정확 — 검증됨.**
**kasina(멀티소스 대표): refresh 5 + reconcile push 5 EDIT(201)/0실패/0스킵 — 검증됨.**

각 `*/stock_price_synchronizer_*.py` 에 동일 적용 (5가지):
1. 헬퍼 추가: `_mark_all_out_of_stock(ace_product_id)`(변이 전부 out_of_stock), `_reconcile_published(products)`(★이번 synced 그룹만 — model_no IN(...) 로 brand_id 조회+canonical dedup. okmall 전체 아님).
2. 404/삭제/흠집 분기: BUYMA 직접삭제 → `_mark_all_out_of_stock` + sync time (BUYMA는 reconcile).
3. step7 refresh: `if not is_delete:` 가드 제거 → 항상 refresh.
4. step8 push 제거(refresh-only) + run() 끝(세션정리 후)에 `if not dry_run: self._reconcile_published(products)`.
5. ★**stdout 이중 wrap 버그 필수 수정**: 파일 상단의 `if sys.platform=='win32': sys.stdout=io.TextIOWrapper(...)` 블록을 **제거**하고, 대신 `import reconcile_runner` 를 상단에 둔다(bnpr 가 win32 stdout utf-8 wrap 을 한 번만 처리). 이걸 안 하면 reconcile import 시 stdout 이중 wrap → "I/O operation on closed file" 크래시 (refresh는 되고 push 직전 죽음).
※ 각 파일 몰별 수집 로직(collect_from_*)·라인번호 다름. push 구조는 동일.

복제 대상 7개: kasina, nextzennpack, labellusso, 9tems, brickmansion, loromoda, milaneez, maisonparco 의 stock_price_synchronizer_*.py.
복제 후: 오케스트레이터 run_daily_multisource_merge.py Phase3 의 STOCK_SCRIPTS 를 *_merge.py 사본 호출로 교체.

---

## 9. 알려진 이슈 / 주의
- `--no-push`(소싱교정 단독) = 폐기(standalone 깨짐). 2차는 방식 A.
- ensure_group 은 과병합을 **그대로 재현**(기존 그룹핑 보존). 과병합 23건 분해는 별개 과제.
- 이미지 IP 차단(`商品イメージを入力してください`)은 사전판별 불가 → 반응적 스킵(과거 28건).
- 2차 stock 템플릿 non-dry-run 은 **okmall 실스크랩 + 운영 BUYMA push** 발생 → 소량(--limit 5)부터.
- DB: pymysql, host 54.180.248.182, db buyma, utf8mb4. Windows 는 PYTHONIOENCODING=utf-8.
- 웹훅: gunicorn systemd `buyma-webhook.service`, /home/ubuntu/buyma/buyma/webhook/server.py.

---

## 10. 메모리
`~/.claude/.../memory/model_merge_reconcile.md` 에 요약 있음. 이 문서가 상세 버전.
