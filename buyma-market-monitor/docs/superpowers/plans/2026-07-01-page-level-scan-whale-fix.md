# 1단계: 페이지 단위 목록 스캔 (고래 수정) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 셀러 상품 목록 스캔을 `(셀러, 페이지)` 단위 글로벌 큐로 전환해 고래 셀러 페이지를 워커 전체에 분산하고, enrich는 분리(재방문 전담), 사라진 상품만 아이템 단위로 품절/삭제 판정한다.

**Architecture:** 신규 `crawler/page_scan.py::run_page_scan`. Phase A: 워커들이 (seller,page) 태스크를 글로벌 큐에서 pull → 파싱 → 셀러별 누적 → 셀러의 모든 페이지 완료 시 `apply_seller_scan_to_db`로 reconcile(신규 upsert/가격이력/사라짐 집계). Phase B: 사라진 후보를 아이템 단위 큐로 품절/삭제 판정. enrich 없음(신규는 detail_fetched_at NULL로 남아 재방문이 흡수).

**Tech Stack:** Python 3.14, SQLite, httpx, BeautifulSoup4, pytest. 기존 `crawler/seller_items.py`, `crawler/monitor.py`, `storage/items_repo.py` 재사용.

---

## 배경 (구현자가 알아야 할 사실)

- `crawler/seller_items.py`: `build_seller_items_url(sid, page)`, `parse_seller_items(html) -> [{item_id,name,price}]`, `parse_seller_items_max_page(html) -> int`(page1에서 최대 페이지 파악).
- `crawler/monitor.py::apply_seller_scan_to_db(conn, seller_id, scanned_items, now) -> SellerScanOutcome` — 한 셀러의 스캔 결과를 단일 트랜잭션으로 reconcile: 신규 upsert, 가격변동 시 price_history, `disappeared_item_ids = 이전ACTIVE − 스캔됨`. **빈 스캔 가드 내장**: scanned_items가 비면 `skipped_due_to_empty_scan=True`로 트랜잭션 없이 반환(ACTIVE를 사라짐으로 잘못 마킹 방지). Outcome 필드: `new_item_ids:set`, `resurrected_item_ids:set`, `disappeared_item_ids:set`, `price_changes:int`, `skipped_due_to_empty_scan:bool`. **enrich는 하지 않음** — 그게 이 설계의 핵심(재방문이 전담).
- `crawler/monitor.py`: `fetch_for_classification(client, item_id, on_error) -> (status_code, body) | None`(client.get_allowing_4xx 사용, 실패 시 on_error 후 None), `apply_classification(conn, item_id, status_code, now) -> ItemStatus`(404/410→DELETED, 200→SOLD_OUT).
- `crawler/item_detail.py::build_item_detail_url(item_id)`.
- `crawler/item_status.py::ItemStatus`(DELETED, SOLD_OUT).
- `storage/store.py::now_iso()`. `storage/db.py::connect, init_schema`.
- 동시성 규칙: HTTP fetch는 db_lock 밖, DB 쓰기는 db_lock 안.
- 테스트는 인메모리 SQLite + 가짜 client + **파서 monkeypatch**로 한다. `run_page_scan`은 `parse_seller_items`/`parse_seller_items_max_page`를 `crawler.page_scan` 네임스페이스로 임포트하므로 테스트가 `monkeypatch.setattr(page_scan, "parse_seller_items", ...)`로 대체할 수 있다.
- 리포 커밋은 `main` 브랜치.

## File Structure

| 파일 | 책임 |
|---|---|
| `crawler/page_scan.py` (신규) | `ScanSummary`, `run_page_scan` (Phase A 스캔+reconcile, Phase B 사라짐 판정) |
| `scan_cli.py` (신규) | 단독 실행 진입점 (오케스트레이터가 함수 호출; CLI는 단독 테스트/스모크용) |
| `tests/test_page_scan.py` (신규) | 단위/통합 테스트 |

---

## Task 1: run_page_scan Phase A (페이지 단위 스캔 + reconcile)

**Files:**
- Create: `crawler/page_scan.py`
- Test: `tests/test_page_scan.py`

Phase A만 구현: 페이지 큐 분산, 셀러별 누적, 완료 시 reconcile, 시간예산/CB 가드. 사라진 후보는 `summary.disappeared`로 집계하되 이 태스크에선 판정(Phase B) 미구현.

- [ ] **Step 1: Write the failing tests**

`tests/test_page_scan.py` 생성:

```python
import threading
from storage.db import connect, init_schema
from storage.items_repo import upsert_scanned_item, get_active_item_ids_for_seller
import crawler.page_scan as page_scan
from crawler.page_scan import run_page_scan, ScanSummary


# --- fake client: resp.text encodes "sid:page"; parsers are monkeypatched ---
class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    def get(self, url):
        # url like https://www.buyma.com/buyer/{sid}/item_{n}.html
        import re
        m = re.search(r"/buyer/([^/]+)/item_(\d+)\.html", url)
        sid, page = m.group(1), int(m.group(2))
        return _Resp(f"{sid}:{page}")

    def close(self):
        pass


def _install_fake_parsers(monkeypatch, pages, maxpages):
    """pages: {(sid,page): [item dicts]}, maxpages: {sid: int}."""
    def fake_parse_items(text):
        sid, page = text.split(":")
        return list(pages.get((sid, int(page)), []))
    def fake_parse_max(text):
        sid, page = text.split(":")
        return maxpages[sid]
    monkeypatch.setattr(page_scan, "parse_seller_items", fake_parse_items)
    monkeypatch.setattr(page_scan, "parse_seller_items_max_page", fake_parse_max)


def _item(iid, price=100, name="n"):
    return {"item_id": iid, "name": name, "price": price}


def test_scan_distributes_pages_and_reconciles(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    conn = connect(str(db)); init_schema(conn); conn.close()
    # whale seller W has 3 pages (2 items each), small seller S has 1 page.
    pages = {
        ("W", 1): [_item("w1"), _item("w2")],
        ("W", 2): [_item("w3"), _item("w4")],
        ("W", 3): [_item("w5")],
        ("S", 1): [_item("s1")],
    }
    maxpages = {"W": 3, "S": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)

    summary = run_page_scan(
        db_path=str(db), sellers=["W", "S"],
        client_factory=lambda: _FakeClient(), num_workers=4,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None)

    assert isinstance(summary, ScanSummary)
    assert summary.sellers_scanned == 2
    assert summary.items_new == 6          # w1..w5 + s1
    conn = connect(str(db))
    assert get_active_item_ids_for_seller(conn, "W") == {"w1", "w2", "w3", "w4", "w5"}
    assert get_active_item_ids_for_seller(conn, "S") == {"s1"}
    # detail NOT fetched (enrich delegated to revisit)
    assert conn.execute(
        "SELECT COUNT(*) FROM items WHERE detail_fetched_at IS NOT NULL").fetchone()[0] == 0


def test_scan_detects_disappeared_and_price_change(tmp_path, monkeypatch):
    db = tmp_path / "s2.db"
    conn = connect(str(db)); init_schema(conn)
    now0 = "2026-06-01T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="keep", seller_id="W", name="n", price=100, now=now0)
    upsert_scanned_item(conn, item_id="gone", seller_id="W", name="n", price=100, now=now0)
    conn.close()
    # this scan: keep still present (price changed 100->150), gone missing, new1 added
    pages = {("W", 1): [_item("keep", price=150), _item("new1")]}
    maxpages = {"W": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)

    summary = run_page_scan(
        db_path=str(db), sellers=["W"],
        client_factory=lambda: _FakeClient(), num_workers=2,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None)

    assert summary.items_new == 1          # new1
    assert summary.price_changes == 1      # keep 100->150
    assert summary.disappeared == 1        # gone
    conn = connect(str(db))
    # price history recorded for keep
    assert conn.execute(
        "SELECT COUNT(*) FROM price_history WHERE item_id='keep'").fetchone()[0] >= 1


def test_scan_respects_deadline(tmp_path, monkeypatch):
    db = tmp_path / "s3.db"
    conn = connect(str(db)); init_schema(conn); conn.close()
    pages = {("S", 1): [_item("s1")]}
    maxpages = {"S": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)
    summary = run_page_scan(
        db_path=str(db), sellers=["S"],
        client_factory=lambda: _FakeClient(), num_workers=1,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None,
        max_hours=0.0)   # already past deadline
    assert summary.sellers_scanned == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_page_scan.py -v`
Expected: FAIL (ModuleNotFoundError: crawler.page_scan)

- [ ] **Step 3: Implement Phase A**

`crawler/page_scan.py` 생성:

```python
"""Page-level parallel seller-listing scan (whale-seller fix).

Distributes (seller, page) tasks across a global queue so one whale seller's
hundreds of pages spread across all workers. Reconciles per seller once all its
pages are scanned (reusing apply_seller_scan_to_db). Detail enrichment is NOT
done here — new items stay detail_fetched_at IS NULL for the revisit scheduler.
Disappeared items are classified (SOLD_OUT/DELETED) in Phase B (Task 2).
"""
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from crawler.seller_items import (
    build_seller_items_url, parse_seller_items, parse_seller_items_max_page,
)
from crawler.monitor import apply_seller_scan_to_db
from storage.db import connect, init_schema


@dataclass
class ScanSummary:
    sellers_scanned: int = 0
    items_new: int = 0
    price_changes: int = 0
    disappeared: int = 0
    deleted: int = 0
    sold_out: int = 0
    errors: int = 0


def run_page_scan(
    db_path: Path | str,
    sellers,
    client_factory: Callable[[], object],
    num_workers: int,
    now: str,
    on_error: Callable[..., None],
    max_hours: float | None = None,
    circuit_breaker=None,
    stop_event=None,
) -> ScanSummary:
    """Phase A: scan all sellers' listing pages via a global (seller,page) queue,
    reconcile each seller once its pages are all scanned. No enrichment.
    Disappeared candidates are collected into summary.disappeared (Phase B classifies)."""
    db_path = Path(db_path)
    summary = ScanSummary()
    main_conn = connect(db_path)
    init_schema(main_conn)

    db_lock = threading.Lock()
    state_lock = threading.Lock()
    counts_lock = threading.Lock()

    deadline = None if max_hours is None else time.monotonic() + max_hours * 3600.0

    def _halt() -> bool:
        if deadline is not None and time.monotonic() >= deadline:
            return True
        if circuit_breaker is not None and circuit_breaker.is_open():
            return True
        if stop_event is not None and stop_event.is_set():
            return True
        return False

    seller_ids = list(sellers)
    buf: dict[str, list] = {sid: [] for sid in seller_ids}
    total: dict[str, int | None] = {sid: None for sid in seller_ids}
    done: dict[str, int] = {sid: 0 for sid in seller_ids}
    reconciled: set[str] = set()
    disappeared_ids: list[str] = []

    page_q: queue.Queue = queue.Queue()
    for sid in seller_ids:
        page_q.put((sid, 1))

    def _reconcile(sid: str) -> None:
        items = buf[sid]
        with db_lock:
            outcome = apply_seller_scan_to_db(main_conn, sid, items, now)
        with counts_lock:
            if not outcome.skipped_due_to_empty_scan:
                summary.sellers_scanned += 1
                summary.items_new += len(outcome.new_item_ids)
                summary.price_changes += outcome.price_changes
        if outcome.disappeared_item_ids:
            with state_lock:
                disappeared_ids.extend(outcome.disappeared_item_ids)

    def _after_page(sid, page, failed, items=None, max_pages=None) -> None:
        to_reconcile = False
        with state_lock:
            if page == 1:
                if failed:
                    # empty-scan guard: page1 failed → cannot scan this seller.
                    # claim as reconciled to skip; do NOT touch its ACTIVE items.
                    total[sid] = 0
                    reconciled.add(sid)
                    return
                total[sid] = max_pages
                buf[sid].extend(items or [])
                done[sid] += 1
                for n in range(2, (max_pages or 1) + 1):
                    page_q.put((sid, n))
            else:
                if not failed and items:
                    buf[sid].extend(items)
                done[sid] += 1
            if total[sid] is not None and done[sid] >= total[sid] and sid not in reconciled:
                reconciled.add(sid)
                to_reconcile = True
        if to_reconcile:
            _reconcile(sid)

    def scan_worker() -> None:
        client = client_factory()
        try:
            while not _halt():
                try:
                    sid, page = page_q.get_nowait()
                except queue.Empty:
                    return
                try:
                    url = build_seller_items_url(sid, page)
                    try:
                        resp = client.get(url)
                    except Exception as e:
                        on_error(stage="seller_items", url=url,
                                 status=getattr(e, "last_status", None), reason=repr(e))
                        with counts_lock:
                            summary.errors += 1
                        _after_page(sid, page, failed=True)
                        continue
                    items = parse_seller_items(resp.text)
                    max_pages = parse_seller_items_max_page(resp.text) if page == 1 else None
                    _after_page(sid, page, failed=False, items=items, max_pages=max_pages)
                finally:
                    page_q.task_done()
        finally:
            if hasattr(client, "close"):
                client.close()

    try:
        threads = [threading.Thread(target=scan_worker, daemon=True) for _ in range(num_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with counts_lock:
            summary.disappeared = len(disappeared_ids)
    finally:
        main_conn.close()
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_page_scan.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add crawler/page_scan.py tests/test_page_scan.py
git commit -m "feat(scan): page-level parallel seller scan (Phase A) — whale fix"
```

---

## Task 2: Phase B — 사라진 상품 품절/삭제 판정

**Files:**
- Modify: `crawler/page_scan.py`
- Test: `tests/test_page_scan.py`

Phase A가 모은 disappeared 후보를 아이템 단위 큐로 처리해 품절/삭제 판정.

- [ ] **Step 1: Write the failing test**

`tests/test_page_scan.py`에 추가:

```python
class _ClassifyClient:
    """Listing pages via .get; detail status via .get_allowing_4xx.
    Item 'gone' → 404 (DELETED); item 'soldout' → 200 (SOLD_OUT)."""
    def get(self, url):
        import re
        m = re.search(r"/buyer/([^/]+)/item_(\d+)\.html", url)
        if m:
            sid, page = m.group(1), int(m.group(2))
            return _Resp(f"{sid}:{page}")
        return _Resp("")

    def get_allowing_4xx(self, url):
        import re
        m = re.search(r"/item/([^/]+)/", url)
        iid = m.group(1)
        code = 404 if iid == "gone" else 200
        return _Resp("") if False else _RespStatus("", code)

    def close(self):
        pass


class _RespStatus:
    def __init__(self, text, status_code):
        self.text = text
        self.status_code = status_code


def test_phase_b_classifies_disappeared(tmp_path, monkeypatch):
    db = tmp_path / "s4.db"
    conn = connect(str(db)); init_schema(conn)
    now0 = "2026-06-01T00:00:00+09:00"
    for iid in ("keep", "gone", "soldout"):
        upsert_scanned_item(conn, item_id=iid, seller_id="W", name="n", price=100, now=now0)
    conn.close()
    # scan: only 'keep' remains → 'gone' + 'soldout' disappeared
    pages = {("W", 1): [_item("keep")]}
    maxpages = {"W": 1}
    _install_fake_parsers(monkeypatch, pages, maxpages)

    summary = run_page_scan(
        db_path=str(db), sellers=["W"],
        client_factory=lambda: _ClassifyClient(), num_workers=2,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None)

    assert summary.disappeared == 2
    assert summary.deleted == 1
    assert summary.sold_out == 1
    conn = connect(str(db))
    st = dict(conn.execute("SELECT item_id, status FROM items WHERE item_id IN ('gone','soldout')"))
    assert st["gone"] == "DELETED"
    assert st["soldout"] == "SOLD_OUT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_page_scan.py::test_phase_b_classifies_disappeared -v`
Expected: FAIL (deleted/sold_out == 0, statuses unchanged)

- [ ] **Step 3: Implement Phase B**

`crawler/page_scan.py` 상단 import에 추가:
```python
from crawler.monitor import (
    apply_seller_scan_to_db, fetch_for_classification, apply_classification,
)
from crawler.item_status import ItemStatus
from crawler.item_detail import build_item_detail_url
```
(기존 `from crawler.monitor import apply_seller_scan_to_db` 줄은 위 3-임포트로 대체.)

`run_page_scan`에서 scan workers `join()` 직후, `summary.disappeared` 설정 다음, `finally` 앞에 Phase B를 추가:
```python
        with counts_lock:
            summary.disappeared = len(disappeared_ids)

        # Phase B: classify disappeared items (item-level queue; whale-agnostic)
        class_q: queue.Queue = queue.Queue()
        for iid in disappeared_ids:
            class_q.put(iid)

        def classify_worker() -> None:
            client = client_factory()
            try:
                while not _halt():
                    try:
                        iid = class_q.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        fetched = fetch_for_classification(client, iid, on_error)
                        if fetched is not None:
                            status_code, _ = fetched
                            try:
                                with db_lock:
                                    status = apply_classification(main_conn, iid, status_code, now)
                                with counts_lock:
                                    if status is ItemStatus.DELETED:
                                        summary.deleted += 1
                                    elif status is ItemStatus.SOLD_OUT:
                                        summary.sold_out += 1
                            except Exception as e:
                                on_error(stage="classify", url=build_item_detail_url(iid),
                                         status=status_code, reason=repr(e))
                                with counts_lock:
                                    summary.errors += 1
                    finally:
                        class_q.task_done()
            finally:
                if hasattr(client, "close"):
                    client.close()

        cthreads = [threading.Thread(target=classify_worker, daemon=True) for _ in range(num_workers)]
        for t in cthreads:
            t.start()
        for t in cthreads:
            t.join()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_page_scan.py -v`
Expected: PASS (all, incl Phase B). Also full suite: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add crawler/page_scan.py tests/test_page_scan.py
git commit -m "feat(scan): Phase B classify disappeared (SOLD_OUT/DELETED)"
```

---

## Task 3: CLI 진입점 + 라이브 스모크

**Files:**
- Create: `scan_cli.py`
- Test: 스모크 (아래)

- [ ] **Step 1: Implement scan_cli.py**

`scan_cli.py` 생성:

```python
"""Page-level seller-listing scan CLI (whale-fixed ③a).

  python scan_cli.py --workers 6 --sleep 0.3 [--max-hours H] [--limit-sellers N]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from crawler.circuit_breaker import CircuitBreaker
from crawler.client import HttpClient
from crawler.page_scan import run_page_scan
from storage.db import connect, init_schema
from storage import sellers_repo
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


def main() -> int:
    p = argparse.ArgumentParser(description="BUYMA page-level seller scan (③a)")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--sleep", type=float, default=0.3)
    p.add_argument("--max-hours", type=float, default=None)
    p.add_argument("--limit-sellers", type=int, default=None)
    p.add_argument("--cb-threshold", type=int, default=5)
    p.add_argument("--cb-window-seconds", type=int, default=60)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    setup_logging(args.verbose)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

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

    cb = CircuitBreaker(threshold=args.cb_threshold, window_seconds=args.cb_window_seconds)

    def factory():
        return HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb)

    now = now_iso()
    logging.info("page-scan start %s (%d sellers, workers=%d, sleep=%.2f, max_hours=%s)",
                 now, len(seller_ids), args.workers, args.sleep, args.max_hours)
    summary = run_page_scan(
        db_path=DB_PATH, sellers=seller_ids, client_factory=factory,
        num_workers=args.workers, now=now, on_error=append_error_jsonl,
        max_hours=args.max_hours, circuit_breaker=cb)
    if cb.is_open():
        logging.warning("Circuit breaker tripped (%d blocks) — likely IP block.", len(cb._events))
    logging.info("Done: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke — help + tiny live run**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 scan_cli.py --help`
Expected: 옵션(--workers/--max-hours/--limit-sellers 등) 표시.

백업 후 소량 라이브(3 셀러):
```bash
cp data/items.db data/items.db.pre-pagescan-bak
PYTHONPATH="$PWD" .venv/bin/python3 scan_cli.py --workers 6 --sleep 0.3 --limit-sellers 3
```
Expected 로그: `Done: ScanSummary(sellers_scanned=3, items_new=..., price_changes=..., disappeared=..., deleted=.., sold_out=.., errors=..)`, 블로킹 없음. (신규 상품은 detail_fetched_at NULL로 남아 재방문이 흡수 — 정상.)

- [ ] **Step 3: Full test suite**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest -q`
Expected: 전체 PASS.

- [ ] **Step 4: Commit**

```bash
git add scan_cli.py
git commit -m "feat(scan): scan_cli entrypoint for page-level ③a"
```

---

## Self-Review (작성자 체크)

**Spec coverage:**
- (셀러,페이지) 글로벌 큐 분산 → Task 1 (page_q, _after_page)
- page1에서 max_pages 파악 후 2..N 등록 → Task 1 (_after_page page==1)
- 셀러 완료 시 reconcile (apply_seller_scan_to_db 재사용, enrich 없음) → Task 1 (_reconcile)
- 빈 스캔 가드(page1 실패 → 스킵) → Task 1 (_after_page failed guard) + apply_seller_scan_to_db 내장 가드
- 사라짐 판정(404→DELETED, 200→SOLD_OUT, 아이템 단위) → Task 2 (Phase B)
- 시간 예산/CB/stop 가드 → Task 1 (_halt, max_hours) 
- CLI/스모크 → Task 3

**Type consistency:** `ScanSummary` 필드(sellers_scanned/items_new/price_changes/disappeared/deleted/sold_out/errors)를 Task1/2에서 일관 사용. `apply_seller_scan_to_db` outcome 필드명(new_item_ids/disappeared_item_ids/price_changes/skipped_due_to_empty_scan)은 crawler/monitor.py 정의와 일치. `_halt` 가드 세 조건 일관.

**비범위:** 기존 run_monitor 유지(삭제 안 함), enrich 분리(재방문 전담), 오케스트레이터(2단계).
