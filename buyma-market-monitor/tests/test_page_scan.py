import threading
from storage.db import connect, init_schema
from storage.items_repo import upsert_scanned_item, get_active_item_ids_for_seller
import crawler.page_scan as page_scan
from crawler.page_scan import run_page_scan, ScanSummary
from crawler.circuit_breaker import CircuitBreaker


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    def get(self, url):
        import re
        m = re.search(r"/buyer/([^/]+)/item_(\d+)\.html", url)
        sid, page = m.group(1), int(m.group(2))
        return _Resp(f"{sid}:{page}")

    def close(self):
        pass


def _install_fake_parsers(monkeypatch, pages, maxpages):
    def fake_parse_items(text):
        sid, page = text.split(":")
        return list(pages.get((sid, int(page)), []))
    def fake_parse_max(text):
        sid, page = text.split(":")
        return maxpages[sid]
    monkeypatch.setattr(page_scan, "parse_seller_items", fake_parse_items)
    monkeypatch.setattr(page_scan, "parse_seller_items_max_page", fake_parse_max)


def _item(iid, price=100, name="n"):
    return {"item_id": iid, "name": name, "price": price}


def test_scan_distributes_pages_and_reconciles(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    conn = connect(str(db)); init_schema(conn); conn.close()
    pages = {
        ("W", 1): [_item("w1"), _item("w2")],
        ("W", 2): [_item("w3"), _item("w4")],
        ("W", 3): [_item("w5")],
        ("S", 1): [_item("s1")],
    }
    maxpages = {"W": 3, "S": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)
    summary = run_page_scan(
        db_path=str(db), sellers=["W", "S"],
        client_factory=lambda: _FakeClient(), num_workers=4,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None)
    assert isinstance(summary, ScanSummary)
    assert summary.sellers_scanned == 2
    assert summary.items_new == 6
    conn = connect(str(db))
    assert get_active_item_ids_for_seller(conn, "W") == {"w1", "w2", "w3", "w4", "w5"}
    assert get_active_item_ids_for_seller(conn, "S") == {"s1"}
    assert conn.execute(
        "SELECT COUNT(*) FROM items WHERE detail_fetched_at IS NOT NULL").fetchone()[0] == 0


def test_scan_detects_disappeared_and_price_change(tmp_path, monkeypatch):
    db = tmp_path / "s2.db"
    conn = connect(str(db)); init_schema(conn)
    now0 = "2026-06-01T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="keep", seller_id="W", name="n", price=100, now=now0)
    upsert_scanned_item(conn, item_id="gone", seller_id="W", name="n", price=100, now=now0)
    conn.close()
    pages = {("W", 1): [_item("keep", price=150), _item("new1")]}
    maxpages = {"W": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)
    summary = run_page_scan(
        db_path=str(db), sellers=["W"],
        client_factory=lambda: _FakeClient(), num_workers=2,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None)
    assert summary.items_new == 1
    assert summary.price_changes == 1
    assert summary.disappeared == 1
    conn = connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM price_history WHERE item_id='keep'").fetchone()[0] >= 1


def test_scan_respects_deadline(tmp_path, monkeypatch):
    db = tmp_path / "s3.db"
    conn = connect(str(db)); init_schema(conn); conn.close()
    pages = {("S", 1): [_item("s1")]}
    maxpages = {"S": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)
    summary = run_page_scan(
        db_path=str(db), sellers=["S"],
        client_factory=lambda: _FakeClient(), num_workers=1,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None,
        max_hours=0.0)
    assert summary.sellers_scanned == 0


class _RespStatus:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code


class _ClassifyClient:
    def get(self, url):
        import re
        m = re.search(r"/buyer/([^/]+)/item_(\d+)\.html", url)
        if m:
            sid, page = m.group(1), int(m.group(2))
            return _Resp(f"{sid}:{page}")
        return _Resp("")

    def get_allowing_4xx(self, url):
        import re
        m = re.search(r"/item/([^/]+)/", url)
        iid = m.group(1)
        code = 404 if iid == "gone" else 200
        return _RespStatus("", code)

    def close(self):
        pass


def test_phase_b_classifies_disappeared(tmp_path, monkeypatch):
    db = tmp_path / "s4.db"
    conn = connect(str(db)); init_schema(conn)
    now0 = "2026-06-01T00:00:00+09:00"
    for iid in ("keep", "gone", "soldout"):
        upsert_scanned_item(conn, item_id=iid, seller_id="W", name="n", price=100, now=now0)
    conn.close()
    pages = {("W", 1): [_item("keep")]}
    maxpages = {"W": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)
    summary = run_page_scan(
        db_path=str(db), sellers=["W"],
        client_factory=lambda: _ClassifyClient(), num_workers=2,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None)
    assert summary.disappeared == 2
    assert summary.deleted == 1
    assert summary.sold_out == 1
    conn = connect(str(db))
    st = dict(conn.execute("SELECT item_id, status FROM items WHERE item_id IN ('gone','soldout')"))
    assert st["gone"] == "DELETED"
    assert st["soldout"] == "SOLD_OUT"


class _PageFailClient:
    """(W,1) ok returns [keep]; (W,2) fetch raises -> whole seller must be skipped."""
    def get(self, url):
        import re
        m = re.search(r"/buyer/([^/]+)/item_(\d+)\.html", url)
        sid, page = m.group(1), int(m.group(2))
        if sid == "W" and page == 2:
            raise RuntimeError("boom")
        return _Resp(f"{sid}:{page}")

    def close(self):
        pass


def test_partial_page_failure_skips_seller_no_false_disappear(tmp_path, monkeypatch):
    db = tmp_path / "pf.db"
    conn = connect(str(db)); init_schema(conn)
    now0 = "2026-06-01T00:00:00+09:00"
    for iid in ("keep", "onpage2a", "onpage2b"):
        upsert_scanned_item(conn, item_id=iid, seller_id="W", name="n", price=100, now=now0)
    conn.close()
    # W has 2 pages; page1 -> [keep]; page2 fetch fails (would have had onpage2a/b)
    pages = {("W", 1): [_item("keep")], ("W", 2): [_item("onpage2a"), _item("onpage2b")]}
    maxpages = {"W": 2}
    _install_fake_parsers(monkeypatch, pages, maxpages)
    summary = run_page_scan(
        db_path=str(db), sellers=["W"],
        client_factory=lambda: _PageFailClient(), num_workers=2,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None)
    # seller skipped -> NOT reconciled: no disappeared, nothing deleted/soldout
    assert summary.sellers_scanned == 0
    assert summary.sellers_skipped == 1
    assert summary.disappeared == 0
    assert summary.deleted == 0
    assert summary.sold_out == 0
    conn = connect(str(db))
    # all three items remain ACTIVE (no false disappearance)
    assert get_active_item_ids_for_seller(conn, "W") == {"keep", "onpage2a", "onpage2b"}


def test_empty_page1_does_not_mark_disappeared(tmp_path, monkeypatch):
    db = tmp_path / "e.db"
    conn = connect(str(db)); init_schema(conn)
    now0 = "2026-06-01T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="keep", seller_id="W", name="n", price=100, now=now0)
    conn.close()
    pages = {("W", 1): []}      # page1 ok but zero items parsed
    maxpages = {"W": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)
    summary = run_page_scan(
        db_path=str(db), sellers=["W"],
        client_factory=lambda: _FakeClient(), num_workers=1,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None)
    assert summary.sellers_scanned == 0     # empty-scan guard skips reconcile
    assert summary.disappeared == 0
    conn = connect(str(db))
    assert get_active_item_ids_for_seller(conn, "W") == {"keep"}   # untouched


def test_open_circuit_breaker_halts(tmp_path, monkeypatch):
    db = tmp_path / "cb.db"
    conn = connect(str(db)); init_schema(conn); conn.close()
    pages = {("S", 1): [_item("s1")]}
    maxpages = {"S": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)
    cb = CircuitBreaker(threshold=1, window_seconds=60)
    cb.record_block()   # threshold=1, so one call trips it
    assert cb.is_open()
    summary = run_page_scan(
        db_path=str(db), sellers=["S"],
        client_factory=lambda: _FakeClient(), num_workers=1,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None,
        circuit_breaker=cb)
    assert summary.sellers_scanned == 0
