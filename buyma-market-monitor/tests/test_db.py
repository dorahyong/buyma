import sqlite3
from pathlib import Path

from storage.db import connect, init_schema, SCHEMA_VERSION


def test_init_schema_creates_tables(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    try:
        init_schema(conn)
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"items", "price_history", "orders"}.issubset(names)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_init_schema_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    try:
        init_schema(conn)
        init_schema(conn)  # second call must not raise
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_items_pk_and_indexes(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    try:
        init_schema(conn)
        idx_names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_items_seller" in idx_names
        assert "idx_items_status" in idx_names
        assert "idx_price_history_item" in idx_names
    finally:
        conn.close()


def test_init_schema_creates_orders_sellers_tables(tmp_path: Path):
    conn = connect(tmp_path / "test.db")
    try:
        init_schema(conn)
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"orders", "order_watermarks", "order_run_meta", "sellers"}.issubset(names)
        idx = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {"idx_orders_seller", "idx_orders_sale_date", "idx_orders_item"}.issubset(idx)
    finally:
        conn.close()


def test_schema_v2_items_has_new_columns(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
        for c in ("size_guide_text", "view_count", "fav_count", "inquiry_count",
                  "brand_model_number", "tags", "themes", "size_chart_json",
                  "listed_at"):
            assert c in cols, f"missing column {c}"
        assert "raw_meta_json" not in cols, "raw_meta_json should be removed"
    finally:
        conn.close()


def test_schema_v3_stats_history_has_inquiry_count(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stats_history)")}
        assert "inquiry_count" in cols, "stats_history missing inquiry_count"
    finally:
        conn.close()


def test_schema_v2_new_tables_exist(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"item_images", "stats_history", "item_variants"}.issubset(names)
    finally:
        conn.close()


def test_schema_version_is_8(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    finally:
        conn.close()


def test_item_variants_pk_and_index(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        idx = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_item_images_item" in idx
        assert "idx_stats_history_item" in idx
        assert "idx_item_variants_item" in idx
    finally:
        conn.close()


def test_schema_v2_preserves_orders_sellers_tables(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"orders", "order_watermarks", "order_run_meta", "sellers"}.issubset(names)
    finally:
        conn.close()
