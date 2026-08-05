"""품번 검색 노출순위(`/r/-O1/`) 1페이지 스냅샷 수집.

표시 순서 = 노출순위. 가격/기타 기준으로 재정렬하지 않는다.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bs4 import BeautifulSoup

from crawler.client import BlockedByServer, MaxRetriesExceeded
from storage.db import connect, init_schema
from storage import exposure_repo
SEARCH_URL_TEMPLATE = "https://www.buyma.com/r/-O1/{query}/"
_ITEM_HREF = re.compile(r"/item/(\d+)/?")
_BUYER_HREF = re.compile(r"/buyer/(\d+)")
_DIGITS = re.compile(r"[\d,]+")
_TOTAL_RESULTS = re.compile(r"([\d,]+)\s*件")


def build_search_url(model_query: str) -> str:
    """추천순(-O1) 검색 URL. 공백 등은 percent-encode."""
    cleaned = (
        model_query.replace("/", " ")
        .replace("#", " ")
        .replace("&", " ")
        .replace("?", " ")
        .strip()
    )
    encoded = urllib.parse.quote(cleaned, safe="")
    return SEARCH_URL_TEMPLATE.format(query=encoded)


def parse_search_page(html: str) -> dict[str, Any]:
    """검색 1페이지 파싱.

    Returns:
      {
        "listings": [{rank, item_id, price_yen, seller_name, seller_id}, ...],
        "total_results": int|None,
      }
    표시 DOM 순서 그대로 rank=1..N.
    """
    soup = BeautifulSoup(html, "lxml")
    listings: list[dict] = []
    products = soup.find_all("li", class_="product")
    for product in products:
        item_id = _extract_item_id(product)
        if not item_id:
            continue
        listings.append(
            {
                "rank": len(listings) + 1,
                "item_id": item_id,
                "price_yen": _extract_price(product),
                "seller_name": _extract_seller_name(product),
                "seller_id": _extract_seller_id(product),
            }
        )
    return {"listings": listings, "total_results": _extract_total_results(soup)}


def _extract_item_id(product) -> str | None:
    for a in product.find_all("a", href=True):
        m = _ITEM_HREF.search(a["href"])
        if m:
            return m.group(1)
    return None


def _extract_price(product) -> int | None:
    price_el = product.find("span", class_="Price_Txt")
    if price_el is None:
        price_el = product.find(class_=re.compile(r"price", re.I))
    if price_el is None:
        return None
    m = _DIGITS.search(price_el.get_text())
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _extract_seller_name(product) -> str | None:
    buyer = product.select_one(".product_Buyer a")
    if buyer is None:
        buyer = product.select_one("a[href*='/buyer/']")
    if buyer is None:
        return None
    name = buyer.get_text(strip=True)
    return name or None


def _extract_seller_id(product) -> str | None:
    buyer = product.select_one(".product_Buyer a")
    if buyer is None:
        buyer = product.select_one("a[href*='/buyer/']")
    if buyer is None or not buyer.get("href"):
        return None
    m = _BUYER_HREF.search(buyer["href"])
    return m.group(1) if m else None


def _extract_total_results(soup) -> int | None:
    # 예: "1,234件" / 검색 결과 헤더
    for sel in (".search_result_number", ".ResultSummary", "#content"):
        node = soup.select_one(sel) if not sel.startswith("#") else soup.select_one(sel)
        if node is None:
            continue
        m = _TOTAL_RESULTS.search(node.get_text(" ", strip=True))
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass
    m = _TOTAL_RESULTS.search(soup.get_text(" ", strip=True)[:2000])
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


@dataclass
class ExposureSummary:
    models_queued: int = 0
    ok: int = 0
    empty: int = 0
    blocked: int = 0
    errors: int = 0
    skipped_done: int = 0


def run_exposure_sweep(
    db_path: Path | str,
    client_factory: Callable[[], object],
    num_workers: int,
    now: str,
    on_error: Callable[..., None],
    max_hours: float | None = None,
    circuit_breaker=None,
    stop_event=None,
) -> ExposureSummary:
    """당일 미완 경쟁∪자사 품번을 -O1 1페이지로 스윕."""
    db_path = Path(db_path)
    summary = ExposureSummary()
    main_conn = connect(db_path)
    init_schema(main_conn)

    day_prefix = now[:10]  # YYYY-MM-DD
    pending = exposure_repo.list_pending_models(main_conn, day_prefix)
    summary.models_queued = len(pending)
    logging.info("[exposure] start models=%d day=%s", summary.models_queued, day_prefix)

    deadline = None if max_hours is None else time.monotonic() + max_hours * 3600.0
    db_lock = threading.Lock()
    counts_lock = threading.Lock()
    work: queue.Queue = queue.Queue()
    for m in pending:
        work.put(m)

    def _halt() -> bool:
        if deadline is not None and time.monotonic() >= deadline:
            return True
        if circuit_breaker is not None and circuit_breaker.is_open():
            return True
        if stop_event is not None and stop_event.is_set():
            return True
        return False

    def worker():
        client = client_factory()
        try:
            while not _halt():
                try:
                    model_query = work.get_nowait()
                except queue.Empty:
                    return
                url = build_search_url(model_query)
                try:
                    result = client.get(url)
                    parsed = parse_search_page(result.text)
                    listings = parsed["listings"]
                    total = parsed["total_results"]
                    status = "ok" if listings else "empty"
                    with db_lock:
                        exposure_repo.save_exposure_result(
                            main_conn,
                            model_query=model_query,
                            observed_at=now,
                            status=status,
                            listings=listings,
                            total_results=total,
                        )
                    with counts_lock:
                        if status == "ok":
                            summary.ok += 1
                        else:
                            summary.empty += 1
                except BlockedByServer as e:
                    with db_lock:
                        exposure_repo.save_exposure_result(
                            main_conn,
                            model_query=model_query,
                            observed_at=now,
                            status="blocked",
                        )
                    on_error(stage="exposure", url=url, status=403, reason=repr(e))
                    with counts_lock:
                        summary.blocked += 1
                    return  # CB open 가능 — 워커 종료, 상위에서 halt
                except (MaxRetriesExceeded, Exception) as e:
                    status_code = getattr(e, "last_status", None)
                    with db_lock:
                        try:
                            exposure_repo.save_exposure_result(
                                main_conn,
                                model_query=model_query,
                                observed_at=now,
                                status="error",
                            )
                        except Exception as db_e:
                            on_error(stage="exposure_db", url=url, status=None, reason=repr(db_e))
                    on_error(stage="exposure", url=url, status=status_code, reason=repr(e))
                    with counts_lock:
                        summary.errors += 1
        finally:
            if hasattr(client, "close"):
                client.close()

    try:
        if summary.models_queued == 0:
            logging.info("[exposure] done models=0 (nothing pending)")
            return summary
        threads = [
            threading.Thread(target=worker, daemon=True) for _ in range(max(1, num_workers))
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        logging.info(
            "[exposure] done models=%d ok=%d empty=%d blocked=%d errors=%d",
            summary.models_queued, summary.ok, summary.empty, summary.blocked, summary.errors,
        )
        return summary
    finally:
        main_conn.close()
