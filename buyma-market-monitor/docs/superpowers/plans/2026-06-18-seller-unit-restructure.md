# Seller-Unit Scan+Enrich Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `run_monitor` so each worker processes one seller end-to-end (scan → enrich new+stranded → classify disappeared) before moving to the next, instead of the current "all sellers scanned, then all items enriched" two-phase global structure.

**Architecture:** Replace the two-phase (`scan_worker` pool → join → `enrich_worker` pool) design with a single `seller_worker` pool. Each worker pulls a seller from one queue and completes that seller fully: scan its listing pages, enrich its new + stranded items, classify its disappeared items, then pick the next seller. This makes time-limited / interrupted runs leave "completed sellers" rather than "all-scanned-but-none-enriched", which the 30-min live test proved is the current failure mode. Behavior (what data ends up in the DB) is preserved; only execution ordering changes. All existing helpers (`scan_seller_items`, `apply_seller_scan_to_db`, `fetch_for_enrich`/`apply_enrich`, `fetch_for_classification`/`apply_classification`, CircuitBreaker, empty-scan guard) are reused unchanged.

**Tech Stack:** Python 3.14, threading, sqlite3, pytest.

---

## Why this change (evidence)

A 30-minute timed run with `--limit-sellers 30` scanned 29 sellers (71,012 items into the DB) but enriched **0 new items** — `enriched` stayed at the prior 12,603. The entire 30 minutes went to Stage A (listing scan); Stage B never started because it only begins after ALL sellers finish Stage A. Listing scan is heavier than first estimated (~2.5s/page; full 482-seller Stage A is ~5-8h on 3 workers). So any run shorter than the full Stage A produces zero enriched detail (no images/variants/inquiry counts). Seller-unit processing fixes this: a 30-min run would instead fully complete N sellers.

---

## File Structure

**Modify:**
- `crawler/monitor.py` — replace the body of `run_monitor` (the two worker pools) with a single seller-worker pool. `RunSummary`, `apply_seller_scan_to_db`, `apply_enrich`, `apply_classification`, `fetch_for_*`, and all module-level imports stay as-is.
- `tests/test_monitor.py` — existing `run_monitor` tests must still pass (behavior preserved); add tests for the new per-seller completion + per-seller stranded recovery semantics.

**Untouched:**
- `crawler/seller_items_crawler.py`, `crawler/item_detail.py`, `storage/*`, `monitor_cli.py` (its `run_monitor(...)` call signature is unchanged).

---

## Key design decisions (locked)

1. **One worker = one seller at a time, fully completed.** Worker loop: `get seller → scan → reconcile (apply_seller_scan_to_db) → enrich (new + stranded) → classify disappeared → next seller`.
2. **Per-seller stranded recovery.** Instead of a global `detail_fetched_at IS NULL` sweep between phases, each seller's enrich targets = `outcome.new_item_ids` ∪ that seller's existing `detail_fetched_at IS NULL AND status='ACTIVE'` rows. This recovers items left un-enriched by a prior interrupted run, scoped to the seller currently being processed.
3. **One HTTP client per worker, used for both scan and enrich.** Both are plain `HttpClient`. Build it from `scan_client_factory()`. The `enrich_client_factory` parameter is kept in the signature (backward compat, monitor_cli passes it) but is no longer separately used — document this. (Rationale: a worker now interleaves scan and enrich for the same seller, so a single reused connection is simplest and avoids doubling open browsers/sockets.)
4. **CircuitBreaker checked at loop top AND between enrich items**, so a trip stops work promptly without finishing a huge seller.
5. **DB writes stay under `db_lock`; HTTP fetches stay outside the lock** — same concurrency rule as today. Reconcile (one `apply_seller_scan_to_db` call) is one locked critical section; each enrich/classify DB write is its own locked section; the fetches between them are lock-free.
6. **Signature, `RunSummary` fields, and `monitor_runs` row handling unchanged.** `summary.sellers_scanned` counts sellers actually dequeued and processed (i.e. `len(sellers) - seller_queue.qsize()` at the end).

---

# Tasks

### Task 1: Add per-seller stranded query helper to items_repo

**Files:**
- Modify: `storage/items_repo.py`
- Test: `tests/test_items_repo.py`

A small, independently-testable query the worker will use to find a seller's un-enriched items.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_items_repo.py`:

```python
from storage.items_repo import get_unenriched_active_item_ids_for_seller


def test_get_unenriched_active_item_ids_for_seller(tmp_path: Path):
    conn = make_conn(tmp_path)
    # two items for S1: one enriched, one not; one item for S2 not enriched
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-18T10:00:00+09:00")
    upsert_scanned_item(conn, "2", "S1", "B", 200, "2026-06-18T10:00:00+09:00")
    upsert_scanned_item(conn, "3", "S2", "C", 300, "2026-06-18T10:00:00+09:00")
    # mark item 1 enriched
    update_detail_fields(
        conn, item_id="1", brand="x", category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None, view_count=None,
        fav_count=None, inquiry_count=None, brand_model_number=None, themes=None,
        size_chart_json=None, fetched_at="2026-06-18T11:00:00+09:00",
    )
    ids = get_unenriched_active_item_ids_for_seller(conn, "S1")
    assert ids == {"2"}  # item 1 enriched, item 3 is S2


def test_get_unenriched_excludes_sold_out(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-18T10:00:00+09:00")
    mark_status(conn, "1", "SOLD_OUT", "2026-06-18T10:30:00+09:00")
    ids = get_unenriched_active_item_ids_for_seller(conn, "S1")
    assert ids == set()  # not ACTIVE → excluded
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_items_repo.py -v -k unenriched
```

Expected: ImportError on `get_unenriched_active_item_ids_for_seller`.

- [ ] **Step 3: Implement**

Append to `storage/items_repo.py`:

```python
def get_unenriched_active_item_ids_for_seller(
    conn: sqlite3.Connection, seller_id: str
) -> set[str]:
    """ACTIVE items for this seller that have not been detail-enriched yet
    (detail_fetched_at IS NULL). Used to recover items left un-enriched by a
    prior interrupted run, scoped to the seller being processed."""
    rows = conn.execute(
        "SELECT item_id FROM items "
        "WHERE seller_id = ? AND status = 'ACTIVE' AND detail_fetched_at IS NULL",
        (seller_id,),
    )
    return {r["item_id"] for r in rows}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_items_repo.py -v -k unenriched
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add storage/items_repo.py tests/test_items_repo.py
git commit -m "feat(repo): add get_unenriched_active_item_ids_for_seller for per-seller stranded recovery"
```

---

### Task 2: Restructure run_monitor to seller-unit worker pool

**Files:**
- Modify: `crawler/monitor.py`
- Test: `tests/test_monitor.py`

- [ ] **Step 1: Add the new behavioral tests**

Append to `tests/test_monitor.py` (the file already imports `Path`, `connect`, `init_schema`, `upsert_scanned_item`, `get_item`, `run_monitor`, `RunSummary`, `FakeDetailClient`, `SAMPLE_DETAIL_HTML`, `SAMPLE_404_HTML`, `make_conn`; reuse them — verify before adding imports):

```python
def test_run_monitor_completes_each_seller_fully(tmp_path: Path):
    """Each scanned seller should be fully enriched in the same run (not left
    stranded), proving seller-unit processing."""
    db_path = tmp_path / "items.db"
    conn = connect(db_path); init_schema(conn); conn.close()

    def _items_page(items):
        cards = "".join(
            f'<li class="buyeritemtable_info">'
            f'<p class="buyeritem_name"><a href="/item/{iid}/">N{iid}</a></p>'
            f'<p class="buyeritem_price">¥{500 + i}</p></li>'
            for i, iid in enumerate(items)
        )
        return f"<html><body><ul>{cards}</ul></body></html>"

    fake = FakeDetailClient({
        "https://www.buyma.com/buyer/S1/item_1.html": (200, _items_page(["111", "222"])),
        "https://www.buyma.com/buyer/S2/item_1.html": (200, _items_page(["333"])),
        "https://www.buyma.com/item/111/": (200, SAMPLE_DETAIL_HTML),
        "https://www.buyma.com/item/222/": (200, SAMPLE_DETAIL_HTML),
        "https://www.buyma.com/item/333/": (200, SAMPLE_DETAIL_HTML),
    })

    summary = run_monitor(
        db_path=db_path,
        sellers={"S1": {"seller_id": "S1"}, "S2": {"seller_id": "S2"}},
        scan_client_factory=lambda: fake,
        enrich_client_factory=lambda: fake,
        num_workers=1,
        now="2026-06-18T10:00:00+09:00",
        on_error=lambda **kw: None,
    )
    assert summary.sellers_scanned == 2
    assert summary.items_new == 3
    conn = connect(db_path)
    # ALL items enriched within this single run (no stranded)
    stranded = conn.execute(
        "SELECT COUNT(*) FROM items WHERE detail_fetched_at IS NULL AND status='ACTIVE'"
    ).fetchone()[0]
    assert stranded == 0
    assert conn.execute("SELECT COUNT(*) FROM item_images").fetchone()[0] >= 3
    conn.close()


def test_run_monitor_recovers_prior_seller_stranded(tmp_path: Path):
    """An item left un-enriched by a prior run (detail NULL) is enriched when
    its seller is processed, even though it is not 'new' this run."""
    db_path = tmp_path / "items.db"
    conn = connect(db_path); init_schema(conn)
    # pre-seed an ACTIVE item with NULL detail (prior interrupted run)
    upsert_scanned_item(conn, "111", "S1", "old", 500, "2026-06-17T10:00:00+09:00")
    assert get_item(conn, "111")["detail_fetched_at"] is None
    conn.close()

    def _items_page(items):
        cards = "".join(
            f'<li class="buyeritemtable_info">'
            f'<p class="buyeritem_name"><a href="/item/{iid}/">N{iid}</a></p>'
            f'<p class="buyeritem_price">¥{500 + i}</p></li>'
            for i, iid in enumerate(items)
        )
        return f"<html><body><ul>{cards}</ul></body></html>"

    fake = FakeDetailClient({
        # scan still lists item 111 (so it stays ACTIVE, is_new=False)
        "https://www.buyma.com/buyer/S1/item_1.html": (200, _items_page(["111"])),
        "https://www.buyma.com/item/111/": (200, SAMPLE_DETAIL_HTML),
    })

    summary = run_monitor(
        db_path=db_path,
        sellers={"S1": {"seller_id": "S1"}},
        scan_client_factory=lambda: fake,
        enrich_client_factory=lambda: fake,
        num_workers=1,
        now="2026-06-18T10:00:00+09:00",
        on_error=lambda **kw: None,
    )
    # item 111 is NOT new this run (already existed), but must be enriched via
    # per-seller stranded recovery
    assert summary.items_new == 0
    conn = connect(db_path)
    row = get_item(conn, "111")
    assert row["detail_fetched_at"] == "2026-06-18T10:00:00+09:00"
    assert row["brand"] is not None
    conn.close()
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
.venv/bin/pytest tests/test_monitor.py -v -k "completes_each_seller or recovers_prior_seller"
```

Expected: They FAIL against the current two-phase implementation — specifically `test_run_monitor_recovers_prior_seller_stranded` may already pass (global stranded sweep exists today) but `test_run_monitor_completes_each_seller_fully` exercises the same path; both should pass after the restructure. (If both already pass, that's fine — they lock in the behavior; proceed to refactor and keep them green.)

- [ ] **Step 3: Replace run_monitor's worker section**

In `crawler/monitor.py`, replace EVERYTHING from the line `# --- Stage A: scan + reconcile ---...` down to (and including) the second `for t in threads: t.join()` block that ends Stage B — i.e. the two worker pools — with the single seller-worker pool below. Keep the function header, the `main_conn`/`run_id`/`db_lock` setup above it, and the `monitor_runs` UPDATE + `main_conn.close()` + `return summary` below it unchanged.

The replacement section:

```python
    # --- Single pass: each worker processes one seller end-to-end ----------
    seller_queue: queue.Queue[str] = queue.Queue()
    for sid in sellers:
        seller_queue.put(sid)

    error_count = [0]
    error_lock = threading.Lock()

    def tracking_on_error(**kw):
        with error_lock:
            error_count[0] += 1
        on_error(**kw)

    def seller_worker():
        # One HTTP client per worker, reused for both scan and enrich.
        client = scan_client_factory()
        try:
            while not _cb_open():
                try:
                    sid = seller_queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    # Stage A: scan this seller's listing pages (HTTP, no lock)
                    items = scan_seller_items(client, sid, tracking_on_error)
                    with db_lock:
                        outcome = apply_seller_scan_to_db(main_conn, sid, items, now)
                        summary.items_new += len(outcome.new_item_ids)
                        summary.items_updated += outcome.price_changes
                        # enrich targets = new this run + this seller's stranded
                        enrich_ids = set(outcome.new_item_ids)
                        enrich_ids |= get_unenriched_active_item_ids_for_seller(
                            main_conn, sid
                        )
                        disappeared_ids = set(outcome.disappeared_item_ids)

                    # Stage B for THIS seller: enrich new + stranded
                    for iid in enrich_ids:
                        if _cb_open():
                            break
                        body = fetch_for_enrich(client, iid, tracking_on_error)
                        if body is not None:
                            with db_lock:
                                apply_enrich(main_conn, iid, body, now)

                    # classify items that disappeared from this seller's listing
                    for iid in disappeared_ids:
                        if _cb_open():
                            break
                        fetched = fetch_for_classification(client, iid, tracking_on_error)
                        if fetched is not None:
                            status_code, _ = fetched
                            with db_lock:
                                status = apply_classification(main_conn, iid, status_code, now)
                                if status is ItemStatus.DELETED:
                                    summary.items_deleted += 1
                                elif status is ItemStatus.SOLD_OUT:
                                    summary.items_sold_out += 1
                finally:
                    seller_queue.task_done()
        finally:
            if hasattr(client, "close"):
                client.close()

    threads = [threading.Thread(target=seller_worker, daemon=True) for _ in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary.sellers_scanned = len(sellers) - seller_queue.qsize()
```

Also: at the top of `crawler/monitor.py`, the import block from `storage.items_repo` must now include `get_unenriched_active_item_ids_for_seller`. Add it to the existing `from storage.items_repo import (...)` group (do not duplicate other names).

Note: the old code referenced `scan_seller_items` via a local `from crawler.seller_items_crawler import scan_seller_items` inside `run_monitor`. Keep that local import line where it is.

The docstring of `run_monitor` should be updated to: replace the "If a circuit_breaker..." paragraph's surrounding text so it reads:

```python
    """Run one full scan+enrich pass over all sellers. Single SQLite DB.

    Each worker processes one seller end-to-end — scan its listing pages,
    enrich its new and previously-stranded items, classify its disappeared
    items — before taking the next seller. A time-limited or interrupted run
    therefore leaves fully-completed sellers rather than all-scanned-but-
    none-enriched. If a circuit_breaker is provided, workers stop promptly
    when it trips.

    Note: enrich_client_factory is accepted for backward compatibility but is
    no longer used separately; each worker uses one client (from
    scan_client_factory) for both scan and enrich.
    """
```

- [ ] **Step 4: Run the new tests + full monitor suite**

```bash
.venv/bin/pytest tests/test_monitor.py -v
```

Expected: all pass, including the two new tests and all pre-existing run_monitor tests (`test_run_monitor_end_to_end_smoke`, `test_run_monitor_stops_when_circuit_breaker_trips`, `test_run_monitor_works_without_circuit_breaker`, `test_run_monitor_recovers_stranded_unenriched_items`, `test_stranded_items_picked_up_even_with_empty_scan`, `test_apply_enrich_writes_all_tables`). If any pre-existing test asserts intermediate two-phase behavior (e.g. that stranded items are enriched even when their seller is NOT scanned this run), see the note below.

**Compatibility note for `test_stranded_items_picked_up_even_with_empty_scan`:** that test pre-seeds a stranded item for a seller, then scans that SAME seller with an empty listing. Under seller-unit processing, the seller IS still processed (it's in the sellers dict), so its stranded item is recovered via `get_unenriched_active_item_ids_for_seller`. It should still pass. Under the OLD global sweep, stranded recovery happened regardless of which sellers were scanned; under the new design recovery is per-seller, so a stranded item whose seller is NOT in this run's `sellers` dict would NOT be recovered until that seller is next scanned. If `test_run_monitor_recovers_stranded_unenriched_items` seeds a stranded item under a seller that IS in the sellers dict, it passes; if it seeds under a seller NOT in the dict, update that test to include the seller in the `sellers` argument (this is the intended new semantics — recovery is scoped to sellers being processed). Make that adjustment if needed and note it in the commit.

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/pytest tests/ -q 2>&1 | tail -3
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add crawler/monitor.py tests/test_monitor.py
git commit -m "refactor(monitor): seller-unit scan+enrich — each worker completes one seller fully"
```

---

### Task 3: Live smoke test (temp DB) — verify seller-unit completion

**Files:**
- None (verification only)

- [ ] **Step 1: Run a 2-seller live smoke against a temp DB**

```bash
PYTHONPATH="$PWD" .venv/bin/python3 -c "
import os, tempfile
from storage.db import connect, init_schema
from storage import sellers_repo
from crawler.client import HttpClient
from crawler.circuit_breaker import CircuitBreaker
from crawler.monitor import run_monitor
from storage.store import now_iso

pconn = connect('data/items.db')
alls = sellers_repo.load_sellers(pconn); pconn.close()
# pick two small sellers (listing_count 10-40)
targets = {}
for sid, info in sorted(alls.items(), key=lambda kv: kv[1].get('listing_count') or 0):
    lc = info.get('listing_count') or 0
    if 10 <= lc <= 40:
        targets[sid] = info
    if len(targets) == 2: break
print('targets:', {k: v.get('listing_count') for k,v in targets.items()})

tmp = tempfile.mktemp(suffix='_su.db')
cb = CircuitBreaker(threshold=5, window_seconds=60)
def f(): return HttpClient(sleep_seconds=0.3, circuit_breaker=cb)
s = run_monitor(db_path=tmp, sellers=targets, scan_client_factory=f,
                enrich_client_factory=f, num_workers=2, now=now_iso(),
                on_error=lambda **kw: None, circuit_breaker=cb)
print('summary:', s)
c = connect(tmp)
stranded = c.execute(\"SELECT COUNT(*) FROM items WHERE detail_fetched_at IS NULL AND status='ACTIVE'\").fetchone()[0]
print('items:', c.execute('SELECT COUNT(*) FROM items').fetchone()[0],
      '| enriched:', c.execute('SELECT COUNT(*) FROM items WHERE detail_fetched_at IS NOT NULL').fetchone()[0],
      '| stranded:', stranded,
      '| images:', c.execute('SELECT COUNT(*) FROM item_images').fetchone()[0])
c.close(); os.remove(tmp)
print('OK — temp db removed, production untouched' if stranded == 0 else 'WARN: stranded != 0')
" 2>&1 | grep -v "HTTP Request\|DEBUG"
```

Expected: `summary` shows `items_new > 0`, `stranded: 0` (both sellers fully enriched in one pass), `images > 0`, and the final line `OK — ...`.

- [ ] **Step 2: No commit** (verification only). If `stranded != 0`, the restructure is incomplete — investigate before proceeding.

---

## Self-Review

**Spec coverage:**
- ✅ Seller-unit worker (Task 2 Step 3) — each worker completes one seller's scan→enrich→classify
- ✅ Per-seller stranded recovery (Task 1 helper + Task 2 use) — replaces global sweep
- ✅ Behavior preserved — existing run_monitor tests must stay green (Task 2 Step 4/5)
- ✅ Concurrency rule preserved — fetch outside lock, DB writes under db_lock (Task 2 Step 3 structure)
- ✅ Signature/RunSummary/monitor_runs unchanged (Task 2 keeps header + tail)
- ✅ Live verification (Task 3)

**Placeholder scan:** No TBD/TODO. All code blocks complete. The one conditional ("if a pre-existing test seeds under a seller not in the dict, adjust it") is a concrete instruction with the exact reason and fix, not a vague placeholder.

**Type consistency:** `get_unenriched_active_item_ids_for_seller(conn, seller_id) -> set[str]` defined in Task 1, imported and called in Task 2. Returns a set, unioned with `outcome.new_item_ids` (also a set). `outcome.new_item_ids`, `outcome.disappeared_item_ids`, `outcome.price_changes` match the existing `SellerScanOutcome` dataclass fields. `ItemStatus.DELETED/SOLD_OUT`, `fetch_for_enrich`, `apply_enrich`, `fetch_for_classification`, `apply_classification` all exist in the current monitor.py.

**Behavior-change note (intended):** Stranded recovery is now scoped to sellers in the current run's `sellers` dict, not a global sweep. For full runs (all 482 sellers) this is identical. For partial runs (`--limit-sellers`), a stranded item under a not-included seller waits until that seller is next processed — which is the correct, more predictable semantics for seller-unit processing.
