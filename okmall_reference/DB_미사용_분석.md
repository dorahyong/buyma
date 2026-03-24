# DB 미사용 테이블/컬럼 분석 결과

> 분석일: 2026-02-26
> 대상: okmall 프로젝트 전체 Python 스크립트 vs ace_tables_create.sql 스키마

---

## 1. 미사용 테이블

**완전 미사용 테이블 없음** (11개 모두 사용 중)

| 테이블 | 참조 스크립트 | 비고 |
|--------|-------------|------|
| `ace_products` | 전체 | 핵심 테이블 |
| `ace_product_images` | image_collector, r2_uploader, register, synchronizer | |
| `ace_product_options` | converter, register, synchronizer, translator | |
| `ace_product_variants` | converter, register, synchronizer, translator | |
| `raw_scraped_data` | collector, converter, sync_categories | |
| `mall_brands` | orchestrator, collector, converter | |
| `mall_categories` | converter, sync_categories | |
| `buyma_master_categories_data` | price_collector, synchronizer, converter (JOIN) | |
| `pipeline_batches` | orchestrator | |
| `pipeline_control` | orchestrator | |
| `shipping_config` | **raw_to_ace_converter.py만** | 나머지 스크립트는 `BUYMA_FIXED_VALUES` 하드코딩 |

---

## 2. 완전 미사용 컬럼 (SELECT/INSERT/UPDATE 모두 없음)

### ace_products (5개)

| 컬럼 | 타입 | 설명 | 삭제 가능 여부 |
|------|------|------|---------------|
| `buyma_registered_at` | DATETIME | 바이마 등록 시각 | O - Webhook 수신 시 사용 예정이었으나 미구현 |
| `size_unit` | VARCHAR(20) | 사이즈 단위 | O - 사용처 없음 |
| `buyer_notes` | TEXT | 구매 메모 | O - 사용처 없음 |
| `is_image_uploaded` | TINYINT(1) | 이미지 업로드 여부 | O - `ace_product_images.is_uploaded`로 개별 관리 |
| `is_ready_to_publish` | TINYINT(1) | 등록 준비 완료 | O - 등록 조건은 `is_published=0` + `model_no` + 이미지 존재로 판단 |

### ace_product_images (1개)

| 컬럼 | 타입 | 설명 | 삭제 가능 여부 |
|------|------|------|---------------|
| `buyma_image_path` | TEXT | 바이마 이미지 경로 | O - R2 URL은 `cloudflare_image_url`에 저장 |

### mall_brands (2개)

| 컬럼 | 타입 | 설명 | 삭제 가능 여부 |
|------|------|------|---------------|
| `mapping_level` | BIGINT(20) | 매핑 신뢰도 레벨 | O - 초기 매핑 작업 시 사용 후 미사용 |
| `is_mapped` | TINYINT(1) | 매핑 완료 여부 | O - `is_active`로 관리 |

### pipeline_control (1개)

| 컬럼 | 타입 | 설명 | 삭제 가능 여부 |
|------|------|------|---------------|
| `item_count` | INT(11) | 처리 아이템 수 | O - INSERT/UPDATE에서 값 설정 안 함 (항상 기본값 0) |

---

## 3. Write-only 컬럼 (INSERT는 하지만 읽지 않음)

### ace_products (3개)

| 컬럼 | 타입 | INSERT 위치 | 비고 |
|------|------|------------|------|
| `reference_price` | INT(11) | raw_to_ace_converter | API에서는 `original_price_jpy`를 직접 사용 |
| `regular_price` | INT(11) | raw_to_ace_converter | 저장만 하고 활용 없음 |
| `reference_price_verify_count` | INT(11) | raw_to_ace_converter (항상 0) | 검증 로직 미구현 |

### mall_categories (1개)

| 컬럼 | 타입 | INSERT 위치 | 비고 |
|------|------|------------|------|
| `gender` | VARCHAR(20) | raw_to_ace_converter (`_register_unmapped_category`) | 추론하여 저장하지만 읽는 곳 없음 |

---

## 4. 코드 미사용 참조 데이터 컬럼

### buyma_master_categories_data (4개)

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `buyma_paths` | VARCHAR(255) | 코드는 `buyma_category_id` + `expected_shipping_fee`만 읽음 |
| `buyma_name` | VARCHAR(255) | 동일 |
| `mall_paths` | VARCHAR(255) | 동일 |
| `mall_name` | VARCHAR(255) | 동일 |

> 수동 매핑 참조용으로 유지해도 무방. 코드에서 불필요할 뿐 관리 편의상 유용.

---

## 5. 정리 우선순위 제안

### 즉시 삭제 가능 (영향 없음)
1. `ace_products.size_unit`
2. `ace_products.buyer_notes`
3. `ace_products.is_image_uploaded`
4. `ace_products.is_ready_to_publish`
5. `ace_product_images.buyma_image_path`
6. `mall_brands.mapping_level`
7. `mall_brands.is_mapped`
8. `pipeline_control.item_count`

### 삭제 전 확인 필요 (Webhook 등 미래 계획 확인)
1. `ace_products.buyma_registered_at` - Webhook 수신 기능 구현 예정이면 유지
2. `ace_products.reference_price` - `original_price_jpy`와 중복이지만 의미가 다를 수 있음
3. `ace_products.regular_price` - 통상 가격 관리 용도로 나중에 쓸 수 있음
4. `ace_products.reference_price_verify_count` - 정가 검증 기능 구현 예정이면 유지

### 유지 권장 (참조 데이터)
1. `buyma_master_categories_data`의 4개 메타 컬럼 - 수동 매핑 시 참고용
2. `mall_categories.gender` - 필터링용으로 활용 가능성 있음

---

## 6. 삭제 SQL (필요 시 사용)

```sql
-- ★ 실행 전 반드시 백업할 것!

-- ace_products: 완전 미사용 5개
ALTER TABLE ace_products DROP COLUMN size_unit;
ALTER TABLE ace_products DROP COLUMN buyer_notes;
ALTER TABLE ace_products DROP COLUMN is_image_uploaded;
ALTER TABLE ace_products DROP COLUMN is_ready_to_publish;
ALTER TABLE ace_products DROP COLUMN buyma_registered_at;

-- ace_product_images: 완전 미사용 1개
ALTER TABLE ace_product_images DROP COLUMN buyma_image_path;

-- mall_brands: 완전 미사용 2개
ALTER TABLE mall_brands DROP COLUMN mapping_level;
ALTER TABLE mall_brands DROP COLUMN is_mapped;

-- pipeline_control: 완전 미사용 1개
ALTER TABLE pipeline_control DROP COLUMN item_count;

-- ace_products: write-only 3개 (선택)
-- ALTER TABLE ace_products DROP COLUMN reference_price;
-- ALTER TABLE ace_products DROP COLUMN regular_price;
-- ALTER TABLE ace_products DROP COLUMN reference_price_verify_count;
```
