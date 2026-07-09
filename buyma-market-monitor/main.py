"""BUYMA Market Monitor CLI entry point."""
import argparse
import logging
import sys
from pathlib import Path

from crawler.client import HttpClient, PlaywrightClient, MaxRetriesExceeded
from crawler.circuit_breaker import CircuitBreaker
from crawler.listing import build_listing_url, crawl_listing_pages
from crawler.pagination import parse_max_pages
from crawler.seller import crawl_sellers_with_factory
from crawler.orders import crawl_all_orders_with_factory, DEFAULT_MAX_PAGES
from storage.store import Store, now_iso
from storage.db import connect, init_schema
from storage import orders_repo, sellers_repo
from dataclasses import asdict


DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "items.db"


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def detect_max_pages(http_client: HttpClient) -> int:
    url = build_listing_url(1)
    logging.info("Detecting max pages from %s", url)
    response = http_client.get(url)
    max_pages = parse_max_pages(response.text)
    logging.info("Detected max_pages = %d", max_pages)
    return max_pages


def run_crawl_sellers(args) -> int:
    store = Store(DATA_DIR)
    conn = connect(DB_PATH)
    init_schema(conn)
    config = store.load_config()

    with HttpClient() as http_client:
        if args.reset_pagination or "max_pages" not in config:
            try:
                max_pages = detect_max_pages(http_client)
            except MaxRetriesExceeded as e:
                logging.error("Failed to detect max pages: %s", e)
                return 1
            config["max_pages"] = max_pages
            config["max_pages_detected_at"] = now_iso()
            if not args.dry_run:
                store.save_config(config)
        else:
            max_pages = config["max_pages"]
            logging.info("Using cached max_pages = %d", max_pages)

        logging.info("Stage 1: scanning %d listing pages", max_pages)
        seller_ids = crawl_listing_pages(
            http_client,
            max_pages=max_pages,
            on_error=store.append_error,
        )
        logging.info("Found %d unique seller IDs in listings", len(seller_ids))

    logging.info("Stage 2: fetching %d seller pages via Playwright", len(seller_ids))
    collected = crawl_sellers_with_factory(
        client_factory=lambda: PlaywrightClient(),
        seller_ids=seller_ids,
        on_error=store.append_error,
        num_workers=3,
    )
    logging.info(
        "Collected %d sellers (filtered out %d)",
        len(collected),
        len(seller_ids) - len(collected),
    )

    timestamp = now_iso()
    new_sellers_map: dict[str, dict] = {}
    for seller in collected:
        seller["first_seen_at"] = timestamp
        seller["updated_at"] = timestamp
        new_sellers_map[seller["seller_id"]] = seller

    existing = sellers_repo.load_sellers(conn)

    errors_count = _count_errors_today(store.errors_path)

    config["last_run_at"] = timestamp
    config["last_run_stats"] = {
        "pages_scanned": max_pages,
        "sellers_found_in_listing": len(seller_ids),
        "sellers_collected": len(collected),
        "sellers_filtered_out": len(seller_ids) - len(collected),
        "errors": errors_count,
    }

    if args.dry_run:
        logging.info("DRY RUN: skipping save. Stats: %s", config["last_run_stats"])
    else:
        sellers_repo.upsert_sellers(conn, new_sellers_map)
        store.save_config(config)
        total = len(sellers_repo.load_sellers(conn))
        logging.info(
            "Saved sellers to DB (total %d, was %d, added/updated %d)",
            total,
            len(existing),
            len(new_sellers_map),
        )
    conn.close()

    return 0


def run_crawl_orders(args) -> int:
    import threading

    store = Store(DATA_DIR)
    conn = connect(DB_PATH)
    init_schema(conn)
    db_lock = threading.Lock()

    sellers = sellers_repo.load_sellers(conn)
    if not sellers:
        logging.error("No sellers in DB. Run `crawl-sellers` first.")
        return 1

    all_watermarks = orders_repo.load_all_watermarks(conn)

    seller_ids_all = sorted(sellers.keys())
    if args.seller_id:
        if args.seller_id not in sellers:
            logging.error("Seller %s not found in DB", args.seller_id)
            return 1
        seller_ids = [args.seller_id]
    else:
        seller_ids = seller_ids_all

    watermarks: dict[str, list] = {}
    for sid in seller_ids:
        if args.full_rescan:
            watermarks[sid] = []
        else:
            sig = all_watermarks.get(sid, {}).get("signature", [])
            watermarks[sid] = [tuple(t) for t in sig]

    logging.info(
        "Starting orders crawl: %d sellers, max_pages=%d, full_rescan=%s",
        len(watermarks), args.max_pages, args.full_rescan,
    )

    timestamp = now_iso()

    def on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings, overcollected):
        if not args.dry_run:
            prev_max = max((t[0] for t in watermarks.get(sid, [])), default=None)
            with db_lock:
                inserted = 0
                if new_orders:
                    order_dicts = []
                    for e in new_orders:
                        d = asdict(e)
                        d["seller_id"] = sid
                        d["collected_at"] = timestamp
                        order_dicts.append(d)
                    inserted = orders_repo.insert_orders_bounded(
                        conn, sid, order_dicts, prev_max, overcollected
                    )
                orders_repo.upsert_watermark(
                    conn, sid,
                    signature=[list(t) for t in new_watermark],
                    last_run_at=timestamp,
                    pages_scanned=pages_scanned,
                    orders_added=inserted,
                )
        if warnings:
            for w in warnings:
                logging.warning(w)
        logging.info("seller %s: +%d orders (pages=%d)", sid, len(new_orders), pages_scanned)

    cb = CircuitBreaker(threshold=1, window_seconds=60)

    stats = crawl_all_orders_with_factory(
        client_factory=lambda: PlaywrightClient(circuit_breaker=cb),
        seller_watermarks=watermarks,
        on_seller_done=on_seller_done,
        on_error=store.append_error,
        max_pages=args.max_pages,
        num_workers=3,
        circuit_breaker=cb,
    )

    if stats.get("aborted_by_ip_block"):
        print("")
        print("=" * 64)
        print("⚠️  BUYMA returned 403 Forbidden — your IP is likely blocked.")
        print("    Check your IP address (switch networks / VPN / wait) and retry.")
        print("=" * 64)
        logging.error("Crawl aborted due to 403 Forbidden (IP block suspected)")
        errors_today = _count_errors_today(store.errors_path)
        stats["errors"] = errors_today
        if not args.dry_run:
            orders_repo.save_run_meta(conn, timestamp, stats)
        conn.close()
        return 2

    errors_today = _count_errors_today(store.errors_path)
    stats["errors"] = errors_today
    if not args.dry_run:
        orders_repo.save_run_meta(conn, timestamp, stats)

    conn.close()
    logging.info("Stats: %s", stats)
    return 0


def _count_errors_today(errors_path: Path) -> int:
    if not errors_path.exists():
        return 0
    today = now_iso()[:10]
    count = 0
    with errors_path.open(encoding="utf-8") as f:
        for line in f:
            if today in line:
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="BUYMA Market Monitor CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # crawl-sellers
    sp_sellers = subparsers.add_parser("crawl-sellers", help="Crawl Korean sellers from BUYMA listing")
    sp_sellers.add_argument("--reset-pagination", action="store_true",
                            help="Force re-detection of max_pages")
    sp_sellers.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    sp_sellers.add_argument("--dry-run", action="store_true", help="Do not write to data files")
    sp_sellers.set_defaults(func=run_crawl_sellers)

    # crawl-orders
    sp_orders = subparsers.add_parser("crawl-orders", help="Crawl incremental orders for known sellers")
    sp_orders.add_argument("--max-pages", type=int, default=100,
                           help="Max sales pages per seller (default: 100)")
    sp_orders.add_argument("--seller-id", type=str, default=None,
                           help="Crawl only this single seller (for debugging)")
    sp_orders.add_argument("--full-rescan", action="store_true",
                           help="Ignore existing watermarks and re-collect everything")
    sp_orders.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    sp_orders.add_argument("--dry-run", action="store_true", help="Do not write to data files")
    sp_orders.set_defaults(func=run_crawl_orders)

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        return args.func(args)
    except KeyboardInterrupt:
        logging.warning("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
