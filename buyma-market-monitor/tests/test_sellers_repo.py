from pathlib import Path

from storage.db import connect, init_schema
from storage import sellers_repo


def make_conn(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    return conn


def _seller(sid, name, first_seen, updated):
    return {
        "seller_id": sid, "seller_name": name, "seller_type": "PERSONAL SHOPPER",
        "seller_url": f"https://www.buyma.com/buyer/{sid}/sales_1.html",
        "country": "한국", "follower_count": 10, "listing_count": 20, "order_count": 30,
        "first_seen_at": first_seen, "updated_at": updated,
    }


def test_upsert_and_load_sellers(tmp_path: Path):
    conn = make_conn(tmp_path)
    sellers_repo.upsert_sellers(conn, {
        "S1": _seller("S1", "INSEOUL", "t1", "t1"),
        "S2": _seller("S2", "OTHER", "t1", "t1"),
    })
    loaded = sellers_repo.load_sellers(conn)
    assert set(loaded) == {"S1", "S2"}
    assert loaded["S1"]["seller_name"] == "INSEOUL"
    assert loaded["S1"]["follower_count"] == 10


def test_upsert_preserves_first_seen_at(tmp_path: Path):
    conn = make_conn(tmp_path)
    sellers_repo.upsert_sellers(conn, {"S1": _seller("S1", "INSEOUL", "2026-06-09", "2026-06-09")})
    sellers_repo.upsert_sellers(conn, {"S1": _seller("S1", "INSEOUL-RENAMED", "2026-06-11", "2026-06-11")})
    row = sellers_repo.get_seller(conn, "S1")
    assert row["first_seen_at"] == "2026-06-09"
    assert row["seller_name"] == "INSEOUL-RENAMED"
    assert row["updated_at"] == "2026-06-11"


def test_get_seller_missing_returns_none(tmp_path: Path):
    conn = make_conn(tmp_path)
    assert sellers_repo.get_seller(conn, "NOPE") is None
