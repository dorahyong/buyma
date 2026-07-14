"""Adaptive revisit orchestration — reuses monitor's fetch/enrich helpers."""
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from crawler.item_detail import build_item_detail_url
from crawler.item_status import ItemStatus
from crawler.monitor import (
    apply_enrich,
    fetch_for_classification, apply_classification,
)
from crawler.revisit_scheduler import compute_observation_state
from storage import revisit_repo
from storage.db import connect, init_schema
from storage.store import now_iso


def apply_revisit(conn: sqlite3.Connection, item_id: str, html: str, now: str) -> None:
    """DB write only — caller holds db_lock.

    1) apply_enrich: 상세/이미지/variants 갱신 + stats_history 관측 1행 (자체 트랜잭션 커밋).
    2) 최근 2관측으로 revisit_state 갱신 (autocommit).

    두 단계는 별도 커밋이다. 둘 사이에 크래시가 나면 stats_history 행은 있고
    revisit_state는 미갱신일 수 있으나, 다음 run에서 get_revisit_queue가 (stale)
    next_revisit_at로 재큐잉하므로 자가 복구된다 (데이터 손상 아님).
    """
    apply_enrich(conn, item_id, html, now)  # writes stats_history row at `now`
    obs = revisit_repo.get_recent_two_observations(conn, item_id)
    state = compute_observation_state(obs, now=now)
    total_obs = conn.execute(
        "SELECT COUNT(*) FROM stats_history WHERE item_id = ?", (item_id,)
    ).fetchone()[0]
    revisit_repo.upsert_revisit_state(
        conn, item_id=item_id,
        tier=state["tier"], base_tier=state["base_tier"],
        last_observed_at=state["last_observed_at"],
        next_revisit_at=state["next_revisit_at"],
        obs_count=total_obs, last_velocity=state["last_velocity"],
    )


@dataclass
class RevisitSummary:
    seeded: int = 0
    observed: int = 0
    deleted: int = 0
    sold_out: int = 0
    errors: int = 0


def run_revisit(
    db_path: Path | str,
    scan_client_factory: Callable[[], object],
    num_workers: int,
    now: str,
    on_error: Callable[..., None],
    max_hours: float | None,
    circuit_breaker=None,
    revisit_limit: int = 1_000_000,
    loop: bool = False,
    idle_seconds: float = 600.0,
    stop_event=None,
    clock: Callable[[], str] = now_iso,
) -> "RevisitSummary":
    """One revisit pass (or daemon loop): seed backfill, then fetch unobserved(1st) + due
    revisits(2nd) within the time budget. Stops promptly past deadline, on circuit breaker,
    or when stop_event is set (loop mode).

    loop=False: single pass, identical to previous behaviour.
    loop=True: rounds repeat until deadline/cb/stop_event; when queue is empty, sleeps
    idle_seconds (interruptible via stop_event) then retries.
    """
    db_path = Path(db_path)
    summary = RevisitSummary()

    main_conn = connect(db_path)
    init_schema(main_conn)
    # seed_backfill은 대량 스캔이라 caught-up 상태에서도 수십초 걸릴 수 있다. 실패(연결드롭 등)해도
    # 데몬을 죽이지 않는다: 후보가 없으면 건너뛰어도 무손실, 있으면 다음 라운드에 재시도.
    # (다음 main_conn 사용 시 shim이 자동 재연결)
    try:
        summary.seeded = revisit_repo.seed_backfill(main_conn)
    except Exception as e:
        on_error(stage="seed_backfill", url="", status=None, reason=repr(e))
        summary.seeded = 0

    deadline = None if max_hours is None else time.monotonic() + max_hours * 3600.0

    def _expired() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    def _cb_open() -> bool:
        return circuit_breaker is not None and circuit_breaker.is_open()

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    # In loop mode, stamp each observation with the real current time;
    # in single-pass, use the fixed `now` string passed by caller.
    _stamp = (lambda: clock()) if loop else (lambda: now)

    db_lock = threading.Lock()
    counts_lock = threading.Lock()

    def worker(work):
        client = scan_client_factory()
        try:
            while not _expired() and not _cb_open() and not _stopped():
                try:
                    iid = work.get_nowait()
                except queue.Empty:
                    return
                obs_now = _stamp()
                fetched = fetch_for_classification(client, iid, on_error)
                if fetched is not None:
                    status_code, body = fetched
                    if status_code == 200:
                        try:
                            with db_lock:
                                apply_revisit(main_conn, iid, body, obs_now)
                            with counts_lock:
                                summary.observed += 1
                        except Exception as e:
                            on_error(stage="revisit", url=build_item_detail_url(iid),
                                     status=None, reason=repr(e))
                            with counts_lock:
                                summary.errors += 1
                    else:
                        try:
                            with db_lock:
                                status = apply_classification(main_conn, iid, status_code, obs_now)
                            with counts_lock:
                                if status is ItemStatus.DELETED:
                                    summary.deleted += 1
                                elif status is ItemStatus.SOLD_OUT:
                                    summary.sold_out += 1
                        except Exception as e:
                            on_error(stage="classify", url=build_item_detail_url(iid),
                                     status=status_code, reason=repr(e))
                            with counts_lock:
                                summary.errors += 1
        finally:
            if hasattr(client, "close"):
                client.close()

    try:
        while True:
            if _expired() or _cb_open() or _stopped():
                break

            # Build work queue for this round
            work: queue.Queue = queue.Queue()
            had = 0
            for iid in revisit_repo.get_unobserved_active(main_conn):
                work.put(iid)
                had += 1
            due_before = clock() if loop else None
            for iid in revisit_repo.get_revisit_queue(main_conn, limit=revisit_limit, due_before=due_before):
                work.put(iid)
                had += 1

            # Drain with workers
            threads = [threading.Thread(target=worker, args=(work,), daemon=True) for _ in range(num_workers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            if not loop:
                break
            if _expired() or _cb_open() or _stopped():
                break
            if had == 0:
                # Nothing to do right now — idle until next due (interruptible)
                if stop_event is not None:
                    stop_event.wait(idle_seconds)
                else:
                    time.sleep(idle_seconds)
    finally:
        main_conn.close()
    return summary
