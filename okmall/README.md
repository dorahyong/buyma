# AI를 이용한 바이마 상품관리 크롤러 - okmall
- okmall 쇼핑몰 상품 정보를 웹스크래핑하여 바이마에 상품을 등록하고 관리하는 프로젝트 생성

## 목표
- 오케이몰의 상품 데이터를 웹스크래핑 하여 원본 데이터를 db 에 저장하고, 가공하여 ace db 를 확보한다.
- 오케이몰 상품의 모델명을 이용하여 바이마에서 최저가를 확보한다.
- 바이마 api 를 통해 상품을 대량 등록한다.
- 가장 중요한 사항 1 : 오케이몰을 반복적으로 수집하여 바이마에 재고를 업데이트한다.
	> 바이마에서 주문이 들어왔는데 오케이몰에 재고가 없는 경우는 페널티가 발생하기 때문에 절대 일어나서는 안 된다.
- 가장 중요한 사항 2 : 모델명을 이용하여 바이마를 수집하여, 바이마에 등록된 상품의 최저가를 확보 및 업데이트한다.
	> 바이마에 등록된 같은 상품(동일 모델명)에 대해서 최저가로 우리 상품을 업데이트하는 것이 중요하다.

## 할 일 (순서 무관)
(0) 바이마 api를 분석하여 필요한 raw 데이터를 확보하고 바이마 api 에 사용할 수 있는 ace 데이터를 가공하기 위해 db 설계 및 생성한다.
(1) 쇼핑몰 상품데이터 웹스크래핑 하여 원본 데이터 테이블에 저장
(2) 원본 데이터 테이블의 데이터를 가공하여 바이마 api 에 사용할 ace 데이터 테이블에 저장
(3) 상품의 모델명을 이용하여 바이마에서 최저가 확보하여 데이터 저장
(4) 상품의 모델명을 이용하여 w컨셉 에서 검색하여 이미지를 cloudflare 서버에 저장
(5) ace 상품 데이터와 최저가, 이미지 url 이 확보가 되었다면 바이마 api 를 통해 상품 등록 
(6) 오케이몰을 반복적으로 수집하여 바이마 상품의 재고를 동기화
(7) 바이마의 최저가를 반복적으로 확보하여 바이마 상품의 최저가를 업데이트 

## 지침
- 참조 내용으로 파일명을 입력한 경우 모두 README.md 파일(해당 파일)과 같은 경로이다.
- 파일을 확인할 수 없거나 링크를 열어볼 수 없어서 분석할 수 없는 경우에는 반드시 추가 요청을 한다.
- 한국어로 진행한다.
- 소스 파일을 생성할 때에는 주석과 실행 로그를 자세하게 기재한다.
- 근거 없이 추측하지 않는다. (예 : 파일이 안 열리거나 링크에 접속할 수 없거나 db에 접속할 수 없는데 파일명, 테이블명 등으로 추측하기)

## 바이마 정보 (중요)
- REST API : https://specification.personal-shopper-api.buyma.com/api/
- Webhook : https://specification.personal-shopper-api.buyma.com/api/webhook/
- 상품 API : https://specification.personal-shopper-api.buyma.com/api/products_json/
- 재고 API : https://specification.personal-shopper-api.buyma.com/api/products_json/
- API 속도제한 : https://specification.personal-shopper-api.buyma.com/api/rate_limit/
- api 를 정확하게 분석한다.
- api 에 필요한 정보 중 쇼핑몰 상품 html 에서 추가로 수집해야할 정보가 있다면 반드시 알려준다.

### 바이마 상품 API 고정값 (필수)
| 필드명 | 값 | 설명 |
|--------|-----|------|
| buying_area_id | 2002003000 | 구매 지역 ID (고정) |
| shipping_area_id | 2002003000 | 발송 지역 ID (고정) |
| buying_shop_name | {브랜드명}正規販売店 | 구매처명 (브랜드명 + 正規販売店) |
| theme_id | 98 | 테마 ID (고정) |
| duty | included | 관세 정보 (고정) |
| shipping_methods | 369 | 배송 방법 ID (고정) |
| tags | - | 공란 |

### 바이마 상품 API 필수 필드 요약
| 구분 | 필드 |
|------|------|
| 필수 | control, name, comments, brand_id, category_id, price, available_until, buying_area_id, shipping_area_id, images, shipping_methods, _options_, variants |
| 조건부 필수 | id 또는 reference_number 중 하나 |

### 바이마 API 호출 제한
| API 대상 | 제한 | 기간 |
|----------|------|------|
| 전체 API | 5,000회 | 1시간 |
| 상품 API | 2,500회 | 24시간 |

## db 정보
- mysql+pymysql://block:1234@54.180.248.182:3306/buyma

### 브랜드/카테고리 매핑 테이블 (기존 테이블)
- mall_brands : 쇼핑몰별 브랜드 정보 및 바이마 브랜드 ID 매핑
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
AUTO_INCREMENT=513
;

- mall_categories : 쇼핑몰별 카테고리 정보 및 바이마 카테고리 ID 매핑
- 위 테이블들은 이미 okmall과 바이마 간의 매핑 데이터가 입력되어 있음

### raw_scraped_data 테이블 (수집 데이터 테이블)
CREATE TABLE `raw_scraped_data` (
	`id` INT(11) NOT NULL AUTO_INCREMENT,
	`source_site` VARCHAR(50) NULL DEFAULT 'okmall' COLLATE 'utf8mb4_unicode_ci',
	`mall_product_id` VARCHAR(100) NOT NULL COLLATE 'utf8mb4_unicode_ci',
	`brand_name_en` VARCHAR(100) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`brand_name_kr` VARCHAR(100) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`product_name` VARCHAR(255) NULL DEFAULT NULL COMMENT '순수 상품명' COLLATE 'utf8mb4_unicode_ci',
	`p_name_full` TEXT NULL DEFAULT NULL COMMENT '전체 상품명' COLLATE 'utf8mb4_unicode_ci',
	`model_id` VARCHAR(100) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`category_path` VARCHAR(255) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`original_price` DECIMAL(15,2) NULL DEFAULT NULL,
	`raw_price` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '실제 판매가',
	`stock_status` VARCHAR(20) NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`raw_json_data` LONGTEXT NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`product_url` TEXT NULL DEFAULT NULL COLLATE 'utf8mb4_unicode_ci',
	`created_at` TIMESTAMP NOT NULL DEFAULT current_timestamp(),
	`updated_at` TIMESTAMP NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
	PRIMARY KEY (`id`) USING BTREE,
	CONSTRAINT `raw_json_data` CHECK (json_valid(`raw_json_data`))
)
COLLATE='utf8mb4_unicode_ci'
ENGINE=InnoDB
AUTO_INCREMENT=21
;

### raw_scraped_data 테이블 데이터 예시
"id"	"source_site"	"mall_product_id"	"brand_name_en"	"brand_name_kr"	"product_name"	"p_name_full"	"model_id"	"category_path"	"original_price"	"raw_price"	"stock_status"	"raw_json_data"	"product_url"	"created_at"	"updated_at"
"31"	"okmall"	"753625"	"NIKE"	"나이키"	"남성 나이키 드라이 핏 페이서 하프 짚"	"25FW 남성 나이키 드라이 핏 페이서 하프 짚 (FQ2494-010) (M NK DF PACER TOP HZ)"	"FQ2494-010"	"ACTIVITY·LIFE > 액티비티 > 러닝 > 상의"	"79000.00"	"56000.00"	"in_stock"	"{""images"": [""https://okimg.okmall.com/753500/img753625_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/753500/img753625_big_1.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other0_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other1_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other2_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other3_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other4_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other5_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other6_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other7_big.JPG?t=1751260768/okresize/resize/590""], ""options"": [{""color"": ""Black"", ""tag_size"": ""S"", ""real_size"": ""95 전후"", ""option_code"": ""2234429"", ""status"": ""in_stock""}, {""color"": ""Black"", ""tag_size"": ""M"", ""real_size"": ""100 전후"", ""option_code"": ""2234430"", ""status"": ""in_stock""}, {""color"": ""Black"", ""tag_size"": ""L"", ""real_size"": ""105 전후"", ""option_code"": ""2234431"", ""status"": ""in_stock""}], ""season"": ""25FW"", ""ld_json_product"": {""@context"": ""https://schema.org"", ""@type"": ""Product"", ""name"": ""[NIKE]남성 나이키 드라이 핏 페이서 하프 짚 (FQ2494-010)"", ""description"": ""ACTIVITY·LIFE>액티비티>러닝>상의 [NIKE]남성 나이키 드라이 핏 페이서 하프 짚 (FQ2494-010) 56,000원"", ""image"": [""https://okimg.okmall.com/753500/img753625_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/753500/img753625_big_1.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other0_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other1_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other2_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other3_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other4_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other5_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other6_big.JPG?t=1751260768/okresize/resize/590"", ""https://okimg.okmall.com/other753500/img753625_other7_big.JPG?t=1751260768/okresize/resize/590""], ""sku"": 753625, ""mpn"": 753625, ""brand"": {""@type"": ""Brand"", ""name"": ""나이키(NIKE)""}, ""offers"": {""@type"": ""AggregateOffer"", ""offerCount"": 3, ""priceCurrency"": ""KRW"", ""offers"": [{""@type"": ""Offer"", ""sku"": ""2234429"", ""url"": ""https://www.okmall.com/products/view?no=753625"", ""price"": 56000, ""priceCurrency"": ""KRW"", ""availability"": ""https://schema.org/InStock"", ""itemCondition"": ""https://schema.org/NewCondition""}, {""@type"": ""Offer"", ""sku"": ""2234430"", ""url"": ""https://www.okmall.com/products/view?no=753625"", ""price"": 56000, ""priceCurrency"": ""KRW"", ""availability"": ""https://schema.org/InStock"", ""itemCondition"": ""https://schema.org/NewCondition""}, {""@type"": ""Offer"", ""sku"": ""2234431"", ""url"": ""https://www.okmall.com/products/view?no=753625"", ""price"": 56000, ""priceCurrency"": ""KRW"", ""availability"": ""https://schema.org/InStock"", ""itemCondition"": ""https://schema.org/NewCondition""}, {""@type"": ""Offer"", ""sku"": ""2234432"", ""url"": ""https://www.okmall.com/products/view?no=753625"", ""price"": 56000, ""priceCurrency"": ""KRW"", ""availability"": ""https://schema.org/OutOfStock"", ""itemCondition"": ""https://schema.org/NewCondition""}, {""@type"": ""Offer"", ""sku"": ""2234433"", ""url"": ""https://www.okmall.com/products/view?no=753625"", ""price"": 56000, ""priceCurrency"": ""KRW"", ""availability"": ""https://schema.org/OutOfStock"", ""itemCondition"": ""https://schema.org/NewCondition""}], ""lowPrice"": 56000, ""highPrice"": 56000}, ""aggregateRating"": {""@type"": ""AggregateRating"", ""ratingValue"": ""4.6"", ""reviewCount"": 5, ""bestRating"": ""5"", ""worstRating"": ""1""}}, ""rating"": {""@type"": ""AggregateRating"", ""ratingValue"": ""4.6"", ""reviewCount"": 5, ""bestRating"": ""5"", ""worstRating"": ""1""}, ""scraped_at"": ""2026-01-16T17:39:52.880865""}"	"https://www.okmall.com/products/view?no=753625"	"2026-01-16 17:41:24"	"2026-01-16 17:41:24"


## 쇼핑몰 정보
- https://www.okmall.com/

## 수집 관련 정보 1 - 브랜드 리스트
- 브랜드 리스트 url 을 확보한다 

### 오케이몰의 브랜드 리스트 불러오기 URL 
Request URL
https://www.okmall.com/html_data/gnb/V1/top_all_brand_V5.html
Request Method
GET
Status Code
200 OK

### 해당 Request에 대한 Header 정보
accept
text/html, */*; q=0.01
referer
https://www.okmall.com/
sec-ch-ua
"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"
sec-ch-ua-mobile
?0
sec-ch-ua-platform
"Windows"
user-agent
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36
x-csrf-token
dIhjjXdrpJ5lSvx1bjX01uHokM1X4eFcdE3PZxZv
x-requested-with
XMLHttpRequest

### 응답 html
- div.language_brand eng 아래 브랜드를 수집 (<div class="language_brand eng">)
- div.allBrand_list > ul.br_lst > li 형식
<li>
	<a href="/products/list?brand=%EB%B2%A0%EC%9D%B4%ED%94%84%28A+BATHING+APE%29">
		<span class="t_nm">A BATHING APE</span>
		<span class="t_blck">베이프</span>
	</a>
</li>

## 수집 관련 정보 2 - 브랜드 상품 리스트
- 브랜드별 상품 리스트 페이지에서 상세 상품 url을 확보 (product_url)
- Response html 예시 : okmall_niki_product_list.html

### 오케이몰의 나이키 리스트 화면 불러오기 URL 
Request URL
https://www.okmall.com/products/list?brand=%EB%82%98%EC%9D%B4%ED%82%A4%28NIKE%29
Request Method
GET
Status Code
200 OK
Remote Address
211.233.92.10:443
Referrer Policy
strict-origin-when-cross-origin

### 해당 Request에 대한 Header 정보
host
www.okmall.com
referer
https://www.okmall.com/
sec-ch-ua
"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"
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
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36

### 응답 html 중 한개의 상품 
<div class="item_box  first  " data-productno="753625">
                                                    
                                                                                                                        
                            <div class="os_border ">
                                
                                
                                <div class="item_img ">
                                    
                                    

                                                                            <div class="btn_evt_wrap">
                                                                                                                                </div>
                                    
                                    
                                                                            <a target="_blank" href="/products/view?no=753625&amp;item_type=&amp;cate=20009158&amp;uni=M">
                                                                                                                                 
                                                <img width="280" height="325" src="//okimg.okmall.com/753500/img753625_middle6.JPG?t=1751260768" class="pImg" name="ProductImage" style="cursor: pointer; display: inline;">
                                                
                                                                                                    <img width="280" height="325" src="//okimg.okmall.com/753500/img753625_middle6_1.JPG?t=1751260768" img2="//okimg.okmall.com/753500/img753625_middle6_1.JPG?t=1751260768" class="pImg2" name="ProductImage2" style="cursor: pointer; display: inline; opacity: 0;">
                                                                                                                                                                                </a>

                                                
                                                                                                

                                                
                                                
                                                
                                                                                                    <div name="flag_best" class="flag_best  top ">1
                                                        <div class="layer_best" name="layer_best" style="display: none;"></div>
                                                    </div>
                                                
                                                <div class="box_area">
                                                    <ul>
                                                        
                                                                                                                    <li>
                                                                <a href="javascript:void(0);" name="PrdWarehouse" pname="[NIKE]남성 나이키 드라이 핏 페이서 하프 짚 (FQ2494-010)" class="btn_alarm ">입고알림</a>
                                                            </li>
                                                        
                                                        
                                                                                                                    <li>
                                                                <a href="javascript:void(0);" name="PrdCart" pname="[NIKE]남성 나이키 드라이 핏 페이서 하프 짚 (FQ2494-010)" class="btn_quick_cart">장바구니 바로담기</a>
                                                            </li>
                                                                                                                <li>
                                                            
                                                            <a href="javascript:void(0)" name="PrdZZim" class="btn_like ">관심상품</a>
                                                        </li>

                                                        
                                                                                                                                                                                    <li>
                                                                    <span class="zoom_ic" id="viewImageList" img-data="[&quot;\/\/okimg.okmall.com\/753500\/img753625_middle6.JPG?t=1751260768&quot;,&quot;\/\/okimg.okmall.com\/origin753500\/img753625_origin0.JPG?t=1751260768\/okresize\/resize\/292&quot;,&quot;\/\/okimg.okmall.com\/origin753500\/img753625_origin1.JPG?t=1751260768\/okresize\/resize\/292&quot;,&quot;\/\/okimg.okmall.com\/origin753500\/img753625_origin2.JPG?t=1751260768\/okresize\/resize\/292&quot;,&quot;\/\/okimg.okmall.com\/origin753500\/img753625_origin3.JPG?t=1751260768\/okresize\/resize\/292&quot;,&quot;\/\/okimg.okmall.com\/origin753500\/img753625_origin4.JPG?t=1751260768\/okresize\/resize\/292&quot;,&quot;\/\/okimg.okmall.com\/origin753500\/img753625_origin5.JPG?t=1751260768\/okresize\/resize\/292&quot;,&quot;\/\/okimg.okmall.com\/origin753500\/img753625_origin6.JPG?t=1751260768\/okresize\/resize\/292&quot;,&quot;\/\/okimg.okmall.com\/origin753500\/img753625_origin7.JPG?t=1751260768\/okresize\/resize\/292&quot;]"></span>
                                                                </li>
                                                                                                                                                                        </ul>
                                                </div>
                                                                                        
                                </div>
                            </div>
                                                                                                                <ul class="lst_ic_wrap">
                                                                    <li>
                                        <em class=" ic_man ">남성</em>
                                    </li>
                                
                                
                                
                                
                                
                                
                                

                                
                                                            </ul>

                            <div class="val_top ">
                                <p class="item_title" name="shortProductName">
                                    <a href="javascript:void(0);" class="target_brand flex-add">
                                        <span class="prName_Brand">NIKE </span>
                                                                            </a>
                                                                                                                        <a target="_blank" href="/products/view?no=753625&amp;item_type=&amp;cate=20009158&amp;uni=M" name="shortProductNameOver">
                                                                                                                                                                <span class="prName_Season">25FW</span>
                                                                                                        <span class="prName_PrName">남성 나이키 드라이 핏 페이서 하프 짚 (FQ2494-010)(M NK DF PACER TOP HZ)</span>
                                                </a>
                                </p>
                                
                                                                
                                                                    <div class="item_size">
                                        <span class="t_size" name="shortProductOpt">95 , 100 , 105 </span>
                                        

                                        <span class="a_size ">
                                            <img src="https://okimg.okmall.com/zz_design/PC/Common/Icons/icon_arrow_down.png" alt="">
                                                                                    </span>
                                        <div class="ch_size_layer" style="display:none;">
                                                                                        <p>
                                                                                                    구매 가능 사이즈
                                                                                                <a href="javascript:void(0);" class="ch_close">
                                                    <img src="https://okimg.okmall.com/zz_design/PC/Common/Icons/ico_close.png" alt="">
                                                </a>
                                            </p>
                                            <span>95 , 100 , 105 </span>
                                            <div class="ch_size_line">
                                                                                                    <span class="ch_size_txt">오케이몰이 실제 재고 보유 중인 즉시 구매 가능한 사이즈이며, 직접 측정한 정확한 사이즈이기 때문에 97, 103과 같은 수치로 표기될 수 있습니다.</span>
                                                
                                                <span class="ch_size_season">사용 권장 계절 : 봄, 가을</span>
                                            </div>
                                        </div>
                                    </div>
                                                                                                <div style="display: none;" name="detailName" class="brand_detail_layer  no_season  ">
                                    <p class="item_title">
                                                                                            <a target="_blank" href="/products/view?no=753625&amp;item_type=&amp;cate=20009158&amp;uni=M">
                                                                                                                                                                                                                                    <span class="prName_brand">나이키</span>
                                                                                                                                                                                                                                            <span class="prName_PrName">남성 나이키 드라이 핏 페이서 하프 짚 (FQ2494-010)(M NK DF PACER TOP HZ)</span>
                                                            
                                                                                                            </a>
                                    </p>
                                    
                                                                            <p>
                                            <span class="t_size">95 , 100 , 105 </span>
                                        </p>
                                                                    </div>
                            </div>
                            <div class="al_left  ">
                                                                    <div class="icon_group clearfix">
                                        <div class="ic ic_coupon">
                                            <div class="icon">29.1<span class="t_per">%</span></div>
                                        </div>
                                    </div>
                                                                
                                <div class="price_bx">
                                    <div class="t_bx">
                                                                                    <span class="delivery_fee">
                                                <i class="i-delivery"></i>
                                                <em>4,000</em>
                                            </span>
                                        
                                                                                                                                    <span class="orgin_price">79,000<span class="t_won"></span></span>
                                                                                                                                                                    </div>
                                    <div class="b_bx">
                                        <span class="okmall_price" val="56000">56,000<span class="t_won"></span></span>
                                                                                    <span class="r">
                                            <a href="#" name="viewPrice" data-no="753625" data-code="VIPDC_ex">
                                                <span class="btn_help">도움말</span>
                                            </a>
                                        </span>
                                                                            </div>
                                    <div style="display:none;" class="viewPriceLayer layer_ws_add " name="viewPriceLayer"><!-- 가격 안내레이어 영역 --></div>
                                </div>
                            </div>

                            <div class="badge_bx">
                                
                                
                                
                                                            </div>

                                                            <div class="num_group">
                                    
                                                                            <span class="num_score">4.6</span>
                                                                        
                                                                            <span class="num_zzim">382</span>
                                                                    </div>
                            
                                                                        </div>



## 수집 관련 정보 2 - 브랜드 상품 상세 페이지 
- 상세 상품 페이지 접속 
- Response html 예시 : okmall_product.html

### 오케이몰의 상품 상세 화면 불러오기 URL 
Request URL
https://www.okmall.com/products/view?no=753625&item_type=&cate=20009158&uni=M
Request Method
GET
Status Code
200 OK
Remote Address
211.233.92.10:443
Referrer Policy
strict-origin-when-cross-origin

### 해당 Request에 대한 Header 정보
host
www.okmall.com
referer
https://www.okmall.com/products/list?brand=%EB%82%98%EC%9D%B4%ED%82%A4%28NIKE%29
sec-ch-ua
"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"
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
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36

### 응답 html 일부 및 raw 테이블 매핑 정보
- raw_scraped_data 테이블 컬럼 
- 참고 파일(기존에 임의로 러프하게 테스트한 파일) : okmall_niki_collector.py
raw_data_id : pk
source_site : 쇼핑몰명 (okmall)
brand_name_en : 브랜드 영문명 (<script type="application/ld+json"> > @type:Product > brand > name > (괄호 안 문자열) )
brand_name_kr : 브랜드 한글명 (<script type="application/ld+json"> > @type:Product > brand > name > 괄호 앞 문자() )
mall_product_id : 쇼핑몰 상품 id (sku)
product_name : 상품명 (prd_name)
product_full_name : 전체 상품명
model_id : 모델명 (상품명의 첫 번째 괄호 안 문자열)
category_path : 카테고리 경로( BreadcrumbList 내 경로)
season_type : 시즌 타입 (prd_name_season)
original_price : 정가 
sales_price : 판매가 
stock_status : 재고 상태
raw_json_data
  > response 에 json 원본 데이터가 있는지 확인. 있으면 전체 저장
  > [size] type:size, value:쇼핑몰의 사이즈 이름, position:순서 (1부터)
  > [color] type:color, value:쇼핑몰의 컬러 이름, position:순서 (1부터)
  > [real_size]
{
  "real_size": [
    {
      "size_name": "M",
      "info": "오케이몰 실측 사이즈 약 95",
      "measurements": {
        "shoulder": "45cm",
        "chest": "56cm",
        "sleeve_length": "53cm",
        "cuff_width": "9cm",
        "total_length": "70cm",
        "weight": "510g"
      }
    },
    {
      "size_name": "L",
      "info": "오케이몰 실측 사이즈 약 100",
      "measurements": {
        "shoulder": "48cm",
        "chest": "60cm",
        "sleeve_length": "53cm",
        "cuff_width": "9.5cm",
        "total_length": "71cm",
        "weight": "550g"
      }
    }
  ]
}
mall_product_url : 쇼핑몰 상품 url
created_at 
updated_at

- 수집에 사용할 html 부분
<script type="application/ld+json">
        {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "[TOD`S]T 타임리스 쇼퍼 백 스몰(XBWTSBA0200Q8E B999)",
    "description": "LUXURY>여성 가방&ACC>가방>숄더백 [TODS]T 타임리스 쇼퍼 백 스몰(XBWTSBA0200Q8E B999) 1,750,000원",
    "image": [
        "https://okimg.okmall.com/769500/img769979_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/769500/img769979_big_1.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other0_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other1_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other2_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other3_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other4_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other5_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other6_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other7_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other8_big.JPG?t=1751443265/okresize/resize/590",
        "https://okimg.okmall.com/other769500/img769979_other9_big.JPG?t=1751443265/okresize/resize/590"
    ],
    "sku": 769979,
    "mpn": 769979,
    "brand": {
        "@type": "Brand",
        "name": "토즈(TOD`S)"
    },
    "offers": {
        "@type": "Offer",
        "url": "https://www.okmall.com/products/view?cate=20009366&item_type=RANK&no=769979&uni=F",
        "price": 1750000,
        "priceCurrency": "KRW",
        "availability": "https://schema.org/InStock",
        "itemCondition": "https://schema.org/NewCondition"
    }
}
    </script>
            <script type="application/ld+json">
            {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                                            {
                "@type": "ListItem",
                "position": 1,
                        "name": "LUXURY",
                        "item": "https://www.okmall.com/products/list?cate=20008605"
                        }
                             ,                 {
                "@type": "ListItem",
                "position": 2,
                        "name": "여성 가방&amp;ACC",
                        "item": "https://www.okmall.com/products/list?cate=20009363"
                        }
                             ,                 {
                "@type": "ListItem",
                "position": 3,
                        "name": "가방",
                        "item": "https://www.okmall.com/products/list?cate=20009364"
                        }
                             ,                 {
                "@type": "ListItem",
                "position": 4,
                        "name": "숄더백",
                        "item": "https://www.okmall.com/products/list?cate=20009366"
                        }
                        ]
        }
        </script>

<div class="prd_option">
    <div class="brd_name">
        <div class="brd_name_wrap clr brd_img  brd_img ">
            <div class="brand_img_box">
                <a href="/products/list?brand=%ED%86%A0%EC%A6%88%28TOD%60S%29" target="_blank">
                    <span class="brand_tit">TOD`S</span>
                </a>
                <div class="zzim_sns_area">
                    <ul>
                        <li class="zzim " id="inputZZim">78</li>
                    </ul>
                </div>
            </div>
            <h3 id="ProductNameArea">
                <span class="prd_name_season">26SS</span>
                <span class="prd_name">T 타임리스 쇼퍼 백 스몰(XBWTSBA0200Q8E B999)</span>
            </h3>
            <div class="badge">
                <span class="ic_woman">여성</span>
            </div>
        </div>
    </div>

    <div class="prd_bd cus_price_bd  brand_sale_total ">
        <div class="real_price">
            <div class="last_price">
                <span class="l">
                    <span class="name_price name">오케이몰가</span>
                    <span class="price">1,750,000원
                        <span></span>
                    </span>
                </span>
                <span class="r ">
                    <a data-code="VIPDC_ex" data-no="769979" href="#" name="viewPriceInfo">
                        <img src="https://okst.okmall.com/OKOutdoor/PC/Common/Icons/20140508/icon_help.gif"></a>
                </span>
                <div class="viewPriceLayer  layer_ws_add" name="viewPriceLayer"
                     style="display:none;width:270px;margin-top:13px;">
                </div>
                <div class="icon_group clearfix">
                    <div class="ic ic_coupon">
                        <div class="icon">38.4<span class="t_per">%</span></div>
                    </div>
                </div>
            </div>
            <div class="value_price">
                <span class="l">
                    <span class="name_price name">정찰 판매가</span>
                    <span class="price">2,840,000원
                        <span></span>
                    </span>
                </span>
            </div>
            <div class="value_price2">
                <span class="l">
                    <span class="name_price2 name">회원 맞춤가</span>
                    <span class="price">1,715,000원
                        <span></span>
                    </span>
                </span>
            </div>
        </div>
    </div>
</div>