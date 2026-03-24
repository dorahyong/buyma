# 브랜드 목록 Headers 

## General
Request URL
https://shop-api-secondary.shopby.co.kr/products/search?categoryNos=&pageSize=100&pageNumber=1&filter.keywords=&order.by=MD_RECOMMEND&order.direction=ASC&brandNos=40331479&filter.saleStatus=ALL_CONDITIONS&order.soldoutPlaceEnd=true&filter.soldout=true&onlySaleProduct=&excludeCategoryNos=1095823
Request Method
GET
Status Code
200 OK
Remote Address
103.87.116.149:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers 
access-control-allow-origin
https://www.kasina.co.kr
access-control-expose-headers
Date
cache-control
max-age=1, public
content-length
401026
content-type
application/json
date
Thu, 05 Mar 2026 06:16:05 GMT
vary
Origin,Access-Control-Request-Method,Access-Control-Request-Headers

## Request Headers
:authority
shop-api-secondary.shopby.co.kr
:method
GET
:path
/products/search?categoryNos=&pageSize=100&pageNumber=1&filter.keywords=&order.by=MD_RECOMMEND&order.direction=ASC&brandNos=40331479&filter.saleStatus=ALL_CONDITIONS&order.soldoutPlaceEnd=true&filter.soldout=true&onlySaleProduct=&excludeCategoryNos=1095823
:scheme
https
accept
*/*
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
clientid
183SVEgDg5nHbILW//3jvg==
company
Kasina/Request
content-type
application/json
origin
https://www.kasina.co.kr
platform
PC
priority
u=1, i
referer
https://www.kasina.co.kr/
sec-ch-ua
"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"Windows"
sec-fetch-dest
empty
sec-fetch-mode
cors
sec-fetch-site
cross-site
user-agent
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36
version
1.0

## Payload
categoryNos=&pageSize=100&pageNumber=1&filter.keywords=&order.by=MD_RECOMMEND&order.direction=ASC&brandNos=40331479&filter.saleStatus=ALL_CONDITIONS&order.soldoutPlaceEnd=true&filter.soldout=true&onlySaleProduct=&excludeCategoryNos=1095823

## Response
kasina_nike_list_response.json

# 상품 상세 페이지 Headers

## General
Request URL
https://shop-api-secondary.shopby.co.kr/products/132892414?useCache=false
Request Method
GET
Status Code
200 OK
Remote Address
103.87.116.149:443
Referrer Policy
strict-origin-when-cross-origin

## Response Headers
access-control-allow-origin
https://www.kasina.co.kr
access-control-expose-headers
Date
cache-control
max-age=1, public
content-length
16192
content-type
application/json
date
Thu, 05 Mar 2026 06:33:28 GMT
vary
Origin,Access-Control-Request-Method,Access-Control-Request-Headers

## Request Headers
:authority
shop-api-secondary.shopby.co.kr
:method
GET
:path
/products/132892414?useCache=false
:scheme
https
accept
*/*
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
clientid
183SVEgDg5nHbILW//3jvg==
company
Kasina/Request
origin
https://www.kasina.co.kr
platform
PC
priority
u=1, i
referer
https://www.kasina.co.kr/
sec-ch-ua
"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"Windows"
sec-fetch-dest
empty
sec-fetch-mode
cors
sec-fetch-site
cross-site
user-agent
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36
version
1.0

## Payload
useCache=false

## Response
kasina_product_detail_132892414.json


# 상품 상세페이지 들어가면 옵션 api 

## General
Request URL
https://shop-api.e-ncp.com/products/132892414/options?useCache=false
Request Method
GET
Status Code
200 OK
Remote Address
115.89.203.159:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers
access-control-allow-origin
https://www.kasina.co.kr
access-control-expose-headers
Date
cache-control
max-age=1, public
content-length
8332
content-type
application/json
date
Thu, 05 Mar 2026 06:51:53 GMT
vary
Origin,Access-Control-Request-Method,Access-Control-Request-Headers

## Request Headers
:authority
shop-api.e-ncp.com
:method
GET
:path
/products/132892414/options?useCache=false
:scheme
https
accept
*/*
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
clientid
183SVEgDg5nHbILW//3jvg==
company
Kasina/Request
origin
https://www.kasina.co.kr
platform
PC
priority
u=1, i
referer
https://www.kasina.co.kr/
sec-ch-ua
"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"Windows"
sec-fetch-dest
empty
sec-fetch-mode
cors
sec-fetch-site
cross-site
user-agent
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36
version
1.0

## Response
kasina_product_detail_options_132892414.json


# URL 구조

## 목록 페이지
- https://www.kasina.co.kr/brands/nike
- https://www.kasina.co.kr/brands/nike?page=2
- ...
- https://www.kasina.co.kr/brands/nike?page=7

## 상품 상세 페이지
- https://www.kasina.co.kr/product-detail/{productNo}
- 예: https://www.kasina.co.kr/product-detail/132892414


# 수집기 구현 결과 (2026-03-05)

## 스크립트
- `kasina/kasina_collector.py`
- 실행: `python kasina_collector.py [--brand NIKE] [--limit N] [--dry-run] [--skip-existing]`

## API 호스트 구분
| 용도 | 호스트 |
|------|--------|
| 리스트 + 상세 | `shop-api-secondary.shopby.co.kr` |
| 옵션(사이즈/재고) | `shop-api.e-ncp.com` |

## 인증 헤더 (공통)
- `clientId: 183SVEgDg5nHbILW//3jvg==`
- `company: Kasina/Request`
- `platform: PC`
- `version: 1.0`

## 브랜드별 API 파라미터
| 브랜드 | brandNos | excludeCategoryNos | 비고 |
|--------|----------|-------------------|------|
| NIKE | 40331479 | 1095823 | excludeCategoryNos 없으면 다른 브랜드 섞임 |

## 수집 흐름
1. 리스트 API (`/products/search`) → pageSize=100 페이지네이션
2. --skip-existing: ace_products에 등록완료(is_published=1) 상품 스킵
3. 상세 API (`/products/{productNo}`) → category_path 추출 (첫 번째 카테고리 사용)
4. 옵션 API (`/products/{productNo}/options`) → 사이즈/재고 (flatOptions)
5. DB 저장: INSERT ON DUPLICATE KEY UPDATE (10개 배치)

## API → raw_scraped_data 필드 매핑
| API 필드 | raw 컬럼 | 비고 |
|----------|---------|------|
| `productNo` | `mall_product_id` | |
| `productManagementCd` | `model_id` | 브랜드 스타일코드 (없으면 스킵) |
| `brandName` | `brand_name_en` | |
| `brandNameKo` | `brand_name_kr` | |
| `productName` | `product_name` | 영문 상품명 |
| `productNameEn` | `p_name_full` | 한글 상품명 (필드명 반대) |
| `salePrice` | `original_price` | 정가 |
| `salePrice - immediateDiscountAmt` | `raw_price` | 할인가 |
| `saleStatusType` + 옵션 saleType | `stock_status` | 옵션 기준 보정 |
| 상세 API categories[0].fullCategoryLabel | `category_path` | |

## 옵션 파싱 (flatOptions)
- `value`: `"색상|사이즈"` 형식 (예: `"STADIUM GREEN/BRT CRIMSON|250"`)
- `saleType`: `AVAILABLE` → in_stock, `SOLDOUT` → out_of_stock
- `stockCnt`: -999 = 무한재고(재고차감 안함), 0 = 품절

## 수집 결과 (NIKE, 2026-03-05)
- 총 661개 (7페이지)
- model_id 없는 상품: 0개
- in_stock: 327개, out_of_stock: 334개
- saleStatusType은 전부 ONSALE이지만 옵션 레벨에서 전 사이즈 SOLDOUT인 경우 있음
- 할인 적용 상품: 455개
- okmall과 model_id 중복: 6개 (전부 카시나가 더 저렴하나 5개는 카시나에서 전체 품절)

## 주의사항
- 리스트 API에 `excludeCategoryNos=1095823` 필수 (없으면 SALOMON, JORDAN 등 다른 브랜드 섞임 → 11,889개)
- 카시나는 완전 품절 상품도 리스트에 포함 (saleStatusType=ONSALE이지만 옵션 전부 SOLDOUT)
- delay 0.3초로도 차단 없었음 (API 호출이라 안전)
