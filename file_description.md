# 파일 설명

## okmall/ 파이프라인 스크립트 (운영)

일일 자동화 흐름: `run_daily.py` → `orchestrator.py` + `stock_price_synchronizer.py`

```
run_daily.py (진입점)
├── orchestrator.py (6단계 파이프라인)
│   ├── [COLLECT]    okmall_all_brands_collector.py    - OkMall 상품 수집
│   ├── [CONVERT]    raw_to_ace_converter.py            - 데이터 변환
│   │                 └── convert_to_japanese_gemini.py  - Gemini 일본어 번역 (import)
│   ├── [PRICE]      buyma_lowest_price_collector.py    - 바이마 최저가 수집
│   ├── [TRANSLATE]  convert_to_japanese_gemini.py      - 번역 (최저가 확보분만)
│   ├── [IMAGE]      image_collector_parallel.py        - 이미지 수집
│   │                 → r2_image_uploader.py             - R2 이미지 업로드
│   └── [REGISTER]   buyma_new_product_register.py     - 바이마 상품 등록
│
└── stock_price_synchronizer.py                        - 등록 상품 재고/가격 동기화
```

| 파일 | 역할 |
|---|---|
| `run_daily.py` | 일일 자동화 진입점. orchestrator → synchronizer 순차 실행. `--brand`, `--sync-only`, `--register-only` 옵션 |
| `orchestrator.py` | 6단계 파이프라인 오케스트레이터. 브랜드별 병렬 처리, pipeline_control 기반 중단/재개 |
| `okmall_all_brands_collector.py` | OkMall에서 브랜드별 상품 수집 → raw_scraped_data INSERT |
| `raw_to_ace_converter.py` | raw_scraped_data → ace_products 변환. 사이즈/색상 옵션, 실측 정보 처리 |
| `convert_to_japanese_gemini.py` | Gemini API로 상품명/설명 일본어 번역. CONVERT(import)와 TRANSLATE(subprocess) 두 곳에서 사용 |
| `buyma_lowest_price_collector.py` | 바이마에서 동일 model_no 경쟁 상품의 최저가 수집 |
| `image_collector_parallel.py` | Playwright로 OkMall 상품 이미지 병렬 수집 |
| `r2_image_uploader.py` | 수집된 이미지를 Cloudflare R2에 업로드, ace_product_images에 URL 저장 |
| `buyma_new_product_register.py` | ace_products 데이터를 바이마 Shopper API로 등록 |
| `stock_price_synchronizer.py` | 등록된 상품(is_published=1)의 재고/가격을 OkMall 원본과 동기화 |

### 데이터 파일

| 파일 | 용도 |
|---|---|
| `../.env` | DB, API 키, R2 인증 등 환경변수 |
| `colors.csv` | 색상명 → 바이마 색상 매핑 |
| `ace_tables_create.sql` | DB 스키마 정의 (초기 설정용) |
| `buyma_master_data_20260226/` | 바이마 API 마스터 데이터 (categories, brands, sizes 등). converter/synchronizer에서 참조 |
| `buyma_master_data_csv_to_md_translator_20260226.py` | 마스터 데이터 CSV를 Gemini로 일→한 번역하여 md/ 생성. 1회성 유틸 (실행 완료) |
| `logs/{batch_id}.log` | orchestrator 실행 로그 (런타임 생성) |

---

## okmall/measurement_key_cache.json
- 한국어 사이즈 측정 키 → 일본어 키 매핑 캐시 (Gemini API 번역 결과)
- 생성: `raw_to_converter_auto_measurements.py`, `kasina/raw_to_converter_kasina.py` 실행 시 자동 생성/갱신
- 용도: 이미 번역한 키를 재사용하여 Gemini API 호출 절감
- 삭제 시: 다음 실행 때 재생성됨 (API 비용 약간 발생)

## v2_multisource/ (멀티소스 통합 작업 파일)
- 멀티소스(okmall + kasina) 통합을 위한 v2 개발 파일 모음
- 현재 운영에서 사용하지 않음. 멀티소스 통합 재개 시 참고용

| 파일 | 역할 | 상태 |
|---|---|---|
| `orchestrator_v2.py` | 7단계 파이프라인 (+MERGE 단계, kasina 병렬 수집) | 동작하나 converter 이슈 연결 |
| `raw_to_converter_v2.py` | 멀티소스 best source 자동 선택 converter | 버그: PARTITION BY model_id 색상 유실 |
| `raw_to_converter_auto_measurements.py` | Gemini 자동 측정키 번역 converter | kasina converter에 흡수됨 (중복) |
| `stock_price_synchronizer_v2.py` | 등록 상품 재고/가격 동기화 멀티소스 버전 | 미완성 (S-1~S-4 코딩 필요) |
| `stock_merge.py` | MERGE 단계: 멀티소스 재고를 마진 기준 통합 | stock_utils 의존 |
| `stock_utils.py` | 공통 모듈: 마진 계산, variants 파싱, merge 로직 | 핵심 모듈 |

## okmall_reference/ (참고 자료)
- 운영 중 참고하는 문서, DB 스키마, 유틸 서버 등 모음

| 파일 | 설명 |
|---|---|
| `README.md` | 프로젝트 전체 설명 문서 |
| `REFERENCE.md` | API/DB 참고 문서 |
| `ace_tables_create.sql` | DB 전체 스키마 정의 (실제 DB와 일치, 2026-03-24 기준) |
| `DB_미사용_분석.md` | DB 테이블/컬럼 사용 여부 분석 메모 |
| `buyma_fail.md` | 바이마 등록 실패 케이스 디버깅 메모 |
| `issue_image.md` | 이미지 관련 이슈 메모 |
| `server.py` | 독립 실행 서버 (파이프라인과 무관, 수동 실행) |

## buyma_cleaners/ (정리 스크립트)

바이마/DB/R2의 불일치·중복 데이터를 정리하는 **수동 실행** 스크립트 모음.
어디서도 자동 호출되지 않으며, 필요할 때 직접 실행한다.
모든 스크립트는 `--dry-run` 옵션을 지원하므로, **반드시 dry-run으로 먼저 확인 후 실행**할 것.

---

### 1. cleanup_duplicates.py (반복 유지보수)

DB에 쌓인 중복 데이터를 3단계로 정리한다.

**정리 대상:**
- Step 1: `ace_products`에서 같은 `model_no`가 2개 이상인 경우 → 중복분 삭제
- Step 2: `raw_scraped_data`에서 같은 `model_id`가 2개 이상인 경우 → 연결된 ace 데이터 포함 삭제
- Step 3: `ace_products`에 `raw_data_id`가 있지만 해당 raw 레코드가 없는 고아 → 삭제

**바이마에 등록된 상품이면 바이마 API로 먼저 삭제 후 DB에서 제거한다.**

**언제 사용?**
- COLLECT/CONVERT 반복 실행 후 중복이 의심될 때
- DB 정합성 점검 시 정기적으로 (주 1회 등)

```bash
cd buyma_cleaners
python cleanup_duplicates.py --dry-run    # 먼저 확인
python cleanup_duplicates.py              # 실제 삭제
```

---

### 2. buyma_orphan_cleaner.py (반복 유지보수)

바이마 사이트와 DB 간 불일치를 정리한다. 두 가지 문제를 해결:

**Orphan (바이마에 있는데 DB에 없음):**
- 예: 등록 후 DB에서 수동 삭제했거나, 등록 중 DB 기록이 실패한 경우
- → 바이마 Shopper API로 해당 상품 삭제

**Ghost (DB에 published인데 바이마에 없음):**
- 예: 바이마에서 수동으로 상품을 삭제했지만 DB는 is_published=1인 상태
- → DB에서 `is_published=0`으로 수정

**언제 사용?**
- 등록 실패/네트워크 오류 후 정합성 점검 시
- 바이마에서 수동으로 상품을 삭제한 후

```bash
cd buyma_cleaners
# 1단계: 바이마 로그인 (최초 1회, 쿠키 저장)
python buyma_orphan_cleaner.py --login

# 2단계: 바이마 전체 상품 스캔 + orphan/ghost 탐지
python buyma_orphan_cleaner.py --scan

# 3단계: orphan 삭제
python buyma_orphan_cleaner.py --delete --dry-run
python buyma_orphan_cleaner.py --delete

# 4단계: ghost 상태 수정
python buyma_orphan_cleaner.py --clean-ghost --dry-run
python buyma_orphan_cleaner.py --clean-ghost

# 한번에 실행
python buyma_orphan_cleaner.py --scan --delete --clean-ghost
```

---

### 3. buyma_unpublished_cleaner.py (반복 유지보수)

DB에서 `is_published=0`인데 `buyma_product_id`가 남아있는 비정상 상태를 정리한다.

**정리 대상:**
- 예: synchronizer가 상품을 삭제 처리했지만 바이마에서 실제 삭제 API 호출이 실패한 경우
- → 바이마 API로 삭제 + DB에서 buyma_product_id=NULL, status='deleted' 처리

**언제 사용?**
- `stock_price_synchronizer.py` 실행 후 삭제 실패 건이 있을 때
- `SELECT COUNT(*) FROM ace_products WHERE is_published=0 AND buyma_product_id IS NOT NULL`로 확인

```bash
cd buyma_cleaners
python buyma_unpublished_cleaner.py --count              # 대상 건수만 확인
python buyma_unpublished_cleaner.py --dry-run             # 대상 목록 출력
python buyma_unpublished_cleaner.py                       # 실제 삭제
python buyma_unpublished_cleaner.py --brand ALYX          # 특정 브랜드만
python buyma_unpublished_cleaner.py --limit 10            # 최대 10개만
```

---

### 4. cleanup_old_format_products.py (1회성, 완료)

구형 이름 포맷(【 문자 포함)으로 등록된 상품을 일괄 삭제한다.
삭제 후 orchestrator CONVERT 단계를 재실행하면 새 포맷으로 재생성된다.

**이미 실행 완료된 스크립트. 같은 종류의 일괄 정리가 필요할 때 템플릿으로 참고 가능.**

```bash
cd buyma_cleaners
python cleanup_old_format_products.py --dry-run
python cleanup_old_format_products.py
```

---

### 5. r2_orphan_cleaner.py (1회성)

Cloudflare R2에 업로드된 이미지 중 더 이상 필요 없는 파일을 CSV 기반으로 배치 삭제한다.

**사용법:**
- 삭제할 R2 URL 목록을 CSV로 준비 (헤더: `cloudflare_image_url`)
- `--file` 옵션으로 CSV 경로 지정

```bash
cd buyma_cleaners
python r2_orphan_cleaner.py --file=삭제대상.csv --dry-run
python r2_orphan_cleaner.py --file=삭제대상.csv
```

---

### 권장 실행 순서

데이터 정합성을 전체적으로 점검할 때는 아래 순서로 실행:

1. `cleanup_duplicates.py` — DB 내부 중복 먼저 제거
2. `buyma_orphan_cleaner.py` — 바이마↔DB 불일치 정리
3. `buyma_unpublished_cleaner.py` — 미등록 잔여 상품 정리
4. (필요 시) `r2_orphan_cleaner.py` — R2 고아 이미지 정리
