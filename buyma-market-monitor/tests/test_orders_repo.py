from pathlib import Path

from storage.db import connect, init_schema
from storage import orders_repo


def make_conn(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    return conn


def test_insert_orders_appends_rows(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.insert_orders(conn, [
        {"seller_id": "S1", "item_id": "100", "item_name": "A", "item_url": "u1",
         "qty": 1, "sale_date": "2026/06/09", "collected_at": "2026-06-09T18:00:00+09:00"},
        {"seller_id": "S1", "item_id": "100", "item_name": "A", "item_url": "u1",
         "qty": 1, "sale_date": "2026/06/09", "collected_at": "2026-06-09T18:00:00+09:00"},
    ])
    rows = conn.execute("SELECT seller_id, item_id, qty FROM orders ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["seller_id"] == "S1"
    assert rows[0]["qty"] == 1


def test_insert_orders_empty_is_noop(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.insert_orders(conn, [])
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_upsert_and_get_watermark_roundtrip(tmp_path: Path):
    conn = make_conn(tmp_path)
    sig = [["2026/06/09", "100", 1], ["2026/06/08", "200", 2]]
    orders_repo.upsert_watermark(
        conn, "S1", signature=sig,
        last_run_at="2026-06-09T18:00:00+09:00", pages_scanned=3, orders_added=5,
    )
    wm = orders_repo.get_watermark(conn, "S1")
    assert wm["signature"] == sig
    assert wm["last_run_at"] == "2026-06-09T18:00:00+09:00"
    assert wm["pages_scanned_last_run"] == 3
    assert wm["orders_added_last_run"] == 5


def test_upsert_watermark_overwrites(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.upsert_watermark(conn, "S1", [["a"]], "t1", 1, 1)
    orders_repo.upsert_watermark(conn, "S1", [["b"]], "t2", 9, 9)
    wm = orders_repo.get_watermark(conn, "S1")
    assert wm["signature"] == [["b"]]
    assert wm["pages_scanned_last_run"] == 9
    assert conn.execute("SELECT COUNT(*) FROM order_watermarks").fetchone()[0] == 1


def test_get_watermark_missing_returns_none(tmp_path: Path):
    conn = make_conn(tmp_path)
    assert orders_repo.get_watermark(conn, "NOPE") is None


def test_load_all_watermarks(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.upsert_watermark(conn, "S1", [["a"]], "t1", 1, 1)
    orders_repo.upsert_watermark(conn, "S2", [["b"]], "t2", 2, 2)
    allwm = orders_repo.load_all_watermarks(conn)
    assert set(allwm) == {"S1", "S2"}
    assert allwm["S2"]["signature"] == [["b"]]


def test_save_and_load_run_meta(tmp_path: Path):
    conn = make_conn(tmp_path)
    stats = {"sellers_scanned": 401, "orders_added": 12, "errors": 0}
    orders_repo.save_run_meta(conn, "2026-06-09T18:00:00+09:00", stats)
    meta = orders_repo.load_run_meta(conn)
    assert meta["last_run_at"] == "2026-06-09T18:00:00+09:00"
    assert meta["last_run_stats"] == stats


def test_save_run_meta_overwrites_single_row(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.save_run_meta(conn, "t1", {"a": 1})
    orders_repo.save_run_meta(conn, "t2", {"a": 2})
    assert conn.execute("SELECT COUNT(*) FROM order_run_meta").fetchone()[0] == 1
    assert orders_repo.load_run_meta(conn)["last_run_at"] == "t2"


def test_load_run_meta_empty(tmp_path: Path):
    conn = make_conn(tmp_path)
    meta = orders_repo.load_run_meta(conn)
    assert meta == {"last_run_at": None, "last_run_stats": None}


def _order(seller_id, item_id, sale_date, qty=1):
    return {
        "seller_id": seller_id, "item_id": item_id, "item_name": "n",
        "item_url": "u", "qty": qty, "sale_date": sale_date,
        "collected_at": "2026-06-15T10:00:00+09:00",
    }


def test_insert_orders_bounded_not_overcollected_inserts_all(tmp_path: Path):
    conn = make_conn(tmp_path)
    n = orders_repo.insert_orders_bounded(
        conn, "S1",
        [_order("S1", "1", "2026/06/14"), _order("S1", "2", "2026/06/13")],
        prev_max="2026/06/14", overcollected=False,
    )
    assert n == 2
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2


def test_insert_orders_bounded_inserts_all_above_prev_max(tmp_path: Path):
    conn = make_conn(tmp_path)
    n = orders_repo.insert_orders_bounded(
        conn, "S1",
        [_order("S1", "1", "2026/06/15"), _order("S1", "2", "2026/06/14")],
        prev_max="2026/06/13", overcollected=True,
    )
    assert n == 2  # both strictly above prev_max -> all inserted
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2


def test_insert_orders_bounded_reconciles_boundary_day(tmp_path: Path):
    conn = make_conn(tmp_path)
    # DB already has 5 orders on the boundary date for S1
    orders_repo.insert_orders(conn, [_order("S1", str(i), "2026/06/13") for i in range(5)])
    # This run re-collected 7 on the boundary date + 1 newer
    new = [_order("S1", str(100 + i), "2026/06/13") for i in range(7)]
    new.append(_order("S1", "200", "2026/06/14"))
    n = orders_repo.insert_orders_bounded(conn, "S1", new, prev_max="2026/06/13", overcollected=True)
    # boundary: max(0, 7-5)=2 inserted; above: 1 inserted -> 3 total
    assert n == 3
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE sale_date='2026/06/13'").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE sale_date='2026/06/14'").fetchone()[0] == 1


def test_insert_orders_bounded_boundary_already_complete_inserts_zero(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.insert_orders(conn, [_order("S1", str(i), "2026/06/13") for i in range(5)])
    new = [_order("S1", str(100 + i), "2026/06/13") for i in range(3)]  # fewer than existing
    n = orders_repo.insert_orders_bounded(conn, "S1", new, prev_max="2026/06/13", overcollected=True)
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE sale_date='2026/06/13'").fetchone()[0] == 5
