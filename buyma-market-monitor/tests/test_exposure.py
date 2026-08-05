"""exposure URL/파서/repo/스윕 테스트."""
from pathlib import Path

from crawler.exposure import build_search_url, parse_search_page, run_exposure_sweep
from storage.db import connect, init_schema
from storage import exposure_repo, items_repo

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_search_url_encodes_spaces_and_keeps_o1():
    url = build_search_url("DD1391 100")
    assert url.startswith("https://www.buyma.com/r/-O1/")
    assert "DD1391%20100" in url
    assert url.endswith("/")


def test_parse_search_page_preserves_display_order():
    html = (FIXTURES / "search_o1_page.html").read_text(encoding="utf-8")
    parsed = parse_search_page(html)
    listings = parsed["listings"]
    assert [r["item_id"] for r in listings] == ["111111111", "222222222", "333333333"]
    assert [r["rank"] for r in listings] == [1, 2, 3]
    assert listings[0]["price_yen"] == 12000
    assert listings[0]["seller_id"] == "1001"
    assert listings[0]["seller_name"] == "SellerA"
    assert listings[1]["seller_id"] == "2002"
    assert parsed["total_results"] == 1234
    # floor would be 9800 among prices
    assert min(r["price_yen"] for r in listings) == 9800


def test_parse_search_page_empty():
    html = (FIXTURES / "search_o1_empty.html").read_text(encoding="utf-8")
    parsed = parse_search_page(html)
    assert parsed["listings"] == []
    assert parsed["total_results"] == 0


def test_list_competitive_and_pending(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    now = "2026-08-04T10:00:00+09:00"
    # 경쟁 품번 X: 셀러 2명
    items_repo.upsert_scanned_item(conn, "1", "s1", "a", 100, now)
    items_repo.upsert_scanned_item(conn, "2", "s2", "b", 100, now)
    conn.execute(
        "UPDATE items SET brand_model_number=?, status='ACTIVE' WHERE item_id IN ('1','2')",
        ("MODEL-X",),
    )
    # 단일 셀러 품번 Y: 제외
    items_repo.upsert_scanned_item(conn, "3", "s1", "c", 100, now)
    conn.execute(
        "UPDATE items SET brand_model_number=?, status='ACTIVE' WHERE item_id='3'",
        ("MODEL-Y",),
    )
    comps = exposure_repo.list_competitive_models(conn)
    assert comps == ["MODEL-X"]
    targets = exposure_repo.list_target_models(conn)
    assert targets == ["MODEL-X"]
    pending = exposure_repo.list_pending_models(conn, "2026-08-04")
    assert pending == ["MODEL-X"]

    exposure_repo.save_exposure_result(
        conn, model_query="MODEL-X", observed_at=now, status="ok",
        listings=[{"rank": 1, "item_id": "1", "price_yen": 100,
                   "seller_name": "A", "seller_id": "s1"}],
    )
    assert exposure_repo.list_pending_models(conn, "2026-08-04") == []
    # 다른 날은 다시 pending
    assert exposure_repo.list_pending_models(conn, "2026-08-05") == ["MODEL-X"]
    conn.close()


def test_save_exposure_empty_still_records_snapshot(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    sid = exposure_repo.save_exposure_result(
        conn, model_query="Z", observed_at="2026-08-04T11:00:00+09:00",
        status="empty", listings=[],
    )
    assert sid > 0
    snap = conn.execute(
        "SELECT status, n_results_page1 FROM exposure_snapshot WHERE snapshot_id=?",
        (sid,),
    ).fetchone()
    assert snap[0] == "empty"
    assert snap[1] == 0
    hist = conn.execute(
        "SELECT COUNT(*) FROM exposure_history WHERE snapshot_id=?", (sid,)
    ).fetchone()[0]
    assert hist == 0
    conn.close()


class _FakeClient:
    def __init__(self, html_by_url_substr: dict[str, str]):
        self._map = html_by_url_substr

    def get(self, url: str):
        from crawler.client import FetchResult
        for key, html in self._map.items():
            if key in url:
                return FetchResult(text=html, status_code=200)
        return FetchResult(text="<html></html>", status_code=200)

    def close(self):
        pass


def test_run_exposure_sweep_writes_history(tmp_path: Path):
    db = tmp_path / "t.db"
    conn = connect(db)
    init_schema(conn)
    now = "2026-08-04T12:00:00+09:00"
    items_repo.upsert_scanned_item(conn, "1", "s1", "a", 100, now)
    items_repo.upsert_scanned_item(conn, "2", "s2", "b", 100, now)
    conn.execute(
        "UPDATE items SET brand_model_number=?, status='ACTIVE' WHERE item_id IN ('1','2')",
        ("DD1391",),
    )
    conn.close()

    html = (FIXTURES / "search_o1_page.html").read_text(encoding="utf-8")
    errors = []
    summary = run_exposure_sweep(
        db_path=db,
        client_factory=lambda: _FakeClient({"DD1391": html}),
        num_workers=1,
        now=now,
        on_error=lambda **kw: errors.append(kw),
        max_hours=None,
    )
    assert summary.models_queued == 1
    assert summary.ok == 1
    assert errors == []

    conn = connect(db)
    n_hist = conn.execute("SELECT COUNT(*) FROM exposure_history").fetchone()[0]
    assert n_hist == 3
    ranks = [r[0] for r in conn.execute(
        "SELECT rank FROM exposure_history ORDER BY rank"
    ).fetchall()]
    assert ranks == [1, 2, 3]
    state = conn.execute(
        "SELECT last_status FROM exposure_state WHERE model_query='DD1391'"
    ).fetchone()[0]
    assert state == "ok"
    conn.close()
