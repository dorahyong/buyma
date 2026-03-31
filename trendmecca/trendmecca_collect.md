# 트렌드메카 수집기 (trendmecca_collector.py)

## 실행 옵션
```
python trendmecca_collector.py                        # 전체 브랜드 수집
python trendmecca_collector.py --brand "AMI"          # 특정 브랜드만
python trendmecca_collector.py --limit 10             # 브랜드당 최대 N개
python trendmecca_collector.py --dry-run              # DB 저장 없이 테스트
python trendmecca_collector.py --skip-existing        # 등록 완료(is_published=1) 상품 스킵
python trendmecca_collector.py --update-categories    # 기존 데이터의 category_path만 업데이트 (수집 안 함)
```

## 수집 흐름
1. `mall_brands` (mall_name='trendmecca', is_active=1)에서 브랜드 목록 조회
2. `mall_categories` (mall_name='trendmecca')에서 카테고리 맵 구축 — 각 카테고리 페이지 순회하여 {product_no: full_path} 매핑
3. 브랜드별 리스트 페이지 순회: `/product/list.html?cate_no={mall_brand_no}&page={n}`
4. 각 상품 상세 페이지에서 model_id, 옵션(사이즈/재고), 이미지, 가격 추출
5. `raw_scraped_data`에 UPSERT (`ON DUPLICATE KEY UPDATE`)
6. model_id 없는 상품은 스킵 (dead row 방지)

## 파싱 포인트
| 항목 | 리스트 페이지 | 상세 페이지 |
|------|-------------|------------|
| product_no | `li[id^="anchorBoxId_"]` | - |
| 상품명 | `div.name > a > span` (.displaynone 제거, 타임메카/트렌드메카 접미어 제거) | - |
| 가격 | `div.description` 속성 ec-data-custom(소비자가), ec-data-price(판매가) | JS 변수 `product_sale_price`, `product_price` (우선) |
| 이미지 | `div.thumbnail > a > img` | `.xans-product-addimage img.ThumbImage` |
| 모델명 | - | `tr[rel="모델명"] td > span` (fallback: th 텍스트 검색) |
| 제조국 | - | `tr[rel="제조국"] td > span` |
| 옵션/재고 | - | JS 변수 `option_stock_data` (JSON) → 사이즈/재고 (fallback: select 옵션) |
| 상품 URL | `div.name > a` href (slug 포함) | - |

## 카테고리 매핑
- `mall_categories` 테이블에서 trendmecca 카테고리 목록 조회 (category_id = cate_no, full_path = "MEN > 의류 > 상의 > 반팔티" 형태)
- 각 카테고리 리스트 페이지를 순회하여 product_no 수집 → {product_no: full_path} 매핑
- 첫 매칭 우선 (한 상품이 여러 카테고리에 있을 경우)
- `--update-categories`: `category_path IS NULL OR ''`인 기존 행만 업데이트

## 봇 감지 방지
- 30개 요청마다 세션 교체 + 메인 페이지(`/index_time.html`) 재방문 (쿠키 갱신)
- 랜덤 브라우저 프로필 3종 (Chrome 120/121 Windows, Chrome 120 macOS)
- 요청 간 0.3~0.8초 랜덤 딜레이
- 타임아웃 연속 5회 시 차단 감지 → 자동 중지
- 403 응답 시 즉시 중지

## 현재 상태 (2026-03-27)
- 등록 브랜드: 197개 (전체 활성)
- A.P.C 검증 완료: 162개 전량 category_path, model_id, price, url 정상
- DB 저장: `raw_scraped_data` (source_site='trendmecca')
- 자체 이미지 보유 (`mall_sites.has_own_images=1`)
- converter: `kasina/raw_to_converter_kasina.py` 공용 (`--source-site trendmecca`)

---

# API/HTML 레퍼런스

# 인덱스 페이지

## Genaral

## Response headers

## Request Headers

## Response


# 브랜드 목록 
https://trendmecca.co.kr/brand

## General
Request URL
https://trendmecca.co.kr/brand
Request Method
GET
Status Code
200 OK
Remote Address
203.245.12.117:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers
accept-ranges
bytes
cache-control
no-store, no-cache, must-revalidate, post-check=0, pre-check=0
content-encoding
gzip
content-type
text/html; charset=utf-8
date
Thu, 26 Mar 2026 05:12:40 GMT
expires
Mon, 26 Jul 1997 05:00:00 GMT
last-modified
Thu, 26 Mar 2026 05:12:40 GMT
p3p
CP="NOI ADM DEV PSAi COM NAV OUR OTR STP IND DEM"
pragma
no-cache
server
openresty
vary
Accept-Encoding
x-anigif
webp
x-cache
MISS
x-cache-valid
YES
x-hits
0
x-hrpcs-signal
1
x-hrpcs-ttl
300s
x-hurl
/brandsineorb01view_pcKRwebpagent_pc
x-iscacheurl
YES
x-reqid
431bb61522957d82d996ba3e3e59032e
x-ttl
300.000
x-via
magneto-edge-icn02-ktog-132
x-xss-protection
1;mode=block

## Request Headers
:authority
trendmecca.co.kr
:method
GET
:path
/brand
:scheme
https
accept
text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
cache-control
max-age=0
cookie
_fwb=14562wVLpzWD31iJHrDkkI0.1773108253192; siteLT=1feab9f8-ff9a-adb6-3fc4-db2d40e6d2f5; analytics_longterm=analytics_longterm.sineorb0_1.7AE6110.1773108253615; CVID_Y=CVID_Y.425b5a51574752096c01.1773108253615; _fcOM={"k":"4879bd6c283c9107a2f638319b01f84812-4765","i":"220.76.70.1.5084820","r":1773108254078}; timemecca.co.kr-crema_device_token=3uCgTc0nOyPzZ3xWmSXLxToPrVwHU7g9; psrui=72805635.1765852329; psrfp=eaba98d0ca9250477fcd535b933317ce; _gcl_au=1.1.534764476.1773108254; _tt_enable_cookie=1; _ttp=01KKAQSNJYHEXW22PHRM0RV1TA_.tt.2; recent_plist=113976; _wp_uid=1-885a6aeaaf2ddd7b81eb0ff7b42f0207-s1765852328.460224|windows_10|chrome-1nrfmkl; CFAE_CUK1Y=CFAE_CUK1Y.sineorb0_1.0BA4PO5.1773108256149; CUK45=cuk45_sineorb0_7qfsv43r3q48sgpbr6m2vpv9ad4jch6l; CUK2Y=cuk2y_sineorb0_7qfsv43r3q48sgpbr6m2vpv9ad4jch6l; _fbp=fb.2.1773108275072.49102848333052503; ch-veil-id=51587550-017d-404f-85a8-e742e82d70cd; fb_external_id=231da8bda81305782c940520332d950e6350fcc2529dc70feedf4cb4d8a263ed; siteSID=d7e21d21-e24e-5163-3046-93f5752c4c76; analytics_session_id=analytics_session_id.sineorb0_1.B467C0A.1774501869734; CVID=CVID.425b5a51574752096c01.1774501869734; _gid=GA1.3.383800888.1774501871; ec_ipad_device=F; CFAE_CID=CFAE_CID.sineorb0_1.G37XNKT.1774501875440; CID=CIDRef4676bda5b0ac4b5d5a80b891941d3e; CIDRef4676bda5b0ac4b5d5a80b891941d3e=60b6a3f7c7b31400a82231b1e0b8b790%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%2Findex_time.html%3A%3A1774501875%3A%3A%3A%3Appdp%3A%3A1774501875%3A%3A%3A%3A%3A%3A%3A%3A; ECSESSID=dh466g5fuppb7mmg0d4mrg32pgsha7hf; basketcount_1=0; basketprice_1=0%EC%9B%90; wish_id=1b1182c2047b8e08c49aca97010c2270; wishcount_1=0; isviewtype=pc; fb_event_id=event_id.sineorb0.1.3M2YTC1IYYCGPWKKBX3AAJU306R5FHQ8; wcs_bt=s_2bc791b9bc98:1774501931; _ga_JM9PNKBD79=GS2.1.s1774501871$o2$g1$t1774501932$j60$l0$h0; _ga_ZVXPT4FXK2=GS2.1.s1774501871$o2$g1$t1774501932$j60$l0$h0; _ga_ZE7PK22CMX=GS2.1.s1774501870$o2$g1$t1774501932$j58$l0$h1695453495; _ga=GA1.3.314827125.1773108255; _gat_gtag_UA_104415011_1=1; CFAE_LC=CFAE_LC.sineorb0_1.DI6PXQF.1774501932610; support_cookie=1; vt=1774501931; ttcsid=1774501870495::NGzN73n_Ku0AUrjA7cx8.2.1774501933040.0; ttcsid_CN1CJT3C77U2L3UB1RI0=1774501870494::LlsNvxobU1R0G-D7h4uA.2.1774501933040.1; cto_bundle=68YDgF9CVFFmWVRndDJuc0Y4MTRIcU5FcnolMkJ6c01jdXBLU2FsUW1nam5yOGxRZlRVWDFTREROM1hzZ0Q5ZVJDRiUyRlphSXg3OXl4VUpSU1RwQml4ZUJSTFhrUFR1bW5ZdjdhVzJ0a0tsMEdLVDNnMnZ0TTcxckM2NzJha1RvUmdJOU9oUmNnSk5EYlo4RXRGWE1YQjVkcTBPc2dBJTNEJTNE; _ga_KBS17W908X=GS2.1.s1774501872$o2$g1$t1774501937$j55$l0$h0; ch-session-97935=eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzZXMiLCJleHAiOjE3NzcwOTM5NTMsImlhdCI6MTc3NDUwMTk1Mywia2V5IjoiOTc5MzUtNjlhZjdjMzUwMDVmYWYyY2Q5NjQifQ.HWEKhABL6lnjtGWGw9U9EMO2k3DYfV4PWjwa1l0OksE
priority
u=0, i
referer
https://trendmecca.co.kr/index_time.html
sec-ch-ua
"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"Windows"
sec-fetch-dest
document
sec-fetch-mode
navigate
sec-fetch-site
same-origin
sec-fetch-user
?1
upgrade-insecure-requests
1
user-agent
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36

## Response
buyma\trendmecca\brand_list.html


# 브랜드별 리스트 페이지
https://trendmecca.co.kr/product/list.html?cate_no=2604
https://trendmecca.co.kr/product/list.html?cate_no=2604&page=2

## General
Request URL
https://trendmecca.co.kr/product/list.html?cate_no=2604&page=1
Request Method
GET
Status Code
200 OK
Remote Address
203.245.12.117:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers
accept-ranges
bytes
cache-control
no-store, no-cache, must-revalidate, post-check=0, pre-check=0
content-encoding
gzip
content-type
text/html; charset=utf-8
date
Thu, 26 Mar 2026 05:18:11 GMT
expires
Mon, 26 Jul 1997 05:00:00 GMT
last-modified
Thu, 26 Mar 2026 05:18:11 GMT
p3p
CP="NOI ADM DEV PSAi COM NAV OUR OTR STP IND DEM"
pragma
no-cache
server
openresty
vary
Accept-Encoding
x-anigif
webp
x-cache
MISS
x-cache-valid
YES
x-hits
0
x-hrpcs-signal
1
x-hrpcs-ttl
300s
x-hurl
/product/list.html?cate_no=2604&page=1sineorb01view_pcKRwebpagent_pc
x-iscacheurl
YES
x-reqid
bf927f85b099a23e66d250896911b383
x-ttl
300.000
x-via
magneto-edge-icn02-ktog-108
x-xss-protection
1;mode=block

## Request Headers
:authority
trendmecca.co.kr
:method
GET
:path
/product/list.html?cate_no=2604&page=1
:scheme
https
accept
text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
cache-control
max-age=0
cookie
_fwb=14562wVLpzWD31iJHrDkkI0.1773108253192; siteLT=1feab9f8-ff9a-adb6-3fc4-db2d40e6d2f5; analytics_longterm=analytics_longterm.sineorb0_1.7AE6110.1773108253615; CVID_Y=CVID_Y.425b5a51574752096c01.1773108253615; _fcOM={"k":"4879bd6c283c9107a2f638319b01f84812-4765","i":"220.76.70.1.5084820","r":1773108254078}; timemecca.co.kr-crema_device_token=3uCgTc0nOyPzZ3xWmSXLxToPrVwHU7g9; psrui=72805635.1765852329; psrfp=eaba98d0ca9250477fcd535b933317ce; _gcl_au=1.1.534764476.1773108254; _tt_enable_cookie=1; _ttp=01KKAQSNJYHEXW22PHRM0RV1TA_.tt.2; recent_plist=113976; _wp_uid=1-885a6aeaaf2ddd7b81eb0ff7b42f0207-s1765852328.460224|windows_10|chrome-1nrfmkl; CFAE_CUK1Y=CFAE_CUK1Y.sineorb0_1.0BA4PO5.1773108256149; CUK45=cuk45_sineorb0_7qfsv43r3q48sgpbr6m2vpv9ad4jch6l; CUK2Y=cuk2y_sineorb0_7qfsv43r3q48sgpbr6m2vpv9ad4jch6l; _fbp=fb.2.1773108275072.49102848333052503; ch-veil-id=51587550-017d-404f-85a8-e742e82d70cd; fb_external_id=231da8bda81305782c940520332d950e6350fcc2529dc70feedf4cb4d8a263ed; siteSID=d7e21d21-e24e-5163-3046-93f5752c4c76; analytics_session_id=analytics_session_id.sineorb0_1.B467C0A.1774501869734; CVID=CVID.425b5a51574752096c01.1774501869734; _gid=GA1.3.383800888.1774501871; ec_ipad_device=F; CFAE_CID=CFAE_CID.sineorb0_1.G37XNKT.1774501875440; CID=CIDRef4676bda5b0ac4b5d5a80b891941d3e; CIDRef4676bda5b0ac4b5d5a80b891941d3e=60b6a3f7c7b31400a82231b1e0b8b790%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%2Findex_time.html%3A%3A1774501875%3A%3A%3A%3Appdp%3A%3A1774501875%3A%3A%3A%3A%3A%3A%3A%3A; ECSESSID=dh466g5fuppb7mmg0d4mrg32pgsha7hf; basketcount_1=0; basketprice_1=0%EC%9B%90; wish_id=1b1182c2047b8e08c49aca97010c2270; wishcount_1=0; isviewtype=pc; support_cookie=1; ch-session-97935=eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzZXMiLCJleHAiOjE3NzcwOTQyMjMsImlhdCI6MTc3NDUwMjIyMywia2V5IjoiOTc5MzUtNjlhZjdjMzUwMDVmYWYyY2Q5NjQifQ.UIfVkAUyqbal4FN3g9DkpOc4fAihjgUN2NW6aXHrrEg; fb_event_id=event_id.sineorb0.1.BXQGUZBU6LHWRV2MXT8EQCMXKA6PQ8KDG; wcs_bt=s_2bc791b9bc98:1774502226; cto_bundle=gZjAdF9CVFFmWVRndDJuc0Y4MTRIcU5Fcno5YXN1UkRyYXRIZ0Q5UW11WGdIRXNZZWtPMWRjbk9XYjF2ZXRjeSUyRm9od20lMkZPRXJsSVJDJTJCbDFZOXBsb2VvSGhjSXFLRU4lMkJnRmhaMHdLeUR3MCUyRnZuelE0b28lMkZ5TU9ZZnZPTzd0Q3BXNnZtSG5aJTJGeDI3dTdyYXY2UUYlMkZUUEZrVmpRJTNEJTNE; vt=1774502226; _ga_JM9PNKBD79=GS2.1.s1774501871$o2$g1$t1774502226$j60$l0$h0; _ga_ZVXPT4FXK2=GS2.1.s1774501871$o2$g1$t1774502227$j60$l0$h0; CFAE_LC=CFAE_LC.sineorb0_1.588LNT7.1774502227037; _ga_KBS17W908X=GS2.1.s1774501872$o2$g1$t1774502227$j58$l0$h0; _ga=GA1.1.314827125.1773108255; ttcsid=1774501870495::NGzN73n_Ku0AUrjA7cx8.2.1774502227521.0; ttcsid_CN1CJT3C77U2L3UB1RI0=1774501870494::LlsNvxobU1R0G-D7h4uA.2.1774502227521.1; _ga_ZE7PK22CMX=GS2.1.s1774501870$o2$g1$t1774502286$j60$l0$h1695453495
priority
u=0, i
referer
https://trendmecca.co.kr/product/list.html?cate_no=2604&page=2
sec-ch-ua
"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"Windows"
sec-fetch-dest
document
sec-fetch-mode
navigate
sec-fetch-site
same-origin
sec-fetch-user
?1
upgrade-insecure-requests
1
user-agent
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36

## Payload
cate_no=2604&page=1

## Response
buyma\trendmecca\list.html


# 상품 상세 페이지
https://trendmecca.co.kr/product/ami-usw247-730-0951-paris-france-%EB%A1%9C%EA%B3%A0-%EA%B3%B5%EC%9A%A9-%ED%9B%84%EB%93%9C%ED%8B%B0-%ED%83%80%EC%9E%84%EB%A9%94%EC%B9%B4/69093/category/2604/display/1/

## General 
Request URL
https://trendmecca.co.kr/product/ami-usw247-730-0951-paris-france-%EB%A1%9C%EA%B3%A0-%EA%B3%B5%EC%9A%A9-%ED%9B%84%EB%93%9C%ED%8B%B0-%ED%83%80%EC%9E%84%EB%A9%94%EC%B9%B4/69093/category/2604/display/1/
Request Method
GET
Status Code
200 OK
Remote Address
203.245.12.117:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers
accept-ranges
bytes
cache-control
no-store, no-cache, must-revalidate, post-check=0, pre-check=0
content-encoding
gzip
content-type
text/html; charset=utf-8
date
Thu, 26 Mar 2026 05:19:34 GMT
expires
Mon, 26 Jul 1997 05:00:00 GMT
last-modified
Thu, 26 Mar 2026 05:19:34 GMT
p3p
CP="NOI ADM DEV PSAi COM NAV OUR OTR STP IND DEM"
pragma
no-cache
server
openresty
vary
Accept-Encoding
x-anigif
webp
x-cache
MISS
x-cache-valid
YES
x-hits
0
x-hrpcs-signal
1
x-hrpcs-ttl
300s
x-hurl
/product/ami-usw247-730-0951-paris-france-%EB%A1%9C%EA%B3%A0-%EA%B3%B5%EC%9A%A9-%ED%9B%84%EB%93%9C%ED%8B%B0-%ED%83%80%EC%9E%84%EB%A9%94%EC%B9%B4/69093/category/2604/display/1/sineorb01view_pcKRwebpagent_pc
x-iscacheurl
YES
x-reqid
bdcf8424828cb0592007018951a91687
x-ttl
300.000
x-via
magneto-edge-icn01-ktog-121
x-xss-protection
1;mode=block


## Request Headers 
:authority
trendmecca.co.kr
:method
GET
:path
/product/ami-usw247-730-0951-paris-france-%EB%A1%9C%EA%B3%A0-%EA%B3%B5%EC%9A%A9-%ED%9B%84%EB%93%9C%ED%8B%B0-%ED%83%80%EC%9E%84%EB%A9%94%EC%B9%B4/69093/category/2604/display/1/
:scheme
https
accept
text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
cookie
_fwb=14562wVLpzWD31iJHrDkkI0.1773108253192; siteLT=1feab9f8-ff9a-adb6-3fc4-db2d40e6d2f5; analytics_longterm=analytics_longterm.sineorb0_1.7AE6110.1773108253615; CVID_Y=CVID_Y.425b5a51574752096c01.1773108253615; _fcOM={"k":"4879bd6c283c9107a2f638319b01f84812-4765","i":"220.76.70.1.5084820","r":1773108254078}; timemecca.co.kr-crema_device_token=3uCgTc0nOyPzZ3xWmSXLxToPrVwHU7g9; psrui=72805635.1765852329; psrfp=eaba98d0ca9250477fcd535b933317ce; _gcl_au=1.1.534764476.1773108254; _tt_enable_cookie=1; _ttp=01KKAQSNJYHEXW22PHRM0RV1TA_.tt.2; recent_plist=113976; _wp_uid=1-885a6aeaaf2ddd7b81eb0ff7b42f0207-s1765852328.460224|windows_10|chrome-1nrfmkl; CFAE_CUK1Y=CFAE_CUK1Y.sineorb0_1.0BA4PO5.1773108256149; CUK45=cuk45_sineorb0_7qfsv43r3q48sgpbr6m2vpv9ad4jch6l; CUK2Y=cuk2y_sineorb0_7qfsv43r3q48sgpbr6m2vpv9ad4jch6l; _fbp=fb.2.1773108275072.49102848333052503; ch-veil-id=51587550-017d-404f-85a8-e742e82d70cd; fb_external_id=231da8bda81305782c940520332d950e6350fcc2529dc70feedf4cb4d8a263ed; siteSID=d7e21d21-e24e-5163-3046-93f5752c4c76; analytics_session_id=analytics_session_id.sineorb0_1.B467C0A.1774501869734; CVID=CVID.425b5a51574752096c01.1774501869734; _gid=GA1.3.383800888.1774501871; ec_ipad_device=F; CFAE_CID=CFAE_CID.sineorb0_1.G37XNKT.1774501875440; CID=CIDRef4676bda5b0ac4b5d5a80b891941d3e; CIDRef4676bda5b0ac4b5d5a80b891941d3e=60b6a3f7c7b31400a82231b1e0b8b790%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%3A%2Findex_time.html%3A%3A1774501875%3A%3A%3A%3Appdp%3A%3A1774501875%3A%3A%3A%3A%3A%3A%3A%3A; ECSESSID=dh466g5fuppb7mmg0d4mrg32pgsha7hf; basketcount_1=0; basketprice_1=0%EC%9B%90; wish_id=1b1182c2047b8e08c49aca97010c2270; wishcount_1=0; isviewtype=pc; support_cookie=1; vt=1774502226; fb_event_id=event_id.sineorb0.1.GJIY01SBOROBL7985PJ8ZS0WXR47QM5EX; wcs_bt=s_2bc791b9bc98:1774502292; _ga_KBS17W908X=GS2.1.s1774501872$o2$g1$t1774502292$j59$l0$h0; _ga_JM9PNKBD79=GS2.1.s1774501871$o2$g1$t1774502292$j60$l0$h0; _ga=GA1.1.314827125.1773108255; _ga_ZVXPT4FXK2=GS2.1.s1774501871$o2$g1$t1774502292$j59$l0$h0; cto_bundle=XyJUsF9CVFFmWVRndDJuc0Y4MTRIcU5FcnowaUpQaEtRSFB0RHhJc2FwRnZRZE9waEdFTTdCM0JCcHcxTEtvR3UlMkZOajB5cWxuZWJFcVFnSExya1BPdFRld0lZbnJmVVc2U1lRUHd0S3N4OGF1QSUyRlRIalJmUTNMaTZEQ2xpT3V6YnBDdURicnpGSTMwQ0ZGWVJDTVlveTFxenp3JTNEJTNE; CFAE_LC=CFAE_LC.sineorb0_1.GE9UVFR.1774502292708; ch-session-97935=eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzZXMiLCJleHAiOjE3NzcwOTQzMTQsImlhdCI6MTc3NDUwMjMxNCwia2V5IjoiOTc5MzUtNjlhZjdjMzUwMDVmYWYyY2Q5NjQifQ.DFMqqi8afhblAT5BRLTCmw9EfFHQOuJIezJ2eHX8tS8; ttcsid=1774501870495::NGzN73n_Ku0AUrjA7cx8.2.1774502374508.0; ttcsid_CN1CJT3C77U2L3UB1RI0=1774501870494::LlsNvxobU1R0G-D7h4uA.2.1774502374508.1; _ga_ZE7PK22CMX=GS2.1.s1774501870$o2$g1$t1774502374$j38$l0$h1695453495
priority
u=0, i
referer
https://trendmecca.co.kr/product/list.html?cate_no=2604&page=1
sec-ch-ua
"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"Windows"
sec-fetch-dest
document
sec-fetch-mode
navigate
sec-fetch-site
same-origin
sec-fetch-user
?1
upgrade-insecure-requests
1
user-agent
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36

## Response
buyma\trendmecca\product.html

### 모델명 : div.infoArea 아래 모델명
<div class="xans-element- xans-product xans-product-detaildesign"><table border="1">
                    <caption> 기본 정보</caption>
                    <tbody>
                        <tr rel="상품명" class=" xans-record-">
                            <th scope="row"><span class="" style="font-size:16px;color:#000000;">상품명</span></th>
                            <td><span class="" style="font-size:16px;color:#000000;">AMI USW247 730 0951 PARIS FRANCE 로고 공용 후드티 타임메카</span></td>
                        </tr>
                        <tr rel="소비자가" class=" xans-record-">
                            <th scope="row"><span class="" style="font-size:13px;color:#555555;">소비자가</span></th>
                            <td><span class="" style="font-size:13px;color:#555555;"><span id="span_product_price_custom"><strike>635,000원</strike></span></span> </td>
                        </tr>
                        <tr rel="판매가" class=" xans-record-">
                            <th scope="row"><span class="" style="font-size:18px;color:#222222;">판매가</span></th>
                            <td><span class="" style="font-size:18px;color:#222222;"><strong id="span_product_price_text">279,000원</strong><input id="product_price" name="product_price" value="" type="hidden"></span> <span class="button" style="display: block;"><a href="#none" id="btn_all_coupondown" class="btnNormal sizeS" style="padding:7px; height:27px; line-height:0.9; vertical-align: top;">전체쿠폰다운받기</a></span>
</td>
                        </tr>
                        <tr rel="최적할인가" class=" xans-record-">
                            <th scope="row"><span class="" style="font-size:18px;color:#000000;font-weight:bold;">최적할인가</span></th>
                            <!--<td><span><span class="" style="font-size:18px;color:#000000;font-weight:bold;"><span id="span_optimum_discount_price">272,100원 <span style="font-size:12px;color:#555555;">(최대  6,900원 할인)</span></span></span></span> <ul class="discountMember"><li style="display: none;"><a href="#" module="product_detail" style="margin-bottom:0" class="ec-front-product-show-benefit-icon" product-no="" benefit="MG"><img class="ec-front-product-show-benefit-icon" product-no="" benefit="MG" src="/img/btn/membtn.png" alt="회원등급 할인혜택"></a></li></ul></td>-->
                            <td clas="card_wrap" style="padding-right: 110px;">
                                <span><span class="" style="font-size:18px;color:#000000;font-weight:bold;"><span id="span_optimum_discount_price">272,100원 <span style="font-size:12px;color:#555555;">(최대  6,900원 할인)</span></span></span></span> 
                                <ul style="position: absolute; top: 0; right: 0;">
                                    <li>
                                        <a href="#" onclick="return false;">
                                        	


<div class="modal">
    <div class="modal_popup">
        
        <div class="modal_demo">
            <div class="modal_tit">
                <h3>무이자 할부 혜택</h3>
            	<p>26.03.01 ~ 26.03.31</p>	
            </div>
        	           
            <div class="modal_txt">
                <div class="card_member">
                	<h4>금액 | 50,000원 이상</h4>
                    <h4>대상 | 트랜드메카 전 고객</h4>
                </div>
                
                <div class="card_dc_wrap">
					<p>고객부담 할부 수수료 있으며, 할부 회차별 일부 할부수수료 면제가 있습니다.</p>
                    <!-- 비씨 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/bc_card.jpg" alt="bc_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~5개월 무이자</li>
<li>
                            <p>※BC카드 부분무이자에서 수협카드는 제외</p>
                            </li>
<li>- 6개월: 1~3회차 수수료 고객 부담</li>
                            <li>- 10개월: 1~4회차 수수료 고객 부담</li>
                            <li>- 12개월: 1~5회차 수수료 고객 부담</li>
                            <p>* 잔여회차 BC카드 부담</p>
                            <p style="margin-top:10px;">* BC카드 Light 할부</p>
                            <li>- 4~6개월: 1~2회차 수수료 고객 부담</li>
                            <li>- 7~10개월: 1~3회차 수수료 고객 부담</li>
                            <li>- 11~12개월: 1~4회차 수수료 고객 부담</li>
                            <p style="margin-top:10px;">* 잔여회차 BC카드 부담</p>
                            <li>이벤트내용: 비씨카드 회원 중 Light 할부 사전신청 고객에 한하여 적용 (※ Non-BC카드 제외)</li>
                            <li>신청방법: 비씨카드홈페이지, APP, ARS ☎1899-5772 통해 사전등록 필수</li>
                        </ul>
                        
                    </div>
                    <!-- 비씨 카드 끝 -->
                    
                    <!-- 우리 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/wl_card.jpg" alt="wl_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~5개월 무이자</li>
<li>
                            </li>
<li>- 10개월 : 1~4회차 수수료 고객 부담</li>
                            <li>- 12개월 : 1~5회차 수수료 고객 부담</li>
                            <p>* 잔여회차 우리카드 부담</p>
                        </ul>
                        
                    </div>
                    <!-- 우리 카드 끝 -->
                    
                    <!-- 농협 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/nh_card.jpg" alt="nh_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~6개월 무이자</li>
<li>
                            </li>
<li>- 7~10개월 : 1~3회차 수수료 고객 부담</li>
                            <li>- 12개월 : 1~4회차 수수료 고객 부담</li>
                            <li>- 18개월 : 1~5회차 수수료 고객 부담</li>
                            <li>- 24개월 : 1~6회차 수수료 고객 부담</li>
                            <p>* 잔여회차 NH농협카드 부담 </p>
                        </ul>
                        
                    </div>
                    <!-- 농협 카드 끝 -->
                    
                    <!-- 삼성 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/ss_card.jpg" alt="ss_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~3개월 무이자</li>
<li>
                            </li>
<li>- 7개월 : 1~3회차 수수료 고객 부담</li>
                            <li>- 11개월 : 1~5회차 수수료 고객 부담</li>
                            <li>- 23개월 : 1~10회차 수수료 고객 부담</li>
                            <p>* 잔여회차 삼성카드 부담</p>
                        </ul>
                        
                    </div>
                    <!-- 삼성 카드 끝 -->
                    
                    <!-- 현대 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/hd_card.jpg" alt="hd_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~3개월 무이자</li>
<li>
                            </li>
<li>- 10개월 : 1~5회차 수수료 고객 부담</li>
<li>
                            </li>
<li>- 12개월 : 1~6회차 수수료 고객 부담</li>
<li>
                            <p>* 잔여회차 현대카드 부담</p>
                        </li>
</ul>
                        
                    </div>
                    <!-- 현대 카드 끝 -->
                    
                    <!-- 신한 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/sh_card.jpg" alt="sh_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~3개월 무이자</li>
<li>
                            </li>
<li>- 7개월 : 1~3회차 수수료 고객 부담</li>
<li>
                            </li>
<li>- 9개월 : 1~4회차 수수료 고객 부담</li>
<li>
                            </li>
<li>- 11개월 : 1~5회차 수수료 고객 부담</li>
<li>
                            </li>
<li>- 23개월 : 1~10회차 수수료 고객 부담</li>
<li>
                            <p>* 잔여회차 신한카드 부담</p>
                        </li>
</ul>
                        
                    </div>
                    <!-- 현대 카드 끝 -->
                    
                    <!-- 국민 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/kb_card.jpg" alt="kb_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~3개월 무이자</li>
<li>
                            </li>
<li>- 6개월 : 1~3회차 수수료 고객 부담</li>
                            <li>- 10개월 : 1~5회차 수수료 고객 부담</li>
                            <li>- 12개월 : 1~5회차 수수료 고객 부담</li>
                            <li>- 18개월 : 1~7회차 수수료 고객 부담</li>
                            <p>* 잔여회차 국민카드 부담</p>
                        </ul>
                        
                    </div>
                    <!-- 국민 카드 끝 -->
                    
                    <!-- 롯데 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/lt_card.jpg" alt="lt_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~5개월 무이자</li>
<li>
                        </li>
</ul>
                        
                    </div>
                    <!-- 롯데 카드 끝 -->
                    
                    <!-- 하나 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/hn_card.jpg" alt="hn_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~3개월 무이자</li>
<li>
                            </li>
<li>- 6개월 : 1~3회차 수수료 고객 부담</li>
                            <li>- 10개월 : 1~4회차 수수료 고객 부담</li>
                            <li>- 12개월 : 1~5회차 수수료 고객 부담</li>
                            <li>- 18개월 : 1~8회차 수수료 고객 부담</li>
                            <p>* 잔여회차 하나카드 부담</p>
                        </ul>
                        
                    </div>
                    <!-- 하나 카드 끝 -->
                    
                    <!-- 전북 카드 시작 -->
                    <div class="card_dc" style="display:none;">
                    	<img src="/design/kr/subpage/product/jb_card.jpg" alt="jb_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~3개월 무이자</li>
<li>
                            </li>
<li>- 4~9개월 : 1회차 수수료 고객 부담</li>
<li>
                            </li>
<li>- 10~12개월 : 1~2회차 수수료 고객 부담</li>
<li>
                            <p>* 잔여회차 전북카드 부담</p>
                        </li>
</ul>
                        
                    </div>
                    <!-- 전북 카드 끝 -->
                    
                    <!-- 광주 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/kg_card.jpg" alt="kg_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~5개월 무이자</li>
<li>
                        </li>
</ul>
                        
                    </div>
                    <!-- 광주 카드 끝 -->
                    
                    <!-- 수협 카드 시작 -->
                    <div class="card_dc">
                    	<img src="/design/kr/subpage/product/ssh_card.jpg" alt="ssh_card.jpg">
                        &nbsp;<ul>
                        	<li class="sub_title">- 2~5개월 무이자</li>
<li>
                        </li>
</ul>
                        
                    </div>
                    <!-- 수협 카드 끝 -->
                    
                </div>
                
            </div>
            
            <div class="card_notice">
                <p>[유의사항]</p>
            	<ul>
                    <li>※ 상기행사는 카드사 사정에 의해 변경 및 중단될 수 있습니다. <span>자세한 사항은 각 카드사 홈페이지를 참조 바랍니다.</span>
</li>
                    <li>※ 법인/기업(개인사업자)/체크/선불/GIFT/하이브리드/ 은행계열 카드 등 제외</li>
                    <li>※ 무이자/부분무이자 적용 시, <span>포인트/마일리지 적립 불가.</span>
</li>
                    <li>※ 상점부담 무이자/특별제휴/직계약가맹점, 오프라인(비인증), 대학등록금, 지방세(세외수입,상수도), 제약(B2B), 주유, 자동차, 승차권, 방송수신료, 보험, 렌터카, 백화점업종, 전기차충전, 홈쇼핑, 병원업종 등 제외</li>
                    <li>※ 비씨,NH농협카드 부분무이자 <span>ARS 사전 신청 고객의 경우에만 우대회차 적용</span>
</li>
                    <li>※ 우리비씨, 우리독자카드는 우리카드 무이자 행사 적용 (우리비씨 이외 비씨카드는 비씨카드 무이자 행사 적용)</li>
                    <li>※ 하나카드: PG업종 외 서적, 학원, 면세점 등 별도업종 및 환금성가맹점, 토스카드 제외</li>
                    <li>※ 현대카드: 의약업종, 홈쇼핑 제외</li>
                    <li>※ 삼성카드: PG업종 무이자 홈쇼핑, 세금, 병원업종 제외 / 부분무이자 홈쇼핑, 세금, 병원업종, 제약, 유류, 도시가스 제외</li>
                    <li>※ 수협카드: 비씨카드와 동일한 업종 무이자 적용 (단, 부분 무이자는 제외)</li>
                </ul>
            </div>
        </div>
        
        <div class="modal_fixed">
        	<button type="button" class="close_btn">닫기</button>
        </div>
        
    </div>
</div>

<section>
	<button type="button" class="modal_btn">카드혜택</button>
</section>
                                        </a>
                                    </li>
                                </ul>
                            </td>
                        </tr>
                        <tr rel="모델명" class=" xans-record-">
                            <th scope="row"><span class="" style="font-size:13px;color:#555555;">모델명</span></th>
                            <td><span class="" style="font-size:13px;color:#555555;">USW247 730 0951</span> </td>
                        </tr>
                        <tr rel="브랜드" class=" xans-record-">
                            <th scope="row"><span class="" style="font-size:13px;color:#353535;">브랜드</span></th>
                            <td><span class="" style="font-size:13px;color:#353535;">아미[AMI]</span> </td>
                        </tr>
                        <tr rel="제조국" class=" xans-record-">
                            <th scope="row"><span class="" style="font-size:13px;color:#555555;">제조국</span></th>
                            <td><span class="" style="font-size:13px;color:#555555;">포르투갈</span> </td>
                        </tr>
<tr rel="적립금" class=" xans-record-">
                            <th scope="row"><span class="" style="font-size:13px;color:#555555;">적립금</span></th>
                            <td><span class="" style="font-size:13px;color:#555555;"><p><img src="//img.echosting.cafe24.com/design/skin/admin/ko_KR/ico_pay_money.gif" alt="무통장 입금 결제" style="margin-bottom:2px;">5,442P (2%)</p><p><img src="//img.echosting.cafe24.com/design/skin/admin/ko_KR/ico_pay_card.gif" alt="신용카드 결제" style="margin-bottom:2px;">1,360P (0.5%)</p><p><img src="//img.echosting.cafe24.com/design/skin/admin/ko_KR/ico_pay_bank.gif" alt="실시간 계좌 이체" style="margin-bottom:2px;">2,721P (1%)</p><p><img src="//img.echosting.cafe24.com/design/skin/admin/ko_KR/ico_pay_mobile.gif" alt="휴대폰 결제" style="margin-bottom:2px;">1,360P (0.5%)</p><p><img src="//img.echosting.cafe24.com/design/skin/admin/ko_KR/ico_pay_account.gif" alt="가상계좌결제" style="margin-bottom:2px;">1,360P (0.5%)</p><p><img src="//img.echosting.cafe24.com/design/skin/admin/ko_KR/ico_pay_payco.gif" alt="페이코" style="margin-bottom:2px;">1,360P (0.5%)</p><p><img src="//img.echosting.cafe24.com/design/skin/admin/ko_KR/ico_pay_kakaopay.gif" alt="카카오페이" style="margin-bottom:2px;">1,360P (0.5%)</p><p><img src="//img.echosting.cafe24.com/design/skin/admin/ko_KR/ico_pay_store.gif" alt="편의점결제" style="margin-bottom:2px;">1,360P (0.5%)</p></span> </td>
                        </tr>
                    </tbody>
                </table>
</div>


### 실측정보, 소재 등 x

### 카테고리 : 넥스트젠팩과 동일하게 list.html 페이지에 카테고리가 있음. 먼저 각 product_id 확보 후 세팅

### 이미지 : <div class="detailArea"> 아래
넥스트젠팩팩 처럼 자체 수집해서 ace_product_images 테이블에 넣음.
<div class="xans-element- xans-product xans-product-addimage listImg"><div class="inner">
                        <ul class="list">
                            <li class="xans-record-"><img src="https://sineorb3.cafe24.com/Design/mall_2/600/AMI/USW247 730 0951.jpg" class="ThumbImage" alt=""></li>
                                                    </ul>
                    </div>
<button type="button" class="prev"><i aria-hidden="true" class="icon icoArrowLeft"></i>이전</button>
<button type="button" class="next"><i aria-hidden="true" class="icon icoArrowRight"></i>다음</button>
</div>