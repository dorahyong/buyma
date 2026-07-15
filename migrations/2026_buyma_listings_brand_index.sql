-- =====================================================
-- buyma_listings 브랜드 인덱스 추가 (성능 개선)
--
-- 작성일: 2026-07-15
-- 목적: reconcile_ensure_group 의 브랜드별 listing 조회 성능 개선.
--   해당 쿼리(SELECT ... FROM buyma_listings l JOIN source_offerings so ...
--   WHERE l.brand_id=?)가 상품 판정마다 호출되는데, buyma_listings 에
--   brand_id 인덱스가 없어 source_offerings 12만행을 매번 풀스캔 →
--   결과 건수와 무관하게 건당 16~18초 소요.
--   register·stock 이 상품마다 이 쿼리를 부르므로 처리 전체가 지연됨.
--
-- 효과(실측): 브랜드 조회 16~18초 → 1초 이하 (11~300배). 상품당 처리시간 동반 단축.
-- 원칙: 인덱스 1건 추가만 → 기존 데이터·로직 0 영향.
--
-- ※ 온라인 DDL(LOCK=NONE)이라 생성 중에도 읽기·쓰기 계속됨.
--   운영 중 적용 시엔 SET SESSION lock_wait_timeout 을 짧게(예: 30) 두고 실행 권장
--   (긴 조회가 테이블을 점유 중이면 즉시 실패시켜 대기 줄서기 방지).
-- =====================================================

USE buyma;

-- brand_id 로 좁힌 뒤 is_active 까지 커버 (WHERE l.is_active=1 AND l.brand_id=?)
ALTER TABLE `buyma_listings`
  ADD INDEX `idx_brand_active` (`brand_id`, `is_active`),
  ALGORITHM=INPLACE, LOCK=NONE;
