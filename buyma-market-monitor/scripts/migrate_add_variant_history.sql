-- 변형(색·사이즈) 재고 상태 이력. 상세 enrich 시 직전 대비 변화분만 기록.
-- availability: 0=OutOfStock, 1=InStock, 2=변형삭제
-- observed_at 은 market_stats_history 와 동일한 VARCHAR(32) ISO8601 (조인 정합).
-- 적용: python scripts/_run_sql_file.py scripts/migrate_add_variant_history.sql

CREATE TABLE IF NOT EXISTS market_variant_history (
  item_id      VARCHAR(64)  NOT NULL,
  variant_sku  VARCHAR(128) NOT NULL,
  observed_at  VARCHAR(32)  NOT NULL,
  availability TINYINT      NOT NULL COMMENT '0=OutOfStock, 1=InStock, 2=변형삭제',
  PRIMARY KEY (item_id, variant_sku, observed_at),
  KEY idx_mvh_observed (observed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
