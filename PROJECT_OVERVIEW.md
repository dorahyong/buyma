# BUYMA 자동화 프로젝트 — 전체 개요 & 코드 동작 명세

> 생성일 2026-05-29. 프로젝트 내 전체 Python 스크립트(55개)를 읽고 정리한 단일 레퍼런스 문서.
> 시스템 개요(파이프라인/DB/외부서비스)와 파일별 동작 명세(역할·핵심로직·CLI·DB·의존관계)를 함께 담는다.

---

## 1. 시스템 개요

한국 편집숍/스토어 상품을 수집 → BUYMA 등록 형식으로 변환 → 일본어 번역 → 이미지 처리 → BUYMA 등록 → 재고/가격 동기화하는 멀티소스 자동화 시스템.

### 1.1 수집처 (source_site)

| 분류 | 수집처 | 수집 방식 | 자체이미지 | 변환기 |
|---|---|---|---|---|
| 핵심 | **okmall** | HTML 스크래핑 | X (W컨셉 검색) | `okmall/raw_to_ace_converter.py` |
| Shopby API | **kasina** | Shopby REST API | X | 공용 `kasina/raw_to_converter_kasina.py` |
| Cafe24 | **nextzennpack** | HTML 스크래핑 | O | 공용 converter |
| Cafe24 | **labellusso** | HTML 스크래핑 | O | 공용 converter |
| Cafe24 | **laprima** | HTML 스크래핑 (신규) | O | 공용 converter |
| 네이버 | smartstore 다수 + brand.naver.com (brandstore) — 총 21+ mall | Playwright | O | 공용 converter |

- 우선순위(중복 제거 시): okmall > nextzennpack > labellusso > trendmecca > kasina > … (`dedup_corrector.py`의 `SOURCE_PRIORITY`)
- **trendmecca**는 네이버 스마트스토어로 전환됨(독립 collector 제거, naver category collector가 흡수).
- **loromoda/**는 설계 메모·HTML 샘플만 존재, collector 미구현.

### 1.2 6단계 파이프라인 (okmall 기준 `orchestrator.py`)

```
COLLECT → CONVERT(번역 제외) → PRICE → TRANSLATE(최저가확보분만) → IMAGE(최저가확보분만) → REGISTER
```

각 수집처별 일일 자동화 진입점:

| 진입점 | 대상 | Phase 구성 |
|---|---|---|
| `okmall/run_daily.py` | okmall | orchestrator(6단계) → stock sync |
| `run_daily_multisource.py` | kasina/nextzennpack/labellusso | Collect병렬 → Convert+Dedup → Price·Translate·Image·Stock → Register |
| `run_daily_naver.py` | 네이버 21 mall | Collect(직렬) → Convert+Dedup → Price·Image → Translate → Register → Stock |
| `fast_price_updater.py` | 등록 전체 | 수집처 접근 없이 BUYMA 최저가만 빠르게 갱신 |

### 1.3 핵심 데이터 흐름

```
raw_scraped_data  ──(converter)──▶  ace_products (+ options/variants/images)
   (수집 원본)                          (BUYMA 등록용)
                                            │
                          ┌─────────────────┼──────────────────┐
                          ▼                 ▼                  ▼
                  buyma_lowest_price   convert_to_japanese   r2_image_uploader
                  _collector (PRICE)   _gemini (TRANSLATE)   (IMAGE)
                          │                 │                  │
                          └─────────────────┼──────────────────┘
                                            ▼
                              buyma_new_product_register (REGISTER)
                                            │  ▲
                              (BUYMA PS API)│  │(webhook: okmall_reference/server.py)
                                            ▼  │
                                  stock_price_synchronizer_* (재고/가격 동기화)
```

### 1.4 주요 DB 테이블 (MySQL `buyma`)

| 테이블 | 용도 |
|---|---|
| `raw_scraped_data` | 수집 원본 (source_site별). model_id 없으면 수집 단계에서 스킵(dead row 방지). `is_active` 컬럼은 미사용 dead column이라 2026-06-02 DROP |
| `ace_products` | 변환된 BUYMA 등록용 데이터. 같은 model_no 색상별 별도 행 허용 |
| `ace_product_options` / `ace_product_variants` | 옵션(색상/사이즈)·variant(재고단위). variant는 `color_value_original`/`size_value_original`(한글 원본, **stock sync 매칭 키 — 절대 번역 갱신 금지**) 별도 보유 |
| `ace_product_images` | 이미지 URL (source/cloudflare). `is_uploaded` 플래그 |
| `ace_product_api_logs` | BUYMA API 요청/응답 로그 |
| `mall_brands` | 수집처 브랜드 매핑 (`mall_brand_no`, `buyma_brand_id`, `is_active`: NULL검수대기/1활성/0차단 — §1.6) |
| `mall_categories` | 수집처 경로 → buyma category_id 매핑 (`is_active`: NULL검수대기/1활성/0차단 — §1.6) |
| `mall_sites` | 수집처 설정 (`has_own_images`, site_url 등) |
| `buyma_master_categories_data` | BUYMA 카테고리 마스터 + 예상배송비 |
| `buyma_product_stats` | 셀러 통계(access/cart/favorite/sold_count 등) |
| `pipeline_batches` / `pipeline_control` | orchestrator 배치·단계 상태 |
| `product_filter_tabs` | manage_server 필터 탭 정의 |

### 1.5 외부 서비스 & 환경

- **BUYMA Personal-Shopper API** (`personal-shopper-api.buyma.com`): 등록/수정/삭제. 차단 안 됨.
- **BUYMA 웹사이트** (`www.buyma.com`): 최저가 검색·출품목록 크롤링. 공유오피스 IP 차단 → **Cloudflare WARP 우회 필요**.
- **Gemini API** (`gemini-2.0-flash`): 일본어 번역, 카테고리 매칭, 측정키 매칭.
- **Cloudflare R2** (S3 호환): 이미지 스토리지.
- 공통: `../.env`에서 DB/API키/R2 인증 로드. Windows에서 `PYTHONIOENCODING=utf-8` 필수.
- 마진 공식 공통 상수: 환율 9.2, 판매수수료 5.5%, 기본 배송비 15000엔, VAT 환급 = 매입가KRW/11, 목표 마진율 20%.

### 1.6 매핑 검수 정책 & convert 게이트 (`is_active`, 2026-06-02 통일)

`mall_brands` / `mall_categories` 의 `is_active` 가 등록 경로마다 다르게 들어가 **검수 안 된 매핑이 즉시 ace 변환에 사용**되던 문제를 정책으로 통일. 상세: `reports/mall_brands_categories_is_active_policy_20260602.md`.

| 값 | 의미 | 누가 결정 |
|---|---|---|
| NULL | 자동등록 후 검수 대기 | 시스템 |
| 1 | 사용 가능 (검수 완료) | 사람 |
| 0 | 사용 불가 (명시적 차단) | 사람 |

- **시스템은 NULL 로만 등록**한다. 0/1 은 사람만 확정. 자동매핑 도구(Gemini, kasina `match_brands.py`)도 `buyma_*_id` 만 채우고 `is_active` 는 NULL 유지.
- **convert 게이트**(2개 converter): raw 의 brand 가 `mall_brands.is_active=1` 캐시에 없으면 ace INSERT skip. category 가 `mall_categories.is_active=1` 캐시에 없으면 신규 path 는 `is_active=NULL` 로 검수 큐에 자동 등록 후 skip. → 미검수 매핑이 ace 로 흘러갈 수 없음.
- **검수자 큐**: `SELECT * FROM mall_brands WHERE is_active IS NULL;` / 동일 mall_categories. 1=사용시작, 0=계속 skip.
- 한 번 검수 완료한 매핑을 나중에 0 으로 바꿔도 이미 들어간 ace 는 자동 삭제 안 됨 → `buyma_cleaners/buyma_inactive_mapping_cleaner.py` 로 정리(§5).

---

## 2. okmall/ 파이프라인

BUYMA 자동화의 핵심 디렉토리. 수집(COLLECT) → 변환(CONVERT) → 최저가(PRICE) → 번역(TRANSLATE) → 이미지(IMAGE) → 등록(REGISTER)의 6단계 파이프라인과 보조 도구로 구성된다.

### orchestrator.py

**역할**: 6단계 신규 상품 등록 파이프라인을 브랜드별로 병렬 실행하는 오케스트레이터. 날짜 기반 배치 관리와 단계별 재실행(이어하기)을 지원.

**핵심 로직**:
- `ALL_STAGES = [COLLECT, CONVERT, PRICE, TRANSLATE, IMAGE, REGISTER]`. `--until`로 특정 단계까지만 실행.
- `get_or_create_batch()`: 오늘 날짜(`YYYYMMDD`)의 `RUNNING` 배치 이어하기, 어제 이전 `RUNNING`은 `FAILED`로 마감, 없으면 새 `batch_id`(`YYYYMMDD_HHMMSS`) 생성.
- `get_target_brands()`: `mall_brands`(`is_active=1`)에서 대상 조회. `--source`/`--brand`/`--exclude` 필터.
- `ThreadPoolExecutor`로 브랜드별 병렬 실행, 같은 단계는 `Semaphore(1)`로 동시 1개 제한, DB 접근은 `db_lock` 직렬화.
- `process_brand_pipeline()`: `pipeline_control` 상태 확인 → `DONE` 스킵, `PARTIAL` 모드면 CONVERT/TRANSLATE/IMAGE 스킵.
- `execute_worker()`: 단계별 워커를 `subprocess.Popen`(실시간 로깅)으로 실행.
  - COLLECT: mall별 분기(`nextzennpack_collector.py` / `kasina_collector.py` / 기본 `okmall_all_brands_collector.py`, 모두 `--skip-existing`)
  - CONVERT: `raw_to_ace_converter.py --skip-translation`
  - PRICE: `buyma_lowest_price_collector.py --new-only`
  - TRANSLATE: `convert_to_japanese_gemini.py --price-checked-only`
  - IMAGE: `image_collector_parallel.py --price-checked-only` → `r2_image_uploader.py`
  - REGISTER: `buyma_new_product_register.py`
  - COLLECT/CONVERT는 `mall_brand_name_en`, 그 외는 `buyma_brand_name`을 `--brand`로 사용.

**CLI**: `--mode {FULL,PARTIAL}`, `--source`, `--brand`, `--exclude`, `--until`
**DB**: `pipeline_batches`(R/W), `pipeline_control`(R/W), `mall_brands`(R)
**의존**: 6개 워커 스크립트를 subprocess 호출. 로그 `logs/{batch_id}.log`.

### run_daily.py

**역할**: 일일 자동화 진입점. `orchestrator.py`(신규 등록)와 `stock_price_synchronizer.py`(동기화)를 순차 실행하는 얇은 래퍼.
**핵심**: `[1/2]` orchestrator(`--sync-only`면 스킵) → `[2/2]` synchronizer(`--register-only`면 스킵). 각각 `subprocess.run`, 실패해도 다음 단계 진행.
**CLI**: `--source`, `--brand`, `--sync-only`, `--register-only` (+ 미지정 인자는 orchestrator에 패스스루)

### okmall_all_brands_collector.py

**역할**: okmall.com의 활성 브랜드 상품을 HTML/ld+json 스크래핑하여 `raw_scraped_data`에 저장. 봇 감지 회피 세션 관리 포함.
**핵심**:
- `OkmallSessionManager`: 5종 브라우저 프로필 랜덤, 30요청마다 세션 교체+메인페이지 재방문, 403/연속 타임아웃 5회 시 전체 중단.
- 브랜드 목록 페이지 페이징(최대 100p)으로 `.item_box[data-productno]` 수집(흠집특가 `item_scratch` 제외).
- 추출: ld+json(Product/BreadcrumbList), 브랜드, 상품명(괄호에서 model_id 추출, `_is_valid_model_id`로 색상/한글/짧은 후보 배제), 가격, 카테고리경로, 옵션(실제 품절만 out_of_stock), 실측, 혼용률.
- **model_id 없으면 저장 스킵**.
- `--skip-existing`: `is_published=1`인 mall_product_id만 제외(신규+미등록 수집). 10건 배치 저장(`ON DUPLICATE KEY UPDATE`).

**CLI**: `--brand`, `--limit`, `--dry-run`, `--skip-existing`
**DB**: `raw_scraped_data`(W, source='okmall'), `mall_brands`(R), `ace_products`(R)

### raw_to_ace_converter.py

**역할**: `raw_scraped_data` → `ace_products`/options/variants로 변환. okmall 전용(멀티소스는 kasina 공용 converter 사용, 구조 동일).
**핵심**:
- 매핑 로드(캐시): `mall_brands`, `mall_categories`+`buyma_master_categories_data`(배송비), `shipping_config`, `colors.csv`, `size_details.csv`.
- `fetch_raw_data()`: `LIKE '%하자%'` 제외. 미변환 신규 + 미등록인데 가격 갱신된 상품 조회. `--upsert`면 전체 재변환.
- 가격: 엔화 정가 = KRW×0.1(100엔 반올림), 엔화 매입가 = KRW/9.2, 판매가=0(PRICE 단계 결정).
- `brand_id=0`(미등록)이면 수집처 `brand_name_en` 사용. 미매핑 카테고리는 `mall_categories`에 `buyma_category_id=NULL, is_active=NULL`로 자동 INSERT.
- **convert 게이트(§1.6)**: `get_brand_info`/`get_category_info` 캐시(`is_active=1`만) miss → None 반환 → `convert_single_raw_to_ace` 진입부에서 skip, `run_conversion`이 `skipped += 1`. 미검수(NULL)·차단(0) 매핑은 ace 변환 안 됨.
- `convert_measurements_to_details()`: 한/영→일 키 변환, 카테고리별 허용 키 필터, 너비→둘레 ×2.
- 색상/사이즈 한국어 원본을 `_original`에 유지, 일본어는 NULL(TRANSLATE 단계). 옵션 없는 사이즈는 out_of_stock variant 추가.
- INSERT/UPDATE 발생 + `--skip-translation` 아니면 `run_batch_translation()` 호출.

**CLI**: `--dry-run`, `--limit`, `--brand`, `--raw-id`, `--upsert`, `--skip-translation`, `--source`
**DB**: R `raw_scraped_data`/`mall_brands`/`mall_categories`/`buyma_master_categories_data`/`shipping_config`, W `ace_products`/options/variants/`mall_categories`
**의존**: `from convert_to_japanese_gemini import run_batch_translation`. `colors.csv`, `size_details.csv`.

### convert_to_japanese_gemini.py

**역할**: `ace_products`/옵션/variants 한국어 텍스트를 일본어로 배치 번역. 하드코딩 매핑 우선, 잔여만 Gemini 호출.
**핵심**:
- `KOREAN_TO_JAPANESE` 사전(섹션제목/안내문/부위명/소재/색상/BUYMA 금지어)을 길이순 치환. 영문 금지어("harrington"→"スイングトップ" 등)도 정규식 치환.
- `collect_translation_targets()`: `is_active=1` AND `colorsize_comments_jp` 비어있는 상품. `--price-checked-only`/`--with-images`/`--source`/`--brand` 필터.
- `extract_unique_texts()`: 하드코딩 후 잔여 한국어만 md5 dedup.
- `translate_all_texts()`: 30개씩 배치, `ThreadPoolExecutor`(최대 5), `gemini-2.0-flash` JSON, 429 지수 대기.
- **`color_value_original`/`size_value_original`은 절대 갱신 금지**(stock sync 매칭 키).

**CLI**: `--brand`, `--limit`, `--dry-run`, `--price-checked-only`, `--with-images`, `--source`, `--max-texts`
**DB**: R/W `ace_products`/options/variants

### buyma_lowest_price_collector.py

**역할**: `model_no`로 BUYMA 검색해 경쟁자 최저가 수집, 판매가·최저가여부·마진율 계산·저장.
**핵심**:
- 검색 `https://www.buyma.com/r/-O3/{model_no}/`(가격 오름차순). 중고·내 상품(`BUYMA_BUYER_ID`) 제외 후 첫 경쟁자 가격.
- `fetch_products_to_check()`: `model_no` 있고 `is_active=1`. `--new-only`면 미노출만. 오래 체크 안 된 것 우선.
- 가격: 경쟁자 있으면 최저가−랜덤(1~9엔), 없으면 매입가 기반 마진 20% 역산.
- `calculate_margin_rate()`: 판매가×9.2 → 수수료 5.5% 차감 → −(매입가+배송비)+부가세환급(÷11).
- `update_lowest_price()`: `buyma_lowest_price`/`price`/`margin_rate`/`buyma_lowest_price_checked_at` 갱신.

**CLI**: `--limit`, `--brand`, `--source`, `--dry-run`, `--id`, `--new-only`, `--workers`
**DB**: `ace_products`(R/W), `buyma_master_categories_data`(R)

### image_collector_parallel.py

**역할**: W컨셉(wconcept.co.kr)에서 model_no 검색으로 이미지 수집 → `ace_product_images`. Playwright 멀티프로세싱.
**핵심**:
- `fetch_target_products()`: 이미지 없는/`not found` + model_no 있음 + `mall_sites.has_own_images=0`만.
- 워커별 독립 Chromium(봇 회피 init script), 10개마다 중간 저장(DELETE 후 INSERT).
- 검색결과 영역에서만 상품 ID 추출(추천 영역 제외), 최대 2 후보. `MIN_IMAGE_COUNT(5)` 기준 best 선택, 최대 20장. 실패 시 `'not found'` 저장.

**CLI**: `--brand`, `--model-no`, `--limit`, `--dry-run`, `--headless`, `--workers`(4), `--price-checked-only`, `--source`
**DB**: R `ace_products`/`ace_product_images`/`mall_sites`, W `ace_product_images`

### r2_image_uploader.py

**역할**: `source_image_url` 원본을 Cloudflare R2에 업로드, `cloudflare_image_url` 갱신.
**핵심**:
- `fetch_pending_images()`: 미업로드분, `--retry-failed`면 에러분.
- 다운로드(W컨셉 Referer, 2회 재시도) → webp/PNG RGBA는 JPEG 흰배경 합성 → `{ace_id}_{pos:03d}_{md5}.{ext}` 파일명 → `upload/` prefix로 R2 PUT.
- 성공 시 `is_uploaded=1`, 실패 시 `upload_error` 기록. `ThreadPoolExecutor`(10).

**CLI**: `--limit`, `--dry-run`, `--retry-failed`, `--ace-product-id`, `--brand`, `--source`, `--workers`(10)
**DB**: `ace_product_images`(R/W), `ace_products`(R) / boto3 + PIL

### buyma_new_product_register.py

**역할**: 미등록 신규 상품을 BUYMA PS API로 등록. 중복/model_no 없는 등록상품 정리 모드 포함.
**핵심**:
- `get_products_to_register()`: `is_active=1 AND is_published=0 AND model_no AND category_id>0 AND cloudflare_image_url IS NOT NULL`. 이미 등록된 model_no 제외(`--allow-duplicate-model`로 허용).
- `process_product()`: model_no 한글 스킵 → 마진 재계산 손해 스킵 → `build_request_json()` → API.
- `build_request_json()`: 전체 품절이면 스킵(None). `generate_model_no_variants()`로 `style_numbers` 배열(brand_id=0이면 제외+`brand_name` 추가). 고정 일본어 공지, `colorsize_comments` 1000자 한도 푸터 누적. 길이 제한(상품명 60반각, 옵션 26반각, 구매처 30반각). options는 variants에 있는 값만+카테고리 허용 size_details만. variants 재고있으면 `purchase_for_order`(order_quantity 90~100 랜덤), 없으면 `out_of_stock`. `reference_price`는 price보다 클 때만.
- `update_product_after_request()`: 성공 시 `status='pending'`+`available_until`(+90일)+불변필드 백업(`locked_*`), 실패 시 `status='api_error'`. **실제 등록 확정은 Webhook**.
- 정리 모드: `--clean-duplicates`, `--clean-no-model` — API delete 후 DB `status='deleted'`.

**CLI**: `--limit`, `--brand`, `--dry-run`, `--product-id`, `--source`, `--allow-duplicate-model`, `--clean-duplicates`, `--clean-no-model`
**DB**: R `ace_products`/images/options/variants, W `ace_products`/`ace_product_api_logs`
**환경**: `BUYMA_MODE=1` 본환경 / `2` 샌드박스.

### stock_price_synchronizer.py

**역할**: 등록된 okmall 상품(`is_published=1`)의 재고/가격을 OkMall 원본과 동기화, BUYMA PS API로 수정/삭제. (멀티소스 동기화기들의 원형 — `build_buyma_request`/마진/매칭 로직 공유)

### dedup_corrector.py

**역할**: 멀티소스 동일 상품 중복을 model_id 정규화·fuzzy 매칭으로 그룹핑, 우선순위 낮은 source의 `ace_products`를 `status='duple', is_active=0` 처리. okmall 이미지 결손을 타 source에서 복사.
**핵심**:
- `SOURCE_PRIORITY`(okmall 최상위), `IMAGE_PRIORITY`(okmall 제외).
- `canonicalize()`: 괄호/슬래시/시즌 접두 제거 + 공백·특수문자 제거 + 대문자.
- `build_duplicate_groups()`: ① canonical exact 그룹(2+ source), ② 브랜드 내 single→exact contains, ③ single끼리 contains.
- `process_groups()`: 그룹별 우선순위 최상위=primary. okmall primary + 이미지 0건이면 donor 이미지 복사. non-primary를 duple 마킹(published 건 별도 카운트).

**CLI**: `--dry-run`
**DB**: R `raw_scraped_data`/`ace_products`/`ace_product_images`, W `ace_products`/`ace_product_images`

### buyma_master_data_csv_to_md_translator_20260226.py

**역할**: `buyma_master_data/`의 11개 마스터 CSV를 일→한 번역해 `_ko` 컬럼 추가 마크다운(`md/`) 생성. **1회성(실행 완료)**.
**핵심**: 일본어 셀 수집 → `translation_cache.json` 미번역분만 Gemini 200개씩 배치(4초 간격, 중단 재개) → `{col}_ko` 컬럼 삽입 마크다운 생성.
**DB**: 없음 (CSV→MD 전용)

---

## 3. kasina / nextzennpack / labellusso

세 수집처는 동일 파이프라인 구조(수집 → 공용 변환 → 재고/가격 동기화). 수집기는 별도지만 **변환기는 `kasina/raw_to_converter_kasina.py`를 4개 수집처(kasina/nextzennpack/labellusso/naver)가 `--source-site`로 공유**. 동기화기 3개는 mall 수집 부분만 다르고 BUYMA API/마진/매칭 로직은 모두 복제.

### kasina/kasina_collector.py

**역할**: kasina.co.kr Shopby API로 상품 수집 → `raw_scraped_data`(source='kasina').
**핵심**:
- 두 엔드포인트: 리스트/상세 `shop-api-secondary.shopby.co.kr`, 옵션 `shop-api.e-ncp.com`. 고정 헤더(clientId 등) 인증.
- `mall_brands`의 `mall_brand_no`(brandNo)로 `/products/search`(`brandNos`+`excludeCategoryNos`) 페이지네이션(100).
- 상품별 상세 API + 옵션 API 호출.
- 스킵: `productManagementCd`(model_id) 없음 / `STORE_ONLY`(오프라인 전용) / 변환 실패.
- `parse_options`: `flatOptions[].value`를 `색상|사이즈` split, `saleType != 'SOLDOUT'`이면 in_stock.
- `extract_measurements`: `baseInfo.contentFooter` 파싱(사이즈별 실측). 성별은 `customPropertise`로 prefix.

**CLI**: `--brand`, `--limit`, `--dry-run`, `--skip-existing`
**DB**: R `mall_brands`/`raw_scraped_data`/`ace_products`, W `raw_scraped_data`

### kasina/match_brands.py

**역할**: `mall_brands`의 `buyma_brand_id IS NULL`을 `brands.csv`와 매칭. **1회성 매핑 도구**.
**핵심**: 영문명 추출 후 4레벨 매칭(완전일치 → 특수문자제거 일치 → 포함 → 실패). 레벨>0이면 `buyma_brand_id`/`mapping_level`/`is_mapped=1` UPDATE.
**DB**: R/W `mall_brands` (pymysql)

### kasina/raw_to_converter_kasina.py ★공용 변환기

**역할**: `raw_scraped_data` → `ace_products`/options/variants/images. **kasina·nextzennpack·labellusso·naver·laprima 공유**.
**source-site별 분기**: 파일명/로그만 "kasina", 실제 분기는 전적으로 `--source-site` 값. 모든 매핑 조회가 `mall_name = :source_site` / `r.source_site = :source_site` 사용. 미매핑 카테고리 INSERT도 source_site로. 번역 호출 `run_batch_translation(source=self.source_site)`.
**핵심**:
- `fetch_raw_data` 분기: `--raw-id` 단건 / 기본(미변환 신규 OR 미등록이며 updated_at 비교) / `--include-unpublished`(미변환 OR is_published=0 전부) / `--upsert`(전체 재변환). 공통 `하자` 제외.
- 가격·브랜드·색상 처리는 okmall converter와 동일. 색상/사이즈 한국어 원본 `_original` 저장.
- **convert 게이트(§1.6)**: okmall converter와 동일 — `is_active=1` 캐시 miss 시 brand는 skip, 신규 category는 `is_active=NULL` INSERT 후 skip.
- 실측 → details: `MEASUREMENT_KEY_TO_JAPANESE` + `measurement_key_cache.json` + Gemini 미매핑 키 자동 매칭(캐시, null도 영구 스킵). `size_details.csv` 카테고리별 허용 키 필터, 너비→둘레 ×2. `measurement_report_*.log` 생성.
- 자체 이미지 수집처는 `raw_json_data['images']`를 `ace_product_images`(is_uploaded=0)에 저장.

**CLI**: `--dry-run`, `--limit`, `--brand`, `--raw-id`, `--upsert`, `--include-unpublished`, `--skip-translation`, `--source-site`(기본 kasina)
**DB**: R `mall_brands`/`mall_categories`/`buyma_master_categories_data`/`shipping_config`/`raw_scraped_data`, W `ace_products`/options/variants/images
**의존**: `okmall/`을 sys.path 추가 후 `convert_to_japanese_gemini.run_batch_translation` import. `colors.csv`, `size_details.csv`.

### kasina/stock_price_synchronizer_kasina.py

**역할**: 등록된 kasina 상품 재고/가격을 카시나 API로 재수집 → `ace_products`/variants 갱신 + BUYMA PS API 수정/삭제. (**멀티소스 동기화기 표준형**)
**핵심**:
- 대상: `is_published=1 AND buyma_product_id IS NOT NULL AND source_product_url IS NOT NULL AND is_active=1 AND source_site='kasina'`, `buyma_lowest_price_checked_at ASC`. `MAX_WORKERS=2`.
- `collect_from_kasina`: 상세 API(가격) + 옵션 API(재고). `saleStatusType` ONSALE/READY 외면 판매종료, 404면 삭제됨.
- **`detect_stock_changes` (2026-05-21 개편)**: DB·mall 각 1개면 이름 무관 직접 매칭. 다중이면 **1순위 `source_option_code`, 2순위 `(color_value_original, size_value_original)` 한글 원본**, 실패 시 skip. **일본어 fallback 제거**(5,320건 false delete 사고 원인).
- 최저가: BUYMA 검색(`/r/-O3/{model_no}/`)에서 중고+내 상품 제외 경쟁자 최저가(재고감지와 병렬).
- 가격: 경쟁자 있으면 `[최저가-9, 최저가-1]` 내면 유지, 아니면 `최저가-random(1~9)`. 없으면 매입가 기반 20% 역산.
- API 트리거: 재고변동/가격변동/마진≤0(삭제)/전체품절(삭제)/`--force`. `build_buyma_request`는 register와 동일. 일시적 API 오류는 삭제 안 함.

**CLI**: `--id`, `--limit`, `--brand`, `--dry-run`, `--force`
**DB**: R/W `ace_products`/`ace_product_variants`, R images/options/`buyma_master_categories_data`, W `ace_product_api_logs`, (삭제 시) DELETE ace_*/raw

### nextzennpack/nextzennpack_collector.py

**역할**: nextzennpack.com(Cafe24) HTML 스크래핑 → `raw_scraped_data`(source='nextzennpack').
**핵심**:
- `SessionManager`(30요청 교체+메인방문, 랜덤 프로필, Referer 체인, 403/5회 차단).
- 브랜드 리스트(`/product/list.html?cate_no={mall_brand_no}`) → `extract_subcategories`(사이드바 depth1/2) → 서브카테고리별 페이지네이션.
- 상세: `table.detail`(모델명/소재/색상), 옵션 `select#product_option_id1`(구분선/품절/ONESIZE→FREE, 2번째 select면 색상×사이즈 재조합), 실측 `table.size`, **이미지 `#prdDetail` 내 `wisacdn.com/brand/`만 + `ec-data-src` 우선**(poxo /big/ 404 회피), 가격 JS 변수.
- model_id 없으면 스킵.

**CLI**: `--brand`, `--limit`, `--dry-run`, `--skip-existing`
**DB**: R `mall_brands`/raw/ace, W `raw_scraped_data`

### nextzennpack/stock_price_synchronizer_nextzennpack.py

**역할**: 등록된 nextzennpack 상품 재고/가격을 HTML 재스크래핑으로 갱신 + BUYMA 수정/삭제.
**핵심**: kasina 동기화기와 **거의 동일**(마진/최저가/매칭/`build_buyma_request`/트리거/DB 모두 동일), `source_site='nextzennpack'`만 다름. mall 수집은 API 대신 HTML 스크래핑(자체 세션관리, 딜레이 1.0~2.0). `collect_from_nextzennpack`: JS 변수 가격, `table.detail` 색상, `select#product_option_id1` 옵션, 옵션 없으면 '품절' 텍스트로 단일상품 재고 판단.
**CLI/DB**: kasina 동기화기와 동일(source만 다름)

### labellusso/labellusso_collector.py

**역할**: labellusso.com(Cafe24) HTML 스크래핑 → `raw_scraped_data`(source='labellusso').
**핵심**:
- nextzennpack과 동일 SessionManager.
- `extract_subcategories`: `[여성]/[남성]/[공용]` → gender prefix + depth 조합.
- 리스트: `li[id^="anchorBoxId_"]`, 상품명 `strong.name a`(`[브랜드]` 제거), 가격 `data-prod-custom`/`data-prod-price`.
- 상세: 브랜드/모델명/`#desctable`, 옵션/재고는 JS `option_stock_data`(`is_selling/is_display/stock_number`) + `#desctable` 사이즈 매칭. **이미지 `/small/` URL 그대로(870×870, /big/ 404)**. 가격 JS 변수.
- 모델명 없으면 상품명 영숫자 패턴 fallback, 없으면 스킵.

**CLI**: `--brand`, `--limit`, `--dry-run`, `--skip-existing`
**DB**: R `mall_brands`/raw/ace, W `raw_scraped_data`

### labellusso/stock_price_synchronizer_labellusso.py

**역할**: 등록된 labellusso 상품 재고/가격을 HTML 재스크래핑으로 갱신 + BUYMA 수정/삭제.
**핵심**: nextzennpack 동기화기와 **거의 동일**, `source_site='labellusso'`만 다름. `collect_from_labellusso`: `product_price`(정가)/`product_sale_price`(실판매가, ≤0이면 추출 실패)/`#span_product_price_custom`, 색상 `#desctable`, 옵션 `option_stock_data` JSON + 사이즈 정규화(ONE SIZE→FREE) 포함 매칭.
**CLI/DB**: kasina/nextzennpack 동기화기와 동일

> **수집기 3종 공통**: `mall_brands.is_active=1`만 대상, model_id 없으면 스킵, `ON DUPLICATE KEY UPDATE` 저장. nextzennpack/labellusso는 자체 이미지(`has_own_images=1`).

---

## 4. naver / smartstore

네이버 스마트스토어/브랜드스토어 수집처. 단일 공용 수집기(`premiumsneakers_collector.py`)를 중심으로 브랜드형/카테고리형/브랜드스토어형 3종 collector가 컴포넌트 공유. **공통 핵심**: ① 네이버가 `?page=N`을 무시하므로 DOM 페이지 버튼 클릭 + `wait_for_function`(첫 상품 ID 변화) 방식, ② Playwright `headless=False` 봇 회피, ③ **상세 진입 시 `referer`(스토어 홈) 헤더 필수**(미적용 시 로그인+캡챠, 2026-05-12 패치), ④ `naver_cookies.json` 공유 쿠키.

### naver/run_all_collectors.py

**역할**: 6개 스마트스토어(premiumsneakers/fabstyle/loutique/t1global/vvano/veroshopmall)에 `premiumsneakers_collector.py`를 `--source`로 순차 실행하는 배치 러너.
**핵심**: UTF-8 환경 설정 후 6개 순회 `subprocess.run`, 받은 인자(`--skip-existing` 등) 패스스루. 실패해도 다음 진행.

### naver/run_stock_10_new.py

**역할**: 신규 10개 mall(maniaon/bblue/euroline/unico/kometa/larlashoes/thegrande/upset/luxlimit/pano)에 stock sync 순차 실행. lovegrande 제외.
**핵심**: `stock_price_synchronizer_naver.py --source <mall>` 10개 순차, mall별 종료코드/소요시간/실패목록 요약. `naver_cookies.json` 사전 로그인 전제.

### naver/scan_store_brands.py

**역할**: 네이버 스토어 네비게이션 메뉴를 Playwright로 열어 브랜드/카테고리 추출 → `mall_brands`/`mall_categories`(+옵션 `mall_sites`) INSERT하는 시드 스캐너 (2026-04-09).
**핵심**:
- `STORES` dict에 스토어별 네비 구조 플래그 하드코딩(`brands_at_top`/`brand_parent(s)`/`brand_prefix`/`category_roots` 등).
- Phase 1 `scan_navigation`(headless=False, 쿠키): `'보안 확인'` 감지 시 캡챠 중단. `li[data-category-menu-key]`의 `data-shp-contents-dtl` JSON 파싱, `hover_and_diff`로 호버 전후 키 diff 자식 수집.
- Phase 2 INSERT: `insert_mall_brands`(신규만, `mall_brand_no`=category_key, **`is_active=NULL`** 검수대기 §1.6), `insert_mall_categories`(depth 분리+gender 추론, `is_active=NULL`), `insert_mall_site`(`has_own_images=1`).

**CLI**: `--store`(필수), `--dry-run`, `--insert-site`, `--brands-only`, `--categories-only`
**DB**: R/W `mall_brands`/`mall_categories`/`mall_sites`

### naver/stock_price_synchronizer_naver.py

**역할**: 네이버 21개 mall(`NAVER_MALLS`) 재고/가격을 재수집해 BUYMA 반영. 가격/마진/API 로직은 kasina 동기화기와 동일.
**핵심**:
- mall 분류: `SMARTSTORE_MALLS`(/i/v2/) vs `BRANDSTORE_MALLS={carpi,joharistore}`(/n/v2/).
- 대상: `is_published=1 AND ... AND source_site IN (대상)`, 오래된 것 우선. Playwright `headless=False`, **단일 브라우저/페이지 공유 → `MAX_WORKERS=1` 직렬**.
- `fetch_naver_detail`: **`page.goto(url, referer=store_home)`**. products XHR + product-benefits XHR 캡처. 에러: NOT_FOUND/CAPTCHA/LOAD_FAIL/XHR_MISS/TIMEOUT. 캡챠 시 `is_blocked` 전체 중단.
- `collect_from_naver`: 가격은 `salePrice`(정가) + 쿠폰 적용가(`optimalDiscount...totalPayAmount` 1순위 / `discountedSalePrice` fallback). `statusType` SALE/ONSALE/READY 외면 판매종료. 옵션은 `groupName`으로 color/size 분류, `optionCombinations[].stockQuantity>0`로 재고.
- `detect_stock_changes`: kasina와 동일(source_option_code → 한글 원본 매칭, 일본어 fallback 제거).
- `build_buyma_request`/마진/삭제 트리거: kasina 동기화기와 동일.

**CLI**: `--source`(미지정 21개 전체), `--id`, `--limit`, `--brand`, `--dry-run`, `--force`
**DB**: R ace/variants/options/images/`buyma_master_categories_data`, W `ace_products`/variants/`ace_product_api_logs`, (삭제 시) DELETE

### naver/premiumsneakers/brand_store_collector.py

**역할**: `brand.naver.com` 브랜드스토어(carpi/joharistore 등) 전용 수집기 (2026-04-17).
**핵심**: 스마트스토어 collector와 **유일 차이는 XHR 경로 `/n/v2/`(vs /i/v2/)**. `fetch_detail_brand_store`로 상품/쿠폰 JSON 캡처(캡챠 판정). 나머지(리스트/페이지네이션/매핑)는 `premiumsneakers_collector`에서 import 재사용.
**CLI**: `--source`(필수), `--brand`, `--limit`, `--dry-run`, `--skip-existing`, `--login`, `--dump`, `--count`

### naver/premiumsneakers/premiumsneakers_category_collector.py

**역할**: 브랜드 리스트가 없는 스토어(dmont/tuttobene/thefactor2 + 신규 11개)용. 스토어별 '전체상품' URL 1개를 페이지네이션하고 상세에서 브랜드/카테고리 추출 → `mall_brands`/`mall_categories` auto-INSERT (2026-04-17).
**핵심**:
- `STORE_ALL_PRODUCT_URLS` 하드코딩(`size=80&filters=oa` 품절 제외). lovegrande는 model_id 품질 불량으로 제외.
- 도메인이 brand.naver.com이면 `fetch_detail_brand_store`(/n/v2/), 아니면 `fetch_detail`(/i/v2/).
- 상품명 정리: `NAME_CLEANUP_PATTERNS`(스토어별 prefix/태그) + `SEASON_PATTERN`. unico/upset은 modelName 비워 상품명에서 재추출. `STORE_EXCLUDE_KEYWORDS` 제외.
- auto-INSERT: `ensure_mall_brand`/`ensure_mall_category`(둘 다 `is_active=NULL` 검수대기 §1.6, category_cleaner 매칭 대기).

**CLI**: `--source`(필수), `--limit`, `--dry-run`, `--skip-existing`, `--login`, `--dump`, `--count`, `--mall-product-id`
**DB**: R `mall_sites`/`mall_brands`/`mall_categories`/raw/ace, W `mall_brands`/`mall_categories`/`raw_scraped_data`

### naver/premiumsneakers/premiumsneakers_collector.py

**역할**: 네이버 스마트스토어 공용 상품 수집기(브랜드 기반). `--source`로 어느 스토어든 전환. 다른 두 collector가 import하는 컴포넌트 원본.
**핵심**:
- `set_source`: `mall_sites.site_url`에서 `STORE_HOME` 조회.
- `login_and_save_cookies`: 로그인 → `naver_cookies.json`(naver/ 공용).
- Phase 1 `collect_product_list`: 브랜드별 카테고리 URL 순회, pno 추출 + '품절' 사전 스킵 + `총 N개`로 max_pages 추정. DOM 페이지 버튼 클릭.
- Phase 2 `fetch_detail`: **`page.goto(url, referer=STORE_HOME)`**. products GET + benefits POST XHR 캡처. 타이틀 기반 캡챠 감지.
- `map_to_row`: 브랜드는 list 값 우선(authoritative). 모델번호 `modelName`→검증→`extract_model_from_name` fallback, 없으면 None(스킵). 옵션 `groupName` 분류. `save_rows` upsert(10개 배치).

**CLI**: `--source`(기본 premiumsneakers), `--brand`, `--limit`, `--dry-run`, `--skip-existing`, `--login`, `--dump`, `--count`
**DB**: R `mall_sites`/`mall_brands`/raw/ace, W `raw_scraped_data`

### smartstore/image_collector_parallel_smartstore.py

**역할**: 네이버 스마트스토어 `adorelux`에서 model_no 검색 이미지 수집 → `ace_product_images`. 봇 차단 우회를 위해 **별도 디버그 Chrome에 CDP 연결**(2026-03-18). (위 naver collector들과 별개 도구)
**핵심**:
- `--start-chrome`: `chrome.exe --remote-debugging-port=9222 --user-data-dir=<profile>`(수동 로그인).
- `fetch_target_products`: 이미지 없는 + model_no + `has_own_images=0`. `--price-checked-only` 필터.
- `search_smartstore_products`: `/adorelux/search?q={model_no}`, 캡챠 감지 시 3분 수동 대기.
- `get_product_images`: `__PRELOADED_STATE__` → HTML 정규식 → DOM 3단계 fallback, 최대 20장.
- CDP `connect_over_cdp`로 기존 Chrome에 새 탭, 작업 후 탭만 닫음.

**CLI**: `--start-chrome`, `--brand`, `--model-no`, `--limit`, `--dry-run`, `--price-checked-only`
**DB**: R `ace_products`+images+`mall_sites`, W `ace_product_images`

---

## 5. buyma_cleaners/ (정리 스크립트)

BUYMA 출품 데이터 ↔ DB ↔ R2 간 불일치를 탐지·정리하는 **수동 실행** 도구. 대부분 `--dry-run`/`--apply` 안전장치 보유. 크롤링 필요 스크립트는 Playwright 로그인 후 쿠키(`buyma_cookies.json`)를 requests 세션으로 재사용.

### buyma_orphan_cleaner.py (반복 유지보수)

**역할**: BUYMA 출품 리스트 ↔ `ace_products` 대조. (1) BUYMA에만 있는 "고아"를 BUYMA 삭제, (2) DB는 published인데 BUYMA에 없는 "유령"을 DB `is_published=0`.
**흐름**: Phase 1 출품 리스트 크롤링(부분 실패 시 RuntimeError 중단 — 대량 오판정 방지) → 2-A `find_orphans` → 2-B `find_ghosts` → Phase 3 `delete_orphans`(rorapi로 reference_number 조회 → PS API delete) → Phase 4 `clean_ghosts`(100개 배치 UPDATE).
**CLI**: `--login`, `--scan`, `--delete`, `--clean-ghost`, `--dry-run`
**외부**: BUYMA 크롤링 + rorapi + PS API

### buyma_suspended_cleaner.py (반복 유지보수)

**역할**: BUYMA "출품정지중(suspended)"/"비승인(not_approved)" 상품을 크롤링해 `ace_products`에 상태 표시 + `is_active=0`. 쿠키 orphan_cleaner와 공유.
**흐름**: Phase 1 상태별 크롤링 → Phase 2 `match_with_db`(IN 조회) → Phase 3 `apply_updates`(이미 처리된 건 스킵).
**CLI**: `--login`, `--scan`(기본 dry-run), `--apply`, `--type {suspended,not_approved}`
**외부**: 크롤링만(삭제 API 미사용)

### buyma_unpublished_cleaner.py (반복 유지보수)

**역할**: `is_published=0`인데 `buyma_product_id` 남은 비정상 상태를 BUYMA 삭제 + DB 정리.
**흐름**: `fetch_targets`(is_published=0 AND buyma_product_id IS NOT NULL) → PS API delete → DB `is_active=0, is_published=0, buyma_product_id=NULL, status='deleted'` + `ace_product_api_logs` upsert.
**CLI**: `--count`, `--dry-run`, `--brand`, `--limit`
**외부**: PS API

### buyma_inactive_mapping_cleaner.py (반복 유지보수, 2026-06-02 신규)

**역할**: `mall_brands.is_active=0` 또는 `mall_categories.is_active=0` 매핑에 걸린 잔존 `ace_products`를 정리(§1.6 게이트 도입 전 흘러간 데이터). 검수자가 매핑을 0으로 차단한 뒤 한 번 돌리면 BUYMA 잔존분까지 정리됨.
**흐름**: JOIN(`ace_products` × `raw_scraped_data` × `mall_brands` × `mall_categories`)으로 대상 추출 → `buyma_product_id` 있으면 BUYMA delete API 호출(성공/실패 무관 DB DELETE) → FK 순서대로 `ace_product_api_logs` → `ace_products` DELETE. per-item commit, idempotent. `raw_scraped_data`는 미변경(게이트가 재변환 차단).
**CLI**: `--count`, `--dry-run`, `--limit`
**외부**: PS API
**비고**: 도입 시 1회 정리 실적 — 대상 1,402건(BUYMA 라이브 165 + ghost 272 + DB only 965), API 437/437 성공, 잔여 0.

### category_cleaner.py (반복 유지보수)

**역할**: `mall_categories` ↔ `ace_products` 카테고리 매핑 관리. Gemini로 buyma_category_id 자동 추론. 서브커맨드 기반.
**서브커맨드**:
- `register`: 미등록 경로를 `mall_categories`에 INSERT(`buyma_category_id=NULL, is_active=NULL`, gender 자동추출, depth 분해).
- `match`: `buyma_category_id IS NULL AND (is_active=1 OR NULL)` 행을 `categories.md`(599개) 기준 Gemini 100건씩 매칭 → UPDATE(is_active 미변경).
- `apply`: `ace_products.category_id=0`을 raw→mall_categories 조인으로 `category_id=buyma_category_id` UPDATE.
- `import`: 고정 파일 `category_cleaner_import_csv.txt`(탭 구분)로 일괄 UPDATE.

**CLI**: 서브커맨드별 `--mall`, `match`에 `--dry-run`
**is_active**: NULL(신규)/1(활성) 대상, 0(명시적 비활성) 제외
**외부**: Gemini API

### cleanup_duplicates.py (1회성/특수)

**역할**: 중복·고아 데이터 3단계 일괄 정리. published 건은 BUYMA 삭제까지 하는 강한 삭제.
**흐름**: Step 1 ace `model_no` 중복 그룹 전체 삭제 / Step 2 raw `model_id` 중복 + 연결 ace 삭제 / Step 3 raw 없는 고아 ace 삭제. `delete_ace_from_db`는 variants/options/images → ace 순. published+reference_number면 API 삭제.
**CLI**: `--dry-run` (옵션 없으면 즉시 실행, 프롬프트 없음)
**외부**: PS API (`BUYMA_MODE` 운영/샌드박스)

### cleanup_hazard_products.py (반복 유지보수)

**역할**: 상품명 "하자" 포함 `raw_scraped_data`의 매칭 `ace_products` + BUYMA 게시분 정리(raw는 유지 — converter `LIKE '%하자%'` 필터가 재변환 차단).
**흐름**: Phase 1 `scan_targets`(LEFT JOIN 저장) → Phase 2 `delete_from_buyma`(published+reference_number만, 실패분 보류) → Phase 3-4 `delete_from_ace`(200건 배치, 트랜잭션 롤백).
**CLI**: `--scan`, `--delete`(매번 fresh 스캔, `DELETE` 입력 확인), `--dry-run`, `--source-site`
**외부**: PS API

### r2_orphan_cleaner.py (1회성/주문형)

**역할**: CSV URL 목록의 R2 객체(고아 이미지) 일괄 삭제.
**흐름**: CSV 로드 → URL path에서 R2 key 추출 → boto3 `delete_objects` 1000개 배치(0.5초 딜레이).
**CLI**: `--file`(기본 `r2_data.csv`), `--dry-run`
**비고**: `.env`를 현재 디렉터리에서 로드(루트 아님)

### update_mall_brands_manual.py (1회성/수동매핑)

**역할**: 수작업 TSV(`mall_brands_manual_update.tsv`)로 `mall_brands`의 `buyma_brand_id`/`buyma_brand_name`/`is_active` 일괄 UPDATE.
**흐름**: TSV 파싱(`\N`→NULL) → `(mall_name, raw_brand_name)` 키 조회 → not_found/multi_match/no_change 분류 → 값 다르면 UPDATE.
**CLI**: 기본 dry-run, `--apply`

> **권장 정합성 점검 순서**: cleanup_duplicates → buyma_orphan_cleaner → buyma_suspended_cleaner → buyma_unpublished_cleaner → (필요 시) r2_orphan_cleaner

> **발견된 코드-문서 불일치(사실 보고, 미수정)**: ① orphan_cleaner는 `COOKIE_FILE` 상대경로, suspended_cleaner는 절대경로 → 실행 디렉터리 따라 다른 쿠키 참조 가능. ② category_cleaner docstring(`import --txt/--csv`)과 실제 `cmd_import`(고정파일) 불일치.

---

## 6. buyma_stats/ (통계·시장조사)

BUYMA 셀러 페이지/검색 결과를 크롤링해 자사 통계·판매실적·시장경쟁 데이터를 수집, DB와 머지하여 관리화면용 JSON/JS 데이터셋 생성. **쿠키 주의**: market·7days collector는 `../buyma_cleaners/buyma_cookies.json` 공유, stats·sales·available_until updater는 폴더 내 전용 `.buyma_cookies.json`(별개 파일).

### build_merged_dataset.py

**역할**: DB(raw+ace+images)와 셀러 크롤 JSON들을 `model_id`(품번) 단위로 머지 → 화면용 `merged_latest.json`/`.js`(`window.STATS_DATA`). 1행=품번 1개, 여러 소싱처는 `sources[]`.
**흐름**: 입력 JSON 로드(self_stats/self_7days/market) → raw GROUP BY model_id → ace `model_no IN` 청크 매칭 → images → 품번별 대표 raw/ace 선정, `determine_status`/`detect_db_mismatch`, item 조립.
**상태 판정**: on_sale(published+active) → waiting(ready, not published) → no_lowest(lowest=0, not published) → sold_out(전체 oos) → unknown. **`in_seller_listing`은 status 판정에서 제외**(stale row 부풀림 방지).
**CLI**: `--limit`
**DB**: R `raw_scraped_data`/`ace_products`/`ace_product_images` (쓰기 없음)

### buyma_low_view_cleaner.py

**역할**: 등록 N일(기본 30) 경과 + 조회수 0 출품 상품을 PS delete API로 삭제.
**흐름**: `fetch_targets`(is_active=1, is_published=1, exception_reason IS NULL, 등록 N일 초과) JOIN `buyma_product_stats`(access_count=0) → delete API → 성공 시 `is_active=0, exception_reason='low_view_30d'`(status/is_published은 webhook이 처리).
**CLI**: `--count`, `--dry-run`, `--days`(30), `--limit`
**DB**: R ace+stats, W `ace_products`/`ace_product_api_logs`

### buyma_market_collector.py

**역할**: 시장 경쟁 데이터 수집(Phase 1). on_sale 품번을 BUYMA 인기순 검색(`/r/-O1/{model_id}/`)으로 동일품번 제품수·자사순위·1위정보·시장최저가 수집.
**흐름**: `merged_latest.json`에서 on_sale model_id 로드 → 쿠키 세션 → 검색 GET → `parse_search`(중고 제외) → `aggregate_one`(same_count/our_ranks/market_lowest_price/top1). `ThreadPoolExecutor`(5).
**CLI**: `--limit`, `--workers`(5), `--resume`, `--save-every`(2000)
**출력**: `buyma_market_latest.json`/`.js`(`window.MARKET_DATA`)

### buyma_self_7days_collector.py

**역할**: 셀러 상품 "최근 7일 조회수"(`data.access.last_7days`)를 상품별 API로 수집.
**흐름**: `buyma_self_stats_latest.json`에서 pid 목록 → `GET /rorapi/sell/products/{pid}` → last_7days 추출. `ThreadPoolExecutor`(5).
**CLI**: `--limit`, `--workers`(5), `--resume`, `--save-every`
**출력**: `buyma_self_7days_latest.json`/`.js`(`window.SEVEN_DAYS_DATA`)

### buyma_self_sales_collector.py

**역할**: 자사 판매실적(총판매수/금액)을 셀러 거래 페이지에서 수집 → `buyma_product_stats` UPSERT.
**흐름**: `/my/orders/`(진행중) + `/my/buyersales/`(종료) 페이지네이션 → 取引ID dedup → 상품별 (sold_count, sales_amount_jpy) 집계 → executemany UPSERT.
**CLI**: `--max-pages`
**DB**: W `buyma_product_stats`

### buyma_self_stats_collector.py

**역할**: 셀러 전시목록 행에서 장바구니/찜/액세스 카운트 수집 → `buyma_product_stats` UPSERT.
**흐름**: `/my/sell?...&rows=100`(전체 탭, 460+ 페이지) 페이지네이션 → `parse_row`(pid + cart/favorite/access) → `ace_products`에서 buyma_product_id→id 매핑 → UPSERT.
**CLI**: `--max-pages`, `--start-page`
**DB**: R `ace_products`, W `buyma_product_stats`

### buyma_available_until_updater.py (커밋됨, commit 91ec087)

**역할**: 셀러 전시목록의 有効期限(게시기한)을 크롤링해 `ace_products.available_until`에 반영하는 **1회성 정합 스크립트**. stock/register가 BUYMA엔 today+90을 push하지만 DB write-back을 안 해 stale해진 값 보정. (2026-06-09 기준 1회성 정합 실행 완료.)
**흐름**: self_stats_collector와 동일 인프라. `parse_row`로 pid+available_until → `/`→`-` 정규화 후 UPDATE(1000개 executemany).
**CLI**: `--dry-run`, `--max-pages`, `--start-page`
**DB**: W `ace_products.available_until`

---

## 7. manage_server/ (관리 웹서버)

DB 조회 전용 Flask 미니 관리 서버(웹훅 server.py와 역할 분리). 기본 `127.0.0.1:8001`, nginx `/manage` 프록시. 모든 보호 라우트 `@require_login`.

### app.py

**역할**: Flask 앱 본체. DB 테이블 브라우저 + 상품/브랜드/카테고리/탭 관리 라우트 등록, 부팅 시 `products_cache.start()` 워밍업.
**주요 라우트**: `GET /health`(인증 불필요), `GET /manage`(테이블 뷰어), `GET /manage/products/`(products.html), `/manage/products/data.json`(캐시 gzip 송신, 워밍업 전 503), `sources.json`/`images.json`(lazy load), `/manage/brands`·`/manage/categories`(목록/data.json/update/search_buyma), 탭 CRUD(`GET/POST/PUT/DELETE /manage/products/tabs`).
**실행**: `python app.py`(`MANAGE_SERVER_PORT` 기본 8001) 또는 gunicorn.
**import**: products_api/products_cache/auth/brands_api/categories_api/tabs_api

### auth.py

**역할**: 세션 기반 단일 비밀번호 인증.
**핵심**: `configure_auth`(`MANAGE_SECRET_KEY` 필수), 세션 수명 180분(HTTPOnly/SameSite=Lax). `require_login` 데코레이터(미인증 시 `/manage/login` redirect, `?next=` 보존). 라우트 `GET|POST /manage/login`(open-redirect 방지), `GET /manage/logout`.
**env**: `MANAGE_PASSWORD`, `MANAGE_SECRET_KEY`

### brands_api.py

**역할**: `mall_brands` 검수 데이터 액세스 + `brands.csv` 부분일치 검색.
**핵심**: `get_mall_names`/`get_brands`(필터+isoformat)/`apply_updates`(PK=mall_name+raw_brand_name, `EDITABLE_COLUMNS`만, 빈문자열→NULL)/`search_buyma_brands`(csv 캐시).
**DB**: R/W `mall_brands` / 파일 `brands.csv`

### categories_api.py

**역할**: `mall_categories` 검수 + `categories.csv` 검색. brands_api와 동일 구조.
**핵심**: `get_categories`(필터 mall_name/is_active/unmapped_only/search), `apply_updates`(PK=id, EDITABLE=buyma_category_id/is_active), `search_buyma_categories`.
**DB**: R/W `mall_categories` / 파일 `categories.csv`

### products_api.py

**역할**: 출품목록관리 화면용 JSON 페이로드 생성 + sources/images lazy-load. `build_merged_dataset.py`의 머지 로직을 서버용으로 재구현(DB 실시간).
**핵심**: `build_payload`가 4개 배치 쿼리(raw aggregated + ace 풀스캔 + first images + buyma_stats) → 품번별 대표 ace `_determine_status`/`_detect_db_mismatch`. **시장 컬럼은 모두 None**(서버는 시장수집 미수행).
**DB**: R raw/`mall_brands`/ace/images/`buyma_product_stats` (쓰기 없음)

### products_cache.py

**역할**: `build_payload` 결과 메모리 캐시. 백그라운드 daemon이 부팅+`REFRESH_INTERVAL=300초`마다 갱신, JSON+gzip bytes 미리 생성.
**핵심**: `start(db_cfg)`/`get()`(워밍업 전 (None,None))/`_refresh_loop`. `_LOCK` 보호.

### tabs_api.py

**역할**: 필터 탭 정의 CRUD(`product_filter_tabs` 테이블, id=`t`+epoch_ms). 상품 데이터와 무관, 탭 정의만.
**핵심**: `_validate_name`(80자)/`_validate_filter`(groups[].conditions[], field·op 화이트리스트 — frontend filter-tabs.js와 동기). CRUD: list/create(201)/update(없으면 404)/delete. `ValidationError`→app.py가 400.
**DB**: R/W `product_filter_tabs`

> **비고**: `build_merged_dataset.py`(파일 기반, 시장 데이터 포함, 정적 JS 화면용)와 `products_api.build_payload`(DB 실시간, 5분 캐시, 시장 None, /manage 라이브 화면용)는 동일 상태판정/머지 로직의 두 갈래.

---

## 8. 루트 / 기타 스크립트

### fast_price_updater.py (운영, 2026-04-06)

**역할**: 수집처 접근 없이 BUYMA 검색으로 경쟁자 최저가만 크롤링, DB `purchase_price_krw`만으로 가격 인하/삭제. stock sync보다 가볍게 가격만 빠르게.
**흐름**: published+active+model_no 상품 조회(오래된 것 우선) → `/r/-O3/{model_no}/` 검색(중고/자기 제외) → 경쟁자 없으면 최저가 처리 / 최저가지만 gap 크면 재조정 / 최저가 아니면 경쟁자-1~9엔. 마진+면 edit API, 마진-면 delete API + DB 비활성화. `build_buyma_request_update`는 stock sync와 동일 full payload.
**CLI**: `--count`, `--dry-run`, `--brand`, `--source`, `--limit`, `--id`
**DB**: R ace/images/options/variants/`buyma_master_categories_data`, W `ace_products`/`ace_product_api_logs`. `run_fast_price_loop.bat`으로 반복 실행.

### run_daily_multisource.py (운영, ~16-17h)

**역할**: kasina/nextzennpack/labellusso 일일 자동화.
**Phase**: 1 collector 3개 병렬(`--skip-existing`) → 2 converter 순차(`--source-site --skip-translation`)+dedup → 3 Price·Translate·Image·Stock 4트랙(각 트랙 내 3 source 병렬) → 4 register 3개 병렬.
**CLI**: `--phase`, `--dry-run`
**호출**: 각 mall collector, `kasina/raw_to_converter_kasina.py`, `okmall/dedup_corrector.py`, `buyma_lowest_price_collector.py --new-only`, `convert_to_japanese_gemini.py --price-checked-only`, `r2_image_uploader.py`, 각 `stock_price_synchronizer_*.py`, `buyma_new_product_register.py`

### run_daily_naver.py (운영)

**역할**: 네이버 21개 mall 일일 자동화. `naver_cookies.json` 사전 로그인 전제.
**Phase**: 1 collector 1→21 **직렬**(캡챠 회피, `COLLECTOR_MAP`으로 BRAND/CATEGORY/BRANDSTORE 분기) → 2 converter 4 병렬(+dedup, `--skip-collect`면 `--include-unpublished` 강제) → 3 Price·Image 2트랙 병렬 → 4 Translate 4 병렬 → 5 Register 4 병렬 → 6 Stock 단일 스크립트 1회(register 뒤: deleted 복구→재변환→register→즉시 sync).
**CLI**: `--phase`(1~6), `--dry-run`, `--source`, `--skip-collect`, `--skip-stock`

### run_naver_collect_new10.py (운영, 부분실행)

**역할**: 신규 10개 smartstore collector만 직렬 실행. `naver_cookies.json` 전제.
**흐름**: 10개 mall `premiumsneakers_category_collector.py --source <mall> --skip-existing` 직렬.
**CLI**: `--dry-run`

### run_naver_stock_new10.py (운영, 부분실행)

**역할**: 신규 10개 mall stock sync만 직렬 실행.
**흐름**: 10개 mall `stock_price_synchronizer_naver.py --source <mall>` 직렬 + summary.
**CLI**: `--dry-run`

### reconvert_unpublished_names.py (1회성 보정)

**역할**: BUYMA 미등록 상품의 `name`만 raw 기준 converter 로직으로 재생성(다른 필드 미변경).
**흐름**: 브랜드 매핑 캐시 → `ace_products JOIN raw WHERE is_published=0` → `sanitize_text`+`resolve_brand_name_en`+kometa 셀러명 제거+`format_buyma_product_name` → 기존과 다르면 `--apply` 시 500 배치 UPDATE.
**CLI**: `--apply`, `--source`, `--limit`
**비고**: docstring은 "is_active=1 AND is_published=0"이지만 실제 SELECT는 `is_published=0`만(사실 보고).

### reupdate_ace_brand_name.py (1회성 보정)

**역할**: BUYMA 미등록 상품의 `brand_id`/`brand_name`을 새 우선순위(buyma_brand_name → mall_brand_name_en → 매핑없으면 미변경)로 재설정.
**흐름**: `mall_brands` 캐시 → `ace JOIN raw WHERE is_published=0` 매칭 → `--apply` 시 500 배치 UPDATE.
**CLI**: `--apply`, `--source`

### buyma_expiry_extender.py (커밋됨 commit 91ec087, 서버 cron 매일 07:00)

**역할**: `available_until` 만료 임박 출품 상품을 today+90으로 강제 연장(재고/가격 변동 없어 stock sync가 edit 안 보내는 문제 보완).
**운영**: Ubuntu 서버 crontab `0 7 * * *`로 매일 자동 실행(`--limit` 없이 전체), 로그 `buyma_stats/cron.log`. PS API 사용이라 WARP 불필요.
**흐름**: published+active+만료임박 조회 → **`okmall/stock_price_synchronizer.py`의 `StockPriceSynchronizer` import 재사용**(payload/API/write-back 공유) → `build_buyma_request`가 `control='delete'`면 SKIP(삭제는 stock sync 책임) → `call_buyma_api` 성공 시 DB write-back.
**CLI**: `--guard-days`(10), `--limit`, `--dry-run`
**전제**: `buyma_available_until_updater.py`로 1회 정합 후 운영.

### laprima/laprima_collector.py (운영 신규 수집처)

**역할**: laprima.co.kr(Cafe24) HTML 스크래핑 → `raw_scraped_data`(source='laprima'), 미등록 브랜드 `mall_brands` auto-INSERT. 브랜드 카테고리가 없어 `mall_categories` 리프를 순회하고 상세에서 브랜드 추출하는 점이 다름.
**흐름**: SessionManager → ko→en 브랜드 dict → `get_leaf_categories`(3단 리프) 순회 → 상세 파싱(테이블/옵션 `option_stock_data`/이미지 `ec-data-src`) → 브랜드 다단계 추출(테이블 영문 → CDN 폴더명 → ko→en lookup) → 가격 우선순위(회원혜택가>판매가>정상가) → model_id 없으면 스킵 → 10개 배치 UPSERT + `ensure_mall_brand`(`is_active=NULL` 검수대기 §1.6).
**CLI**: `--category`, `--limit`, `--dry-run`, `--skip-existing`, `--dump`
**DB**: R `mall_categories`/`mall_brands`/raw/ace, W `raw_scraped_data`/`mall_brands`
**후속**: 공용 converter(`--source-site laprima`)

> **loromoda/**: .py 없음(collect.md + HTML 샘플만, collector 미구현).

### okmall_reference/server.py (운영, 원격 서버측)

**역할**: BUYMA 웹훅(create/update/fail) 수신 Flask 서버. 파일 로그 + `ace_products` 상태 동기화. (로그 경로 `/home/ubuntu/buyma/...` 리눅스 서버용)
**흐름**: `POST /webhook/buyma`에서 `X-Buyma-Event` 분기:
- `create`/`update`: `buyer_deleted`면 `is_published=0, status='deleted'`; `id` 있으면 `buyma_product_id` 세팅 + `is_published=1, status='success', is_buyma_locked=1, buyma_registered_at`.
- `fail_to_create`: `商品IDは不正な値です`/`削除できない商品です`면 재등록 대상(`buyma_product_id=NULL` 등), 아니면 `status='fail'`.
- `fail_to_update`: `status='fail'`만(is_published 유지).
- 모든 분기 `ace_product_api_logs` 저장, `reference_number` 매칭. DB 실패해도 200.
**DB**: R/W `ace_products`/`ace_product_api_logs`

---

## 9. 알아둘 점 / 운영 메모

- **stock sync 매칭 키**: variant의 `color_value_original`/`size_value_original`(한글 원본)은 stock sync 매칭의 핵심. 번역(`convert_to_japanese_gemini.py`)이 절대 건드리면 안 됨. 일본어 fallback 매칭 제거가 5,320건+ false delete 사고의 근본 해결책(2026-05-21).
- **model_id 없으면 수집 스킵**: 모든 collector 공통. 이후 단계 진행 불가 → dead row 방지.
- **WARP 필요**: 공유오피스 IP가 `www.buyma.com` 차단. 최저가 검색/출품목록 크롤링 전 Cloudflare WARP ON 확인. PS API는 차단 안 됨. WARP ON에서도 naver 정상(Referer 패치).
- **naver Referer**: 스마트스토어 상세 진입 시 스토어 홈 Referer 필수(미적용 시 로그인+캡챠). brand.naver.com은 면제.
- **매핑 검수 정책(§1.6)**: `mall_brands`/`mall_categories.is_active` = NULL(검수대기)/1(사람승인)/0(사람차단). 시스템은 NULL로만 등록, converter 게이트가 `is_active=1`만 ace 변환. 검수 큐 = `WHERE is_active IS NULL`. 차단 후 잔존 ace 정리는 `buyma_inactive_mapping_cleaner.py`.
- **카테고리는 등록 후 수정 불가**: 자동 추론 위험, 신규 경로는 `buyma_category_id=NULL`로 INSERT 후 수동/Gemini 매핑.
- **등록 확정은 Webhook**: register는 `status='pending'`까지만. `okmall_reference/server.py` 웹훅 수신으로 `is_published=1, status='success'` 확정.
- **인코딩**: Windows subprocess에 `PYTHONIOENCODING=utf-8` + `encoding='utf-8'` 필수.
