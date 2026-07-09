"""SQLite connection and schema for the product monitoring pipeline."""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 6

_DDL = """
CREATE TABLE IF NOT EXISTS items (
  item_id             TEXT PRIMARY KEY,
  seller_id           TEXT NOT NULL,
  name                TEXT NOT NULL,
  current_price       INTEGER,
  brand               TEXT,
  category_path       TEXT,
  origin_country      TEXT,
  image_url           TEXT,
  description         TEXT,
  size_guide_text     TEXT,
  view_count          INTEGER,
  fav_count           INTEGER,
  inquiry_count       INTEGER,
  brand_model_number  TEXT,
  themes              TEXT,
  size_chart_json     TEXT,
  status              TEXT NOT NULL,
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  sold_out_at         TEXT,
  deleted_at          TEXT,
  detail_fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_seller ON items(seller_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE TABLE IF NOT EXISTS price_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  price        INTEGER NOT NULL,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_price_history_item ON price_history(item_id);

CREATE TABLE IF NOT EXISTS orders (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  seller_id    TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  item_name    TEXT,
  item_url     TEXT,
  qty          INTEGER,
  sale_date    TEXT NOT NULL,
  collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_seller    ON orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_orders_sale_date ON orders(sale_date);
CREATE INDEX IF NOT EXISTS idx_orders_item      ON orders(item_id);

CREATE TABLE IF NOT EXISTS order_watermarks (
  seller_id              TEXT PRIMARY KEY,
  signature_json         TEXT NOT NULL,
  last_run_at            TEXT,
  pages_scanned_last_run INTEGER,
  orders_added_last_run  INTEGER
);

CREATE TABLE IF NOT EXISTS order_run_meta (
  id                  INTEGER PRIMARY KEY CHECK (id = 1),
  last_run_at         TEXT,
  last_run_stats_json TEXT
);

CREATE TABLE IF NOT EXISTS sellers (
  seller_id      TEXT PRIMARY KEY,
  seller_name    TEXT,
  seller_type    TEXT,
  seller_url     TEXT,
  country        TEXT,
  follower_count INTEGER,
  listing_count  INTEGER,
  order_count    INTEGER,
  first_seen_at  TEXT,
  updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS item_images (
  item_id    TEXT NOT NULL,
  position   INTEGER NOT NULL,
  image_url  TEXT NOT NULL,
  PRIMARY KEY (item_id, position)
);
CREATE INDEX IF NOT EXISTS idx_item_images_item ON item_images(item_id);

CREATE TABLE IF NOT EXISTS stats_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  view_count    INTEGER,
  fav_count     INTEGER,
  inquiry_count INTEGER,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_stats_history_item ON stats_history(item_id);

CREATE TABLE IF NOT EXISTS item_variants (
  item_id       TEXT NOT NULL,
  variant_sku   TEXT NOT NULL,
  color         TEXT,
  size          TEXT,
  price         INTEGER,
  availability  TEXT,
  stock_min     INTEGER,
  stock_max     INTEGER,
  PRIMARY KEY (item_id, variant_sku)
);
CREATE INDEX IF NOT EXISTS idx_item_variants_item ON item_variants(item_id);

CREATE TABLE IF NOT EXISTS revisit_state (
  item_id          TEXT PRIMARY KEY,
  tier             TEXT,
  base_tier        TEXT,
  last_observed_at TEXT,
  next_revisit_at  TEXT,
  obs_count        INTEGER,
  last_velocity    REAL
);
CREATE INDEX IF NOT EXISTS idx_revisit_next ON revisit_state(next_revisit_at);

CREATE TABLE IF NOT EXISTS seller_scan_state (
  seller_id       TEXT PRIMARY KEY,
  value_tier      TEXT,
  value_score     INTEGER,
  last_scanned_at TEXT,
  next_scan_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_seller_scan_next ON seller_scan_state(next_scan_at);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
