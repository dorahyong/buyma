-- =====================================================
-- ACE 테이블 생성 스크립트
-- 바이마(BUYMA) API 연동을 위한 가공 데이터 저장용
--
-- 최종 수정일: 2026-03-24
-- 목적: 실제 DB 구조(SHOW CREATE TABLE)와 완전 일치
-- =====================================================

USE buyma;

-- =====================================================
-- 1. 배송 설정 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `shipping_config` (
    `id`                 INT(11)      NOT NULL AUTO_INCREMENT,
    `config_name`        VARCHAR(100) NOT NULL                 COMMENT '설정명',
    `buying_area_id`     VARCHAR(20)  NOT NULL DEFAULT '2002003000' COMMENT '구매 지역 ID (고정: 2002003000)',
    `shipping_area_id`   VARCHAR(20)  NOT NULL DEFAULT '2002003000' COMMENT '발송 지역 ID (고정: 2002003000)',
    `shipping_method_id` INT(11)      NOT NULL DEFAULT 369     COMMENT '배송 방법 ID (고정: 369)',
    `theme_id`           INT(11)      NOT NULL DEFAULT 98      COMMENT '테마 ID (고정: 98)',
    `duty`               VARCHAR(20)  NOT NULL DEFAULT 'included' COMMENT '관세 정보 (고정: included)',
    `is_default`         TINYINT(1)   NOT NULL DEFAULT 0       COMMENT '기본 설정 여부',
    `is_active`          TINYINT(1)   NOT NULL DEFAULT 1       COMMENT '활성화 여부',
    `created_at`         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_config_name` (`config_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='바이마 배송 및 고정값 설정 테이블';

-- =====================================================
-- 2. ACE 메인 상품 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_products` (
    `id`                            INT(11)      NOT NULL AUTO_INCREMENT,
    `raw_data_id`                   INT(11)      NOT NULL                  COMMENT 'raw_scraped_data 테이블 FK',
    `source_site`                   VARCHAR(50)  NOT NULL DEFAULT 'okmall' COMMENT '소스 쇼핑몰',
    `reference_number`              VARCHAR(50)  NULL     DEFAULT NULL      COMMENT '사용자 관리번호 (바이마용)',
    `buyma_product_id`              INT(11)      NULL     DEFAULT NULL      COMMENT '바이마 발급 상품 ID (등록 후)',
    `buyma_registered_at`           DATETIME     NULL     DEFAULT NULL      COMMENT '바이마 등록 완료 시각 (Webhook 수신 시)',
    `model_no`                      VARCHAR(100) NULL     DEFAULT NULL      COMMENT '원본 모델번호',
    `control`                       ENUM('publish','draft','suspend','delete') NOT NULL DEFAULT 'draft' COMMENT '상품 상태',
    `status`                        VARCHAR(50)  NULL     DEFAULT NULL      COMMENT '바이마 상품 현재 상태 (read-only)',
    `name`                          VARCHAR(500) NOT NULL                   COMMENT '바이마 상품명 (형식: 【즉발】브랜드 상품명【국내발】)',
    `comments`                      TEXT         NULL     DEFAULT NULL      COMMENT '상품 상세 설명 (최대 3000자)',
    `brand_id`                      INT(11)      NOT NULL DEFAULT 0         COMMENT '바이마 브랜드 ID (mall_brands.buyma_brand_id)',
    `brand_name`                    VARCHAR(100) NULL     DEFAULT NULL      COMMENT '브랜드명 (brand_id가 0일 때)',
    `category_id`                   INT(11)      NOT NULL                   COMMENT '바이마 카테고리 ID (mall_categories.buyma_category_id)',
    `expected_shipping_fee`         INT(11)      NULL     DEFAULT NULL      COMMENT '카테고리별 예상 배송비 (원화)',
    `original_price_krw`            DECIMAL(15,2) NULL   DEFAULT NULL      COMMENT '원화 정가 (VAT 포함)',
    `purchase_price_krw`            DECIMAL(15,2) NULL   DEFAULT NULL      COMMENT '원화 매입가 (옵션 중 최고가 기준)',
    `original_price_jpy`            INT(11)      NULL     DEFAULT NULL      COMMENT '엔화 정가 (KRW / 10)',
    `purchase_price_jpy`            INT(11)      NULL     DEFAULT NULL      COMMENT '엔화 매입가 (옵션 중 최고가 기준)',
    `price`                         INT(11)      NOT NULL                   COMMENT '엔화 판매가 (배송료 미포함, 바이마 API용)',
    `regular_price`                 INT(11)      NULL     DEFAULT NULL      COMMENT '통상 출품 가격 (14일 후 변경 가능, 미사용)',
    `reference_price`               INT(11)      NULL     DEFAULT NULL      COMMENT '참고 가격 (정가, 교차검증 2회 이상시만 설정)',
    `reference_price_verify_count`  INT(11)      NOT NULL DEFAULT 0         COMMENT '정가 교차검증 횟수',
    `margin_amount_krw`             DECIMAL(15,2) NULL   DEFAULT NULL      COMMENT '마진액 (원화)',
    `margin_rate`                   DECIMAL(5,2) NULL     DEFAULT NULL      COMMENT '최종 마진율 (%)',
    `buyma_lowest_price`            INT(11)      NULL     DEFAULT NULL      COMMENT '바이마 경쟁사 최저가 (엔화)',
    `is_lowest_price`               TINYINT(1)   NOT NULL DEFAULT 0         COMMENT '최저가 확보 여부',
    `buyma_lowest_price_checked_at` TIMESTAMP    NULL     DEFAULT NULL      COMMENT '바이마 최저가 확인 시간',
    `available_until`               DATE         NOT NULL                   COMMENT '구매 기한 (최대 90일)',
    `buying_area_id`                VARCHAR(20)  NOT NULL DEFAULT '2002003000' COMMENT '구매 지역 ID (고정)',
    `shipping_area_id`              VARCHAR(20)  NOT NULL DEFAULT '2002003000' COMMENT '발송 지역 ID (고정)',
    `buying_shop_name`              VARCHAR(200) NULL     DEFAULT NULL      COMMENT '구매처명 (브랜드명 + 正規販売店)',
    `buyma_model_id`                INT(11)      NULL     DEFAULT NULL      COMMENT '바이마 모델 ID',
    `theme_id`                      INT(11)      NOT NULL DEFAULT 98        COMMENT '테마 ID (고정: 98)',
    `season_id`                     INT(11)      NULL     DEFAULT NULL      COMMENT '시즌 ID',
    `size_unit`                     VARCHAR(20)  NULL     DEFAULT NULL      COMMENT '사이즈 단위 (cm 등)',
    `colorsize_comments`            TEXT         NULL     DEFAULT NULL      COMMENT '색상/사이즈 보충 정보',
    `colorsize_comments_jp`         TEXT         NULL     DEFAULT NULL      COMMENT '색상/사이즈 보충 정보 (한국어)',
    `buyer_notes`                   TEXT         NULL     DEFAULT NULL      COMMENT '출품자 메모 (비공개)',
    `duty`                          VARCHAR(20)  NOT NULL DEFAULT 'included' COMMENT '관세 정보 (고정: included)',
    `source_product_url`            TEXT         NULL     DEFAULT NULL      COMMENT '원본 상품 URL',
    `source_model_id`               VARCHAR(100) NULL     DEFAULT NULL      COMMENT '원본 모델 ID',
    `source_original_price`         DECIMAL(15,2) NULL   DEFAULT NULL      COMMENT '원본 정가 (원화)',
    `source_sales_price`            DECIMAL(15,2) NULL   DEFAULT NULL      COMMENT '원본 판매가 (원화)',
    `is_image_uploaded`             TINYINT(1)   NOT NULL DEFAULT 0         COMMENT '이미지 업로드 완료 여부',
    `is_ready_to_publish`           TINYINT(1)   NOT NULL DEFAULT 0         COMMENT '등록 준비 완료 여부',
    `is_published`                  TINYINT(1)   NOT NULL DEFAULT 0         COMMENT '바이마 등록 완료 여부',
    `is_buyma_locked`               TINYINT(1)   NOT NULL DEFAULT 0         COMMENT '바이마 등록 완료 후 불변 필드 잠금 (1=잠금, 수정 시 불변필드 변경 불가)',
    `locked_name`                   VARCHAR(500) NULL     DEFAULT NULL      COMMENT '바이마 등록 시점 상품명 (불변)',
    `locked_brand_id`               INT(11)      NULL     DEFAULT NULL      COMMENT '바이마 등록 시점 브랜드 ID (불변)',
    `locked_category_id`            INT(11)      NULL     DEFAULT NULL      COMMENT '바이마 등록 시점 카테고리 ID (불변)',
    `locked_reference_number`       VARCHAR(50)  NULL     DEFAULT NULL      COMMENT '바이마 등록 시점 관리번호 (불변)',
    `is_active`                     TINYINT(1)   NOT NULL DEFAULT 1         COMMENT '활성화 여부',
    `created_at`                    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`                    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `margin_calculated_at`          DATETIME     NULL     DEFAULT NULL      COMMENT '마진 계산 시점',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_raw_data_id`       (`raw_data_id`),
    UNIQUE KEY `uk_reference_number`  (`reference_number`),
    INDEX `idx_buyma_product_id`      (`buyma_product_id`),
    INDEX `idx_control`               (`control`),
    INDEX `idx_is_published`          (`is_published`),
    INDEX `idx_brand_id`              (`brand_id`),
    INDEX `idx_category_id`           (`category_id`),
    INDEX `idx_source_model_id`       (`source_model_id`),
    INDEX `idx_model_no`              (`model_no`),
    INDEX `idx_is_buyma_locked`       (`is_buyma_locked`),
    INDEX `idx_buyma_registered_at`   (`buyma_registered_at`),
    INDEX `idx_active_price_check`    (`is_active`, `buyma_lowest_price_checked_at`, `id`),
    INDEX `idx_active_published_model`(`is_active`, `is_published`, `model_no`),
    INDEX `idx_published_active`      (`is_published`, `is_active`, `buyma_product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='바이마 API 등록용 가공 상품 데이터';

-- =====================================================
-- 3. ACE API 로그 테이블 (ace_products에서 분리)
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_api_logs` (
    `ace_product_id`    INT(11)   NOT NULL,
    `api_request_json`  LONGTEXT  NULL     DEFAULT NULL,
    `api_response_json` LONGTEXT  NULL     DEFAULT NULL,
    `last_api_call_at`  TIMESTAMP NULL     DEFAULT NULL,
    `created_at`        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`ace_product_id`),
    CONSTRAINT `fk_api_logs_product` FOREIGN KEY (`ace_product_id`) REFERENCES `ace_products` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='API 요청/응답 로그 (ace_products에서 분리)';

-- =====================================================
-- 4. ACE 상품 이미지 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_images` (
    `id`                   INT(11)    NOT NULL AUTO_INCREMENT,
    `ace_product_id`       INT(11)    NOT NULL                 COMMENT 'ace_products FK',
    `position`             INT(11)    NOT NULL DEFAULT 1       COMMENT '이미지 순서 (1부터, 최대 20)',
    `source_image_url`     TEXT       NOT NULL                 COMMENT '원본 이미지 URL (오케이몰)',
    `cloudflare_image_url` TEXT       NULL DEFAULT NULL        COMMENT 'Cloudflare 업로드 URL',
    `buyma_image_path`     TEXT       NULL DEFAULT NULL        COMMENT '바이마 API용 이미지 경로',
    `is_uploaded`          TINYINT(1) NOT NULL DEFAULT 0       COMMENT '업로드 완료 여부',
    `upload_error`         TEXT       NULL DEFAULT NULL        COMMENT '업로드 오류 메시지',
    `created_at`           TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`           TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_position` (`ace_product_id`, `position`),
    INDEX `idx_is_uploaded` (`is_uploaded`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='바이마 등록용 상품 이미지';

-- =====================================================
-- 5. ACE 상품 옵션 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_options` (
    `id`                  INT(11)              NOT NULL AUTO_INCREMENT,
    `ace_product_id`      INT(11)              NOT NULL                 COMMENT 'ace_products FK',
    `option_type`         ENUM('color','size') NOT NULL                 COMMENT '옵션 타입',
    `value`               VARCHAR(100)         NOT NULL                 COMMENT '옵션 값 (S, M, L, White 등)',
    `master_id`           INT(11)              NULL DEFAULT NULL        COMMENT '바이마 마스터 ID (색상 미선택: 99, 사이즈 미선택: 0)',
    `position`            INT(11)              NOT NULL DEFAULT 1       COMMENT '순서 (1부터)',
    `details_json`        LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL DEFAULT NULL COMMENT '상세 정보 (착장, 실측 등)' CHECK (json_valid(`details_json`)),
    `source_option_value` VARCHAR(100)         NULL DEFAULT NULL        COMMENT '원본 옵션 값',
    `created_at`          TIMESTAMP            NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_type_value` (`ace_product_id`, `option_type`, `value`),
    INDEX `idx_option_type` (`option_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='바이마 상품 옵션 (색상/사이즈)';

-- =====================================================
-- 6. ACE 상품 재고 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `ace_product_variants` (
    `id`                  INT(11)     NOT NULL AUTO_INCREMENT,
    `ace_product_id`      INT(11)     NOT NULL                 COMMENT 'ace_products FK',
    `color_value`         VARCHAR(100) NULL DEFAULT NULL       COMMENT '색상 값',
    `size_value`          VARCHAR(100) NULL DEFAULT NULL       COMMENT '사이즈 값',
    `options_json`        LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '옵션 조합 JSON (바이마 API용)' CHECK (json_valid(`options_json`)),
    `stock_type`          ENUM('stock_in_hand','purchase_for_order','out_of_stock') NOT NULL DEFAULT 'stock_in_hand' COMMENT '재고 타입',
    `stocks`              INT(11)     NOT NULL DEFAULT 1       COMMENT '재고 수량',
    `source_option_code`  VARCHAR(100) NULL DEFAULT NULL       COMMENT '원본 옵션 코드 (오케이몰)',
    `source_stock_status` VARCHAR(50)  NULL DEFAULT NULL       COMMENT '원본 재고 상태',
    `created_at`          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `source_site`         VARCHAR(50)  NULL DEFAULT NULL       COMMENT '재고 출처 수집처 (MERGE용)',
    `source_raw_price`    DECIMAL(15,2) NULL DEFAULT NULL      COMMENT '재고 출처 매입가 (MERGE용)',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ace_product_variant` (`ace_product_id`, `color_value`, `size_value`),
    INDEX `idx_stock_type` (`stock_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='바이마 상품 재고 정보';

-- =====================================================
-- 7. 바이마 마스터 카테고리 테이블 (배송비 매핑)
-- =====================================================
CREATE TABLE IF NOT EXISTS `buyma_master_categories_data` (
    `buyma_category_id`   INT(11)      NOT NULL,
    `buyma_paths`         VARCHAR(255) NOT NULL,
    `buyma_name`          VARCHAR(255) NOT NULL,
    `mall_paths`          VARCHAR(255) NOT NULL,
    `mall_name`           VARCHAR(255) NOT NULL,
    `expected_shipping_fee` INT(11)    NOT NULL,
    `created_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`buyma_category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 8. 쇼핑몰 브랜드 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `mall_brands` (
    `mall_name`           MEDIUMTEXT NULL,
    `mall_brand_name_ko`  MEDIUMTEXT NULL,
    `mall_brand_name_en`  MEDIUMTEXT NULL,
    `buyma_brand_id`      DOUBLE     NULL,
    `buyma_brand_name`    MEDIUMTEXT NULL,
    `mapping_level`       BIGINT(20) NULL,
    `is_mapped`           TINYINT(1) NULL,
    `mall_brand_url`      VARCHAR(200) NULL,
    `is_active`           TINYINT(1) NULL DEFAULT 1,
    `mall_brand_no`       VARCHAR(20) NULL DEFAULT NULL,
    INDEX `idx_active_brand_en` (`is_active`, `mall_brand_name_en`(100))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 9. 쇼핑몰 카테고리 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `mall_categories` (
    `id`               INT(11)      NOT NULL AUTO_INCREMENT,
    `mall_name`        VARCHAR(50)  NULL DEFAULT 'okmall',
    `category_id`      VARCHAR(50)  NULL DEFAULT NULL,
    `gender`           VARCHAR(20)  NULL DEFAULT NULL,
    `depth1`           VARCHAR(100) NULL DEFAULT NULL,
    `depth2`           VARCHAR(100) NULL DEFAULT NULL,
    `depth3`           VARCHAR(100) NULL DEFAULT NULL,
    `full_path`        VARCHAR(255) NULL DEFAULT NULL,
    `buyma_category_id` INT(11)    NULL DEFAULT NULL,
    `is_active`        TINYINT(1)  NULL DEFAULT 1,
    `created_at`       TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `mall_name` (`mall_name`, `full_path`),
    INDEX `idx_buyma_category_id` (`buyma_category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 10. 수집처 사이트 설정 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `mall_sites` (
    `id`               INT(11)      NOT NULL AUTO_INCREMENT,
    `site_name`        VARCHAR(50)  NOT NULL,
    `has_own_images`   TINYINT(1)   NOT NULL DEFAULT 0,
    `is_active`        TINYINT(1)   NOT NULL DEFAULT 1,
    `created_at`       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_site_name` (`site_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 초기 데이터
INSERT IGNORE INTO `mall_sites` (`site_name`, `has_own_images`) VALUES
    ('okmall', 0),
    ('kasina', 1);

-- =====================================================
-- 11. 원본 수집 데이터 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `raw_scraped_data` (
    `id`             INT(11)      NOT NULL AUTO_INCREMENT,
    `source_site`    VARCHAR(50)  NULL DEFAULT 'okmall',
    `mall_product_id` VARCHAR(100) NOT NULL,
    `brand_name_en`  VARCHAR(100) NULL DEFAULT NULL,
    `brand_name_kr`  VARCHAR(100) NULL DEFAULT NULL,
    `product_name`   VARCHAR(255) NULL DEFAULT NULL      COMMENT '순수 상품명',
    `p_name_full`    TEXT         NULL DEFAULT NULL      COMMENT '전체 상품명',
    `model_id`       VARCHAR(100) NULL DEFAULT NULL,
    `category_path`  VARCHAR(255) NULL DEFAULT NULL,
    `original_price` DECIMAL(15,2) NULL DEFAULT NULL,
    `raw_price`      DECIMAL(15,2) NULL DEFAULT NULL    COMMENT '실제 판매가',
    `stock_status`   VARCHAR(20)  NULL DEFAULT NULL,
    `raw_json_data`  LONGTEXT     NULL DEFAULT NULL      COMMENT '' CHECK (json_valid(`raw_json_data`)),
    `product_url`    TEXT         NULL DEFAULT NULL,
    `created_at`     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_source_product` (`source_site`, `mall_product_id`),
    UNIQUE KEY `uk_source_mall_product` (`source_site`, `mall_product_id`),
    INDEX `idx_brand_site`       (`brand_name_en`, `source_site`),
    INDEX `idx_mall_product_id`  (`mall_product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================
-- 12. 오케스트레이터 배치 실행 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `pipeline_batches` (
    `id`             INT(11)     NOT NULL AUTO_INCREMENT,
    `batch_id`       VARCHAR(50) NOT NULL,
    `run_mode`       ENUM('FULL','PARTIAL') NOT NULL,
    `status`         ENUM('RUNNING','COMPLETED','FAILED') NULL DEFAULT 'RUNNING',
    `start_time`     DATETIME    NULL DEFAULT CURRENT_TIMESTAMP,
    `end_time`       DATETIME    NULL DEFAULT NULL,
    `total_brands`   INT(11)     NULL DEFAULT 0,
    `success_brands` INT(11)     NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_batch_id` (`batch_id`),
    INDEX `idx_status`     (`status`),
    INDEX `idx_start_time` (`start_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='오케스트레이터 배치 실행 이력';

-- =====================================================
-- 13. 오케스트레이터 파이프라인 단계별 상태 테이블
-- =====================================================
CREATE TABLE IF NOT EXISTS `pipeline_control` (
    `id`         INT(11)      NOT NULL AUTO_INCREMENT,
    `batch_id`   VARCHAR(50)  NOT NULL,
    `mall_name`  VARCHAR(50)  NOT NULL,
    `brand_name` VARCHAR(100) NOT NULL,
    `run_mode`   ENUM('FULL','PARTIAL') NOT NULL,
    `stage`      ENUM('COLLECT','CONVERT','PRICE','MERGE','TRANSLATE','IMAGE','REGISTER') NOT NULL COMMENT '파이프라인 단계',
    `status`     ENUM('PENDING','RUNNING','DONE','ERROR') NULL DEFAULT 'PENDING',
    `item_count` INT(11)      NULL DEFAULT 0,
    `error_msg`  TEXT         NULL DEFAULT NULL,
    `started_at` DATETIME     NULL DEFAULT NULL,
    `updated_at` DATETIME     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_batch_brand_stage` (`batch_id`, `mall_name`, `brand_name`, `stage`),
    INDEX `idx_batch_id`    (`batch_id`),
    INDEX `idx_status`      (`status`),
    INDEX `idx_mall_brand`  (`mall_name`, `brand_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='파이프라인 단계별 실행 상태';
