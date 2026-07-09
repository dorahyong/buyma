"""Orders, watermarks, and run-meta CRUD. Operates on an open sqlite3 connection."""
import json
import sqlite3


def upsert_watermark(
    conn: sqlite3.Connection,
    seller_id: str,
    signature: list,
    last_run_at: str | None,
    pages_scanned: int | None,
    orders_added: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO order_watermarks (
          seller_id, signature_json, last_run_at,
          pages_scanned_last_run, orders_added_last_run
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(seller_id) DO UPDATE SET
          signature_json         = excluded.signature_json,
          last_run_at            = excluded.last_run_at,
          pages_scanned_last_run = excluded.pages_scanned_last_run,
          orders_added_last_run  = excluded.orders_added_last_run
        """,
        (
            seller_id,
            json.dumps(signature, ensure_ascii=False),
            last_run_at,
            pages_scanned,
            orders_added,
        ),
    )


def _row_to_watermark(row: sqlite3.Row) -> dict:
    return {
        "signature": json.loads(row["signature_json"]),
        "last_run_at": row["last_run_at"],
        "pages_scanned_last_run": row["pages_scanned_last_run"],
        "orders_added_last_run": row["orders_added_last_run"],
    }


def get_watermark(conn: sqlite3.Connection, seller_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM order_watermarks WHERE seller_id = ?", (seller_id,)
    ).fetchone()
    return _row_to_watermark(row) if row is not None else None


def load_all_watermarks(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM order_watermarks").fetchall()
    return {row["seller_id"]: _row_to_watermark(row) for row in rows}


def save_run_meta(
    conn: sqlite3.Connection, last_run_at: str | None, stats: dict | None
) -> None:
    conn.execute(
        """
        INSERT INTO order_run_meta (id, last_run_at, last_run_stats_json)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          last_run_at         = excluded.last_run_at,
          last_run_stats_json = excluded.last_run_stats_json
        """,
        (
            last_run_at,
            json.dumps(stats, ensure_ascii=False) if stats is not None else None,
        ),
    )


def load_run_meta(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM order_run_meta WHERE id = 1").fetchone()
    if row is None:
        return {"last_run_at": None, "last_run_stats": None}
    return {
        "last_run_at": row["last_run_at"],
        "last_run_stats": (
            json.loads(row["last_run_stats_json"])
            if row["last_run_stats_json"]
            else None
        ),
    }


def insert_orders(conn: sqlite3.Connection, orders: list[dict]) -> None:
    """Append order rows. No dedup — the caller's watermark prevents re-collection."""
    if not orders:
        return
    conn.executemany(
        """
        INSERT INTO orders (
          seller_id, item_id, item_name, item_url, qty, sale_date, collected_at
        ) VALUES (
          :seller_id, :item_id, :item_name, :item_url, :qty, :sale_date, :collected_at
        )
        """,
        [
            {
                "seller_id": o["seller_id"],
                "item_id": o["item_id"],
                "item_name": o.get("item_name"),
                "item_url": o.get("item_url"),
                "qty": o.get("qty"),
                "sale_date": o["sale_date"],
                "collected_at": o["collected_at"],
            }
            for o in orders
        ],
    )


def insert_orders_bounded(
    conn: sqlite3.Connection,
    seller_id: str,
    orders: list[dict],
    prev_max: str | None,
    overcollected: bool,
) -> int:
    """Insert new orders, returning the number of rows inserted.

    Normal case (overcollected=False): insert everything (watermark already
    bounded the increment). Over-collection case: the crawler fell back to the
    date ceiling, so reconcile the boundary date (sale_date == prev_max) against
    existing DB rows to avoid re-inserting already-collected same-day orders.
    Orders strictly above prev_max are always genuinely new.

    Invariant: the boundary reconcile assumes the DB row count for
    (seller_id, prev_max) reflects what was previously collected for that date.
    This holds because orders and watermarks are written together each run; it
    would break only if the orders table were externally truncated while
    watermarks survived.
    """
    if not overcollected or prev_max is None:
        insert_orders(conn, orders)
        return len(orders)

    above = [o for o in orders if o["sale_date"] > prev_max]
    boundary = [o for o in orders if o["sale_date"] == prev_max]

    to_insert = list(above)
    if boundary:
        existing = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE seller_id = ? AND sale_date = ?",
            (seller_id, prev_max),
        ).fetchone()[0]
        surplus = max(0, len(boundary) - existing)
        to_insert.extend(boundary[:surplus])

    insert_orders(conn, to_insert)
    return len(to_insert)
