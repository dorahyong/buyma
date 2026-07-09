-- buyma-market-monitor → buyma MySQL DB (market_ 접두어). InnoDB / utf8mb4.
-- 원본: storage/db.py 의 SQLite 스키마(11 tables, schema_version=6) 1:1 변환.
-- 타임스탬프(*_at)는 코드가 ISO 문자열로 넣으므로 VARCHAR(32) 유지(무손실). 나중에 DATETIME 최적화 가능.
-- ★ 역소싱 매칭용: market_items(brand_model_number) 인덱스 추가.

CREATE TABLE IF NOT EXISTS market_items (
  item_id             VARCHAR(64)  NOT NULL,
  seller_id           VARCHAR(64)  NOT NULL,
  name                VARCHAR(512) NOT NULL,
  current_price       INT,
  brand               VARCHAR(255),
  category_path       VARCHAR(512),
  origin_country      VARCHAR(64),
  image_url           VARCHAR(1024),
  description         TEXT,
  size_guide_text     TEXT,
  view_count          INT,
  fav_count           INT,
  inquiry_count       INT,
  brand_model_number  VARCHAR(255),
  themes              TEXT,
  size_chart_json     MEDIUMTEXT,
  status              VARCHAR(16)  NOT NULL,
  first_seen_at       VARCHAR(32)  NOT NULL,
  last_seen_at        VARCHAR(32)  NOT NULL,
  sold_out_at         VARCHAR(32),
  deleted_at          VARCHAR(32),
  detail_fetched_at   VARCHAR(32),
  PRIMARY KEY (item_id),
  KEY idx_market_items_seller (seller_id),
  KEY idx_market_items_status (status),
  KEY idx_market_items_model  (brand_model_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_price_history (
  item_id      VARCHAR(64) NOT NULL,
  observed_at  VARCHAR(32) NOT NULL,
  price        INT NOT NULL,
  PRIMARY KEY (item_id, observed_at),
  KEY idx_market_price_history_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_orders (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  seller_id    VARCHAR(64) NOT NULL,
  item_id      VARCHAR(64) NOT NULL,
  item_name    VARCHAR(512),
  item_url     VARCHAR(1024),
  qty          INT,
  sale_date    VARCHAR(32) NOT NULL,
  collected_at VARCHAR(32) NOT NULL,
  KEY idx_market_orders_seller    (seller_id),
  KEY idx_market_orders_sale_date (sale_date),
  KEY idx_market_orders_item      (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_order_watermarks (
  seller_id              VARCHAR(64) NOT NULL PRIMARY KEY,
  signature_json         TEXT NOT NULL,
  last_run_at            VARCHAR(32),
  pages_scanned_last_run INT,
  orders_added_last_run  INT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_order_run_meta (
  id                  INT NOT NULL PRIMARY KEY,   -- 항상 1 (코드에서 강제)
  last_run_at         VARCHAR(32),
  last_run_stats_json TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_sellers (
  seller_id      VARCHAR(64) NOT NULL PRIMARY KEY,
  seller_name    VARCHAR(255),
  seller_type    VARCHAR(32),
  seller_url     VARCHAR(1024),
  country        VARCHAR(64),
  follower_count INT,
  listing_count  INT,
  order_count    INT,
  first_seen_at  VARCHAR(32),
  updated_at     VARCHAR(32)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_item_images (
  item_id    VARCHAR(64)   NOT NULL,
  position   INT           NOT NULL,
  image_url  VARCHAR(1024) NOT NULL,
  PRIMARY KEY (item_id, position),
  KEY idx_market_item_images_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_stats_history (
  item_id       VARCHAR(64) NOT NULL,
  observed_at   VARCHAR(32) NOT NULL,
  view_count    INT,
  fav_count     INT,
  inquiry_count INT,
  PRIMARY KEY (item_id, observed_at),
  KEY idx_market_stats_history_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_item_variants (
  item_id       VARCHAR(64)  NOT NULL,
  variant_sku   VARCHAR(128) NOT NULL,
  color         VARCHAR(128),
  size          VARCHAR(128),
  price         INT,
  availability  VARCHAR(32),
  stock_min     INT,
  stock_max     INT,
  PRIMARY KEY (item_id, variant_sku),
  KEY idx_market_item_variants_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_revisit_state (
  item_id          VARCHAR(64) NOT NULL PRIMARY KEY,
  tier             VARCHAR(16),
  base_tier        VARCHAR(16),
  last_observed_at VARCHAR(32),
  next_revisit_at  VARCHAR(32),
  obs_count        INT,
  last_velocity    DOUBLE,
  KEY idx_market_revisit_next (next_revisit_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_seller_scan_state (
  seller_id       VARCHAR(64) NOT NULL PRIMARY KEY,
  value_tier      VARCHAR(16),
  value_score     INT,
  last_scanned_at VARCHAR(32),
  next_scan_at    VARCHAR(32),
  KEY idx_market_seller_scan_next (next_scan_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
