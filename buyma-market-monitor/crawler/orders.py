"""BUYMA sales page parsing and incremental orders crawling."""
import re
import time
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

from crawler.client import BlockedByServer, MaxRetriesExceeded


SALES_URL_TEMPLATE = "https://www.buyma.com/buyer/{seller_id}/sales_{page}.html"
ITEM_URL_TEMPLATE = "https://www.buyma.com/item/{item_id}/"

_ITEM_HREF_PATTERN = re.compile(r"/item/(\d+)/")
_QTY_PATTERN = re.compile(r"注文\s*数\s*[：:]\s*(\d+)")
_DATE_PATTERN = re.compile(r"成約日\s*[：:]\s*(\d{4}/\d{2}/\d{2})")


@dataclass(frozen=True)
class OrderEntry:
    sale_date: str   # "YYYY/MM/DD"
    item_id: str
    qty: int
    item_name: str
    item_url: str


def build_sales_url(seller_id: str, page: int) -> str:
    return SALES_URL_TEMPLATE.format(seller_id=seller_id, page=page)


def parse_sales_page(html: str) -> list[OrderEntry]:
    """Return OrderEntry list in page order. Entries that fail to parse are skipped."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[OrderEntry] = []
    for li in soup.find_all("li", class_="buyeritemtable_info"):
        entry = _parse_li(li)
        if entry is not None:
            entries.append(entry)
    return entries


def _parse_li(li) -> Optional[OrderEntry]:
    a = li.select_one("p.buyeritem_name a[href]")
    if a is None:
        return None
    href = a.get("href", "")
    m_id = _ITEM_HREF_PATTERN.search(href)
    if m_id is None:
        return None
    item_id = m_id.group(1)
    item_name = a.get_text(strip=True)
    if not item_name:
        return None

    qty: Optional[int] = None
    sale_date: Optional[str] = None
    for p in li.find_all("p"):
        text = p.get_text(" ", strip=True)
        if qty is None:
            m_q = _QTY_PATTERN.search(text)
            if m_q:
                qty = int(m_q.group(1))
        if sale_date is None:
            m_d = _DATE_PATTERN.search(text)
            if m_d:
                sale_date = m_d.group(1)
        if qty is not None and sale_date is not None:
            break

    if qty is None or sale_date is None:
        return None

    return OrderEntry(
        sale_date=sale_date,
        item_id=item_id,
        qty=qty,
        item_name=item_name,
        item_url=ITEM_URL_TEMPLATE.format(item_id=item_id),
    )


WatermarkTuple = tuple[str, str, int]  # (sale_date, item_id, qty)


def find_watermark_boundary(
    page_signatures: list[WatermarkTuple],
    watermark: list[WatermarkTuple],
) -> int:
    """Find the start index where `watermark` appears as a contiguous subsequence
    of `page_signatures`. Return -1 if not found or watermark is empty.

    Callers interpret page_signatures[:boundary] as new entries.
    """
    if not watermark:
        return -1
    L = len(watermark)
    P = len(page_signatures)
    if L > P:
        return -1
    for i in range(P - L + 1):
        if page_signatures[i:i + L] == watermark:
            return i
    return -1


WATERMARK_SIZE = 30
DEFAULT_MAX_PAGES = 100


def crawl_seller_orders(
    client,
    seller_id: str,
    watermark: list[WatermarkTuple],
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[OrderEntry], list[WatermarkTuple], list[str], int, bool]:
    """Walk sales_1, sales_2, ... until the watermark matches, the date ceiling
    is passed, or max_pages is reached.

    Returns:
      (new_orders, new_watermark, warnings, pages_scanned, overcollected)

    overcollected is True when the watermark was non-empty but never matched, so
    the result was bounded by the date ceiling (sale_date >= prev_max) instead.
    """
    accumulated: list[OrderEntry] = []
    warnings: list[str] = []
    page_num_done = 0

    prev_max = max((t[0] for t in watermark), default=None)
    wm_min = min((t[0] for t in watermark), default=None)

    for page_num in range(1, max_pages + 1):
        url = build_sales_url(seller_id, page_num)
        try:
            response = client.get(url)
        except MaxRetriesExceeded as e:
            if e.last_status == 404:
                page_num_done = page_num
                break
            raise
        page_entries = parse_sales_page(response.text)
        page_num_done = page_num
        if not page_entries:
            break
        accumulated.extend(page_entries)
        signatures = [(e.sale_date, e.item_id, e.qty) for e in accumulated]
        boundary = find_watermark_boundary(signatures, watermark)
        if boundary >= 0:
            new_orders = accumulated[:boundary]
            new_watermark = _build_new_watermark(accumulated)
            return new_orders, new_watermark, warnings, page_num_done, False
        # Date-ceiling early stop: pages are sale-date descending. Once the oldest
        # accumulated entry is older than the watermark's oldest date, the full
        # watermark block has been covered without matching -> stop paging.
        if wm_min is not None and accumulated[-1].sale_date < wm_min:
            break

    # Watermark never matched.
    if watermark:
        new_orders = [e for e in accumulated if e.sale_date >= prev_max]
        if page_num_done == max_pages:
            reason = f"max_pages={max_pages} reached"
        else:
            reason = f"history ended/ceiling at page {page_num_done}"
        warnings.append(
            f"watermark not matched for seller {seller_id} ({reason}): "
            f"bounding to {len(new_orders)} entries at/after {prev_max} (over-collection guard)"
        )
        new_watermark = _build_new_watermark(accumulated)
        return new_orders, new_watermark, warnings, page_num_done, True

    # Empty watermark (new seller / full rescan): full scan is expected.
    new_watermark = _build_new_watermark(accumulated)
    return accumulated, new_watermark, warnings, page_num_done, False


def _build_new_watermark(entries: list[OrderEntry]) -> list[WatermarkTuple]:
    return [(e.sale_date, e.item_id, e.qty) for e in entries[:WATERMARK_SIZE]]


import queue
import threading
from typing import Callable


SELLER_MAX_WORKERS = 3


def crawl_all_orders_with_factory(
    client_factory: Callable[[], object],
    seller_watermarks: dict[str, list[WatermarkTuple]],
    on_seller_done: Callable[[str, list[OrderEntry], list[WatermarkTuple], int, list[str], bool], None],
    on_error: Callable[..., None],
    max_pages: int = DEFAULT_MAX_PAGES,
    num_workers: int = SELLER_MAX_WORKERS,
    circuit_breaker=None,
    deadline: float | None = None,
) -> dict:
    """Run N sellers across num_workers worker threads, one client per worker.

    on_seller_done(seller_id, new_orders, new_watermark, pages_scanned, warnings, overcollected) is called
    after each seller completes (success path).

    on_error(stage="orders", url=..., status=..., reason=...) is called when seller processing
    raises an exception other than BlockedByServer.

    BlockedByServer triggers a graceful abort: all workers stop, no further sellers are processed.
    The returned stats dict will have aborted_by_ip_block=True in that case.

    Returns stats dict with sellers_processed / sellers_with_new_orders / total_new_orders /
    max_pages_reached_warnings / aborted_by_ip_block.
    """
    work_queue: queue.Queue = queue.Queue()
    for sid in seller_watermarks:
        work_queue.put(sid)

    stats = {
        "sellers_processed": 0,
        "sellers_with_new_orders": 0,
        "total_new_orders": 0,
        "max_pages_reached_warnings": 0,
        "aborted_by_ip_block": False,
    }
    stats_lock = threading.Lock()

    def _cb_open() -> bool:
        return circuit_breaker is not None and circuit_breaker.is_open()

    def worker():
        client = client_factory()
        try:
            while not _cb_open():
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    sid = work_queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    watermark = seller_watermarks.get(sid, [])
                    try:
                        new_orders, new_watermark, warnings, pages_scanned, overcollected = crawl_seller_orders(
                            client, sid, watermark, max_pages=max_pages,
                        )
                        on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings, overcollected)
                        with stats_lock:
                            stats["sellers_processed"] += 1
                            if new_orders:
                                stats["sellers_with_new_orders"] += 1
                                stats["total_new_orders"] += len(new_orders)
                            if warnings:
                                stats["max_pages_reached_warnings"] += len(warnings)
                    except BlockedByServer:
                        # circuit_breaker.record_block() was already called by PlaywrightClient.
                        # If threshold=1, the breaker is now open; the while-loop guard exits.
                        return
                    except Exception as e:
                        status = getattr(e, "last_status", None)
                        on_error(
                            stage="orders",
                            url=build_sales_url(sid, 1),
                            status=status,
                            reason=repr(e),
                        )
                        with stats_lock:
                            stats["sellers_processed"] += 1
                finally:
                    work_queue.task_done()
        finally:
            if hasattr(client, "close"):
                client.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats["aborted_by_ip_block"] = _cb_open()
    return stats
