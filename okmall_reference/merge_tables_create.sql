-- =====================================================
-- MERGE 테이블 생성 스크립트
-- 모델명 중복 → 멀티소스 merge 구조 (스키마 A: 테이블 분리)
--
-- 작성일: 2026-06-15
-- 설계서: reports/model_merge_design_handoff.md
-- 원칙: 새 테이블만 생성 → 기존 운영 데이터 0 영향
-- =====================================================

USE buyma;

-- ① 출품 정체성 (BUYMA와 1:1)
CREATE TABLE IF NOT EXISTS `buyma_listings` (
    `id`                   INT(11)      NOT NULL AUTO_INCREMENT,
    `group_key`            VARCHAR(100) NOT NULL                COMMENT '같은 품번 묶는 키 (정규화 model_no)',
    `buyma_product_id`     INT(11)      NULL DEFAULT NULL       COMMENT '바이마 발급 ID (여기에만 존재)',
    `reference_number`     VARCHAR(50)  NULL DEFAULT NULL       COMMENT '웹훅 write-back 키',
    `winner_offering_id`   INT(11)      NULL DEFAULT NULL       COMMENT '현재 메인 소싱처 → source_offerings.id',
    `control`              ENUM('publish','draft','suspend','delete') NOT NULL DEFAULT 'draft',
    `status`               VARCHAR(50)  NULL DEFAULT NULL       COMMENT '바이마 현재상태 (read-only)',
    `name`                 VARCHAR(500) NOT NULL                COMMENT '출품명 (대표)',
    `comments`             TEXT         NULL DEFAULT NULL,
    `brand_id`             INT(11)      NOT NULL DEFAULT 0,
    `brand_name`           VARCHAR(100) NULL DEFAULT NULL,
    `category_id`          INT(11)      NOT NULL,
    `model_no`             VARCHAR(100) NULL DEFAULT NULL,
    `price`                INT(11)      NULL DEFAULT NULL        COMMENT '판매가 JPY',
    `buyma_lowest_price`   INT(11)      NULL DEFAULT NULL        COMMENT '경쟁자 최저가 (그룹 1회 크롤)',
    `is_lowest_price`      TINYINT(1)   NOT NULL DEFAULT 0,
    `available_until`      DATE         NULL DEFAULT NULL,
    `buying_shop_name`     VARCHAR(200) NULL DEFAULT NULL        COMMENT 'winner 기준',
    `theme_id`             INT(11)      NOT NULL DEFAULT 98,
    `season_id`            INT(11)      NULL DEFAULT NULL,
    `size_unit`            VARCHAR(20)  NULL DEFAULT NULL,
    `colorsize_comments`   TEXT         NULL DEFAULT NULL,
    `is_buyma_locked`      TINYINT(1)   NOT NULL DEFAULT 0,
    `locked_name`          VARCHAR(500) NULL DEFAULT NULL        COMMENT '불변 백업',
    `locked_brand_id`      INT(11)      NULL DEFAULT NULL,
    `locked_category_id`   INT(11)      NULL DEFAULT NULL,
    `locked_reference_number` VARCHAR(50) NULL DEFAULT NULL,
    `is_published`         TINYINT(1)   NOT NULL DEFAULT 0,
    `is_active`            TINYINT(1)   NOT NULL DEFAULT 1,
    `created_at`           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_group_key`        (`group_key`),
    UNIQUE KEY `uk_reference_number` (`reference_number`),
    INDEX `idx_buyma_product_id`     (`buyma_product_id`),
    INDEX `idx_is_published`         (`is_published`),
    INDEX `idx_model_no`             (`model_no`),
    INDEX `idx_winner_offering`      (`winner_offering_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='MERGE: 출품 정체성 (BUYMA 1:1)';

-- ② 수집처별 상품 (그룹 멤버)
CREATE TABLE IF NOT EXISTS `source_offerings` (
    `id`                  INT(11)       NOT NULL AUTO_INCREMENT,
    `listing_id`          INT(11)       NOT NULL                COMMENT 'buyma_listings FK',
    `ace_product_id`      INT(11)       NULL DEFAULT NULL       COMMENT '기존 ace_products 행 연결 (마이그레이션/추적)',
    `source_site`         VARCHAR(50)   NOT NULL,
    `source_product_url`  TEXT          NULL DEFAULT NULL,
    `source_model_id`     VARCHAR(100)  NULL DEFAULT NULL,
    `purchase_price_krw`  DECIMAL(15,2) NULL DEFAULT NULL       COMMENT '멤버 매입가 (마진계산 기준)',
    `margin_amount_krw`   DECIMAL(15,2) NULL DEFAULT NULL,
    `margin_rate`         DECIMAL(10,2) NULL DEFAULT NULL       COMMENT '음수 마진율 폭주 대비 넓게',
    `is_margin_ok`        TINYINT(1)    NOT NULL DEFAULT 0       COMMENT '마진 게이트 통과',
    `last_collected_at`   DATETIME      NULL DEFAULT NULL,
    `is_active`           TINYINT(1)    NOT NULL DEFAULT 1       COMMENT '0=죽어도 행 보존',
    `created_at`          TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`          TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_listing_source` (`listing_id`, `source_site`, `source_model_id`),
    INDEX `idx_listing`     (`listing_id`),
    INDEX `idx_source_site` (`source_site`),
    INDEX `idx_ace_product` (`ace_product_id`),
    CONSTRAINT `fk_offering_listing` FOREIGN KEY (`listing_id`) REFERENCES `buyma_listings`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='MERGE: 수집처별 상품 (그룹 멤버)';

-- ③ 수집처별 옵션·재고 (가장 원자적 사실)
CREATE TABLE IF NOT EXISTS `source_offering_options` (
    `id`                   INT(11)       NOT NULL AUTO_INCREMENT,
    `offering_id`          INT(11)       NOT NULL                COMMENT 'source_offerings FK',
    `color_value_original` VARCHAR(100)  NULL DEFAULT NULL       COMMENT '매칭 키 (일본어 금지)',
    `size_value_original`  VARCHAR(100)  NULL DEFAULT NULL       COMMENT '매칭 키',
    `source_option_code`   VARCHAR(100)  NULL DEFAULT NULL       COMMENT '매칭 키 (최우선)',
    `color_value`          VARCHAR(100)  NULL DEFAULT NULL,
    `size_value`           VARCHAR(100)  NULL DEFAULT NULL,
    `stock_type`           ENUM('stock_in_hand','purchase_for_order','out_of_stock') NOT NULL DEFAULT 'stock_in_hand',
    `stocks`               INT(11)       NOT NULL DEFAULT 1,
    `purchase_price_krw`   DECIMAL(15,2) NULL DEFAULT NULL       COMMENT '옵션별 매입가',
    `is_margin_ok`         TINYINT(1)    NOT NULL DEFAULT 0,
    `source_stock_status`  VARCHAR(50)   NULL DEFAULT NULL,
    `created_at`           TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`           TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_offering_option` (`offering_id`, `color_value_original`, `size_value_original`),
    INDEX `idx_offering`    (`offering_id`),
    INDEX `idx_stock_type`  (`stock_type`),
    INDEX `idx_option_code` (`source_option_code`),
    CONSTRAINT `fk_offopt_offering` FOREIGN KEY (`offering_id`) REFERENCES `source_offerings`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='MERGE: 수집처별 옵션·재고';

-- ④ 출품에 올라간 옵션 (마진O union)
CREATE TABLE IF NOT EXISTS `listing_options` (
    `id`                        INT(11)      NOT NULL AUTO_INCREMENT,
    `listing_id`                INT(11)      NOT NULL            COMMENT 'buyma_listings FK',
    `color_value`               VARCHAR(100) NULL DEFAULT NULL,
    `size_value`                VARCHAR(100) NULL DEFAULT NULL,
    `options_json`              LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL COMMENT '바이마 API용' CHECK (json_valid(`options_json`)),
    `stock_type`                ENUM('stock_in_hand','purchase_for_order','out_of_stock') NOT NULL DEFAULT 'stock_in_hand',
    `stocks`                    INT(11)      NOT NULL DEFAULT 1,
    `sourced_offering_option_id` INT(11)     NULL DEFAULT NULL   COMMENT '이 옵션 떼올 최저가 멤버 → source_offering_options.id',
    `is_active`                 TINYINT(1)   NOT NULL DEFAULT 1,
    `created_at`                TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`                TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_listing_option` (`listing_id`, `color_value`, `size_value`),
    INDEX `idx_listing`        (`listing_id`),
    INDEX `idx_sourced_option` (`sourced_offering_option_id`),
    CONSTRAINT `fk_lstopt_listing` FOREIGN KEY (`listing_id`) REFERENCES `buyma_listings`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='MERGE: 출품 옵션 (마진O union + 소싱 포인터)';

-- ⑤ 출품 이미지 (멤버 union)
CREATE TABLE IF NOT EXISTS `listing_images` (
    `id`                   INT(11)    NOT NULL AUTO_INCREMENT,
    `listing_id`           INT(11)    NOT NULL                 COMMENT 'buyma_listings FK',
    `position`             INT(11)    NOT NULL DEFAULT 1       COMMENT '1부터, 최대 20, 연속 재매김',
    `source_site`          VARCHAR(50) NULL DEFAULT NULL       COMMENT '어느 몰 이미지',
    `source_image_url`     TEXT       NOT NULL,
    `cloudflare_image_url` TEXT       NULL DEFAULT NULL,
    `buyma_image_path`     TEXT       NULL DEFAULT NULL,
    `is_uploaded`          TINYINT(1) NOT NULL DEFAULT 0,
    `created_at`           TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`           TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_listing_position` (`listing_id`, `position`),
    INDEX `idx_listing`     (`listing_id`),
    INDEX `idx_is_uploaded` (`is_uploaded`),
    CONSTRAINT `fk_img_listing` FOREIGN KEY (`listing_id`) REFERENCES `buyma_listings`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='MERGE: 출품 이미지 (멤버 union)';
