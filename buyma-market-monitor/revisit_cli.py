"""Adaptive revisit scheduler CLI.

  python revisit_cli.py run --max-hours 2 --workers 5 --sleep 0.3
  python revisit_cli.py run --loop --idle-minutes 10 --workers 5 --sleep 0.3
  python revisit_cli.py report --top 30
"""
import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from crawler.circuit_breaker import CircuitBreaker
from crawler.client import HttpClient
from crawler.revisit import run_revisit
from storage.db import connect, init_schema
from storage import revisit_repo
from storage.store import now_iso

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


def cmd_run(args) -> int:
    setup_logging(args.verbose)
    if not args.loop and args.max_hours is None:
        logging.error("--max-hours is required unless --loop is set")
        return 2
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cb = CircuitBreaker(threshold=args.cb_threshold, window_seconds=args.cb_window_seconds)

    def factory():
        return HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb)

    stop_event = threading.Event()
    if args.loop:
        def _handle_sigint(signum, frame):
            logging.info("SIGINT received — finishing current items, stopping loop...")
            stop_event.set()
        signal.signal(signal.SIGINT, _handle_sigint)

    now = now_iso()
    logging.info(
        "revisit run start %s (max_hours=%s, workers=%d, sleep=%.2f, loop=%s, idle_minutes=%.1f)",
        now, args.max_hours, args.workers, args.sleep, args.loop, args.idle_minutes)
    summary = run_revisit(
        db_path=DB_PATH, scan_client_factory=factory, num_workers=args.workers,
        now=now, on_error=append_error_jsonl,
        max_hours=(None if args.loop else args.max_hours),
        circuit_breaker=cb,
        loop=args.loop,
        idle_seconds=args.idle_minutes * 60.0,
        stop_event=(stop_event if args.loop else None))
    if cb.is_open():
        logging.warning(
            "Circuit breaker tripped (%d blocks observed) — likely IP block. "
            "Investigate before rerun.", len(cb._events))
    logging.info("Done: %s", summary)
    return 0


def cmd_report(args) -> int:
    conn = connect(DB_PATH)
    try:
        init_schema(conn)
        cov = revisit_repo.coverage_stats(conn)
        print(f"등록 {cov['total']:,} / 2회+관측 {cov['multi_observed']:,}  티어 {cov['by_tier']}")
        print(f"\n급등 TOP {args.top} (fav velocity/day):")
        print(f"{'item_id':>10}  {'tier':>4}  {'vel/day':>8}  {'obs':>3}  name")
        for r in revisit_repo.top_surging(conn, limit=args.top):
            print(f"{r['item_id']:>10}  {r['tier']:>4}  {r['last_velocity']:>8.1f}  "
                  f"{r['obs_count']:>3}  {(r['name'] or '')[:40]}")
    finally:
        conn.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="BUYMA adaptive revisit scheduler")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one revisit pass within a time budget")
    r.add_argument("--max-hours", type=float, required=False, default=None)
    r.add_argument("--loop", action="store_true")
    r.add_argument("--idle-minutes", type=float, default=10.0)
    r.add_argument("--workers", type=int, default=5)
    r.add_argument("--sleep", type=float, default=0.3)
    r.add_argument("--cb-threshold", type=int, default=5)
    r.add_argument("--cb-window-seconds", type=int, default=60)
    r.add_argument("--verbose", action="store_true")
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="show surge ranking + coverage")
    rep.add_argument("--top", type=int, default=30)
    rep.set_defaults(func=cmd_report)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
