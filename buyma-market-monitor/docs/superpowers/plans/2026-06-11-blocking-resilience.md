# Blocking Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop wasting requests (and worsening IP reputation) when BUYMA starts blocking us, by tripping a circuit breaker on repeated 403/429 responses and surfacing the "blocked" state distinctly from generic retry failures.

**Architecture:** A single `CircuitBreaker` instance is shared across all worker threads. Every 403 or 429 increments a sliding-window counter; once it crosses a configurable threshold the circuit latches OPEN for the rest of the run. `HttpClient` calls `circuit_breaker.assert_not_open()` before each request — if open, raises `CircuitOpenError` immediately with no network hit. A new `BlockedByServer` exception replaces the misleading "max retries exceeded" message for 403 responses (which never retried anyway). Workers exit cleanly when the circuit opens, so the run wraps up gracefully with accurate stats instead of hammering BUYMA further.

**Tech Stack:** Python `threading.Lock` for thread-safe state, `time.monotonic` (injectable for tests), existing httpx-based `HttpClient`. No new dependencies.

---

## File Structure

**Create:**
- `crawler/circuit_breaker.py` — `CircuitBreaker` class + `CircuitOpenError` exception
- `tests/test_circuit_breaker.py` — unit tests (sliding window, threshold latching, thread safety, injected clock)
- `tests/test_client_blocked.py` — HttpClient + CircuitBreaker integration tests for 403/429

**Modify:**
- `crawler/client.py` — add `BlockedByServer` exception, integrate `CircuitBreaker` into `HttpClient.__init__`/`get`/`get_allowing_4xx`, clarify `MaxRetriesExceeded` message
- `crawler/monitor.py` — `run_monitor` accepts optional `circuit_breaker`; workers check `cb.is_open()` between queue pulls and exit cleanly
- `tests/test_monitor.py` — integration test: CB opens on simulated 403 storm, workers exit, summary reflects accurate errors count
- `monitor_cli.py` — `--cb-threshold` and `--cb-window-seconds` flags; build single CB and inject into both factory and `run_monitor`

**Untouched (we are NOT changing):**
- Existing retry logic for 5xx (RETRY_STATUSES, RETRY_BACKOFFS)
- `seller_items_crawler.py` — error handling flow unchanged (BlockedByServer propagates through existing `on_error` path with `getattr(e, "last_status", None)`)
- `storage/`, `parse_*` modules — not touched

---

## Design Decisions (Locked)

### Circuit Breaker

- **Latching behavior:** Once tripped, stays tripped for the lifetime of the `CircuitBreaker` instance. No automatic reset, no half-open state. Operator restarts the run after investigating.
- **Trigger events:** Both 403 and 429 count as "blocks" and are recorded via `record_block()`. 5xx and network errors do NOT count (those are server problems, not blocking signals).
- **Default threshold/window:** 5 blocks in 60 seconds. Tunable via CLI.
- **Counter representation:** Sliding window via a list of monotonic timestamps. On each call, prune timestamps older than `now - window_seconds`, then check count.
- **Clock injection:** Constructor accepts `clock: Callable[[], float]` defaulting to `time.monotonic`. Tests pass a fake clock for deterministic behavior.

### BlockedByServer Exception

- Raised by `HttpClient.get()` and `HttpClient.get_allowing_4xx()` on any 403 response.
- Carries `url` and `last_status` (=403) attributes — matches `MaxRetriesExceeded` for `on_error` callback compatibility (`getattr(e, "last_status", None)` works for both).
- Different exception class so callers can distinguish if needed; `scan_seller_items` and the monitor workers don't need to special-case it — the existing `Exception` handler routes it to `on_error`.

### CircuitOpenError Exception

- Raised by `CircuitBreaker.assert_not_open()` when the circuit has tripped.
- Also raised proactively from `HttpClient.get*()` if the CB is already open at request start, so no HTTP call is wasted.
- Has the same `last_status = None` convention; reason is the blocking that tripped the breaker.

### Worker Behavior

- Both `scan_worker` and `enrich_worker` check `circuit_breaker.is_open()` at the top of each loop iteration (before `queue.get_nowait()`). If open, return immediately.
- This means a tripped circuit during Stage A causes remaining sellers to be skipped. Stage B (enrich) then starts with whatever `new_items`/`disappeared_items` were collected so far. Stage B's workers also check `is_open()` and exit immediately if still tripped.
- Workers that exit early do NOT count as errors (the errors are the 403s that tripped the CB, already counted).

### CLI

- `--cb-threshold` (int, default 5)
- `--cb-window-seconds` (int, default 60)
- A single `CircuitBreaker` is created and shared by every `HttpClient` (via factory closure) and passed to `run_monitor`.

---

# Tasks

### Task 1: CircuitBreaker module

**Files:**
- Create: `crawler/circuit_breaker.py`
- Test: `tests/test_circuit_breaker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_circuit_breaker.py`:

```python
import threading

import pytest

from crawler.circuit_breaker import CircuitBreaker, CircuitOpenError


class FakeClock:
    """Manually advanced monotonic clock for deterministic tests."""
    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_is_open_false_before_any_blocks():
    cb = CircuitBreaker(threshold=3, window_seconds=60, clock=FakeClock())
    assert cb.is_open() is False


def test_is_open_false_below_threshold():
    cb = CircuitBreaker(threshold=3, window_seconds=60, clock=FakeClock())
    cb.record_block()
    cb.record_block()
    assert cb.is_open() is False


def test_is_open_true_at_threshold():
    cb = CircuitBreaker(threshold=3, window_seconds=60, clock=FakeClock())
    cb.record_block()
    cb.record_block()
    cb.record_block()
    assert cb.is_open() is True


def test_circuit_latches_open_after_window_expires():
    """Once tripped, stays tripped even if old events would have aged out."""
    clock = FakeClock()
    cb = CircuitBreaker(threshold=2, window_seconds=10, clock=clock)
    cb.record_block()
    cb.record_block()
    assert cb.is_open() is True
    clock.advance(100)  # well past window
    assert cb.is_open() is True


def test_old_events_pruned_before_threshold_reached():
    """Blocks outside the window do not count toward the threshold."""
    clock = FakeClock()
    cb = CircuitBreaker(threshold=3, window_seconds=10, clock=clock)
    cb.record_block()
    clock.advance(11)  # first event ages out
    cb.record_block()
    cb.record_block()
    assert cb.is_open() is False  # only 2 events within window


def test_assert_not_open_raises_when_tripped():
    cb = CircuitBreaker(threshold=1, window_seconds=60, clock=FakeClock())
    cb.record_block()
    with pytest.raises(CircuitOpenError):
        cb.assert_not_open()


def test_assert_not_open_passes_when_closed():
    cb = CircuitBreaker(threshold=2, window_seconds=60, clock=FakeClock())
    cb.record_block()
    cb.assert_not_open()  # must not raise


def test_circuit_open_error_carries_last_status_none():
    """Compatible with on_error callback convention: getattr(e, 'last_status', None)."""
    cb = CircuitBreaker(threshold=1, window_seconds=60, clock=FakeClock())
    cb.record_block()
    try:
        cb.assert_not_open()
    except CircuitOpenError as e:
        assert getattr(e, "last_status", "missing") is None


def test_thread_safe_concurrent_record_block():
    """50 threads each call record_block once — final count of events must be 50."""
    cb = CircuitBreaker(threshold=10_000, window_seconds=60, clock=FakeClock())

    def worker():
        cb.record_block()

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Internal _events list should have exactly 50 entries
    assert len(cb._events) == 50
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_circuit_breaker.py -v
```

Expected: ImportError on `crawler.circuit_breaker`.

- [ ] **Step 3: Implement crawler/circuit_breaker.py**

Create `crawler/circuit_breaker.py`:

```python
"""Sliding-window circuit breaker for stopping HTTP fetches after repeated blocks.

Used to halt the monitoring run when BUYMA starts returning 403/429 — continuing
to hammer the server worsens our IP reputation and wastes the run's budget.

Latching design: once the threshold is reached within the window, the circuit
stays open until the process restarts. There is no half-open / probe state.
Operator restarts the run after investigating the cause.
"""
import threading
import time
from typing import Callable


class CircuitOpenError(Exception):
    """Raised when a request is attempted while the circuit is open."""

    def __init__(self, blocks: int, threshold: int, window_seconds: int):
        self.last_status: int | None = None
        super().__init__(
            f"circuit breaker open: {blocks} blocks in {window_seconds}s "
            f"window (threshold={threshold})"
        )


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 5,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: list[float] = []
        self._tripped = False
        self._lock = threading.Lock()

    def record_block(self) -> None:
        """Record a 403/429 observation. May trip the circuit."""
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            self._events = [t for t in self._events if t > cutoff]
            self._events.append(now)
            if len(self._events) >= self.threshold:
                self._tripped = True

    def is_open(self) -> bool:
        with self._lock:
            return self._tripped

    def assert_not_open(self) -> None:
        with self._lock:
            if self._tripped:
                raise CircuitOpenError(
                    blocks=len(self._events),
                    threshold=self.threshold,
                    window_seconds=self.window_seconds,
                )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_circuit_breaker.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add crawler/circuit_breaker.py tests/test_circuit_breaker.py
git commit -m "feat: add sliding-window circuit breaker for blocking detection"
```

---

### Task 2: BlockedByServer exception + 403 detection in HttpClient.get

**Files:**
- Modify: `crawler/client.py`
- Create: `tests/test_client_blocked.py`

This task adds a new `BlockedByServer` exception, integrates `CircuitBreaker` into `HttpClient`, and changes `get()` to:
1. Call `cb.assert_not_open()` at the start (if a CB is provided)
2. On HTTP 403: call `cb.record_block()` and raise `BlockedByServer` (no retry — 403 is permanent for our purposes)
3. On HTTP 429: call `cb.record_block()` then continue existing retry logic

Also clarify the `MaxRetriesExceeded` message: today it always says "max retries exceeded" even when no retries actually happened. Add an `attempts` field and adjust the message.

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_blocked.py`:

```python
import httpx
import pytest

from crawler.circuit_breaker import CircuitBreaker, CircuitOpenError
from crawler.client import HttpClient, BlockedByServer


def _transport(responses: list[int]):
    """Mock transport: returns canned status codes in order, then loops on the last."""
    state = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return httpx.Response(responses[i], text=f"status {responses[i]}")

    return httpx.MockTransport(handler)


def test_get_raises_blocked_by_server_on_403():
    client = HttpClient(transport=_transport([403]), sleep_seconds=0)
    try:
        with pytest.raises(BlockedByServer):
            client.get("https://example.test/")
    finally:
        client.close()


def test_get_records_block_in_circuit_breaker_on_403():
    cb = CircuitBreaker(threshold=10, window_seconds=60)
    client = HttpClient(transport=_transport([403]), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(BlockedByServer):
            client.get("https://example.test/")
        assert len(cb._events) == 1
    finally:
        client.close()


def test_get_records_block_in_circuit_breaker_on_429():
    cb = CircuitBreaker(threshold=10, window_seconds=60)
    # 429 will retry; provide enough responses for all attempts plus a final non-retry
    client = HttpClient(transport=_transport([429, 429, 429, 429]), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(Exception):  # MaxRetriesExceeded after retries
            client.get("https://example.test/")
        # Each attempt that saw 429 records a block
        assert len(cb._events) >= 1
    finally:
        client.close()


def test_get_short_circuits_when_breaker_open():
    """If CB is already open, get() must raise CircuitOpenError without hitting transport."""
    cb = CircuitBreaker(threshold=1, window_seconds=60)
    cb.record_block()
    assert cb.is_open()

    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="ok")

    client = HttpClient(transport=httpx.MockTransport(handler), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(CircuitOpenError):
            client.get("https://example.test/")
        assert calls["n"] == 0  # transport never invoked
    finally:
        client.close()


def test_get_passes_normally_with_closed_breaker():
    cb = CircuitBreaker(threshold=5, window_seconds=60)
    client = HttpClient(transport=_transport([200]), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        r = client.get("https://example.test/")
        assert r.status_code == 200
    finally:
        client.close()


def test_get_works_without_circuit_breaker():
    """Backward compat: CB is optional."""
    client = HttpClient(transport=_transport([200]), sleep_seconds=0)
    try:
        r = client.get("https://example.test/")
        assert r.status_code == 200
    finally:
        client.close()


def test_blocked_by_server_carries_last_status_403():
    """For on_error compatibility — callbacks read getattr(e, 'last_status', None)."""
    client = HttpClient(transport=_transport([403]), sleep_seconds=0)
    try:
        client.get("https://example.test/")
    except BlockedByServer as e:
        assert e.last_status == 403
        assert "blocked" in str(e).lower() or "403" in str(e)
    finally:
        client.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_client_blocked.py -v
```

Expected: ImportError on `BlockedByServer` from `crawler.client`.

- [ ] **Step 3: Modify crawler/client.py**

First, add the `BlockedByServer` exception class near the top of the file, right after `MaxRetriesExceeded`:

```python
class BlockedByServer(Exception):
    """Raised on HTTP 403 — distinguishes blocking from generic retry exhaustion."""

    def __init__(self, url: str, last_status: int = 403):
        self.url = url
        self.last_status = last_status
        super().__init__(f"blocked by server (status {last_status}): {url}")
```

Then update `HttpClient.__init__` to accept an optional `circuit_breaker`:

```python
class HttpClient:
    """For listing pages — static HTML, retry+sleep+UA."""

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        sleep_seconds: float = SLEEP_SECONDS,
        circuit_breaker=None,
    ):
        self._client = httpx.Client(
            http2=True,
            headers=DEFAULT_HEADERS,
            timeout=TIMEOUT,
            transport=transport,
            follow_redirects=True,
        )
        self._sleep_seconds = sleep_seconds
        self._cb = circuit_breaker
```

Replace the body of `HttpClient.get` with the new version that integrates the CB and raises `BlockedByServer` on 403:

```python
    def get(self, url: str) -> FetchResult:
        if self._cb is not None:
            self._cb.assert_not_open()

        last_status: int | None = None
        last_error: str | None = None

        for attempt in range(len(RETRY_BACKOFFS) + 1):
            try:
                response = self._client.get(url)
            except httpx.RequestError as e:
                last_error = repr(e)
                last_status = None
            else:
                last_status = response.status_code
                if response.status_code == 403:
                    if self._cb is not None:
                        self._cb.record_block()
                    self._sleep()
                    raise BlockedByServer(url, last_status=403)
                if response.status_code == 429 and self._cb is not None:
                    self._cb.record_block()
                if response.status_code not in RETRY_STATUSES:
                    self._sleep()
                    if response.status_code >= 400:
                        raise MaxRetriesExceeded(url, last_status, None)
                    return FetchResult(text=response.text, status_code=response.status_code)

            if attempt < len(RETRY_BACKOFFS):
                time.sleep(RETRY_BACKOFFS[attempt])

        self._sleep()
        raise MaxRetriesExceeded(url, last_status, last_error)
```

Key change: `403` is handled BEFORE the `not in RETRY_STATUSES` branch — it records the block and raises immediately. `429` records the block but continues to the existing retry logic (since 429 is in `RETRY_STATUSES`).

- [ ] **Step 4: Run new tests to verify they pass**

```bash
.venv/bin/pytest tests/test_client_blocked.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run all existing client tests to verify no regression**

```bash
.venv/bin/pytest tests/test_client_status_check.py -v
```

Expected: 4 passed (the existing test_client_status_check tests use 404, 200, 5xx — none use 403 except `test_get_strict_still_raises_on_404` which checks 404 → MaxRetriesExceeded. That should still work since 404 is not 403.)

- [ ] **Step 6: Commit**

```bash
git add crawler/client.py tests/test_client_blocked.py
git commit -m "feat: detect HTTP 403 as BlockedByServer, wire CircuitBreaker into HttpClient.get"
```

---

### Task 3: get_allowing_4xx — same blocking detection

**Files:**
- Modify: `crawler/client.py`
- Modify: `tests/test_client_blocked.py`

`get_allowing_4xx` returns 404/410 as data, but should still raise `BlockedByServer` on 403 and respect the circuit breaker.

- [ ] **Step 1: Add failing tests (append to tests/test_client_blocked.py)**

Append to `tests/test_client_blocked.py`:

```python
def test_get_allowing_4xx_raises_blocked_on_403():
    client = HttpClient(transport=_transport([403]), sleep_seconds=0)
    try:
        with pytest.raises(BlockedByServer):
            client.get_allowing_4xx("https://example.test/")
    finally:
        client.close()


def test_get_allowing_4xx_still_returns_404():
    """404 must still pass through as a normal FetchResult, not raise."""
    client = HttpClient(transport=_transport([404]), sleep_seconds=0)
    try:
        r = client.get_allowing_4xx("https://example.test/")
        assert r.status_code == 404
    finally:
        client.close()


def test_get_allowing_4xx_short_circuits_when_breaker_open():
    cb = CircuitBreaker(threshold=1, window_seconds=60)
    cb.record_block()

    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="x")

    client = HttpClient(transport=httpx.MockTransport(handler), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(CircuitOpenError):
            client.get_allowing_4xx("https://example.test/")
        assert calls["n"] == 0
    finally:
        client.close()


def test_get_allowing_4xx_records_block_on_403():
    cb = CircuitBreaker(threshold=10, window_seconds=60)
    client = HttpClient(transport=_transport([403]), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(BlockedByServer):
            client.get_allowing_4xx("https://example.test/")
        assert len(cb._events) == 1
    finally:
        client.close()
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
.venv/bin/pytest tests/test_client_blocked.py::test_get_allowing_4xx_raises_blocked_on_403 -v
```

Expected: FAIL — current `get_allowing_4xx` raises `MaxRetriesExceeded`, not `BlockedByServer`.

- [ ] **Step 3: Modify get_allowing_4xx in crawler/client.py**

Replace the body of `HttpClient.get_allowing_4xx` with:

```python
    def get_allowing_4xx(self, url: str) -> FetchResult:
        """Like get(), but returns the response for 404/410 instead of raising.

        Used by status classification: a 404 is meaningful data, not an error.
        Other 4xx (e.g. 403) still raise — 403 raises BlockedByServer specifically.
        5xx still retry/raise as before.
        """
        if self._cb is not None:
            self._cb.assert_not_open()

        last_status: int | None = None
        last_error: str | None = None

        for attempt in range(len(RETRY_BACKOFFS) + 1):
            try:
                response = self._client.get(url)
            except httpx.RequestError as e:
                last_error = repr(e)
                last_status = None
            else:
                last_status = response.status_code
                if response.status_code == 403:
                    if self._cb is not None:
                        self._cb.record_block()
                    self._sleep()
                    raise BlockedByServer(url, last_status=403)
                if response.status_code == 429 and self._cb is not None:
                    self._cb.record_block()
                if response.status_code in (404, 410):
                    self._sleep()
                    return FetchResult(text=response.text, status_code=response.status_code)
                if response.status_code not in RETRY_STATUSES:
                    self._sleep()
                    if response.status_code >= 400:
                        raise MaxRetriesExceeded(url, last_status, None)
                    return FetchResult(text=response.text, status_code=response.status_code)

            if attempt < len(RETRY_BACKOFFS):
                time.sleep(RETRY_BACKOFFS[attempt])

        self._sleep()
        raise MaxRetriesExceeded(url, last_status, last_error)
```

- [ ] **Step 4: Run tests to verify all pass**

```bash
.venv/bin/pytest tests/test_client_blocked.py tests/test_client_status_check.py -v
```

Expected: 11 passed (7 from Task 2 + 4 new in this task) — plus the 4 from test_client_status_check.py.

- [ ] **Step 5: Run full suite to confirm no regression**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add crawler/client.py tests/test_client_blocked.py
git commit -m "feat: get_allowing_4xx raises BlockedByServer on 403, respects circuit breaker"
```

---

### Task 4: Workers respect circuit breaker

**Files:**
- Modify: `crawler/monitor.py`
- Modify: `tests/test_monitor.py`

`run_monitor` accepts an optional `circuit_breaker` parameter. Both `scan_worker` and `enrich_worker` check `cb.is_open()` between queue pulls and exit cleanly if the circuit has tripped.

The integration test simulates a 403 storm and verifies:
1. CB trips after threshold blocks
2. Workers stop pulling from queues
3. `RunSummary.errors` reflects the actual block count
4. No data corruption — items already scanned successfully are preserved

- [ ] **Step 1: Add failing test (append to tests/test_monitor.py)**

Append to `tests/test_monitor.py`:

```python
from crawler.circuit_breaker import CircuitBreaker
from crawler.client import BlockedByServer


def test_run_monitor_stops_when_circuit_breaker_trips(tmp_path: Path):
    """All sellers return 403. CB should trip and workers should exit early."""
    db_path = tmp_path / "items.db"
    conn = connect(db_path); init_schema(conn); conn.close()

    class BlockingClient:
        """Always raises BlockedByServer, records block in shared CB."""
        def __init__(self, cb):
            self._cb = cb
        def get(self, url):
            self._cb.record_block()
            raise BlockedByServer(url, last_status=403)
        def get_allowing_4xx(self, url):
            return self.get(url)

    cb = CircuitBreaker(threshold=2, window_seconds=60)

    def client_factory():
        return BlockingClient(cb)

    sellers = {str(i): {"seller_id": str(i)} for i in range(1, 11)}  # 10 sellers

    summary = run_monitor(
        db_path=db_path,
        sellers=sellers,
        scan_client_factory=client_factory,
        enrich_client_factory=client_factory,
        num_workers=2,
        now="2026-06-11T15:00:00+09:00",
        on_error=lambda **kw: None,
        circuit_breaker=cb,
    )
    # CB should trip after 2 blocks. At most a handful of sellers attempted.
    assert cb.is_open() is True
    assert summary.errors >= 2
    # NOT all 10 sellers got processed
    assert summary.errors < 10


def test_run_monitor_works_without_circuit_breaker(tmp_path: Path):
    """Backward compat: cb parameter is optional."""
    db_path = tmp_path / "items.db"
    conn = connect(db_path); init_schema(conn); conn.close()

    def _items_page(items: list[str]) -> str:
        cards = "".join(
            f'<li class="buyeritemtable_info">'
            f'<p class="buyeritem_name"><a href="/item/{iid}/">N{iid}</a></p>'
            f'<p class="buyeritem_price">¥{500 + i}</p></li>'
            for i, iid in enumerate(items)
        )
        return f"<html><body><ul>{cards}</ul></body></html>"

    fake = FakeDetailClient({
        "https://www.buyma.com/buyer/12345/item_1.html": (200, _items_page(["111"])),
        "https://www.buyma.com/item/111/": (200, SAMPLE_DETAIL_HTML),
    })

    summary = run_monitor(
        db_path=db_path,
        sellers={"12345": {}},
        scan_client_factory=lambda: fake,
        enrich_client_factory=lambda: fake,
        num_workers=1,
        now="2026-06-11T15:00:00+09:00",
        on_error=lambda **kw: None,
        # NO circuit_breaker — must still work
    )
    assert summary.sellers_scanned == 1
    assert summary.items_new == 1
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
.venv/bin/pytest tests/test_monitor.py::test_run_monitor_stops_when_circuit_breaker_trips -v
```

Expected: FAIL — `run_monitor` does not accept `circuit_breaker` parameter yet (TypeError) and workers don't check `is_open()`.

- [ ] **Step 3: Modify run_monitor in crawler/monitor.py**

Update the `run_monitor` signature to accept `circuit_breaker`:

```python
def run_monitor(
    db_path: Path | str,
    sellers: dict[str, dict],
    scan_client_factory: Callable[[], object],
    enrich_client_factory: Callable[[], object],
    num_workers: int,
    now: str,
    on_error: Callable[..., None],
    circuit_breaker=None,
) -> RunSummary:
    """Run one full scan+enrich pass over all sellers. Single SQLite DB.

    If a circuit_breaker is provided, workers exit cleanly when it trips
    (typically after repeated 403/429 responses).
    """
    from crawler.seller_items_crawler import scan_seller_items

    db_path = Path(db_path)
    summary = RunSummary()

    main_conn = connect(db_path)
    init_schema(main_conn)
    run_id = main_conn.execute(
        "INSERT INTO monitor_runs (started_at) VALUES (?)", (now,)
    ).lastrowid
    db_lock = threading.Lock()

    def _cb_open() -> bool:
        return circuit_breaker is not None and circuit_breaker.is_open()

    # --- Stage A: scan + reconcile -----------------------------------------
    seller_queue: queue.Queue[str] = queue.Queue()
    for sid in sellers:
        seller_queue.put(sid)

    new_items: list[tuple[str, str]] = []
    disappeared_items: list[tuple[str, str]] = []
    error_count = [0]
    error_lock = threading.Lock()

    def tracking_on_error(**kw):
        with error_lock:
            error_count[0] += 1
        on_error(**kw)

    def scan_worker():
        client = scan_client_factory()
        try:
            while not _cb_open():
                try:
                    sid = seller_queue.get_nowait()
                except queue.Empty:
                    return
                items = scan_seller_items(client, sid, tracking_on_error)
                with db_lock:
                    outcome = apply_seller_scan_to_db(main_conn, sid, items, now)
                    for iid in outcome.new_item_ids:
                        new_items.append((iid, sid))
                    for iid in outcome.disappeared_item_ids:
                        disappeared_items.append((iid, sid))
                    summary.items_new += len(outcome.new_item_ids)
                    summary.items_updated += outcome.price_changes
                seller_queue.task_done()
        finally:
            if hasattr(client, "close"):
                client.close()

    threads = [threading.Thread(target=scan_worker, daemon=True) for _ in range(num_workers)]
    for t in threads: t.start()
    for t in threads: t.join()

    summary.sellers_scanned = len(sellers) - seller_queue.qsize()

    # --- Stage B: enrich new + classify disappeared ------------------------
    enrich_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    for iid, _ in new_items:
        enrich_queue.put(("new", iid))
    for iid, _ in disappeared_items:
        enrich_queue.put(("disappeared", iid))

    def enrich_worker():
        client = enrich_client_factory()
        try:
            while not _cb_open():
                try:
                    kind, iid = enrich_queue.get_nowait()
                except queue.Empty:
                    return
                if kind == "new":
                    body = fetch_for_enrich(client, iid, tracking_on_error)
                    if body is not None:
                        with db_lock:
                            apply_enrich(main_conn, iid, body, now)
                elif kind == "disappeared":
                    fetched = fetch_for_classification(client, iid, tracking_on_error)
                    if fetched is not None:
                        status_code, _ = fetched
                        with db_lock:
                            status = apply_classification(main_conn, iid, status_code, now)
                            if status is ItemStatus.DELETED:
                                summary.items_deleted += 1
                            elif status is ItemStatus.SOLD_OUT:
                                summary.items_sold_out += 1
                enrich_queue.task_done()
        finally:
            if hasattr(client, "close"):
                client.close()

    threads = [threading.Thread(target=enrich_worker, daemon=True) for _ in range(num_workers)]
    for t in threads: t.start()
    for t in threads: t.join()

    summary.errors = error_count[0]

    main_conn.execute(
        """UPDATE monitor_runs SET
            finished_at = ?, sellers_scanned = ?, items_new = ?, items_updated = ?,
            items_sold_out = ?, items_deleted = ?, errors = ?
           WHERE run_id = ?""",
        (now, summary.sellers_scanned, summary.items_new, summary.items_updated,
         summary.items_sold_out, summary.items_deleted, summary.errors, run_id),
    )
    main_conn.close()
    return summary
```

Key changes:
1. New `circuit_breaker=None` parameter
2. `_cb_open()` helper that returns False when no CB is provided
3. Both worker loops check `while not _cb_open():`
4. `summary.sellers_scanned` now subtracts unprocessed sellers (those still in queue when CB tripped) — gives accurate stats

- [ ] **Step 4: Run new tests to verify they pass**

```bash
.venv/bin/pytest tests/test_monitor.py -v
```

Expected: all monitor tests pass (existing 9 + 2 new = 11).

- [ ] **Step 5: Run full suite to confirm no regression**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crawler/monitor.py tests/test_monitor.py
git commit -m "feat: workers exit cleanly when circuit breaker trips, sellers_scanned reflects actual processed count"
```

---

### Task 5: CLI wiring

**Files:**
- Modify: `monitor_cli.py`

Add `--cb-threshold` and `--cb-window-seconds` flags. Build one `CircuitBreaker`, share it across all `HttpClient` instances via the factory closure, and pass it to `run_monitor`.

- [ ] **Step 1: Modify monitor_cli.py**

Update `monitor_cli.py`:

```python
"""Run the monitoring pipeline once over all sellers in data/sellers.json."""
import argparse
import json
import logging
import sys
from pathlib import Path

from crawler.circuit_breaker import CircuitBreaker
from crawler.client import HttpClient
from crawler.monitor import run_monitor
from storage.store import now_iso


DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "items.db"
SELLERS_PATH = DATA_DIR / "sellers.json"
ERRORS_PATH = DATA_DIR / "errors.log"


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def append_error_jsonl(**kw) -> None:
    record = {"timestamp": now_iso(), **kw}
    with ERRORS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="BUYMA product monitor")
    parser.add_argument("--workers", type=int, default=5,
                        help="parallel HTTP workers (default 5)")
    parser.add_argument("--sleep", type=float, default=0.3,
                        help="per-request sleep seconds (default 0.3)")
    parser.add_argument("--limit-sellers", type=int, default=None,
                        help="process only the first N sellers (debug)")
    parser.add_argument("--cb-threshold", type=int, default=5,
                        help="circuit breaker: max 403/429 blocks in window (default 5)")
    parser.add_argument("--cb-window-seconds", type=int, default=60,
                        help="circuit breaker: rolling window size in seconds (default 60)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if not SELLERS_PATH.exists():
        logging.error("sellers.json not found at %s — run main.py first", SELLERS_PATH)
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    sellers = json.loads(SELLERS_PATH.read_text(encoding="utf-8"))
    if args.limit_sellers:
        sellers = dict(list(sellers.items())[: args.limit_sellers])
    logging.info("Loaded %d sellers from %s", len(sellers), SELLERS_PATH)

    circuit_breaker = CircuitBreaker(
        threshold=args.cb_threshold,
        window_seconds=args.cb_window_seconds,
    )

    def factory():
        return HttpClient(sleep_seconds=args.sleep, circuit_breaker=circuit_breaker)

    now = now_iso()
    logging.info(
        "Starting monitor run at %s (workers=%d, sleep=%.2fs, cb_threshold=%d, cb_window=%ds)",
        now, args.workers, args.sleep, args.cb_threshold, args.cb_window_seconds,
    )

    summary = run_monitor(
        db_path=DB_PATH,
        sellers=sellers,
        scan_client_factory=factory,
        enrich_client_factory=factory,
        num_workers=args.workers,
        now=now,
        on_error=append_error_jsonl,
        circuit_breaker=circuit_breaker,
    )
    if circuit_breaker.is_open():
        logging.warning(
            "Run halted by circuit breaker (%d blocks observed). "
            "BUYMA likely rate-limiting or blocking this IP. "
            "Investigate before restarting.",
            len(circuit_breaker._events),
        )
    logging.info("Done: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Two minor improvements bundled in:
- `DATA_DIR.mkdir(parents=True, exist_ok=True)` — was flagged in the final review of the original implementation as a minor robustness gap. This is a one-line fix and belongs naturally in the CLI.
- Warning log when CB trips — the operator needs to see this clearly.

- [ ] **Step 2: Verify the CLI parses correctly**

```bash
.venv/bin/python3 monitor_cli.py --help
```

Expected: help text shows `--cb-threshold` and `--cb-window-seconds`.

- [ ] **Step 3: Run full test suite to confirm no regression**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Optional smoke test (skip if BUYMA still blocking)**

```bash
.venv/bin/python3 monitor_cli.py --limit-sellers 1 --workers 1 --cb-threshold 3
```

Two expected outcomes:
- (a) **If BUYMA has un-blocked us:** scan completes normally, summary shows items_new > 0.
- (b) **If still blocking:** CB trips after 3 blocks, warning log appears, summary.errors=3 (or similar), run ends in seconds without hammering.

Either outcome confirms wiring is correct. Do NOT mark this step done if the run silently completes with errors=0 AND items_new=0 — that would mean CB never tripped but also fetches never succeeded (inconsistent state).

- [ ] **Step 5: Commit**

```bash
git add monitor_cli.py
git commit -m "feat: CLI wires CircuitBreaker, warns on trip, ensures data/ dir exists"
```

---

---

### Task 6: Refactor crawler/orders.py to use shared BlockedByServer + CircuitBreaker

**Context:** Between sessions, the orders pipeline (`crawler/orders.py` + `main.py::run_crawl_orders`) was updated independently with its own `BuyMaIPBlocked` exception and `threading.Event`-based abort mechanism (commit `ccb5aae`). This task unifies it with the shared blocking infrastructure introduced in Tasks 1–3 so both pipelines use the same exception and circuit breaker types.

The orders crawler's semantic intent — "abort on first 403" — is preserved by configuring the CircuitBreaker with `threshold=1`. No behavioral change from the operator's perspective.

**Files:**
- Modify: `crawler/orders.py` — remove `BuyMaIPBlocked` class, use `BlockedByServer`; replace `abort_event` with `CircuitBreaker.is_open()`
- Modify: `main.py` — instantiate CircuitBreaker(threshold=1) and thread through

**There are NO tests for `crawler/orders.py` today.** The refactor is verified by:
1. Running the existing full test suite (must not regress)
2. `python -c "from crawler.orders import ..."` smoke imports
3. Reading the diff carefully to confirm equivalent semantics

- [ ] **Step 1: Modify crawler/orders.py — replace exception + abort_event with CircuitBreaker**

Open `crawler/orders.py`. The current file has:

```python
from crawler.client import MaxRetriesExceeded


class BuyMaIPBlocked(Exception):
    """Raised when BUYMA returns 403, indicating the caller's IP is likely blocked."""
    def __init__(self, url: str):
        self.url = url
        super().__init__(f"403 Forbidden at {url}")
```

Change the imports and DELETE the `BuyMaIPBlocked` class:

```python
from crawler.client import BlockedByServer, MaxRetriesExceeded
```

(Note: `BlockedByServer` was added in Task 2.)

Then in `crawl_seller_orders`, the current code has:

```python
        if e.last_status == 403:
            raise BuyMaIPBlocked(url) from e
```

The 403 handling is no longer needed at this layer because `HttpClient.get` (Task 2) now raises `BlockedByServer` directly on 403 — it never returns 403 as a `MaxRetriesExceeded`. Remove the entire `if e.last_status == 403: raise BuyMaIPBlocked(url) from e` branch. The `BlockedByServer` from `client.get()` will propagate naturally through the existing `except MaxRetriesExceeded as e:` handler — wait, that's wrong: `BlockedByServer` is NOT a `MaxRetriesExceeded`, so it won't be caught by that handler. It will propagate up to the worker.

That's actually the desired behavior: the worker layer catches `BlockedByServer` and triggers abort. So at the `crawl_seller_orders` layer, just delete the `if e.last_status == 403:` branch.

After your edit, the relevant section of `crawl_seller_orders` should look like:

```python
        try:
            response = client.get(url)
        except MaxRetriesExceeded as e:
            if e.last_status == 404:
                page_num_done = page_num
                break
            raise
```

Next, update `crawl_all_orders_with_factory`. Replace its signature to accept an optional `circuit_breaker`:

```python
def crawl_all_orders_with_factory(
    client_factory,
    seller_watermarks: dict[str, list],
    max_pages: int,
    on_error,
    on_seller_done,
    num_workers: int = 3,
    circuit_breaker=None,
) -> dict:
```

Then in the function body, REMOVE the `abort_event` and REPLACE its usages with `circuit_breaker`-based checks:

Replace:
```python
    stats_lock = threading.Lock()
    abort_event = threading.Event()
```
with:
```python
    stats_lock = threading.Lock()

    def _cb_open() -> bool:
        return circuit_breaker is not None and circuit_breaker.is_open()
```

In the worker, replace:
```python
        while True:
            if abort_event.is_set():
                return
```
with:
```python
        while not _cb_open():
```

Replace the `except BuyMaIPBlocked` branch:
```python
                    except BuyMaIPBlocked:
                        abort_event.set()
                        return
```
with:
```python
                    except BlockedByServer:
                        # circuit_breaker.record_block() was already called by HttpClient.
                        # If threshold=1, the breaker is now open; the while-loop guard exits.
                        return
```

At the bottom of `crawl_all_orders_with_factory`, replace:
```python
    stats["aborted_by_ip_block"] = abort_event.is_set()
    return stats
```
with:
```python
    stats["aborted_by_ip_block"] = _cb_open()
    return stats
```

- [ ] **Step 2: Modify main.py — create CircuitBreaker and thread through**

Open `main.py`. Find `run_crawl_orders`. Near the top of the function (before the client_factory is built or used), add:

```python
    from crawler.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(threshold=1, window_seconds=60)
```

(Adjust the import to module-level if other code in main.py prefers that style — match the existing convention.)

Find the `client_factory` definition in `run_crawl_orders`. It currently creates an `HttpClient(...)` — update it to pass the circuit breaker:

```python
    def client_factory():
        return HttpClient(sleep_seconds=..., circuit_breaker=cb)  # keep existing sleep_seconds arg
```

(The existing `sleep_seconds` and any other args must be preserved verbatim.)

Find the call to `crawl_all_orders_with_factory(...)`. Add `circuit_breaker=cb` to its kwargs:

```python
    stats = crawl_all_orders_with_factory(
        client_factory=client_factory,
        seller_watermarks=seller_watermarks,
        max_pages=...,
        on_error=...,
        on_seller_done=...,
        num_workers=3,
        circuit_breaker=cb,
    )
```

(Preserve all existing arguments — only ADD `circuit_breaker=cb`.)

The existing `if stats.get("aborted_by_ip_block"):` block is untouched — it still works because `aborted_by_ip_block` is still populated.

- [ ] **Step 3: Smoke-test the imports and grep for leftovers**

```bash
.venv/bin/python3 -c "
from crawler.orders import crawl_all_orders_with_factory, crawl_seller_orders
from crawler.client import BlockedByServer
from crawler.circuit_breaker import CircuitBreaker
print('imports OK')
"
```

Expected: `imports OK`.

```bash
grep -rn "BuyMaIPBlocked" "buyma market monitor/" --include="*.py" || echo "no references"
```

Expected: `no references` (the class and all usages should be gone).

- [ ] **Step 4: Run full test suite to confirm no regression**

```bash
.venv/bin/pytest tests/ -v
```

Expected: 64 tests still pass (no orders tests exist, so this just confirms the refactor didn't break monitor/client/etc).

- [ ] **Step 5: Commit**

```bash
git add crawler/orders.py main.py
git commit -m "refactor(orders): use shared BlockedByServer + CircuitBreaker (threshold=1)"
```

---

## Self-Review

**Spec coverage:**
- ✅ A (Circuit breaker): Tasks 1, 4, 5 — module, worker integration, CLI wiring
- ✅ E (403 detection + clearer logs): Tasks 2, 3 — BlockedByServer exception, distinct from MaxRetriesExceeded; CB trip produces a warning log line in Task 5
- ✅ Consistency across pipelines: Task 6 — orders.py uses the same BlockedByServer + CircuitBreaker

**Placeholder scan:**
- No "TBD", "TODO", "implement later" in any step
- All code blocks are complete and runnable
- All test code is concrete (no "test the above" stubs)

**Type consistency:**
- `CircuitBreaker.record_block()`, `is_open()`, `assert_not_open()` — same names used in Tasks 1, 2, 3, 4, 6
- `BlockedByServer.last_status` attribute — set in Task 2, asserted in Task 2 test, relied upon by `on_error` callback (existing convention) in Task 4's blocking client mock; orders.py workers also rely on it via `getattr(e, "last_status", None)` in their existing on_error path
- `circuit_breaker` parameter — added to `HttpClient.__init__` in Task 2, to `run_monitor` in Task 4, to `crawl_all_orders_with_factory` in Task 6, threaded through CLI in Task 5 and main.py in Task 6

**Test count after all tasks:**
- Existing: 42 tests
- + Task 1: 9 tests
- + Task 2: 7 tests
- + Task 3: 4 tests
- + Task 4: 2 tests
- + Task 6: 0 new tests (orders.py has no pre-existing test suite; refactor is verified via import smoke + suite no-regression)
- = 64 tests total
