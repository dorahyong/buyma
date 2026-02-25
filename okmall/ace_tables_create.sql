-- =====================================================
-- ACE 테이블 생성 스크립트 (최신 DB 구조 반영)
-- 바이마(BUYMA) API 연동을 위한 가공 데이터 저장용
--
-- 최종 수정일: 2026-01-22
-- 목적: 실제 DB 구조(DESCRIBE)를 기반으로 테이블 정의 최신화
-- =====================================================

-- 데이터베이스 사용
USE buyma;

-- =====================================================
-- 1. 배송 설정 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `shipping_config` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `config_name` VARCHAR(100) NOT NULL COMMENT '설정명',
    `buying_area_id` VARCHAR(20) NOT NULL DEFAULT '2002003000' COMMENT '구매 지역 ID',
    `shipping_area_id` VARCHAR(20) NOT NULL DEFAULT '2002003000' COMMENT '발송 지역 ID',
    `shipping_method_id` INT(11) NOT NULL DEFAULT 369 COMMENT '배송 방법 ID',
    `theme_id` INT(11) NOT NULL DEFAULT 98 COMMENT '테마 ID',
    `duty` VARCHAR(20) NOT NULL DEFAULT 'included' COMMENT '관세 정보',
    `is_default` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '기본 설정 여부',
    `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '활성화 여부',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_config_name` (`config_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='바이마 배송 및 고정값 설정 테이블';

-- =====================================================
-- 2. ACE 메인 상품 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_products` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `raw_data_id` INT(11) NOT NULL COMMENT 'raw_scraped_data 테이블 FK',
    `source_site` VARCHAR(50) NOT NULL DEFAULT 'okmall' COMMENT '소스 쇼핑몰',
    `reference_number` VARCHAR(50) NULL DEFAULT NULL COMMENT '바이마용 관리번호',
    `buyma_product_id` INT(11) NULL DEFAULT NULL COMMENT '바이마 발급 상품 ID',
    `control` ENUM('publish','draft','suspend','delete') NOT NULL DEFAULT 'draft' COMMENT '상품 상태',
    `status` VARCHAR(50) NULL DEFAULT NULL COMMENT '바이마 상태 (read-only)',
    `name` VARCHAR(500) NOT NULL COMMENT '상품명',
    `comments` TEXT NULL DEFAULT NULL COMMENT '상세 설명',
    `brand_id` INT(11) NOT NULL DEFAULT 0 COMMENT '바이마 브랜드 ID',
    `brand_name` VARCHAR(100) NULL DEFAULT NULL COMMENT '브랜드명',
    `category_id` INT(11) NOT NULL COMMENT '바이마 카테고리 ID',
    `expected_shipping_fee` INT(11) NULL DEFAULT NULL COMMENT '카테고리별 예상 배송비 (원화)',
    `original_price_krw` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '원화 정가',
    `purchase_price_krw` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '원화 매입가',
    `original_price_jpy` INT(11) NULL DEFAULT NULL COMMENT '엔화 정가 (KRW / 10)',
    `purchase_price_jpy` INT(11) NULL DEFAULT NULL COMMENT '원화 매입가 → 엔화 변환 (KRW / 9.2)',
    `price` INT(11) NOT NULL COMMENT '엔화 판매가 (바이마 API 전달용)',
    `regular_price` INT(11) NULL DEFAULT NULL COMMENT '통상 출품 가격',
    `reference_price` INT(11) NULL DEFAULT NULL COMMENT '참고 가격',
    `reference_price_verify_count` INT(11) NOT NULL DEFAULT 0 COMMENT '정가 검증 횟수',
    `margin_amount_krw` DECIMAL(15,2) NULL DEFAULT NULL COMMENT '마진액(원화)',
    `margin_rate` DECIMAL(5,2) NULL DEFAULT NULL COMMENT '최종 마진율 (%)',
    `margin_calculated_at` DATETIME NULL DEFAULT NULL COMMENT '마진 계산 시각',
    `buyma_lowest_price` INT(11) NULL DEFAULT NULL COMMENT '경쟁사 최저가(엔화)',
    `is_lowest_price` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '최저가 여부',
    `buyma_lowest_price_checked_at` TIMESTAMP NULL DEFAULT NULL COMMENT '최저가 확인 시각',
    `available_until` DATE NOT NULL COMMENT '구매 기한',
    `buying_area_id` VARCHAR(20) NOT NULL DEFAULT '2002003000',
    `shipping_area_id` VARCHAR(20) NOT NULL DEFAULT '2002003000',
    `buying_shop_name` VARCHAR(200) NULL DEFAULT NULL,
    `model_no` VARCHAR(100) NULL DEFAULT NULL,
    `buyma_model_id` INT(11) NULL DEFAULT NULL,
    `theme_id` INT(11) NOT NULL DEFAULT 98,
    `season_id` INT(11) NULL DEFAULT NULL,
    `size_unit` VARCHAR(20) NULL DEFAULT NULL,
    `colorsize_comments` TEXT NULL DEFAULT NULL,
    `buyer_notes` TEXT NULL DEFAULT NULL,
    `duty` VARCHAR(20) NOT NULL DEFAULT 'included',
    `source_product_url` TEXT NULL DEFAULT NULL,
    `source_model_id` VARCHAR(100) NULL DEFAULT NULL,
    `source_original_price` DECIMAL(15,2) NULL DEFAULT NULL,
    `source_sales_price` DECIMAL(15,2) NULL DEFAULT NULL,
    `api_request_json` LONGTEXT NULL DEFAULT NULL,
    `api_response_json` LONGTEXT NULL DEFAULT NULL,
    `last_api_call_at` TIMESTAMP NULL DEFAULT NULL,
    `is_image_uploaded` TINYINT(1) NOT NULL DEFAULT 0,
    `is_ready_to_publish` TINYINT(1) NOT NULL DEFAULT 0,
    `is_published` TINYINT(1) NOT NULL DEFAULT 0,
    `is_active` TINYINT(1) NOT NULL DEFAULT 1,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_raw_data_id` (`raw_data_id`),
    UNIQUE KEY `uk_reference_number` (`reference_number`),
    INDEX `idx_is_published` (`is_published`),
    INDEX `idx_brand_id` (`brand_id`),
    INDEX `idx_category_id` (`category_id`),
    INDEX `idx_model_no` (`model_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='바이마 API용 가공 상품 데이터';

-- =====================================================
-- 3. ACE 상품 이미지 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_images` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL,
    `position` INT(11) NOT NULL DEFAULT 1,
    `source_image_url` TEXT NOT NULL,
    `cloudflare_image_url` TEXT NULL DEFAULT NULL,
    `buyma_image_path` TEXT NULL DEFAULT NULL,
    `is_uploaded` TINYINT(1) NOT NULL DEFAULT 0,
    `upload_error` TEXT NULL DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_position` (`ace_product_id`, `position`),
    INDEX `idx_is_uploaded` (`is_uploaded`),
    CONSTRAINT `fk_ace_images_product` FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 4. ACE 상품 옵션 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_options` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL,
    `option_type` ENUM('color','size') NOT NULL,
    `value` VARCHAR(100) NOT NULL,
    `master_id` INT(11) NULL DEFAULT NULL,
    `position` INT(11) NOT NULL DEFAULT 1,
    `details_json` LONGTEXT NULL DEFAULT NULL,
    `source_option_value` VARCHAR(100) NULL DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_type_value` (`ace_product_id`, `option_type`, `value`),
    CONSTRAINT `fk_ace_options_product` FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 5. ACE 상품 재고 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_variants` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `ace_product_id` INT(11) NOT NULL,
    `color_value` VARCHAR(100) NULL DEFAULT NULL,
    `size_value` VARCHAR(100) NULL DEFAULT NULL,
    `options_json` LONGTEXT NOT NULL,
    `stock_type` ENUM('stock_in_hand','purchase_for_order','out_of_stock') NOT NULL DEFAULT 'stock_in_hand',
    `stocks` INT(11) NOT NULL DEFAULT 1,
    `source_option_code` VARCHAR(100) NULL DEFAULT NULL,
    `source_stock_status` VARCHAR(50) NULL DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_variant` (`ace_product_id`, `color_value`, `size_value`),
    CONSTRAINT `fk_ace_variants_product` FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 6. 바이마 마스터 카테고리 데이터 (배송비 정보 포함)
-- =====================================================
CREATE TABLE IF NOT EXISTS `buyma_master_categories_data` (
    `buyma_category_id` INT(11) NOT NULL,
    `buyma_paths` VARCHAR(255) NOT NULL,
    `buyma_name` VARCHAR(255) NOT NULL,
    `mall_paths` VARCHAR(255) NOT NULL,
    `mall_name` VARCHAR(255) NOT NULL,
    `expected_shipping_fee` INT(11) NOT NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`buyma_category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='카테고리별 예상 배송비 매핑 테이블';

-- =====================================================
-- 7. 쇼핑몰 브랜드 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `mall_brands` (
    `mall_name` MEDIUMTEXT NULL,
    `mall_brand_name_ko` MEDIUMTEXT NULL,
    `mall_brand_name_en` MEDIUMTEXT NULL,
    `buyma_brand_id` DOUBLE NULL,
    `buyma_brand_name` MEDIUMTEXT NULL,
    `mapping_level` BIGINT(20) NULL,
    `is_mapped` TINYINT(1) NULL,
    `mall_brand_url` VARCHAR(200) NULL,
    `is_active` TINYINT(1) DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 8. 원본 수집 데이터 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `raw_scraped_data` (
    `id` INT(11) NOT NULL AUTO_INCREMENT,
    `source_site` VARCHAR(50) DEFAULT 'okmall',
    `mall_product_id` VARCHAR(100) NOT NULL,
    `brand_name_en強 VARCHAR(100) NULL,
    `brand_name_kr` VARCHAR(100) NULL,
    `product_name` VARCHAR(255) NULL,
    `p_name_full強 TEXT NULL,
    `model_id` VARCHAR(100) NULL,
    `category_path` VARCHAR(255) NULL,
    `original_price` DECIMAL(15,2) NULL,
    `raw_price` DECIMAL(15,2) NULL,
    `stock_status` VARCHAR(20) NULL,
    `raw_json_data` LONGTEXT NULL,
    `product_url強 TEXT NULL,
    `created_at強 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at強 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
