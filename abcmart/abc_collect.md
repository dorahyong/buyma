### ABC-MART / GRANDSTAGE (a-rt.com)
같은 플랫폼(ABC마트 코리아) 내 채널 2개, 수집기는 1개이며 `tChnnlNo`로만 분기합니다.
`source_site`는 채널별로 각각 저장합니다.

### 채널
| 몰 | 도메인 | tChnnlNo | source_site |
|---|---|---|---|
| ABC마트 | abcmart.a-rt.com | 10001 | abcmart |
| 그랜드스테이지 | grandstage.a-rt.com | 10002 | grandstage |
- 실행: `python abcmart/abc_collector.py --channel abcmart` / `--channel grandstage`
- 이미지 호스트는 공통: `image.a-rt.com`
- 브랜드 번호(`brandNo`) 체계도 공통 — 같은 브랜드는 두 채널에서 같은 번호

### 브랜드 목록
- ABC마트: https://abcmart.a-rt.com/ GNB `BRAND` 메뉴 → @abc_brand_list.html
- 그랜드스테이지: https://grandstage.a-rt.com/ GNB `BRAND` 메뉴 → @abc_grandstage_brand_list.html
- `li.brandSearchitem` 안에 있는 것만 수집

| ace 필드 | 출처 CSS 선택자 | 예시 |
|---|---|---|
| `mall_brand_id` | `li.brandSearchitem > a[href]` 의 `brandNo` 쿼리 | `000003` |
| `mall_brand_name_en` | `li.brandSearchitem span.brand-name.eng` | `ADIDAS` |
| `mall_brand_name` | `li.brandSearchitem span.brand-name.kor` | `아디다스` |
| `mall_brand_url` | `a[href]` (도메인 붙여서 절대경로화) | `/product/brand/page?brandNo=000003&tChnnlNo=10001` |

```html
<li class="brandSearchitem">
  <a href="/product/brand/page?brandNo=000003&amp;tChnnlNo=10001" onclick="">
    <span class="brand-name eng">ADIDAS</span>
    <span class="brand-name kor">아디다스</span>
  </a>
  <button type="button" class="btn-brand-favorite" id="000003" onclick="btnIntrBrand(this)"></button>
</li>
```

### 참고사항
- 인기브랜드 배너(`div.hot-brands-wrap` 하위)는 제외. 같은 `brandNo`를 사용하지만 브랜드명이 없는 이미지 링크라, 포함하면 빈 브랜드명이 저장됨.
- 같은 브랜드가 파일에 2번 존재함 — 영문(`#engList`)과 한글 (`#korList`) 목록에 각각 있으므로 `brandNo` 기준으로 dedup 필요.
  (단순히 `brandSearchitem`개수를 세면 abcmart 255개 / grandstage 88개로 실제의 2배가 집계됨)
- 두 채널은 페이지 좌우 배치만 다름(ABC=왼쪽,GS=오른쪽). `li.brandSearchitem` 선택자는 동일하므로 파싱에 영향 없음

---

# 상품 리스트 페이지
- @abc_list.html
- https://abcmart.a-rt.com/product/brand/page/main?brandNo=000003&page=1
- https://grandstage.a-rt.com/product/brand/page/main?brandNo=000003&page=1
- 리스트 페이지의 상품 목록은 JS로 로드됨
- 실제 수집 대상: `GET /display/search-word/result/list`

## General
Request URL
https://abcmart.a-rt.com/product/brand/page/main?brandNo=000003&page=1
Request Method
GET
Status Code
200 OK
Remote Address
203.248.22.246:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers
cache-control
no-cache
content-language
ko
content-type
text/html; charset=UTF-8
date
Mon, 20 Jul 2026 12:59:51 GMT
server
ABCMART
transfer-encoding
chunked
x-content-type-options
nosniff
x-frame-options
SAMEORIGIN
x-xss-protection
1; mode=block

## Request Headers
accept
text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
cache-control
no-cache
connection
keep-alive
cookie
WMONID=-RMcD081CYW; JSESSIONID=caab-ndOz6yJ6_LmgoK9zUqHGLfTzhI8K9I1-qIjtP8Pj1kNERhXVcXlYluh; _fbp=fb.1.1784160788588.203841672893640819; _fwb=43ZF2VEYMEBezuC5EQvEUP.1784160788630; _kmpid=km|abcmart.a-rt.com|1784160788644|75159e60-3ef5-4292-bc73-5cd27276fa09; _kmpid=km|a-rt.com|1784160788644|75159e60-3ef5-4292-bc73-5cd27276fa09; _ga=GA1.1.2021147168.1784160789; _gcl_aw=GCL.1784552236.Cj0KCQjwjvfSBhDpARIsAEiOpSvaqqN0j-2UktQZM5zjXFEDsmSLkgsVYcTU615vvGPrFMymlWsd3JUaAg5PEALw_wcB; _gcl_gs=2.1.k1$i1784552235$u77064515; _TBS_NAUIDA_1085=49fb0879611e8fb01e00b00d7371b580#1784160788#1784552236#4; _TBS_AUIDA_1085=definedvalue:4; _TBS_ASID_1085=d037500a09a5da530324043886a58be4; _gcl_au=1.1.505523726.1784160788.-.-.1784552236.2084290343.1784552237.1784552236; cDomain=abcmart.a-rt.com; __rtbh.uid=%7B%22eventType%22%3A%22uid%22%2C%22id%22%3A%22unknown%22%2C%22expiryDate%22%3A%222027-07-20T12%3A59%3A41.033Z%22%7D; __rtbh.lid=%7B%22eventType%22%3A%22lid%22%2C%22id%22%3A%226xwdmDmZRfnT2NN1wMER%22%2C%22expiryDate%22%3A%222027-07-20T12%3A59%3A41.033Z%22%7D; wcs_bt=s_1a84e8fc3413:1784552381; _ga_1TNDXE1VZN=GS2.1.s1784550127$o3$g1$t1784552381$j53$l0$h0; _ga_LCF0H8N8VP=GS2.1.s1784550127$o4$g1$t1784552381$j53$l0$h0; _TBS_AEX_1085=3; cto_bundle=keFgJ19HcFoyT3BRYmZzcDFzckhzNzdITVMzRDFlZiUyRiUyRmY1WHJNNVprRE5pZ3Y5czFrZnVQMTZhVDlCTVhsdEs1dGR3bUg5UWs4WWdJSWI1ZDhuQ3lkd0NtVEhRUUVOWGh0elV6Y3Rvbmdpb1JDeDhXTVhtelFCVndVOTNzejJhYm5ldFlFbUQzdFRaJTJCVXNuMjhWTXpaR2ozY2clM0QlM0Q
host
abcmart.a-rt.com
pragma
no-cache
referer
https://abcmart.a-rt.com/product/new?prdtNo=1010120358&rcmdProdYn=Y&page=1
sec-ch-ua
"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"
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
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36

### 상품 목록 API
```
GET https://{도메인}/display/search-word/result/list
```
필수 파라미터 (검색폼 직렬화값):

| 파라미터 | 값 | 설명 |
|---|---|---|
| `searchPageGubun` | `brsearch` | **브랜드 검색 모드 (이게 없으면 결과 0)** |
| `searchPageType` | `brand` | |
| `brandNo` | `000003` | 브랜드 번호 |
| `searchBrandNo` | `000003` | brandNo와 동일값 |
| `channel` | `10001`/`10002` | **`tChnnlNo` 아님 주의.** abcmart=10001 / grandstage=10002 |
| `page` | `1`,`2`,… | 페이지 |
| `perPage` | `30` | 페이지당 |
| `pageColumn` | `4` | |
| `tabGubun` | `total` | |
| `deviceCode` | `10000` | |
| `firstSearchYn` | `Y` | |
| `searchRcmdYn` | `N` | 추천상품 섞임 방지 |
| `brandPrdtArtDispYn` | `Y` | |

- 헤더: `X-Requested-With: XMLHttpRequest`, `Referer: {브랜드 랜딩 URL}`
- 응답에 **두 채널 총개수** 포함: `#GROUP_COUNT_CHNNL_NO_10001`, `#GROUP_COUNT_CHNNL_NO_10002` (hidden input value)
    - 예: 아디다스 → abcmart 424 / grandstage 1057
- 반환 상품은 `channel`로 지정한 채널 것만 (`data-product-chnnl-no` 로 확인 가능)

### 상품 카드 구조 (`li.prod-item`)
```html
<li class="col-list-item prod-item smart-search-product-item selling" data-product-no="1010128734">
  <div class="prod-info-wrap" data-product-chnnl-no="10001">
    <span class="prod-brand">아디다스</span>
    <span class="prod-name">
      <span class="badge-gender" aria-label="공용">공용</span>
      핸드볼 스페지알
    </span>
    <span class="prod-price"><span class="price-cost">149,000</span><span class="price-unit">원</span></span>
  </div>
</li>
```

### 목록에서 추출
| ace 필드 | 출처 (목록 API HTML) | 예시 |
|---|---|---|
| `mall_product_id` | `li.prod-item[data-product-no]` | `1010128734` |
| `product_name` | `span.prod-name` (badge-gender 텍스트 제외) | `핸드볼 스페지알` |
| `raw_price` | `span.prod-price span.price-cost` (`,`·`원` 제거) | `149000` |
| 성별(참고) | `span.prod-name span.badge-gender` | `공용` (정확한 카테고리는 상세 breadcrumb에서) |
| `category` | **목록엔 없음** → 상세페이지 breadcrumb에서 (성별 포함) |

- 목록에서 `mall_product_id`만 확보 → 상세(`/product/info`, breadcrumb)로 나머지 채움
- **재고/품절**: `li.prod-item.selling`(판매중) vs 품절 클래스로 구분되나, 정확한 사이즈별 재고는 `/product/info`에서

### 페이지네이션
- **마지막 페이지 감지: `page`를 1씩 늘리며 호출 → 상품 0개 나오면 종료** (page 100 등 초과 시 빈 응답)
- 총 개수로 미리 계산도 가능: `GROUP_COUNT_CHNNL_NO_{채널}` ÷ perPage(30) = 총 페이지수
- 예: 아디다스 abcmart 424개 → 15페이지 (page15에 4개)
```html
<div id="pagingDiv" class="pagination-wrap">
  <div>
    <ol class="pagination-list">
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num selected" pagenum="1">1</button></li>
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="2">2</button></li>	
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="3">3</button></li>		
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="4">4</button></li>
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="5">5</button></li>		
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="6">6</button></li>
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="7">7</button></li>
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="8">8</button></li>
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="9">9</button></li>
      <li class="pagination-item" name="li_page"><button type="button" class="btn-page btn-page-num " pagenum="10">10</button></li>		
      <li class="pagination-item"><button type="button" class="btn-page next" pagenum="" id="btn_next">다음 페이지로</button></li>	
      <li class="pagination-item"><button type="button" class="btn-page last" id="btn_last" pagenum="15">마지막 페이지로</button></li>
    </ol>
  </div>
</div>
```
---

# 상품 상세 페이지
- @abc_product.html

## General
Request URL
https://abcmart.a-rt.com/product/new?prdtNo=1010122563&page=1
Request Method
GET
Status Code
200 OK
Remote Address
203.248.22.246:443
Referrer Policy
strict-origin-when-cross-origin

## Response headers
cache-control
no-cache, no-store, max-age=0, must-revalidate
content-language
ko
content-type
text/html; charset=UTF-8
date
Tue, 21 Jul 2026 02:03:11 GMT
expires
0
pragma
no-cache
server
ABCMART
transfer-encoding
chunked
x-content-type-options
nosniff
x-frame-options
SAMEORIGIN
x-xss-protection
1; mode=block

## Request Headers
accept
text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7
accept-encoding
gzip, deflate, br, zstd
accept-language
ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7
cache-control
no-cache
connection
keep-alive
cookie
WMONID=-RMcD081CYW; JSESSIONID=caab-ndOz6yJ6_LmgoK9zUqHGLfTzhI8K9I1-qIjtP8Pj1kNERhXVcXlYluh; _fbp=fb.1.1784160788588.203841672893640819; _fwb=43ZF2VEYMEBezuC5EQvEUP.1784160788630; _kmpid=km|abcmart.a-rt.com|1784160788644|75159e60-3ef5-4292-bc73-5cd27276fa09; _kmpid=km|a-rt.com|1784160788644|75159e60-3ef5-4292-bc73-5cd27276fa09; _ga=GA1.1.2021147168.1784160789; _gcl_aw=GCL.1784552236.Cj0KCQjwjvfSBhDpARIsAEiOpSvaqqN0j-2UktQZM5zjXFEDsmSLkgsVYcTU615vvGPrFMymlWsd3JUaAg5PEALw_wcB; _gcl_gs=2.1.k1$i1784552235$u77064515; cDomain=abcmart.a-rt.com; _TBS_NAUIDA_1085=49fb0879611e8fb01e00b00d7371b580#1784160788#1784598236#5; _TBS_AUIDA_1085=definedvalue:5; _TBS_ASID_1085=080368a511efdb0d2c030f40f870c96a; __rtbh.uid=%7B%22eventType%22%3A%22uid%22%2C%22id%22%3A%22unknown%22%2C%22expiryDate%22%3A%222027-07-21T02%3A01%3A27.145Z%22%7D; __rtbh.lid=%7B%22eventType%22%3A%22lid%22%2C%22id%22%3A%226xwdmDmZRfnT2NN1wMER%22%2C%22expiryDate%22%3A%222027-07-21T02%3A01%3A27.145Z%22%7D; wcs_bt=s_1a84e8fc3413:1784599288; _ga_LCF0H8N8VP=GS2.1.s1784598233$o5$g1$t1784599288$j28$l0$h0; RECENT_INFOprodP=%5B%7B%22type%22%3A%22P%22%2C%22name%22%3A%22%ED%81%B4%EB%9D%BC%EC%9A%B0%EB%93%9C%ED%8F%BC%20%ED%94%8C%EB%A0%89%EC%8A%A4%20-%20%EB%9D%BC%EC%9A%B4%EC%A7%80%20%EB%9E%98%ED%94%BC%EB%93%9C%ED%95%8F%22%2C%22id%22%3A%221010122563%22%2C%22price%22%3A79000%2C%22salePrice%22%3A63000%2C%22brandName%22%3A%22%EC%95%84%EB%94%94%EB%8B%A4%EC%8A%A4%22%2C%22imgUrl%22%3A%22https%3A%2F%2Fimage.a-rt.com%2Fart%2Fproduct%2F2026%2F02%2F13104_1770630446120.jpg%22%2C%22soldOutYn%22%3A%22N%22%2C%22displayName%22%3A%22%EB%82%A8%EC%84%B1%22%2C%22discountRate%22%3A20%2C%22sellStatCode%22%3A%2210001%22%2C%22dispYn%22%3A%22Y%22%2C%22relisTodoYn%22%3A%22N%22%7D%2C%7B%22type%22%3A%22P%22%2C%22name%22%3A%22%EC%95%8C%ED%8C%8C%EB%A6%AC%EC%8A%A4%ED%8F%B0%EC%8A%A4%20%EC%8A%AC%EB%9D%BC%EC%9D%B4%EB%93%9C%22%2C%22id%22%3A%221010112078%22%2C%22price%22%3A59000%2C%22salePrice%22%3A47000%2C%22brandName%22%3A%22%EC%95%84%EB%94%94%EB%8B%A4%EC%8A%A4%22%2C%22imgUrl%22%3A%22https%3A%2F%2Fimage.a-rt.com%2Fart%2Fproduct%2F2025%2F01%2F55619_1737450732443.jpg%22%2C%22soldOutYn%22%3A%22N%22%2C%22displayName%22%3A%22%EA%B3%B5%EC%9A%A9%22%2C%22discountRate%22%3A20%2C%22sellStatCode%22%3A%2210001%22%2C%22dispYn%22%3A%22Y%22%2C%22relisTodoYn%22%3A%22N%22%7D%2C%7B%22type%22%3A%22P%22%2C%22name%22%3A%22%EC%95%84%EB%94%94%EC%8A%A4%ED%83%80%20%EC%BB%A8%ED%8A%B8%EB%A1%A4%205%20EL%20%EC%B9%A0%EB%93%9C%EB%9F%B0%22%2C%22id%22%3A%221010128955%22%2C%22price%22%3A89000%2C%22salePrice%22%3A89000%2C%22brandName%22%3A%22%EC%95%84%EB%94%94%EB%8B%A4%EC%8A%A4%22%2C%22imgUrl%22%3A%22https%3A%2F%2Fimage.a-rt.com%2Fart%2Fproduct%2F2026%2F07%2F66691_1783584900833.jpg%22%2C%22soldOutYn%22%3A%22N%22%2C%22displayName%22%3A%22%ED%82%A4%EC%A6%88%22%2C%22discountRate%22%3A0%2C%22sellStatCode%22%3A%2210001%22%2C%22dispYn%22%3A%22Y%22%2C%22relisTodoYn%22%3A%22N%22%7D%2C%7B%22type%22%3A%22P%22%2C%22name%22%3A%22%EC%95%84%EB%94%94%EC%8A%A4%ED%83%80%20%EC%BB%A8%ED%8A%B8%EB%A1%A4%205%20EL%20%EC%9D%B8%ED%8E%80%ED%8A%B8%22%2C%22id%22%3A%221010128958%22%2C%22price%22%3A79000%2C%22salePrice%22%3A79000%2C%22brandName%22%3A%22%EC%95%84%EB%94%94%EB%8B%A4%EC%8A%A4%22%2C%22imgUrl%22%3A%22https%3A%2F%2Fimage.a-rt.com%2Fart%2Fproduct%2F2026%2F07%2F47945_1783645465005.jpg%22%2C%22soldOutYn%22%3A%22N%22%2C%22displayName%22%3A%22%ED%82%A4%EC%A6%88%22%2C%22discountRate%22%3A0%2C%22sellStatCode%22%3A%2210001%22%2C%22dispYn%22%3A%22Y%22%2C%22relisTodoYn%22%3A%22N%22%7D%2C%7B%22type%22%3A%22P%22%2C%22name%22%3A%22%ED%81%B4%EB%9D%BC%EC%9A%B0%EB%93%9C%ED%8F%BC%20%ED%94%8C%EB%A0%89%EC%8A%A4%20-%20%EB%9E%98%ED%94%BC%EB%93%9C%ED%95%8F%22%2C%22id%22%3A%221010129058%22%2C%22price%22%3A89000%2C%22salePrice%22%3A89000%2C%22brandName%22%3A%22%EC%95%84%EB%94%94%EB%8B%A4%EC%8A%A4%22%2C%22imgUrl%22%3A%22https%3A%2F%2Fimage.a-rt.com%2Fart%2Fproduct%2F2026%2F07%2F42204_1783644776055.jpg%22%2C%22soldOutYn%22%3A%22N%22%2C%22displayName%22%3A%22%EB%82%A8%EC%84%B1%22%2C%22discountRate%22%3A0%2C%22sellStatCode%22%3A%2210001%22%2C%22dispYn%22%3A%22Y%22%2C%22relisTodoYn%22%3A%22N%22%7D%2C%7B%22type%22%3A%22P%22%2C%22name%22%3A%22VL%20%EC%BD%94%ED%8A%B8%20FC%22%2C%22id%22%3A%221010122061%22%2C%22price%22%3A89000%2C%22salePrice%22%3A71000%2C%22brandName%22%3A%22%EC%95%84%EB%94%94%EB%8B%A4%EC%8A%A4%22%2C%22imgUrl%22%3A%22https%3A%2F%2Fimage.a-rt.com%2Fart%2Fproduct%2F2026%2F01%2F37615_1768465759555.jpg%22%2C%22soldOutYn%22%3A%22N%22%2C%22displayName%22%3A%22%EA%B3%B5%EC%9A%A9%22%2C%22discountRate%22%3A20%2C%22sellStatCode%22%3A%2210001%22%2C%22dispYn%22%3A%22Y%22%2C%22relisTodoYn%22%3A%22N%22%7D%5D; cto_bundle=0PG9519HcFoyT3BRYmZzcDFzckhzNzdITVM1RHNzUDUlMkI5YVZWa09kejkzYXg3N0tFblolMkY4VW5PZkFQaGRQbU9QaVlZQWFiQUp3SFE0NnI0YU1KUjlVYldaUDVqaWdSVndNTkphRVh1TXBidWpnTzVmNDN4R3V3RTNDa3ZyVnZCUGYzQ0l6UDBrN1o4SzVvVVQ2MVpSMUEwdUF3JTNEJTNE; _gcl_au=1.1.505523726.1784160788.-.-.1784552236.2084290343.1784552237.1784599290; _ga_1TNDXE1VZN=GS2.1.s1784598151$o5$g1$t1784599290$j26$l0$h0
host
abcmart.a-rt.com
pragma
no-cache
referer
https://abcmart.a-rt.com/product/brand/page/main?brandNo=000003&page=8
sec-ch-ua
"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"
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
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36

### 기본 정보 추출
- `div.detail-box-right.box-type2 > div.detail-box-header`에서 추출
- 브랜드명 : `<input type="hidden" id="brandName" value="아디다스">` value 값

| ace 필드 | 출처 CSS 선택자| HTML 예시 |
|---|----------------------------------------------------|---------------------------|
| `product_name` | `div.prod-name`| `클라우드폼 플렉스 - 라운지 래피드핏`|
| `original_price` | `span.price-normal-cost`| `79,000`|
| `raw_price` | `span.price-cost data-product="sell-price-amount"` | `63,000` |
| `model_id` | `data-product="style-code"`| `HQ2569` |
| `delivery_info` | div.art-delivery-type data-product-delivery="free" | `(무료배송)` (raw 저장 안 해도 됨)  |


### 상품 옵션
- 옵션은 HTML(`<ul data-product="option-list">`)이 아니라 `GET /product/info?prdtNo={prdtNo}`의 `productOption[]`에서 조회한다.

### productOption[] 필드
| 필드 | 뜻 |
|---|---|
| `optnName` | 1번째 축 (사이즈 또는 색상) |
| `addOptn2Text` | 2번째 축 (사이즈). **없으면 null/빈문자** |
| `totalStockQty` | 재고 수량 |
| `sellStatCode` | 10001=판매중 / 10002=품절 |
| `prdtOptnNo` | 옵션 번호 |

### 차원 판별 (상품종류 무관, `addOptn2Text` 유무로만)
- `addOptn2Text` 없음 → 1차원 옵션 (`optnName` = 사이즈)
- `addOptn2Text` 있음 → 2차원 옵션 (`optnName` = 색상, `addOptn2Text` = 사이즈)
- `FREE`도 일반 옵션과 동일하게 처리

### 저장 시 제외 규칙
**`sellStatCode=10001(판매중)` 이면서 `totalStockQty=0` 인 옵션이 존재한다.**
→ **재고까지 봐야 함.** 둘 다 만족해야 저장:
- `sellStatCode == '10001'` **그리고** `totalStockQty > 0`
- 나머지(품절, 재고0)는 제외

> 참고: `totalStockQty=999`는 "재고 무제한/미관리" 센티넬 값일 수 있음(정상, 저장 대상).

### 소재 / 원산지
- 소재와 원산지는 `GET /product/info/detail?prdtNo={prdtNo}`의 `notice[]`에서 조회한다.
  (`#product-detail-notice`는 JS로 렌더링되며, 원본 HTML과 `/product/info`에는 포함되지 않음.)

#### 추출 규칙
- **소재**: `infoNotcName == '소재'` → `prdtAddInfo`
- **원산지**: `infoNotcName == '제조국'` → `prdtAddInfo`
- `내용없음`, `상세페이지참조` 등 의미 없는 값은 제외

#### 저장
- `material`
- `origin`
- `composition` (`소재`, `원산지`)
- 제조자, A/S 등 기타 고시 항목은 저장하지 않음.
---

### 카테고리
- 채널별 취급 카테고리가 다르므로 각각 수집한다.
- `#prdtCtgrCrumb ol.breadcrumb-list > li.crumb`를 순서대로 읽어 `>`로 연결해 `full_path`를 생성한다.
#### 추출 규칙
- `li.crumb.home`(HOME)은 제외
- 텍스트만 있는 `li.crumb` → 그대로 사용 (예: `KIDS`)
- `select`가 있는 `li.crumb` → `option[selected]`의 텍스트만 사용
예시
```
KIDS>신발>스니커즈>라이프스타일
```

```html
<div class="breadcrumb-wrap" id="prdtCtgrCrumb">
    <ol class="breadcrumb-list">
        <li class="crumb home"><a href="/">HOME</a></li><!-- 버림 -->
        <li class="crumb">KIDS</li>
        <li class="crumb">
            <span class="ui-select breadcrumb">
                <select class="area-same-level-category" id="ui-id-29" style="display: none;">
                  <option selected="" data-ctgr-no="1000000441">신발</option>
                  <option data-ctgr-no="1000000442">의류</option>
                  <option data-ctgr-no="1000000443">잡화</option>
                  <option data-ctgr-no="1000000444">용품</option>
                </select>
                <span tabindex="0" id="ui-id-29-button" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-owns="ui-id-29-menu" aria-haspopup="true" class="ui-selectmenu-button ui-button ui-widget ui-selectmenu-button-closed ui-corner-all" aria-activedescendant="ui-id-30" aria-labelledby="ui-id-30" aria-disabled="false" style="width: 55.3672px;">
                  <span class="ui-selectmenu-icon ui-icon ui-icon-triangle-1-s"></span>
                </span>
              <span class="ui-selectmenu-text">신발</span>
            </span>
        </li>
        <li class="crumb">
            <span class="ui-select breadcrumb">
                <select class="area-same-level-category" id="ui-id-34" style="display: none;">
                    <option selected="" data-ctgr-no="1000000245">스니커즈</option>
                    <option data-ctgr-no="1000000249">스포츠</option>
                    <option data-ctgr-no="1000000254">구두</option>
                    <option data-ctgr-no="1000000260">샌들</option>
                    <option data-ctgr-no="1000000410">캐주얼</option>
                    <option data-ctgr-no="1000000266">부츠</option>
                </select>
              <span tabindex="0" id="ui-id-34-button" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-owns="ui-id-34-menu" aria-haspopup="true" class="ui-selectmenu-button ui-button ui-widget ui-selectmenu-button-closed ui-corner-all" aria-activedescendant="ui-id-35" aria-labelledby="ui-id-35" aria-disabled="false" style="width: 94.7266px;">
                <span class="ui-selectmenu-icon ui-icon ui-icon-triangle-1-s"></span>
              </span>
              <span class="ui-selectmenu-text">스니커즈</span>
            </span>
        </li>

        <li class="crumb">
            <span class="ui-select breadcrumb">
                <select class="area-same-level-category" id="ui-id-41" style="display: none;">
                  <option selected="" data-ctgr-no="1000000762">라이프스타일</option>
                  <option data-ctgr-no="1000000246">스니커즈</option>
                  <option data-ctgr-no="1000000247">캔버스/단화</option>
                  <option data-ctgr-no="1000000248">슬립온</option>
                  <option data-ctgr-no="1000000763">플랫폼</option>
                  <option data-ctgr-no="1000000675">뮬</option>
                  <option data-ctgr-no="1000000761">메리제인</option>
                  <option data-ctgr-no="1000000684">걸음마신발</option>
                  <option data-ctgr-no="1000000730">워크화</option>
                  <option data-ctgr-no="1000000726">기타</option>
                  <option data-ctgr-no="1000000775">라이트닝</option>
                </select>
                <span tabindex="0" id="ui-id-41-button" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-owns="ui-id-41-menu" aria-haspopup="true" class="ui-selectmenu-button ui-button ui-widget ui-selectmenu-button-closed ui-corner-all" aria-activedescendant="ui-id-42" aria-labelledby="ui-id-42" aria-disabled="false" style="width: 119.086px;">
                  <span class="ui-selectmenu-icon ui-icon ui-icon-triangle-1-s"></span>
                </span>
              <span class="ui-selectmenu-text">라이프스타일</span>
            </span>
        </li>
    </ol>
</div>
```
---

### 이미지
- `<div class="detail-box-left">` 아래 `<li class="gallery-thumb"> > img` 썸네일 수집
- nextzennpack처럼 자체 수집해서 `ace_product_images` 테이블에 INSERT
```html
<div class="detail-thumbs-wrap">
    <ul class="detail-thumbs-list" data-product="image-preview-list-large">
        <li class="gallery-thumb active">
            <button type="button" class="btn-dialog" data-product-image="large">
              <a href="javascript:;">
                  <img onerror="javascript:abc.biz.product.common.util.image.noImage(this);" src="https://image.a-rt.com/art/product/2026/04/31753_1775795610520.jpg?shrink=580:580" alt="MAIN">
              </a>
            </button>
        </li>
        <li class="gallery-thumb">
            <button type="button" class="btn-dialog" data-product-image="large">
                <a href="javascript:;">
                    <img onerror="javascript:abc.biz.product.common.util.image.noImage(this);" src="https://image.a-rt.com/art/product/2026/04/08453_1775795610695.jpg?shrink=580:580" alt="SUB 1">
                </a>
            </button>
        </li>
        <li class="gallery-thumb">
            <button type="button" class="btn-dialog" data-product-image="large">
                <a href="javascript:;">
                    <img onerror="javascript:abc.biz.product.common.util.image.noImage(this);" src="https://image.a-rt.com/art/product/2026/04/27381_1775795610819.jpg?shrink=580:580" alt="SUB 2">
                </a>
            </button>
        </li>
        <li class="gallery-thumb">
            <button type="button" class="btn-dialog" data-product-image="large">
                <a href="javascript:;">
                    <img onerror="javascript:abc.biz.product.common.util.image.noImage(this);" src="https://image.a-rt.com/art/product/2026/04/66829_1775795610938.jpg?shrink=580:580" alt="SUB 3">
                </a>
            </button>
        </li>
        <li class="gallery-thumb">
            <button type="button" class="btn-dialog" data-product-image="large">
                <a href="javascript:;">
                    <img onerror="javascript:abc.biz.product.common.util.image.noImage(this);" src="https://image.a-rt.com/art/product/2026/04/88215_1775795611062.jpg?shrink=580:580" alt="SUB 4">
                </a>
            </button>
        </li>
    </ul>
</div>
```
---

### 공통
- `<br>` 태그는 공백 또는 제거해서 한 줄 문자열로 변환
- 가격 `,` + `원` 제거해서 int 파싱
- `brand_name_en`은 판매자 입력이므로 `A.P.C` vs `A.P.C.` 같은 변동 있을 수 있음 — labellusso/nextzennpack과 동일하게 `mall_brands`에서 사전 매핑 권장

