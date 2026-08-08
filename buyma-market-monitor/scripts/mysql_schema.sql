-- buyma-market-monitor → buyma MySQL DB (market_ 접두어). InnoDB / utf8mb4.
-- 원본: storage/db.py 의 SQLite 스키마(schema_version=13) 1:1 변환.
-- tags = BUYMA 「タグ」 JSON 배열(옛 themes 컬럼 rename), themes = 단일 「テーマ」 이름.
-- listed_at = 出品(公開)日 kokaidate. write-once(NULL일 때만 기록, 재출품시에도 불변).
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
  tags                TEXT,
  themes              TEXT,
  size_chart_json     MEDIUMTEXT,
  status              VARCHAR(16)  NOT NULL,
  first_seen_at       VARCHAR(32)  NOT NULL,
  last_seen_at        VARCHAR(32)  NOT NULL,
  sold_out_at         VARCHAR(32),
  deleted_at          VARCHAR(32),
  detail_fetched_at   VARCHAR(32),
  listed_at           VARCHAR(32),
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
  KEY idx_market_orders_item      (item_id),
  KEY idx_market_orders_item_date   (item_id, sale_date),
  KEY idx_market_orders_seller_date (seller_id, sale_date),
  KEY idx_market_orders_date_seller (sale_date, seller_id)
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
  seller_id        VARCHAR(64),
  last_observed_at VARCHAR(32),
  next_revisit_at  VARCHAR(32),
  obs_count        INT,
  last_velocity    DOUBLE,
  KEY idx_market_revisit_next (next_revisit_at),
  KEY idx_market_revisit_tier_seller (tier, seller_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_seller_scan_state (
  seller_id       VARCHAR(64) NOT NULL PRIMARY KEY,
  value_tier      VARCHAR(16),
  value_score     INT,
  last_scanned_at VARCHAR(32),
  next_scan_at    VARCHAR(32),
  KEY idx_market_seller_scan_next (next_scan_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 품번 노출순위 스냅샷 (검색 /r/-O1/ 1페이지). append-only history + 요약 + 당일 상태.
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

-- STYLE HAUS 관련 동영상·関連記事 연동 유무 (상세 enrich 시 delta 기록).
CREATE TABLE IF NOT EXISTS market_stylehaus_history (
  item_id               VARCHAR(64) NOT NULL,
  observed_at           VARCHAR(32) NOT NULL,
  has_style_haus        TINYINT     NOT NULL,
  stylehaus_video_count INT,
  has_style_haus_post   TINYINT     NOT NULL DEFAULT 0,
  stylehaus_post_count  INT,
  PRIMARY KEY (item_id, observed_at),
  KEY idx_market_stylehaus_history_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 변형(색·사이즈) 재고 상태 이력. 상세 enrich 시 직전 대비 변화분만 기록.
-- availability: 0=OutOfStock, 1=InStock, 2=변형삭제
-- observed_at 은 market_stats_history 와 동일한 VARCHAR(32) ISO8601 (조인 정합).
CREATE TABLE IF NOT EXISTS market_variant_history (
  item_id      VARCHAR(64)  NOT NULL,
  variant_sku  VARCHAR(128) NOT NULL,
  observed_at  VARCHAR(32)  NOT NULL,
  availability TINYINT      NOT NULL COMMENT '0=OutOfStock, 1=InStock, 2=변형삭제',
  PRIMARY KEY (item_id, variant_sku, observed_at),
  KEY idx_mvh_observed (observed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
