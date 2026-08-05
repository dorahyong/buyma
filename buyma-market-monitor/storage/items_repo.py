"""Items table CRUD. Operates on an open sqlite3 connection."""
import sqlite3


def upsert_scanned_item(
    conn: sqlite3.Connection,
    item_id: str,
    seller_id: str,
    name: str,
    price: int | None,
    now: str,
) -> bool:
    """Insert a new item or update last_seen_at + resurrect if needed.

    Returns True if the item was newly inserted (caller should queue detail fetch).
    """
    existing = conn.execute(
        "SELECT status FROM items WHERE item_id = ?",
        (item_id,),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO items (
              item_id, seller_id, name, current_price, status,
              first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (item_id, seller_id, name, price, now, now),
        )
        return True

    conn.execute(
        """
        UPDATE items
        SET name = ?,
            current_price = ?,
            status = 'ACTIVE',
            sold_out_at = NULL,
            deleted_at = NULL,
            last_seen_at = ?
        WHERE item_id = ?
        """,
        (name, price, now, item_id),
    )
    return False


def record_price_observation(
    conn: sqlite3.Connection,
    item_id: str,
    price: int,
    observed_at: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO price_history (item_id, observed_at, price) VALUES (?, ?, ?)",
        (item_id, observed_at, price),
    )


def get_seller_items_state(
    conn: sqlite3.Connection, seller_id: str
) -> dict[str, tuple]:
    """One SELECT returning {item_id: (status, current_price)} for every item of a
    seller. Replaces the per-item get_item lookups in the reconcile loop so a whale
    seller costs one query instead of thousands of remote round-trips."""
    rows = conn.execute(
        "SELECT item_id, status, current_price FROM items WHERE seller_id = ?",
        (seller_id,),
    )
    return {r["item_id"]: (r["status"], r["current_price"]) for r in rows}


# NOTE: status is bound as a parameter (not a literal in the VALUES tuple) so
# PyMySQL's executemany can rewrite this into a single multi-row INSERT.
_BULK_UPSERT_SQL = (
    "INSERT INTO items "
    "(item_id, seller_id, name, current_price, status, first_seen_at, last_seen_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(item_id) DO UPDATE SET "
    "name = excluded.name, "
    "current_price = excluded.current_price, "
    "status = 'ACTIVE', "
    "sold_out_at = NULL, "
    "deleted_at = NULL, "
    "last_seen_at = excluded.last_seen_at"
)


def bulk_upsert_scanned_items(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """Insert-or-update many scanned items in ONE batched statement.
    rows: (item_id, seller_id, name, current_price, status, first_seen_at, last_seen_at).
    On conflict, existing rows are reactivated (status ACTIVE, sold_out/deleted cleared)
    and keep their original first_seen_at; new rows take first_seen_at from the tuple.
    Collapses thousands of per-item round-trips into a single multi-row upsert."""
    if not rows:
        return
    conn.executemany(_BULK_UPSERT_SQL, rows)


def bulk_record_price_observations(
    conn: sqlite3.Connection, price_rows: list[tuple]
) -> None:
    """INSERT OR IGNORE many (item_id, observed_at, price) points in one batched
    statement. Duplicate (item_id, observed_at) rows are ignored."""
    if not price_rows:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO price_history (item_id, observed_at, price) VALUES (?, ?, ?)",
        price_rows,
    )


def mark_status(
    conn: sqlite3.Connection,
    item_id: str,
    status: str,
    now: str,
) -> None:
    """Set status to SOLD_OUT or DELETED, stamping the appropriate timestamp."""
    if status == "SOLD_OUT":
        conn.execute(
            "UPDATE items SET status='SOLD_OUT', sold_out_at=?, deleted_at=NULL WHERE item_id=?",
            (now, item_id),
        )
    elif status == "DELETED":
        conn.execute(
            "UPDATE items SET status='DELETED', deleted_at=?, sold_out_at=NULL WHERE item_id=?",
            (now, item_id),
        )
    else:
        raise ValueError(f"unsupported status: {status}")


def get_active_item_ids_for_seller(
    conn: sqlite3.Connection, seller_id: str
) -> set[str]:
    rows = conn.execute(
        "SELECT item_id FROM items WHERE seller_id = ? AND status = 'ACTIVE'",
        (seller_id,),
    )
    return {r["item_id"] for r in rows}


def get_item(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()


def get_current_price(conn: sqlite3.Connection, item_id: str) -> int | None:
    row = conn.execute(
        "SELECT current_price FROM items WHERE item_id = ?", (item_id,)
    ).fetchone()
    return row["current_price"] if row else None


def update_detail_fields(
    conn: sqlite3.Connection,
    item_id: str,
    brand: str | None,
    category_path: str | None,
    origin_country: str | None,
    image_url: str | None,
    description: str | None,
    size_guide_text: str | None,
    view_count: int | None,
    fav_count: int | None,
    inquiry_count: int | None,
    brand_model_number: str | None,
    tags: str | None,
    themes: str | None,
    size_chart_json: str | None,
    listed_at: str | None,
    fetched_at: str,
) -> None:
    # listed_at (出品日/kokaidate) is write-once: set only while NULL, never
    # overwritten. We want the FIRST observed listing date; a later re-listing
    # that resets kokaidate must NOT clobber it. COALESCE keeps the existing value.
    conn.execute(
        """
        UPDATE items SET
          brand = ?,
          category_path = ?,
          origin_country = ?,
          image_url = ?,
          description = ?,
          size_guide_text = ?,
          view_count = ?,
          fav_count = ?,
          inquiry_count = ?,
          brand_model_number = ?,
          tags = ?,
          themes = ?,
          size_chart_json = ?,
          listed_at = COALESCE(listed_at, ?),
          detail_fetched_at = ?
        WHERE item_id = ?
        """,
        (brand, category_path, origin_country, image_url, description,
         size_guide_text, view_count, fav_count, inquiry_count, brand_model_number,
         tags, themes, size_chart_json, listed_at, fetched_at, item_id),
    )


def replace_item_images(
    conn: sqlite3.Connection,
    item_id: str,
    image_urls: list[str],
) -> None:
    """Replace all images for an item with the given ordered list."""
    conn.execute("DELETE FROM item_images WHERE item_id = ?", (item_id,))
    conn.executemany(
        "INSERT INTO item_images (item_id, position, image_url) VALUES (?, ?, ?)",
        [(item_id, i, url) for i, url in enumerate(image_urls)],
    )


def replace_item_variants(
    conn: sqlite3.Connection,
    item_id: str,
    variants: list[dict],
) -> None:
    """Replace all variants for an item with the given list."""
    conn.execute("DELETE FROM item_variants WHERE item_id = ?", (item_id,))
    conn.executemany(
        """
        INSERT INTO item_variants
          (item_id, variant_sku, color, size, price, availability, stock_min, stock_max)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (item_id, v["variant_sku"], v.get("color"), v.get("size"),
             v.get("price"), v.get("availability"),
             v.get("stock_min"), v.get("stock_max"))
            for v in variants
        ],
    )


def record_stats_observation(
    conn: sqlite3.Connection,
    item_id: str,
    view_count: int | None,
    fav_count: int | None,
    inquiry_count: int | None,
    observed_at: str,
) -> None:
    """Record a view/fav/inquiry observation. Every observation is recorded (unchanged
    too) so that 'missing' and 'unchanged' can be distinguished. Duplicate
    (item_id, observed_at) is ignored."""
    conn.execute(
        "INSERT OR IGNORE INTO stats_history "
        "(item_id, observed_at, view_count, fav_count, inquiry_count) VALUES (?, ?, ?, ?, ?)",
        (item_id, observed_at, view_count, fav_count, inquiry_count),
    )


def record_stylehaus_observation(
    conn: sqlite3.Connection,
    item_id: str,
    has_style_haus: bool,
    stylehaus_video_count: int | None,
    observed_at: str,
) -> bool:
    """Append STYLE HAUS presence only when it differs from the latest row
    (first observation always written). Returns True if a row was inserted."""
    has_i = 1 if has_style_haus else 0
    count_i = int(stylehaus_video_count or 0)
    last = conn.execute(
        "SELECT has_style_haus, stylehaus_video_count FROM stylehaus_history "
        "WHERE item_id = ? ORDER BY observed_at DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    if last is not None:
        last_has = int(last["has_style_haus"] if isinstance(last, sqlite3.Row) else last[0])
        last_count = last["stylehaus_video_count"] if isinstance(last, sqlite3.Row) else last[1]
        last_count_i = int(last_count or 0)
        if last_has == has_i and last_count_i == count_i:
            return False
    conn.execute(
        "INSERT OR IGNORE INTO stylehaus_history "
        "(item_id, observed_at, has_style_haus, stylehaus_video_count) VALUES (?, ?, ?, ?)",
        (item_id, observed_at, has_i, count_i),
    )
    return True


def get_unenriched_active_item_ids_for_seller(
    conn: sqlite3.Connection, seller_id: str
) -> set[str]:
    """ACTIVE items for this seller that have not been detail-enriched yet
    (detail_fetched_at IS NULL). Used to recover items left un-enriched by a
    prior interrupted run, scoped to the seller being processed."""
    rows = conn.execute(
        "SELECT item_id FROM items "
        "WHERE seller_id = ? AND status = 'ACTIVE' AND detail_fetched_at IS NULL",
        (seller_id,),
    )
    return {r["item_id"] for r in rows}


def get_seller_ids_with_pending_enrich(conn: sqlite3.Connection) -> set[str]:
    """Seller IDs that still have ACTIVE items needing enrichment
    (detail_fetched_at IS NULL). Sellers whose ACTIVE items are all enriched
    are excluded. Used by --skip-completed to avoid re-scanning finished sellers."""
    rows = conn.execute(
        "SELECT DISTINCT seller_id FROM items "
        "WHERE status = 'ACTIVE' AND detail_fetched_at IS NULL"
    )
    return {r["seller_id"] for r in rows}
