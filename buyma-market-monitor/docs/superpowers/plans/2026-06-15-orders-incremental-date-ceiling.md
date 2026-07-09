# 주문 증분 "패턴 + 날짜 상한" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 워터마크 패턴이 빗나가도 과거 주문을 재수집해 중복을 만들지 않도록, 날짜 상한(prev_max) 가드와 경계날 개수대조를 추가한다.

**Architecture:** 워터마크 패턴 매칭은 주(主) 경계 탐지로 유지한다. 크롤러는 패턴 미스 시 출력 주문을 `sale_date >= prev_max`로 제한하고 `overcollected=True`를 반환하며, 워터마크 날짜 구간(`wm_min`)까지만 스캔하도록 조기 종료한다. 저장 레이어는 `overcollected`일 때 경계날(`== prev_max`)만 DB 기존 개수와 대조해 부족분만 삽입한다. 스키마 변경은 없다(`prev_max`/`wm_min`은 워터마크에서 도출).

**Tech Stack:** Python 3, sqlite3, pytest 8.3.3, BeautifulSoup(기존 파서).

**Spec:** [docs/superpowers/specs/2026-06-15-orders-incremental-date-ceiling-design.md](../specs/2026-06-15-orders-incremental-date-ceiling-design.md)

---

## File Structure

- `storage/orders_repo.py` (수정) — `insert_orders_bounded` 헬퍼 추가(경계날 개수대조).
- `crawler/orders.py` (수정) — `crawl_seller_orders`에 조기종료 + 폴백 상한 + `overcollected` 반환; worker 언패킹 + `on_seller_done` 콜백에 플래그 전달; `crawl_all_orders_with_factory` 타입힌트.
- `main.py` (수정) — `on_seller_done`이 `overcollected`를 받아 `insert_orders_bounded` 호출.
- `tests/test_orders_repo.py` (수정) — `insert_orders_bounded` 테스트.
- `tests/test_orders_watermark.py` (수정) — 5-튜플 반영 + 조기종료/폴백상한/플래그 테스트.

**작업 순서 주의:** Task 2가 `crawl_seller_orders` 반환을 5-튜플로 바꾸므로, 같은 Task에서 호출부(worker, main의 `on_seller_done` 시그니처)를 함께 갱신해 스위트를 녹색으로 유지한다. Task 2에서 main은 플래그를 **받기만** 하고, 실제 경계대조는 Task 3에서 소비한다.

---

## Task 1: `insert_orders_bounded` 헬퍼

**Files:**
- Modify: `storage/orders_repo.py`
- Test: `tests/test_orders_repo.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_orders_repo.py` 끝에 추가

```python
def _order(seller_id, item_id, sale_date, qty=1):
    return {
        "seller_id": seller_id, "item_id": item_id, "item_name": "n",
        "item_url": "u", "qty": qty, "sale_date": sale_date,
        "collected_at": "2026-06-15T10:00:00+09:00",
    }


def test_insert_orders_bounded_not_overcollected_inserts_all(tmp_path: Path):
    conn = make_conn(tmp_path)
    n = orders_repo.insert_orders_bounded(
        conn, "S1",
        [_order("S1", "1", "2026/06/14"), _order("S1", "2", "2026/06/13")],
        prev_max="2026/06/14", overcollected=False,
    )
    assert n == 2
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2


def test_insert_orders_bounded_inserts_all_above_prev_max(tmp_path: Path):
    conn = make_conn(tmp_path)
    n = orders_repo.insert_orders_bounded(
        conn, "S1",
        [_order("S1", "1", "2026/06/15"), _order("S1", "2", "2026/06/14")],
        prev_max="2026/06/13", overcollected=True,
    )
    assert n == 2  # both strictly above prev_max -> all inserted
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2


def test_insert_orders_bounded_reconciles_boundary_day(tmp_path: Path):
    conn = make_conn(tmp_path)
    # DB already has 5 orders on the boundary date for S1
    orders_repo.insert_orders(conn, [_order("S1", str(i), "2026/06/13") for i in range(5)])
    # This run re-collected 7 on the boundary date + 1 newer
    new = [_order("S1", str(100 + i), "2026/06/13") for i in range(7)]
    new.append(_order("S1", "200", "2026/06/14"))
    n = orders_repo.insert_orders_bounded(conn, "S1", new, prev_max="2026/06/13", overcollected=True)
    # boundary: max(0, 7-5)=2 inserted; above: 1 inserted -> 3 total
    assert n == 3
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE sale_date='2026/06/13'").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE sale_date='2026/06/14'").fetchone()[0] == 1


def test_insert_orders_bounded_boundary_already_complete_inserts_zero(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.insert_orders(conn, [_order("S1", str(i), "2026/06/13") for i in range(5)])
    new = [_order("S1", str(100 + i), "2026/06/13") for i in range(3)]  # fewer than existing
    n = orders_repo.insert_orders_bounded(conn, "S1", new, prev_max="2026/06/13", overcollected=True)
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE sale_date='2026/06/13'").fetchone()[0] == 5
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_orders_repo.py -k bounded -v`
Expected: FAIL (`AttributeError: ... 'insert_orders_bounded'`)

- [ ] **Step 3: 구현** — `storage/orders_repo.py`에 추가 (기존 `insert_orders` 아래)

```python
def insert_orders_bounded(
    conn: sqlite3.Connection,
    seller_id: str,
    orders: list[dict],
    prev_max: str | None,
    overcollected: bool,
) -> int:
    """Insert new orders, returning the number of rows inserted.

    Normal case (overcollected=False): insert everything (watermark already
    bounded the increment). Over-collection case: the crawler fell back to the
    date ceiling, so reconcile the boundary date (sale_date == prev_max) against
    existing DB rows to avoid re-inserting already-collected same-day orders.
    Orders strictly above prev_max are always genuinely new.
    """
    if not overcollected or prev_max is None:
        insert_orders(conn, orders)
        return len(orders)

    above = [o for o in orders if o["sale_date"] > prev_max]
    boundary = [o for o in orders if o["sale_date"] == prev_max]

    to_insert = list(above)
    if boundary:
        existing = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE seller_id = ? AND sale_date = ?",
            (seller_id, prev_max),
        ).fetchone()[0]
        surplus = max(0, len(boundary) - existing)
        to_insert.extend(boundary[:surplus])

    insert_orders(conn, to_insert)
    return len(to_insert)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_orders_repo.py -v`
Expected: PASS (모든 기존 + 신규)

- [ ] **Step 5: 커밋**

```bash
git add storage/orders_repo.py tests/test_orders_repo.py
git commit -m "feat(orders_repo): insert_orders_bounded with boundary-date reconciliation"
```

---

## Task 2: `crawl_seller_orders` — 조기종료 + 폴백 상한 + overcollected 플래그

**Files:**
- Modify: `crawler/orders.py` (`crawl_seller_orders`, worker 언패킹/콜백, `crawl_all_orders_with_factory` 타입힌트)
- Modify: `main.py` (`on_seller_done` 시그니처에 `overcollected` 파라미터 추가 — 이 Task에선 받기만 함)
- Test: `tests/test_orders_watermark.py`

- [ ] **Step 1: 테스트 갱신/추가** — `tests/test_orders_watermark.py`를 아래 전체 내용으로 교체

```python
"""Watermark over-collection guard behavior in crawl_seller_orders."""
import crawler.orders as om
from crawler.orders import crawl_seller_orders, OrderEntry


def _e(sale_date, item_id, qty=1):
    return OrderEntry(
        sale_date=sale_date, item_id=item_id, qty=qty,
        item_name=f"item {item_id}", item_url="",
    )


class FakeClient:
    """Returns a trivial response per URL; parse_sales_page is monkeypatched."""
    def get(self, url):
        class R:
            text = url
            status_code = 200
        return R()


def _patch_pages(monkeypatch, pages):
    """Make parse_sales_page yield each page's entries in order."""
    seq = iter(pages)
    monkeypatch.setattr(om, "parse_sales_page", lambda html: next(seq))


def test_pattern_match_returns_increment_not_overcollected(monkeypatch):
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1")],   # genuinely new
        [_e("2026/06/13", "2")],   # watermark boundary
    ])
    watermark = [("2026/06/13", "2", 1)]
    new_orders, _wm, warnings, _pages, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S3", watermark=watermark, max_pages=100,
    )
    assert [e.item_id for e in new_orders] == ["1"]
    assert overcollected is False
    assert warnings == []


def test_fallback_bounds_output_to_prev_max(monkeypatch):
    # Watermark never matches. prev_max = 2026/06/12 (max of watermark dates).
    # Page entries go below prev_max; only >= prev_max must be returned.
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1"), _e("2026/06/13", "2")],
        [_e("2026/06/12", "3"), _e("2026/06/11", "4")],  # 06/11 < prev_max -> excluded + early stop
    ])
    watermark = [("2026/06/12", "99", 1), ("2026/06/10", "98", 1)]  # prev_max=06/12, wm_min=06/10
    new_orders, _wm, warnings, _pages, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S1", watermark=watermark, max_pages=100,
    )
    assert overcollected is True
    # 06/14, 06/13, 06/12 kept; 06/11 dropped
    assert sorted(e.sale_date for e in new_orders) == ["2026/06/12", "2026/06/13", "2026/06/14"]
    assert any("watermark not matched" in w for w in warnings)


def test_fallback_early_stops_at_wm_min(monkeypatch):
    # After page 2 the oldest accumulated date (06/09) < wm_min (06/10): must stop,
    # never fetching page 3. If it fetched page 3, next(seq) would raise StopIteration.
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1")],
        [_e("2026/06/09", "2")],   # 06/09 < wm_min 06/10 -> early stop here
        # no page 3 provided on purpose
    ])
    watermark = [("2026/06/12", "99", 1), ("2026/06/10", "98", 1)]  # prev_max=06/12, wm_min=06/10
    new_orders, _wm, warnings, pages_scanned, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S2", watermark=watermark, max_pages=100,
    )
    assert pages_scanned == 2
    assert overcollected is True
    assert [e.sale_date for e in new_orders] == ["2026/06/14"]  # only >= prev_max(06/12)


def test_empty_watermark_full_scan_not_overcollected(monkeypatch):
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1")],
        [],
    ])
    new_orders, _wm, warnings, _pages, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S4", watermark=[], max_pages=100,
    )
    assert len(new_orders) == 1
    assert overcollected is False
    assert warnings == []
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_orders_watermark.py -v`
Expected: FAIL (반환이 4-튜플이라 5개 언패킹 실패 / overcollected 미존재)

- [ ] **Step 3: `crawl_seller_orders` 교체** — `crawler/orders.py`의 함수 전체(`def crawl_seller_orders` ~ 그 `return ...`)를 아래로 교체

```python
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
```

- [ ] **Step 4: worker 언패킹/콜백 갱신** — `crawler/orders.py`의 worker 내부

기존:
```python
                        new_orders, new_watermark, warnings, pages_scanned = crawl_seller_orders(
                            client, sid, watermark, max_pages=max_pages,
                        )
                        on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings)
```
교체:
```python
                        new_orders, new_watermark, warnings, pages_scanned, overcollected = crawl_seller_orders(
                            client, sid, watermark, max_pages=max_pages,
                        )
                        on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings, overcollected)
```

- [ ] **Step 5: 콜백 타입힌트/문서 갱신** — `crawl_all_orders_with_factory` 시그니처의 `on_seller_done` 타입힌트

기존:
```python
    on_seller_done: Callable[[str, list[OrderEntry], list[WatermarkTuple], int, list[str]], None],
```
교체:
```python
    on_seller_done: Callable[[str, list[OrderEntry], list[WatermarkTuple], int, list[str], bool], None],
```
그리고 그 docstring의 `on_seller_done(seller_id, new_orders, new_watermark, pages_scanned, warnings)` 줄을 `on_seller_done(seller_id, new_orders, new_watermark, pages_scanned, warnings, overcollected)`로 수정.

- [ ] **Step 6: main.py `on_seller_done`이 인자 받도록 갱신** — `main.py`

기존:
```python
    def on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings):
```
교체(파라미터만 추가, 본문은 이 Task에선 변경하지 않음):
```python
    def on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings, overcollected):
```

- [ ] **Step 7: 테스트 + 스모크**

Run: `python -m pytest tests/test_orders_watermark.py -v`
Expected: PASS (4개)
Run: `python -c "import main" && python -m pytest -q`
Expected: import 성공, 전체 PASS

- [ ] **Step 8: 커밋**

```bash
git add crawler/orders.py main.py tests/test_orders_watermark.py
git commit -m "feat(orders): date-ceiling guard + early stop + overcollected flag in crawl_seller_orders"
```

---

## Task 3: `main.py` — 폴백 시 경계날 개수대조 적용

**Files:**
- Modify: `main.py` (`on_seller_done` 본문)

- [ ] **Step 1: `on_seller_done` 본문 교체** — `main.py`

`on_seller_done` 함수 본문에서, 주문 삽입 블록을 `insert_orders_bounded` 호출로 바꾼다.

기존:
```python
    def on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings, overcollected):
        if not args.dry_run:
            with db_lock:
                if new_orders:
                    order_dicts = []
                    for e in new_orders:
                        d = asdict(e)
                        d["seller_id"] = sid
                        d["collected_at"] = timestamp
                        order_dicts.append(d)
                    orders_repo.insert_orders(conn, order_dicts)
                orders_repo.upsert_watermark(
                    conn, sid,
                    signature=[list(t) for t in new_watermark],
                    last_run_at=timestamp,
                    pages_scanned=pages_scanned,
                    orders_added=len(new_orders),
                )
```
교체:
```python
    def on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings, overcollected):
        if not args.dry_run:
            prev_max = max((t[0] for t in watermarks.get(sid, [])), default=None)
            with db_lock:
                if new_orders:
                    order_dicts = []
                    for e in new_orders:
                        d = asdict(e)
                        d["seller_id"] = sid
                        d["collected_at"] = timestamp
                        order_dicts.append(d)
                    orders_repo.insert_orders_bounded(
                        conn, sid, order_dicts, prev_max, overcollected
                    )
                orders_repo.upsert_watermark(
                    conn, sid,
                    signature=[list(t) for t in new_watermark],
                    last_run_at=timestamp,
                    pages_scanned=pages_scanned,
                    orders_added=len(new_orders),
                )
```
(나머지 본문 — warnings 로깅, `logging.info("seller %s: ...")` — 은 그대로 둔다.)

주의: `watermarks`는 `run_crawl_orders` 안에서 이미 만들어진 `dict[str, list]`(셀러별 튜플 워터마크)이며 `on_seller_done`의 클로저로 접근 가능하다. 신규/`--full-rescan` 셀러는 `watermarks[sid]==[]` → `prev_max=None` → `insert_orders_bounded`가 전량 삽입(overcollected도 False).

- [ ] **Step 2: 스모크 + 전체 테스트**

Run: `python -c "import main" && python -m pytest -q`
Expected: import 성공, 전체 PASS

- [ ] **Step 3: 커밋**

```bash
git add main.py
git commit -m "feat(main): apply boundary-date reconciliation on watermark-miss over-collection"
```

---

## Self-Review 결과

- **Spec coverage:** 조기종료(wm_min)·폴백상한(prev_max)·overcollected 플래그(Task 2) / 경계날 개수대조(Task 1 헬퍼 + Task 3 호출) / 빈 워터마크 전체수집(Task 2 test) / 스키마 무변경(전 Task) — 모두 태스크 존재.
- **Type 일관성:** `crawl_seller_orders` → 5-튜플 `(new_orders, new_watermark, warnings, pages_scanned, overcollected)` 정의(Task2)와 worker 언패킹(Task2)·test(Task2) 일치. `insert_orders_bounded(conn, seller_id, orders, prev_max, overcollected) -> int` 정의(Task1)와 호출(Task3) 일치. `on_seller_done` 6-인자 정의(Task2)·호출(Task2)·소비(Task3) 일치.
- **Placeholder:** 없음.
- **날짜 비교:** 전부 `YYYY/MM/DD` 문자열 사전식 비교(고정폭이라 시간순과 동일).
