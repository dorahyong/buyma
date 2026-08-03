-- market_revisit_state: 셀러 가치 집계용 seller_id 비정규화 컬럼 + (tier, seller_id) 인덱스.
--   기존 recompute_seller_values 는 revisit_state ⋈ market_items(12GB) 조인으로 셀러별
--   HOT/WARM 상품 수를 세다가 원격 MySQL read_timeout(300s)을 넘겨 2013 오류로 죽었다.
--   seller_id 를 revisit_state 에 복사해 두면 조인 없이 인덱스만으로 순식간에 집계된다.
--
-- ⚠️ 실행 순서:
--   1) 이 마이그레이션(컬럼 + 인덱스 추가)을 운영 DB에 먼저 적용.
--   2) scripts/backfill_revisit_seller_id.py 로 기존 178만 행의 seller_id 를 청크로 채움.
--   3) 빈(NULL) 행이 0인지 확인 후 새 코드(조인 없는 recompute) 가동.
--      (백필 전에 새 코드로 돌리면 seller_id 가 비어 셀러 가치가 낮게 계산됨)

ALTER TABLE market_revisit_state
  ADD COLUMN seller_id VARCHAR(64) AFTER base_tier,
  ADD KEY idx_market_revisit_tier_seller (tier, seller_id);
