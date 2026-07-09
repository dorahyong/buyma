"""Sellers CRUD. Operates on an open sqlite3 connection."""
import sqlite3

_COLS = [
    "seller_id", "seller_name", "seller_type", "seller_url", "country",
    "follower_count", "listing_count", "order_count", "first_seen_at", "updated_at",
]


def upsert_sellers(conn: sqlite3.Connection, sellers: dict[str, dict]) -> None:
    """Insert/update sellers. first_seen_at is preserved from the existing row when present."""
    for seller_id, data in sellers.items():
        existing = conn.execute(
            "SELECT first_seen_at FROM sellers WHERE seller_id = ?", (seller_id,)
        ).fetchone()
        row = {c: data.get(c) for c in _COLS}
        row["seller_id"] = seller_id
        if existing is not None and existing["first_seen_at"] is not None:
            row["first_seen_at"] = existing["first_seen_at"]
        conn.execute(
            """
            INSERT INTO sellers (
              seller_id, seller_name, seller_type, seller_url, country,
              follower_count, listing_count, order_count, first_seen_at, updated_at
            ) VALUES (
              :seller_id, :seller_name, :seller_type, :seller_url, :country,
              :follower_count, :listing_count, :order_count, :first_seen_at, :updated_at
            )
            ON CONFLICT(seller_id) DO UPDATE SET
              seller_name    = excluded.seller_name,
              seller_type    = excluded.seller_type,
              seller_url     = excluded.seller_url,
              country        = excluded.country,
              follower_count = excluded.follower_count,
              listing_count  = excluded.listing_count,
              order_count    = excluded.order_count,
              first_seen_at  = excluded.first_seen_at,
              updated_at     = excluded.updated_at
            """,
            row,
        )


def load_sellers(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM sellers").fetchall()
    return {row["seller_id"]: {c: row[c] for c in _COLS} for row in rows}


def get_seller(conn: sqlite3.Connection, seller_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sellers WHERE seller_id = ?", (seller_id,)
    ).fetchone()
    return {c: row[c] for c in _COLS} if row is not None else None
