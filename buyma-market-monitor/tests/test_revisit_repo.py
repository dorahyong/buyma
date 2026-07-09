import sqlite3
from storage.db import connect, init_schema
from storage import revisit_repo
from storage.items_repo import record_stats_observation


def _mem():
    conn = connect(":memory:")
    init_schema(conn)
    return conn


def test_revisit_state_table_exists():
    conn = _mem()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(revisit_state)")}
    assert cols == {
        "item_id", "tier", "base_tier", "last_observed_at",
        "next_revisit_at", "obs_count", "last_velocity",
    }


def test_upsert_and_get_revisit_state():
    conn = _mem()
    revisit_repo.upsert_revisit_state(
        conn, item_id="1", tier="HOT", base_tier="WARM",
        last_observed_at="2026-06-29T00:00:00+09:00",
        next_revisit_at="2026-06-30T00:00:00+09:00",
        obs_count=2, last_velocity=5.0,
    )
    row = conn.execute("SELECT * FROM revisit_state WHERE item_id='1'").fetchone()
    assert row["tier"] == "HOT"
    assert row["base_tier"] == "WARM"
    assert row["obs_count"] == 2
    assert row["last_velocity"] == 5.0

    revisit_repo.upsert_revisit_state(
        conn, item_id="1", tier="COLD", base_tier="COLD",
        last_observed_at="2026-07-01T00:00:00+09:00",
        next_revisit_at="2026-07-31T00:00:00+09:00",
        obs_count=3, last_velocity=None,
    )
    row = conn.execute("SELECT * FROM revisit_state WHERE item_id='1'").fetchone()
    assert row["tier"] == "COLD"
    assert row["obs_count"] == 3
    assert row["last_velocity"] is None


def test_get_recent_two_observations_desc():
    conn = _mem()
    record_stats_observation(conn, "1", 100, 10, 1, "2026-06-27T00:00:00+09:00")
    record_stats_observation(conn, "1", 300, 20, 5, "2026-06-29T00:00:00+09:00")
    record_stats_observation(conn, "1", 50, 5, 0, "2026-06-25T00:00:00+09:00")
    obs = revisit_repo.get_recent_two_observations(conn, "1")
    assert [o[0] for o in obs] == [
        "2026-06-29T00:00:00+09:00", "2026-06-27T00:00:00+09:00",
    ]
    assert obs[0] == ("2026-06-29T00:00:00+09:00", 300, 20, 5)


def _add_item(conn, item_id, seller="s1", status="ACTIVE",
              view=None, fav=None, inq=None, detail="2026-06-20T00:00:00+09:00"):
    conn.execute(
        "INSERT INTO items (item_id, seller_id, name, status, first_seen_at, "
        "last_seen_at, view_count, fav_count, inquiry_count, detail_fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (item_id, seller, "n", status, "2026-06-01T00:00:00+09:00",
         "2026-06-20T00:00:00+09:00", view, fav, inq, detail),
    )


def test_seed_backfill_registers_enriched_active_only():
    conn = _mem()
    _add_item(conn, "hot", view=3000)
    _add_item(conn, "warm", fav=12)
    _add_item(conn, "cold", view=1)
    _add_item(conn, "stranded", detail=None)
    _add_item(conn, "dead", status="DELETED", view=9999)
    record_stats_observation(conn, "hot", 3000, 0, 0, "2026-06-20T00:00:00+09:00")

    n = revisit_repo.seed_backfill(conn)
    assert n == 3
    seeded = {r[0]: r for r in conn.execute(
        "SELECT item_id, tier, base_tier, last_observed_at, obs_count FROM revisit_state")}
    assert set(seeded) == {"hot", "warm", "cold"}
    assert seeded["hot"][1] == "HOT"
    assert seeded["warm"][3] == "2026-06-20T00:00:00+09:00"
    assert seeded["hot"][3] == "2026-06-20T00:00:00+09:00"

    assert revisit_repo.seed_backfill(conn) == 0


def test_seed_backfill_prefers_latest_stats_over_detail_fetched_at():
    conn = _mem()
    _add_item(conn, "x", view=1, detail="2026-06-10T00:00:00+09:00")
    record_stats_observation(conn, "x", 1, 0, 0, "2026-06-22T00:00:00+09:00")
    revisit_repo.seed_backfill(conn)
    row = conn.execute("SELECT last_observed_at, next_revisit_at, obs_count "
                       "FROM revisit_state WHERE item_id='x'").fetchone()
    assert row[0] == "2026-06-22T00:00:00+09:00"   # latest stats, not detail date
    assert row[1] == "2026-06-22T00:00:00+09:00"   # next = last (seed)
    assert row[2] == 1


def test_get_unobserved_active_returns_stranded():
    conn = _mem()
    _add_item(conn, "ok", view=1)
    _add_item(conn, "stranded", detail=None)
    ids = revisit_repo.get_unobserved_active(conn)
    assert ids == ["stranded"]


def test_get_revisit_queue_orders_by_next_revisit_at():
    conn = _mem()
    for iid, nxt in [("a", "2026-07-10"), ("b", "2026-07-01"), ("c", "2026-07-05")]:
        revisit_repo.upsert_revisit_state(
            conn, item_id=iid, tier="COLD", base_tier="COLD",
            last_observed_at="2026-06-01T00:00:00+09:00",
            next_revisit_at=nxt + "T00:00:00+09:00",
            obs_count=1, last_velocity=None,
        )
    assert revisit_repo.get_revisit_queue(conn, limit=2) == ["b", "c"]


def test_top_surging_orders_by_velocity_desc():
    conn = _mem()
    _add_item(conn, "a", view=1); _add_item(conn, "b", view=1)
    for iid, vel in [("a", 3.0), ("b", 50.0)]:
        revisit_repo.upsert_revisit_state(
            conn, item_id=iid, tier="HOT", base_tier="WARM",
            last_observed_at="2026-06-29T00:00:00+09:00",
            next_revisit_at="2026-06-30T00:00:00+09:00",
            obs_count=2, last_velocity=vel)
    rows = revisit_repo.top_surging(conn, limit=10)
    assert [r["item_id"] for r in rows] == ["b", "a"]
    assert rows[0]["last_velocity"] == 50.0
    assert rows[0]["name"] == "n"


def test_coverage_stats_counts_observed():
    conn = _mem()
    _add_item(conn, "a", view=1); _add_item(conn, "b", view=1)
    revisit_repo.upsert_revisit_state(
        conn, item_id="a", tier="HOT", base_tier="HOT",
        last_observed_at="x", next_revisit_at="y", obs_count=3, last_velocity=1.0)
    revisit_repo.upsert_revisit_state(
        conn, item_id="b", tier="COLD", base_tier="COLD",
        last_observed_at="x", next_revisit_at="y", obs_count=1, last_velocity=None)
    stats = revisit_repo.coverage_stats(conn)
    assert stats["total"] == 2
    assert stats["multi_observed"] == 1
    assert stats["by_tier"]["HOT"] == 1
    assert stats["by_tier"]["COLD"] == 1

def test_get_revisit_queue_due_before_filters_future():
    conn = _mem()
    for iid, nxt in [("past", "2026-06-01"), ("soon", "2026-06-28"), ("future", "2026-07-20")]:
        revisit_repo.upsert_revisit_state(
            conn, item_id=iid, tier="COLD", base_tier="COLD",
            last_observed_at="2026-06-01T00:00:00+09:00",
            next_revisit_at=nxt + "T00:00:00+09:00",
            obs_count=1, last_velocity=None)
    due = revisit_repo.get_revisit_queue(conn, limit=10, due_before="2026-06-29T00:00:00+09:00")
    assert due == ["past", "soon"]          # future excluded, ordered by next_revisit_at ASC
    allq = revisit_repo.get_revisit_queue(conn, limit=10)
    assert allq == ["past", "soon", "future"]   # due_before=None → whole queue
