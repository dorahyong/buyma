# 오케이몰 일시품절 상품 품절 미감지 이슈

## 날짜
2026-03-27

## 문제 현상
오케이몰에서 일시품절된 상품이 바이마에서 삭제되지 않고 남아있었음.

## 원인

### synchronizer (stock_price_synchronizer.py)
오케이몰의 일시품절 상품 페이지는 사이즈 선택 옵션 테이블이 아예 표시되지 않음.

synchronizer가 이 페이지를 방문하면:
1. 옵션 테이블이 없으니 옵션이 비어있음
2. "옵션이 없는 단일 상품"으로 판단하여 폴백 로직 진입
3. 폴백 로직에서 JSON-LD의 최상위 `offers.availability`를 확인하는데, 오케이몰은 `AggregateOffer` 타입이라 최상위에는 availability가 없음 (개별 offer 안에 있음)
4. 빈 문자열 → "재고 있음"으로 잘못 판정 → 바이마에서 삭제 안 됨

추가로, 일시품절 페이지는 가격(lowPrice) 값이 None이어서 `int(None)` 에러가 발생. 품절 감지 로직까지 도달하기 전에 "수집처에서 상품 삭제됨"으로 잘못 처리되었음.

### collector (okmall_all_brands_collector.py)
오케이몰 브랜드 목록 페이지에서 일시품절 상품이 노출되지 않아 재수집 자체가 불가능. (미수정 — synchronizer가 담당하는 영역)

## 수정 내용 (stock_price_synchronizer.py)

### 1. 가격 파싱 에러 방지 (620, 622행)
```python
# before
int(offers.get('lowPrice', 0))   # None이면 int(None) 에러
# after
int(offers.get('lowPrice') or 0)  # None이면 0으로 대체
```

### 2. 일시품절 품절 감지 (679~702행)
옵션 테이블이 비어있을 때, JSON-LD가 AggregateOffer이면 개별 offer들의 availability를 확인하도록 수정.

```python
# before: 최상위 availability만 확인 (AggregateOffer면 빈 값)
availability = offers.get('availability', '')
status = 'out_of_stock' if 'OutOfStock' in availability else 'in_stock'

# after: AggregateOffer이면 개별 offer 확인
if offers.get('@type') == 'AggregateOffer':
    offer_list = offers.get('offers', [])
    if offer_list and all('OutOfStock' in o.get('availability', '') for o in offer_list):
        status = 'out_of_stock'
    else:
        status = 'in_stock'
else:
    availability = offers.get('availability', '')
    status = 'out_of_stock' if 'OutOfStock' in availability else 'in_stock'
```

## 수정 후 동작
일시품절 상품 방문 시:
1. 가격 파싱 에러 없이 통과
2. 옵션 테이블 없음 → AggregateOffer 개별 offer 확인 → 전부 OutOfStock → 품절 판정
3. DB variants 전체 out_of_stock 업데이트
4. 전체 품절 → 바이마 삭제 요청

## 영향 범위
- 기존 정상 상품(옵션 테이블 있는 경우): 수정 블록에 진입하지 않으므로 영향 없음
- 기존 단일 상품(non-AggregateOffer): else 분기로 기존과 동일하게 동작
- 일시품절 상품(AggregateOffer, 옵션 테이블 없음): 품절로 정확히 감지됨 (수정됨)

## 커밋
`95bf020` — 오케이몰 일시품절 상품 품절 감지 로직 수정
