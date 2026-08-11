"""Page-level parallel seller-listing scan (whale-seller fix).

Distributes (seller, page) tasks across a global queue so one whale seller's
hundreds of pages spread across all workers. Reconciles per seller once all its
pages are scanned (reusing apply_seller_scan_to_db). Detail enrichment is NOT
done here — new items stay detail_fetched_at IS NULL for the revisit scheduler.
Disappeared items are classified (SOLD_OUT/DELETED) in Phase B (Task 2).

If any page of a seller fails to fetch, that seller is skipped this run (not
reconciled) and retried next run — conservative guard against false
disappearances from transient page errors.
"""
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from crawler.seller_items import (
    build_seller_items_url, parse_seller_items, parse_seller_items_max_page,
)
from crawler.monitor import (
    apply_seller_scan_to_db, fetch_for_classification, apply_classification,
)
from crawler.item_status import ItemStatus
from crawler.item_detail import build_item_detail_url
from storage.db import connect, init_schema, with_lock_retry
from storage import scan_repo


@dataclass
class ScanSummary:
    sellers_scanned: int = 0
    sellers_skipped: int = 0
    items_new: int = 0
    price_changes: int = 0
    disappeared: int = 0
    abandoned: int = 0
    deleted: int = 0
    sold_out: int = 0
    errors: int = 0


def run_page_scan(
    db_path: Path | str,
    sellers,
    client_factory: Callable[[], object],
    num_workers: int,
    now: str,
    on_error: Callable[..., None],
    max_hours: float | None = None,
    circuit_breaker=None,
    stop_event=None,
    tier_of: dict | None = None,
) -> ScanSummary:
    db_path = Path(db_path)
    summary = ScanSummary()
    main_conn = connect(db_path)
    init_schema(main_conn)

    db_lock = threading.Lock()
    state_lock = threading.Lock()
    counts_lock = threading.Lock()

    deadline = None if max_hours is None else time.monotonic() + max_hours * 3600.0

    def _halt() -> bool:
        if deadline is not None and time.monotonic() >= deadline:
            return True
        if circuit_breaker is not None and circuit_breaker.is_open():
            return True
        if stop_event is not None and stop_event.is_set():
            return True
        return False

    seller_ids = list(sellers)
    buf: dict[str, list] = {sid: [] for sid in seller_ids}
    total: dict[str, int | None] = {sid: None for sid in seller_ids}
    done: dict[str, int] = {sid: 0 for sid in seller_ids}
    failed: dict[str, bool] = {sid: False for sid in seller_ids}
    reconciled: set[str] = set()
    disappeared_ids: list[str] = []

    page_q: queue.Queue = queue.Queue()
    for sid in seller_ids:
        page_q.put((sid, 1))

    def _reconcile(sid: str) -> None:
        items = buf[sid]
        with db_lock:
            # 잠금 충돌(데드락/락대기)은 트랜잭션 통째로 다시 실행한다.
            #   apply_seller_scan_to_db 는 BEGIN~COMMIT 한 덩어리이고, 함수 시작에서
            #   현재 상태를 다시 읽으므로 통째 재실행이 안전하다.
            #   ★ 이 자리는 예외가 위로 새면 scan_worker 스레드가 죽는다(2026-08-10 사고).
            #   (집계 쿼리 INSERT INTO po SELECT ... FROM market_items 와 부딪힌다)
            outcome = with_lock_retry(
                apply_seller_scan_to_db, main_conn, sid, items, now,
                label=f"[scan] seller {sid}")
            # 셀러 저장 즉시 스캔 체크포인트 기록(같은 db_lock 안) → 중간에 죽어도
            # 완료된 셀러는 다음 run에서 다시 안 긁음. (구: value_scan에서 일괄 기록)
            if not outcome.skipped_due_to_empty_scan:
                scan_repo.mark_seller_scanned(
                    main_conn, sid, tier=(tier_of or {}).get(sid, "LOW"), now=now)
        with counts_lock:
            if not outcome.skipped_due_to_empty_scan:
                summary.sellers_scanned += 1
                summary.items_new += len(outcome.new_item_ids)
                summary.price_changes += outcome.price_changes
                logging.info(
                    "[scan] seller %s: +%d new / %d items (%d/%d sellers, %d disappeared)",
                    sid, len(outcome.new_item_ids), len(items),
                    summary.sellers_scanned, len(seller_ids), len(outcome.disappeared_item_ids))
        if outcome.disappeared_item_ids:
            with state_lock:
                disappeared_ids.extend(outcome.disappeared_item_ids)

    def _after_page(sid, page, failed_fetch, items=None, max_pages=None) -> None:
        action = None  # "reconcile" | "skip"
        with state_lock:
            if failed_fetch:
                failed[sid] = True
            if page == 1:
                if failed_fetch:
                    total[sid] = 0            # no pages enqueued; will resolve to skip
                else:
                    total[sid] = max_pages
                    buf[sid].extend(items or [])
                    for n in range(2, (max_pages or 1) + 1):
                        page_q.put((sid, n))
                    done[sid] += 1
            else:
                if not failed_fetch and items:
                    buf[sid].extend(items)
                done[sid] += 1
            # A seller is complete once total is known and all its pages are done.
            # Reconcile only if no page failed; otherwise skip this run (retry next run)
            # so a transient page error never causes false "disappeared" classifications.
            if total[sid] is not None and done[sid] >= total[sid] and sid not in reconciled:
                reconciled.add(sid)
                action = "reconcile" if not failed[sid] else "skip"
        if action == "reconcile":
            _reconcile(sid)
        elif action == "skip":
            with counts_lock:
                summary.sellers_skipped += 1

    def scan_worker() -> None:
        try:
            client = client_factory()
        except Exception as e:
            on_error(stage="client_init", url="", status=None, reason=repr(e))
            with counts_lock:
                summary.errors += 1
            return
        try:
            while not _halt():
                try:
                    sid, page = page_q.get_nowait()
                except queue.Empty:
                    return
                try:
                    url = build_seller_items_url(sid, page)
                    try:
                        resp = client.get(url)
                    except Exception as e:
                        on_error(stage="seller_items", url=url,
                                 status=getattr(e, "last_status", None), reason=repr(e))
                        with counts_lock:
                            summary.errors += 1
                        _after_page(sid, page, failed_fetch=True)
                        continue
                    items = parse_seller_items(resp.text)
                    max_pages = parse_seller_items_max_page(resp.text) if page == 1 else None
                    _after_page(sid, page, failed_fetch=False, items=items, max_pages=max_pages)
                finally:
                    page_q.task_done()
        finally:
            if hasattr(client, "close"):
                client.close()

    try:
        threads = [threading.Thread(target=scan_worker, daemon=True) for _ in range(num_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with counts_lock:
            summary.disappeared = len(disappeared_ids)
            summary.abandoned = len(seller_ids) - len(reconciled)

        # Phase B: classify disappeared items (item-level queue; whale-agnostic)
        class_q: queue.Queue = queue.Queue()
        for iid in disappeared_ids:
            class_q.put(iid)

        def classify_worker() -> None:
            try:
                client = client_factory()
            except Exception as e:
                on_error(stage="client_init", url="", status=None, reason=repr(e))
                with counts_lock:
                    summary.errors += 1
                return
            try:
                while not _halt():
                    try:
                        iid = class_q.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        fetched = fetch_for_classification(client, iid, on_error)
                        if fetched is not None:
                            status_code, _ = fetched
                            try:
                                with db_lock:
                                    status = with_lock_retry(
                                        apply_classification, main_conn, iid, status_code, now,
                                        label=f"[classify] item {iid}")
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
                        class_q.task_done()
            finally:
                if hasattr(client, "close"):
                    client.close()

        cthreads = [threading.Thread(target=classify_worker, daemon=True) for _ in range(num_workers)]
        for t in cthreads:
            t.start()
        for t in cthreads:
            t.join()
    finally:
        main_conn.close()
    return summary
