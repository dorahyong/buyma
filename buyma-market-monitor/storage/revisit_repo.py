"""revisit_state CRUD + queue/seed queries for the adaptive revisit scheduler."""
import sqlite3


def upsert_revisit_state(
    conn: sqlite3.Connection,
    item_id: str,
    tier: str,
    base_tier: str,
    last_observed_at: str,
    next_revisit_at: str,
    obs_count: int,
    last_velocity: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO revisit_state
          (item_id, tier, base_tier, last_observed_at, next_revisit_at, obs_count, last_velocity)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
          tier=excluded.tier,
          base_tier=excluded.base_tier,
          last_observed_at=excluded.last_observed_at,
          next_revisit_at=excluded.next_revisit_at,
          obs_count=excluded.obs_count,
          last_velocity=excluded.last_velocity
        """,
        (item_id, tier, base_tier, last_observed_at, next_revisit_at, obs_count, last_velocity),
    )


def get_recent_two_observations(conn: sqlite3.Connection, item_id: str) -> list[tuple]:
    """최신순 최대 2개: [(observed_at, view, fav, inquiry), ...]."""
    rows = conn.execute(
        "SELECT observed_at, view_count, fav_count, inquiry_count "
        "FROM stats_history WHERE item_id = ? ORDER BY observed_at DESC LIMIT 2",
        (item_id,),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def seed_backfill(conn: sqlite3.Connection) -> int:
    """revisit_state에 없는 enriched·ACTIVE 상품을 base_tier로 일괄 등록.

    fetch 없이 기존 items 지표로 티어를 매긴다. last_observed_at은 그 상품의
    최신 stats observed_at(없으면 detail_fetched_at). next_revisit_at은
    last_observed_at과 동일(전부 과거 → 즉시 재방문 후보). 이미 등록된 건 건드리지 않음.
    Returns: 새로 등록된 행 수.
    """
    cur = conn.execute(
        """
        INSERT INTO revisit_state
          (item_id, tier, base_tier, last_observed_at, next_revisit_at, obs_count, last_velocity)
        SELECT item_id, tier, tier, last_obs, last_obs, cnt, NULL
        FROM (
          SELECT
            i.item_id AS item_id,
            CASE
              WHEN COALESCE(i.fav_count,0) >= 50 OR COALESCE(i.view_count,0) >= 2000
                   OR COALESCE(i.inquiry_count,0) >= 5 THEN 'HOT'
              WHEN COALESCE(i.fav_count,0) >= 10 OR COALESCE(i.view_count,0) >= 500
                   OR COALESCE(i.inquiry_count,0) >= 1 THEN 'WARM'
              ELSE 'COLD'
            END AS tier,
            COALESCE(
              (SELECT MAX(s.observed_at) FROM stats_history s WHERE s.item_id = i.item_id),
              i.detail_fetched_at
            ) AS last_obs,
            (SELECT COUNT(*) FROM stats_history s WHERE s.item_id = i.item_id) AS cnt
          FROM items i
          LEFT JOIN revisit_state r ON r.item_id = i.item_id
          WHERE i.status = 'ACTIVE' AND i.detail_fetched_at IS NOT NULL
            AND r.item_id IS NULL
          LIMIT 10000
        ) AS seed_src
        """
    )
    return cur.rowcount


def get_unobserved_active(conn: sqlite3.Connection, limit: int | None = None) -> list[str]:
    """첫 상세 fetch가 필요한 ACTIVE 상품(detail_fetched_at IS NULL)."""
    sql = "SELECT item_id FROM items WHERE status='ACTIVE' AND detail_fetched_at IS NULL"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [r[0] for r in conn.execute(sql)]


def get_revisit_queue(conn: sqlite3.Connection, limit: int, due_before: str | None = None) -> list[str]:
    """재방문 후보를 next_revisit_at 빠른(오래된) 순으로 반환.

    due_before가 주어지면 next_revisit_at <= due_before 인 것만(=지금 due된 것).
    None이면 전체(미래 포함). 같은 티어 내에선 urgency 순과 일치, 티어가 섞이면 근사.
    """
    if due_before is None:
        rows = conn.execute(
            "SELECT item_id FROM revisit_state ORDER BY next_revisit_at ASC LIMIT ?",
            (int(limit),))
    else:
        rows = conn.execute(
            "SELECT item_id FROM revisit_state WHERE next_revisit_at <= ? "
            "ORDER BY next_revisit_at ASC LIMIT ?",
            (due_before, int(limit)))
    return [r[0] for r in rows]


def top_surging(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """fav_velocity 높은 순 급등 상품 (이름·티어 포함)."""
    return conn.execute(
        """
        SELECT r.item_id, r.tier, r.base_tier, r.last_velocity, r.obs_count,
               i.name, i.current_price, i.view_count, i.fav_count, i.inquiry_count
        FROM revisit_state r JOIN items i ON i.item_id = r.item_id
        WHERE r.last_velocity IS NOT NULL
        ORDER BY r.last_velocity DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()


def coverage_stats(conn: sqlite3.Connection) -> dict:
    """스케줄 커버리지 요약: 총 등록 수, 2회+ 관측 수, 티어 분포."""
    total = conn.execute("SELECT COUNT(*) FROM revisit_state").fetchone()[0]
    multi = conn.execute("SELECT COUNT(*) FROM revisit_state WHERE obs_count >= 2").fetchone()[0]
    by_tier = {r[0]: r[1] for r in conn.execute(
        "SELECT tier, COUNT(*) FROM revisit_state GROUP BY tier")}
    return {"total": total, "multi_observed": multi, "by_tier": by_tier}
