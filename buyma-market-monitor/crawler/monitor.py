"""Shared helpers for the page_scan / revisit pipeline.

Provides apply_seller_scan_to_db (reconcile a seller's scanned items with the DB),
apply_enrich (write item detail + stats to DB), fetch_for_classification /
apply_classification (HTTP fetch + DB write for disappeared-item status checks),
and the SellerScanOutcome dataclass returned by apply_seller_scan_to_db.
"""
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from crawler.item_detail import build_item_detail_url, parse_item_detail
from crawler.item_status import ItemStatus, classify_status_from_response
from storage.items_repo import (
    upsert_scanned_item,
    record_price_observation,
    get_active_item_ids_for_seller,
    get_item,
    get_seller_items_state,
    bulk_upsert_scanned_items,
    bulk_record_price_observations,
    update_detail_fields,
    mark_status,
    replace_item_images,
    replace_item_variants,
    record_stats_observation,
    record_stylehaus_observation,
)


@dataclass
class SellerScanOutcome:
    seller_id: str = ""
    new_item_ids: set[str] = field(default_factory=set)
    resurrected_item_ids: set[str] = field(default_factory=set)
    disappeared_item_ids: set[str] = field(default_factory=set)
    price_changes: int = 0
    skipped_due_to_empty_scan: bool = False


def apply_seller_scan_to_db(
    conn: sqlite3.Connection,
    seller_id: str,
    scanned_items: list[dict],
    now: str,
) -> SellerScanOutcome:
    """Reconcile a fresh scan of one seller with the DB. Single transaction.

    Returns the outcome so the orchestrator can drive the enrich stage and
    log aggregate stats. Does NOT classify disappeared items here — that
    requires HTTP fetches and is handled by the enrich stage.
    """
    outcome = SellerScanOutcome(seller_id=seller_id)

    if not scanned_items:
        outcome.skipped_due_to_empty_scan = True
        return outcome

    conn.execute("BEGIN")
    try:
        # One SELECT loads every existing item of this seller; the new/resurrected/
        # price-change/disappeared decisions are then made in Python, and all writes
        # go out as two batched statements. On remote MySQL this turns a whale
        # seller's thousands of round-trips into a handful.
        existing = get_seller_items_state(conn, seller_id)
        prev_active = {iid for iid, (status, _) in existing.items() if status == "ACTIVE"}
        scanned_ids: set[str] = set()

        upsert_rows: list[tuple] = []
        price_rows: list[tuple] = []
        for it in scanned_items:
            item_id = it["item_id"]
            scanned_ids.add(item_id)
            price = it["price"]
            upsert_rows.append((item_id, seller_id, it["name"], price, "ACTIVE", now, now))

            prior = existing.get(item_id)
            if prior is None:
                outcome.new_item_ids.add(item_id)
                if price is not None:
                    price_rows.append((item_id, now, price))
            else:
                prior_status, prior_price = prior
                if prior_status in ("SOLD_OUT", "DELETED"):
                    outcome.resurrected_item_ids.add(item_id)
                if price is not None and price != prior_price:
                    price_rows.append((item_id, now, price))
                    outcome.price_changes += 1

        bulk_upsert_scanned_items(conn, upsert_rows)
        bulk_record_price_observations(conn, price_rows)

        outcome.disappeared_item_ids = prev_active - scanned_ids
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return outcome



def apply_enrich(conn: sqlite3.Connection, item_id: str, html: str, now: str) -> None:
    """DB write only — caller holds the db_lock. Writes items detail columns
    plus item_images, item_variants, stats_history, and stylehaus_history
    (delta), all in one transaction so a crash cannot leave a half-enriched item."""
    meta = parse_item_detail(html)
    tags = meta["tags"]
    size_chart = meta["size_chart"]
    conn.execute("BEGIN")
    try:
        update_detail_fields(
            conn, item_id=item_id,
            brand=meta["brand"],
            category_path=meta["category_path"],
            origin_country=meta["origin_country"],
            image_url=meta["image_url"],
            description=meta["description"],
            size_guide_text=meta["size_guide_text"],
            view_count=meta["view_count"],
            fav_count=meta["fav_count"],
            inquiry_count=meta["inquiry_count"],
            brand_model_number=meta["brand_model_number"],
            tags=json.dumps(tags, ensure_ascii=False) if tags else None,
            themes=meta["themes"],
            size_chart_json=json.dumps(size_chart, ensure_ascii=False) if size_chart else None,
            listed_at=meta["listed_at"],
            fetched_at=now,
        )
        replace_item_images(conn, item_id, meta["image_urls"])
        replace_item_variants(conn, item_id, meta["variants"])
        record_stats_observation(conn, item_id, meta["view_count"], meta["fav_count"], meta["inquiry_count"], now)
        record_stylehaus_observation(
            conn, item_id, meta["has_style_haus"], meta["stylehaus_video_count"], now,
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def fetch_for_classification(client, item_id: str, on_error: Callable[..., None]):
    """HTTP fetch only — returns (status_code, body), or None on error.

    Uses get_allowing_4xx if available so 404 doesn't raise; falls back to .get
    for clients that don't expose it (e.g. test fakes).
    """
    url = build_item_detail_url(item_id)
    try:
        resp = (
            client.get_allowing_4xx(url)
            if hasattr(client, "get_allowing_4xx")
            else client.get(url)
        )
    except Exception as e:
        on_error(stage="status_check", url=url,
                 status=getattr(e, "last_status", None), reason=repr(e))
        return None
    return resp.status_code, resp.text


def apply_classification(
    conn: sqlite3.Connection, item_id: str, status_code: int, now: str
) -> ItemStatus:
    """DB write only — caller holds the db_lock."""
    status = classify_status_from_response(status_code, "")
    mark_status(conn, item_id, status.value, now)
    return status
