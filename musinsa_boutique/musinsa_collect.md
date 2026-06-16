## 리스트는 HTML이 아니라 JSON API
- 리스트 페이지(`/category/105/goods`)는 React/Next.js로 화면을 나중에 그림 > `musinsa_list.html`에는 상품이 0개로 HTML 긁기 불가
- 실제 상품 목록은 아래 JSON API에서 받는다.

## 상품목록 API
```
GET https://api.musinsa.com/api2/dp/v1/plp/goods?gf=A&category=105&sortCode=NEW&page=1&size=30&caller=CATEGORY
```
- `category=105` : 부티크 전체상품 (= 전체상품 메뉴)
- `sortCode=NEW` : 신상품순
- `page` : 1부터 1씩 증가. 응답 `data.pagination`에 총 페이지/총 개수 있음
- `size` : 페이지당 개수 (30, 늘려도 됨)
- `gf=A` : 성별 전체

응답 `data.list[]` 각 항목 주요 필드:
| 필드 | 내용 | 쓰임 |
|---|---|---|
| `goodsNo` | 상품번호 (예: 6658941) | 상세 URL `/products/{goodsNo}` |
| `goodsName` | 상품명 | product_name |
| `brandName` | 브랜드 한글명 (예: 존 스메들리) | mall_brands 한글 |
| `brand` | 브랜드 영문코드 (예: johnsmedley) | mall_brands 영문 (= 상세 `data-brand-id`) |
| `price` / `normalPrice` / `saleRate` | 판매가 / 정가 / 할인율 | 가격 |
| `thumbnail` | 썸네일 이미지 | 이미지 |
| `isSoldOut` | 품절 여부 | 재고 |
| `displayGenderText` | 여성/남성 | 성별 |

## 상세 API (HTML 파싱 불필요)
상세도 HTML이 아니라 JSON API가 있다. **아래 HTML 추출 규칙들은 참고용일 뿐, 실제 수집기는 API를 쓴다.**
```
GET https://goods-detail.musinsa.com/api2/goods/{goodsNo}
```
- `brandInfo.brandName`(한글) / `brandInfo.brandEnglishName`(영문) / `brand`(코드 johnsmedley)
- `category.categoryDepth1~4Name` + `Code` → category_path (예 `상의 > 니트/스웨터`, 코드 001006)
- `styleNo` → model_id (예 DANIELLA03)
- `goodsPrice.salePrice / normalPrice / discountRate` → 가격 / `isOutOfStock` → 재고
- `goodsImages[].imageUrl` → 상대경로라 앞에 `https://image.msscdn.net` 붙임 / `sex` → 성별

## 옵션 API
```
GET https://goods-detail.musinsa.com/api2/goods/{goodsNo}/options
```
- `basic[]` : 옵션 그룹 (컬러=COLOR_CHIP, 사이즈=DROPDOWN), 각 `optionValues[].name`
- `optionItems[]` : 실제 판매 조합 (재고 동기화용, raw_json에 통째 저장)
---

## 리스트 페이지 (페이지 구조 참고)
- https://www.musinsa.com/category/105/goods?sortCode=NEW&gf=A
- @musinsa_list.html


## General
Request URL
https://www.musinsa.com/category/105/goods?sortCode=NEW&gf=A
Request Method
GET
Status Code
200 OK
Remote Address
172.66.1.211:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers
alt-svc
h3=":443"; ma=86400
cache-control
private, no-cache, no-store, max-age=0, must-revalidate
cf-cache-status
DYNAMIC
cf-ray
a0bd7c7bfb44d1d1-ICN
content-encoding
gzip
content-type
text/html; charset=utf-8
date
Mon, 15 Jun 2026 00:33:57 GMT
priority
u=0,i
server
cloudflare
server-timing
cfCacheStatus;desc="DYNAMIC"
server-timing
cfEdge;dur=5,cfOrigin;dur=38
server-timing
cfExtPri
vary
Accept-Encoding
via
1.1 3f8ca32ba33b2ea83ece87bf9c622aec.cloudfront.net (CloudFront)
x-amz-cf-id
DfyZ9skxQvtZDNdYOfmZL1m2cSKTfpfaXQFLqR5p7fEULxhgvxuh7g==
x-amz-cf-pop
ICN53-P1
x-cache
Miss from cloudfront

## Request Headers
:authority
www.musinsa.com
:method
GET
:path
/category/105/goods?sortCode=NEW&gf=A
:scheme
https
accept
text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
cache-control
no-cache
cookie
_gf=A; tr[vid]=6a2b872dd27309.62734196; tr[vd]=1781237549; _gcl_au=1.1.1711811817.1781237550; _ga=GA1.1.706763544.1781237550; _fwb=53Ajf3tQIDjTKmH2sc5FkX.1781237550571; _kmpid=km|www.musinsa.com|1781237550574|31572642-1a36-42f1-9b52-56c269022a95; _kmpid=km|musinsa.com|1781237550574|31572642-1a36-42f1-9b52-56c269022a95; _twpid=tw.1781237550653.706961778838005598; _pin_unauth=dWlkPU9UZ3dOamxrTkRJdFpXSXlOUzAwTW1JM0xUZ3dOVEl0T1RSa1pEVXhNelUzWkRBdw; _fbp=fb.1.1781237551049.817534330487966718; _hjSessionUser_1491926=eyJpZCI6IjAwYzE5Mzc0LTMxMDItNTY3NS1iZTViLTM4MjMxNDZiNzE0MiIsImNyZWF0ZWQiOjE3ODEyMzc1NTA1ODYsImV4aXN0aW5nIjp0cnVlfQ==; apptech_tooltip_shown=1; viewKind=3GridView; cart_no=qG1EBdXZjpahRixSxuL59%2Fg90LKgEMphGpuBlSnNtjI%3D; tr[vt]=1781482941; tr[vc]=1; cf_clearance=_oNHvrls3YBuNGbMmtPgQFfMfjf8WhkS3jo6y80xrvY-1781482942-1.2.1.1-KcJs4atVbkJdqiuDaJ7qF2BQfNxhrhdcHTcC.EhXGfBSl_KvN4K.hqidBRjR5z5hc1REU4rjRBn8Rfsf2x4KFcXJC1zOJcHQKY2GiYxoOBDxbYN9EQo_I6AfjjXjpSsA2cJRphRmtPBTEOebJHJ6uesVaR8f.9sO49eoQf0Zz4vbRjkCHrGBGYhgCmzjZgUi851OJaWCGYsAadDdNJcFlD_R3vizTsth1faHvSkQYo8xC4mi5raPmFJqmFts70zE4cfbs3.ETgaW62lNT1bvNdPmm5Yc9Sd3TtKHCGkfyEgwn0wmztfX.2aG6lq8Q8ZdVJotA.XZWd9USvt7VTf0Uw; __cf_bm=R9IUsyNv5uS.BV14PjuyX9r1idUkp1UQag4ICIQCgiU-1781482942.345401-1.0.1.1-NSxXdppn6N3SEkFKgHV5m5P9hq97clKLm8VPHnHlWMfqNNlwCJnYaATRKl41ytyPpw9N9vlVj2d5vHCYGsYM4yzXbB8hiLy6QfsrIdoVXNZak10cE13To1yfoj6kosbf; _hjSession_1491926=eyJpZCI6IjAzMWM1OTczLTBmNGUtNDhkMS1iNzRlLTllNWNmNzZjYzk4ZSIsImMiOjE3ODE0ODI5NDIwMDMsInMiOjAsInIiOjAsInNiIjowLCJzciI6MCwic2UiOjAsImZzIjowLCJzcCI6MH0=; ab.storage.deviceId.1773491f-ef03-4901-baf8-dbf84e1de25b=%7B%22g%22%3A%226f2e0f75-2ee5-8005-c297-43b1d17f5476%22%2C%22c%22%3A1781482946267%2C%22l%22%3A1781482946267%7D; ab.storage.sessionId.1773491f-ef03-4901-baf8-dbf84e1de25b=%7B%22g%22%3A%221c00fa2f-a9d2-0350-99b4-33e83bdf3528%22%2C%22e%22%3A1781484746270%2C%22c%22%3A1781482946266%2C%22l%22%3A1781482946270%7D; _tt_enable_cookie=1; _ttp=01KV4AGZT9FGGZ7VT6NDKAV2YF_.tt.1; ttcsid_CF2AOI3C77UCCRP8DVQG=1781482946379::j7CAxUmMwhBs8lr0O9Ej.1.1781482949860.0; ttcsid=1781482946379::jZbq-7Q0GzOc1IlLEUqs.1.1781482949860.0::1.-3075.0::3466.1.1259.98::4312.5.3018; tr[pv]=3; AMP_74a056ea4a=JTdCJTIyZGV2aWNlSWQlMjIlM0ElMjIxYmUzMjJkZi1hOGM3LTQyN2QtYWU2NC1iYzQ1ZDAyNDNjOGQlMjIlMkMlMjJzZXNzaW9uSWQlMjIlM0ExNzgxMjM3NTQ4OTU4JTJDJTIyb3B0T3V0JTIyJTNBZmFsc2UlMkMlMjJwYWdlQ291bnRlciUyMiUzQTAlN0Q=; wcs_bt=s_eacb1da8e76:1781483616; _ga_8PEGV51YTJ=GS2.1.s1781482936$o2$g1$t1781483616$j25$l0$h0; _derived_epik=dj0yJnU9RVRRVkR2b2dYdTh3UlBFeVhsZHl6clhYREdTa1MydGYmbj05WTRudUhaekNPT0tEa1RrTGZNclBRJm09MSZ0PUFBQUFBR292U0dBJnJtPTEmcnQ9QUFBQUFHb3ZTR0Emc3A9Mg; cto_bundle=obn3TV9vVTlOTW1zT2RoJTJGeDU0ZW1BVVJuMmZNcnBpZDFaNDNra2pQeFhGd2FEVkQxVEpoRUlaNXowZjFUSGhCV25KRE8yM3ZVcFQlMkZzdE5pZFJFOXIxVmtHcmVKTVVaaWg1RWh6WmZBN1BkZjdhMVZ1ck5aQlp6Mm5Hc2I0MHVHamV4VGx5ZDczQ2olMkJvVXkxa09tZ0VGb1RPT3clM0QlM0Q; _dd_s=rum=0&expire=1781484536698
pragma
no-cache
priority
u=0, i
referer
https://www.musinsa.com/main/boutique/recommend?gf=A
sec-ch-ua
"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"macOS"
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
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36


# 상품 상세 페이지
- https://www.musinsa.com/products/6658941
- @musinsa_product.html


## General 
Request URL
https://www.musinsa.com/products/6658941
Request Method
GET
Status Code
200 OK
Remote Address
172.66.1.211:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers
alt-svc
h3=":443"; ma=86400
cache-control
private, no-cache, no-store, max-age=0, must-revalidate
cf-cache-status
DYNAMIC
cf-ray
a0bd8b3aea26d1d1-ICN
content-encoding
gzip
content-type
text/html; charset=utf-8
date
Mon, 15 Jun 2026 00:44:01 GMT
priority
u=0,i
referrer-policy
strict-origin-when-cross-origin
server
cloudflare
server-timing
cfCacheStatus;desc="DYNAMIC"
server-timing
cfEdge;dur=4,cfOrigin;dur=63
server-timing
cfExtPri
strict-transport-security
max-age=31536000
vary
Accept-Encoding
vary
Origin
via
1.1 ec4bedae713c0a8b58127f20294df120.cloudfront.net (CloudFront)
x-amz-cf-id
qYYppWxCoAKlMmDD2oAmgiOIFZaPFuYfYD7BjGjClP5zNOULu5ryFg==
x-amz-cf-pop
ICN53-P1
x-cache
Miss from cloudfront
x-content-type-options
nosniff
x-frame-options
SAMEORIGIN
x-xss-protection
1; mode=block

## Request Headers 
:authority
www.musinsa.com
:method
GET
:path
/products/6658941
:scheme
https
accept
text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
cache-control
no-cache
cookie
_gf=A; tr[vid]=6a2b872dd27309.62734196; tr[vd]=1781237549; _gcl_au=1.1.1711811817.1781237550; _ga=GA1.1.706763544.1781237550; _fwb=53Ajf3tQIDjTKmH2sc5FkX.1781237550571; _kmpid=km|www.musinsa.com|1781237550574|31572642-1a36-42f1-9b52-56c269022a95; _kmpid=km|musinsa.com|1781237550574|31572642-1a36-42f1-9b52-56c269022a95; _twpid=tw.1781237550653.706961778838005598; _pin_unauth=dWlkPU9UZ3dOamxrTkRJdFpXSXlOUzAwTW1JM0xUZ3dOVEl0T1RSa1pEVXhNelUzWkRBdw; _fbp=fb.1.1781237551049.817534330487966718; _hjSessionUser_1491926=eyJpZCI6IjAwYzE5Mzc0LTMxMDItNTY3NS1iZTViLTM4MjMxNDZiNzE0MiIsImNyZWF0ZWQiOjE3ODEyMzc1NTA1ODYsImV4aXN0aW5nIjp0cnVlfQ==; apptech_tooltip_shown=1; viewKind=3GridView; cart_no=qG1EBdXZjpahRixSxuL59%2Fg90LKgEMphGpuBlSnNtjI%3D; tr[vt]=1781482941; tr[vc]=1; _hjSession_1491926=eyJpZCI6IjAzMWM1OTczLTBmNGUtNDhkMS1iNzRlLTllNWNmNzZjYzk4ZSIsImMiOjE3ODE0ODI5NDIwMDMsInMiOjAsInIiOjAsInNiIjowLCJzciI6MCwic2UiOjAsImZzIjowLCJzcCI6MH0=; ab.storage.deviceId.1773491f-ef03-4901-baf8-dbf84e1de25b=%7B%22g%22%3A%226f2e0f75-2ee5-8005-c297-43b1d17f5476%22%2C%22c%22%3A1781482946267%2C%22l%22%3A1781482946267%7D; _tt_enable_cookie=1; _ttp=01KV4AGZT9FGGZ7VT6NDKAV2YF_.tt.1; tr[pv]=5; cf_clearance=PaD.2_hctLApgwrMt8ecTKxj_kbDOURwnxC41URoOqY-1781484224-1.2.1.1-_ST5NxdGRVePUAFo_l7kct8UYPRZxvpoRt.43ynUn6J2hIbDsD.3IH_C8760aVPuE6Q8xtTOXOdAtpIHgjsbgJDpVkClcFDTj3IETJlbJ8xeC6wAUkXeMSXQZnSrScMGJLqogXpSyC1yaoz1DwDitgywl5VlbrnV4S_.AoYC7TixW2qVRBTVG1I8XpTbeqKZXOFwcVPvTxW_3yOG0B.JAdVlYmRQEAXzCVNihRqHWoVE4bzSI3y5wvMbhJAyUWuw48.ZBMS6nNkVbzYb4JYHbH5hzHBb0TJ31JU8j1_P3Cqtfq7BKGGy_whCe4ZLRCWGHI17N7bHShmbv3yq2uOCYg; __cf_bm=nAg5pRAMlQnGfueNhph3GuHzYFonyLXqV1hBOkvCjGY-1781484224.4363377-1.0.1.1-ukWl8vjOeOQg.VvwGlnEN5ZYXRSR1yu3SiT.Kzm9jWqkH5a_Jl8BQsO7m9__XHijMBaXsryfA_aFfplJC5yyMq0e2_OMJsWcRwvrq3w6wi409brAJd8Y9msV4L39Zktf; AMP_74a056ea4a=JTdCJTIyZGV2aWNlSWQlMjIlM0ElMjIxYmUzMjJkZi1hOGM3LTQyN2QtYWU2NC1iYzQ1ZDAyNDNjOGQlMjIlMkMlMjJzZXNzaW9uSWQlMjIlM0ExNzgxMjM3NTQ4OTU4JTJDJTIyb3B0T3V0JTIyJTNBZmFsc2UlMkMlMjJwYWdlQ291bnRlciUyMiUzQTAlN0Q=; wcs_bt=s_eacb1da8e76:1781484224; cto_bundle=dvOZhl9vVTlOTW1zT2RoJTJGeDU0ZW1BVVJuMmJYcW5IczNvT3RHRHhFM2N6dmZWbk5tRXBVZnMlMkZROUZRd1hBMlFEWFVzZEgweHFwUFZQYjR1NXd5c1Z6T2NNUmJhcXdibm5VVWRHUFB5c2pxQ01QclloWjM4RVM1RW0xd2NMR3I2VlkxNXFDR0tUeDNCR2RyZCUyRnNkQU0lMkI2MUl5USUzRCUzRA; _ga_8PEGV51YTJ=GS2.1.s1781482936$o2$g1$t1781484225$j58$l0$h0; ttcsid_CF2AOI3C77UCCRP8DVQG=1781482946379::j7CAxUmMwhBs8lr0O9Ej.1.1781484225521.1; _derived_epik=dj0yJnU9VGNnSmxkSnpuVVRfN1NnNjJqNm5vWmFtOHdqTVFwNXQmbj1pMzJtVEpVRmN0YUs0TXItbGxURExRJm09MSZ0PUFBQUFBR292U3NFJnJtPTEmcnQ9QUFBQUFHb3ZTc0Umc3A9NQ; ab.storage.sessionId.1773491f-ef03-4901-baf8-dbf84e1de25b=%7B%22g%22%3A%221c00fa2f-a9d2-0350-99b4-33e83bdf3528%22%2C%22e%22%3A1781486025876%2C%22c%22%3A1781482946266%2C%22l%22%3A1781484225876%7D; _dd_s=rum=0&expire=1781485140712; ttcsid=1781482946379::jZbq-7Q0GzOc1IlLEUqs.1.1781484225521.0::1.1277458.1279139::1294334.2.27.397::4312.5.3018
pragma
no-cache
priority
u=0, i
referer
https://www.musinsa.com/category/105/goods?sortCode=NEW&gf=A
sec-ch-ua
"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"macOS"
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
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36

### 기본 정보 TitleWrap__Wrap 에서 추출 (상품 상세 HTML)
- `brand_name`, `존 스메들리` - `Brand__BrandName` 텍스트
- `brand_name_en`, `johnsmedley` - `a[data-brand-id]` 의 `data-brand-id`
- `product_name` → `GoodsName-sc-*` 텍스트: `DANIELLA Classic Half-Sleeve Sea Island Cotton Knit Heritage Col.3_10 Colors`
<div class="TitleWrap__Wrap-sc-1sh1akj-0 iXDPhZ">
  <div class="Brand__Wrap-sc-idrot9-0 cJDnsU">
    <a href="https://www.musinsa.com/brand/johnsmedley" class="Brand__Link-sc-idrot9-1 clA-DNR gtm-click-brand" data-index="(not set)" data-section-name="prd_title" data-section-index="5" data-brand-id="johnsmedley">
      <div class="inline-flex items-center justify-center relative z-5 rounded-full before:border before:rounded-full before:border-solid before:absolute before:inset-0 before:size-full before:z-6 size-8 bg-white before:border-black before:opacity-[8%]" data-mds="ImageCircleItem">
        <div class="relative z-5 inline-flex items-center justify-center w-full h-full before:absolute before:inset-0 before:size-full before:z-5 before:overflow-hidden before:bg-transparent overflow-hidden rounded-full" data-mds="Image">
          <img class="w-full absolute m-auto inset-0 h-auto z-0 visible max-w-[75%] max-h-[35%] object-contain" aria-hidden="true" src="//image.musinsa.com/mfile_s01/_brand/free_medium/johnsmedley.png?20230524153651" data-mds="Image">
        </div>
      </div>
      <span class="Brand__BrandName-sc-idrot9-2 jSXjwz">
        <span class="text-[14px] leading-[20px] tracking-[0] font-[500] lang-ja-JP:text-[13px] lang-zh-CN:text-[13px] lang-zh-TW:text-[13px] font-global" data-mds="Typography">존 스메들리</span>
        <button class="inline-flex items-center justify-center group-data-[disabled]:cursor-not-allowed focus:outline-none size-5 Brand__InfoIconWrap-sc-idrot9-3 glNsaG" aria-label="브랜드 소개" data-mds="IconButton">
          <svg width="100%" height="100%" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="relative" data-mds="IcInfo"><path d="M10 6V8M10 9V14M17.5 10C17.5 14.1421 14.1421 17.5 10 17.5C5.85786 17.5 2.5 14.1421 2.5 10C2.5 5.85786 5.85786 2.5 10 2.5C14.1421 2.5 17.5 5.85786 17.5 10Z" class="stroke-black" data-mds="IcInfo" vector-effect="non-scaling-stroke"></path></svg>
        </button>
      </span>
    </a>
    <button class="inline-flex items-center border border-solid justify-center rounded focus:outline-none text-black border-gray-300 disabled:bg-gray-100 disabled:border-gray-200 disabled:text-gray-400 h-6 px-1.5 LikeBrand__BrandLikeButton-sc-1ixldjp-0 JpzPO" data-mds="Button">
      <div class="LikeBrand__LikeButton-sc-1ixldjp-1 eoBnWC">
        <div class="inline-flex" data-mds="LikeMotion" style="transform: none;">
          <svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="0 0 20 20" fill="none" class="" data-mds="IcBoldLike"><path d="M9.79493 16.3061C9.91046 16.4154 10.0895 16.4154 10.2051 16.3061C11.1045 15.4553 14.7235 12.0265 16.25 10.5C16.8895 9.85325 17.5 8.75 17.5 7.5C17.5 5.34156 15.8342 3.5 13.75 3.5C11.9105 3.5 11 4.99545 10 6.25C9 4.99545 8.08947 3.5 6.25 3.5C4.16579 3.5 2.5 5.34156 2.5 7.5C2.5 8.75 3.11053 9.85325 3.75 10.5C5.27651 12.0265 8.89549 15.4553 9.79493 16.3061Z" stroke-width="1.4" stroke-miterlimit="10" class="stroke-black" data-mds="IcBoldLike" vector-effect="non-scaling-stroke"></path></svg>
        </div>
      </div>
      <span class="text-[13px] leading-[18px] tracking-[0] font-[500] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] font-global" data-mds="Typography">346</span>
    </button>
  </div>
  <div class="GoodsName__Wrap-sc-1tpr922-0 lcKinb">
    <span class="text-[18px] leading-[24px] tracking-[0] font-[500] lang-ja-JP:text-[17px] lang-zh-CN:text-[17px] lang-zh-TW:text-[17px] GoodsName-sc-1tpr922-1 gRHqyI font-global" data-mds="Typography">DANIELLA Classic Half-Sleeve Sea Island Cotton Knit Heritage Col.3_10 Colors</span>
  </div>
</div>

### 가격 추출 
- `original_price`: `340,000원` , 취소선 가격 (`line-through`)
- `raw_price`: `173,400원`, 현재 판매가 (`Price__CalculatedPrice`)
<div class="Price__PriceTotalWrap-sc-1vz564u-0 eMWYCp">
  <div class="Price__PriceWrap-sc-1vz564u-1 fFmHwn">
    <div class="Price__PriceTitle-sc-1vz564u-2 fMRerq">
      <div class="Price__DiscountWrap-sc-1vz564u-3 haXAhi">
        <span class="text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] line-through text-gray-500 font-global" data-mds="Typography">340,000원</span>
      </div>
      <div class="Price__CurrentPrice-sc-1vz564u-11 bwPLPH">
        <span class="text-[18px] leading-[24px] tracking-[0] font-[600] lang-ja-JP:text-[17px] lang-zh-CN:text-[17px] lang-zh-TW:text-[17px] Price__DiscountRate-sc-1vz564u-14 WwisH text-red font-global" data-mds="Typography">49%</span>
        <span class="text-[18px] leading-[24px] tracking-[0] font-[600] lang-ja-JP:text-[17px] lang-zh-CN:text-[17px] lang-zh-TW:text-[17px] Price__CalculatedPrice-sc-1vz564u-15 diBrae text-black font-global" data-mds="Typography">173,400원</span>
      </div>
    </div>
  </div>
</div>


### 옵션 상품
<div class="OptionAreaWrapper__Wrap-sc-w2u03d-0 hOdYGk">
  <div class="pt-1 pb-2 OptionDropdown__Wrapper-sc-f18lx4-0 ejLFdp">
    <div class="py-1 w-full">
      <div class="static-dropdown-menu" data-mds="StaticDropdownMenu">
        <div class="relative w-full h-full cursor-pointer group" data-state="open" data-mds="DropdownTriggerBox">
          <input type="text" readonly="" class="w-full text-left font-global text-black placeholder-gray-500 placeholder:font-global placeholder:text-[14px] text-[14px] leading-[20px] tracking-[0] font-[400] lang-ja-JP:text-[13px] lang-zh-CN:text-[13px] lang-zh-TW:text-[13px] group-data-[state=closed]:rounded group-data-[state=open]:rounded-t focus:outline-none disabled:text-gray-400 group-data-[disabled]:placeholder-gray-400 disabled:placeholder-gray-400 disabled:bg-gray-100 group-data-[disabled]:text-gray-400 group-data-[disabled]:bg-gray-100 cursor-pointer disabled:cursor-not-allowed group-data-[disabled]:cursor-not-allowed border border-solid border-gray-300 disabled:border-gray-200 py-2 pl-2 pr-8 gtm-click-button" data-index="(not set)" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="option_type" data-button-name="0컬러" placeholder="컬러" data-mds="DropdownTriggerInput">
          <button class="justify-center group-data-[disabled]:cursor-not-allowed focus:outline-none size-4 absolute right-0 top-0 items-center h-full mr-2 hidden group-data-[state=open]:flex" data-mds="IconButton">
            <svg width="100%" height="100%" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="text-gray-500 group-data-[disabled]:text-gray-400" data-mds="IcArrowTop"><path d="M4 12L9.78787 6.21213C9.90503 6.09497 10.095 6.09497 10.2121 6.21213L16 12" class="stroke-current" data-mds="IcArrowTop" vector-effect="non-scaling-stroke"></path></svg>
          </button>
          <button class="justify-center group-data-[disabled]:cursor-not-allowed focus:outline-none size-4 absolute right-0 top-0 items-center h-full mr-2 hidden group-data-[state=closed]:flex" data-mds="IconButton">
            <svg width="100%" height="100%" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg" class="stroke-black text-gray-500 group-data-[disabled]:text-gray-400" data-mds="IcArrowDown" fill="none"><path d="M4 8L9.78787 13.7879C9.90503 13.905 10.095 13.905 10.2121 13.7879L16 8" class="stroke-current" data-mds="IcArrowDown" vector-effect="non-scaling-stroke"></path></svg>
          </button>
        </div>
        <div class="w-[var(--radix-dropdown-menu-trigger-width)] bg-white z-50 overflow-hidden py-2 rounded-b border-b border-x text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 DropdownScrollContainer__Wrap-sc-1ehpo16-0 jbxibh" data-state="open" data-mds="StaticDropdownMenuContent">
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/darknavy.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Navy(네이비)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/lavender.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Pintuck Lilac(핀턱라일락)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/rosegold.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Rosebud(로즈버드)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/etc.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Ruche Red(루체레드)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/lightgrey.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Silver(실버)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/deepred.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Sorrel Red(소렐레드)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/red.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Tulip Red(튤립레드)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/mustard.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Tailors Yellow(테일러옐로우)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/purple.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">Ultra Violet(울트라바이올렛)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
          <div class="SelectedOption__SelectOptionItemContainer-sc-2fd5hw-1 hfUyvf relative text-gray-500 text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] flex cursor-pointer select-none items-center rounded-sm px-4 py-[11px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:bg-gray-100 data-[disabled]:text-gray-400 gtm-click-button" data-index="33" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="select_optionvalue" data-button-name="none" data-mds="StaticDropdownMenuItem">
            <div class="DropdownItemContent__Container-sc-163ikwq-0 ePVptR">
              <span class="leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-xs DropdownItemContent__TitleTypography-sc-163ikwq-1 OsYHc text-black font-global" data-mds="Typography">
                <div class="DropdownItemContent__ColorChipWrapper-sc-163ikwq-2 fnQXXO">
                  <img src="//image.msscdn.net/images/color_image/color/white.png" size="16" class="ColorChip-sc-wgqqa1-0 dNSmhE">
                </div>
                <div class="DropdownItemContent__ContentColumn-sc-163ikwq-3 gaOHCi">White(화이트)
                  <div class="DropdownItemContent__DeliverySection-sc-163ikwq-6 emjWfR">
                    <div class="DropdownItemContent__DeliveryRow-sc-163ikwq-7 jLlEfo"></div>
                  </div>
                </div>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="py-1 w-full">
      <div class="static-dropdown-menu" data-mds="StaticDropdownMenu">
        <div class="relative w-full h-full cursor-pointer group" data-state="closed" data-mds="DropdownTriggerBox">
          <input type="text" readonly="" class="w-full text-left font-global text-black placeholder-gray-500 placeholder:font-global placeholder:text-[14px] text-[14px] leading-[20px] tracking-[0] font-[400] lang-ja-JP:text-[13px] lang-zh-CN:text-[13px] lang-zh-TW:text-[13px] group-data-[state=closed]:rounded group-data-[state=open]:rounded-t focus:outline-none disabled:text-gray-400 group-data-[disabled]:placeholder-gray-400 disabled:placeholder-gray-400 disabled:bg-gray-100 group-data-[disabled]:text-gray-400 group-data-[disabled]:bg-gray-100 cursor-pointer disabled:cursor-not-allowed group-data-[disabled]:cursor-not-allowed border border-solid border-gray-300 disabled:border-gray-200 py-2 pl-2 pr-8 gtm-click-button" data-index="(not set)" data-section-name="2depth_btn" data-section-index="33" data-brand-id="(not set)" data-button-id="option_type" data-button-name="1사이즈" placeholder="사이즈" data-mds="DropdownTriggerInput">
            <button class="justify-center group-data-[disabled]:cursor-not-allowed focus:outline-none size-4 absolute right-0 top-0 items-center h-full mr-2 hidden group-data-[state=open]:flex" data-mds="IconButton">
              <svg width="100%" height="100%" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="text-gray-500 group-data-[disabled]:text-gray-400" data-mds="IcArrowTop"><path d="M4 12L9.78787 6.21213C9.90503 6.09497 10.095 6.09497 10.2121 6.21213L16 12" class="stroke-current" data-mds="IcArrowTop" vector-effect="non-scaling-stroke"></path></svg>
            </button>
            <button class="justify-center group-data-[disabled]:cursor-not-allowed focus:outline-none size-4 absolute right-0 top-0 items-center h-full mr-2 hidden group-data-[state=closed]:flex" data-mds="IconButton">
              <svg width="100%" height="100%" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg" class="stroke-black text-gray-500 group-data-[disabled]:text-gray-400" data-mds="IcArrowDown" fill="none"><path d="M4 8L9.78787 13.7879C9.90503 13.905 10.095 13.905 10.2121 13.7879L16 8" class="stroke-current" data-mds="IcArrowDown" vector-effect="non-scaling-stroke"></path></svg>
            </button>
        </div>
      </div>
    </div>
  </div>
  <div class="SelectedOption__Container-sc-2fd5hw-3 kpcggq"></div>
</div>

### 공통
- `<br>` 태그는 공백 또는 제거해서 한 줄 문자열로 변환
- 가격 `,` + `원` 제거해서 int 파싱
- `brand_name_en`은 판매자 입력이므로 `A.P.C` vs `A.P.C.` 같은 변동 있을 수 있음 — labellusso/nextzennpack과 동일하게 `mall_brands`에서 사전 매핑 권장


### 카테고리
- `<div class="FixedArea__Container-sc-1puoja0-0 lgLQzE">` 카테고리 추출 `a.gtm-click-brand` 브랜드명 제외
- `raw_scraped_data.category_path` 포맷: `WOMEN > 가방 > 숄더백`
<div class="Category__Wrap-sc-x0nx70-1 ehTERP text-[13px] leading-[18px] tracking-[0] font-[400] lang-ja-JP:text-[12px] lang-zh-CN:text-[12px] lang-zh-TW:text-[12px] text-gray-600 font-global" data-mds="Typography">
  <span class="Category__CategoryList-sc-x0nx70-2 kavYEX"><a href="https://www.musinsa.com/category/017" class="Category__CategoryLink-sc-x0nx70-0 Category__CategoryItem-sc-x0nx70-3 ePyACG jIifdK gtm-click-button" data-index="(not set)" data-section-name="cate_navi" data-section-index="39" data-brand-id="(not set)" data-button-id="category_navi" data-button-name="상품카테고리" data-category-id="1depth" data-category-name="스포츠/레저">스포츠/레저</a></span>
  <span class="Category__CategoryList-sc-x0nx70-2 kavYEX">
    <a href="https://www.musinsa.com/category/017016" class="Category__CategoryLink-sc-x0nx70-0 Category__CategoryItem-sc-x0nx70-3 ePyACG jIifdK gtm-click-button" data-index="(not set)" data-section-name="cate_navi" data-section-index="39" data-brand-id="(not set)" data-button-id="category_navi" data-button-name="상품카테고리" data-category-id="2depth" data-category-name="상의">
      <button class="inline-flex items-center justify-center group-data-[disabled]:cursor-not-allowed focus:outline-none size-3" data-mds="IconButton">
        <svg width="100%" height="100%" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="" data-mds="IcArrowRight"><path d="M7.5 16L13.2879 10.2121C13.405 10.095 13.405 9.90503 13.2879 9.78787L7.5 4" class="stroke-gray-600" data-mds="IcArrowRight" vector-effect="non-scaling-stroke"></path></svg>
      </button>
      상의
    </a>
  </span>
  <span class="Category__CategoryList-sc-x0nx70-2 kavYEX">
    <a href="https://www.musinsa.com/category/017016006" class="Category__CategoryLink-sc-x0nx70-0 Category__CategoryItem-sc-x0nx70-3 ePyACG jIifdK gtm-click-button" data-index="(not set)" data-section-name="cate_navi" data-section-index="39" data-brand-id="(not set)" data-button-id="category_navi" data-button-name="상품카테고리" data-category-id="3depth" data-category-name="피케/카라 티셔츠">
      <button class="inline-flex items-center justify-center group-data-[disabled]:cursor-not-allowed focus:outline-none size-3" data-mds="IconButton">
        <svg width="100%" height="100%" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="" data-mds="IcArrowRight"><path d="M7.5 16L13.2879 10.2121C13.405 10.095 13.405 9.90503 13.2879 9.78787L7.5 4" class="stroke-gray-600" data-mds="IcArrowRight" vector-effect="non-scaling-stroke"></path></svg>
      </button>
      피케/카라 티셔츠
    </a>
  </span>
  <a href="https://www.musinsa.com/category/017016006?brand=briefinggolf" title="브리핑 골프" class="Category__CategoryLink-sc-x0nx70-0 Category__CategoryBrand-sc-x0nx70-4 ePyACG gtm-click-brand" data-index="(not set)" data-section-name="cate_navi" data-section-index="39" data-brand-id="briefinggolf">(브리핑 골프)</a>
</div>

### 이미지 (API에서 수집 — HTML 파싱 X)
- 상세 API의 **커버(`thumbnailImageUrl`) + 갤러리(`goodsImages[].imageUrl`)** 가 곧 상품 페이지 Swiper/Pagination 캐러셀 이미지 전체다.
- `goodsImages`에는 커버 이미지가 포함되지 않으므로 `thumbnailImageUrl`을 첫 번째 이미지로 추가한다.
- 상대경로(`/images/...`)라 앞에 `https://image.msscdn.net` 붙여 절대 URL로 저장.
- 일부 상품은 `goodsImages`가 비어 있는 경우가 있어서, 해당 상품의 경우 커버만 사용.
- 결과는 `raw_json_data.images`에 저장 (커버 = index 0).
- `ace_product_images` 생성은 raw→ace 변환 단계에서 처리
