-- STYLE HAUS 관련 동영상 유무 시계열 (delta). 상세 enrich 시 값이 바뀔 때만 INSERT.
-- 적용: python scripts/_run_sql_file.py scripts/migrate_add_stylehaus_history.sql

CREATE TABLE IF NOT EXISTS market_stylehaus_history (
  item_id               VARCHAR(64) NOT NULL,
  observed_at           VARCHAR(32) NOT NULL,
  has_style_haus        TINYINT     NOT NULL,
  stylehaus_video_count INT,
  PRIMARY KEY (item_id, observed_at),
  KEY idx_market_stylehaus_history_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
