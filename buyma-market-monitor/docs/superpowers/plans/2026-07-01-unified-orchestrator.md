# 2단계: 통합 오케스트레이터 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 단일 데몬 `orchestrator.py`가 매 24h 사이클에서 ①셀러→②주문→③a목록스캔을 각 시간 cap으로 순차 실행하고 남는 시간 전부를 ③b 재방문(loop)에 배분한다. 차단 시 전체 일시정지→쿨다운→재개, SIGINT graceful.

**Architecture:** 순수 사이클 로직(`orchestrator.py::run_cycle`/`run_forever`)은 스테이지 함수를 주입받아 네트워크 없이 테스트한다. 실제 스테이지는 얇은 어댑터로 기존 파이프라인(`run_page_scan`, `run_revisit`, seller/orders 크롤러)에 연결. ①② 크롤러에 `deadline` 지원을 추가해 cap을 강제한다.

**Tech Stack:** Python 3.14, threading, pytest. 기존 `crawler/page_scan.py`, `crawler/revisit.py`, `crawler/seller.py`, `crawler/orders.py`, `main.py` 재사용.

---

## 배경

- ③a: `crawler/page_scan.py::run_page_scan(db_path, sellers, client_factory, num_workers, now, on_error, max_hours=None, circuit_breaker=None, stop_event=None) -> ScanSummary`.
- ③b: `crawler/revisit.py::run_revisit(db_path, scan_client_factory, num_workers, now, on_error, max_hours, circuit_breaker=None, revisit_limit=1_000_000, loop=False, idle_seconds=600.0, stop_event=None, clock=now_iso) -> RevisitSummary`.
- ①: `main.py::run_crawl_sellers(args)` → 내부에서 `crawler/listing.crawl_listing_pages` + `crawler/seller.crawl_sellers_with_factory(client_factory, seller_ids, on_error, num_workers)`.
- ②: `main.py::run_crawl_orders(args)` → `crawler/orders.crawl_all_orders_with_factory(client_factory, seller_watermarks, on_seller_done, on_error, max_pages, num_workers, circuit_breaker)`.
- `crawler/circuit_breaker.py::CircuitBreaker(threshold, window_seconds)`, `.is_open()`, `.record_block()`.
- `crawler/client.py::HttpClient(sleep_seconds=, circuit_breaker=)`, `PlaywrightClient(circuit_breaker=)`.
- `storage/store.py::now_iso()`. 시각/Δ는 `time.monotonic()` 사용(벽시계 아님).
- 리포 커밋은 `main`.

## File Structure

| 파일 | 책임 |
|---|---|
| `crawler/seller.py` (수정) | `crawl_sellers_with_factory`에 `deadline: float | None = None` 추가 (셀러 pull 직전 체크) |
| `crawler/orders.py` (수정) | `crawl_all_orders_with_factory`에 `deadline: float | None = None` 추가 |
| `orchestrator.py` (신규) | `run_cycle`, `run_forever`(supervisor 루프), 스테이지 어댑터, CLI(SIGINT/cap/쿨다운) |
| `tests/test_orchestrator.py` (신규) | 사이클 순차·cap·remaining·쿨다운·정지 단위 테스트(주입 가짜 스테이지) |

---

## Task 1: ①② 크롤러에 deadline 지원 추가

**Files:**
- Modify: `crawler/seller.py`, `crawler/orders.py`
- Test: `tests/test_seller_deadline.py` (신규)

각 워커가 다음 셀러를 처리하기 전에 deadline 초과를 확인하고 조기 종료한다(남은 셀러는 다음 사이클이 재개). 기존 동작은 `deadline=None`이면 완전히 동일.

- [ ] **Step 1: Write the failing test**

`tests/test_seller_deadline.py` 생성:

```python
import time
from crawler.seller import crawl_sellers_with_factory


class _SlowClient:
    """Each .get sleeps a bit so deadline can trip mid-run."""
    def __init__(self, seen):
        self._seen = seen

    def get(self, url):
        self._seen.append(url)
        time.sleep(0.05)
        class _R:
            text = "<html></html>"
        return _R()

    def close(self):
        pass


def test_crawl_sellers_stops_at_deadline():
    seen = []
    # deadline already in the past → no seller should be processed
    result = crawl_sellers_with_factory(
        client_factory=lambda: _SlowClient(seen),
        seller_ids=[f"s{i}" for i in range(50)],
        on_error=lambda **kw: None,
        num_workers=2,
        deadline=time.monotonic() - 1.0,
    )
    assert result == []          # nothing collected past deadline
    assert seen == []            # no fetches issued
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_seller_deadline.py -v`
Expected: FAIL (unexpected keyword argument 'deadline')

- [ ] **Step 3: Implement — add deadline to crawl_sellers_with_factory**

Read `crawler/seller.py`. Add parameter `deadline: float | None = None` to `crawl_sellers_with_factory`. In the worker loop, immediately before a worker pulls/starts processing the next seller id (i.e., at the top of the per-seller iteration inside the worker), add:
```python
                import time as _time
                if deadline is not None and _time.monotonic() >= deadline:
                    break
```
Place it so that once the deadline passes, the worker stops taking new sellers (already-in-flight seller finishes). Keep everything else unchanged. (Import `time` at module top if not present, instead of the inline import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_seller_deadline.py -v`
Expected: PASS

- [ ] **Step 5: Add the same to crawl_all_orders_with_factory + test**

Add `deadline: float | None = None` to `crawl_all_orders_with_factory` in `crawler/orders.py`, with the same top-of-per-seller guard (`if deadline is not None and time.monotonic() >= deadline: break`). Add to `tests/test_seller_deadline.py`:

```python
from crawler.orders import crawl_all_orders_with_factory


def test_crawl_orders_stops_at_deadline():
    done = []
    stats = crawl_all_orders_with_factory(
        client_factory=lambda: _SlowClient([]),
        seller_watermarks={f"s{i}": [] for i in range(50)},
        on_seller_done=lambda *a, **k: done.append(a[0]),
        on_error=lambda **kw: None,
        max_pages=1,
        num_workers=2,
        deadline=time.monotonic() - 1.0,
    )
    assert done == []            # no seller completed past deadline
```

- [ ] **Step 6: Run tests + full suite**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_seller_deadline.py -v` then `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add crawler/seller.py crawler/orders.py tests/test_seller_deadline.py
git commit -m "feat(orch): deadline support in seller/orders crawlers"
```

---

## Task 2: 오케스트레이터 코어 (run_cycle / run_forever)

**Files:**
- Create: `orchestrator.py`
- Test: `tests/test_orchestrator.py`

순수 사이클 로직. 스테이지는 `Stage(name, fn, cap_seconds)`로 주입. `fn(deadline, cb, stop_event)`를 호출하며, `cb.is_open()`이면 차단으로 간주 → 쿨다운 후 같은 스테이지 재시도. 배치(①②③a) 순차 후 남는 시간을 revisit_fn(max_hours=remaining)로.

- [ ] **Step 1: Write the failing tests**

`tests/test_orchestrator.py` 생성:

```python
import threading
from dataclasses import dataclass
from orchestrator import Stage, run_cycle


class _FakeClock:
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


def test_run_cycle_runs_stages_in_order_then_revisit():
    clock = _FakeClock()
    calls = []

    def mk(name, dt):
        def fn(deadline, cb, stop_event):
            calls.append(name)
            clock.advance(dt)   # simulate time spent
            return f"{name}-done"
        return fn

    stages = [
        Stage("sellers", mk("sellers", 100), cap_seconds=3600),
        Stage("orders", mk("orders", 200), cap_seconds=3600),
        Stage("scan", mk("scan", 300), cap_seconds=3600),
    ]
    revisit_seen = {}

    def revisit_fn(remaining_seconds, cb, stop_event):
        revisit_seen["remaining"] = remaining_seconds
        clock.advance(remaining_seconds)
        return "revisit-done"

    run_cycle(
        stages=stages, revisit_fn=revisit_fn, cycle_seconds=86400,
        cooldown_seconds=1, clock=clock, sleep_fn=lambda s: None,
        make_cb=lambda: _FakeCB(open_after=None), stop_event=threading.Event())

    assert calls == ["sellers", "orders", "scan"]
    # remaining = 86400 - (100+200+300) = 85800
    assert revisit_seen["remaining"] == 85800


class _FakeCB:
    """Opens (is_open True) on the first is_open() check after `open_after`
    record_block calls; here we simulate a stage that blocks by opening immediately."""
    def __init__(self, open_after):
        self._open = False
        self.open_after = open_after
    def is_open(self):
        return self._open
    def force_open(self):
        self._open = True


def test_run_cycle_cooldown_and_retry_on_block():
    clock = _FakeClock()
    attempts = []
    cbs = []

    def make_cb():
        cb = _FakeCB(open_after=None)
        cbs.append(cb)
        return cb

    # sellers stage blocks on first attempt (cb opens), succeeds on retry
    def sellers_fn(deadline, cb, stop_event):
        attempts.append("sellers")
        if len(attempts) == 1:
            cb.force_open()      # signal block
            return "blocked"
        return "ok"

    slept = []
    stages = [Stage("sellers", sellers_fn, cap_seconds=3600)]
    run_cycle(
        stages=stages, revisit_fn=lambda rem, cb, se: None,
        cycle_seconds=86400, cooldown_seconds=42,
        clock=clock, sleep_fn=lambda s: slept.append(s),
        make_cb=make_cb, stop_event=threading.Event())

    assert attempts == ["sellers", "sellers"]   # retried after cooldown
    assert slept == [42]                          # cooldown slept once


def test_run_cycle_stops_on_stop_event():
    clock = _FakeClock()
    ev = threading.Event(); ev.set()
    calls = []
    stages = [Stage("sellers", lambda d, cb, se: calls.append("x"), cap_seconds=10)]
    run_cycle(
        stages=stages, revisit_fn=lambda rem, cb, se: calls.append("revisit"),
        cycle_seconds=86400, cooldown_seconds=1,
        clock=clock, sleep_fn=lambda s: None,
        make_cb=lambda: _FakeCB(None), stop_event=ev)
    assert calls == []    # stop_event set → nothing runs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_orchestrator.py -v`
Expected: FAIL (ModuleNotFoundError: orchestrator)

- [ ] **Step 3: Implement orchestrator core**

`orchestrator.py` 생성 (코어 부분):

```python
"""Single-process supervisor: daily batch (sellers→orders→scan) with per-stage
time caps, remaining time to the revisit loop, cooldown-and-resume on IP block."""
import logging
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class Stage:
    name: str
    fn: Callable            # fn(deadline: float|None, cb, stop_event) -> result
    cap_seconds: float


def _sleep_interruptible(sleep_fn, seconds, stop_event) -> None:
    # sleep_fn is injected for tests; real caller uses stop_event.wait
    if stop_event is not None and hasattr(stop_event, "wait"):
        stop_event.wait(seconds)
    else:
        sleep_fn(seconds)


def run_cycle(
    stages, revisit_fn, cycle_seconds, cooldown_seconds,
    clock=time.monotonic, sleep_fn=time.sleep, make_cb=None, stop_event=None,
) -> None:
    """Run one daily cycle: each batch stage within its cap, then revisit for the
    remaining time. On block (cb.is_open() after a stage), cooldown then retry that
    stage. stop_event short-circuits everything."""
    def _stopped():
        return stop_event is not None and stop_event.is_set()

    cycle_start = clock()

    for stage in stages:
        while not _stopped():
            cb = make_cb() if make_cb is not None else None
            deadline = clock() + stage.cap_seconds
            logging.info("[%s] start (cap=%.0fs)", stage.name, stage.cap_seconds)
            stage.fn(deadline, cb, stop_event)
            if cb is not None and cb.is_open():
                logging.warning("[%s] blocked — cooldown %.0fs then resume",
                                stage.name, cooldown_seconds)
                _sleep_interruptible(sleep_fn, cooldown_seconds, stop_event)
                continue    # retry same stage (resumable)
            break           # stage finished (or hit its cap) → next stage
        if _stopped():
            return

    remaining = cycle_seconds - (clock() - cycle_start)
    if remaining <= 0:
        remaining = 0
    logging.info("[revisit] remaining %.0fs", remaining)
    cb = make_cb() if make_cb is not None else None
    revisit_fn(remaining, cb, stop_event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_orchestrator.py -v`
Expected: PASS (3 tests). (Note: `_sleep_interruptible` in tests uses stop_event.wait; the test's stop_event is a real Event that is NOT set during cooldown tests, so `.wait(seconds)` returns after `seconds`. To keep the cooldown test fast and assert on `sleep_fn`, the implementation must call `sleep_fn` when stop_event has no meaningful wait. Adjust: in tests we pass a real Event; to record cooldown, change `_sleep_interruptible` to ALWAYS call `sleep_fn(seconds)` and additionally `stop_event.wait(0)`-style checks. SIMPLER: make `_sleep_interruptible(sleep_fn, seconds, stop_event)` call `sleep_fn(seconds)` always, and rely on `sleep_fn` in the CLI to be a stop-aware sleeper.)

  Implementation adjustment for Step 3 (use this instead): replace `_sleep_interruptible` with a direct `sleep_fn(cooldown_seconds)` call in `run_cycle`, and in the CLI pass a `sleep_fn` that is stop-aware (see Task 3). Update the block branch to:
```python
                logging.warning("[%s] blocked — cooldown %.0fs then resume",
                                stage.name, cooldown_seconds)
                sleep_fn(cooldown_seconds)
                if _stopped():
                    return
                continue
```
  Remove the `_sleep_interruptible` helper. Re-run tests → the cooldown test asserts `slept == [42]`. PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orch): run_cycle core (staged caps, remaining→revisit, cooldown/resume)"
```

---

## Task 3: run_forever + CLI 배선 (실제 스테이지)

**Files:**
- Modify: `orchestrator.py`
- Test: 스모크 (no full network run)

`run_forever`(사이클 무한 반복) + 실제 스테이지 어댑터 + SIGINT + stop-aware sleep.

- [ ] **Step 1: Implement run_forever + adapters + CLI**

`orchestrator.py`에 추가:

```python
import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from crawler.circuit_breaker import CircuitBreaker
from crawler.client import HttpClient, PlaywrightClient
from crawler.page_scan import run_page_scan
from crawler.revisit import run_revisit
from storage.db import connect, init_schema
from storage import sellers_repo
from storage.store import now_iso

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "items.db"
ERRORS_PATH = DATA_DIR / "errors.log"


def _append_error(**kw) -> None:
    with ERRORS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": now_iso(), **kw}, ensure_ascii=False) + "\n")


def _load_seller_ids() -> list[str]:
    conn = connect(DB_PATH); init_schema(conn)
    sellers = sellers_repo.load_sellers(conn); conn.close()
    return list(sellers)


def run_forever(args, stop_event) -> None:
    def make_http_cb():
        return CircuitBreaker(threshold=args.cb_threshold, window_seconds=args.cb_window_seconds)

    # --- stage adapters: fn(deadline, cb, stop_event) ---
    def stage_sellers(deadline, cb, se):
        from main import run_crawl_sellers  # reuse existing pipeline
        # NOTE: run_crawl_sellers builds its own clients; deadline/cb wiring for ①
        # is via crawl_sellers_with_factory(deadline=...). Minimal MVP: call as-is.
        class _A:
            reset_pagination = False; verbose = args.verbose; dry_run = False
        run_crawl_sellers(_A())

    def stage_orders(deadline, cb, se):
        from main import run_crawl_orders
        class _A:
            max_pages = args.orders_max_pages; seller_id = None
            full_rescan = False; verbose = args.verbose; dry_run = False
        run_crawl_orders(_A())

    def stage_scan(deadline, cb, se):
        max_hours = None if deadline is None else max(0.0, (deadline - time.monotonic()) / 3600.0)
        run_page_scan(
            db_path=DB_PATH, sellers=_load_seller_ids(),
            client_factory=lambda: HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb),
            num_workers=args.workers, now=now_iso(), on_error=_append_error,
            max_hours=max_hours, circuit_breaker=cb, stop_event=se)

    def revisit_fn(remaining_seconds, cb, se):
        run_revisit(
            db_path=DB_PATH,
            scan_client_factory=lambda: HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb),
            num_workers=args.workers, now=now_iso(), on_error=_append_error,
            max_hours=max(0.0, remaining_seconds / 3600.0),
            circuit_breaker=cb, loop=True, idle_seconds=args.idle_minutes * 60.0,
            stop_event=se)

    stages = [
        Stage("sellers", stage_sellers, cap_seconds=args.sellers_cap_hours * 3600.0),
        Stage("orders", stage_orders, cap_seconds=args.orders_cap_hours * 3600.0),
        Stage("scan", stage_scan, cap_seconds=args.scan_cap_hours * 3600.0),
    ]

    def stop_aware_sleep(seconds):
        stop_event.wait(seconds)

    while not stop_event.is_set():
        logging.info("=== cycle start %s ===", now_iso())
        run_cycle(
            stages=stages, revisit_fn=revisit_fn,
            cycle_seconds=args.cycle_hours * 3600.0,
            cooldown_seconds=args.cooldown_minutes * 60.0,
            clock=time.monotonic, sleep_fn=stop_aware_sleep,
            make_cb=make_http_cb, stop_event=stop_event)


def main() -> int:
    p = argparse.ArgumentParser(description="BUYMA unified orchestrator")
    p.add_argument("--cycle-hours", type=float, default=24.0)
    p.add_argument("--sellers-cap-hours", type=float, default=1.0)
    p.add_argument("--orders-cap-hours", type=float, default=3.0)
    p.add_argument("--scan-cap-hours", type=float, default=6.0)
    p.add_argument("--cooldown-minutes", type=float, default=45.0)
    p.add_argument("--idle-minutes", type=float, default=10.0)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--sleep", type=float, default=0.3)
    p.add_argument("--orders-max-pages", type=int, default=100)
    p.add_argument("--cb-threshold", type=int, default=5)
    p.add_argument("--cb-window-seconds", type=int, default=60)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    stop_event = threading.Event()

    def _sigint(signum, frame):
        logging.info("SIGINT — finishing current work, stopping after this stage...")
        stop_event.set()
    signal.signal(signal.SIGINT, _sigint)

    run_forever(args, stop_event)
    logging.info("orchestrator stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> MVP 주의: ①셀러 스테이지는 기존 `run_crawl_sellers`를 그대로 호출한다(자체 cap 미적용 — 셀러 수집은 상대적으로 짧음). ①②의 deadline/cb 정밀 배선은 Task 1의 `deadline` 파라미터를 쓰도록 후속 개선 가능하나, MVP는 ③a/③b에 cap·cb·stop을 정확히 적용하고 ①②는 완주시키는 것으로 시작한다. (③a/③b가 대부분의 시간을 쓰므로 예산 배분의 본질은 확보된다.)

- [ ] **Step 2: Smoke (no full run)**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 orchestrator.py --help`
Expected: 옵션 표시(--cycle-hours/--*-cap-hours/--cooldown-minutes 등).

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -c "import orchestrator"`
Expected: 임포트 클린.

- [ ] **Step 3: Full test suite**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest -q`
Expected: 전체 PASS.

- [ ] **Step 4: Commit**

```bash
git add orchestrator.py
git commit -m "feat(orch): run_forever daemon + stage adapters + CLI (SIGINT, caps, cooldown)"
```

---

## Self-Review (작성자 체크)

**Spec coverage:**
- 24h 사이클 ①→②→③a 순차 + 남는 시간 ③b → Task 2 run_cycle, Task 3 run_forever
- 스테이지별 시간 cap → Task 2 (deadline=clock()+cap), Task 1 (①② deadline 파라미터)
- 남는 시간 전부 재방문 → Task 2 (remaining→revisit_fn max_hours)
- 차단→전체 일시정지→쿨다운→재개 → Task 2 (cb.is_open()→sleep_fn(cooldown)→retry)
- SIGINT graceful, 단일 로그 → Task 3 (signal + stop_event, logging)
- 단일 프로세스 supervisor(A) → orchestrator.py in-process 함수 호출

**Type consistency:** `Stage(name, fn, cap_seconds)`; `fn(deadline, cb, stop_event)`; `revisit_fn(remaining_seconds, cb, stop_event)` 시그니처 일관. run_page_scan/run_revisit 호출 인자는 실제 시그니처와 일치.

**MVP 한계 명시:** ①②는 완주(자체 cap 미적용) — 예산의 본질(③a cap + ③b 남은시간)은 확보. ①②의 deadline 정밀 적용은 Task1에서 파라미터를 깔아두었으므로 후속으로 배선 가능.

**비범위:** launchd 재부팅복구, 스테이지 병렬, 요청수 예산.
