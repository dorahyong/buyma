import queue
import re
import threading
import time
from typing import Callable

from bs4 import BeautifulSoup


SELLER_URL_TEMPLATE = "https://www.buyma.com/buyer/{seller_id}/sales_1.html"
SELLER_MAX_WORKERS = 3
KOREA_COUNTRY_LABEL = "한국"
_COUNTRY_ALT_MAP = {
    "韓国": "한국",
    "한국": "한국",
}
_DIGIT_PATTERN = re.compile(r"[\d,]+")


def build_seller_url(seller_id: str) -> str:
    return SELLER_URL_TEMPLATE.format(seller_id=seller_id)


def parse_seller_page(html: str, seller_id: str) -> dict | None:
    """Parse a seller page (sales_1.html). Returns None if filter rejected or invalid."""
    soup = BeautifulSoup(html, "lxml")

    seller_type = _extract_seller_type(soup)
    if seller_type is None:
        return None

    seller_name = _extract_seller_name(soup)
    if not seller_name:
        return None

    country = _extract_country(soup)

    if not _passes_filter(seller_type, country):
        return None

    return {
        "seller_id": seller_id,
        "seller_name": seller_name,
        "seller_type": seller_type,
        "seller_url": build_seller_url(seller_id),
        "country": country,
        "follower_count": _extract_int_from_selector(soup, "#js_fan_count"),
        "listing_count": _extract_int_from_selector(soup, "span.syohin_cnt_text"),
        "order_count": _extract_order_count(soup),
    }


def _extract_seller_type(soup) -> str | None:
    label = soup.select_one("p.label")
    if label is None:
        return None
    if label.select_one("span.label_shop"):
        return "SHOP"
    if label.select_one("span.label_premium"):
        return "PREMIUM PERSONAL SHOPPER"
    text = label.get_text(strip=True).upper()
    return text or None


def _extract_seller_name(soup) -> str:
    el = soup.select_one("#buyer_name h1 a")
    if el is None:
        return ""
    return el.get_text(strip=True)


def _extract_country(soup) -> str | None:
    img = soup.select_one("#buyer_name h1 img")
    if img is None:
        return None
    alt = img.get("alt", "").strip()
    if not alt:
        return None
    return _COUNTRY_ALT_MAP.get(alt, alt)


def _extract_int_from_selector(soup, selector: str) -> int:
    el = soup.select_one(selector)
    if el is None:
        return 0
    return _parse_int(el.get_text())


def _extract_order_count(soup) -> int:
    """Pick the p.buyer_eva_text under <h3>注文実績</h3> (not avg shipping/success rate)."""
    order_h3 = soup.find("h3", string=lambda s: s and "注文実績" in s)
    if order_h3 is None:
        return 0
    order_p = order_h3.find_next("p", class_="buyer_eva_text")
    if order_p is None:
        return 0
    return _parse_int(order_p.get_text())


def _parse_int(text: str) -> int:
    m = _DIGIT_PATTERN.search(text)
    if m is None:
        return 0
    return int(m.group(0).replace(",", ""))


def _passes_filter(seller_type: str, country: str | None) -> bool:
    if seller_type == "SHOP":
        return True
    if seller_type in ("PREMIUM PERSONAL SHOPPER", "PERSONAL SHOPPER"):
        return country == KOREA_COUNTRY_LABEL
    return False


def crawl_sellers_with_factory(
    client_factory: Callable[[], "object"],
    seller_ids,
    on_error: Callable[..., None],
    num_workers: int = SELLER_MAX_WORKERS,
    deadline: float | None = None,
) -> list[dict]:
    """Crawl seller pages using N worker threads, each with its own client.

    `client_factory()` returns a fresh client instance. Each worker thread calls
    it once and reuses the client for all seller IDs it processes. The client
    must have a `.get(url) -> response with .text` interface and a `.close()`
    method (called when worker exits).

    Threading model: required because Playwright sync API is not thread-safe —
    each browser instance must be owned by a single thread.
    """
    work_queue = queue.Queue()
    for sid in seller_ids:
        work_queue.put(sid)

    results: list[dict] = []
    results_lock = threading.Lock()

    def worker():
        client = client_factory()
        try:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    sid = work_queue.get_nowait()
                except queue.Empty:
                    return
                url = build_seller_url(sid)
                try:
                    response = client.get(url)
                    parsed = parse_seller_page(response.text, sid)
                    if parsed is not None:
                        with results_lock:
                            results.append(parsed)
                except Exception as e:
                    status = getattr(e, "last_status", None)
                    on_error(
                        stage="seller",
                        url=url,
                        status=status,
                        reason=repr(e),
                    )
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

    return results
