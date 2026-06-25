# 한 몰의 한 사이클 전체 프로세스 (수집 → register → stock)

작성 2026-06-23. 코드 기준 재검증. 통합 자동화(`run_daily_unified.py` + `pipeline_engine.py`) 기준.
관련 문서: `reports/unified_pipeline_engine_design_20260618.md`, 메모리 `model_merge_reconcile.md`.

---

## 0. 큰 그림

한 몰은 **두 갈래(트랙)가 동시에** 돈다:
- **NEW** = 아직 바이마에 없는 상품 → 새로 등록 (collector~register)
- **STOCK** = 이미 바이마에 있는 상품 → 재고/가격 갱신

두 트랙은 같은 몰에서 **동시에** 진행된다. (naver만 캡챠 때문에 사이트 접속(collector·stock)을 1개씩 직렬; 나머지 몰은 전부 병렬.)

**핵심 개념 = "그룹/병합(reconcile)"**: NEW의 등록도 STOCK의 수정도, 둘 다 동일한 reconcile 로직을 쓴다 —
"같은 품번을 여러 몰에서 묶어 → 마진 나는 **제일 싼 몰** + 살 수 있는 **옵션 다 모아서(합집합)** → 바이마에 1개로".
이게 옛 `dedup_corrector`(싼몰 죽이기)를 대체한 **중복 고도화의 본체**.

---

## A. NEW 갈래 (신규 + 미등록 → 등록)

stage 순서: `COLLECT → CONVERT → PRICE → TRANSLATE → IMAGE → REGISTER`

### 1) COLLECT — 몰 사이트에서 데이터 수집
- 명령(예 9tems): `9tems_collector.py --skip-existing` (일부 몰은 카테고리 채우기 1회 더: `--categories`/`--map-categories`)
- **수집 기준(--skip-existing)**: 이미 바이마에 등록된(is_published) 상품은 **건너뛰고**, 그 외 전부 수집
  = 처음 보는 신규 + 수집은 됐지만 아직 미등록인 것. (로그의 "등록 완료 스킵 N개")
- 수집 중 **품절 상품은 목록에서 제외**.
- 출력: `raw_scraped_data` 적재.

### 2) CONVERT — 원본을 표준 형식으로 변환
- 명령: 멀티소스 `raw_to_converter_kasina.py --source-site {mall} --skip-translation` / okmall `raw_to_ace_converter.py --source okmall --skip-translation`
- 입력 `raw_scraped_data` → 출력 `ace_products` + `ace_product_variants`(옵션·재고) + `ace_product_images`.
- **변환 기준**: 아직 변환 안 된 신규 + (미등록인데 raw 가 갱신된 것). (`--include-unpublished` 시 미등록 전부)
- **제외 필터**: 상품명에 **'하자'** 포함 시 변환 안 함(불량품 제외). 한글 품번 등은 이후 단계에서 제외됨.

### 3) PRICE — 바이마 경쟁자 최저가 수집
- 명령: `buyma_lowest_price_collector.py --source {mall} --new-only`
- **기준(--new-only)**: 아직 바이마에 없는 상품(buyma_product_id 없음 또는 is_published=0) = 신규+사고복구 대상만.
- 바이마에서 같은 품번 검색 → 경쟁자 최저가를 `buyma_lowest_price` 에 저장 + 마진 계산.

### 4) TRANSLATE — 일본어 번역
- 명령: `convert_to_japanese_gemini.py --source {mall} --price-checked-only`
- **기준**: 최저가까지 확인된 상품만.
- 상품명·옵션을 Gemini 로 일본어 번역(바이마=일본 사이트라 필수).

### 5) IMAGE — 이미지 준비
- 멀티소스: 수집 때 이미지 URL 확보됨 → **업로드만** (`r2_image_uploader.py --source {mall}`, Cloudflare R2).
- okmall: 이미지 **수집(`image_collector_parallel.py`) 후 업로드** (2단계).

### 6) REGISTER — 바이마 신규등록 (reconcile, 핵심)
- 명령: `reconcile_runner.py --mode auto --scope new --source {mall} --execute --confirm-live`
- **상품 1개가 아니라 "그룹" 단위**로 처리:
  1. **그룹 묶기**: 같은 품번을 여러 몰에서 모아 한 그룹으로 (출품층 `buyma_listings`/`source_offerings`/`source_offering_options`/`listing_options`/`listing_images` 생성·갱신).
  2. **winner 선정**: 그룹에서 **마진 나는 매입가 최저 몰**을 소싱처로.
  3. **옵션 합집합**: 몰마다 파는 색·사이즈를 다 모아 출품 옵션으로(옵션별 최저 매입 몰을 소싱 포인터로).
  4. **가격 결정**: 경쟁자 있으면 `경쟁자 − 1~9엔`(현재가가 그 범위면 유지), 없으면 **30% 목표가**.
  5. **판정(게시멤버 수 n_pub)**:
     - 그룹이 아직 바이마에 없으면(n_pub=0) → **CREATE(신규등록)**
     - 이미 있으면 → 건너뜀("STOCK 담당")
     - 마진X / 전체 품절 → 등록 안 함
     - 한글 품번·이름·옵션, 이미지 없음, 카테고리 없음 → 제외
  6. **그룹락(GET_LOCK, 서버전역)** 으로 잠그고 등록 → 여러 PC·동시 실행에도 중복등록 차단.

---

## B. STOCK 갈래 (이미 등록된 상품 → 갱신)

stage: `STOCK_REFRESH` (한 스크립트 `stock_price_synchronizer_{mall}_merge.py` 가 refresh + reconcile 내부 수행)

### 1) 몰 재방문 (refresh)
- 바이마에 올라간(is_published) 그 몰 상품들을 다시 긁어 현재 가격·재고 확인.
- 경쟁자 최저가 재확인 → 가격 재계산(경쟁자 있으면 −1~9엔, 없으면 30%).
  - 경쟁자 검색이 **404 등 실패** 시 → 기존 가격 유지(망치지 않음).
- **품절 / 404(상품삭제) / 흠집** 이면: 바이마 직접 삭제 안 하고 **그 몰 재고를 0(out_of_stock)으로 표시**만.
  (다른 몰에 있으면 살릴 수 있으니, 최종 판단은 reconcile 에 위임)
- ⚠️ refresh 자체는 BUYMA 를 직접 안 건드림 — **수집·DB(ace) 갱신만**.

### 2) 끝에 reconcile (`--scope published`)
- 이번에 refresh 한 그룹들만 대상으로 BUYMA 반영:
  - 정상 → **EDIT(수정)**: 옵션 합집합 + 제일 싼 몰 + 새 가격으로 바이마 상품 수정.
  - 마진 사라짐 / 전체 품절 → **RETIRE(삭제)**.
  - 같은 그룹이 2개 이상 중복 출품(n_pub≥2) → 자동 안 하고 **건너뜀(수동검토, 안전)**.
- 역시 **그룹락**으로 multi-PC 안전.

---

## C. 한눈에 (한 몰의 한 사이클)

```
NEW:   수집(미등록만) → 변환(하자제외) → 최저가 → 번역 → 이미지 → 등록
                                                              └ 그룹묶기·싼몰·옵션합집합·CREATE
STOCK: 재수집(등록된것) → [품절/404/흠집 = 재고0 표시] → reconcile → 수정(EDIT) / 삭제(RETIRE)
       ↑ NEW·STOCK 동시 진행 (naver만 사이트접속 1개씩, 나머지 전부 병렬)
```

- NEW 등록도, STOCK 수정도 결국 **같은 reconcile**(그룹·winner·옵션합집합)을 쓴다. 차이는 scope(new=미등록만 CREATE / published=등록된것 EDIT·RETIRE) 뿐.
- 가격 규칙은 전 경로 공통: 경쟁자 −1~9엔 / 무경쟁 30%.
- 안전장치: 그룹락(중복처리 차단), 마진X·품절·한글·이미지없음 제외, COLLAPSE(과중복) 자동처리 안 함.

---

## D. 2층 데이터 구조 (참고)

- **입력층(수집)**: `raw_scraped_data` → `ace_products` + `ace_product_variants` + `ace_product_images`. (몰별 상품)
- **출품층(reconcile 가 채움)**: `buyma_listings`(출품=그룹 1개) / `source_offerings`(어느 몰들이 먹이는지) / `source_offering_options`(멤버별 옵션·재고) / `listing_options`(BUYMA 보낸 합집합 옵션) / `listing_images`.
- reconcile = 입력층을 읽어 출품층을 만들고 → BUYMA 로 CREATE/EDIT/RETIRE.
- 진행 추적: `pipeline_batches`(하루 1배치) + `pipeline_control`(몰×트랙×단계 상태, 이어돌기·이력).
