-- =====================================================
-- 단일권위 마이그레이션 — 1단계 (저위험 additive)
--
-- 작성일: 2026-06-29
-- 목적: 정체성 ace_products → buyma_listings 이동의 안전한 선행 작업.
--   ① buyma_listings 에 실제 등록시각 컬럼 추가 (현재 created_at=테이블 적재일뿐)
--   ② merge 출품 webhook API 로그 누락 해결용 테이블
--      (현재 webhook 은 ace_products.reference_number 매칭 시에만 api_logs 기록 →
--       ref 가 buyma_listings 에만 있는 merge 출품은 로그가 통째로 누락됨)
--
-- 원칙: 새 컬럼/테이블만 → 기존 데이터·운영 0 영향.
-- ※ 실행 중인 webhook(server.py)은 재시작 전까지 옛 코드라, 이 DDL 만으론 동작 안 바뀜(안전).
-- =====================================================

USE buyma;

-- ① buyma_listings 실제 등록시각 (불변, 게시일수/단일권위용)
ALTER TABLE `buyma_listings`
  ADD COLUMN `buyma_registered_at` DATETIME NULL DEFAULT NULL
  COMMENT '최초 등록시각(불변)' AFTER `reference_number`;

-- ② merge 출품 webhook API 로그 (ace 없는 listing 용). ace_product_api_logs 구조 미러.
CREATE TABLE IF NOT EXISTS `buyma_listing_api_logs` (
    `buyma_listing_id`  INT(11)    NOT NULL                COMMENT 'buyma_listings.id',
    `api_request_json`  LONGTEXT   NULL DEFAULT NULL,
    `api_response_json` LONGTEXT   NULL DEFAULT NULL,
    `last_api_call_at`  DATETIME   NULL DEFAULT NULL,
    `created_at`        TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`buyma_listing_id`),
    CONSTRAINT `fk_listing_apilog` FOREIGN KEY (`buyma_listing_id`)
        REFERENCES `buyma_listings`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='MERGE 출품 webhook API 로그 (ace 없는 merge listing용)';
