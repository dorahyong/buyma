-- pipeline_control.stage ENUM 에 'THUMBNAIL' 추가.
--
-- 문제: 파이프라인에 THUMBNAIL 단계(대표이미지 뱃지 합성, commit ac460c6)를 추가했으나
--   pipeline_control.stage ENUM 에 'THUMBNAIL' 이 없어, 재개 시 set_stage_status 가
--   Data truncated for column 'stage'(오류 1265) 로 실패. 결과적으로 모든 NEW 몰이
--   THUMBNAIL 직전에 죽어 REGISTER 에 도달하지 못함(몰별 즉사).
-- 조치: ENUM 맨 끝에 'THUMBNAIL' 추가(맨 끝이라 기존 값 저장위치 불변 → 메타데이터만, 무중단).
--
-- 적용 이력: 2026-07-15 운영 DB 적용 완료(0.04초). 이 파일은 재현/기록용.

ALTER TABLE pipeline_control MODIFY COLUMN stage
  ENUM('COLLECT','CONVERT','PRICE','MERGE','TRANSLATE','IMAGE','REGISTER','STOCK_REFRESH','RECONCILE','THUMBNAIL') NOT NULL;
