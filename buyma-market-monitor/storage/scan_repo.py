"""seller_scan_state CRUD + value recomputation + due selection."""
import sqlite3

from crawler.scan_scheduler import tier_for_rank, next_scan_at_from


def recompute_seller_values(conn: sqlite3.Connection, now: str, recent_cutoff: str) -> None:
    """Recompute each seller's value tier from HOT/WARM item counts + recent orders.
    recent_cutoff: 'YYYY/MM/DD'. New sellers get next_scan_at=now (due now); existing
    sellers keep next_scan_at, only tier/score refreshed.
    Tiers are rank-based (distribution-relative): top 15% → HIGH, next 35% → MID, rest → LOW."""
    # revisit_state.seller_id(비정규화)로 직접 집계 → 12GB items 조인 제거.
    # (tier, seller_id) 인덱스로 커버되어 원격 MySQL 타임아웃(2013) 없이 순식간에 끝난다.
    hot_warm = {r[0]: r[1] for r in conn.execute(
        "SELECT seller_id, COUNT(*) FROM revisit_state "
        "WHERE tier IN ('HOT','WARM') AND seller_id IS NOT NULL GROUP BY seller_id")}
    recent = {r[0]: r[1] for r in conn.execute(
        "SELECT seller_id, COUNT(*) FROM orders WHERE sale_date >= ? GROUP BY seller_id",
        (recent_cutoff,))}
    scored = []
    for (sid,) in conn.execute("SELECT seller_id FROM sellers"):
        hw = hot_warm.get(sid, 0)
        ro = recent.get(sid, 0)
        scored.append((hw * 10 + ro, sid))
    scored.sort(reverse=True)
    n = len(scored) or 1
    # 신규 셀러: last_scanned_at=NULL·next_scan_at=now(즉시 due)로 삽입. 기존 셀러:
    # tier/score만 갱신하고 next_scan_at/last_scanned_at 보존(ON CONFLICT). 셀러당
    # 단건 UPDATE/INSERT(원격 MySQL서 1만 왕복) → executemany 한 번으로.
    rows = [(sid, tier_for_rank(i / n), score, None, now)
            for i, (score, sid) in enumerate(scored)]
    if rows:
        conn.executemany(
            "INSERT INTO seller_scan_state "
            "(seller_id, value_tier, value_score, last_scanned_at, next_scan_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(seller_id) DO UPDATE SET "
            "value_tier = excluded.value_tier, value_score = excluded.value_score",
            rows)


def get_due_sellers(conn: sqlite3.Connection, now: str, limit: int) -> list[str]:
    """Sellers due (next_scan_at <= now), highest tier first then most overdue."""
    rows = conn.execute(
        "SELECT seller_id FROM seller_scan_state WHERE next_scan_at <= ? "
        "ORDER BY CASE value_tier WHEN 'HIGH' THEN 0 WHEN 'MID' THEN 1 ELSE 2 END, "
        "next_scan_at ASC LIMIT ?",
        (now, int(limit)))
    return [r[0] for r in rows]


def mark_seller_scanned(conn: sqlite3.Connection, seller_id: str, tier: str, now: str) -> None:
    """Advance a completed seller's schedule by its tier interval."""
    conn.execute(
        "UPDATE seller_scan_state SET last_scanned_at=?, next_scan_at=? WHERE seller_id=?",
        (now, next_scan_at_from(now, tier), seller_id))
