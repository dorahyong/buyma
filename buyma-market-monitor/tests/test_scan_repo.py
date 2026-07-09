from storage.db import connect, init_schema


def _mem():
    conn = connect(":memory:"); init_schema(conn); return conn


def test_seller_scan_state_table_exists():
    conn = _mem()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(seller_scan_state)")}
    assert cols == {"seller_id", "value_tier", "value_score", "last_scanned_at", "next_scan_at"}


from storage import scan_repo
from storage.items_repo import upsert_scanned_item


def _add_item(conn, iid, sid, now="2026-06-01T00:00:00+09:00"):
    upsert_scanned_item(conn, item_id=iid, seller_id=sid, name="n", price=100, now=now)


def _add_seller(conn, sid):
    conn.execute("INSERT INTO sellers (seller_id) VALUES (?)", (sid,))


def _set_tier(conn, iid, tier):
    conn.execute("INSERT INTO revisit_state (item_id, tier, base_tier, last_observed_at, "
                 "next_revisit_at, obs_count, last_velocity) VALUES (?,?,?,?,?,?,?)",
                 (iid, tier, tier, "x", "y", 1, None))


def test_recompute_and_due():
    conn = connect(":memory:"); init_schema(conn)
    for sid in ("hi", "mid", "low"):
        _add_seller(conn, sid)
    for i in range(5):
        _add_item(conn, f"h{i}", "hi"); _set_tier(conn, f"h{i}", "HOT")
    _add_item(conn, "m0", "mid"); _set_tier(conn, "m0", "WARM")
    _add_item(conn, "l0", "low"); _set_tier(conn, "l0", "COLD")
    now = "2026-07-01T00:00:00+09:00"
    scan_repo.recompute_seller_values(conn, now=now, recent_cutoff="2026/06/01")
    tiers = dict(conn.execute("SELECT seller_id, value_tier FROM seller_scan_state"))
    assert tiers == {"hi": "HIGH", "mid": "MID", "low": "LOW"}
    due = scan_repo.get_due_sellers(conn, now=now, limit=10)
    assert set(due) == {"hi", "mid", "low"}
    assert due[0] == "hi"


def test_mark_seller_scanned_advances():
    conn = connect(":memory:"); init_schema(conn)
    _add_seller(conn, "hi")
    scan_repo.recompute_seller_values(conn, now="2026-07-01T00:00:00+09:00", recent_cutoff="2026/06/01")
    scan_repo.mark_seller_scanned(conn, "hi", tier="HIGH", now="2026-07-01T00:00:00+09:00")
    nxt = conn.execute("SELECT next_scan_at FROM seller_scan_state WHERE seller_id='hi'").fetchone()[0]
    assert nxt == "2026-07-02T00:00:00+09:00"
    assert scan_repo.get_due_sellers(conn, now="2026-07-01T00:00:00+09:00", limit=10) == []


def test_recompute_counts_recent_orders():
    conn = connect(":memory:"); init_schema(conn)
    _add_seller(conn, "o")
    # 3 recent orders (>= cutoff) → HIGH via recent_orders>=3
    for i, d in enumerate(["2026/06/15", "2026/06/20", "2026/06/25", "2026/01/01"]):
        conn.execute("INSERT INTO orders (seller_id, item_id, sale_date, collected_at) "
                     "VALUES (?,?,?,?)", ("o", f"i{i}", d, "2026-07-01T00:00:00+09:00"))
    scan_repo.recompute_seller_values(conn, now="2026-07-01T00:00:00+09:00", recent_cutoff="2026/06/01")
    tier = conn.execute("SELECT value_tier FROM seller_scan_state WHERE seller_id='o'").fetchone()[0]
    assert tier == "HIGH"   # 3 recent orders (2026/01/01 excluded by cutoff)


def test_recompute_keeps_existing_next_scan_at():
    conn = connect(":memory:"); init_schema(conn)
    _add_seller(conn, "s")
    scan_repo.recompute_seller_values(conn, now="2026-07-01T00:00:00+09:00", recent_cutoff="2026/06/01")
    scan_repo.mark_seller_scanned(conn, "s", tier="LOW", now="2026-07-01T00:00:00+09:00")
    before = conn.execute("SELECT next_scan_at FROM seller_scan_state WHERE seller_id='s'").fetchone()[0]
    # recompute again — must NOT reset next_scan_at
    scan_repo.recompute_seller_values(conn, now="2026-07-10T00:00:00+09:00", recent_cutoff="2026/06/10")
    after = conn.execute("SELECT next_scan_at FROM seller_scan_state WHERE seller_id='s'").fetchone()[0]
    assert after == before
