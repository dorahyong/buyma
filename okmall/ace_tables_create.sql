-- =====================================================
-- ACE 테이블 생성 스크립트
-- 바이마(BUYMA) API 연동을 위한 가공 데이터 저장용
--
-- 작성일: 2026-01-18
-- 수정일: 2026-01-18
-- 목적: raw_scraped_data에서 수집한 원본 데이터를
--       바이마 API 형식에 맞게 가공하여 저장
--
-- 참고: 브랜드/카테고리 매핑은 기존 테이블 사용
--       - mall_brands : 쇼핑몰 브랜드 → 바이마 브랜드 ID 매핑
--       - mall_categories : 쇼핑몰 카테고리 → 바이마 카테고리 ID 매핑
-- =====================================================

-- 데이터베이스 사용
USE buyma;

-- =====================================================
-- 바이마 API 고정값 정보 (참고용 주석)
-- =====================================================
-- buying_area_id : 2002003000 (고정)
-- shipping_area_id : 2002003000 (고정)
-- buying_shop_name : {브랜드명}正規販売店 (브랜드별 동적 생성)
-- theme_id : 98 (고정)
-- duty : included (고정)
-- shipping_methods : 369 (고정)
-- tags : 공란
-- =====================================================

-- =====================================================
-- 1. 배송 설정 테이블
-- 바이마 배송 관련 기본 설정값 (고정값 저장)
-- =====================================================
CREATE TABLE IF NOT EXISTS `shipping_config` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `config_name` VARCHAR(100) NOT NULL COMMENT '설정명',
    `buying_area_id` VARCHAR(20) NOT NULL DEFAULT '2002003000' COMMENT '구매 지역 ID (고정: 2002003000)',
    `shipping_area_id` VARCHAR(20) NOT NULL DEFAULT '2002003000' COMMENT '발송 지역 ID (고정: 2002003000)',
    `shipping_method_id` INT(11) NOT NULL DEFAULT 369 COMMENT '배송 방법 ID (고정: 369)',
    `theme_id` INT(11) NOT NULL DEFAULT 98 COMMENT '테마 ID (고정: 98)',
    `duty` VARCHAR(20) NOT NULL DEFAULT 'included' COMMENT '관세 정보 (고정: included)',
    `is_default` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '기본 설정 여부',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '활성화 여부',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_config_name` (`config_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 배송 및 고정값 설정 테이블';

-- =====================================================
-- 2. ACE 메인 상품 테이블
-- 바이마 API에 등록할 상품 정보
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_products` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `raw_data_id` INT(11) NOT NULL COMMENT 'raw_scraped_data 테이블 FK',
    `source_site` VARCHAR(50) NOT NULL DEFAULT 'okmall' COMMENT '소스 쇼핑몰',

    -- 바이마 상품 식별자
    `reference_number` VARCHAR(50) NULL DEFAULT NULL COMMENT '사용자 관리번호 (바이마용)',
    `buyma_product_id` INT(11) NULL DEFAULT NULL COMMENT '바이마 발급 상품 ID (등록 후)',

    -- 상품 상태 관리
    `control` ENUM('publish', 'draft', 'suspend', 'delete') NOT NULL DEFAULT 'draft' COMMENT '상품 상태',
    `status` VARCHAR(50) NULL DEFAULT NULL COMMENT '바이마 상품 현재 상태 (read-only)',

    -- 상품 기본 정보
    `name` VARCHAR(500) NOT NULL COMMENT '바이마 상품명 (형식: 【즉발】브랜드 상품명【국내발】)',
    `comments` TEXT NULL DEFAULT NULL COMMENT '상품 상세 설명 (최대 3000자)',

    -- 브랜드 정보 (mall_brands 테이블 참조)
    `brand_id` INT(11) NOT NULL DEFAULT 0 COMMENT '바이마 브랜드 ID (mall_brands.buyma_brand_id)',
    `brand_name` VARCHAR(100) NULL DEFAULT NULL COMMENT '브랜드명 (brand_id가 0일 때)',

    -- 카테고리 정보 (mall_categories 테이블 참조)
    `category_id` INT(11) NOT NULL COMMENT '바이마 카테고리 ID (mall_categories.buyma_category_id)',

    -- 가격 정보 (원화)
    `original_price_krw` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '원화 정가 (VAT 포함)',
    `purchase_price_krw` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '원화 매입가 (옵션 중 최고가 기준)',

    -- 가격 정보 (엔화)
    `original_price_jpy` INT(11) NULL DEFAULT NULL COMMENT '엔화 정가 (KRW / 10)',
    `price` INT(11) NOT NULL COMMENT '엔화 판매가 (배송료 미포함, 바이마 API용)',
    `regular_price` INT(11) NULL DEFAULT NULL COMMENT '통상 출품 가격 (14일 후 변경 가능, 미사용)',
    `reference_price` INT(11) NULL DEFAULT NULL COMMENT '참고 가격 (정가, 교차검증 2회 이상시만 설정)',
    `reference_price_verify_count` INT(11) NOT NULL DEFAULT 0 COMMENT '정가 교차검증 횟수',

    -- 마진 정보
    `margin_amount_krw` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '마진액 (원화)',
    `margin_rate` DECIMAL(5,2) NULL DEFAULT NULL COMMENT '마진율 (%)',

    -- 최저가 정보
    `buyma_lowest_price` INT(11) NULL DEFAULT NULL COMMENT '바이마 경쟁사 최저가 (엔화)',
    `is_lowest_price` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '최저가 확보 여부',

    -- 판매 기한
    `available_until` DATE NOT NULL COMMENT '구매 기한 (최대 90일)',

    -- 지역 및 배송 정보 (고정값)
    `buying_area_id` VARCHAR(20) NOT NULL DEFAULT '2002003000' COMMENT '구매 지역 ID (고정)',
    `shipping_area_id` VARCHAR(20) NOT NULL DEFAULT '2002003000' COMMENT '발송 지역 ID (고정)',
    `buying_shop_name` VARCHAR(200) NULL DEFAULT NULL COMMENT '구매처명 (브랜드명 + 正規販売店)',

    -- 부가 정보 (고정값 포함)
    `model_no` VARCHAR(100) NULL DEFAULT NULL COMMENT '원본 모델번호',
    `buyma_model_id` INT(11) NULL DEFAULT NULL COMMENT '바이마 모델 ID',
    `theme_id` INT(11) NOT NULL DEFAULT 98 COMMENT '테마 ID (고정: 98)',
    `season_id` INT(11) NULL DEFAULT NULL COMMENT '시즌 ID',
    `size_unit` VARCHAR(20) NULL DEFAULT NULL COMMENT '사이즈 단위 (cm 등)',
    `colorsize_comments` TEXT NULL DEFAULT NULL COMMENT '색상/사이즈 보충 정보',
    `buyer_notes` TEXT NULL DEFAULT NULL COMMENT '출품자 메모 (비공개)',
    `duty` VARCHAR(20) NOT NULL DEFAULT 'included' COMMENT '관세 정보 (고정: included)',

    -- 원본 데이터 참조
    `source_product_url` TEXT NULL DEFAULT NULL COMMENT '원본 상품 URL',
    `source_model_id` VARCHAR(100) NULL DEFAULT NULL COMMENT '원본 모델 ID',
    `source_original_price` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '원본 정가 (원화)',
    `source_sales_price` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '원본 판매가 (원화)',

    -- API 요청/응답 저장
    `api_request_json` LONGTEXT NULL DEFAULT NULL COMMENT 'API 요청 JSON',
    `api_response_json` LONGTEXT NULL DEFAULT NULL COMMENT 'API 응답 JSON',
    `last_api_call_at` TIMESTAMP NULL DEFAULT NULL COMMENT '마지막 API 호출 시간',

    -- 상태 플래그
    `is_image_uploaded` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '이미지 업로드 완료 여부',
    `is_ready_to_publish` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '등록 준비 완료 여부',
    `is_published` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '바이마 등록 완료 여부',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '활성화 여부',

    -- 타임스탬프
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_raw_data_id` (`raw_data_id`),
    UNIQUE KEY `uk_reference_number` (`reference_number`),
    INDEX `idx_buyma_product_id` (`buyma_product_id`),
    INDEX `idx_control` (`control`),
    INDEX `idx_is_published` (`is_published`),
    INDEX `idx_brand_id` (`brand_id`),
    INDEX `idx_category_id` (`category_id`),
    INDEX `idx_source_model_id` (`source_model_id`),

    CONSTRAINT `fk_ace_products_raw_data`
        FOREIGN KEY (`raw_data_id`) REFERENCES `raw_scraped_data` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT `chk_api_request_json` CHECK (JSON_VALID(`api_request_json`) OR `api_request_json` IS NULL),
    CONSTRAINT `chk_api_response_json` CHECK (JSON_VALID(`api_response_json`) OR `api_response_json` IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 API 등록용 가공 상품 데이터';

-- =====================================================
-- 3. ACE 상품 이미지 테이블
-- 바이마 등록용 상품 이미지 (최대 20장)
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_images` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL COMMENT 'ace_products FK',
    `position` INT(11) NOT NULL DEFAULT 1 COMMENT '이미지 순서 (1부터, 최대 20)',
    `source_image_url` TEXT NOT NULL COMMENT '원본 이미지 URL (오케이몰)',
    `cloudflare_image_url` TEXT NULL DEFAULT NULL COMMENT 'Cloudflare 업로드 URL',
    `buyma_image_path` TEXT NULL DEFAULT NULL COMMENT '바이마 API용 이미지 경로',
    `is_uploaded` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '업로드 완료 여부',
    `upload_error` TEXT NULL DEFAULT NULL COMMENT '업로드 오류 메시지',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_position` (`ace_product_id`, `position`),
    INDEX `idx_is_uploaded` (`is_uploaded`),

    CONSTRAINT `fk_ace_images_product`
        FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 등록용 상품 이미지';

-- =====================================================
-- 4. ACE 상품 옵션 테이블 (색상/사이즈)
-- 바이마 _options_ 필드용 데이터
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_options` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL COMMENT 'ace_products FK',
    `option_type` ENUM('color', 'size') NOT NULL COMMENT '옵션 타입',
    `value` VARCHAR(100) NOT NULL COMMENT '옵션 값 (S, M, L, White 등)',
    `master_id` INT(11) NULL DEFAULT NULL COMMENT '바이마 마스터 ID (색상 미선택: 99, 사이즈 미선택: 0)',
    `position` INT(11) NOT NULL DEFAULT 1 COMMENT '순서 (1부터)',
    `details_json` JSON NULL DEFAULT NULL COMMENT '상세 정보 (착장, 실측 등)',
    `source_option_value` VARCHAR(100) NULL DEFAULT NULL COMMENT '원본 옵션 값',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_type_value` (`ace_product_id`, `option_type`, `value`),
    INDEX `idx_option_type` (`option_type`),

    CONSTRAINT `fk_ace_options_product`
        FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 상품 옵션 (색상/사이즈)';

-- =====================================================
-- 5. ACE 상품 재고(Variants) 테이블
-- 바이마 variants 필드용 재고 정보
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_variants` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL COMMENT 'ace_products FK',
    `color_value` VARCHAR(100) NULL DEFAULT NULL COMMENT '색상 값',
    `size_value` VARCHAR(100) NULL DEFAULT NULL COMMENT '사이즈 값',
    `options_json` JSON NOT NULL COMMENT '옵션 조합 JSON (바이마 API용)',
    `stock_type` ENUM('stock_in_hand', 'purchase_for_order', 'out_of_stock') NOT NULL DEFAULT 'stock_in_hand' COMMENT '재고 타입',
    `stocks` INT(11) NOT NULL DEFAULT 1 COMMENT '재고 수량',
    `source_option_code` VARCHAR(100) NULL DEFAULT NULL COMMENT '원본 옵션 코드 (오케이몰)',
    `source_stock_status` VARCHAR(50) NULL DEFAULT NULL COMMENT '원본 재고 상태',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_variant` (`ace_product_id`, `color_value`, `size_value`),
    INDEX `idx_stock_type` (`stock_type`),

    CONSTRAINT `fk_ace_variants_product`
        FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 상품 재고 정보';

-- =====================================================
-- 6. ACE 상품 배송 방법 테이블
-- 바이마 shipping_methods 필드용 (고정값: 369)
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_shipping` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL COMMENT 'ace_products FK',
    `shipping_method_id` INT(11) NOT NULL DEFAULT 369 COMMENT '바이마 배송 방법 ID (고정: 369)',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_shipping` (`ace_product_id`, `shipping_method_id`),

    CONSTRAINT `fk_ace_shipping_product`
        FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 상품 배송 방법';

-- =====================================================
-- 7. ACE 상품 태그 테이블
-- 바이마 tags 필드용 (현재 공란)
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_tags` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL COMMENT 'ace_products FK',
    `tag_id` INT(11) NOT NULL COMMENT '바이마 태그 ID',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_tag` (`ace_product_id`, `tag_id`),

    CONSTRAINT `fk_ace_tags_product`
        FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 상품 태그 (현재 사용 안함)';

-- =====================================================
-- 8. 바이마 최저가 정보 테이블
-- 모델번호 기준 바이마 최저가 수집 결과
-- =====================================================
CREATE TABLE IF NOT EXISTS `buyma_lowest_prices` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `model_id` VARCHAR(100) NOT NULL COMMENT '모델번호',
    `buyma_search_url` TEXT NULL DEFAULT NULL COMMENT '바이마 검색 URL',
    `lowest_price` INT(11) NULL DEFAULT NULL COMMENT '바이마 최저가 (엔화)',
    `lowest_price_product_id` VARCHAR(100) NULL DEFAULT NULL COMMENT '최저가 상품 ID',
    `lowest_price_seller` VARCHAR(200) NULL DEFAULT NULL COMMENT '최저가 판매자',
    `competitor_count` INT(11) NULL DEFAULT NULL COMMENT '경쟁 상품 수',
    `price_data_json` JSON NULL DEFAULT NULL COMMENT '가격 정보 상세 JSON',
    `last_checked_at` TIMESTAMP NULL DEFAULT NULL COMMENT '마지막 확인 시간',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_model_id` (`model_id`),
    INDEX `idx_lowest_price` (`lowest_price`),
    INDEX `idx_last_checked` (`last_checked_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 모델별 최저가 정보';

-- =====================================================
-- 9. API 로그 테이블
-- 바이마 API 호출 이력 관리
-- =====================================================
CREATE TABLE IF NOT EXISTS `api_call_logs` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `api_type` VARCHAR(50) NOT NULL COMMENT 'API 타입 (product, stock, order 등)',
    `api_action` VARCHAR(50) NOT NULL COMMENT 'API 동작 (create, update, delete 등)',
    `ace_product_id` INT(11) NULL DEFAULT NULL COMMENT 'ace_products FK (해당시)',
    `request_json` LONGTEXT NULL DEFAULT NULL COMMENT '요청 JSON',
    `response_json` LONGTEXT NULL DEFAULT NULL COMMENT '응답 JSON',
    `http_status_code` INT(11) NULL DEFAULT NULL COMMENT 'HTTP 상태 코드',
    `request_uid` VARCHAR(100) NULL DEFAULT NULL COMMENT '바이마 요청 UID',
    `is_success` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '성공 여부',
    `error_message` TEXT NULL DEFAULT NULL COMMENT '오류 메시지',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_api_type` (`api_type`),
    INDEX `idx_ace_product_id` (`ace_product_id`),
    INDEX `idx_is_success` (`is_success`),
    INDEX `idx_created_at` (`created_at`),
    INDEX `idx_request_uid` (`request_uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='바이마 API 호출 로그';

-- =====================================================
-- 10. 정가 출처 테이블 (교차검증용)
-- reference_price 설정을 위한 정가 출처 기록
-- 2회 이상 교차검증 시에만 reference_price 설정
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_price_sources` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL COMMENT 'ace_products FK',
    `source_site` VARCHAR(100) NOT NULL COMMENT '정가 출처 사이트 (okmall, brand_official 등)',
    `original_price_krw` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '해당 출처의 원화 정가',
    `original_price_jpy` INT(11) NULL DEFAULT NULL COMMENT '해당 출처의 엔화 정가 (KRW / 10)',
    `source_url` TEXT NULL DEFAULT NULL COMMENT '출처 URL',
    `is_verified` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '검증 완료 여부',
    `verified_at` TIMESTAMP NULL DEFAULT NULL COMMENT '검증 시간',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_source_site` (`ace_product_id`, `source_site`),
    INDEX `idx_source_site` (`source_site`),
    INDEX `idx_is_verified` (`is_verified`),

    CONSTRAINT `fk_price_sources_product`
        FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='정가 교차검증용 출처 테이블';

-- =====================================================
-- 기본 데이터 삽입 (배송 설정 - 고정값)
-- =====================================================
INSERT INTO `shipping_config` (
    `config_name`,
    `buying_area_id`,
    `shipping_area_id`,
    `shipping_method_id`,
    `theme_id`,
    `duty`,
    `is_default`
)
VALUES (
    '한국_국내발송_기본설정',
    '2002003000',     -- 구매 지역 ID (고정)
    '2002003000',     -- 발송 지역 ID (고정)
    369,              -- 배송 방법 ID (고정)
    98,               -- 테마 ID (고정)
    'included',       -- 관세 (고정)
    1                 -- 기본 설정
)
ON DUPLICATE KEY UPDATE
    `buying_area_id` = '2002003000',
    `shipping_area_id` = '2002003000',
    `shipping_method_id` = 369,
    `theme_id` = 98,
    `duty` = 'included',
    `updated_at` = CURRENT_TIMESTAMP;

-- =====================================================
-- 완료 메시지
-- =====================================================
SELECT 'ACE 테이블 생성 완료!' AS message;
SELECT '주의: 브랜드/카테고리 매핑은 기존 mall_brands, mall_categories 테이블 사용' AS note;
