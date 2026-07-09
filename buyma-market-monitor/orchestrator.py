"""Single-process supervisor: daily batch (sellers→orders→scan) with per-stage
time caps, remaining time to the revisit loop, cooldown-and-resume on IP block."""
import argparse
import json
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import httpx
from crawler.circuit_breaker import CircuitBreaker
from crawler.client import HttpClient
from crawler.page_scan import run_page_scan
from crawler.revisit import run_revisit
from crawler.value_scan import run_value_scan
from storage.db import connect, init_schema
from storage import sellers_repo
from storage.store import now_iso, KST


DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "items.db"
ERRORS_PATH = DATA_DIR / "errors.log"


@dataclass
class Stage:
    name: str
    fn: Callable            # fn(deadline: float|None, cb, stop_event) -> result
    cap_seconds: float


def run_cycle(
    stages, revisit_fn, cycle_seconds, cooldown_seconds,
    clock=time.monotonic, sleep_fn=time.sleep, make_cb=None, stop_event=None,
) -> None:
    """Run one daily cycle: each batch stage within its cap, then revisit for the
    remaining time. On block (cb.is_open() after a stage), cooldown then retry that
    stage. stop_event short-circuits everything."""
    def _stopped():
        return stop_event is not None and stop_event.is_set()

    cycle_start = clock()

    for stage in stages:
        while not _stopped():
            cb = make_cb() if make_cb is not None else None
            deadline = clock() + stage.cap_seconds
            logging.info("[%s] start (cap=%.0fs)", stage.name, stage.cap_seconds)
            stage.fn(deadline, cb, stop_event)
            if cb is not None and cb.is_open():
                logging.warning("[%s] blocked — cooldown %.0fs then resume",
                                stage.name, cooldown_seconds)
                sleep_fn(cooldown_seconds)
                if _stopped():
                    return
                continue    # retry same stage (resumable)
            break           # stage finished (or hit its cap) → next stage
        if _stopped():
            return

    remaining = cycle_seconds - (clock() - cycle_start)
    if remaining < 0:
        remaining = 0
    logging.info("[revisit] remaining %.0fs", remaining)
    cb = make_cb() if make_cb is not None else None
    revisit_fn(remaining, cb, stop_event)


def _append_error(**kw) -> None:
    with ERRORS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": now_iso(), **kw}, ensure_ascii=False) + "\n")


def _recent_cutoff() -> str:
    return (datetime.now(KST) - timedelta(days=30)).strftime("%Y/%m/%d")


def run_forever(args, stop_event) -> None:
    def make_http_cb():
        return CircuitBreaker(threshold=args.cb_threshold, window_seconds=args.cb_window_seconds)

    def stage_sellers(deadline, cb, se):
        # TODO(MVP): ① has no unambiguous IP-block return signal to wire into `cb`
        # (run_crawl_sellers returns 1 for several failure modes). Left best-effort;
        # ② (orders) and ③a/③b propagate blocks to the orchestrator cb.
        from main import run_crawl_sellers
        class _A:
            reset_pagination = False
            dry_run = False
        run_crawl_sellers(_A())

    def stage_orders(deadline, cb, se):
        from main import run_crawl_orders
        class _A:
            max_pages = args.orders_max_pages
            seller_id = None
            full_rescan = False
            dry_run = False
        rc = run_crawl_orders(_A())
        if rc == 2 and cb is not None:   # 2 = aborted by IP block
            cb.force_open()

    _SCAN_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)

    def stage_scan(deadline, cb, se):
        max_hours = None if deadline is None else max(0.0, (deadline - time.monotonic()) / 3600.0)
        run_value_scan(
            db_path=DB_PATH,
            client_factory=lambda: HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb,
                                              timeout=_SCAN_TIMEOUT, max_retries=1),
            num_workers=args.workers, now=now_iso(), on_error=_append_error,
            recent_cutoff=_recent_cutoff(), max_hours=max_hours,
            circuit_breaker=cb, stop_event=se)

    def revisit_fn(remaining_seconds, cb, se):
        run_revisit(
            db_path=DB_PATH,
            scan_client_factory=lambda: HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb),
            num_workers=args.workers, now=now_iso(), on_error=_append_error,
            max_hours=max(0.0, remaining_seconds / 3600.0),
            circuit_breaker=cb, loop=True, idle_seconds=args.idle_minutes * 60.0,
            stop_event=se)

    stages = [
        Stage("sellers", stage_sellers, cap_seconds=args.sellers_cap_hours * 3600.0),
        Stage("orders", stage_orders, cap_seconds=args.orders_cap_hours * 3600.0),
        Stage("scan", stage_scan, cap_seconds=args.scan_cap_hours * 3600.0),
    ]

    def stop_aware_sleep(seconds):
        stop_event.wait(seconds)

    while not stop_event.is_set():
        logging.info("=== cycle start %s ===", now_iso())
        run_cycle(
            stages=stages, revisit_fn=revisit_fn,
            cycle_seconds=args.cycle_hours * 3600.0,
            cooldown_seconds=args.cooldown_minutes * 60.0,
            clock=time.monotonic, sleep_fn=stop_aware_sleep,
            make_cb=make_http_cb, stop_event=stop_event)


def main() -> int:
    p = argparse.ArgumentParser(description="BUYMA unified orchestrator")
    p.add_argument("--cycle-hours", type=float, default=24.0)
    p.add_argument("--sellers-cap-hours", type=float, default=1.0)
    p.add_argument("--orders-cap-hours", type=float, default=3.0)
    p.add_argument("--scan-cap-hours", type=float, default=6.0)
    p.add_argument("--cooldown-minutes", type=float, default=45.0)
    p.add_argument("--idle-minutes", type=float, default=10.0)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--sleep", type=float, default=0.3)
    p.add_argument("--orders-max-pages", type=int, default=100)
    p.add_argument("--cb-threshold", type=int, default=5)
    p.add_argument("--cb-window-seconds", type=int, default=60)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    stop_event = threading.Event()

    def _sigint(signum, frame):
        logging.info("SIGINT — finishing current work, stopping after this stage...")
        stop_event.set()
    signal.signal(signal.SIGINT, _sigint)

    run_forever(args, stop_event)
    logging.info("orchestrator stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
