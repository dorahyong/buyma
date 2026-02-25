# AI를 이용한 바이마 상품관리 크롤러 - okmall
- okmall 쇼핑몰 상품 정보를 웹스크래핑하여 바이마에 상품을 등록하고 관리하는 프로젝트 생성
- 샹품관리란 : 수집부터 변환, 이미지 수집/업로드, 최저가 수집, 상품 등록까지의 일
- 가장 중요한 것은 재고 관리이다. 쇼핑몰에서 품절된 상품이 바이마에 남아있으면 안된다.
- 또 중요한것은 최저가 관리이다. 최저가를 매시간 수집하고 업데이트한다.

## 현황
- 최초 상품관리 
(1) 수집
 - 테이블 : raw_scraped_data
 - unique : source_site, mall_product_id 기준으로 데이터 수집
 - 파일 : okmall_all_brands_collector.py
(2) 변환
 - 테이블 : ace_products, ace_product_options, ace_product_variants
 - 파일 : raw_to_ace_converter.py 
 - 비고 : 현재는 raw_data_id가 있으면 convert 하지 않음.
(3) 이미지
 - 테이블 : ace_product_images
 - 파일 : image_collector_parallel.py, r2_image_uploader.py
(4) 최저가
 - 파일 : buyma_lowest_pridct_collector.py
(5) 등록
 - 파일 : buyma_product_register.py

## 목표1
- 최초 상품관리 후 상품관리
(1) 수집
 - raw_scraped_data 테이블의 unique 를 기준으로 데이터가 있다면 아예 수집하지 않는다.
 - 새로운 상품만 수집한다 -> 변환을 위해서 새로 수집됐다는 표시가 필요할 것 같다.
(2) 변환
 - 기존 데이터는 변환하지 않는다.
  > 쇼핑몰의 기존 데이터의 정보가 변경되어도 무관
  > 재고 및 최저가만 업데이트 되면 다른 정보는 싱크가 맞지 않아도 된다.
 - 새로 수집된 데이터만 변환한다.
(3) 이미지
 - 기존 데이터는 이미지를 수집하지 않느다.
 - 새로 변환된 데이터에 대해서 이미지를 수집한다.
(4) 최저가
 - 최저가를 수집한다.
(5) 등록 
 - 새로 수집된 상품에 대해서는 등록을 한다.

## 목표2
- 재고/가격 업데이트
(1) 바이마에 등록된 상품에 대해서 쇼핑몰에서 재고, 가격 정보를 수집한다.
(2) (1) 에 대해 변환이 필요하면 변환한다.
(3) 최저가를 수집한다
(4) 바이마에 재고와 최저가 부분을 update 한다 (상품api에 reference_number을 같이보내면 update 되는 것으로 알고있음, 확인 필요) 


