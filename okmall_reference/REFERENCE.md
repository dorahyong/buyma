# 기술 레퍼런스

바이마 API, 마진 계산, 외부 사이트 수집 스펙, DB DDL 등 개발/운영 참조 문서입니다.

---

## 목차

1. [바이마 API 레퍼런스](#바이마-api-레퍼런스)
2. [마진 계산](#마진-계산)
3. [외부 사이트 수집 스펙](#외부-사이트-수집-스펙)
4. [이슈 해결 기록](#이슈-해결-기록)
5. [DB 테이블 DDL](#db-테이블-ddl)

---

## 바이마 API 레퍼런스

### API 스펙 문서 URL

| 항목 | URL |
|------|-----|
| REST API | https://specification.personal-shopper-api.buyma.com/api/ |
| Webhook | https://specification.personal-shopper-api.buyma.com/api/webhook/ |
| 상품 API | https://specification.personal-shopper-api.buyma.com/api/products_json/ |
| 재고 API | https://specification.personal-shopper-api.buyma.com/api/products_json/ |
| API 속도제한 | https://specification.personal-shopper-api.buyma.com/api/rate_limit/ |

### API 엔드포인트

| 환경 | Base URL | 상품 API |
|------|----------|----------|
| 운영 | `https://personal-shopper-api.buyma.com/` | `{base}api/v1/products` |
| 샌드박스 | `https://sandbox.personal-shopper-api.buyma.com/` | `{base}api/v1/products` |

> `BUYMA_MODE=1`이면 운영, 그 외 샌드박스 (stock_price_synchronizer.py:85, buyma_new_product_register.py:121)

### API 호출 제한

| API 대상 | 제한 | 기간 |
|----------|------|------|
| 전체 API | 5,000회 | 1시간 |
| 상품 API | 2,500회 | 24시간 |

### 상품 API 고정값

> 코드 내 `BUYMA_FIXED_VALUES` 딕셔너리로 관리

| 필드명 | 값 | 설명 | 코드 위치 |
|--------|-----|------|----------|
| buying_area_id | `2002003000` | 구매 지역 ID (한국) | 모든 파일 공통 |
| shipping_area_id | `2002003000` | 발송 지역 ID (한국) | 모든 파일 공통 |
| theme_id | `98` | 테마 ID | 모든 파일 공통 |
| duty | `included` | 관세 포함 | 모든 파일 공통 |
| shipping_method_id | `1063035` | 배송 방법 ID | 모든 파일 공통 |
| buying_shop_name | `{브랜드명}正規販売店` | 구매처명 | buyma_new_product_register.py, raw_to_ace_converter.py |

### 상품 API 필수 필드 요약

| 구분 | 필드 |
|------|------|
| 필수 | control, name, comments, brand_id, category_id, price, available_until, buying_area_id, shipping_area_id, images, shipping_methods, options, variants |
| 조건부 필수 | id 또는 reference_number 중 하나 |
| brand_id=0일 때 | brand_name 추가 필요 (미등록 브랜드) |

### Webhook 이벤트

| 이벤트 | DB 업데이트 |
|--------|------------|
| `product/create` | `is_published=1`, `buyma_product_id` 저장, `is_buyma_locked=1` |
| `product/update` | 동일 |
| `product/fail_to_create` | `is_published=0`, `is_buyma_locked=0`, 에러 메시지 저장 |
| `product/fail_to_update` | 에러 종류에 따라 `is_active=0` 또는 재등록 대상 처리 |

---

## 마진 계산

### 고정값

| 항목 | 값 | 설명 | 코드 위치 |
|------|-----|------|----------|
| 환율 | 9.2 | 원/엔 (고정) | EXCHANGE_RATE (모든 파일) |
| 판매수수료 | 5.5% | 바이마 판매수수료 | SALES_FEE_RATE=0.055 |
| 부가세환급 | 구매가/11 | 부가세 환급액 | calculate_margin() 내 |
| 기본 배송비 | 15,000원 | 카테고리별 배송비 없을 때 | DEFAULT_SHIPPING_FEE |
| 정가 환산율 | 0.1 | KRW → JPY 정가 (KRW / 10) | EXCHANGE_RATE_FOR_REFERENCE_PRICE |
| 최소 엔화가 | 500엔 | 엔화 변환 최솟값 | MIN_PRICE_JPY |
| 구매기한 | 90일 | 등록일로부터 | DEFAULT_AVAILABLE_DAYS, timedelta(days=90) |

### 계산에 사용되는 컬럼

| 데이터 | 테이블.컬럼 | 설명 |
|--------|-------------|------|
| 바이마 최저가 (엔) | ace_products.buyma_lowest_price | 바이마에서 검색한 최저가 |
| 구매가 (원) | ace_products.source_sales_price / purchase_price_krw | 오케이몰 판매가 |
| 예상 배송비 (원) | buyma_master_categories_data.expected_shipping_fee | 카테고리별 배송비 |
| 마진율 (%) | ace_products.margin_rate | 계산된 마진율 (저장) |
| 마진 계산 시점 | ace_products.margin_calculated_at | 마진 계산 시점 |

### 계산 공식

```
1. 바이마 판매가 (원) = 바이마 최저가 (엔) x 환율(9.2)
2. 판매수수료 (원)   = 바이마 판매가 (원) x 5.5%
3. 실수령액 (원)     = 바이마 판매가 (원) - 판매수수료
4. 총 원가 (원)      = 구매가 + 예상 배송비
5. 마진 (환급X)      = 실수령액 - 총 원가
6. 부가세 환급액     = 구매가 / 11
7. 마진 (환급포함)   = 마진 (환급X) + 부가세 환급액
8. 마진율 (%)        = 마진 (환급포함) / 바이마 판매가 (원) x 100
```

### 계산 예시

```
[입력값]
- 바이마 최저가: ¥26,677
- 구매가: ₩190,180
- 예상 배송비: ₩14,900

[계산]
1. 바이마 판매가 (원) = 26,677 x 9.2 = ₩245,428
2. 판매수수료 = 245,428 x 5.5% = ₩13,499
3. 실수령액 = 245,428 - 13,499 = ₩231,929
4. 총 원가 = 190,180 + 14,900 = ₩205,080
5. 마진 (환급X) = 231,929 - 205,080 = ₩26,849
6. 부가세 환급액 = 190,180 / 11 = ₩17,289
7. 마진 (환급포함) = 26,849 + 17,289 = ₩44,138
8. 마진율 = 44,138 / 245,428 x 100 = 17.98%
```

### 저장 정책

| 구분 | 저장 여부 | 이유 |
|------|----------|------|
| margin_rate | 저장 (ace_products) | 등록 대상 WHERE절 필터링용 |
| margin_calculated_at | 저장 (ace_products) | 계산 시점 추적 |
| 마진 금액 (원) | 실시간 계산 | 필요시 calculate_margin() 호출 |
| 부가세환급액 | 실시간 계산 | 필요시 calculate_margin() 호출 |
| 판매수수료 | 실시간 계산 | 필요시 calculate_margin() 호출 |

### 등록 대상 조건

```sql
WHERE margin_rate >= 10.0          -- 최소 마진율 10%
  AND buyma_lowest_price IS NOT NULL
  AND buyma_lowest_price > 0
```

### 경쟁자 없을 때 가격 설정 (마진 20% 역산)

> buyma_lowest_price_collector.py — `calculate_target_price_jpy()`

바이마 검색 결과에 경쟁자가 없는 경우 ("검색 결과 없음" 또는 "경쟁자 없음 (내 상품/중고만 존재)"),
마진율 20%가 되는 바이마 판매가(엔)를 매입가 기준으로 역산하여 `price`에 세팅한다.

**역산 공식:**

```
buyma_price_krw = (총원가 - 부가세환급) / (1 - 수수료율 - 목표마진율)
                = (매입가 + 배송비 - 매입가/11) / 0.745
buyma_price_jpy = buyma_price_krw / 9.2
```

**예시:**

```
매입가: ₩974,000 / 배송비: ₩15,000
→ (974,000 + 15,000 - 88,545) / 0.745 / 9.2 = ¥131,376
→ 이 가격으로 마진율 검증: 20.0%
```

**DB 저장값:**

| 컬럼 | 값 |
|------|-----|
| buyma_lowest_price | NULL (경쟁자 없음) |
| price | 역산된 판매가 (엔) |
| is_lowest_price | 1 (경쟁자 없음 = 최저가) |
| margin_rate | ~20.00% |
| margin_amount_krw | 계산된 마진액 |

**주의:** 역산가가 `reference_price`(오케이몰 정가의 엔화 환산)보다 높을 수 있으나,
`buyma_new_product_register.py`(655행)에서 `reference_price > price`일 때만 API에 전송하므로 문제없음.

### 마진 계산 시점

| 시점 | 설명 |
|------|------|
| 최저가 수집 후 | buyma_lowest_price 업데이트 시 마진율 재계산 (buyma_lowest_price_collector.py) |
| 재고/가격 동기화 시 | 오케이몰 가격 변동 감지 시 재계산 (stock_price_synchronizer.py) |

---

## 외부 사이트 수집 스펙

### OkMall 수집

#### 브랜드별 상품 목록

```
URL: https://www.okmall.com/products/list?brand={브랜드명(URL인코딩)}
페이지네이션: 최대 100페이지 자동 순회
상품 선택자: .item_box[data-productno]
흠집특가 제외: item_scratch 클래스 필터링
```

#### 상품 상세 페이지

```
URL: https://www.okmall.com/products/view?no={product_no}
가격 추출: ld+json (@type=Product > offers > price/lowPrice)
정가 추출: .value_price .price
```

#### 옵션/재고 (ProductOPTList)

```
선택자: #ProductOPTList tbody tr[name="selectOption"]
색상: 1번째 td
사이즈: 2번째 td (.size_notice 제거)
옵션코드: sinfo 속성의 마지막 | 이후
```

**재고 판정 로직:**
- 행 텍스트에 '품절' 포함 & '품절 임박' 미포함 → `out_of_stock`
- ld+json `offers.offers[].availability`에 `OutOfStock` → `out_of_stock`
- 그 외 → `in_stock`
- '품절 임박'은 재고 있음으로 처리 (실제 구매 가능)

#### 봇 감지 방지 (okmall_all_brands_collector.py, stock_price_synchronizer.py 공통)

| 설정 | collector | synchronizer | 설명 |
|------|-----------|-------------|------|
| SESSION_REFRESH_INTERVAL | 30 | 30 | 요청마다 세션 교체 + 메인 페이지 방문 |
| MAX_CONSECUTIVE_TIMEOUTS | 5 | 5 | 연속 타임아웃 시 차단 판단 → 자동 중단 |
| 브라우저 프로필 | 5종 | 5종 | Chrome/Firefox/Edge 로테이션 |
| 요청 딜레이 | 1.2~2.2초 | 1.5~2.5초 | 랜덤 딜레이 |

---

### W컨셉 이미지 수집

> image_collector_parallel.py (Playwright 기반)

#### 검색

```
URL: https://display.wconcept.co.kr/search?keyword={model_no}&type=direct
선택자: .product-list:not(.area-rec-prd-list) .product-item
```

> `.area-rec-prd-list`를 제외해야 추천 상품이 섞이지 않음 (이슈 해결 기록 참조)

#### 상품 상세 → 이미지 추출

```
URL: https://www.wconcept.co.kr/Product/{product_id}
선택자: #gallery li a[data-zoom-image]
이미지 URL: data-zoom-image 속성값
```

**gallery HTML 구조:**
```html
<div class="gallery_wrap">
  <ul id="gallery">
    <li>
      <a href="#"
         data-image="//product-image.wconcept.co.kr/.../img0/..."
         data-zoom-image="//product-image.wconcept.co.kr/.../img9/...">
        <img src="..." width="60" height="80">
      </a>
    </li>
    ...
  </ul>
</div>
```

- `data-image`: 중간 해상도
- `data-zoom-image`: 고해상도 (수집 대상)

#### Cloudflare R2 이미지 저장

- 수집된 W컨셉 이미지를 다운로드 → Cloudflare R2 업로드 → 퍼블릭 URL 확보
- `ace_product_images.cloudflare_image_url`에 저장
- 바이마 API images 필드에 이 URL 사용

---

### 바이마 최저가 수집

> buyma_lowest_price_collector.py, stock_price_synchronizer.py

```
검색 URL: https://www.buyma.com/r/-O3/{model_no(URL인코딩)}/
정렬: -O3 = 가격 낮은 순
```

**파싱:**
- 상품 목록: `li.product`
- 중고 상품 제외: `span.product_used_tag` 있으면 스킵
- 가격: `span.Price_Txt` (첫 번째 = 최저가)
- 판매자 ID: `.product_Buyer a` → `href="/buyer/{id}.html"` 에서 추출
- 내 상품(BUYMA_BUYER_ID)은 제외하고 경쟁자 최저가만 반환

**경쟁자 없을 때 (양쪽 파일 공통):**
- 매입가 기반 마진 20% 가격 역산하여 `price` 설정
- `buyma_lowest_price = NULL`, `is_lowest_price = 1`

---

## 이슈 해결 기록

### W컨셉 검색 결과 1개일 때 추천 상품까지 수집되는 문제 (2026-01-27)

**문제:** 검색 결과가 1개인 경우 "이 상품을 찾으셨나요?" 추천 상품까지 수집됨

**원인:** W컨셉 HTML 구조상 추천 영역도 동일한 `.product-list` 클래스를 사용

```html
<!-- 실제 검색 결과 -->
<div class="product-list">
    <div class="items-grid list">
        <div class="product-item item type-all">...</div>
    </div>
</div>

<!-- 추천 상품 (제외 대상) -->
<div class="product-list area-rec-prd-list">
    <div class="items-grid list">
        <div class="product-item item type-simple">...</div>
    </div>
</div>
```

**핵심 차이:** 추천 영역에는 `area-rec-prd-list` 클래스가 추가됨

**해결:** `.product-list:not(.area-rec-prd-list)` 셀렉터로 추천 영역 제외

> 코드: image_collector_parallel.py:376

### Webhook 서버 (server.py) 배포 방법 (2026-02-26)

**서버 정보:**
- 위치: `ubuntu@ip-172-31-35-212:~/buyma/buyma/webhook/server.py`
- 서비스: `buyma-api.service` (systemd + gunicorn, worker 2개)
- 포트: `127.0.0.1:8000`

**일반적인 배포 절차:**
```bash
# 1. 파일 수정
nano ~/buyma/buyma/webhook/server.py

# 2. 서비스 재시작
sudo systemctl restart buyma-api

# 3. 정상 동작 확인
sudo systemctl status buyma-api
```

**포트 충돌 시 (Address already in use):**

이전 gunicorn 프로세스가 좀비로 남아 포트를 점유하는 경우 발생한다.
`Restart=always` 설정 때문에 kill해도 계속 살아나므로, 반드시 서비스를 먼저 멈춘 후 프로세스를 죽여야 한다.

```bash
# 1. 서비스 비활성화 + 중지 (auto-restart 방지)
sudo systemctl disable buyma-api
sudo systemctl stop buyma-api

# 2. 남은 gunicorn 전부 강제 종료
sudo pkill -9 -f gunicorn

# 3. 포트 해제 대기 + 확인
sleep 3
sudo lsof -i :8000    # 출력 없으면 OK

# 4. 서비스 재활성화 + 시작
sudo systemctl enable buyma-api
sudo systemctl start buyma-api
sudo systemctl status buyma-api
```

> **주의:** `nohup python server.py &`로 직접 실행하면 systemd와 충돌하므로 반드시 `systemctl`로만 관리할 것.
> **주의:** `sudo kill <PID>`만 하면 `Restart=always`에 의해 즉시 재생성된다. 반드시 `disable` + `stop` 먼저.

---

## DB 테이블 DDL

> `ace_tables_create.sql`에 포함된 테이블은 해당 파일 참조.
> 아래는 ace_tables_create.sql에 없거나 별도 관리되는 테이블입니다.

### mall_brands (브랜드 매핑)

```sql
CREATE TABLE `mall_brands` (
    `mall_name` MEDIUMTEXT NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `mall_brand_name_ko` MEDIUMTEXT NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `mall_brand_name_en` MEDIUMTEXT NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `buyma_brand_id` DOUBLE NULL DEFAULT NULL,
    `buyma_brand_name` MEDIUMTEXT NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `mapping_level` BIGINT(20) NULL DEFAULT NULL,
    `is_mapped` TINYINT(1) NULL DEFAULT NULL,
    `mall_brand_url` VARCHAR(200) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `is_active` TINYINT(1) NULL DEFAULT '1'
)
COLLATE='utf8mb4_unicode_ci'
ENGINE=InnoDB
;
```

### mall_categories (카테고리 매핑)

```sql
CREATE TABLE `mall_categories` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `mall_name` VARCHAR(50) NULL DEFAULT 'okmall' COLLATE 'utf8mb4_unicode_ci',
    `category_id` VARCHAR(50) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `gender` VARCHAR(20) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `depth1` VARCHAR(100) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `depth2` VARCHAR(100) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `depth3` VARCHAR(100) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `full_path` VARCHAR(255) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
    `buyma_category_id` INT(11) NULL DEFAULT NULL,
    `is_active` TINYINT(1) NULL DEFAULT '1',
    `created_at` TIMESTAMP NOT NULL DEFAULT current_timestamp(),
    PRIMARY KEY (`id`) USING BTREE,
    UNIQUE INDEX `mall_name` (`mall_name`, `full_path`) USING BTREE
)
COLLATE='utf8mb4_unicode_ci'
ENGINE=InnoDB
;
```

### buyma_master_categories_data (카테고리별 배송비)

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

**배송비 JOIN 쿼리:**
```sql
SELECT
    ap.id,
    ap.buyma_lowest_price,
    ap.purchase_price_krw,
    COALESCE(bmc.expected_shipping_fee, 15000) as shipping_fee
FROM ace_products ap
LEFT JOIN buyma_master_categories_data bmc
    ON ap.category_id = bmc.buyma_category_id
WHERE ap.id = :product_id;
```

### buyma_tokens (API 토큰 관리)

> 현재 코드에서는 `.env`의 `BUYMA_ACCESS_TOKEN`을 직접 사용하며, 이 테이블을 참조하지 않음.
> 토큰 자동 갱신 기능 구현 시 사용 예정.

```sql
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
```

---

**최종 수정일: 2026-03-03**

**최종 수정일: 2026-02-26**
