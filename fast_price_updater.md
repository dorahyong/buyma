# 빠른 최저가 업데이트 (fast_price_updater.py)

## 배경
- `run_daily.py` / `run_daily_multisource.py`의 full sync는 source mall 접근 포함하여 전체 순회에 시간이 오래 걸림
- 그 사이 타 쇼퍼가 더 낮은 가격으로 등록하면 최대 24시간 동안 최저가를 빼앗김
- source mall 접근 없이 **바이마 + DB만으로** 빠르게 최저가를 재확보하는 스크립트 필요

## 핵심 원칙
- **source mall 접근 없음** — 바이마 최저가 조회 + DB의 기존 데이터(`purchase_price_krw`, `expected_shipping_fee`)만 사용
- **가격 인하 전용** — 현재보다 가격을 낮춰서 최저가를 확보하는 것이 목적
- **품절/가격 상승은 무시** — source의 실시간 상태 변동은 1일 1회 full sync가 처리

## 처리 대상
```sql
SELECT ap.id, ap.buyma_product_id, ap.reference_number, ap.model_no,
       ap.brand_name, ap.category_id, ap.price,
       ap.purchase_price_krw, ap.expected_shipping_fee,
       ap.buyma_lowest_price, ap.is_lowest_price
FROM ace_products ap
WHERE ap.is_published = 1
  AND ap.buyma_product_id IS NOT NULL
  AND ap.is_active = 1
```
- 전체 source_site 대상 (okmall, kasina, nextzennpack, labellusso, trendmecca)
- `source_product_url` 불필요 (source mall 접근 안 하므로)

## 처리 흐름

### 1. 바이마 최저가 조회
- `buyma_lowest_price_collector.py`와 동일한 방식으로 바이마 검색
- `https://www.buyma.com/r/-O3/{model_no}/` 크롤링
- 내 상품 가격(my_price)과 최저가(lowest_price) 추출

### 2. 분기 처리

```
[내가 최저가] → 스킵 (변경 불필요)

[내가 최저가 아님]
  └─ DB의 purchase_price_krw + shipping_fee 기준으로 마진 계산
     ├─ 최저가 - 1엔 으로 판매 시 마진 + → 가격 인하 (바이마 API 수정)
     └─ 최저가 - 1엔 으로 판매 시 마진 - → 바이마 API 삭제
```

### 3. 마진 계산
- `stock_price_synchronizer.py`의 `calculate_margin()` 과 동일한 로직 사용
  - 환율: 9.2 (원/엔)
  - 바이마 수수료: 5.5%
  - 부가세 환급: purchase_price_krw / 11
  - 배송비: `ace_products.expected_shipping_fee` (DB 저장값)
- 타겟 가격: `lowest_price - 1`엔 (또는 `lowest_price - 3`엔 — 기존 sync 패턴 확인 필요)

### 4. 바이마 API 호출
- **가격 인하**: shopper API로 price 수정 (control: "update")
- **삭제**: 마진 마이너스 시 shopper API로 삭제 (control: "delete") + DB is_active=0 처리

### 5. DB 업데이트
- 가격 인하 시: `price`, `buyma_lowest_price`, `is_lowest_price`, `margin_rate_percent`, `margin_amount_krw`, `updated_at` 업데이트
- 삭제 시: `is_active=0`, `is_published=0`, `status='deleted'` 처리 (기존 sync와 동일)

## CLI 인터페이스
```bash
python fast_price_updater.py                      # 전체 실행
python fast_price_updater.py --dry-run             # 변경 대상만 확인
python fast_price_updater.py --brand NIKE           # 특정 브랜드만
python fast_price_updater.py --source okmall        # 특정 소스만
python fast_price_updater.py --limit 100            # 최대 N건
python fast_price_updater.py --count                # 건수만 확인
```

## full sync와의 차이점

| 항목 | full sync (stock_price_synchronizer) | fast_price_updater |
|------|--------------------------------------|--------------------|
| source mall 접근 | O (실시간 가격/재고 확인) | X |
| 매입가 기준 | source에서 실시간 조회 | DB의 기존 purchase_price_krw |
| 재고 변동 처리 | O (품절/재입고 반영) | X |
| 가격 인상 | O (경쟁자 없으면 20% 마진 재계산) | X (인하만) |
| 삭제 조건 | 품절/마진부족/흠집 | 마진 마이너스만 |
| 실행 빈도 | 1일 1회 | 수시 (1일 여러 회) |
| 소요 시간 | 수 시간 | 바이마 크롤링 시간만 (source 접근 없으므로 빠름) |

## 참고 파일
- 바이마 최저가 크롤링: `okmall/buyma_lowest_price_collector.py`
- 마진 계산 로직: `okmall/stock_price_synchronizer.py` → `calculate_margin()`
- 바이마 API 호출: `okmall/stock_price_synchronizer.py` → `build_buyma_request()`
- 배송비 조회: `ace_products.expected_shipping_fee` 컬럼 (카테고리별 배송비)

## 주의사항
- source의 실시간 품절/가격변동은 반영하지 않음 → full sync가 보정
- DB의 purchase_price_krw가 오래된 데이터일 수 있음 (source 가격이 올랐을 수 있음)
  - 하지만 이 경우 full sync 때 자연스럽게 보정됨
- 가격 인하만 수행하므로, 경쟁자가 사라진 경우(가격 올릴 수 있는 기회)는 full sync에서 처리
