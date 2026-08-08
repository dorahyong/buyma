import threading as _threading

from storage.db import connect, init_schema
from storage.items_repo import upsert_scanned_item, update_detail_fields
from crawler.revisit import apply_revisit, run_revisit
import crawler.monitor as monitor_mod


def _seed_item(conn, item_id, now):
    upsert_scanned_item(conn, item_id=item_id, seller_id="s1", name="n", price=100, now=now)


def test_apply_revisit_records_observation_and_state(monkeypatch):
    conn = connect(":memory:")
    init_schema(conn)
    now1 = "2026-06-27T00:00:00+09:00"
    now2 = "2026-06-29T00:00:00+09:00"
    _seed_item(conn, "1", now1)

    fake = {
        "name": "n", "brand": None, "category_path": None, "origin_country": None,
        "image_url": None, "description": None, "size_guide_text": None,
        "view_count": 100, "fav_count": 10, "inquiry_count": 1,
        "brand_model_number": None, "tags": None, "themes": None, "listed_at": None,
        "size_chart": None,
        "image_urls": [], "variants": [],
        "has_style_haus": False, "stylehaus_video_count": 0,
        "has_style_haus_post": False, "stylehaus_post_count": 0,
    }
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: fake)

    apply_revisit(conn, "1", "<html>", now1)
    row = conn.execute("SELECT * FROM revisit_state WHERE item_id='1'").fetchone()
    assert row["base_tier"] == "WARM"
    assert row["tier"] == "WARM"
    assert row["obs_count"] == 1
    assert row["last_velocity"] is None

    fake2 = dict(fake, fav_count=40, view_count=100)
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: fake2)
    apply_revisit(conn, "1", "<html>", now2)
    row = conn.execute("SELECT * FROM revisit_state WHERE item_id='1'").fetchone()
    assert row["base_tier"] == "WARM"
    assert row["tier"] == "HOT"
    assert row["obs_count"] == 2
    assert row["last_velocity"] == 15.0
    assert row["next_revisit_at"] == "2026-06-30T00:00:00+09:00"

    assert conn.execute("SELECT COUNT(*) FROM stats_history WHERE item_id='1'").fetchone()[0] == 2


_LOW_METRIC_FAKE = {
    "name": "n", "brand": None, "category_path": None, "origin_country": None,
    "image_url": None, "description": None, "size_guide_text": None,
    "view_count": 1, "fav_count": 0, "inquiry_count": 0,
    "brand_model_number": None, "tags": None, "themes": None, "listed_at": None,
    "size_chart": None, "image_urls": [], "variants": [],
    "has_style_haus": False, "stylehaus_video_count": 0,
    "has_style_haus_post": False, "stylehaus_post_count": 0,
}


def test_apply_revisit_item_sales_promote_to_hot(monkeypatch):
    conn = connect(":memory:")
    init_schema(conn)
    now = "2026-06-29T00:00:00+09:00"
    _seed_item(conn, "1", now)
    # 조회·찜 낮지만 최근 30일 판매 3건 → HOT
    for i in range(3):
        conn.execute(
            "INSERT INTO orders (seller_id,item_id,sale_date,collected_at) VALUES ('s1','1',?,?)",
            (f"2026/06/2{i}", now))
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: dict(_LOW_METRIC_FAKE))
    apply_revisit(conn, "1", "<html>", now)
    assert conn.execute("SELECT tier FROM revisit_state WHERE item_id='1'").fetchone()[0] == "HOT"


def test_apply_revisit_strong_seller_extended_window_warm(monkeypatch):
    conn = connect(":memory:")
    init_schema(conn)
    now = "2026-06-29T00:00:00+09:00"
    _seed_item(conn, "1", now)
    conn.execute("INSERT INTO sellers (seller_id, order_count) VALUES ('s1', 1500)")
    # 최근 30일 판매 없음, 90일 창(2026/04/15)에 1건 → 강한 셀러라 WARM
    conn.execute(
        "INSERT INTO orders (seller_id,item_id,sale_date,collected_at) VALUES ('s1','1','2026/04/15',?)",
        (now,))
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: dict(_LOW_METRIC_FAKE))
    apply_revisit(conn, "1", "<html>", now)
    assert conn.execute("SELECT tier FROM revisit_state WHERE item_id='1'").fetchone()[0] == "WARM"


def test_apply_revisit_weak_seller_extended_window_stays_cold(monkeypatch):
    conn = connect(":memory:")
    init_schema(conn)
    now = "2026-06-29T00:00:00+09:00"
    _seed_item(conn, "1", now)
    conn.execute("INSERT INTO sellers (seller_id, order_count) VALUES ('s1', 100)")
    conn.execute(
        "INSERT INTO orders (seller_id,item_id,sale_date,collected_at) VALUES ('s1','1','2026/04/15',?)",
        (now,))
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: dict(_LOW_METRIC_FAKE))
    apply_revisit(conn, "1", "<html>", now)
    assert conn.execute("SELECT tier FROM revisit_state WHERE item_id='1'").fetchone()[0] == "COLD"


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _FakeClient:
    def __init__(self):
        self.gets = 0

    def get(self, url):
        self.gets += 1
        return _FakeResp("<html>")

    def get_allowing_4xx(self, url):
        return _FakeResp("<html>")

    def close(self):
        pass


def test_run_revisit_seeds_then_fetches(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="enriched", seller_id="s1", name="n", price=1, now=now0)
    update_detail_fields(
        conn, item_id="enriched", brand=None, category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None,
        view_count=600, fav_count=0, inquiry_count=0, brand_model_number=None,
        tags=None, themes=None, size_chart_json=None, listed_at=None, fetched_at=now0)
    conn.execute("INSERT INTO stats_history VALUES ('enriched', ?, 600, 0, 0)", (now0,))
    upsert_scanned_item(conn, item_id="stranded", seller_id="s1", name="n", price=1, now=now0)
    conn.close()

    fake = {
        "name": "n", "brand": None, "category_path": None, "origin_country": None,
        "image_url": None, "description": None, "size_guide_text": None,
        "view_count": 700, "fav_count": 1, "inquiry_count": 0,
        "brand_model_number": None, "tags": None, "themes": None, "listed_at": None,
        "size_chart": None,
        "image_urls": [], "variants": [],
        "has_style_haus": False, "stylehaus_video_count": 0,
        "has_style_haus_post": False, "stylehaus_post_count": 0,
    }
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: fake)

    client = _FakeClient()
    summary = run_revisit(
        db_path=str(db),
        scan_client_factory=lambda: client,
        num_workers=1,
        now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None,
        max_hours=1.0,
    )
    assert summary.observed == 2
    assert summary.seeded == 1

    conn = connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM revisit_state").fetchone()[0] == 2
    assert conn.execute(
        "SELECT detail_fetched_at FROM items WHERE item_id='stranded'").fetchone()[0] is not None


def test_run_revisit_respects_deadline(tmp_path, monkeypatch):
    db = tmp_path / "t2.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    for i in range(5):
        upsert_scanned_item(conn, item_id=f"s{i}", seller_id="s1", name="n", price=1, now=now0)
    conn.close()
    fake = {
        "name": "n", "brand": None, "category_path": None, "origin_country": None,
        "image_url": None, "description": None, "size_guide_text": None,
        "view_count": 1, "fav_count": 0, "inquiry_count": 0,
        "brand_model_number": None, "tags": None, "themes": None, "listed_at": None,
        "size_chart": None,
        "image_urls": [], "variants": [],
        "has_style_haus": False, "stylehaus_video_count": 0,
        "has_style_haus_post": False, "stylehaus_post_count": 0,
    }
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: fake)
    summary = run_revisit(
        db_path=str(db), scan_client_factory=lambda: _FakeClient(),
        num_workers=1, now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None, max_hours=0.0)
    assert summary.observed == 0


class _UnexpectedStatusClient:
    def get_allowing_4xx(self, url):
        return _FakeResp("", status=451)

    def get(self, url):
        return _FakeResp("", status=451)

    def close(self):
        pass


def test_run_revisit_counts_unexpected_status_as_error(tmp_path, monkeypatch):
    db = tmp_path / "t4.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="weird", seller_id="s1", name="n", price=1, now=now0)
    update_detail_fields(
        conn, item_id="weird", brand=None, category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None,
        view_count=600, fav_count=0, inquiry_count=0, brand_model_number=None,
        tags=None, themes=None, size_chart_json=None, listed_at=None, fetched_at=now0)
    conn.execute("INSERT INTO stats_history VALUES ('weird', ?, 600, 0, 0)", (now0,))
    conn.close()
    errs = []
    summary = run_revisit(
        db_path=str(db), scan_client_factory=lambda: _UnexpectedStatusClient(),
        num_workers=1, now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: errs.append(kw), max_hours=1.0)
    assert summary.errors == 1
    assert summary.deleted == 0
    assert errs and errs[0]["stage"] == "classify"


class _NotFoundClient:
    def get_allowing_4xx(self, url):
        return _FakeResp("", status=404)

    def get(self, url):
        return _FakeResp("", status=404)

    def close(self):
        pass


def test_run_revisit_marks_deleted_on_404(tmp_path, monkeypatch):
    db = tmp_path / "t3.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="gone", seller_id="s1", name="n", price=1, now=now0)
    update_detail_fields(
        conn, item_id="gone", brand=None, category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None,
        view_count=600, fav_count=0, inquiry_count=0, brand_model_number=None,
        tags=None, themes=None, size_chart_json=None, listed_at=None, fetched_at=now0)
    conn.execute("INSERT INTO stats_history VALUES ('gone', ?, 600, 0, 0)", (now0,))
    conn.close()

    summary = run_revisit(
        db_path=str(db), scan_client_factory=lambda: _NotFoundClient(),
        num_workers=1, now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None, max_hours=1.0)
    assert summary.deleted == 1
    conn = connect(str(db))
    assert conn.execute("SELECT status FROM items WHERE item_id='gone'").fetchone()[0] == "DELETED"


def test_run_revisit_loop_terminates_on_stop_event(tmp_path):
    db = tmp_path / "loop.db"
    c = connect(str(db)); init_schema(c); c.close()   # empty DB: nothing to do
    ev = _threading.Event()
    timer = _threading.Timer(0.2, ev.set); timer.start()
    summary = run_revisit(
        db_path=str(db), scan_client_factory=lambda: _FakeClient(),
        num_workers=1, now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None, max_hours=None,
        loop=True, idle_seconds=5.0, stop_event=ev)
    timer.cancel()
    assert summary.observed == 0   # returns (does not hang); stop_event ended the loop


def test_run_revisit_loop_backfills_when_no_due(tmp_path, monkeypatch):
    # due가 없어도(예정이 미래) 잠들지 않고 앞당겨 수집한다 (never-idle).
    db = tmp_path / "backfill.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="future", seller_id="s1", name="n", price=1, now=now0)
    update_detail_fields(
        conn, item_id="future", brand=None, category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None,
        view_count=600, fav_count=0, inquiry_count=0, brand_model_number=None,
        tags=None, themes=None, size_chart_json=None, listed_at=None, fetched_at=now0)
    conn.execute("INSERT INTO stats_history VALUES ('future', ?, 600, 0, 0)", (now0,))
    # 이미 revisit_state에 있고 next_revisit_at은 먼 미래 → due 아님
    conn.execute(
        "INSERT INTO revisit_state "
        "(item_id,tier,base_tier,last_observed_at,next_revisit_at,obs_count,last_velocity) "
        "VALUES ('future','WARM','WARM',?, '2099-01-01T00:00:00+09:00', 1, NULL)", (now0,))
    conn.close()

    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: dict(_LOW_METRIC_FAKE))
    ev = _threading.Event()
    timer = _threading.Timer(0.2, ev.set); timer.start()
    summary = run_revisit(
        db_path=str(db), scan_client_factory=lambda: _FakeClient(),
        num_workers=1, now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None, max_hours=None,
        loop=True, idle_seconds=5.0, stop_event=ev)
    timer.cancel()
    assert summary.observed >= 1   # idle 대신 미래 예정 상품을 backfill로 관측


def test_run_revisit_loop_backfill_off_stays_idle(tmp_path, monkeypatch):
    # never_idle=False면 due가 없을 때 앞당기지 않고 잠든다(기존 동작 보존).
    db = tmp_path / "no_backfill.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="future", seller_id="s1", name="n", price=1, now=now0)
    update_detail_fields(
        conn, item_id="future", brand=None, category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None,
        view_count=600, fav_count=0, inquiry_count=0, brand_model_number=None,
        tags=None, themes=None, size_chart_json=None, listed_at=None, fetched_at=now0)
    conn.execute("INSERT INTO stats_history VALUES ('future', ?, 600, 0, 0)", (now0,))
    conn.execute(
        "INSERT INTO revisit_state "
        "(item_id,tier,base_tier,last_observed_at,next_revisit_at,obs_count,last_velocity) "
        "VALUES ('future','WARM','WARM',?, '2099-01-01T00:00:00+09:00', 1, NULL)", (now0,))
    conn.close()

    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: dict(_LOW_METRIC_FAKE))
    ev = _threading.Event()
    timer = _threading.Timer(0.2, ev.set); timer.start()
    summary = run_revisit(
        db_path=str(db), scan_client_factory=lambda: _FakeClient(),
        num_workers=1, now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None, max_hours=None,
        loop=True, idle_seconds=5.0, stop_event=ev, never_idle=False)
    timer.cancel()
    assert summary.observed == 0   # 앞당기지 않고 idle
