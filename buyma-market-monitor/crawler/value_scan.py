"""Value-prioritized ③a orchestration: recompute → due → scan → advance."""
from pathlib import Path
from typing import Callable

from crawler.page_scan import run_page_scan, ScanSummary
from storage.db import connect, init_schema
from storage import scan_repo


def run_value_scan(
    db_path, client_factory: Callable[[], object], num_workers: int, now: str,
    on_error: Callable[..., None], recent_cutoff: str,
    max_hours: float | None = None, circuit_breaker=None, stop_event=None,
    due_limit: int = 100000,
) -> ScanSummary:
    """One value-prioritized ③a pass: recompute seller values, scan due sellers
    (highest value first) within the cap via run_page_scan, advance the schedule of
    sellers that actually completed (items.last_seen_at == now). Skipped sellers keep
    their due time → retried next run."""
    db_path = Path(db_path)
    conn = connect(db_path); init_schema(conn)
    scan_repo.recompute_seller_values(conn, now=now, recent_cutoff=recent_cutoff)
    due = scan_repo.get_due_sellers(conn, now=now, limit=due_limit)
    tier_of = dict(conn.execute("SELECT seller_id, value_tier FROM seller_scan_state"))
    conn.close()

    if not due:
        return ScanSummary()

    summary = run_page_scan(
        db_path=db_path, sellers=due, client_factory=client_factory,
        num_workers=num_workers, now=now, on_error=on_error,
        max_hours=max_hours, circuit_breaker=circuit_breaker, stop_event=stop_event)

    conn = connect(db_path)
    scanned_ids = {r[0] for r in conn.execute(
        "SELECT DISTINCT seller_id FROM items WHERE last_seen_at = ?", (now,))}
    for sid in due:
        if sid in scanned_ids:
            scan_repo.mark_seller_scanned(conn, sid, tier=tier_of.get(sid, "LOW"), now=now)
    conn.close()
    return summary
