-- 품번 노출순위 시계열 테이블 3종 (exposure-history.md).
-- 적용: python scripts/_run_sql_file.py scripts/migrate_add_exposure_history.sql
-- 신규 설치는 mysql_schema.sql 에 이미 포함되어 있으므로 이 파일은 기존 운영 DB용.

CREATE TABLE IF NOT EXISTS market_exposure_snapshot (
  snapshot_id      BIGINT NOT NULL AUTO_INCREMENT,
  model_query      VARCHAR(255) NOT NULL,
  observed_at      VARCHAR(32)  NOT NULL,
  n_results_page1  INT,
  total_results    INT,
  floor_price_yen  INT,
  status           VARCHAR(16)  NOT NULL,
  PRIMARY KEY (snapshot_id),
  KEY idx_market_exposure_snapshot_model (model_query),
  KEY idx_market_exposure_snapshot_obs (observed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_exposure_history (
  snapshot_id  BIGINT       NOT NULL,
  rank         INT          NOT NULL,
  model_query  VARCHAR(255) NOT NULL,
  item_id      VARCHAR(64)  NOT NULL,
  price_yen    INT,
  seller_name  VARCHAR(255),
  seller_id    VARCHAR(64),
  observed_at  VARCHAR(32)  NOT NULL,
  PRIMARY KEY (snapshot_id, rank),
  KEY idx_market_exposure_history_item (item_id),
  KEY idx_market_exposure_history_model (model_query),
  KEY idx_market_exposure_history_obs (observed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_exposure_state (
  model_query       VARCHAR(255) NOT NULL,
  last_collected_at VARCHAR(32),
  last_status       VARCHAR(16),
  PRIMARY KEY (model_query)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
