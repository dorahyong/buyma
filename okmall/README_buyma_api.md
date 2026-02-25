# AI를 이용한 바이마 상품관리 크롤러 - okmall

## 바이마 url
BUYMA_CALLBACK_REDIRECT_URL=https://buyma-api.oneblocks.co.kr/oauth/callback
BUYMA_WEBHOOK_URL=https://buyma-api.oneblocks.co.kr/webhook/buyma


## 바이마 API ACCESS_TOKEN 테이블
CREATE TABLE `buyma_tokens` (
	`id` INT(11) NOT NULL DEFAULT '1',
	`access_token` TEXT NOT NULL COLLATE 'utf8mb4_unicode_ci',
	`refresh_token` TEXT NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`updated_at` TIMESTAMP NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
	PRIMARY KEY (`id`) USING BTREE,
	CONSTRAINT `single_row` CHECK (`id` = 1)
)
COLLATE='utf8mb4_unicode_ci'
ENGINE=InnoDB
;


---

## 마진 계산

### 고정값
| 항목 | 값 | 설명 |
|------|-----|------|
| 환율 | 9.2 | 원/엔 (고정) |
| 판매수수료 | 5.5% | 바이마 판매수수료 |
| 부가세환급 | 구매가/11 | 부가세 환급액 |

### 계산에 사용되는 컬럼

| 데이터 | 테이블.컬럼 | 설명 |
|--------|-------------|------|
| 바이마 최저가 (엔) | ace_products.buyma_lowest_price | 바이마에서 검색한 최저가 |
| 구매가 (원) | ace_products.source_sales_price | 오케이몰 판매가 |
| 예상 배송비 (원) | buyma_master_categories_data.expected_shipping_fee | 카테고리별 배송비 |
| 마진율 (%) | ace_products.margin_rate_percent | 계산된 마진율 (저장) |
| 마진 계산 시점 | ace_products.margin_calculated_at | 마진 계산 시점 |

### 계산 공식

```
1. 바이마 판매가 (원) = 바이마 최저가 (엔) × 환율(9.2)
2. 판매수수료 (원) = 바이마 판매가 (원) × 5.5%
3. 실수령액 (원) = 바이마 판매가 (원) - 판매수수료
4. 총 원가 (원) = 구매가 + 예상 배송비
5. 마진 (환급X) = 실수령액 - 총 원가
6. 부가세 환급액 = 구매가 ÷ 11
7. 마진 (환급포함) = 마진 (환급X) + 부가세 환급액
8. 마진율 (%) = 마진 (환급포함) ÷ 바이마 판매가 (원) × 100
```

### 계산 예시

```
[입력값]
- 바이마 최저가: ¥26,677
- 구매가: ₩190,180
- 예상 배송비: ₩14,900

[계산]
1. 바이마 판매가 (원) = 26,677 × 9.2 = ₩245,428
2. 판매수수료 = 245,428 × 5.5% = ₩13,499
3. 실수령액 = 245,428 - 13,499 = ₩231,929
4. 총 원가 = 190,180 + 14,900 = ₩205,080
5. 마진 (환급X) = 231,929 - 205,080 = ₩26,849
6. 부가세 환급액 = 190,180 ÷ 11 = ₩17,289
7. 마진 (환급포함) = 26,849 + 17,289 = ₩44,138
8. 마진율 = 44,138 ÷ 245,428 × 100 = 17.98%
```

### 저장 정책

최종 결론

  ┌─────────────────────────────────────────────────────────┐
  │                    권장 방법 3                          │
  ├─────────────────────────────────────────────────────────┤
  │                                                         │
  │  [저장] ace_products                                    │
  │  ├── original_price_krw      ← 원본                    │
  │  ├── original_price_ref      ← 원본 (출처)             │
  │  ├── purchase_price_krw      ← 원본                    │
  │  ├── margin_rate_percent     ← 파생 (필터링용)         │
  │  └── margin_calculated_at    ← 계산 시점               │
  │                                                         │
  │  [View] v_ace_products_margin                           │
  │  ├── original_price_jpy      ← 계산                    │
  │  ├── sales_price_jpy         ← 계산                    │
  │  ├── margin_with_refund_krw  ← 계산                    │
  │  ├── margin_without_refund_krw ← 계산                  │
  │  └── vat_refund_krw          ← 계산                    │
  │                                                         │
  └─────────────────────────────────────────────────────────┘

| 구분 | 저장 여부 | 이유 |
|------|----------|------|
| margin_rate_percent | ✅ 저장 | 등록 대상 WHERE절 필터링용 |
| margin_calculated_at | ✅ 저장 | 계산 시점 추적 |
| 마진 금액 (원) | ❌ 계산 | 필요시 실시간 계산 |
| 부가세환급액 | ❌ 계산 | 필요시 실시간 계산 |
| 판매수수료 | ❌ 계산 | 필요시 실시간 계산 |

### 등록 대상 조건

```sql
WHERE margin_rate_percent >= 10.0  -- 최소 마진율 10%
  AND buyma_lowest_price IS NOT NULL
  AND buyma_lowest_price > 0
```

### 마진 계산 시점

| 시점 | 설명 |
|------|------|
| 최저가 수집 후 | buyma_lowest_price 업데이트 시 마진율 재계산 |
| 가격 변경 시 | source_sales_price 변경 시 마진율 재계산 |


---

## 카테고리별 예상 배송비 테이블

```sql
CREATE TABLE `buyma_master_categories_data` (
    `buyma_category_id` INT(11) NOT NULL,
    `buyma_paths` VARCHAR(255) NOT NULL COLLATE 'utf8mb4_unicode_ci',
    `buyma_name` VARCHAR(255) NOT NULL COLLATE 'utf8mb4_unicode_ci',
    `mall_paths` VARCHAR(255) NOT NULL COLLATE 'utf8mb4_unicode_ci',
    `mall_name` VARCHAR(255) NOT NULL COLLATE 'utf8mb4_unicode_ci',
    `expected_shipping_fee` INT(11) NOT NULL COMMENT '예상 해외배송비(원)',
    `created_at` DATETIME NOT NULL DEFAULT current_timestamp(),
    `updated_at` DATETIME NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
    PRIMARY KEY (`buyma_category_id`) USING BTREE
)
COLLATE='utf8mb4_unicode_ci'
ENGINE=InnoDB
;
```

### 배송비 조회 방법

```sql
-- ace_products와 buyma_master_categories_data JOIN
SELECT
    ap.id,
    ap.buyma_lowest_price,
    ap.source_sales_price,
    COALESCE(bmc.expected_shipping_fee, 15000) as shipping_fee
FROM ace_products ap
LEFT JOIN buyma_master_categories_data bmc
    ON ap.category_id = bmc.buyma_category_id
WHERE ap.id = :product_id;
```
