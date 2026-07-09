# Orders / Sellers JSON → SQLite 마이그레이션 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** orders, sellers, orders 워터마크/실행메타를 기존 `data/items.db`(SQLite)로 이전하고, 기존 JSON 파일은 백업 후 코드에서 제거한다.

**Architecture:** items가 쓰는 `storage/items_repo.py` 패턴(열린 `sqlite3.Connection`에 동작하는 함수 모음)을 그대로 따른다. `db.py`에 테이블 4개(orders, order_watermarks, order_run_meta, sellers)를 추가하고, `orders_repo.py`·`sellers_repo.py`를 신규 작성한다. 멀티워커 쓰기는 기존 `crawler/monitor.py`와 동일하게 공유 connection + `threading.Lock()`으로 직렬화한다. 일회성 스크립트로 기존 데이터를 적재한 뒤 원본을 `.bak`으로 백업한다.

**Tech Stack:** Python 3, sqlite3(표준 라이브러리), pytest 8.3.3.

**Spec:** [docs/superpowers/specs/2026-06-11-orders-sellers-db-migration-design.md](../specs/2026-06-11-orders-sellers-db-migration-design.md)

---

## File Structure

- `storage/db.py` (수정) — `_DDL`에 테이블 4개 + 인덱스 추가, `SCHEMA_VERSION` 2로.
- `storage/orders_repo.py` (신규) — orders insert, watermark get/upsert/load_all, run_meta save/load.
- `storage/sellers_repo.py` (신규) — sellers upsert(first_seen_at 보존)/load/get.
- `scripts/migrate_json_to_db.py` (신규) — 일회성 마이그레이션 + 백업.
- `main.py` (수정) — `run_crawl_orders`, `run_crawl_sellers`를 repo 호출로 교체.
- `monitor_cli.py` (수정) — sellers를 DB에서 로드.
- `storage/orders_store.py` (삭제), `storage/store.py` (수정 — sellers 메서드 제거).
- `tests/test_orders_repo.py`, `tests/test_sellers_repo.py`, `tests/test_migration.py` (신규).
- `tests/test_db.py` (수정) — 새 테이블/인덱스 존재 확인.

**구현 순서 주의:** 스펙의 "진행 순서"는 우선순위(워터마크 효과가 가장 큼) 기준이다. 하지만 `main.py`의 `run_crawl_orders`는 orders append와 watermark를 함께 쓰므로, main.py 전환(Task 7)은 워터마크+orders+run_meta를 한 번에 교체하는 것이 깔끔하다. 따라서 repo는 워터마크 함수부터 만들되, main.py 커밋은 원자적으로 진행한다.

---

## Task 1: 스키마 추가 (db.py)

**Files:**
- Modify: `storage/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_db.py` 끝에 추가

```python
def test_init_schema_creates_orders_sellers_tables(tmp_path: Path):
    conn = connect(tmp_path / "test.db")
    try:
        init_schema(conn)
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"orders", "order_watermarks", "order_run_meta", "sellers"}.issubset(names)
        idx = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {"idx_orders_seller", "idx_orders_sale_date", "idx_orders_item"}.issubset(idx)
    finally:
        conn.close()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_db.py::test_init_schema_creates_orders_sellers_tables -v`
Expected: FAIL (`order_watermarks` 등 테이블 없음 → assert 실패)

- [ ] **Step 3: 최소 구현** — `storage/db.py`의 `_DDL` 문자열 끝(닫는 `"""` 직전)에 아래 DDL을 추가하고, `SCHEMA_VERSION = 1`을 `SCHEMA_VERSION = 2`로 변경

```sql
CREATE TABLE IF NOT EXISTS orders (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  seller_id    TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  item_name    TEXT,
  item_url     TEXT,
  qty          INTEGER,
  sale_date    TEXT NOT NULL,
  collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_seller    ON orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_orders_sale_date ON orders(sale_date);
CREATE INDEX IF NOT EXISTS idx_orders_item      ON orders(item_id);

CREATE TABLE IF NOT EXISTS order_watermarks (
  seller_id              TEXT PRIMARY KEY,
  signature_json         TEXT NOT NULL,
  last_run_at            TEXT,
  pages_scanned_last_run INTEGER,
  orders_added_last_run  INTEGER
);

CREATE TABLE IF NOT EXISTS order_run_meta (
  id                  INTEGER PRIMARY KEY CHECK (id = 1),
  last_run_at         TEXT,
  last_run_stats_json TEXT
);

CREATE TABLE IF NOT EXISTS sellers (
  seller_id      TEXT PRIMARY KEY,
  seller_name    TEXT,
  seller_type    TEXT,
  seller_url     TEXT,
  country        TEXT,
  follower_count INTEGER,
  listing_count  INTEGER,
  order_count    INTEGER,
  first_seen_at  TEXT,
  updated_at     TEXT
);
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS (기존 `test_init_schema_creates_tables`는 `SCHEMA_VERSION == 2`로 자동 일치 — `assert version == SCHEMA_VERSION` 그대로 통과)

- [ ] **Step 5: 커밋**

```bash
git add storage/db.py tests/test_db.py
git commit -m "feat(db): add orders, order_watermarks, order_run_meta, sellers tables (schema v2)"
```

---

## Task 2: orders_repo — insert_orders

**Files:**
- Create: `storage/orders_repo.py`
- Test: `tests/test_orders_repo.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_orders_repo.py` 신규

```python
from pathlib import Path

from storage.db import connect, init_schema
from storage import orders_repo


def make_conn(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    return conn


def test_insert_orders_appends_rows(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.insert_orders(conn, [
        {"seller_id": "S1", "item_id": "100", "item_name": "A", "item_url": "u1",
         "qty": 1, "sale_date": "2026/06/09", "collected_at": "2026-06-09T18:00:00+09:00"},
        {"seller_id": "S1", "item_id": "100", "item_name": "A", "item_url": "u1",
         "qty": 1, "sale_date": "2026/06/09", "collected_at": "2026-06-09T18:00:00+09:00"},
    ])
    rows = conn.execute("SELECT seller_id, item_id, qty FROM orders ORDER BY id").fetchall()
    assert len(rows) == 2  # 동일 필드라도 별개 주문 — UNIQUE 제약 없음
    assert rows[0]["seller_id"] == "S1"
    assert rows[0]["qty"] == 1


def test_insert_orders_empty_is_noop(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.insert_orders(conn, [])
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_orders_repo.py -v`
Expected: FAIL (`ModuleNotFoundError: storage.orders_repo`)

- [ ] **Step 3: 최소 구현** — `storage/orders_repo.py` 신규

```python
"""Orders, watermarks, and run-meta CRUD. Operates on an open sqlite3 connection."""
import json
import sqlite3


def insert_orders(conn: sqlite3.Connection, orders: list[dict]) -> None:
    """Append order rows. No dedup — the caller's watermark prevents re-collection."""
    if not orders:
        return
    conn.executemany(
        """
        INSERT INTO orders (
          seller_id, item_id, item_name, item_url, qty, sale_date, collected_at
        ) VALUES (
          :seller_id, :item_id, :item_name, :item_url, :qty, :sale_date, :collected_at
        )
        """,
        [
            {
                "seller_id": o["seller_id"],
                "item_id": o["item_id"],
                "item_name": o.get("item_name"),
                "item_url": o.get("item_url"),
                "qty": o.get("qty"),
                "sale_date": o["sale_date"],
                "collected_at": o["collected_at"],
            }
            for o in orders
        ],
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_orders_repo.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add storage/orders_repo.py tests/test_orders_repo.py
git commit -m "feat(orders_repo): insert_orders append-only writer"
```

---

## Task 3: orders_repo — 워터마크 get/upsert/load_all

**Files:**
- Modify: `storage/orders_repo.py`
- Test: `tests/test_orders_repo.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_orders_repo.py`에 추가

```python
def test_upsert_and_get_watermark_roundtrip(tmp_path: Path):
    conn = make_conn(tmp_path)
    sig = [["2026/06/09", "100", 1], ["2026/06/08", "200", 2]]
    orders_repo.upsert_watermark(
        conn, "S1", signature=sig,
        last_run_at="2026-06-09T18:00:00+09:00", pages_scanned=3, orders_added=5,
    )
    wm = orders_repo.get_watermark(conn, "S1")
    assert wm["signature"] == sig
    assert wm["last_run_at"] == "2026-06-09T18:00:00+09:00"
    assert wm["pages_scanned_last_run"] == 3
    assert wm["orders_added_last_run"] == 5


def test_upsert_watermark_overwrites(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.upsert_watermark(conn, "S1", [["a"]], "t1", 1, 1)
    orders_repo.upsert_watermark(conn, "S1", [["b"]], "t2", 9, 9)
    wm = orders_repo.get_watermark(conn, "S1")
    assert wm["signature"] == [["b"]]
    assert wm["pages_scanned_last_run"] == 9
    assert conn.execute("SELECT COUNT(*) FROM order_watermarks").fetchone()[0] == 1


def test_get_watermark_missing_returns_none(tmp_path: Path):
    conn = make_conn(tmp_path)
    assert orders_repo.get_watermark(conn, "NOPE") is None


def test_load_all_watermarks(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.upsert_watermark(conn, "S1", [["a"]], "t1", 1, 1)
    orders_repo.upsert_watermark(conn, "S2", [["b"]], "t2", 2, 2)
    allwm = orders_repo.load_all_watermarks(conn)
    assert set(allwm) == {"S1", "S2"}
    assert allwm["S2"]["signature"] == [["b"]]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_orders_repo.py -k watermark -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'upsert_watermark'`)

- [ ] **Step 3: 최소 구현** — `storage/orders_repo.py`에 추가

```python
def upsert_watermark(
    conn: sqlite3.Connection,
    seller_id: str,
    signature: list,
    last_run_at: str | None,
    pages_scanned: int | None,
    orders_added: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO order_watermarks (
          seller_id, signature_json, last_run_at,
          pages_scanned_last_run, orders_added_last_run
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(seller_id) DO UPDATE SET
          signature_json         = excluded.signature_json,
          last_run_at            = excluded.last_run_at,
          pages_scanned_last_run = excluded.pages_scanned_last_run,
          orders_added_last_run  = excluded.orders_added_last_run
        """,
        (
            seller_id,
            json.dumps(signature, ensure_ascii=False),
            last_run_at,
            pages_scanned,
            orders_added,
        ),
    )


def _row_to_watermark(row: sqlite3.Row) -> dict:
    return {
        "signature": json.loads(row["signature_json"]),
        "last_run_at": row["last_run_at"],
        "pages_scanned_last_run": row["pages_scanned_last_run"],
        "orders_added_last_run": row["orders_added_last_run"],
    }


def get_watermark(conn: sqlite3.Connection, seller_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM order_watermarks WHERE seller_id = ?", (seller_id,)
    ).fetchone()
    return _row_to_watermark(row) if row is not None else None


def load_all_watermarks(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM order_watermarks").fetchall()
    return {row["seller_id"]: _row_to_watermark(row) for row in rows}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_orders_repo.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add storage/orders_repo.py tests/test_orders_repo.py
git commit -m "feat(orders_repo): watermark get/upsert/load_all"
```

---

## Task 4: orders_repo — run_meta save/load

**Files:**
- Modify: `storage/orders_repo.py`
- Test: `tests/test_orders_repo.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_orders_repo.py`에 추가

```python
def test_save_and_load_run_meta(tmp_path: Path):
    conn = make_conn(tmp_path)
    stats = {"sellers_scanned": 401, "orders_added": 12, "errors": 0}
    orders_repo.save_run_meta(conn, "2026-06-09T18:00:00+09:00", stats)
    meta = orders_repo.load_run_meta(conn)
    assert meta["last_run_at"] == "2026-06-09T18:00:00+09:00"
    assert meta["last_run_stats"] == stats


def test_save_run_meta_overwrites_single_row(tmp_path: Path):
    conn = make_conn(tmp_path)
    orders_repo.save_run_meta(conn, "t1", {"a": 1})
    orders_repo.save_run_meta(conn, "t2", {"a": 2})
    assert conn.execute("SELECT COUNT(*) FROM order_run_meta").fetchone()[0] == 1
    assert orders_repo.load_run_meta(conn)["last_run_at"] == "t2"


def test_load_run_meta_empty(tmp_path: Path):
    conn = make_conn(tmp_path)
    meta = orders_repo.load_run_meta(conn)
    assert meta == {"last_run_at": None, "last_run_stats": None}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_orders_repo.py -k run_meta -v`
Expected: FAIL (`AttributeError: ... 'save_run_meta'`)

- [ ] **Step 3: 최소 구현** — `storage/orders_repo.py`에 추가

```python
def save_run_meta(
    conn: sqlite3.Connection, last_run_at: str | None, stats: dict | None
) -> None:
    conn.execute(
        """
        INSERT INTO order_run_meta (id, last_run_at, last_run_stats_json)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          last_run_at         = excluded.last_run_at,
          last_run_stats_json = excluded.last_run_stats_json
        """,
        (
            last_run_at,
            json.dumps(stats, ensure_ascii=False) if stats is not None else None,
        ),
    )


def load_run_meta(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM order_run_meta WHERE id = 1").fetchone()
    if row is None:
        return {"last_run_at": None, "last_run_stats": None}
    return {
        "last_run_at": row["last_run_at"],
        "last_run_stats": (
            json.loads(row["last_run_stats_json"])
            if row["last_run_stats_json"]
            else None
        ),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_orders_repo.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add storage/orders_repo.py tests/test_orders_repo.py
git commit -m "feat(orders_repo): order_run_meta save/load"
```

---

## Task 5: sellers_repo — upsert/load/get

**Files:**
- Create: `storage/sellers_repo.py`
- Test: `tests/test_sellers_repo.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_sellers_repo.py` 신규

```python
from pathlib import Path

from storage.db import connect, init_schema
from storage import sellers_repo


def make_conn(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    return conn


def _seller(sid, name, first_seen, updated):
    return {
        "seller_id": sid, "seller_name": name, "seller_type": "PERSONAL SHOPPER",
        "seller_url": f"https://www.buyma.com/buyer/{sid}/sales_1.html",
        "country": "한국", "follower_count": 10, "listing_count": 20, "order_count": 30,
        "first_seen_at": first_seen, "updated_at": updated,
    }


def test_upsert_and_load_sellers(tmp_path: Path):
    conn = make_conn(tmp_path)
    sellers_repo.upsert_sellers(conn, {
        "S1": _seller("S1", "INSEOUL", "t1", "t1"),
        "S2": _seller("S2", "OTHER", "t1", "t1"),
    })
    loaded = sellers_repo.load_sellers(conn)
    assert set(loaded) == {"S1", "S2"}
    assert loaded["S1"]["seller_name"] == "INSEOUL"
    assert loaded["S1"]["follower_count"] == 10


def test_upsert_preserves_first_seen_at(tmp_path: Path):
    conn = make_conn(tmp_path)
    sellers_repo.upsert_sellers(conn, {"S1": _seller("S1", "INSEOUL", "2026-06-09", "2026-06-09")})
    # 재수집: first_seen_at는 보존, 나머지는 갱신
    sellers_repo.upsert_sellers(conn, {"S1": _seller("S1", "INSEOUL-RENAMED", "2026-06-11", "2026-06-11")})
    row = sellers_repo.get_seller(conn, "S1")
    assert row["first_seen_at"] == "2026-06-09"      # 보존
    assert row["seller_name"] == "INSEOUL-RENAMED"   # 갱신
    assert row["updated_at"] == "2026-06-11"         # 갱신


def test_get_seller_missing_returns_none(tmp_path: Path):
    conn = make_conn(tmp_path)
    assert sellers_repo.get_seller(conn, "NOPE") is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_sellers_repo.py -v`
Expected: FAIL (`ModuleNotFoundError: storage.sellers_repo`)

- [ ] **Step 3: 최소 구현** — `storage/sellers_repo.py` 신규

```python
"""Sellers CRUD. Operates on an open sqlite3 connection."""
import sqlite3

_COLS = [
    "seller_id", "seller_name", "seller_type", "seller_url", "country",
    "follower_count", "listing_count", "order_count", "first_seen_at", "updated_at",
]


def upsert_sellers(conn: sqlite3.Connection, sellers: dict[str, dict]) -> None:
    """Insert/update sellers. first_seen_at is preserved from the existing row when present."""
    for seller_id, data in sellers.items():
        existing = conn.execute(
            "SELECT first_seen_at FROM sellers WHERE seller_id = ?", (seller_id,)
        ).fetchone()
        row = {c: data.get(c) for c in _COLS}
        row["seller_id"] = seller_id
        if existing is not None and existing["first_seen_at"] is not None:
            row["first_seen_at"] = existing["first_seen_at"]
        conn.execute(
            """
            INSERT INTO sellers (
              seller_id, seller_name, seller_type, seller_url, country,
              follower_count, listing_count, order_count, first_seen_at, updated_at
            ) VALUES (
              :seller_id, :seller_name, :seller_type, :seller_url, :country,
              :follower_count, :listing_count, :order_count, :first_seen_at, :updated_at
            )
            ON CONFLICT(seller_id) DO UPDATE SET
              seller_name    = excluded.seller_name,
              seller_type    = excluded.seller_type,
              seller_url     = excluded.seller_url,
              country        = excluded.country,
              follower_count = excluded.follower_count,
              listing_count  = excluded.listing_count,
              order_count    = excluded.order_count,
              first_seen_at  = excluded.first_seen_at,
              updated_at     = excluded.updated_at
            """,
            row,
        )


def load_sellers(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM sellers").fetchall()
    return {row["seller_id"]: {c: row[c] for c in _COLS} for row in rows}


def get_seller(conn: sqlite3.Connection, seller_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sellers WHERE seller_id = ?", (seller_id,)
    ).fetchone()
    return {c: row[c] for c in _COLS} if row is not None else None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_sellers_repo.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add storage/sellers_repo.py tests/test_sellers_repo.py
git commit -m "feat(sellers_repo): upsert/load/get with first_seen_at preservation"
```

---

## Task 6: 마이그레이션 스크립트

**Files:**
- Create: `scripts/migrate_json_to_db.py`
- Create: `scripts/__init__.py` (빈 파일 — 테스트에서 import 가능하게)
- Test: `tests/test_migration.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_migration.py` 신규

```python
import json
from pathlib import Path

import pytest

from storage.db import connect, init_schema
from scripts.migrate_json_to_db import migrate


def _write_sources(data_dir: Path):
    (data_dir / "orders.jsonl").write_text(
        json.dumps({"sale_date": "2026/06/09", "item_id": "100", "qty": 1,
                    "item_name": "A", "item_url": "u", "seller_id": "S1",
                    "collected_at": "2026-06-09T18:00:00+09:00"}, ensure_ascii=False) + "\n"
        + json.dumps({"sale_date": "2026/06/09", "item_id": "200", "qty": 2,
                      "item_name": "B", "item_url": "u2", "seller_id": "S2",
                      "collected_at": "2026-06-09T18:00:00+09:00"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (data_dir / "sellers.json").write_text(
        json.dumps({"S1": {"seller_id": "S1", "seller_name": "INSEOUL",
                           "first_seen_at": "2026-06-09", "updated_at": "2026-06-09"}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "orders_config.json").write_text(
        json.dumps({
            "watermarks": {"S1": {"signature": [["2026/06/09", "100", 1]],
                                  "last_run_at": "2026-06-09T18:00:00+09:00",
                                  "pages_scanned_last_run": 3, "orders_added_last_run": 5}},
            "last_run_at": "2026-06-09T18:00:00+09:00",
            "last_run_stats": {"sellers_scanned": 1, "errors": 0},
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_migrate_loads_all_and_backs_up(tmp_path: Path):
    _write_sources(tmp_path)
    stats = migrate(tmp_path)
    assert stats == {"orders": 2, "sellers": 1, "watermarks": 1}

    conn = connect(tmp_path / "items.db")
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM order_watermarks").fetchone()[0] == 1
    meta = conn.execute("SELECT last_run_at FROM order_run_meta WHERE id=1").fetchone()
    assert meta["last_run_at"] == "2026-06-09T18:00:00+09:00"
    conn.close()

    # 원본은 .bak으로 백업, 원본 경로는 사라짐
    assert (tmp_path / "orders.jsonl.bak").exists()
    assert not (tmp_path / "orders.jsonl").exists()
    assert (tmp_path / "sellers.json.bak").exists()
    assert (tmp_path / "orders_config.json.bak").exists()


def test_migrate_aborts_if_already_run(tmp_path: Path):
    _write_sources(tmp_path)
    migrate(tmp_path)
    # 2회차: 백업본만 남아 원본이 없지만, DB 테이블이 비어있지 않으므로 중단
    _write_sources(tmp_path)  # 원본 재생성
    with pytest.raises(SystemExit):
        migrate(tmp_path)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_migration.py -v`
Expected: FAIL (`ModuleNotFoundError: scripts.migrate_json_to_db`)

- [ ] **Step 3: 최소 구현** — `scripts/__init__.py`(빈 파일) + `scripts/migrate_json_to_db.py` 신규

```python
"""One-time migration: orders.jsonl + sellers.json + orders_config.json -> items.db.

Idempotency: aborts (SystemExit) if any target table already has rows.
On success, renames each source file to <name>.bak.
"""
import json
import sys
from pathlib import Path

from storage.db import connect, init_schema
from storage import orders_repo, sellers_repo

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def migrate(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    db_path = data_dir / "items.db"
    orders_path = data_dir / "orders.jsonl"
    sellers_path = data_dir / "sellers.json"
    config_path = data_dir / "orders_config.json"

    conn = connect(db_path)
    init_schema(conn)

    for table in ("orders", "order_watermarks", "order_run_meta", "sellers"):
        if _count(conn, table) > 0:
            conn.close()
            raise SystemExit(
                f"Aborting: table '{table}' is not empty. Migration likely already ran."
            )

    stats = {"orders": 0, "sellers": 0, "watermarks": 0}

    if orders_path.exists():
        orders = []
        with orders_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    orders.append(json.loads(line))
        orders_repo.insert_orders(conn, orders)
        stats["orders"] = len(orders)

    if sellers_path.exists():
        sellers = json.loads(sellers_path.read_text(encoding="utf-8"))
        sellers_repo.upsert_sellers(conn, sellers)
        stats["sellers"] = len(sellers)

    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        watermarks = cfg.get("watermarks", {})
        for sid, wm in watermarks.items():
            orders_repo.upsert_watermark(
                conn, sid,
                signature=wm.get("signature", []),
                last_run_at=wm.get("last_run_at"),
                pages_scanned=wm.get("pages_scanned_last_run"),
                orders_added=wm.get("orders_added_last_run"),
            )
        stats["watermarks"] = len(watermarks)
        orders_repo.save_run_meta(conn, cfg.get("last_run_at"), cfg.get("last_run_stats"))

    # verify counts
    assert _count(conn, "orders") == stats["orders"]
    assert _count(conn, "sellers") == stats["sellers"]
    assert _count(conn, "order_watermarks") == stats["watermarks"]
    conn.close()

    # backup sources only after successful load
    for p in (orders_path, sellers_path, config_path):
        if p.exists():
            p.rename(p.with_name(p.name + ".bak"))

    return stats


if __name__ == "__main__":
    s = migrate(DATA_DIR)
    print(
        f"Migrated: {s['orders']} orders, {s['sellers']} sellers, "
        f"{s['watermarks']} watermarks. Sources backed up to *.bak."
    )
    sys.exit(0)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_migration.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/__init__.py scripts/migrate_json_to_db.py tests/test_migration.py
git commit -m "feat(scripts): one-time JSON->SQLite migration with backup + idempotency guard"
```

---

## Task 7: main.py — run_crawl_orders를 DB로 전환

**Files:**
- Modify: `main.py` (imports, `DATA_DIR` 부근, `run_crawl_orders` 함수 전체)

이 태스크는 네트워크/스레드가 얽혀 단위 테스트가 어렵다. 검증은 (a) 전체 pytest 통과, (b) import 스모크, (c) `--dry-run` 실행으로 한다.

- [ ] **Step 1: imports 교체** — `main.py` 상단

`from storage.orders_store import OrdersStore` 줄을 삭제하고, 다음을 추가한다:

```python
from storage.db import connect, init_schema
from storage import orders_repo, sellers_repo
```

`DATA_DIR = Path(__file__).parent / "data"` 바로 아래에 추가:

```python
DB_PATH = DATA_DIR / "items.db"
```

- [ ] **Step 2: `run_crawl_orders` 본문 교체** — [main.py:115-209](main.py#L115-L209)의 함수 전체를 아래로 교체

```python
def run_crawl_orders(args) -> int:
    import threading

    store = Store(DATA_DIR)
    conn = connect(DB_PATH)
    init_schema(conn)
    db_lock = threading.Lock()

    sellers = sellers_repo.load_sellers(conn)
    if not sellers:
        logging.error("No sellers in DB. Run `crawl-sellers` first.")
        return 1

    all_watermarks = orders_repo.load_all_watermarks(conn)

    seller_ids_all = sorted(sellers.keys())
    if args.seller_id:
        if args.seller_id not in sellers:
            logging.error("Seller %s not found in DB", args.seller_id)
            return 1
        seller_ids = [args.seller_id]
    else:
        seller_ids = seller_ids_all

    watermarks: dict[str, list] = {}
    for sid in seller_ids:
        if args.full_rescan:
            watermarks[sid] = []
        else:
            sig = all_watermarks.get(sid, {}).get("signature", [])
            watermarks[sid] = [tuple(t) for t in sig]

    logging.info(
        "Starting orders crawl: %d sellers, max_pages=%d, full_rescan=%s",
        len(watermarks), args.max_pages, args.full_rescan,
    )

    timestamp = now_iso()

    def on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings):
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
        if warnings:
            for w in warnings:
                logging.warning(w)
        logging.info("seller %s: +%d orders (pages=%d)", sid, len(new_orders), pages_scanned)

    cb = CircuitBreaker(threshold=1, window_seconds=60)

    stats = crawl_all_orders_with_factory(
        client_factory=lambda: PlaywrightClient(circuit_breaker=cb),
        seller_watermarks=watermarks,
        on_seller_done=on_seller_done,
        on_error=store.append_error,
        max_pages=args.max_pages,
        num_workers=3,
        circuit_breaker=cb,
    )

    if stats.get("aborted_by_ip_block"):
        print("")
        print("=" * 64)
        print("⚠️  BUYMA returned 403 Forbidden — your IP is likely blocked.")
        print("    Check your IP address (switch networks / VPN / wait) and retry.")
        print("=" * 64)
        logging.error("Crawl aborted due to 403 Forbidden (IP block suspected)")
        errors_today = _count_errors_today(store.errors_path)
        stats["errors"] = errors_today
        if not args.dry_run:
            orders_repo.save_run_meta(conn, timestamp, stats)
        conn.close()
        return 2

    errors_today = _count_errors_today(store.errors_path)
    stats["errors"] = errors_today
    if not args.dry_run:
        orders_repo.save_run_meta(conn, timestamp, stats)

    conn.close()
    logging.info("Stats: %s", stats)
    return 0
```

주의: 워터마크 `signature`는 DB에 list-of-lists(JSON)로 저장되고, 크롤러에는 tuple 리스트로 전달해야 하므로 읽을 때 `[tuple(t) for t in sig]`, 쓸 때 `[list(t) for t in new_watermark]`로 변환한다(기존 OrdersStore 동작과 동일).

- [ ] **Step 3: import 스모크 확인**

Run: `python -c "import main"`
Expected: 출력 없이 성공 (ImportError 없음)

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `python -m pytest -q`
Expected: 모든 테스트 PASS (이 시점에 `storage/orders_store.py`는 아직 존재 — Task 10에서 제거)

- [ ] **Step 5: 커밋**

```bash
git add main.py
git commit -m "feat(main): run_crawl_orders writes orders/watermark/run_meta to SQLite"
```

---

## Task 8: main.py — run_crawl_sellers를 DB로 전환

**Files:**
- Modify: `main.py` (`run_crawl_sellers` 함수의 sellers 저장부)

- [ ] **Step 1: sellers load/merge/save 교체** — [main.py:86-104](main.py#L86-L104) 영역

함수 시작부에서 connection을 연다. `def run_crawl_sellers(args) -> int:` 다음 줄의 `store = Store(DATA_DIR)` 바로 아래에 추가:

```python
    conn = connect(DB_PATH)
    init_schema(conn)
```

그리고 기존 sellers 저장 블록을 교체한다. 아래 기존 코드:

```python
    existing = store.load_sellers()
    merged = Store.merge_sellers(existing, new_sellers_map)
```
를 다음으로 교체:
```python
    existing = sellers_repo.load_sellers(conn)
```

그리고 저장 블록(아래 기존 코드):

```python
    if args.dry_run:
        logging.info("DRY RUN: skipping save. Stats: %s", config["last_run_stats"])
    else:
        store.save_sellers(merged)
        store.save_config(config)
        logging.info(
            "Saved %d total sellers (was %d, added/updated %d)",
            len(merged),
            len(existing),
            len(new_sellers_map),
        )
```
를 다음으로 교체:
```python
    if args.dry_run:
        logging.info("DRY RUN: skipping save. Stats: %s", config["last_run_stats"])
    else:
        sellers_repo.upsert_sellers(conn, new_sellers_map)
        store.save_config(config)
        total = len(sellers_repo.load_sellers(conn))
        logging.info(
            "Saved sellers to DB (total %d, was %d, added/updated %d)",
            total,
            len(existing),
            len(new_sellers_map),
        )
    conn.close()
```

주의: `config`(max_pages 등)는 여전히 `store.save_config`로 `config.json`에 저장한다(범위 밖). `merge_sellers` 호출은 제거되고 first_seen_at 보존은 `upsert_sellers`가 담당한다.

- [ ] **Step 2: import 스모크 확인**

Run: `python -c "import main"`
Expected: 성공

- [ ] **Step 3: 전체 테스트 통과 확인**

Run: `python -m pytest -q`
Expected: 모든 테스트 PASS

- [ ] **Step 4: 커밋**

```bash
git add main.py
git commit -m "feat(main): run_crawl_sellers writes sellers to SQLite"
```

---

## Task 9: monitor_cli.py — sellers를 DB에서 로드

**Files:**
- Modify: `monitor_cli.py`

- [ ] **Step 1: sellers 로드부 교체** — [monitor_cli.py:1-60](monitor_cli.py#L1-L60)

상단 import에 추가:

```python
from storage.db import connect, init_schema
from storage import sellers_repo
```

기존 블록(아래):

```python
    if not SELLERS_PATH.exists():
        logging.error("sellers.json not found at %s — run main.py first", SELLERS_PATH)
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    sellers = json.loads(SELLERS_PATH.read_text(encoding="utf-8"))
    if args.limit_sellers:
        sellers = dict(list(sellers.items())[: args.limit_sellers])
    logging.info("Loaded %d sellers from %s", len(sellers), SELLERS_PATH)
```
를 다음으로 교체:
```python
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    _conn = connect(DB_PATH)
    init_schema(_conn)
    sellers = sellers_repo.load_sellers(_conn)
    _conn.close()
    if not sellers:
        logging.error("No sellers in DB at %s — run `python main.py crawl-sellers` first", DB_PATH)
        return 1
    if args.limit_sellers:
        sellers = dict(list(sellers.items())[: args.limit_sellers])
    logging.info("Loaded %d sellers from %s", len(sellers), DB_PATH)
```

`SELLERS_PATH = DATA_DIR / "sellers.json"` 줄은 더 이상 쓰이지 않으므로 삭제한다. (`DB_PATH`는 이미 [monitor_cli.py:15](monitor_cli.py#L15)에 정의되어 있음.)

- [ ] **Step 2: import 스모크 확인**

Run: `python -c "import monitor_cli"`
Expected: 성공

- [ ] **Step 3: 커밋**

```bash
git add monitor_cli.py
git commit -m "feat(monitor_cli): load sellers from SQLite instead of sellers.json"
```

---

## Task 10: 죽은 코드 제거 (orders_store.py, Store sellers 메서드)

**Files:**
- Delete: `storage/orders_store.py`
- Modify: `storage/store.py` (sellers 관련 메서드 제거)

- [ ] **Step 1: 잔존 참조 확인**

Run: `grep -rn "orders_store\|OrdersStore\|load_sellers\|save_sellers\|merge_sellers" --include="*.py" main.py monitor_cli.py crawler/ storage/store.py`
Expected: 출력 없음 (Task 7-9에서 모두 교체됨). 출력이 있으면 먼저 해당 참조를 정리한다.

- [ ] **Step 2: `storage/orders_store.py` 삭제**

```bash
git rm storage/orders_store.py
```

- [ ] **Step 3: `storage/store.py`에서 sellers 메서드 제거** — [store.py:32-60](storage/store.py#L32-L60)

`load_sellers`, `save_sellers`, `merge_sellers` 세 메서드와 `__init__`의 `self.sellers_path = self.data_dir / "sellers.json"` 줄을 삭제한다. `load_config`/`save_config`/`append_error`와 `now_iso`는 유지한다.

- [ ] **Step 4: 전체 테스트 + import 스모크**

Run: `python -m pytest -q && python -c "import main, monitor_cli"`
Expected: 모든 테스트 PASS, import 성공

- [ ] **Step 5: 커밋**

```bash
git add -A storage/
git commit -m "refactor(storage): remove orders_store.py and Store sellers methods (moved to repos)"
```

---

## Task 11: 실데이터 마이그레이션 실행 + 스모크 검증

**Files:** 없음 (운영 데이터 처리)

- [ ] **Step 1: 마이그레이션 전 백업 안전장치 확인**

Run: `ls -la "data/" && wc -l data/orders.jsonl && python -c "import json; print(len(json.load(open('data/sellers.json'))), 'sellers')"`
Expected: orders.jsonl 47811행, sellers 401명 확인

- [ ] **Step 2: 마이그레이션 실행**

Run: `python -m scripts.migrate_json_to_db`
Expected: `Migrated: 47811 orders, 401 sellers, N watermarks. Sources backed up to *.bak.`

- [ ] **Step 3: DB 적재 검증**

Run:
```bash
python -c "
from storage.db import connect
c = connect('data/items.db')
print('orders', c.execute('SELECT COUNT(*) FROM orders').fetchone()[0])
print('sellers', c.execute('SELECT COUNT(*) FROM sellers').fetchone()[0])
print('watermarks', c.execute('SELECT COUNT(*) FROM order_watermarks').fetchone()[0])
print('run_meta', c.execute('SELECT last_run_at FROM order_run_meta WHERE id=1').fetchone())
"
```
Expected: orders 47811, sellers 401, watermarks > 0, run_meta 값 존재

- [ ] **Step 4: 원본 백업 확인**

Run: `ls -la data/*.bak`
Expected: `orders.jsonl.bak`, `sellers.json.bak`, `orders_config.json.bak` 존재

- [ ] **Step 5: CLI 스모크 (네트워크 없이 안전한 경로)**

Run: `python main.py crawl-orders --seller-id 99999999 --dry-run 2>&1 | head -5`
Expected: "Seller 99999999 not found in DB" (DB에서 sellers 로드가 동작함을 확인 — 존재하지 않는 ID라 즉시 반환, 네트워크 미발생)

- [ ] **Step 6: 최종 커밋 (백업 파일은 .gitignore 대상인지 확인)**

Run: `git status`
백업 `.bak`과 `items.db`가 git에 추적되지 않는지 확인(`data/`는 보통 ignore). 추적 대상이 아니면 커밋할 코드 변경은 없음. 추적 중이면:
```bash
git add -A data/
git commit -m "chore: migrate orders/sellers data to items.db, back up JSON sources"
```

---

## Self-Review 결과

- **Spec coverage:** orders 테이블(Task 1,2,7) / order_watermarks(Task 1,3,7) / order_run_meta(Task 1,4,7) / sellers 테이블(Task 1,5,8,9) / 마이그레이션·백업(Task 6,11) / orders UNIQUE 미적용(Task 2 테스트로 검증) / 죽은 코드 제거(Task 10) — 모두 태스크 존재.
- **Type 일관성:** `insert_orders(conn, list[dict])`, `upsert_watermark(conn, seller_id, signature, last_run_at, pages_scanned, orders_added)`, `load_all_watermarks→{sid: {signature,...}}`, `save_run_meta(conn, last_run_at, stats)`, `upsert_sellers(conn, dict)`, `load_sellers→{sid: {...}}` — 정의(Task 2-5)와 사용(Task 6-9)이 일치.
- **signature 변환:** 저장 list-of-lists ↔ 크롤러 tuple 리스트 변환을 Task 7에 명시.
- **Placeholder:** 없음.
