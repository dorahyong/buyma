from pathlib import Path

from storage.db import connect, init_schema
from storage.items_repo import (
    upsert_scanned_item,
    record_price_observation,
    mark_status,
    get_active_item_ids_for_seller,
    get_item,
    update_detail_fields,
    replace_item_images, replace_item_variants, record_stats_observation,
    record_stylehaus_observation, record_variant_history_delta,
    get_unenriched_active_item_ids_for_seller,
    get_seller_ids_with_pending_enrich,
)


def make_conn(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    return conn


def test_upsert_inserts_new_item_as_active(tmp_path: Path):
    conn = make_conn(tmp_path)
    is_new = upsert_scanned_item(
        conn,
        item_id="100",
        seller_id="S1",
        name="Test Item",
        price=1000,
        now="2026-06-09T10:00:00+09:00",
    )
    assert is_new is True
    row = get_item(conn, "100")
    assert row["status"] == "ACTIVE"
    assert row["current_price"] == 1000
    assert row["first_seen_at"] == "2026-06-09T10:00:00+09:00"
    assert row["last_seen_at"] == "2026-06-09T10:00:00+09:00"


def test_upsert_existing_updates_last_seen_keeps_first_seen(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    is_new = upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-10T10:00:00+09:00")
    assert is_new is False
    row = get_item(conn, "100")
    assert row["first_seen_at"] == "2026-06-09T10:00:00+09:00"
    assert row["last_seen_at"] == "2026-06-10T10:00:00+09:00"


def test_upsert_resurrects_sold_out(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    mark_status(conn, "100", "SOLD_OUT", "2026-06-10T00:00:00+09:00")
    assert get_item(conn, "100")["status"] == "SOLD_OUT"

    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-11T10:00:00+09:00")
    row = get_item(conn, "100")
    assert row["status"] == "ACTIVE"
    assert row["sold_out_at"] is None


def test_record_price_observation_appends_row(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    record_price_observation(conn, "100", 1000, "2026-06-09T10:00:00+09:00")
    record_price_observation(conn, "100", 1100, "2026-06-10T10:00:00+09:00")
    rows = list(conn.execute(
        "SELECT price FROM price_history WHERE item_id=? ORDER BY observed_at",
        ("100",),
    ))
    assert [r["price"] for r in rows] == [1000, 1100]


def test_get_active_item_ids_for_seller(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-09T10:00:00+09:00")
    upsert_scanned_item(conn, "2", "S1", "B", 200, "2026-06-09T10:00:00+09:00")
    upsert_scanned_item(conn, "3", "S2", "C", 300, "2026-06-09T10:00:00+09:00")
    mark_status(conn, "2", "SOLD_OUT", "2026-06-10T00:00:00+09:00")
    ids = get_active_item_ids_for_seller(conn, "S1")
    assert ids == {"1"}


def test_mark_status_deleted_sets_deleted_at(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    mark_status(conn, "100", "DELETED", "2026-06-10T00:00:00+09:00")
    row = get_item(conn, "100")
    assert row["status"] == "DELETED"
    assert row["deleted_at"] == "2026-06-10T00:00:00+09:00"
    assert row["sold_out_at"] is None


def test_update_detail_fields(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    update_detail_fields(
        conn,
        item_id="100",
        brand="LEMAIRE",
        category_path="バッグ > ショルダー",
        origin_country="韓国",
        image_url="https://example.com/x.jpg",
        description="full body",
        size_guide_text="S=95",
        view_count=42,
        fav_count=7,
        inquiry_count=15,
        brand_model_number="BG0074",
        tags='["ロゴ"]',
        themes="円高還元セール特集！",
        size_chart_json='{"S":{"胸囲":"90cm"}}',
        listed_at="2026-06-09T15:20:53+09:00",
        fetched_at="2026-06-09T11:00:00+09:00",
    )
    row = get_item(conn, "100")
    assert row["brand"] == "LEMAIRE"
    assert row["category_path"] == "バッグ > ショルダー"
    assert row["origin_country"] == "韓国"
    assert row["image_url"] == "https://example.com/x.jpg"
    assert row["description"] == "full body"
    assert row["size_guide_text"] == "S=95"
    assert row["view_count"] == 42
    assert row["fav_count"] == 7
    assert row["inquiry_count"] == 15
    assert row["brand_model_number"] == "BG0074"
    assert row["tags"] == '["ロゴ"]'
    assert row["themes"] == "円高還元セール特集！"
    assert row["size_chart_json"] == '{"S":{"胸囲":"90cm"}}'
    assert row["listed_at"] == "2026-06-09T15:20:53+09:00"
    assert row["detail_fetched_at"] == "2026-06-09T11:00:00+09:00"


def test_update_detail_fields_listed_at_write_once(tmp_path: Path):
    """listed_at is set only while NULL; a later value must not overwrite it."""
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "200", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")

    def enrich(listed_at, fetched_at):
        update_detail_fields(
            conn, item_id="200", brand=None, category_path=None, origin_country=None,
            image_url=None, description=None, size_guide_text=None, view_count=None,
            fav_count=None, inquiry_count=None, brand_model_number=None, tags=None,
            themes=None, size_chart_json=None, listed_at=listed_at, fetched_at=fetched_at,
        )

    # 1) NULL 상태 → 첫 관측 시 채워짐
    enrich("2026-06-09T15:20:53+09:00", "2026-06-09T11:00:00+09:00")
    assert get_item(conn, "200")["listed_at"] == "2026-06-09T15:20:53+09:00"

    # 2) 재출품으로 값이 바뀌어도(다른 kokaidate) 기존 값 유지 (덮어쓰지 않음)
    enrich("2026-08-01T09:00:00+09:00", "2026-08-01T12:00:00+09:00")
    assert get_item(conn, "200")["listed_at"] == "2026-06-09T15:20:53+09:00"

    # 3) 새 관측이 None 이어도 기존 값 유지
    enrich(None, "2026-08-02T12:00:00+09:00")
    assert get_item(conn, "200")["listed_at"] == "2026-06-09T15:20:53+09:00"


def test_replace_item_images_inserts_ordered(tmp_path: Path):
    conn = make_conn(tmp_path)
    replace_item_images(conn, "100", ["a.jpg", "b.jpg", "c.jpg"])
    rows = list(conn.execute(
        "SELECT position, image_url FROM item_images WHERE item_id='100' ORDER BY position"
    ))
    assert [(r["position"], r["image_url"]) for r in rows] == [
        (0, "a.jpg"), (1, "b.jpg"), (2, "c.jpg"),
    ]


def test_replace_item_images_replaces_existing(tmp_path: Path):
    conn = make_conn(tmp_path)
    replace_item_images(conn, "100", ["a.jpg", "b.jpg"])
    replace_item_images(conn, "100", ["x.jpg"])
    rows = list(conn.execute(
        "SELECT image_url FROM item_images WHERE item_id='100' ORDER BY position"
    ))
    assert [r["image_url"] for r in rows] == ["x.jpg"]


def test_replace_item_images_empty_clears(tmp_path: Path):
    conn = make_conn(tmp_path)
    replace_item_images(conn, "100", ["a.jpg"])
    replace_item_images(conn, "100", [])
    cnt = conn.execute("SELECT COUNT(*) FROM item_images WHERE item_id='100'").fetchone()[0]
    assert cnt == 0


def test_replace_item_variants(tmp_path: Path):
    conn = make_conn(tmp_path)
    variants = [
        {"variant_sku": "v1", "color": "RED", "size": "S", "price": 1000,
         "availability": "InStock", "stock_min": None, "stock_max": None},
        {"variant_sku": "v2", "color": "RED", "size": "M", "price": 1100,
         "availability": "OutOfStock", "stock_min": 7, "stock_max": 15},
    ]
    replace_item_variants(conn, "100", variants)
    rows = list(conn.execute(
        "SELECT variant_sku, color, size, price, availability, stock_min, stock_max "
        "FROM item_variants WHERE item_id='100' ORDER BY variant_sku"
    ))
    assert len(rows) == 2
    assert rows[0]["variant_sku"] == "v1"
    assert rows[0]["price"] == 1000
    assert rows[1]["availability"] == "OutOfStock"
    assert rows[1]["stock_min"] == 7
    assert rows[1]["stock_max"] == 15


def test_replace_item_variants_replaces_existing(tmp_path: Path):
    conn = make_conn(tmp_path)
    replace_item_variants(conn, "100", [
        {"variant_sku": "v1", "color": "RED", "size": "S", "price": 1,
         "availability": None, "stock_min": None, "stock_max": None}])
    replace_item_variants(conn, "100", [
        {"variant_sku": "v2", "color": "BLUE", "size": "L", "price": 2,
         "availability": None, "stock_min": None, "stock_max": None}])
    rows = list(conn.execute(
        "SELECT variant_sku FROM item_variants WHERE item_id='100'"))
    assert [r["variant_sku"] for r in rows] == ["v2"]


def test_record_stats_observation_inserts_every_time(tmp_path: Path):
    conn = make_conn(tmp_path)
    record_stats_observation(conn, "100", 10, 2, 5, "2026-06-09T10:00:00+09:00")
    record_stats_observation(conn, "100", 10, 2, 5, "2026-06-10T10:00:00+09:00")
    rows = list(conn.execute(
        "SELECT observed_at, view_count, fav_count, inquiry_count FROM stats_history "
        "WHERE item_id='100' ORDER BY observed_at"
    ))
    assert len(rows) == 2
    assert rows[0]["view_count"] == 10 and rows[0]["fav_count"] == 2
    assert rows[0]["inquiry_count"] == 5


def test_record_stats_observation_ignores_duplicate_timestamp(tmp_path: Path):
    conn = make_conn(tmp_path)
    record_stats_observation(conn, "100", 10, 2, 5, "2026-06-09T10:00:00+09:00")
    record_stats_observation(conn, "100", 99, 99, 99, "2026-06-09T10:00:00+09:00")
    rows = list(conn.execute(
        "SELECT view_count, inquiry_count FROM stats_history WHERE item_id='100'"))
    assert len(rows) == 1
    assert rows[0]["view_count"] == 10  # first wins (INSERT OR IGNORE)
    assert rows[0]["inquiry_count"] == 5  # first wins (INSERT OR IGNORE)


def test_record_stylehaus_observation_writes_first_then_delta_only(tmp_path: Path):
    conn = make_conn(tmp_path)
    assert record_stylehaus_observation(conn, "100", False, 0, "2026-06-09T10:00:00+09:00")
    assert not record_stylehaus_observation(conn, "100", False, 0, "2026-06-10T10:00:00+09:00")
    assert record_stylehaus_observation(conn, "100", True, 3, "2026-06-11T10:00:00+09:00")
    assert not record_stylehaus_observation(conn, "100", True, 3, "2026-06-12T10:00:00+09:00")
    rows = list(conn.execute(
        "SELECT observed_at, has_style_haus, stylehaus_video_count FROM stylehaus_history "
        "WHERE item_id='100' ORDER BY observed_at"
    ))
    assert len(rows) == 2
    assert rows[0]["has_style_haus"] == 0 and rows[0]["stylehaus_video_count"] == 0
    assert rows[1]["has_style_haus"] == 1 and rows[1]["stylehaus_video_count"] == 3


def _v(sku, availability, color="RED", size="S"):
    return {
        "variant_sku": sku, "color": color, "size": size, "price": 1000,
        "availability": availability, "stock_min": None, "stock_max": None,
    }


def test_variant_history_baseline_on_first_observation(tmp_path: Path):
    conn = make_conn(tmp_path)
    variants = [_v("v1", "InStock"), _v("v2", "OutOfStock")]
    n = record_variant_history_delta(conn, "100", variants, "2026-08-06T10:00:00+09:00")
    assert n == 2
    replace_item_variants(conn, "100", variants)
    rows = list(conn.execute(
        "SELECT variant_sku, availability FROM variant_history "
        "WHERE item_id='100' ORDER BY variant_sku"
    ))
    assert [(r["variant_sku"], r["availability"]) for r in rows] == [("v1", 1), ("v2", 0)]


def test_variant_history_skips_unchanged(tmp_path: Path):
    conn = make_conn(tmp_path)
    variants = [_v("v1", "InStock"), _v("v2", "OutOfStock")]
    record_variant_history_delta(conn, "100", variants, "2026-08-06T10:00:00+09:00")
    replace_item_variants(conn, "100", variants)
    n = record_variant_history_delta(conn, "100", variants, "2026-08-06T11:00:00+09:00")
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM variant_history WHERE item_id='100'").fetchone()[0] == 2


def test_variant_history_records_instock_to_outofstock_and_restock(tmp_path: Path):
    conn = make_conn(tmp_path)
    v1 = [_v("v1", "InStock")]
    record_variant_history_delta(conn, "100", v1, "2026-08-06T10:00:00+09:00")
    replace_item_variants(conn, "100", v1)

    v2 = [_v("v1", "OutOfStock")]
    assert record_variant_history_delta(conn, "100", v2, "2026-08-06T11:00:00+09:00") == 1
    replace_item_variants(conn, "100", v2)

    v3 = [_v("v1", "InStock")]
    assert record_variant_history_delta(conn, "100", v3, "2026-08-06T12:00:00+09:00") == 1
    replace_item_variants(conn, "100", v3)

    rows = list(conn.execute(
        "SELECT observed_at, availability FROM variant_history "
        "WHERE item_id='100' AND variant_sku='v1' ORDER BY observed_at"
    ))
    assert [r["availability"] for r in rows] == [1, 0, 1]


def test_variant_history_records_new_and_deleted_sku(tmp_path: Path):
    conn = make_conn(tmp_path)
    first = [_v("v1", "InStock"), _v("v2", "InStock")]
    record_variant_history_delta(conn, "100", first, "2026-08-06T10:00:00+09:00")
    replace_item_variants(conn, "100", first)

    second = [_v("v2", "InStock"), _v("v3", "OutOfStock")]  # v1 gone, v3 new, v2 unchanged
    n = record_variant_history_delta(conn, "100", second, "2026-08-06T11:00:00+09:00")
    assert n == 2
    replace_item_variants(conn, "100", second)

    rows = {
        r["variant_sku"]: r["availability"]
        for r in conn.execute(
            "SELECT variant_sku, availability FROM variant_history "
            "WHERE item_id='100' AND observed_at='2026-08-06T11:00:00+09:00'"
        )
    }
    assert rows == {"v1": 2, "v3": 0}


def test_get_unenriched_active_item_ids_for_seller(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-18T10:00:00+09:00")
    upsert_scanned_item(conn, "2", "S1", "B", 200, "2026-06-18T10:00:00+09:00")
    upsert_scanned_item(conn, "3", "S2", "C", 300, "2026-06-18T10:00:00+09:00")
    update_detail_fields(
        conn, item_id="1", brand="x", category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None, view_count=None,
        fav_count=None, inquiry_count=None, brand_model_number=None, tags=None, themes=None,
        size_chart_json=None, listed_at=None, fetched_at="2026-06-18T11:00:00+09:00",
    )
    ids = get_unenriched_active_item_ids_for_seller(conn, "S1")
    assert ids == {"2"}


def test_get_unenriched_excludes_sold_out(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-18T10:00:00+09:00")
    mark_status(conn, "1", "SOLD_OUT", "2026-06-18T10:30:00+09:00")
    ids = get_unenriched_active_item_ids_for_seller(conn, "S1")
    assert ids == set()


def test_get_seller_ids_with_pending_enrich(tmp_path: Path):
    conn = make_conn(tmp_path)
    # S1: one item, enriched → completed (not pending)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-23T10:00:00+09:00")
    update_detail_fields(
        conn, item_id="1", brand="x", category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None, view_count=None,
        fav_count=None, inquiry_count=None, brand_model_number=None, tags=None, themes=None,
        size_chart_json=None, listed_at=None, fetched_at="2026-06-23T11:00:00+09:00",
    )
    # S2: one item, NOT enriched → pending
    upsert_scanned_item(conn, "2", "S2", "B", 200, "2026-06-23T10:00:00+09:00")
    # S3: two items, one enriched one not → pending
    upsert_scanned_item(conn, "3", "S3", "C", 300, "2026-06-23T10:00:00+09:00")
    upsert_scanned_item(conn, "4", "S3", "D", 400, "2026-06-23T10:00:00+09:00")
    update_detail_fields(
        conn, item_id="3", brand="x", category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None, view_count=None,
        fav_count=None, inquiry_count=None, brand_model_number=None, tags=None, themes=None,
        size_chart_json=None, listed_at=None, fetched_at="2026-06-23T11:00:00+09:00",
    )
    pending = get_seller_ids_with_pending_enrich(conn)
    assert pending == {"S2", "S3"}  # S1 fully enriched → excluded


def test_get_seller_ids_with_pending_enrich_excludes_sold_out(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-23T10:00:00+09:00")
    mark_status(conn, "1", "SOLD_OUT", "2026-06-23T10:30:00+09:00")
    # S1's only item is SOLD_OUT (not ACTIVE), so nothing pending → S1 not returned
    pending = get_seller_ids_with_pending_enrich(conn)
    assert "S1" not in pending
