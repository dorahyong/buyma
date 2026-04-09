# buyma_suspended_cleaner.py 기획

## 목적
바이마 '출품정지중' / '비승인' 상태의 상품을 크롤링하여 DB(ace_products)에서 비활성화 처리

## 대상 페이지
| 상태 | URL | status 파라미터 |
|------|-----|-----------------|
| 출품정지중 | https://www.buyma.com/my/sell/?status=suspended&tab=b#/ | `suspended` |
| 비승인 | https://www.buyma.com/my/sell/?status=not_approved&tab=b#/ | `not_approved` |

## 처리 로직

### Phase 1: 크롤링
- `buyma_orphan_cleaner.py`와 동일한 방식 (쿠키 세션 + BeautifulSoup)
- 출품정지 페이지, 비승인 페이지를 각각 페이지네이션하며 `buyma_product_id` 수집
- HTML 셀렉터: `input[name="chkitems"]` → value가 buyma_product_id

### Phase 2: DB 매칭
- 수집된 buyma_product_id로 ace_products 조회
- DB에 매칭되는 행 추출

### Phase 3: DB 업데이트
- 매칭된 행에 대해:
  ```sql
  UPDATE ace_products
  SET status = '{출품정지 or 비승인}',  -- 출처에 따라 구분
      is_active = 0,
      updated_at = NOW()
  WHERE buyma_product_id = %s
  ```
- **바이마 API 호출 없음** (이미 바이마 측에서 정지/비승인 처리된 상태)

## status 값
- 출품정지중 → `'suspended'`
- 비승인 → `'not_approved'`

## CLI 인터페이스
```
python buyma_suspended_cleaner.py --login          # 쿠키 갱신 (orphan_cleaner 공유)
python buyma_suspended_cleaner.py --scan            # 크롤링 + DB 매칭 결과 출력
python buyma_suspended_cleaner.py --scan --dry-run  # 업데이트 대상만 확인
python buyma_suspended_cleaner.py --scan --apply    # 실제 DB 업데이트 실행
python buyma_suspended_cleaner.py --count           # 건수만 확인
```

## 참고 패턴
- 쿠키/세션: `buyma_orphan_cleaner.py`의 `create_session()`, `login_and_save_cookies()`
- DB 연결: `buyma_unpublished_cleaner.py`의 DB_CONFIG 패턴
- 크롤링: `buyma_orphan_cleaner.py`의 `crawl_buyma_product_ids()` — URL 템플릿의 `status` 파라미터만 변경

## 크롤링 URL 템플릿
```
https://www.buyma.com/my/sell?status=suspended&order=desc&page={page}&rows=100&sort=item_id
https://www.buyma.com/my/sell?status=not_approved&order=desc&page={page}&rows=100&sort=item_id
```

## 산출물 (중간 파일)
- `buyma_suspended_products.json` — 출품정지 상품 목록
- `buyma_not_approved_products.json` — 비승인 상품 목록

## 주의사항
- is_published, buyma_product_id는 변경하지 않음 (바이마에 상품 자체는 존재)
- 바이마 API 호출 없음 (DB 업데이트만)
- 추후 출품정지 해제되면 다시 is_active=1로 복구할 수 있도록 buyma_product_id 유지
