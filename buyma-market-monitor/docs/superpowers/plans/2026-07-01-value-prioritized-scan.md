# ③a 가치 기준 우선순위 스캔 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** ③a 목록 스캔을 셀러 가치(복합: HOT/WARM 상품 수 + 최근 주문) 기준 주기 차등으로. 고가치 셀러 자주·저가치 드물게 스캔하되 기본 주기 보장, 신규 셀러 초기 스캔. 실행기는 기존 `run_page_scan` 재사용.

**Architecture:** 신규 `crawler/scan_scheduler.py`(순수 티어/주기 로직) + `storage/scan_repo.py`(seller_scan_state CRUD·가치 재계산·due 조회) + `seller_scan_state` 테이블(v5). `run_value_scan`이 매 실행: 가치 재계산 → due 셀러 선정(cap) → run_page_scan → 완료 셀러 주기 전진.

**Tech Stack:** Python 3.14, SQLite, pytest. 기존 `crawler/page_scan.py`, `storage/store.py` 재사용.

---

## 배경 (구현자 필독)
- `storage/db.py` `_DDL`은 CREATE TABLE IF NOT EXISTS만; 새 테이블만 추가, SCHEMA_VERSION 4→5.
- `crawler/page_scan.py::run_page_scan(db_path, sellers, client_factory, num_workers, now, on_error, max_hours=None, circuit_breaker=None, stop_event=None) -> ScanSummary`. `sellers`는 seller_id 리스트. 완료 셀러는 `summary.sellers_scanned`, skip(느린 페이지/실패)은 reconcile 안 됨.
- `revisit_state(item_id, tier, ...)`; `items(item_id, seller_id, status, ...)`; `orders(seller_id, sale_date 'YYYY/MM/DD', ...)`; `sellers(seller_id, ...)`.
- 시각: `storage/store.py::now_iso()` KST ISO. `datetime.fromisoformat`. 문자열 사전식=시간순.
- 커밋 `main`.

## File Structure
| 파일 | 책임 |
|---|---|
| `storage/db.py` (수정) | `seller_scan_state` 테이블(v5) |
| `crawler/scan_scheduler.py` (신규) | `classify_seller_tier`, `SELLER_SCAN_INTERVAL_DAYS`, `next_scan_at_from`, `run_value_scan` |
| `storage/scan_repo.py` (신규) | `recompute_seller_values`, `get_due_sellers`, `mark_seller_scanned` |
| tests | `tests/test_scan_scheduler.py`, `tests/test_scan_repo.py` |

---

## Task 1: seller_scan_state 스키마 (v5)

**Files:** Modify `storage/db.py`; Test `tests/test_scan_repo.py`

- [ ] **Step 1: failing test** — create `tests/test_scan_repo.py`:
```python
from storage.db import connect, init_schema


def _mem():
    conn = connect(":memory:"); init_schema(conn); return conn


def test_seller_scan_state_table_exists():
    conn = _mem()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(seller_scan_state)")}
    assert cols == {"seller_id", "value_tier", "value_score", "last_scanned_at", "next_scan_at"}
```
- [ ] **Step 2:** `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_scan_repo.py -v` → FAIL.
- [ ] **Step 3:** In `storage/db.py` set `SCHEMA_VERSION = 5`; append to `_DDL`:
```sql

CREATE TABLE IF NOT EXISTS seller_scan_state (
  seller_id       TEXT PRIMARY KEY,
  value_tier      TEXT,
  value_score     INTEGER,
  last_scanned_at TEXT,
  next_scan_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_seller_scan_next ON seller_scan_state(next_scan_at);
```
Also update the existing schema-version test in `tests/test_db.py` (`test_schema_version_is_4` → `_is_5`, assert 5).
- [ ] **Step 4:** tests pass; full suite `-q` green.
- [ ] **Step 5:** `git add storage/db.py tests/test_scan_repo.py tests/test_db.py && git commit -m "feat(scan): seller_scan_state table (schema v5)"`

---

## Task 2: 순수 로직 — 티어 판정 + 주기

**Files:** Create `crawler/scan_scheduler.py`; Test `tests/test_scan_scheduler.py`

- [ ] **Step 1: failing tests** — create `tests/test_scan_scheduler.py`:
```python
from crawler.scan_scheduler import (
    classify_seller_tier, SELLER_SCAN_INTERVAL_DAYS, next_scan_at_from)


def test_classify_seller_tier():
    # HIGH: hot_warm>=5 OR recent_orders>=3
    assert classify_seller_tier(hot_warm=5, recent_orders=0) == "HIGH"
    assert classify_seller_tier(hot_warm=0, recent_orders=3) == "HIGH"
    # MID: hot_warm>=1 OR recent_orders>=1
    assert classify_seller_tier(hot_warm=1, recent_orders=0) == "MID"
    assert classify_seller_tier(hot_warm=0, recent_orders=1) == "MID"
    # LOW
    assert classify_seller_tier(hot_warm=0, recent_orders=0) == "LOW"


def test_intervals_and_next():
    assert SELLER_SCAN_INTERVAL_DAYS == {"HIGH": 1, "MID": 4, "LOW": 21}
    assert next_scan_at_from("2026-07-01T00:00:00+09:00", "HIGH") == "2026-07-02T00:00:00+09:00"
    assert next_scan_at_from("2026-07-01T00:00:00+09:00", "MID") == "2026-07-05T00:00:00+09:00"
    assert next_scan_at_from("2026-07-01T00:00:00+09:00", "LOW") == "2026-07-22T00:00:00+09:00"
```
- [ ] **Step 2:** run → FAIL (ModuleNotFoundError).
- [ ] **Step 3:** create `crawler/scan_scheduler.py`:
```python
"""Pure logic for value-prioritized seller listing scan cadence."""
from datetime import datetime, timedelta

SELLER_SCAN_INTERVAL_DAYS = {"HIGH": 1, "MID": 4, "LOW": 21}


def classify_seller_tier(hot_warm: int, recent_orders: int) -> str:
    """Composite value tier: product value (HOT/WARM item count) + recent orders."""
    if hot_warm >= 5 or recent_orders >= 3:
        return "HIGH"
    if hot_warm >= 1 or recent_orders >= 1:
        return "MID"
    return "LOW"


def next_scan_at_from(last_scanned_at: str, tier: str) -> str:
    dt = datetime.fromisoformat(last_scanned_at) + timedelta(days=SELLER_SCAN_INTERVAL_DAYS[tier])
    return dt.isoformat()
```
- [ ] **Step 4:** tests pass.
- [ ] **Step 5:** `git add crawler/scan_scheduler.py tests/test_scan_scheduler.py && git commit -m "feat(scan): seller value tier + scan intervals"`

---

## Task 3: scan_repo — 가치 재계산 / due / 완료

**Files:** Create `storage/scan_repo.py`; Test `tests/test_scan_repo.py`

- [ ] **Step 1: failing tests** — append to `tests/test_scan_repo.py`:
```python
from storage import scan_repo
from storage.items_repo import upsert_scanned_item


def _add_item(conn, iid, sid, now="2026-06-01T00:00:00+09:00"):
    upsert_scanned_item(conn, item_id=iid, seller_id=sid, name="n", price=100, now=now)


def _add_seller(conn, sid):
    conn.execute("INSERT INTO sellers (seller_id) VALUES (?)", (sid,))


def _set_tier(conn, iid, tier):
    conn.execute("INSERT INTO revisit_state (item_id, tier, base_tier, last_observed_at, "
                 "next_revisit_at, obs_count, last_velocity) VALUES (?,?,?,?,?,?,?)",
                 (iid, tier, tier, "x", "y", 1, None))


def test_recompute_and_due(tmp_path):
    conn = connect(":memory:"); init_schema(conn)
    for sid in ("hi", "mid", "low"):
        _add_seller(conn, sid)
    # hi: 5 HOT items
    for i in range(5):
        _add_item(conn, f"h{i}", "hi"); _set_tier(conn, f"h{i}", "HOT")
    # mid: 1 WARM item
    _add_item(conn, "m0", "mid"); _set_tier(conn, "m0", "WARM")
    # low: only COLD
    _add_item(conn, "l0", "low"); _set_tier(conn, "l0", "COLD")

    now = "2026-07-01T00:00:00+09:00"
    scan_repo.recompute_seller_values(conn, now=now, recent_cutoff="2026/06/01")
    tiers = dict(conn.execute("SELECT seller_id, value_tier FROM seller_scan_state"))
    assert tiers == {"hi": "HIGH", "mid": "MID", "low": "LOW"}
    # all new → next_scan_at = now → all due
    due = scan_repo.get_due_sellers(conn, now=now, limit=10)
    assert set(due) == {"hi", "mid", "low"}
    # HIGH first in ordering
    assert due[0] == "hi"


def test_mark_seller_scanned_advances(tmp_path):
    conn = connect(":memory:"); init_schema(conn)
    _add_seller(conn, "hi")
    scan_repo.recompute_seller_values(conn, now="2026-07-01T00:00:00+09:00", recent_cutoff="2026/06/01")
    scan_repo.mark_seller_scanned(conn, "hi", tier="HIGH", now="2026-07-01T00:00:00+09:00")
    nxt = conn.execute("SELECT next_scan_at FROM seller_scan_state WHERE seller_id='hi'").fetchone()[0]
    assert nxt == "2026-07-02T00:00:00+09:00"   # HIGH +1d
    # no longer due at now
    assert scan_repo.get_due_sellers(conn, now="2026-07-01T00:00:00+09:00", limit=10) == []
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** create `storage/scan_repo.py`:
```python
"""seller_scan_state CRUD + value recomputation + due selection."""
import sqlite3

from crawler.scan_scheduler import classify_seller_tier, next_scan_at_from

_TIER_RANK = {"HIGH": 0, "MID": 1, "LOW": 2}


def recompute_seller_values(conn: sqlite3.Connection, now: str, recent_cutoff: str) -> None:
    """Recompute each seller's value tier from HOT/WARM item counts + recent orders.

    recent_cutoff: 'YYYY/MM/DD' — orders with sale_date >= cutoff count as recent.
    New sellers (no scan state) get next_scan_at = now (immediately due). Existing
    sellers keep their next_scan_at; only tier/score refreshed.
    """
    hot_warm = {r[0]: r[1] for r in conn.execute(
        "SELECT i.seller_id, COUNT(*) FROM revisit_state r JOIN items i ON i.item_id = r.item_id "
        "WHERE r.tier IN ('HOT','WARM') GROUP BY i.seller_id")}
    recent = {r[0]: r[1] for r in conn.execute(
        "SELECT seller_id, COUNT(*) FROM orders WHERE sale_date >= ? GROUP BY seller_id",
        (recent_cutoff,))}
    existing = {r[0] for r in conn.execute("SELECT seller_id FROM seller_scan_state")}
    for (sid,) in conn.execute("SELECT seller_id FROM sellers"):
        hw = hot_warm.get(sid, 0)
        ro = recent.get(sid, 0)
        tier = classify_seller_tier(hw, ro)
        score = hw * 10 + ro
        if sid in existing:
            conn.execute("UPDATE seller_scan_state SET value_tier=?, value_score=? WHERE seller_id=?",
                         (tier, score, sid))
        else:
            conn.execute(
                "INSERT INTO seller_scan_state (seller_id, value_tier, value_score, "
                "last_scanned_at, next_scan_at) VALUES (?,?,?,?,?)",
                (sid, tier, score, None, now))


def get_due_sellers(conn: sqlite3.Connection, now: str, limit: int) -> list[str]:
    """Sellers due (next_scan_at <= now), highest tier first then most overdue."""
    rows = conn.execute(
        "SELECT seller_id, value_tier FROM seller_scan_state WHERE next_scan_at <= ? "
        "ORDER BY CASE value_tier WHEN 'HIGH' THEN 0 WHEN 'MID' THEN 1 ELSE 2 END, "
        "next_scan_at ASC LIMIT ?",
        (now, int(limit)))
    return [r[0] for r in rows]


def mark_seller_scanned(conn: sqlite3.Connection, seller_id: str, tier: str, now: str) -> None:
    """Advance a completed seller's schedule by its tier interval."""
    conn.execute(
        "UPDATE seller_scan_state SET last_scanned_at=?, next_scan_at=? WHERE seller_id=?",
        (now, next_scan_at_from(now, tier), seller_id))
```
- [ ] **Step 4:** tests pass; full suite `-q` green.
- [ ] **Step 5:** `git add storage/scan_repo.py tests/test_scan_repo.py && git commit -m "feat(scan): scan_repo value recompute + due selection + mark scanned"`

---

## Task 4: run_value_scan 오케스트레이션 + CLI/오케스트레이터 연결

**Files:** Modify `crawler/scan_scheduler.py`, `scan_cli.py`, `orchestrator.py`; Test `tests/test_scan_scheduler.py`

- [ ] **Step 1: failing test** — append to `tests/test_scan_scheduler.py`:
```python
from storage.db import connect, init_schema
from storage.items_repo import upsert_scanned_item
from crawler.scan_scheduler import run_value_scan


def test_run_value_scan_scans_only_due_and_advances(tmp_path, monkeypatch):
    db = tmp_path / "v.db"
    conn = connect(str(db)); init_schema(conn)
    conn.execute("INSERT INTO sellers (seller_id) VALUES ('s1')")
    upsert_scanned_item(conn, item_id="a", seller_id="s1", name="n", price=1,
                        now="2026-06-01T00:00:00+09:00")
    conn.close()

    scanned = {}
    def fake_run_page_scan(**kw):
        from crawler.page_scan import ScanSummary
        scanned["sellers"] = list(kw["sellers"])
        return ScanSummary(sellers_scanned=len(kw["sellers"]))
    import crawler.scan_scheduler as ss
    monkeypatch.setattr(ss, "run_page_scan", fake_run_page_scan)

    summary = run_value_scan(
        db_path=str(db), client_factory=lambda: None, num_workers=2,
        now="2026-07-01T00:00:00+09:00", on_error=lambda **kw: None,
        recent_cutoff="2026/06/01")
    assert scanned["sellers"] == ["s1"]      # s1 is new → due → scanned
    conn = connect(str(db))
    nxt = conn.execute("SELECT next_scan_at, value_tier FROM seller_scan_state WHERE seller_id='s1'").fetchone()
    assert nxt[0] != "2026-07-01T00:00:00+09:00"   # advanced past now
```
- [ ] **Step 2:** run → FAIL (ImportError: run_value_scan).
- [ ] **Step 3:** add to `crawler/scan_scheduler.py` (imports + function):
```python
from pathlib import Path
from typing import Callable

from crawler.page_scan import run_page_scan
from storage.db import connect, init_schema
from storage import scan_repo


def run_value_scan(
    db_path, client_factory: Callable[[], object], num_workers: int, now: str,
    on_error: Callable[..., None], recent_cutoff: str,
    max_hours: float | None = None, circuit_breaker=None, stop_event=None,
    due_limit: int = 100000,
):
    """One value-prioritized ③a pass: recompute seller values, scan due sellers
    (highest value first) within the time cap via run_page_scan, advance the
    schedule of sellers that completed. Sellers skipped (slow pages) keep their
    due time → retried next run (HIGH again soon)."""
    db_path = Path(db_path)
    conn = connect(db_path); init_schema(conn)
    scan_repo.recompute_seller_values(conn, now=now, recent_cutoff=recent_cutoff)
    due = scan_repo.get_due_sellers(conn, now=now, limit=due_limit)
    tier_of = dict(conn.execute(
        "SELECT seller_id, value_tier FROM seller_scan_state"))
    conn.close()
    if not due:
        return run_page_scan.__globals__["ScanSummary"]() if False else _empty_summary()

    summary = run_page_scan(
        db_path=db_path, sellers=due, client_factory=client_factory,
        num_workers=num_workers, now=now, on_error=on_error,
        max_hours=max_hours, circuit_breaker=circuit_breaker, stop_event=stop_event)

    # advance schedule ONLY for sellers whose scan completed this run.
    conn = connect(db_path)
    scanned_ids = {r[0] for r in conn.execute(
        "SELECT DISTINCT seller_id FROM items WHERE last_seen_at = ?", (now,))}
    for sid in due:
        if sid in scanned_ids:
            scan_repo.mark_seller_scanned(conn, sid, tier=tier_of.get(sid, "LOW"), now=now)
    conn.close()
    return summary


def _empty_summary():
    from crawler.page_scan import ScanSummary
    return ScanSummary()
```
> 완료 판정: run_page_scan은 스캔한 셀러의 아이템 `last_seen_at`을 `now`로 upsert하므로, `items.last_seen_at == now`인 seller_id 집합 = 이번에 실제 스캔된 셀러. 그 셀러만 주기 전진(skip된 느린 셀러는 다음 run 재시도). 단순화를 위해 `_empty_summary` 헬퍼로 due 없을 때 빈 요약 반환.

정리(구현 시): 위 `if not due:` 분기는 `return _empty_summary()`로 단순화하고 `run_page_scan.__globals__[...]` 표현은 넣지 말 것(설명 잔재). 최종 코드는:
```python
    if not due:
        return _empty_summary()
```

- [ ] **Step 4:** test passes; full suite `-q` green.

- [ ] **Step 5: CLI/오케스트레이터 연결**
`scan_cli.py`: `--value` 플래그 추가 시 `run_value_scan`(recent_cutoff = 30일 전 `now_iso()` 날짜를 'YYYY/MM/DD'로 변환) 사용; 기본은 기존 전수 스캔 유지.
`orchestrator.py`의 `stage_scan`: `run_page_scan(all sellers)` 대신 `run_value_scan(...)` 호출로 교체(recent_cutoff 계산 포함). 나머지 인자(cb/max_hours/stop_event) 그대로 전달.
recent_cutoff 계산 예:
```python
from datetime import datetime, timedelta
from storage.store import KST
cutoff = (datetime.now(KST) - timedelta(days=30)).strftime("%Y/%m/%d")
```
(`storage/store.py`에 `KST`가 있음. 없으면 now_iso()[:10]에서 파생.)

- [ ] **Step 6: smoke (no full run)**
`PYTHONPATH="$PWD" .venv/bin/python3 scan_cli.py --help` (--value 표시), `python3 -c "import scan_cli, orchestrator, crawler.scan_scheduler, storage.scan_repo"` clean. 전체 `-q` 통과.

- [ ] **Step 7:** commit `git add crawler/scan_scheduler.py scan_cli.py orchestrator.py tests/test_scan_scheduler.py && git commit -m "feat(scan): run_value_scan + CLI/orchestrator wiring (③a value-prioritized)"`

---

## Self-Review
- 스키마 v5 → Task1. 순수 티어/주기 → Task2. 재계산/due/완료 → Task3. 오케스트레이션+연결 → Task4.
- 스킵 아님: 완료 셀러만 주기 전진, skip 셀러는 due 유지→재시도(spec 일치).
- ①② 불변, 실행기 run_page_scan 재사용, 페이지 분산 유지.
- 타입 일관: classify_seller_tier(hot_warm, recent_orders), next_scan_at_from(last, tier), SELLER_SCAN_INTERVAL_DAYS 키(HIGH/MID/LOW).
- recent_cutoff는 'YYYY/MM/DD'(sale_date 형식과 일치, 문자열 비교).
- 바운드 타임아웃(hang)은 이 계획 범위 밖(후속) — 가치 우선순위로 저가치 고래 노출 급감이 1차 완화. 필요 시 별도.
