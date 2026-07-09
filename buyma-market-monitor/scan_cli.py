"""Page-level seller-listing scan CLI (whale-fixed 3a).

  python scan_cli.py --workers 6 --sleep 0.3 [--max-hours H] [--limit-sellers N]
  python scan_cli.py --value --workers 6 --sleep 0.3   # value-prioritized scan
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from crawler.circuit_breaker import CircuitBreaker
from crawler.client import HttpClient
from crawler.page_scan import run_page_scan
from crawler.value_scan import run_value_scan
from storage.db import connect, init_schema
from storage import sellers_repo
from storage.store import now_iso, KST

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "items.db"
ERRORS_PATH = DATA_DIR / "errors.log"


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def append_error_jsonl(**kw) -> None:
    with ERRORS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": now_iso(), **kw}, ensure_ascii=False) + "\n")


def _recent_cutoff() -> str:
    return (datetime.now(KST) - timedelta(days=30)).strftime("%Y/%m/%d")


def main() -> int:
    p = argparse.ArgumentParser(description="BUYMA page-level seller scan")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--sleep", type=float, default=0.3)
    p.add_argument("--max-hours", type=float, default=None)
    p.add_argument("--limit-sellers", type=int, default=None)
    p.add_argument("--cb-threshold", type=int, default=5)
    p.add_argument("--cb-window-seconds", type=int, default=60)
    p.add_argument("--value", action="store_true",
                   help="Value-prioritized scan: recompute tiers and scan due sellers first")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    setup_logging(args.verbose)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    cb = CircuitBreaker(threshold=args.cb_threshold, window_seconds=args.cb_window_seconds)

    _SCAN_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)

    def factory():
        return HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb,
                          timeout=_SCAN_TIMEOUT, max_retries=1)

    def factory_default():
        return HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb)

    now = now_iso()

    if args.value:
        logging.info("value-scan start %s (workers=%d, sleep=%.2f, max_hours=%s)",
                     now, args.workers, args.sleep, args.max_hours)
        summary = run_value_scan(
            db_path=DB_PATH, client_factory=factory, num_workers=args.workers,
            now=now, on_error=append_error_jsonl, recent_cutoff=_recent_cutoff(),
            max_hours=args.max_hours, circuit_breaker=cb)
    else:
        conn = connect(DB_PATH)
        init_schema(conn)
        sellers = sellers_repo.load_sellers(conn)
        conn.close()
        if not sellers:
            logging.error("No sellers in DB — run `python main.py crawl-sellers` first")
            return 1
        seller_ids = list(sellers)
        if args.limit_sellers:
            seller_ids = seller_ids[: args.limit_sellers]

        logging.info("page-scan start %s (%d sellers, workers=%d, sleep=%.2f, max_hours=%s)",
                     now, len(seller_ids), args.workers, args.sleep, args.max_hours)
        summary = run_page_scan(
            db_path=DB_PATH, sellers=seller_ids, client_factory=factory_default,
            num_workers=args.workers, now=now, on_error=append_error_jsonl,
            max_hours=args.max_hours, circuit_breaker=cb)

    if cb.is_open():
        logging.warning("Circuit breaker tripped (%d blocks) — likely IP block.", len(cb._events))
    logging.info("Done: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
