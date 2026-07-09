from crawler.scan_scheduler import (
    tier_for_rank, SELLER_SCAN_INTERVAL_DAYS, next_scan_at_from)


def test_tier_for_rank():
    assert tier_for_rank(0.0) == "HIGH"
    assert tier_for_rank(0.14) == "HIGH"
    assert tier_for_rank(0.15) == "MID"
    assert tier_for_rank(0.49) == "MID"
    assert tier_for_rank(0.50) == "LOW"
    assert tier_for_rank(0.99) == "LOW"


def test_intervals_and_next():
    assert SELLER_SCAN_INTERVAL_DAYS == {"HIGH": 1, "MID": 4, "LOW": 21}
    assert next_scan_at_from("2026-07-01T00:00:00+09:00", "HIGH") == "2026-07-02T00:00:00+09:00"
    assert next_scan_at_from("2026-07-01T00:00:00+09:00", "MID") == "2026-07-05T00:00:00+09:00"
    assert next_scan_at_from("2026-07-01T00:00:00+09:00", "LOW") == "2026-07-22T00:00:00+09:00"


from storage.db import connect, init_schema
from storage.items_repo import upsert_scanned_item
from crawler.value_scan import run_value_scan


def test_run_value_scan_scans_only_due_and_advances(tmp_path, monkeypatch):
    db = tmp_path / "v.db"
    conn = connect(str(db)); init_schema(conn)
    conn.execute("INSERT INTO sellers (seller_id) VALUES ('s1')")
    upsert_scanned_item(conn, item_id="a", seller_id="s1", name="n", price=1,
                        now="2026-06-01T00:00:00+09:00")
    conn.close()

    scanned = {}
    def fake_run_page_scan(**kw):
        from crawler.page_scan import ScanSummary
        from storage import scan_repo
        scanned["sellers"] = list(kw["sellers"])
        # simulate the real page_scan: mark items.last_seen_at = now AND advance each
        # completed seller's schedule per-seller (marking now lives in page_scan).
        c = connect(str(db))
        tier_of = kw.get("tier_of") or {}
        for sid in kw["sellers"]:
            c.execute("UPDATE items SET last_seen_at = ? WHERE seller_id = ?", (kw["now"], sid))
            scan_repo.mark_seller_scanned(c, sid, tier=tier_of.get(sid, "LOW"), now=kw["now"])
        c.close()
        return ScanSummary(sellers_scanned=len(kw["sellers"]))
    import crawler.value_scan as vs
    monkeypatch.setattr(vs, "run_page_scan", fake_run_page_scan)

    summary = run_value_scan(
        db_path=str(db), client_factory=lambda: None, num_workers=2,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None,
        recent_cutoff="2026/06/01")
    assert scanned["sellers"] == ["s1"]
    conn = connect(str(db))
    row = conn.execute("SELECT next_scan_at, value_tier FROM seller_scan_state WHERE seller_id='s1'").fetchone()
    assert row[0] != "2026-07-01T00:00:00+09:00"   # advanced past now (completed → scheduled)


def test_run_value_scan_no_due_returns_empty(tmp_path, monkeypatch):
    db = tmp_path / "v2.db"
    conn = connect(str(db)); init_schema(conn); conn.close()
    import crawler.value_scan as vs
    called = {"n": 0}
    def fake_run_page_scan(**kw):
        called["n"] += 1
        from crawler.page_scan import ScanSummary
        return ScanSummary()
    monkeypatch.setattr(vs, "run_page_scan", fake_run_page_scan)
    summary = run_value_scan(
        db_path=str(db), client_factory=lambda: None, num_workers=2,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None,
        recent_cutoff="2026/06/01")
    assert called["n"] == 0   # no sellers → no due → run_page_scan not called
    from crawler.page_scan import ScanSummary
    assert isinstance(summary, ScanSummary)
