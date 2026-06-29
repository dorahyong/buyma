-- =====================================================
-- 실제 게시일수 트래킹 — 스키마 (1단계: 테이블 2개 + 뷰)
--
-- 작성일: 2026-06-29
-- 설계서: reports/listing_days_tracking_design_20260609.md (C안 + 초단위)
--         + merge 세계 반영 → 키를 buyma_product_id 로 (정체성이 ace/listings 로 갈려있어도 안정적)
-- 소비처: scoring-system (시간감점의 "등록기간(일)" 입력값)
--
-- 원칙:
--   - 키 = buyma_product_id (소싱처 변경·품절 왕복 무관하게 "같은 상품")
--   - 계산은 카운터(숫자 2개)로 충분, 이력 로그는 복구·분석용 안전망
--   - 일수 = 초 단위 적분(TIMESTAMPDIFF SECOND), DATEDIFF 폐기(하루내 순환 0버그 회피)
--   - 기존 테이블 0 영향 (새 테이블만 생성)
--
-- ※ 이 파일은 스키마만. 트리거(2단계)·백필(3단계)은 별도 파일.
-- ※ 실행 전 사용자 확인 후 적용.
-- =====================================================

USE buyma;

-- =====================================================
-- ① 상품별 누적 게시일수 (카운터 — 빠른 조회 본체)
--    상품당 1행. "며칠째?" = (accumulated + 게시중이면 NOW-listed_since)/86400
-- =====================================================
CREATE TABLE IF NOT EXISTS `buyma_listing_days` (
    `buyma_product_id`     INT(11)      NOT NULL                COMMENT 'BUYMA 발급 상품 ID (안정적 키)',
    `listed_since`         DATETIME     NULL DEFAULT NULL       COMMENT '현재 게시구간 시작(미게시면 NULL)',
    `accumulated_seconds`  BIGINT       NOT NULL DEFAULT 0      COMMENT '과거 게시구간 누적(초, 현재구간 제외)',
    `first_listed_at`      DATETIME     NULL DEFAULT NULL       COMMENT '최초 게시 시각(참고·불변)',
    `is_listed`            TINYINT(1)   NOT NULL DEFAULT 0      COMMENT '현재 게시중 여부(빠른 필터)',
    `last_event_at`        DATETIME     NULL DEFAULT NULL       COMMENT '마지막 전이(up/down) 시각',
    `created_at`           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`buyma_product_id`),
    INDEX `idx_is_listed`    (`is_listed`),
    INDEX `idx_listed_since` (`listed_since`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='게시일수 카운터 (상품별 누적, buyma_product_id 기준)';

-- =====================================================
-- ② 올라감/내려감 이력 로그 (전이 순간만 1줄 — 복구·분석용 안전망)
--    계산엔 안 씀. 카운터가 틀어지면 이걸로 재계산.
-- =====================================================
CREATE TABLE IF NOT EXISTS `buyma_listing_events` (
    `id`                INT(11)       NOT NULL AUTO_INCREMENT,
    `buyma_product_id`  INT(11)       NOT NULL                COMMENT '어떤 상품',
    `event`             ENUM('up','down') NOT NULL            COMMENT 'up=올라감(0→1) / down=내려감(1→0)',
    `event_at`          DATETIME      NOT NULL                COMMENT '전이 발생 시각 (재계산 기준)',
    `reason`            VARCHAR(32)   NULL DEFAULT NULL       COMMENT 'register/soldout/deleted/fail/orphan 등',
    `source_table`      VARCHAR(20)   NULL DEFAULT NULL       COMMENT 'ace | listings (어느 트리거가 잡았나)',
    `reference_number`  VARCHAR(50)   NULL DEFAULT NULL       COMMENT '그 시점 ref (추적용)',
    `created_at`        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    INDEX `idx_bid_at` (`buyma_product_id`, `event_at`),
    INDEX `idx_event`  (`event`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='게시일수 이력 로그 (전이 순간만, 복구/분석용)';

-- =====================================================
-- ③ 조회용 뷰 — "총 게시일수" / "N일 이상 게시중 정리대상"
--    total_listed_days = (누적초 + 게시중이면 현재구간 경과초) / 86400
-- =====================================================
CREATE OR REPLACE VIEW `v_listing_days` AS
SELECT
    `buyma_product_id`,
    `is_listed`,
    `listed_since`,
    `accumulated_seconds`,
    (`accumulated_seconds`
        + IF(`listed_since` IS NULL, 0, TIMESTAMPDIFF(SECOND, `listed_since`, NOW())))
        / 86400.0 AS `total_listed_days`,
    `first_listed_at`
FROM `buyma_listing_days`;

-- 사용 예:
--   SELECT * FROM v_listing_days WHERE is_listed=1 AND total_listed_days >= 90;  -- 90일+ 게시중(도태 후보)
--   SELECT total_listed_days FROM v_listing_days WHERE buyma_product_id = 134003248;
