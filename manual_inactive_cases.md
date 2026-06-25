# 수동 비활성화(is_active=0) 케이스 정리

## 목적
자동 파이프라인(fast_price_updater / stock_price_synchronizer)이 **"최저가 확보 불가"로 삭제하지 못하도록** 사람이 직접 `is_active=0`으로 빼둔 상품을 추적한다.
바이마에는 **계속 출품 유지**(`is_published=1`)하고, **가격은 사람이 수동으로 관리**한다.

## 왜 비활성화하나
- 우리 수집처 DB 가격으로는 바이마 최저가 확보 불가 → 자동 로직은 "마진 마이너스"로 판단해 삭제하려 함
- 하지만 **수집처가 아닌 다른 몰(11번가, 옥션, 기타)** 에서는 최저가 매입이 가능 → 실제로는 팔 수 있는 상품
- 따라서 자동 삭제를 막기 위해 `is_active=0`으로 파이프라인에서 제외, 출품은 유지

## 규칙
- `is_active = 0` (자동 파이프라인 제외)
- `is_published = 1` (바이마 출품 유지)
- `exception_reason = 'manual'` (수동 처리 표시 — 일괄 복구 시 자동 보호됨)
- 가격: **사람이 수동으로 바이마에서 직접 수정**
- 매입처: 수집처 DB가 아닌 외부 몰(아래 "외부 매입처" 기록)

> 참고: 오늘(2026-06-09) fast 삭제분 일괄 복구는 `exception_reason IS NULL` 기준이라, `exception_reason='manual'`인 이 케이스들은 복구 대상에서 자동 제외됨.

---

## 케이스 목록

### 1. MM6 Maison Margiela × Salomon XT-15 백팩
| 항목 | 값 |
|------|-----|
| 등록일(기록) | 2026-06-09 |
| ace_products.id | 186920 |
| buyma_product_id | 132310779 |
| reference_number | 8a0b0ba2-7776-42df-b422-9c79a61e3835 |
| brand | MM6 Maison Margiela(エムエムシックス) |
| model_no | SB5WA0013 P5782 HB342 LC3063000 |
| 상품명 | MM6 マルジェラ X サロモン ロゴ XT-15 バックパック |
| source_site(수집처) | thefactor2 |
| source_url | https://smartstore.naver.com/thefactor2/products/13526742572 |
| 현재 바이마 가격(price) | ¥45,825 |
| 수집처 매입가(purchase_price_krw) | ₩398,000 |
| is_active / is_published | 0 / 1 |
| exception_reason | manual |
| **사유** | 수집처(thefactor2) 가격으론 최저가 확보 불가. **다른 몰에서 최저가 매입 가능**하여 출품 유지 + 수동 가격관리 |
| 외부 매입처 | (기입 필요 — 예: 11번가/옥션 URL·가격) |
| 비고 | - |
