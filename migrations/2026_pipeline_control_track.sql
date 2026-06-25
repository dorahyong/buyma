-- 통합 파이프라인 엔진용 pipeline_control 일반화 (하위호환)
-- 설계: reports/unified_pipeline_engine_design_20260618.md
-- ⚠️ pipeline_control 은 라이브(매일 RUNNING 배치 존재). 유니크키 변경은 RUNNING 배치 종료 후 실행.
--    ADD COLUMN 단계는 온라인 안전(DEFAULT 있음). 기존 orchestrator.py 는 track 미기록 → DEFAULT 'NEW' 로 무영향.

-- ===== 적용 이력 =====
-- 2026-06-23: 1)·2)·백필 적용 완료 (MariaDB 11.4.2, INSTANT, RUNNING 배치 무영향).
-- 2026-06-23: 3) 유니크키 swap 적용 완료. 적용 전 5컬럼 중복 0건 확인 → 원자적 단일 ALTER.
--             RUNNING orchestrator(track 미지정→기본 'NEW') 정상 동작 확인. 전체 step 3 완료.
-- 2026-06-23: 4) run_mode ENUM 에 'UNIFIED' 추가(통합러너 run_daily_unified.py 전용, 레거시 FULL 과 분리). 양 테이블 적용(INSTANT).

-- 1) 컬럼 추가 (온라인 안전)  ✅ 적용됨
ALTER TABLE `pipeline_control`
  ADD COLUMN `track` ENUM('NEW','STOCK') NOT NULL DEFAULT 'NEW' AFTER `run_mode`,
  ADD COLUMN `unit_key` VARCHAR(100) NULL AFTER `brand_name`;

-- 기존 행 unit_key 백필 (brand_name 그대로)
UPDATE `pipeline_control` SET `unit_key` = `brand_name` WHERE `unit_key` IS NULL;

-- 2) stage ENUM 확장 (STOCK 트랙 stage 추가)
ALTER TABLE `pipeline_control`
  MODIFY COLUMN `stage` ENUM('COLLECT','CONVERT','PRICE','MERGE','TRANSLATE','IMAGE','REGISTER',
                             'STOCK_REFRESH','RECONCILE') NOT NULL COMMENT '파이프라인 단계';

-- 3) 유니크키에 track 추가  ✅ 적용됨 (2026-06-23)
--   왜 필요: 통합 엔진은 같은 (batch,mall,brand,stage)에 NEW/STOCK 두 행을 만들 수 있음.
--           옛 4컬럼 키면 두 행이 충돌(ON DUPLICATE)해 서로 덮어씀 → track 포함 5컬럼 키 필요.
--   안전성: 새 키는 옛 키의 상위집합(track만 추가). 기존 데이터는 전부 track='NEW' 라 위반 없음.
--           orchestrator 도 항상 track='NEW' 로 INSERT → 동작 그대로. ★단일 ALTER(원자적)로 실행해
--           DROP↔ADD 사이 "키 없는 창" 을 없애야 함(그 창에 중복행 INSERT 되면 ADD 가 실패).
ALTER TABLE `pipeline_control`
  DROP INDEX `uk_batch_brand_stage`,
  ADD UNIQUE KEY `uk_batch_unit_track_stage` (`batch_id`, `mall_name`, `brand_name`, `track`, `stage`);

-- 4) run_mode ENUM 확장 (통합러너 전용 run_mode='UNIFIED')  ✅ 적용됨 (2026-06-23)
ALTER TABLE `pipeline_batches`
  MODIFY COLUMN `run_mode` ENUM('FULL','PARTIAL','UNIFIED') NOT NULL;
ALTER TABLE `pipeline_control`
  MODIFY COLUMN `run_mode` ENUM('FULL','PARTIAL','UNIFIED') NOT NULL;

-- 롤백 (필요시)
-- ALTER TABLE `pipeline_control`
--   DROP INDEX `uk_batch_unit_track_stage`,
--   ADD UNIQUE KEY `uk_batch_brand_stage` (`batch_id`, `mall_name`, `brand_name`, `stage`);
-- ALTER TABLE `pipeline_control` DROP COLUMN `track`, DROP COLUMN `unit_key`;
