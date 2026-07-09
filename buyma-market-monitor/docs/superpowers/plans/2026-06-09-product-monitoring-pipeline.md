# Product Monitoring Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an incremental monitoring pipeline that crawls each Korean seller's listing pages periodically to track per-item price changes, new listings, and removals (SOLD_OUT vs DELETED), with full metadata enrichment fetched once per item on first observation.

**Architecture:** Two-stage pipeline backed by SQLite. Stage 1 ("scan") crawls only seller item-list pages (`/buyer/{sid}/item_{n}.html`) — extracts item_id/name/price for every active item, computes diff vs DB to detect new/removed items, appends price_history rows when price changes. Stage 2 ("enrich") fetches `/item/{id}/` detail pages only for newly observed items (full metadata) and for items that disappeared from listings (status classification: 404→DELETED, 200→SOLD_OUT for now). first_seen_at / last_seen_at maintained on items table.

**Tech Stack:** Python 3.14, httpx (HTTP/2), BeautifulSoup4 + lxml, sqlite3 (stdlib), pytest. Reuses existing `crawler/client.py` HttpClient pattern.

---

## File Structure

**Create:**
- `storage/db.py` — SQLite connection helper, schema DDL, migration runner
- `storage/items_repo.py` — items + price_history CRUD
- `crawler/seller_items.py` — parse seller item-list page (`item_N.html`)
- `crawler/item_detail.py` — parse product detail page (`/item/{id}/`)
- `crawler/item_status.py` — classify disappeared items (DELETED vs SOLD_OUT)
- `crawler/monitor.py` — orchestrator: scan stage + enrich stage
- `monitor_cli.py` — CLI entry point at project root
- `tests/__init__.py`
- `tests/fixtures/seller_items_page.html` — captured fixture (real page)
- `tests/fixtures/item_detail_normal.html` — captured fixture (normal item)
- `tests/fixtures/item_detail_404.html` — captured fixture (deleted item, 404 body)
- `tests/test_seller_items.py`
- `tests/test_item_detail.py`
- `tests/test_item_status.py`
- `tests/test_db.py`
- `tests/test_items_repo.py`
- `tests/test_monitor.py`

**Modify:**
- `requirements.txt` — add `pytest==8.3.3`

**Untouched:**
- `crawler/client.py` (HttpClient reused as-is)
- `crawler/listing.py`, `crawler/seller.py`, `crawler/pagination.py`, `main.py` (Stage 0 seller discovery — separate concern)
- `storage/store.py` (config/sellers/errors — read-only consumers here)

---

## SQLite Schema (locked design)

```sql
CREATE TABLE items (
  item_id            TEXT PRIMARY KEY,
  seller_id          TEXT NOT NULL,
  name               TEXT NOT NULL,
  current_price      INTEGER,            -- yen, last observed
  brand              TEXT,
  category_path      TEXT,               -- "バッグ・カバン > ... > ショルダーバッグ"
  origin_country     TEXT,               -- 韓国 / 日本 / ...
  image_url          TEXT,               -- og:image
  description        TEXT,               -- og:description or JSON-LD description
  raw_meta_json      TEXT,               -- full extracted metadata blob
  status             TEXT NOT NULL,      -- ACTIVE | SOLD_OUT | DELETED
  first_seen_at      TEXT NOT NULL,      -- ISO KST
  last_seen_at       TEXT NOT NULL,      -- ISO KST (last time present in listing)
  sold_out_at        TEXT,               -- ISO KST, set when transitioning to SOLD_OUT
  deleted_at         TEXT,               -- ISO KST, set when transitioning to DELETED
  detail_fetched_at  TEXT                -- ISO KST, last time /item/{id}/ was fetched
);
CREATE INDEX idx_items_seller ON items(seller_id);
CREATE INDEX idx_items_status ON items(status);

CREATE TABLE price_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,            -- ISO KST
  price        INTEGER NOT NULL,         -- yen
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX idx_price_history_item ON price_history(item_id);

CREATE TABLE monitor_runs (
  run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  sellers_scanned INTEGER,
  items_new       INTEGER,
  items_updated   INTEGER,
  items_sold_out  INTEGER,
  items_deleted   INTEGER,
  errors          INTEGER
);
```

DB lives at `data/items.db`. Schema versioning via `PRAGMA user_version` (start at 1).

---

## Status Transition Rules (locked design)

For each item currently in DB:

| Previously | Now in scan? | Detail fetch | New status |
|---|---|---|---|
| ACTIVE | yes | skip | ACTIVE (last_seen_at updated) |
| ACTIVE | no | required | DELETED if 404, else SOLD_OUT |
| SOLD_OUT | yes | skip | ACTIVE (resurrected — sold_out_at cleared) |
| SOLD_OUT | no | skip | SOLD_OUT (no change) |
| DELETED | yes | skip | ACTIVE (impossible per user — but support it; deleted_at cleared) |
| DELETED | no | skip | DELETED (no change) |

For items in scan but NOT in DB → INSERT with status=ACTIVE, queue for detail enrichment.

**Phase 1 simplification (user-accepted):** A 200 response from `/item/{id}/` is treated as SOLD_OUT when item is missing from listings. False-positive risk (e.g. temporary listing hiccup) is acknowledged — addressed in Task 14.

---

## Worker Defaults (locked design)

- Stage 1 (scan): 5 worker threads, sleep 0.3s/req → ~2.7h for 40k pages
- Stage 2 (enrich): 5 worker threads, sleep 0.3s/req → scales with new/disappeared item count
- All HTTP via existing `HttpClient` (httpx HTTP/2, retry-on-5xx). No Playwright.

---

# Tasks

### Task 1: Add pytest dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add pytest line**

Append a single line to `requirements.txt`:

```
pytest==8.3.3
```

Final `requirements.txt`:

```
httpx[http2]==0.27.2
beautifulsoup4==4.12.3
lxml==5.3.0
playwright==1.48.0
pytest==8.3.3
```

- [ ] **Step 2: Install**

```bash
.venv/bin/pip install pytest==8.3.3
```

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest --version
```

Expected: `pytest 8.3.3`

- [ ] **Step 4: Create tests package marker**

Create empty file `tests/__init__.py` (empty content).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/__init__.py
git commit -m "chore: add pytest dev dependency and tests package"
```

---

### Task 2: SQLite schema and connection helper

**Files:**
- Create: `storage/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
import sqlite3
from pathlib import Path

from storage.db import connect, init_schema, SCHEMA_VERSION


def test_init_schema_creates_tables(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    try:
        init_schema(conn)
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"items", "price_history", "monitor_runs"}.issubset(names)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_init_schema_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    try:
        init_schema(conn)
        init_schema(conn)  # second call must not raise
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_items_pk_and_indexes(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    try:
        init_schema(conn)
        idx_names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_items_seller" in idx_names
        assert "idx_items_status" in idx_names
        assert "idx_price_history_item" in idx_names
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_db.py -v
```

Expected: ImportError / ModuleNotFoundError on `storage.db`.

- [ ] **Step 3: Implement storage/db.py**

Create `storage/db.py`:

```python
"""SQLite connection and schema for the product monitoring pipeline."""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS items (
  item_id            TEXT PRIMARY KEY,
  seller_id          TEXT NOT NULL,
  name               TEXT NOT NULL,
  current_price      INTEGER,
  brand              TEXT,
  category_path      TEXT,
  origin_country     TEXT,
  image_url          TEXT,
  description        TEXT,
  raw_meta_json      TEXT,
  status             TEXT NOT NULL,
  first_seen_at      TEXT NOT NULL,
  last_seen_at       TEXT NOT NULL,
  sold_out_at        TEXT,
  deleted_at         TEXT,
  detail_fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_seller ON items(seller_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE TABLE IF NOT EXISTS price_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  price        INTEGER NOT NULL,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_price_history_item ON price_history(item_id);

CREATE TABLE IF NOT EXISTS monitor_runs (
  run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  sellers_scanned INTEGER,
  items_new       INTEGER,
  items_updated   INTEGER,
  items_sold_out  INTEGER,
  items_deleted   INTEGER,
  errors          INTEGER
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_db.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add storage/db.py tests/test_db.py
git commit -m "feat: add SQLite schema for items, price_history, monitor_runs"
```

---

### Task 3: items_repo — upsert and diff helpers

**Files:**
- Create: `storage/items_repo.py`
- Test: `tests/test_items_repo.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_items_repo.py`:

```python
from pathlib import Path

from storage.db import connect, init_schema
from storage.items_repo import (
    upsert_scanned_item,
    record_price_observation,
    mark_status,
    get_active_item_ids_for_seller,
    get_item,
    update_detail_fields,
)


def make_conn(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    return conn


def test_upsert_inserts_new_item_as_active(tmp_path: Path):
    conn = make_conn(tmp_path)
    is_new = upsert_scanned_item(
        conn,
        item_id="100",
        seller_id="S1",
        name="Test Item",
        price=1000,
        now="2026-06-09T10:00:00+09:00",
    )
    assert is_new is True
    row = get_item(conn, "100")
    assert row["status"] == "ACTIVE"
    assert row["current_price"] == 1000
    assert row["first_seen_at"] == "2026-06-09T10:00:00+09:00"
    assert row["last_seen_at"] == "2026-06-09T10:00:00+09:00"


def test_upsert_existing_updates_last_seen_keeps_first_seen(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    is_new = upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-10T10:00:00+09:00")
    assert is_new is False
    row = get_item(conn, "100")
    assert row["first_seen_at"] == "2026-06-09T10:00:00+09:00"
    assert row["last_seen_at"] == "2026-06-10T10:00:00+09:00"


def test_upsert_resurrects_sold_out(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    mark_status(conn, "100", "SOLD_OUT", "2026-06-10T00:00:00+09:00")
    assert get_item(conn, "100")["status"] == "SOLD_OUT"

    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-11T10:00:00+09:00")
    row = get_item(conn, "100")
    assert row["status"] == "ACTIVE"
    assert row["sold_out_at"] is None


def test_record_price_observation_appends_row(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    record_price_observation(conn, "100", 1000, "2026-06-09T10:00:00+09:00")
    record_price_observation(conn, "100", 1100, "2026-06-10T10:00:00+09:00")
    rows = list(conn.execute(
        "SELECT price FROM price_history WHERE item_id=? ORDER BY observed_at",
        ("100",),
    ))
    assert [r["price"] for r in rows] == [1000, 1100]


def test_get_active_item_ids_for_seller(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-09T10:00:00+09:00")
    upsert_scanned_item(conn, "2", "S1", "B", 200, "2026-06-09T10:00:00+09:00")
    upsert_scanned_item(conn, "3", "S2", "C", 300, "2026-06-09T10:00:00+09:00")
    mark_status(conn, "2", "SOLD_OUT", "2026-06-10T00:00:00+09:00")
    ids = get_active_item_ids_for_seller(conn, "S1")
    assert ids == {"1"}


def test_mark_status_deleted_sets_deleted_at(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    mark_status(conn, "100", "DELETED", "2026-06-10T00:00:00+09:00")
    row = get_item(conn, "100")
    assert row["status"] == "DELETED"
    assert row["deleted_at"] == "2026-06-10T00:00:00+09:00"
    assert row["sold_out_at"] is None


def test_update_detail_fields(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "Test", 1000, "2026-06-09T10:00:00+09:00")
    update_detail_fields(
        conn,
        item_id="100",
        brand="LEMAIRE",
        category_path="バッグ > ショルダー",
        origin_country="韓国",
        image_url="https://example.com/x.jpg",
        description="desc",
        raw_meta_json='{"a":1}',
        fetched_at="2026-06-09T11:00:00+09:00",
    )
    row = get_item(conn, "100")
    assert row["brand"] == "LEMAIRE"
    assert row["category_path"] == "バッグ > ショルダー"
    assert row["origin_country"] == "韓国"
    assert row["image_url"] == "https://example.com/x.jpg"
    assert row["description"] == "desc"
    assert row["raw_meta_json"] == '{"a":1}'
    assert row["detail_fetched_at"] == "2026-06-09T11:00:00+09:00"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_items_repo.py -v
```

Expected: ImportError on `storage.items_repo`.

- [ ] **Step 3: Implement storage/items_repo.py**

Create `storage/items_repo.py`:

```python
"""Items table CRUD. Operates on an open sqlite3 connection."""
import sqlite3


def upsert_scanned_item(
    conn: sqlite3.Connection,
    item_id: str,
    seller_id: str,
    name: str,
    price: int | None,
    now: str,
) -> bool:
    """Insert a new item or update last_seen_at + resurrect if needed.

    Returns True if the item was newly inserted (caller should queue detail fetch).
    """
    existing = conn.execute(
        "SELECT status FROM items WHERE item_id = ?",
        (item_id,),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO items (
              item_id, seller_id, name, current_price, status,
              first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (item_id, seller_id, name, price, now, now),
        )
        return True

    conn.execute(
        """
        UPDATE items
        SET name = ?,
            current_price = ?,
            status = 'ACTIVE',
            sold_out_at = NULL,
            deleted_at = NULL,
            last_seen_at = ?
        WHERE item_id = ?
        """,
        (name, price, now, item_id),
    )
    return False


def record_price_observation(
    conn: sqlite3.Connection,
    item_id: str,
    price: int,
    observed_at: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO price_history (item_id, observed_at, price) VALUES (?, ?, ?)",
        (item_id, observed_at, price),
    )


def mark_status(
    conn: sqlite3.Connection,
    item_id: str,
    status: str,
    now: str,
) -> None:
    """Set status to SOLD_OUT or DELETED, stamping the appropriate timestamp."""
    if status == "SOLD_OUT":
        conn.execute(
            "UPDATE items SET status='SOLD_OUT', sold_out_at=?, deleted_at=NULL WHERE item_id=?",
            (now, item_id),
        )
    elif status == "DELETED":
        conn.execute(
            "UPDATE items SET status='DELETED', deleted_at=?, sold_out_at=NULL WHERE item_id=?",
            (now, item_id),
        )
    else:
        raise ValueError(f"unsupported status: {status}")


def get_active_item_ids_for_seller(
    conn: sqlite3.Connection, seller_id: str
) -> set[str]:
    rows = conn.execute(
        "SELECT item_id FROM items WHERE seller_id = ? AND status = 'ACTIVE'",
        (seller_id,),
    )
    return {r["item_id"] for r in rows}


def get_item(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()


def get_current_price(conn: sqlite3.Connection, item_id: str) -> int | None:
    row = conn.execute(
        "SELECT current_price FROM items WHERE item_id = ?", (item_id,)
    ).fetchone()
    return row["current_price"] if row else None


def update_detail_fields(
    conn: sqlite3.Connection,
    item_id: str,
    brand: str | None,
    category_path: str | None,
    origin_country: str | None,
    image_url: str | None,
    description: str | None,
    raw_meta_json: str | None,
    fetched_at: str,
) -> None:
    conn.execute(
        """
        UPDATE items SET
          brand = ?,
          category_path = ?,
          origin_country = ?,
          image_url = ?,
          description = ?,
          raw_meta_json = ?,
          detail_fetched_at = ?
        WHERE item_id = ?
        """,
        (brand, category_path, origin_country, image_url, description,
         raw_meta_json, fetched_at, item_id),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_items_repo.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add storage/items_repo.py tests/test_items_repo.py
git commit -m "feat: add items repository with upsert, status transitions, price history"
```

---

### Task 4: Capture seller item-list page fixture

**Files:**
- Create: `tests/fixtures/seller_items_page.html`

This task captures a real HTML fixture so subsequent parser tests are deterministic and run offline.

- [ ] **Step 1: Fetch and save fixture**

```bash
.venv/bin/python3 -c "
from crawler.client import HttpClient
url = 'https://www.buyma.com/buyer/13053653/item_1.html'
with HttpClient(sleep_seconds=0) as c:
    r = c.get(url)
with open('tests/fixtures/seller_items_page.html', 'w', encoding='utf-8') as f:
    f.write(r.text)
print('saved', len(r.text), 'chars')
"
```

Expected output: `saved <N> chars` where N is roughly 180,000–200,000.

- [ ] **Step 2: Sanity check the fixture**

```bash
.venv/bin/python3 -c "
from bs4 import BeautifulSoup
html = open('tests/fixtures/seller_items_page.html', encoding='utf-8').read()
soup = BeautifulSoup(html, 'lxml')
cards = soup.select('p.buyeritem_name')
prices = soup.select('p.buyeritem_price')
print(f'cards={len(cards)} prices={len(prices)}')
assert len(cards) == 30, 'expected 30 cards per page'
assert len(prices) >= 30
print('OK')
"
```

Expected: `cards=30 prices=30+` then `OK`.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/seller_items_page.html
git commit -m "test: add seller item-list page fixture (30 items)"
```

---

### Task 5: Parse seller item-list page

**Files:**
- Create: `crawler/seller_items.py`
- Test: `tests/test_seller_items.py`

The fixture's actual HTML structure was confirmed earlier in this conversation:

```html
<li class="buyeritemtable_info">
  <p class="buyeritem_name">
    <a href="/item/133153945/">送料・関税込 | GRAMICCI | ...</a>
  </p>
  <p>韓国 ...</p>
  <p>購入期限：2026/09/07</p>
  <p class="buyeritem_price">¥30,671 <span class="buyeritem_price_postage">送料込</span></p>
</li>
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_seller_items.py`:

```python
from pathlib import Path

from crawler.seller_items import (
    parse_seller_items,
    parse_seller_items_max_page,
    build_seller_items_url,
)


FIXTURE = Path(__file__).parent / "fixtures" / "seller_items_page.html"


def test_build_seller_items_url():
    assert build_seller_items_url("13053653", 1) == \
        "https://www.buyma.com/buyer/13053653/item_1.html"
    assert build_seller_items_url("13053653", 470) == \
        "https://www.buyma.com/buyer/13053653/item_470.html"


def test_parse_seller_items_returns_30_items():
    html = FIXTURE.read_text(encoding="utf-8")
    items = parse_seller_items(html)
    assert len(items) == 30


def test_parse_seller_items_extracts_required_fields():
    html = FIXTURE.read_text(encoding="utf-8")
    items = parse_seller_items(html)
    first = items[0]
    assert first["item_id"].isdigit()
    assert isinstance(first["name"], str) and len(first["name"]) > 0
    assert isinstance(first["price"], int) and first["price"] > 0


def test_parse_seller_items_max_page():
    html = FIXTURE.read_text(encoding="utf-8")
    max_p = parse_seller_items_max_page(html)
    assert max_p >= 1


def test_parse_seller_items_max_page_single_page():
    # A page with no pagination links should return 1
    html = "<html><body><ul><li class='buyeritemtable_info'>" \
           "<p class='buyeritem_name'><a href='/item/1/'>x</a></p>" \
           "<p class='buyeritem_price'>¥100</p></li></ul></body></html>"
    assert parse_seller_items_max_page(html) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_seller_items.py -v
```

Expected: ImportError on `crawler.seller_items`.

- [ ] **Step 3: Implement crawler/seller_items.py**

Create `crawler/seller_items.py`:

```python
"""Parse a seller's item-list page (/buyer/{sid}/item_{n}.html).

Each page contains 30 product cards. We extract item_id, name, price.
Price changes are tracked via incremental scans; full metadata enrichment
is handled by crawler/item_detail.py on first observation only.
"""
import re

from bs4 import BeautifulSoup


SELLER_ITEMS_URL_TEMPLATE = "https://www.buyma.com/buyer/{sid}/item_{n}.html"
_ITEM_HREF_PATTERN = re.compile(r"^/item/(\d+)/?$")
_PAGE_HREF_PATTERN = re.compile(r"/buyer/\d+/item_(\d+)\.html")
_DIGITS = re.compile(r"[\d,]+")


def build_seller_items_url(seller_id: str, page: int) -> str:
    return SELLER_ITEMS_URL_TEMPLATE.format(sid=seller_id, n=page)


def parse_seller_items(html: str) -> list[dict]:
    """Return [{item_id, name, price}] for every card on the page."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for card in soup.select("li.buyeritemtable_info"):
        name_p = card.select_one("p.buyeritem_name a")
        price_p = card.select_one("p.buyeritem_price")
        if name_p is None or price_p is None:
            continue
        href = name_p.get("href", "")
        m = _ITEM_HREF_PATTERN.match(href)
        if m is None:
            continue
        item_id = m.group(1)
        name = name_p.get_text(strip=True)
        price = _parse_price(price_p.get_text())
        if not name or price is None:
            continue
        out.append({"item_id": item_id, "name": name, "price": price})
    return out


def parse_seller_items_max_page(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    max_n = 1
    for a in soup.find_all("a", href=True):
        m = _PAGE_HREF_PATTERN.search(a["href"])
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return max_n


def _parse_price(text: str) -> int | None:
    m = _DIGITS.search(text)
    if m is None:
        return None
    digits = m.group(0).replace(",", "")
    if not digits:
        return None
    return int(digits)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_seller_items.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add crawler/seller_items.py tests/test_seller_items.py
git commit -m "feat: parse seller item-list page (item_id, name, price + max page)"
```

---

### Task 6: Capture item-detail fixtures (normal + 404)

**Files:**
- Create: `tests/fixtures/item_detail_normal.html`
- Create: `tests/fixtures/item_detail_404.html`

- [ ] **Step 1: Save normal item fixture**

```bash
.venv/bin/python3 -c "
from crawler.client import HttpClient
url = 'https://www.buyma.com/item/133150020/'
with HttpClient(sleep_seconds=0) as c:
    r = c.get(url)
with open('tests/fixtures/item_detail_normal.html', 'w', encoding='utf-8') as f:
    f.write(r.text)
print('saved', len(r.text), 'chars, status', r.status_code)
"
```

Expected: `saved ~170000 chars, status 200`.

- [ ] **Step 2: Save 404 item fixture**

The HttpClient raises on 404, so use httpx directly to capture the 404 body:

```bash
.venv/bin/python3 -c "
import httpx
from crawler.client import DEFAULT_HEADERS, TIMEOUT
url = 'https://www.buyma.com/item/999999999/'
with httpx.Client(http2=True, headers=DEFAULT_HEADERS, timeout=TIMEOUT, follow_redirects=False) as c:
    r = c.get(url)
with open('tests/fixtures/item_detail_404.html', 'w', encoding='utf-8') as f:
    f.write(r.text)
print('saved', len(r.text), 'chars, status', r.status_code)
"
```

Expected: `saved ~65000 chars, status 404`.

- [ ] **Step 3: Sanity check fixtures**

```bash
.venv/bin/python3 -c "
normal = open('tests/fixtures/item_detail_normal.html', encoding='utf-8').read()
notfound = open('tests/fixtures/item_detail_404.html', encoding='utf-8').read()
assert 'og:title' in normal
assert '\"@type\": \"ProductGroup\"' in normal
assert '\"@type\": \"BreadcrumbList\"' in normal
assert 'お探しのページ' in notfound or '削除' in notfound
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/item_detail_normal.html tests/fixtures/item_detail_404.html
git commit -m "test: add item-detail fixtures (normal 200, deleted 404)"
```

---

### Task 7: Parse item detail page (full metadata via JSON-LD)

**Files:**
- Create: `crawler/item_detail.py`
- Test: `tests/test_item_detail.py`

The detail page exposes structured data we lean on:
- `<meta property="og:title">` → product name (also `<h1>`)
- `<meta property="og:image">` → main image URL
- `<meta property="og:description">` → description
- `<meta name="twitter:data1">` (label="価格") → price (yen)
- `<meta name="twitter:data2">` (label="購入地") → origin country
- `<script type="application/ld+json">` with `@type: ProductGroup` → brand, description, productGroupID, variants
- `<script type="application/ld+json">` with `@type: BreadcrumbList` → category path

`raw_meta_json` stores the full extracted blob for forensic/analysis use.

- [ ] **Step 1: Write the failing test**

Create `tests/test_item_detail.py`:

```python
import json
from pathlib import Path

from crawler.item_detail import parse_item_detail, build_item_detail_url


FIXTURE = Path(__file__).parent / "fixtures" / "item_detail_normal.html"


def test_build_item_detail_url():
    assert build_item_detail_url("133150020") == "https://www.buyma.com/item/133150020/"


def test_parse_item_detail_extracts_core_fields():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["name"] and "LEMAIRE" in meta["name"]
    assert meta["brand"] == "LEMAIRE" or "LEMAIRE" in (meta["brand"] or "")
    assert meta["origin_country"] == "韓国"
    assert meta["image_url"].startswith("https://")
    assert meta["category_path"]
    assert ">" in meta["category_path"] or " > " in meta["category_path"]
    assert isinstance(meta["description"], str) and len(meta["description"]) > 0


def test_parse_item_detail_raw_meta_is_json_string():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    parsed = json.loads(meta["raw_meta_json"])
    assert "og" in parsed
    assert "twitter" in parsed
    assert "json_ld" in parsed
    assert any(
        block.get("@type") == "ProductGroup" for block in parsed["json_ld"]
    )


def test_parse_item_detail_missing_fields_dont_crash():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["name"] == ""
    assert meta["brand"] is None
    assert meta["origin_country"] is None
    assert meta["image_url"] is None
    assert meta["category_path"] is None
    assert meta["description"] == ""
    assert isinstance(meta["raw_meta_json"], str)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_item_detail.py -v
```

Expected: ImportError on `crawler.item_detail`.

- [ ] **Step 3: Implement crawler/item_detail.py**

Create `crawler/item_detail.py`:

```python
"""Parse a product detail page (/item/{id}/) into a full metadata dict.

Heavy lifting via JSON-LD (ProductGroup + BreadcrumbList) and OpenGraph/Twitter
meta tags. The complete extracted blob is preserved as raw_meta_json for any
future analysis without re-fetching.
"""
import json
from typing import Any

from bs4 import BeautifulSoup


ITEM_DETAIL_URL_TEMPLATE = "https://www.buyma.com/item/{item_id}/"


def build_item_detail_url(item_id: str) -> str:
    return ITEM_DETAIL_URL_TEMPLATE.format(item_id=item_id)


def parse_item_detail(html: str) -> dict[str, Any]:
    """Return a flat dict of fields plus raw_meta_json containing full extract.

    Keys returned: name, brand, category_path, origin_country, image_url,
    description, raw_meta_json. Missing fields are None (or "" for strings),
    never raise.
    """
    soup = BeautifulSoup(html, "lxml")

    og = _extract_meta(soup, key="property", prefix="og:")
    twitter = _extract_meta(soup, key="name", prefix="twitter:")
    json_ld = _extract_json_ld(soup)

    product_group = next(
        (b for b in json_ld if b.get("@type") == "ProductGroup"), None
    )
    breadcrumbs = [b for b in json_ld if b.get("@type") == "BreadcrumbList"]

    raw_meta = {
        "og": og,
        "twitter": twitter,
        "json_ld": json_ld,
    }

    return {
        "name": og.get("title", "") or _h1_text(soup),
        "brand": _extract_brand(product_group),
        "category_path": _extract_category_path(breadcrumbs),
        "origin_country": _extract_origin_country(twitter),
        "image_url": og.get("image"),
        "description": og.get("description", "") or _extract_pg_description(product_group),
        "raw_meta_json": json.dumps(raw_meta, ensure_ascii=False),
    }


def _extract_meta(soup, key: str, prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in soup.find_all("meta"):
        name = m.get(key)
        if not name or not name.startswith(prefix):
            continue
        content = m.get("content", "")
        out[name[len(prefix):]] = content
    return out


def _extract_json_ld(soup) -> list[dict]:
    blocks: list[dict] = []
    for s in soup.select('script[type="application/ld+json"]'):
        raw = s.string
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            blocks.extend(x for x in data if isinstance(x, dict))
        elif isinstance(data, dict):
            blocks.append(data)
    return blocks


def _h1_text(soup) -> str:
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


def _extract_brand(product_group: dict | None) -> str | None:
    if not product_group:
        return None
    brand = product_group.get("brand")
    if isinstance(brand, dict):
        return brand.get("name")
    if isinstance(brand, str):
        return brand
    return None


def _extract_pg_description(product_group: dict | None) -> str:
    if not product_group:
        return ""
    return product_group.get("description", "") or ""


def _extract_category_path(breadcrumbs: list[dict]) -> str | None:
    """Pick the longest BreadcrumbList and join its item names with ' > '."""
    best: list[str] = []
    for bc in breadcrumbs:
        items = bc.get("itemListElement") or []
        names: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            n = it.get("name") or (it.get("item") or {}).get("name")
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
        if len(names) > len(best):
            best = names
    if not best:
        return None
    return " > ".join(best)


def _extract_origin_country(twitter: dict[str, str]) -> str | None:
    """twitter:label2=='購入地' → twitter:data2 is the country."""
    if twitter.get("label2") == "購入地":
        v = twitter.get("data2", "").strip()
        return v or None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_item_detail.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add crawler/item_detail.py tests/test_item_detail.py
git commit -m "feat: parse item detail page via JSON-LD + OG/twitter meta"
```

---

### Task 8: Classify disappeared item status (DELETED vs SOLD_OUT)

**Files:**
- Create: `crawler/item_status.py`
- Test: `tests/test_item_status.py`

This module handles a single item that disappeared from a seller's listing. It re-fetches `/item/{id}/`. 404 → DELETED. 200 → SOLD_OUT (Phase 1 simplification — see Task 14 for the refinement plan). Network errors propagate to the caller's error log; caller decides whether to retry next run.

- [ ] **Step 1: Write the failing test**

Create `tests/test_item_status.py`:

```python
from crawler.item_status import classify_status_from_response, ItemStatus


def test_404_means_deleted():
    assert classify_status_from_response(404, "") == ItemStatus.DELETED


def test_200_means_sold_out():
    assert classify_status_from_response(200, "<html>...</html>") == ItemStatus.SOLD_OUT


def test_410_also_means_deleted():
    assert classify_status_from_response(410, "") == ItemStatus.DELETED


def test_unexpected_status_raises():
    import pytest
    with pytest.raises(ValueError):
        classify_status_from_response(500, "")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_item_status.py -v
```

Expected: ImportError on `crawler.item_status`.

- [ ] **Step 3: Implement crawler/item_status.py**

Create `crawler/item_status.py`:

```python
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
        # body is accepted for future refinement; ignored in Phase 1.
        _ = body
        return ItemStatus.SOLD_OUT
    raise ValueError(
        f"unexpected status {status_code} during status classification"
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_item_status.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add crawler/item_status.py tests/test_item_status.py
git commit -m "feat: classify disappeared item as DELETED (404) or SOLD_OUT (200)"
```

---

### Task 9: Seller scan crawler — walk all pages of one seller

**Files:**
- Create: `crawler/seller_items_crawler.py`
- Test: `tests/test_seller_items_crawler.py`

A single-seller scan: fetch page 1, parse max_pages, fetch pages 2..N, accumulate items. Returns the full list (caller persists). Errors per page are appended via `on_error` but the scan continues on remaining pages.

- [ ] **Step 1: Write the failing test**

Create `tests/test_seller_items_crawler.py`:

```python
from crawler.seller_items_crawler import scan_seller_items


class FakeClient:
    """Returns canned HTML for specific URLs."""
    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        if url not in self.responses:
            raise ValueError(f"no fake response for {url}")
        class R: pass
        r = R()
        r.text = self.responses[url]
        r.status_code = 200
        return r


def _page_html(item_ids: list[str], max_page: int) -> str:
    cards = "".join(
        f'<li class="buyeritemtable_info">'
        f'<p class="buyeritem_name"><a href="/item/{iid}/">Item {iid}</a></p>'
        f'<p class="buyeritem_price">¥{1000 + i}</p>'
        f'</li>'
        for i, iid in enumerate(item_ids)
    )
    pager = "".join(
        f'<a href="/buyer/S1/item_{n}.html">{n}</a>'
        for n in range(1, max_page + 1)
    )
    return f"<html><body><ul>{cards}</ul><div>{pager}</div></body></html>"


def test_scan_single_page_seller():
    client = FakeClient({
        "https://www.buyma.com/buyer/S1/item_1.html": _page_html(["1", "2", "3"], 1),
    })
    errors = []
    items = scan_seller_items(
        client, seller_id="S1",
        on_error=lambda **kw: errors.append(kw),
    )
    assert [it["item_id"] for it in items] == ["1", "2", "3"]
    assert errors == []
    assert client.calls == ["https://www.buyma.com/buyer/S1/item_1.html"]


def test_scan_multi_page_seller():
    client = FakeClient({
        "https://www.buyma.com/buyer/S1/item_1.html": _page_html(["1", "2"], 3),
        "https://www.buyma.com/buyer/S1/item_2.html": _page_html(["3", "4"], 3),
        "https://www.buyma.com/buyer/S1/item_3.html": _page_html(["5"], 3),
    })
    items = scan_seller_items(client, seller_id="S1", on_error=lambda **kw: None)
    assert sorted(it["item_id"] for it in items) == ["1", "2", "3", "4", "5"]


def test_scan_continues_on_page_error():
    class FlakeyClient:
        def __init__(self):
            self.calls = []
        def get(self, url):
            self.calls.append(url)
            if "item_2.html" in url:
                raise RuntimeError("boom")
            if "item_1.html" in url:
                class R: pass
                r = R()
                r.text = _page_html(["1"], 3)
                r.status_code = 200
                return r
            if "item_3.html" in url:
                class R: pass
                r = R()
                r.text = _page_html(["5"], 3)
                r.status_code = 200
                return r

    client = FlakeyClient()
    errors = []
    items = scan_seller_items(
        client, seller_id="S1",
        on_error=lambda **kw: errors.append(kw),
    )
    assert sorted(it["item_id"] for it in items) == ["1", "5"]
    assert len(errors) == 1
    assert "item_2.html" in errors[0]["url"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_seller_items_crawler.py -v
```

Expected: ImportError on `crawler.seller_items_crawler`.

- [ ] **Step 3: Implement crawler/seller_items_crawler.py**

Create `crawler/seller_items_crawler.py`:

```python
"""Walk every item-list page of one seller and return all items."""
from typing import Callable

from crawler.seller_items import (
    build_seller_items_url,
    parse_seller_items,
    parse_seller_items_max_page,
)


def scan_seller_items(
    client,
    seller_id: str,
    on_error: Callable[..., None],
) -> list[dict]:
    """Fetch page 1, learn max_pages, fetch the rest, return aggregated items.

    Each returned dict carries seller_id added for downstream convenience.
    On any per-page failure, on_error is invoked and that page is skipped.
    """
    all_items: list[dict] = []

    first_url = build_seller_items_url(seller_id, 1)
    try:
        first = client.get(first_url)
    except Exception as e:
        on_error(stage="seller_items", url=first_url,
                 status=getattr(e, "last_status", None), reason=repr(e))
        return all_items

    all_items.extend(_tag_seller(parse_seller_items(first.text), seller_id))
    max_pages = parse_seller_items_max_page(first.text)

    for n in range(2, max_pages + 1):
        url = build_seller_items_url(seller_id, n)
        try:
            resp = client.get(url)
        except Exception as e:
            on_error(stage="seller_items", url=url,
                     status=getattr(e, "last_status", None), reason=repr(e))
            continue
        all_items.extend(_tag_seller(parse_seller_items(resp.text), seller_id))

    return all_items


def _tag_seller(items: list[dict], seller_id: str) -> list[dict]:
    for it in items:
        it["seller_id"] = seller_id
    return items
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_seller_items_crawler.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add crawler/seller_items_crawler.py tests/test_seller_items_crawler.py
git commit -m "feat: walk all pages of one seller's item list with per-page error tolerance"
```

---

### Task 10: Monitor orchestrator — scan stage

**Files:**
- Create: `crawler/monitor.py`
- Test: `tests/test_monitor.py`

The scan stage processes a set of sellers in parallel (thread pool), applies DB transitions per seller atomically, and returns the per-run summary plus the list of (item_id, seller_id) newly observed (for the enrich stage).

Per-seller transaction: open one DB transaction per seller, upsert all scanned items, record price observations on changes, then mark any previously-active item not in this scan as needing classification. Reason: we never want a partial scan to mark live items as disappeared.

- [ ] **Step 1: Write the failing test**

Create `tests/test_monitor.py`:

```python
from pathlib import Path

from storage.db import connect, init_schema
from storage.items_repo import (
    upsert_scanned_item, mark_status, get_item, record_price_observation,
)
from crawler.monitor import (
    apply_seller_scan_to_db,
    SellerScanOutcome,
)


def make_conn(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    return conn


def test_apply_scan_inserts_new_items(tmp_path: Path):
    conn = make_conn(tmp_path)
    scanned = [
        {"item_id": "1", "seller_id": "S1", "name": "A", "price": 100},
        {"item_id": "2", "seller_id": "S1", "name": "B", "price": 200},
    ]
    outcome = apply_seller_scan_to_db(
        conn, seller_id="S1", scanned_items=scanned,
        now="2026-06-09T10:00:00+09:00",
    )
    assert outcome.new_item_ids == {"1", "2"}
    assert outcome.disappeared_item_ids == set()
    assert outcome.price_changes == 0
    assert get_item(conn, "1")["status"] == "ACTIVE"


def test_apply_scan_detects_price_change(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-08T10:00:00+09:00")
    record_price_observation(conn, "1", 100, "2026-06-08T10:00:00+09:00")

    scanned = [{"item_id": "1", "seller_id": "S1", "name": "A", "price": 150}]
    outcome = apply_seller_scan_to_db(
        conn, seller_id="S1", scanned_items=scanned,
        now="2026-06-09T10:00:00+09:00",
    )
    assert outcome.price_changes == 1
    assert get_item(conn, "1")["current_price"] == 150
    rows = list(conn.execute(
        "SELECT price FROM price_history WHERE item_id='1' ORDER BY observed_at"
    ))
    assert [r["price"] for r in rows] == [100, 150]


def test_apply_scan_marks_disappeared(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-08T10:00:00+09:00")
    upsert_scanned_item(conn, "2", "S1", "B", 200, "2026-06-08T10:00:00+09:00")

    scanned = [{"item_id": "1", "seller_id": "S1", "name": "A", "price": 100}]
    outcome = apply_seller_scan_to_db(
        conn, seller_id="S1", scanned_items=scanned,
        now="2026-06-09T10:00:00+09:00",
    )
    assert outcome.disappeared_item_ids == {"2"}
    # status is NOT changed yet — classification happens in enrich stage
    assert get_item(conn, "2")["status"] == "ACTIVE"


def test_apply_scan_resurrects_sold_out(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-08T10:00:00+09:00")
    mark_status(conn, "1", "SOLD_OUT", "2026-06-08T11:00:00+09:00")

    scanned = [{"item_id": "1", "seller_id": "S1", "name": "A", "price": 100}]
    outcome = apply_seller_scan_to_db(
        conn, seller_id="S1", scanned_items=scanned,
        now="2026-06-09T10:00:00+09:00",
    )
    assert outcome.resurrected_item_ids == {"1"}
    assert get_item(conn, "1")["status"] == "ACTIVE"
    assert get_item(conn, "1")["sold_out_at"] is None


def test_empty_scan_skips_disappearance_marking(tmp_path: Path):
    """If scanned_items is empty (likely a fetch failure), do NOT mark every
    active item as disappeared — that would be catastrophic."""
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "1", "S1", "A", 100, "2026-06-08T10:00:00+09:00")

    outcome = apply_seller_scan_to_db(
        conn, seller_id="S1", scanned_items=[],
        now="2026-06-09T10:00:00+09:00",
    )
    assert outcome.disappeared_item_ids == set()
    assert outcome.skipped_due_to_empty_scan is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_monitor.py -v
```

Expected: ImportError on `crawler.monitor`.

- [ ] **Step 3: Implement crawler/monitor.py (scan stage portion)**

Create `crawler/monitor.py`:

```python
"""Orchestrate scan + enrich stages of the monitoring pipeline.

Stage A (scan): walk every seller's item-list pages; per-seller, apply DB diff
  in one transaction. Empty scan results never mark items as disappeared.
Stage B (enrich): for new items, fetch detail page and store full metadata.
  For disappeared items, fetch detail page to classify DELETED vs SOLD_OUT.
"""
import sqlite3
from dataclasses import dataclass, field

from storage.items_repo import (
    upsert_scanned_item,
    record_price_observation,
    get_active_item_ids_for_seller,
    get_current_price,
    get_item,
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
        prev_active = get_active_item_ids_for_seller(conn, seller_id)
        scanned_ids: set[str] = set()

        for it in scanned_items:
            item_id = it["item_id"]
            scanned_ids.add(item_id)
            price = it["price"]

            prior = get_item(conn, item_id)
            prior_status = prior["status"] if prior else None
            prior_price = prior["current_price"] if prior else None

            is_new = upsert_scanned_item(
                conn, item_id=item_id, seller_id=seller_id,
                name=it["name"], price=price, now=now,
            )

            if is_new:
                outcome.new_item_ids.add(item_id)
                if price is not None:
                    record_price_observation(conn, item_id, price, now)
            else:
                if prior_status in ("SOLD_OUT", "DELETED"):
                    outcome.resurrected_item_ids.add(item_id)
                if price is not None and price != prior_price:
                    record_price_observation(conn, item_id, price, now)
                    outcome.price_changes += 1

        outcome.disappeared_item_ids = prev_active - scanned_ids
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return outcome
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_monitor.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add crawler/monitor.py tests/test_monitor.py
git commit -m "feat: per-seller scan reconciler — diff vs DB in one transaction"
```

---

### Task 11: Enrich stage — fetch detail for new items, classify disappeared

**Files:**
- Modify: `crawler/monitor.py`
- Modify: `tests/test_monitor.py`

The enrich stage takes two inputs per run: new item IDs (need full metadata) and disappeared item IDs (need status classification). Both fetch `/item/{id}/`. We treat them as a single work queue distinguished by intent.

For disappeared item classification we need both the status code AND the body (for future refinement). The existing `HttpClient.get()` raises on 4xx, so we use a dedicated `fetch_for_status_check` helper that returns the response without raising on 404/410.

- [ ] **Step 1: Write the failing test (append to tests/test_monitor.py)**

Append to `tests/test_monitor.py`:

```python
from crawler.monitor import enrich_new_item, classify_disappeared_item
from crawler.item_status import ItemStatus


class FakeDetailClient:
    """Returns canned (status_code, body) pairs for /item/{id}/ URLs."""
    def __init__(self, responses: dict[str, tuple[int, str]]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        status, body = self.responses[url]
        if status >= 400 and status not in (404, 410):
            raise RuntimeError(f"http {status}")
        class R: pass
        r = R(); r.text = body; r.status_code = status
        return r


SAMPLE_DETAIL_HTML = (Path(__file__).parent / "fixtures" / "item_detail_normal.html").read_text(encoding="utf-8")
SAMPLE_404_HTML = (Path(__file__).parent / "fixtures" / "item_detail_404.html").read_text(encoding="utf-8")


def test_enrich_new_item_writes_detail_fields(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "A", 100, "2026-06-09T10:00:00+09:00")
    client = FakeDetailClient({
        "https://www.buyma.com/item/100/": (200, SAMPLE_DETAIL_HTML),
    })
    enrich_new_item(conn, client, item_id="100", now="2026-06-09T11:00:00+09:00",
                    on_error=lambda **kw: None)
    row = get_item(conn, "100")
    assert row["brand"]
    assert row["category_path"]
    assert row["image_url"]
    assert row["detail_fetched_at"] == "2026-06-09T11:00:00+09:00"
    assert row["raw_meta_json"] and len(row["raw_meta_json"]) > 100


def test_classify_disappeared_404_marks_deleted(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "A", 100, "2026-06-09T10:00:00+09:00")
    client = FakeDetailClient({
        "https://www.buyma.com/item/100/": (404, SAMPLE_404_HTML),
    })
    status = classify_disappeared_item(
        conn, client, item_id="100", now="2026-06-09T11:00:00+09:00",
        on_error=lambda **kw: None,
    )
    assert status == ItemStatus.DELETED
    row = get_item(conn, "100")
    assert row["status"] == "DELETED"
    assert row["deleted_at"] == "2026-06-09T11:00:00+09:00"


def test_classify_disappeared_200_marks_sold_out(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "A", 100, "2026-06-09T10:00:00+09:00")
    client = FakeDetailClient({
        "https://www.buyma.com/item/100/": (200, SAMPLE_DETAIL_HTML),
    })
    status = classify_disappeared_item(
        conn, client, item_id="100", now="2026-06-09T11:00:00+09:00",
        on_error=lambda **kw: None,
    )
    assert status == ItemStatus.SOLD_OUT
    row = get_item(conn, "100")
    assert row["status"] == "SOLD_OUT"
    assert row["sold_out_at"] == "2026-06-09T11:00:00+09:00"


def test_enrich_records_error_on_fetch_failure(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "100", "S1", "A", 100, "2026-06-09T10:00:00+09:00")
    class BrokenClient:
        def get(self, url):
            raise RuntimeError("network down")
    errors = []
    enrich_new_item(conn, BrokenClient(), item_id="100",
                    now="2026-06-09T11:00:00+09:00",
                    on_error=lambda **kw: errors.append(kw))
    assert len(errors) == 1
    assert errors[0]["stage"] == "enrich"
    # detail_fetched_at NOT updated on failure
    assert get_item(conn, "100")["detail_fetched_at"] is None
```

Also add `from pathlib import Path` import at the top if not present (it already is from the earlier portion of the file — verify before adding).

- [ ] **Step 2: Run new tests to verify they fail**

```bash
.venv/bin/pytest tests/test_monitor.py -v -k "enrich or classify"
```

Expected: ImportError on `enrich_new_item` / `classify_disappeared_item`.

- [ ] **Step 3: Add enrich functions to crawler/monitor.py**

Append to `crawler/monitor.py`:

```python
from typing import Callable

from crawler.item_detail import build_item_detail_url, parse_item_detail
from crawler.item_status import ItemStatus, classify_status_from_response
from storage.items_repo import update_detail_fields, mark_status


def enrich_new_item(
    conn: sqlite3.Connection,
    client,
    item_id: str,
    now: str,
    on_error: Callable[..., None],
) -> None:
    """Fetch /item/{id}/ and write full metadata to the items row."""
    url = build_item_detail_url(item_id)
    try:
        resp = client.get(url)
    except Exception as e:
        on_error(stage="enrich", url=url,
                 status=getattr(e, "last_status", None), reason=repr(e))
        return

    meta = parse_item_detail(resp.text)
    update_detail_fields(
        conn,
        item_id=item_id,
        brand=meta["brand"],
        category_path=meta["category_path"],
        origin_country=meta["origin_country"],
        image_url=meta["image_url"],
        description=meta["description"],
        raw_meta_json=meta["raw_meta_json"],
        fetched_at=now,
    )


def classify_disappeared_item(
    conn: sqlite3.Connection,
    client,
    item_id: str,
    now: str,
    on_error: Callable[..., None],
) -> ItemStatus | None:
    """Re-fetch and classify a disappeared item, persisting the status.

    Returns the classified status, or None on fetch error (item left untouched
    — caller may retry next run).
    """
    url = build_item_detail_url(item_id)
    try:
        resp = client.get(url)
    except Exception as e:
        on_error(stage="status_check", url=url,
                 status=getattr(e, "last_status", None), reason=repr(e))
        return None

    status = classify_status_from_response(resp.status_code, resp.text)
    mark_status(conn, item_id, status.value, now)
    return status
```

- [ ] **Step 4: Run all monitor tests**

```bash
.venv/bin/pytest tests/test_monitor.py -v
```

Expected: 9 passed (5 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add crawler/monitor.py tests/test_monitor.py
git commit -m "feat: enrich new items and classify disappeared items via detail fetch"
```

---

### Task 12: HttpClient — non-raising variant for status checks

**Files:**
- Modify: `crawler/client.py`
- Create: `tests/test_client_status_check.py`

Current `HttpClient.get` raises `MaxRetriesExceeded` on any 4xx, which means we can never observe a 404 body. We add a sibling method `get_allowing_4xx` that returns the response for 404/410 instead of raising. We keep `get`'s strict contract intact (other callers depend on it).

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_status_check.py`:

```python
import httpx
import pytest

from crawler.client import HttpClient, MaxRetriesExceeded


def _make_transport(handler):
    return httpx.MockTransport(handler)


def test_get_allowing_4xx_returns_404_response():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found body")
    client = HttpClient(transport=_make_transport(handler), sleep_seconds=0)
    try:
        r = client.get_allowing_4xx("https://example.test/")
        assert r.status_code == 404
        assert r.text == "not found body"
    finally:
        client.close()


def test_get_allowing_4xx_returns_200_response():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok body")
    client = HttpClient(transport=_make_transport(handler), sleep_seconds=0)
    try:
        r = client.get_allowing_4xx("https://example.test/")
        assert r.status_code == 200
        assert r.text == "ok body"
    finally:
        client.close()


def test_get_allowing_4xx_still_raises_on_persistent_5xx():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="")
    client = HttpClient(transport=_make_transport(handler), sleep_seconds=0)
    try:
        with pytest.raises(MaxRetriesExceeded):
            client.get_allowing_4xx("https://example.test/")
    finally:
        client.close()


def test_get_strict_still_raises_on_404():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="x")
    client = HttpClient(transport=_make_transport(handler), sleep_seconds=0)
    try:
        with pytest.raises(MaxRetriesExceeded):
            client.get("https://example.test/")
    finally:
        client.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_client_status_check.py -v
```

Expected: AttributeError — `get_allowing_4xx` missing.

- [ ] **Step 3: Add the method to HttpClient**

In `crawler/client.py`, add this method inside the `HttpClient` class, right after `get`:

```python
    def get_allowing_4xx(self, url: str) -> FetchResult:
        """Like get(), but returns the response for 404/410 instead of raising.

        Used by status classification: a 404 is meaningful data, not an error.
        Other 4xx (e.g. 403) still raise. 5xx still retry/raise as before.
        """
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

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_client_status_check.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add crawler/client.py tests/test_client_status_check.py
git commit -m "feat: add HttpClient.get_allowing_4xx for status-classification fetches"
```

---

### Task 13: Run loop — parallel sellers, parallel enrichment, run summary

**Files:**
- Modify: `crawler/monitor.py`
- Modify: `tests/test_monitor.py`

The top-level `run_monitor` function:
1. Loads sellers from `data/sellers.json`
2. Spawns a thread pool of N scan workers, each owning its own HttpClient. Each worker pulls seller_ids from a queue, runs `scan_seller_items`, then `apply_seller_scan_to_db` (DB ops serialized via a single Lock since sqlite3 default thread-safety is "serialized" but we want to keep transactions clean).
3. After all scans complete, collects all new_item_ids and disappeared_item_ids across sellers.
4. Spawns a second thread pool for enrichment. Workers use `get` for new items, `get_allowing_4xx` for disappeared.
5. Records a `monitor_runs` row with the run summary.

- [ ] **Step 1: Write the failing test (append to tests/test_monitor.py)**

Append to `tests/test_monitor.py`:

```python
from crawler.monitor import run_monitor, RunSummary


def test_run_monitor_end_to_end_smoke(tmp_path: Path, monkeypatch):
    """Smoke test: 2 sellers, fake HTTP, full pipeline including DB writes."""
    db_path = tmp_path / "items.db"
    conn = connect(db_path); init_schema(conn); conn.close()

    # Pre-populate one disappeared scenario: seller S1 had item "old1" before.
    conn = connect(db_path)
    upsert_scanned_item(conn, "old1", "S1", "Old", 999, "2026-06-08T10:00:00+09:00")
    conn.close()

    def _items_page(sid: str, items: list[str]) -> str:
        cards = "".join(
            f'<li class="buyeritemtable_info">'
            f'<p class="buyeritem_name"><a href="/item/{iid}/">N{iid}</a></p>'
            f'<p class="buyeritem_price">¥{500 + i}</p></li>'
            for i, iid in enumerate(items)
        )
        return f"<html><body><ul>{cards}</ul></body></html>"

    fake_responses = {
        "https://www.buyma.com/buyer/S1/item_1.html": (200, _items_page("S1", ["new1"])),
        "https://www.buyma.com/buyer/S2/item_1.html": (200, _items_page("S2", ["new2"])),
        "https://www.buyma.com/item/new1/": (200, SAMPLE_DETAIL_HTML),
        "https://www.buyma.com/item/new2/": (200, SAMPLE_DETAIL_HTML),
        "https://www.buyma.com/item/old1/": (404, SAMPLE_404_HTML),
    }

    def client_factory():
        return FakeDetailClient(fake_responses)

    sellers = {"S1": {"seller_id": "S1"}, "S2": {"seller_id": "S2"}}

    summary = run_monitor(
        db_path=db_path,
        sellers=sellers,
        scan_client_factory=client_factory,
        enrich_client_factory=client_factory,
        num_workers=2,
        now="2026-06-09T10:00:00+09:00",
        on_error=lambda **kw: None,
    )
    assert isinstance(summary, RunSummary)
    assert summary.sellers_scanned == 2
    assert summary.items_new == 2
    assert summary.items_deleted == 1

    conn = connect(db_path)
    assert conn.execute("SELECT status FROM items WHERE item_id='new1'").fetchone()["status"] == "ACTIVE"
    assert conn.execute("SELECT brand FROM items WHERE item_id='new1'").fetchone()["brand"]
    assert conn.execute("SELECT status FROM items WHERE item_id='old1'").fetchone()["status"] == "DELETED"
    runs = conn.execute("SELECT * FROM monitor_runs").fetchall()
    assert len(runs) == 1 and runs[0]["finished_at"] is not None
    conn.close()
```

Note: this test treats `FakeDetailClient` as both scan and enrich client. That's fine because the fake serves both `/buyer/.../item_N.html` and `/item/.../` URLs from the same dict. The test does NOT exercise `get_allowing_4xx` on the fake — instead the fake's `get` already returns 404 without raising. Production code uses `get_allowing_4xx` for disappeared classification; the orchestrator must call the right method. We test that wiring by counting `summary.items_deleted == 1`.

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_monitor.py::test_run_monitor_end_to_end_smoke -v
```

Expected: ImportError on `run_monitor` or `RunSummary`.

- [ ] **Step 3: Implement run_monitor in crawler/monitor.py**

Append to `crawler/monitor.py`:

```python
import queue
import threading
from dataclasses import field
from pathlib import Path

from storage.db import connect, init_schema


@dataclass
class RunSummary:
    sellers_scanned: int = 0
    items_new: int = 0
    items_updated: int = 0  # price changes
    items_sold_out: int = 0
    items_deleted: int = 0
    errors: int = 0


def run_monitor(
    db_path: Path | str,
    sellers: dict[str, dict],
    scan_client_factory: Callable[[], object],
    enrich_client_factory: Callable[[], object],
    num_workers: int,
    now: str,
    on_error: Callable[..., None],
) -> RunSummary:
    """Run one full scan+enrich pass over all sellers. Single SQLite DB."""
    from crawler.seller_items_crawler import scan_seller_items

    db_path = Path(db_path)
    summary = RunSummary()

    main_conn = connect(db_path)
    init_schema(main_conn)
    run_id = main_conn.execute(
        "INSERT INTO monitor_runs (started_at) VALUES (?)", (now,)
    ).lastrowid
    db_lock = threading.Lock()

    # --- Stage A: scan + reconcile -----------------------------------------
    seller_queue: queue.Queue[str] = queue.Queue()
    for sid in sellers:
        seller_queue.put(sid)

    new_items: list[tuple[str, str]] = []          # (item_id, seller_id)
    disappeared_items: list[tuple[str, str]] = []  # (item_id, seller_id)
    error_count = [0]
    error_lock = threading.Lock()

    def tracking_on_error(**kw):
        with error_lock:
            error_count[0] += 1
        on_error(**kw)

    def scan_worker():
        client = scan_client_factory()
        try:
            while True:
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

    summary.sellers_scanned = len(sellers)

    # --- Stage B: enrich new + classify disappeared ------------------------
    enrich_queue: queue.Queue[tuple[str, str]] = queue.Queue()  # (kind, item_id)
    for iid, _ in new_items:
        enrich_queue.put(("new", iid))
    for iid, _ in disappeared_items:
        enrich_queue.put(("disappeared", iid))

    def enrich_worker():
        client = enrich_client_factory()
        try:
            while True:
                try:
                    kind, iid = enrich_queue.get_nowait()
                except queue.Empty:
                    return
                if kind == "new":
                    with db_lock:
                        enrich_new_item(main_conn, client, iid, now, tracking_on_error)
                elif kind == "disappeared":
                    status = classify_disappeared_item(
                        main_conn if False else main_conn,  # always main_conn
                        client, iid, now, tracking_on_error,
                    )
                    with db_lock:
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

Note on `classify_disappeared_item`: it currently makes its own DB writes via `mark_status`. Those writes are not guarded by `db_lock` if called directly inside the worker. Wrap the call:

Replace the `elif kind == "disappeared":` block above with:

```python
                elif kind == "disappeared":
                    with db_lock:
                        status = classify_disappeared_item(
                            main_conn, client, iid, now, tracking_on_error,
                        )
                        if status is ItemStatus.DELETED:
                            summary.items_deleted += 1
                        elif status is ItemStatus.SOLD_OUT:
                            summary.items_sold_out += 1
```

(The HTTP fetch happens inside the lock too — that serializes enrichment fetches across workers, which defeats parallelism. Fix in Step 4.)

- [ ] **Step 4: Split fetch (parallel) from DB write (serialized) for classify_disappeared**

Refactor: do the HTTP fetch outside the lock, then acquire the lock only for the DB mutation. Replace `classify_disappeared_item` in `crawler/monitor.py` with a two-function split:

```python
def fetch_for_classification(client, item_id: str, on_error):
    """HTTP fetch only — returns (status_code, body) or None on error."""
    url = build_item_detail_url(item_id)
    try:
        resp = client.get_allowing_4xx(url) if hasattr(client, "get_allowing_4xx") else client.get(url)
    except Exception as e:
        on_error(stage="status_check", url=url,
                 status=getattr(e, "last_status", None), reason=repr(e))
        return None
    return resp.status_code, resp.text


def apply_classification(conn, item_id: str, status_code: int, now: str) -> ItemStatus:
    """DB write only — caller holds the db_lock."""
    status = classify_status_from_response(status_code, "")
    mark_status(conn, item_id, status.value, now)
    return status
```

And keep `classify_disappeared_item` as a convenience wrapper (used by Task 11 tests):

```python
def classify_disappeared_item(conn, client, item_id, now, on_error):
    fetched = fetch_for_classification(client, item_id, on_error)
    if fetched is None:
        return None
    status_code, _ = fetched
    return apply_classification(conn, item_id, status_code, now)
```

In the enrich worker, replace the disappeared branch with:

```python
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
```

Similarly split `enrich_new_item` so the fetch is outside the lock:

```python
def fetch_for_enrich(client, item_id, on_error):
    url = build_item_detail_url(item_id)
    try:
        resp = client.get(url)
    except Exception as e:
        on_error(stage="enrich", url=url,
                 status=getattr(e, "last_status", None), reason=repr(e))
        return None
    return resp.text


def apply_enrich(conn, item_id: str, html: str, now: str) -> None:
    meta = parse_item_detail(html)
    update_detail_fields(
        conn, item_id=item_id,
        brand=meta["brand"], category_path=meta["category_path"],
        origin_country=meta["origin_country"], image_url=meta["image_url"],
        description=meta["description"], raw_meta_json=meta["raw_meta_json"],
        fetched_at=now,
    )
```

And in the enrich worker:

```python
                if kind == "new":
                    body = fetch_for_enrich(client, iid, tracking_on_error)
                    if body is not None:
                        with db_lock:
                            apply_enrich(main_conn, iid, body, now)
```

- [ ] **Step 5: Run all tests**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests pass (the prior `enrich_new_item` / `classify_disappeared_item` tests still pass via the wrapper functions).

- [ ] **Step 6: Commit**

```bash
git add crawler/monitor.py tests/test_monitor.py
git commit -m "feat: run_monitor orchestrator — parallel scan + parallel enrich + run summary"
```

---

### Task 14: CLI entry point

**Files:**
- Create: `monitor_cli.py`

- [ ] **Step 1: Implement the CLI**

Create `monitor_cli.py`:

```python
"""Run the monitoring pipeline once over all sellers in data/sellers.json."""
import argparse
import json
import logging
import sys
from pathlib import Path

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
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if not SELLERS_PATH.exists():
        logging.error("sellers.json not found at %s — run main.py first", SELLERS_PATH)
        return 1

    sellers = json.loads(SELLERS_PATH.read_text(encoding="utf-8"))
    if args.limit_sellers:
        sellers = dict(list(sellers.items())[: args.limit_sellers])
    logging.info("Loaded %d sellers from %s", len(sellers), SELLERS_PATH)

    def factory():
        return HttpClient(sleep_seconds=args.sleep)

    now = now_iso()
    logging.info("Starting monitor run at %s (workers=%d, sleep=%.2fs)",
                 now, args.workers, args.sleep)

    summary = run_monitor(
        db_path=DB_PATH,
        sellers=sellers,
        scan_client_factory=factory,
        enrich_client_factory=factory,
        num_workers=args.workers,
        now=now,
        on_error=append_error_jsonl,
    )
    logging.info("Done: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke test the CLI against 1 seller**

```bash
.venv/bin/python3 monitor_cli.py --limit-sellers 1 --workers 2 --verbose
```

Expected:
- Log line `Loaded 401 sellers ...` then `... using 1 sellers` (limited)
- Log lines from HTTP fetches
- Final `Done: RunSummary(sellers_scanned=1, items_new=N, ...)` with N > 0
- `data/items.db` created
- `data/errors.log` either absent or empty

Verify DB:

```bash
.venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('data/items.db')
print('items:', conn.execute('SELECT COUNT(*) FROM items').fetchone()[0])
print('enriched:', conn.execute(\"SELECT COUNT(*) FROM items WHERE brand IS NOT NULL\").fetchone()[0])
print('runs:', conn.execute('SELECT * FROM monitor_runs').fetchall())
"
```

Expected: items > 0, enriched > 0, runs shows one finished row.

- [ ] **Step 3: Commit**

```bash
git add monitor_cli.py
git commit -m "feat: CLI entry point for monitor pipeline (workers/sleep/limit flags)"
```

---

### Task 15: Documentation note for sold-out refinement (Task placeholder for Phase 2)

**Files:**
- Modify: `crawler/item_status.py`

Add a clear inline note (one short comment line) so the Phase-1 simplification is visible to anyone reading the code:

- [ ] **Step 1: Verify the existing simplification comment**

Open `crawler/item_status.py` — confirm the module docstring already explains the Phase 1 policy. If yes, no change needed; if missing, ensure the docstring written in Task 8 is intact.

- [ ] **Step 2: Add a TODO marker referencing the plan task**

Inside the `if status_code == 200:` branch of `classify_status_from_response`, the line `_ = body` exists. Replace it with:

```python
        # TODO(phase-2): refine 200=SOLD_OUT by inspecting DOM for in-stock signal.
        # Plan: docs/superpowers/plans/2026-06-09-product-monitoring-pipeline.md (refinement task)
        _ = body
```

- [ ] **Step 3: Verify tests still pass**

```bash
.venv/bin/pytest tests/test_item_status.py -v
```

Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add crawler/item_status.py
git commit -m "docs: mark sold-out classification as Phase 1 simplification"
```

---

### Task 16: Full test sweep + README pointer

**Files:**
- Modify: `requirements.txt` (no-op verify)
- Verify all tests pass together

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests in all files pass. Total ~30+ tests.

- [ ] **Step 2: Confirm no leftover fixture/file**

```bash
git status
```

Expected: clean working tree (everything committed).

- [ ] **Step 3: Final commit (if anything left over) — otherwise skip**

If `git status` is clean, skip to verification. If anything is untracked or modified, address it explicitly (don't blanket `git add -A`).

---

## Out-of-Scope (Future Work, Not in This Plan)

These were discussed during planning but are intentionally not in scope:

- **First full crawl tooling** — running the pipeline against an empty DB will discover and enrich all ~1.2M items, taking ~2 days. No special checkpoint/resume CLI is provided here; the in-process retry + per-request idempotency (UPSERT) means a Ctrl-C-and-restart is safe but will re-scan completed sellers. A dedicated `--resume` flag and seller-level checkpoint table is a future enhancement.
- **Scheduling/cron** — operator runs `monitor_cli.py` manually or via OS-level cron. No in-app scheduler.
- **Reports/alerts** — no notification on price drops, new items, or stockouts. Consumers query `data/items.db` directly.
- **Sold-out DOM refinement** — Task 14 marks the TODO; refinement requires real sold-out page samples.

---

## Self-Review

**Spec coverage:**
- ✅ Per-item scan via item-list page only (Tasks 5, 9, 10)
- ✅ Price change detection + price_history (Tasks 3, 10)
- ✅ first_seen_at / last_seen_at maintained (Tasks 3, 10)
- ✅ Detail page enrichment for NEW items (Tasks 7, 11)
- ✅ SOLD_OUT vs DELETED classification (Tasks 8, 11, 13)
- ✅ ID never reused — relied on (PK on item_id)
- ✅ Empty scan does not mark items disappeared (Task 10 test)
- ✅ Max metadata captured incl. raw_meta_json (Task 7)
- ✅ SQLite storage (Task 2)
- ✅ Parallel workers, sleep configurable (Tasks 13, 14)

**Placeholder scan:** No "TBD"/"implement later"/"add appropriate error handling". The single explicit TODO (Task 15) is intentional and references this plan.

**Type consistency:** `apply_seller_scan_to_db` returns `SellerScanOutcome`, used identically in Tasks 10, 13. `RunSummary` field names (`sellers_scanned`, `items_new`, `items_updated`, `items_sold_out`, `items_deleted`, `errors`) match the `monitor_runs` columns in Task 2. `ItemStatus` enum values (`DELETED`, `SOLD_OUT`) match string literals in `mark_status`.
