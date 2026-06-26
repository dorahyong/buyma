-- run_daily_unified.py --only-naver 를 별도 배치로 격리하기 위한 run_mode 값 추가.
--   목적: `--no-naver`(run_mode=UNIFIED) 와 `--only-naver`(run_mode=UNIFIED_NAVER) 를
--         동시에 돌려도 배치(pipeline_batches/pipeline_control)가 충돌하지 않게 함.
--   추가형 ALTER(기존 'FULL','PARTIAL','UNIFIED' 보존) → 기존 데이터/동작 0영향.
--   적용일: 2026-06-26 (이미 운영 DB에 적용됨).

ALTER TABLE pipeline_batches
  MODIFY run_mode ENUM('FULL','PARTIAL','UNIFIED','UNIFIED_NAVER') NOT NULL;

ALTER TABLE pipeline_control
  MODIFY run_mode ENUM('FULL','PARTIAL','UNIFIED','UNIFIED_NAVER') NOT NULL;
