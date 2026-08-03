-- market_items: 옛 `themes` 컬럼은 실제로는 BUYMA 「タグ」(태그)를 담고 있었음.
--   → 데이터를 보존한 채 `tags` 로 rename 하고, 진짜 「テーマ」(테마, 단일)를 담을
--     새 `themes` 컬럼을 만든다.
--
-- ⚠️ 배포 순서 주의: 이 마이그레이션을 운영 DB에 먼저 적용한 뒤 새 코드를 가동할 것.
--    (새 코드는 tags/themes 두 컬럼에 UPDATE 하므로, 컬럼이 없으면 enrich 가 에러난다.)
-- ⚠️ market_items 는 대용량(100만+ 행). CHANGE/ADD 는 테이블 잠금·시간이 있으니
--    크롤 피크를 피해서 실행할 것.

ALTER TABLE market_items
  CHANGE COLUMN themes tags TEXT;

ALTER TABLE market_items
  ADD COLUMN themes TEXT AFTER tags;
