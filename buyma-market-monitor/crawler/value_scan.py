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

    # 셀러 완료 도장은 page_scan이 셀러별로 저장하는 즉시 찍음(tier_of 전달) →
    # 중간에 죽어도 완료 셀러는 재스캔 안 함. (구: 아래에서 스캔 전체 끝난 뒤 일괄 기록)
    summary = run_page_scan(
        db_path=db_path, sellers=due, client_factory=client_factory,
        num_workers=num_workers, now=now, on_error=on_error, tier_of=tier_of,
        max_hours=max_hours, circuit_breaker=circuit_breaker, stop_event=stop_event)
    return summary
