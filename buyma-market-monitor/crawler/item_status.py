"""Classify the post-disappearance status of an item by re-fetching its page.

Phase 1 policy: 404/410 → DELETED, 200 → SOLD_OUT. The 200=SOLD_OUT mapping
is intentionally coarse and will be refined once we have real sold-out page
samples (see plan Task 14).
"""
from enum import Enum


class ItemStatus(str, Enum):
    DELETED = "DELETED"
    SOLD_OUT = "SOLD_OUT"


def classify_status_from_response(status_code: int, body: str) -> ItemStatus:
    if status_code in (404, 410):
        return ItemStatus.DELETED
    if status_code == 200:
        # TODO(phase-2): refine 200=SOLD_OUT by inspecting DOM for in-stock signal.
        # Plan: docs/superpowers/plans/2026-06-09-product-monitoring-pipeline.md (refinement task)
        _ = body
        return ItemStatus.SOLD_OUT
    raise ValueError(
        f"unexpected status {status_code} during status classification"
    )
