# mall_brands / mall_categories is_active 정책 통일 + convert 게이트 도입 (2026-06-02)

## 핵심 성과
`mall_brands` / `mall_categories` 의 **`is_active` 의미를 정책으로 통일**하고, 미검수 매핑이 ace 변환으로 흘러가지 못하도록 **convert 게이트**를 도입. 이미 잘못 흘러간 잔존 ace 1,402건(BUYMA 라이브 165 + ghost 272 포함)을 정리하고, dead column `raw_scraped_data.is_active` 를 DROP. 시스템(자동)과 사람(검수)의 경계를 `is_active = NULL / 1 / 0` 으로 명확히 분리.

---

## 1. 배경 & 문제

`mall_brands` / `mall_categories` 의 `is_active` 컬럼이 여러 경로로 INSERT/UPDATE 되면서 의미가 흐트러져 있었음:

- 자동등록(수집기/converter)은 `is_active=1` 로 들어와 **검수 안 된 매핑이 즉시 ace 변환에 사용**됨
- 일부는 NULL, 일부는 1, 일부는 0 — 등록 경로마다 다름
- `is_active=0` 인 매핑에 연결된 ace 행 **1,402건 잔존** (그 중 165건은 BUYMA에 실제 출품 중)
- `raw_scraped_data.is_active` 컬럼은 코드에서 전혀 안 쓰이는 dead column

→ 정책 통일 + 잔존 데이터 정리 + 재발 방지 게이트 필요.

---

## 2. 합의된 정책

| 값 | 의미 | 누가 결정 |
|---|---|---|
| NULL | 자동등록 후 검수 대기 | 시스템 |
| 1 | 사용 가능 (검수 완료) | 사람 |
| 0 | 사용 불가 (명시적 차단) | 사람 |

### 핵심 원칙
- 시스템은 **NULL 로만** 등록한다. 0/1 을 자동으로 결정하지 않는다.
- 사람만 1 또는 0 으로 확정한다.
- `is_active=1` 인 매핑에 한해서만 BUYMA 등록 대상으로 진행한다.
- 자동매핑 도구(Gemini, kasina `match_brands.py`)는 `buyma_*_id` 만 채우고 `is_active` 는 NULL 그대로 둠 → 사람 검수 후 1/0 확정.

### 검수자 큐 식별
```sql
SELECT * FROM mall_brands     WHERE is_active IS NULL;
SELECT * FROM mall_categories WHERE is_active IS NULL;
```

---

## 3. 작업 내역

### 3-1. `raw_scraped_data.is_active` 컬럼 DROP
- 코드에서 단 한 군데도 참조되지 않음을 검증 (직접 / alias / `SELECT *` 모두 0건)
- 인덱스/트리거/뷰 의존성 0건
- 실데이터 366개가 0 으로 있었으나 어디서도 필터에 안 쓰여 의미 없음
- `ALTER TABLE raw_scraped_data DROP COLUMN is_active;` 실행

### 3-2. `ace_products` 잔존 데이터 정리 (1,402행)

`mall_brands.is_active=0` 또는 `mall_categories.is_active=0` 매핑에 걸리는 ace 행 정리.

대상 분포:

| 항목 | 건수 |
|---|---|
| 총 대상 ace 행 | 1,402 |
| ├ BUYMA published=1 (라이브) | 165 |
| ├ ghost (published=0 + buyma_product_id 잔존) | 272 |
| └ DB only (buyma_product_id 없음) | 965 |
| BUYMA delete API 시도 대상 | 437 (165 + 272) |

brand 단위 hit 상위 (`mall_brands.is_active=0`):
- upset 358 / okmall 222 / trendmecca 150 / carpi 85 / fabstyle 61 / labellusso 58 …
- 특기: NEEDLES 가 5개 몰(fabstyle/labellusso/veroshopmall/joharistore/premiumsneakers)에서 일괄 차단되어 134건 published 잔존

category 단위 hit 상위 (`mall_categories.is_active=0`):
- kasina 177 / trendmecca 33 / nextzennpack 30 / bblue 24 …

새 cleaner 작성: **`buyma_cleaners/buyma_inactive_mapping_cleaner.py`**
- JOIN(`ace_products` × `raw_scraped_data` × `mall_brands` × `mall_categories`) 으로 대상 추출
- `buyma_product_id` 있으면 BUYMA delete API 호출 → 성공/실패 무관 DB DELETE
- FK 순서대로 `ace_product_api_logs` → `ace_products` DELETE
- per-item commit, idempotent, `--count` / `--dry-run` / `--limit` 옵션
- `raw_scraped_data` 는 건드리지 않음 (게이트가 막아주므로 재변환 없음)

실행 결과:
```
완료: API OK 437 / API 실패 0 / DB only 965 / DB 오류 0
재검증: 잔여 0건
```
- BUYMA API 437/437 전부 성공
- DB DELETE 1,402/1,402 성공
- 소요: 약 16분

### 3-3. `mall_brands` / `mall_categories` 자체 정합 (4행 보정)

발견된 모순: `buyma_*_id IS NULL` 인데 `is_active=1/NULL` 상태 = 매핑 정보 자체가 없는데 검수된 것처럼 표시됨.

| 테이블 | 보정 전 | 보정 후 |
|---|---|---|
| mall_brands `is_active=1 AND buyma_brand_id IS NULL` (2행) | 1 | 0 |
| mall_categories `(is_active=1 OR NULL) AND buyma_category_id IS NULL` (2행) | 1/NULL | 0 |

보존된 정상 상태:
- mall_categories `is_active IS NULL AND buyma_category_id IS NOT NULL` — 113건 (Gemini 자동매핑 완료 + 사람 검수 대기, 정책상 정상)
- mall_brands `is_active=1 AND buyma_brand_id=0` — 158건 (BUYMA 미등록이지만 활성 수집, converter가 raw `brand_en` 으로 fallback 처리 — 정상)

### 3-4. 코드 수정 — convert 게이트 + 자동등록 NULL

5개 파일 수정:

| # | 파일 | 변경 |
|---|---|---|
| 1 | `okmall/raw_to_ace_converter.py` | `get_brand_info` / `get_category_info` 캐시 miss → None 반환 (skip 신호) / `convert_single_raw_to_ace` 진입부에서 None 검사 후 즉시 return / `run_conversion` 루프에서 `ace_data is None` 이면 `skipped += 1` 처리 / `_register_unmapped_category` INSERT 시 `is_active=1` → NULL |
| 2 | `kasina/raw_to_converter_kasina.py` | 위와 동일 4가지 |
| 3 | `laprima/laprima_collector.py:1041` | `mall_brands` 자동등록 `is_active=1` → NULL |
| 4 | `naver/scan_store_brands.py` | `mall_brands` 자동등록 `is_active=1` → NULL (L477) / `mall_categories` 자동등록 `is_active=NULL` 명시 추가 (L542) |
| 5 | `naver/premiumsneakers/premiumsneakers_category_collector.py:161` | `mall_brands` 자동등록 `is_active=1` → NULL |

게이트 동작:
- raw 의 brand 가 `mall_brands.is_active=1` 캐시에 없음 → ace INSERT skip
- raw 의 category 가 `mall_categories.is_active=1` 캐시에 없음 → 신규 path 는 `is_active=NULL` 로 검수 큐에 자동 등록 후 skip
- 기존 ace 의 upsert 시에도 동일하게 게이트 작동
- 빈 category_path 는 register 호출 안 함 (코드 가드)

---

## 4. 검증 (dry-run)

| Converter | 처리 | 신규 INSERT | 업데이트 | skip (게이트) | 실패 |
|---|---|---|---|---|---|
| okmall | 1,000건 | 3 | 706 | 291 | 0 |
| kasina | 1,000건 | 1 | 753 | 246 | 0 |

skip 사유 샘플:
- 매핑 없는 브랜드: ADIDAS (kasina), Lacoste (okmall) — 의도적 차단으로 추정
- 매핑 없는 카테고리: `MEN > 위탁 브랜드`, `MEN > 2025 > HO`, `LUXURY > 여성 가방&ACC > 모자 > 썬캡/바이저` 등 신규 path
- 빈 category_path: 가드 통과

전 5개 파일 `python -m py_compile` 통과.

---

## 5. 운영 가이드 (검수자용)

### 매일 보는 큐
```sql
-- 검수 대기 (자동등록되어 사람 손길 필요)
SELECT * FROM mall_brands     WHERE is_active IS NULL;
SELECT * FROM mall_categories WHERE is_active IS NULL;

-- AI 자동매핑이 buyma_id 까지 채운 것만 (확인만 하면 됨)
SELECT * FROM mall_brands     WHERE is_active IS NULL AND buyma_brand_id    IS NOT NULL;
SELECT * FROM mall_categories WHERE is_active IS NULL AND buyma_category_id IS NOT NULL;
```

### 검수자 결정
- **1**: 매핑 OK, 사용 시작 → 다음 converter run 부터 ace 변환됨
- **0**: 사용 불가 → 해당 매핑의 raw 는 계속 ace skip 됨 (BUYMA 잔존분은 `buyma_inactive_mapping_cleaner.py` 한 번 더 돌리면 정리됨)

### 통로
- 웹 UI: `manage_server/brands_api.py`, `categories_api.py`
- 일괄: `buyma_cleaners/update_mall_brands_manual.py` (TSV), `category_cleaner.py import` (CSV)
- 직접 SQL

### 운영 시 주의
- 한 번 검수 완료된 브랜드/카테고리를 나중에 `is_active=0` 으로 바꿔도 이미 ace 에 들어가 있는 상품은 자동 삭제되지 않음. 정리 필요 시 `buyma_inactive_mapping_cleaner.py` 실행.
- 자동매핑 도구 돌려도 안전 — `is_active` 는 안 건드리므로 사람 검수 우회 못 함.

---

## 6. 결과 요약

| 항목 | 결과 |
|---|---|
| `raw_scraped_data.is_active` 컬럼 | DROP 완료 (dead column 정리) |
| `ace_products` 잔존 정리 | 1,402건 (BUYMA API 437 + DB 1,402) |
| mall_brands/categories 정합 | 4행 보정 |
| 코드 게이트 도입 | 5개 파일 수정 |
| 신규 cleaner 도구 | `buyma_inactive_mapping_cleaner.py` |
| Dry-run 검증 | 2,000건 처리, 게이트 537건 skip, 실패 0 |

### 효과
- 미검수 매핑이 ace 로 흘러갈 수 없음 (재발 방지)
- BUYMA 의 의도되지 않은 출품 정리 (165건 + ghost 272건)
- 검수자 워크플로 단순화 (`WHERE is_active IS NULL` 한 쿼리)
- AI 자동화와 사람 검수의 명확한 경계 (시스템=NULL, 사람=1/0)
