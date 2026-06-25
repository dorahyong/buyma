# reconcile 엔진 설계 & 진행 핸드오프 (2026-06-17)

> 이 문서만 읽으면 "지금까지 뭘 정했고 다음에 뭘 할지" 바로 이어갈 수 있게 작성.
> 선행 문서: `reports/model_merge_design_handoff.md`(원 설계), `reports/model_merge_progress_20260616.md`(백필 1~5단계 완료 상태).
> 관련: `reports/listing_days_tracking_design_20260609.md`(게시일수 추적 설계).

---

## 0. 한 줄 상태

백필 1~5단계(기존 데이터를 merge 테이블로 1회성 정리) 완료. 이제 **"앞으로 매일 돌 reconcile 엔진"**을 새 파일로 설계·구축하는 단계. 멤버 규칙·status 처리·BUYMA API 구조까지 확정. **다음 = 엔진 골격 + 모드 A(create) dry-run.**

---

## 1. 큰 그림 / 목표 (5만은 이미 채움)

- BUYMA에 ~5만 = **옛 dedup(망가진 로직)**으로 올라간 것. 옛 방식은 중복상품에서 "okmall 우선 1개만 살리고 나머지 kill" → 비싼 몰을 메인으로 잡아 마진 손해 + 팔 수 있던 상품 유실.
- merge = 같은 품번 여러 몰을 **출품 1건으로 묶고**, 마진 나는 옵션만 합쳐, **매입가 최저 몰을 winner(소싱처)**로.
- 진짜 목표 = **글로벌 배치 dedup을 버리고, "건드려지는 그룹만 그때그때 처리하는 이벤트형 엔진"으로 전환** + 다PC 확장 안전.

## 2. 확정된 핵심 결정 (바꾸지 말 것)

1. **스키마 A** (merge 테이블 5개, `ace_products`는 안 건드림). 백필 완료.
2. **운영 파일 절대 직접 수정 X.** 전부 새 파일 또는 `_xx` 사본. 테스트 끝나기 전엔 사본으로만.
3. **reconcile = 엔진 하나.** 신규등록·소싱교정·중복합치기·재고동기화가 전부 "그룹 상태 계산 → BUYMA와 diff → 반영"이라는 같은 동작.
4. **처리 단위 = 그룹(group_key = 정규화 품번).** 상품이 건드려지면 그 상품의 그룹 1개를 reconcile.
5. **동시성**: reconcile은 `hash(group_key)`로 샤딩 → 같은 그룹은 항상 한 PC만. + `GET_LOCK(group_key)` 안전벨트. → 다PC 충돌 구조적 0. (수집은 사이트/브랜드로 별도 샤딩)
6. **소스 오브 트루스**: `ace_products`+`raw` = 수집 착지점(안 건드림). 엔진이 읽어 merge 테이블 + BUYMA 관리. ace의 is_active/status 덮어쓰기는 **8단계 운영전환에서만**.

## 3. 멤버 모으기 규칙 — 확정(잠금)

옛 `status='duple'` 도장은 망가진 로직의 결과물 → **그룹 권위로 안 씀.** 그룹은 **브랜드 + 정규화 품번(canonicalize)으로 라이브 재계산.**

- **is_active=1** → 전부 멤버 후보 (이미 "BUYMA 대상" 의미)
- **is_active=0** → **`status='duple'`만** 멤버 후보로 포함 (옛 dedup이 단순히 끈 것 → 재평가)
- **그 외 is_active=0 전부 제외**: deleted/fail/suspended/not_approved/ip_violation/low_view/success(미상) 등
- `is_active`는 멤버 자격 판단에 쓰지 않음 (그냥 "BUYMA에 뜰 수 있는 대상이냐" 상태)
- deleted/fail은 영구제외 아님(재고·원인 풀리면 복귀)이나, **is_active=0이면 일단 멤버에서 제외**하고 나중에 "어떤 경로로 0이 됐는지" 재검증.

### status 의미 (사용자 확인)
- **is_active=0 = BUYMA 자동화/등록/업데이트 대상에서 제외.** 실제 등록 여부는 **`is_published`**.
- **duple**: 같은 모델명 중 is_active=1은 1개여야 해서 옛 dedup이 나머지를 끈 것.
- **deleted / fail**: BUYMA API로 삭제됨 / API 실패. 재고·최저가 확보(deleted)나 원인수정·일시오류 해결(fail) 시 재등록 대상.
- **ip_violation / suspended / not_approved**: 정책상 진짜 제외.
- **low_view(exception_reason)**: 저조회수로 일부러 내림(7,481건). 디테일 추후 별도 처리(보류).

### is_active=0 분포 (2026-06-17)
duple 27,636 / deleted+low_view 7,452 / success(NULL,미상) 2,446 / fail 623 / suspended 165 / fail+low_view 29 / not_approved 27 / deleted 19 / pending 12 / None 2 / ip_violation 2 / success(manual) 1. **총 38,414.**

## 4. 백필 현황 & 검수 (2026-06-16~17)

- buyma_listings 16,745 (winner 있음=출품가능 12,188 / winner NULL=마진없음 4,557)
- winner 분포: okmall 5,331 / non-okmall 6,857 ← merge 핵심 가치
- **검수(inspect_merge.py) 결과** — winner/마진/소싱 정합성 전부 OK. 막아야 할 데이터 이슈:
  - 빈옵션 484 (마진O 멤버 옵션이 전부 품절 → 올릴 옵션 0개. 버그 아님, register에서 제외)
  - 카테고리없음 91, 한글이름 239
  - 옵션표기중복 4건뿐 (무시 가능 — 과대평가였음)
- **4가지 다 없는 "깨끗한 출품가능" = 11,383건**
- 11,383 분해: **신규(옛로직이 유실) 3,653 / 기존(이미 BUYMA) 7,730**. 기존 중 소싱교정(더 싼 몰로) 2,373, 합쳐야 할 그룹 283(그중 26개가 비정상 大그룹 897중 ~640 차지 → **fuzzy 오매칭 의심, 합치기 전 눈검수 필수**).

## 5. BUYMA API 구조 (엔진 push 레이어 기반)

단일 엔드포인트 **POST `{API_BASE_URL}api/v1/products`**, 헤더 `X-Buyma-Personal-Shopper-Api-Access-Token`. `control`+`id` 유무로 동작:

| 동작 | control | id | reference_number | 비고 |
|---|---|---|---|---|
| CREATE | publish | 없음 | 신규 ref | 전체 필드 |
| EDIT | publish | buyma_product_id | locked_reference_number | 전체 필드(변경분 포함) |
| DELETE | delete | — | reference_number | 그것만 |

**결정적 사실:**
1. CREATE는 buyma_product_id 즉시 안 줌. 성공 시 ace `status='pending'`+locked_* 백업. **실제 id·is_published=1은 웹훅(`okmall_reference/server.py`)이 reference_number로 매칭해 기록.** → 엔진은 `buyma_listings`에 **자체 reference_number 1개** 필요 + 웹훅이 buyma_listings도 회수하게 손봐야 함.
2. EDIT엔 `id`+reference_number 둘 다 필수. `is_buyma_locked=1`이면 **locked_name/brand_id/category_id/reference_number 그대로 재전송**(불변 필드 보호). 엔진 edit는 locked_* 재사용 필수.
3. **전체품절이면 자동 `control:delete`** (`build_buyma_request`의 all_out_of_stock). ← false-delete 원천. 엔진에선 분모를 **"그룹 전체(모든 소싱처) 동시 품절일 때만"**으로 한정.
4. 요청 JSON 빌드 로직(고정 공지문·colorsize footer·style_numbers·truncate·options 필터)은 register/stock sync가 거의 동일 → **공유 빌더 모듈로 재사용**(재발명 X).

## 6. ★ DELETE 대신 "재고 품절 edit" — 연속성 핵심 결정 (2026-06-17 추가)

- **하나의 모델명 = BUYMA에서 같은 상품.** 모델명 AABBCC가 okmall로 올랐다 삭제돼도 다른 몰에서 다시 오르면 **같은 상품으로 처리**해야 함. = merge 그룹 개념 그 자체. buyma_listing(그룹)이 **영속 정체성**.
- 문제: delete 후 재등록하면 buyma_product_id/reference_number가 끊김 → 모델명별 게시이력(실제 게시일수) 추적이 깨짐.
- **BUYMA 답변(2026-06-16)**: delete 안 하고 **모든 재고를 품절로 edit**하면 사용자에겐 삭제처럼 보이지만 상품은 남고 id/ref **연속 유지**. 재고 복귀 시 같은 buyma_product_id로 복구.
  - 품절 처리는 상품수정 API 말고 **재고(variants) API 권장**: `https://specification.personal-shopper-api.buyma.com/api/products/variants_json/`
  - 수천~수만 건 OK, 단 **레이트 리밋** 주의: `https://specification.personal-shopper-api.buyma.com/api/rate_limit/`
  - **Q3 확정(2026-06-17)**: 품절 edit한 상품에 재고 복귀 시 **같은 buyma_product_id로 호출하면 오류 없이 「출품 중」으로 복구.** → "절대 delete 안 함, 품절=재고 edit, 복귀=edit" 방침 완전 검증됨.
- **엔진 방침**: 원칙적으로 **DELETE 안 함**. "그룹 전체 품절"이면 **재고 품절 edit**(가능하면 variants API)으로 처리 → id/ref 영속 → 게시일수 추적 자연 해결 + false-delete 원천 차단.
- 주의: 옛 운영 stock sync는 지금도 매일 품절품을 **delete** 중 → 전환 전까지 연속성 단절 누적. (전환 우선순위 고려사항)

## 7. 엔진 파일 구성 (전부 신규)

```
okmall/
  reconcile_buyma_push.py   ✅생성 create 요청 빌드(register 빌더 import 재사용) + 옵션 표기통합(collapse)
  reconcile_runner.py       ✅생성 진입점 (--dry-run / --limit / --listing-id / --shard i/N / --summary)
  reconcile_engine.py       (예정) reconcile(group_key), 모드 분기(create/edit/stockout), 락/샤딩
  reconcile_queue.py        (예정) 바뀐 group_key 수집 (시작: ace_products.updated_at 폴링 = 비침투)
```
- 계산 로직은 검증된 `resolve_merge.py` 재사용. 빌드 로직은 `buyma_new_product_register` import 재사용(수정 X).
- **옵션 표기통합(collapse)**: FREE/Free, Dark Navy/Dark navy, 30/30インチ 같은 표기변종을 **출구(빌더)에서 대표 1개로 통합** + 중복 variant 제거. 원본 데이터는 안 건드림 → 미래 데이터도 자동 처리. (`_canonical_map`, 최빈 표기 채택). 영향 230 listing(1%).

## 8. 만드는 순서 (안전 게이트)

| # | 단계 | 게이트 | 상태 |
|---|---|---|---|
| 1 | 기존 register/stock의 BUYMA API 파악 | 읽기만 | ✅ 완료 |
| 2 | 엔진 골격 + 모드 A(create) **dry-run** + 옵션 표기통합 | 영향 건수 보고 | ✅ 완료 (2026-06-17) |
| 3 | 신규 **실제 create** | 눈 확인 → 확대 | ✅ **374건 live**(2026-06-17, 성공률 ~93%). 실패는 이미지 IP(28건). 빌더 버그 전부 해결. 나머지는 step6(C)로. |
| 4 | 모드 B(edit) + **재고품절 edit**(variants API) dry-run → 소량 | diff 검수 / Q3 확정 선행 | |
| 5 | 모드 C(collapse) — 26 大그룹 눈검수 먼저 | 수동 승인 | |
| 6 | 큐+샤딩 운영전환, 옛 dedup 교체, 파괴적 cleaner 가드 | 최종 | |

## 8-1. create 빌더 수정 이력 & 실패 모드 (2026-06-17, 실등록으로 검증)

`reconcile_buyma_push.build_create_request` 가 성공 payload(ace_product_api_logs)와 일치하도록 수정:
1. **옵션 position = 타입별 1부터** (색/사이즈 각각). 통합 카운터 쓰면 색이 pos=7 등으로 떠 `色は表示位置番号に歯抜け` 거부.
2. **옵션 표기 통합**(`_canonical_map`): FREE/Free, Dark Navy/Dark navy 등 정규화 대표 1개 + 중복 variant 제거.
3. **variant 완전 그리드**: (색×사이즈) 빠진 조합을 out_of_stock 로 채움. 멀티몰 union 으로 조합 비면 `販売可否/在庫に必要なすべての選択がありません` 거부.
4. ref 발급(UUID)·status 기록(`execute_create`): 성공 시 pending+locked_*, 실패 시 api_error.

`reconcile_runner.select_clean_new_listing_ids` 사전검증(등록 전 제외):
- 이름 한글 / 옵션값(색·사이즈) 한글 / 카테고리 없음 / 옵션 0 / 이미지 0 / 미등록(buyma_product_id 없는 멤버만).

**실패 모드 (검증됨):**
- **이미지 IP 차단** = `商品イメージを入力してください`(画像入力済でも) = BUYMA 권리침해 필터. **사전 예측 불가 → 반응적**(웹훅 fail 기록 후 제외). 배치10 중 3건(#3,53,54).
- **옵션값/이름 한글** = `不正な文字「X」` → 사전필터로 제외. 등록하려면 번역 필요(옵션값 1,853 / 이름 239).
- 이종 사이즈 혼합(US/JP/inch) = 등록되나 품질 이슈, 추후 옵션 정규화.

**사전검증 분류(winner 12,188 기준)**: 알려진 규칙 통과 9,416(77%) / 고쳐야함 2,772(옵션값한글1,853·옵션0=484·이름한글239·이미지0=189·카테고리91).

**실등록 성공분(2026-06-17 배치10)**: buyma_listings #6(133557809)·#41(133557812)·#51(133557811)·#62(133557815)·#66(133557814)·#16(133558227)·#17(133558230). #3 만 직접 데이터 입력 예정(이미지 IP).

## 8-2. reconcile 엔진 — 분류/EDIT 정정 (2026-06-17 오후, 실험으로 확정)

**파일:** `okmall/reconcile_engine.py`(분류·영향 리포트), `reconcile_buyma_push.py`(execute_edit/build_edit_request 추가).

1. **분류 기준 = `is_published=1`** (NOT `buyma_product_id IS NOT NULL`). 삭제된 멤버도 buyma_product_id 는 남아있어 잘못 "게시중"으로 셌었음. 정정 후(출품가능 12,188 기준):
   - **CREATE 6,807** (삭제된 것=재등록 대상 포함) / **EDIT 5,293**(소싱교정 1,499·winner그대로 3,794) / **COLLAPSE 88** (진짜 라이브 중복만 — 287→88로 급감, 위험 大그룹 대폭 감소).
   - runner create 선택·published_member 모두 `is_published=1` 로 정정.
2. **buying_shop_name 은 BUYMA 불변** (`買付先ショップ名は変更できません`). → **"소싱 교정"은 BUYMA edit 으로 못/안 함.** 소싱은 내부 개념(주문 시 최저가 몰 매입)이라 **merge 테이블 winner_offering_id 에 이미 기록 → cutover 시 실현**. **소싱교정 1,499건 = BUYMA push 불필요.** build_edit_request 는 buying_shop 안 보냄(불변 보존).
3. **BUYMA EDIT 은 "보이는 값(가격·재고/옵션)" 변동에만 필요** = stock sync 영역. 그때도 이름/브랜드/카테고리/buying_shop 전부 보존(locked).
4. **웹훅 결과는 DB로 직접 확인 가능** — `ace_product_api_logs.api_response_json` 에 fail 사유까지 저장됨(ace ref 기준 edit/기존건). → 서버 로그 grep 불필요. (merge CREATE 신규건은 ace_product_id 없어 미적재 → 그건 buyma_listings.status + 필요시 last_error 컬럼.)

**EDIT 실패 사유(검증)**: buying_shop 변경불가 / 옵션값 한글(화) → edit 후보에도 한글필터 적용 필요.

## 8-3. 2026-06-17 세션 결과 & C(운영전환) 결정

**오늘 실제 등록: 374건 live / 28 fail(이미지 IP).** create 파이프라인 + 웹훅 회수 + 분류 전부 검증됨.

**남은 등록가능(클린 신규) = 4,414건** 구성(검증):
- 진짜 신규 2,792 (멀티몰 2,205 = 옛 dedup이 비싼 메인 잡아 마진X로 안 올렸던 것 → merge가 싼몰 winner로 살림 = 핵심가치 / 단일몰 587)
- 예전 등록이력 있다 내려감 1,622 (deleted 1,354=품절 등, fail 113, duple 104, success-unpub 81). **저조회수(low_view) 삭제분은 멤버규칙에서 제외돼 여기 없음.**
- ※ 추가로 한글옵션/이름 건은 번역해야 등록가능, 이미지IP는 반응적 스킵.

**결정: C — 4,414를 지금 일괄 등록하지 않고, 운영 파이프라인(cutover)에 붙여 매일 통제하며 점진 등록.**
- runner 에 레이트리밋 간격(`time.sleep(0.4)`) 추가함.
- runner 선택조건에 "이미 등록/시도/실패 제외"(buyma_product_id IS NULL, status NOT IN pending/fail/success) 추가 → 배치가 새 것으로 전진(중복 재등록 방지). ※같은 ref 재전송은 BUYMA가 idempotent 처리해 중복 안 생김도 확인.

## 8-4. step 8 cutover 계획 (운영 전환 — 다음 작업, 운영파일이라 신중)
1. 일일 진입점(`run_daily_multisource`/`run_daily_naver`/`okmall/run_daily`)에 **merge create 단계**를 사본으로 추가(일정량/일, 페이싱). 옛 경로 남긴 채 병행.
2. 옛 `dedup_corrector`(글로벌 kill) → merge GROUP(죽이지 않고 그룹화)로 **교체** (Phase2). 검증 후 전환.
3. EDIT/COLLAPSE/stock 모드 완성(소싱교정은 내부, stock=가격/재고 edit, collapse 88 검수).
4. 파괴적 cleaner(`cleanup_duplicates` 등)에 "merge 그룹 건드리지 마" 가드.
5. 웹훅은 이미 buyma_listings 회수하도록 보강·배포됨.

## 9. 절대 안전 원칙
- 기존 잘 되던 기능 깨지면 안 됨. 모든 단계 dry-run → 건수 보고 → 승인 → 실행.
- 스키마는 가산적(컬럼 추가)만. 단계마다 멈춰 확인.
- 매칭 키에 일본어 안 씀(`*_original`+source_option_code). locked 필드(이름/브랜드/카테고리) 절대 변경 X. `control='suspend'` 금지.

## 10. 열린 항목 (재개 시 확인)
1. ~~Q3 답변~~ ✅ 확정: 같은 id로 호출 시 오류 없이 "출품중" 복구 (2026-06-17).
2. variants(재고) API를 stock에 쓸지(권장) vs 전체 edit 유지.
3. low_view 비활성 상세 처리 (보류).
4. success/inactive 2,446건 비활성 사유 재검증.
5. 26개 비정상 大그룹 fuzzy 오매칭 검수 (collapse 전).
6. ✅ 웹훅 server.py 보강 **완료·배포·기동**(2026-06-17): 4개 이벤트 분기에 buyma_listings UPDATE 5개 추가(additive, ref disjoint 라 무해). 백업 `okmall_reference/server.py.bak_20260617`. 운영서버 배포+재시작 완료(좀비 gunicorn 정리 후 active running). api_logs 는 ace 전용 유지. ※서비스가 `disabled`(부팅 자동시작 아님) — 필요시 `systemctl enable`.
7. 옵션 색상값 한글(예 `화이트`, `다크 네이비`) — 표기통합으로 안 합쳐짐. 번역 or 소수 제외 (보류).
8. 사이즈 표기 혼재(예 `30` vs `31インチ` 한 출품 내) — 중복 아님, 미세 cosmetic. 추후 통일 규칙 검토.
9. 3단계 전 설계: buyma_listings.reference_number 실제 발급·저장 방식(현재 dry-run은 `MG{id}` 임시). 웹훅 회수와 연결.
