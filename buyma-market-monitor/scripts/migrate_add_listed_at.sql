-- market_items: 出品(公開)日(kokaidate)을 담을 listed_at 컬럼 추가.
--   write-once — 코드가 NULL일 때만 기록하고 재출품 시에도 덮어쓰지 않음(최초 등록일 보존).
--
-- ⚠️ 배포 순서: 이 마이그레이션을 운영 DB에 먼저 적용한 뒤 새 코드를 가동할 것.
--    (새 코드가 listed_at 컬럼에 UPDATE 하므로, 컬럼이 없으면 enrich 가 에러난다.)
-- ⚠️ market_items 는 대용량(100만+ 행). ADD COLUMN 은 잠금·시간이 있으니 크롤 피크 피해서.

ALTER TABLE market_items
  ADD COLUMN listed_at VARCHAR(32) AFTER detail_fetched_at;
