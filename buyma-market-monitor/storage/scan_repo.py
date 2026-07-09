"""seller_scan_state CRUD + value recomputation + due selection."""
import sqlite3

from crawler.scan_scheduler import tier_for_rank, next_scan_at_from


def recompute_seller_values(conn: sqlite3.Connection, now: str, recent_cutoff: str) -> None:
    """Recompute each seller's value tier from HOT/WARM item counts + recent orders.
    recent_cutoff: 'YYYY/MM/DD'. New sellers get next_scan_at=now (due now); existing
    sellers keep next_scan_at, only tier/score refreshed.
    Tiers are rank-based (distribution-relative): top 15% → HIGH, next 35% → MID, rest → LOW."""
    hot_warm = {r[0]: r[1] for r in conn.execute(
        "SELECT i.seller_id, COUNT(*) FROM revisit_state r JOIN items i ON i.item_id = r.item_id "
        "WHERE r.tier IN ('HOT','WARM') GROUP BY i.seller_id")}
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
    existing = {r[0] for r in conn.execute("SELECT seller_id FROM seller_scan_state")}
    for i, (score, sid) in enumerate(scored):
        tier = tier_for_rank(i / n)
        if sid in existing:
            conn.execute("UPDATE seller_scan_state SET value_tier=?, value_score=? WHERE seller_id=?",
                         (tier, score, sid))
        else:
            conn.execute(
                "INSERT INTO seller_scan_state (seller_id, value_tier, value_score, "
                "last_scanned_at, next_scan_at) VALUES (?,?,?,?,?)",
                (sid, tier, score, None, now))


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
