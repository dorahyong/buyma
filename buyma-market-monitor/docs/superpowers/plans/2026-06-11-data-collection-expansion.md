# Data Collection Expansion (Project 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand BUYMA item-detail collection (all images, full description, size guide, view/fav counts, brand model number, themes, color/size/stock variants, size chart) and remove the bloated `raw_meta_json` column, replacing it with normalized tables.

**Architecture:** Changes concentrate in the Stage B (enrich) parser and storage layers. `crawler/item_detail.py` gains new extraction helpers and drops `raw_meta_json`. `storage/db.py` bumps to SCHEMA_VERSION 2 with three new tables (`item_images`, `stats_history`, `item_variants`) and seven new `items` columns. `storage/items_repo.py` gets a revised `update_detail_fields` plus three new writers. `crawler/monitor.py::apply_enrich` orchestrates all writes in one transaction. DB is reset (existing 3,605 enriched rows discarded).

**Tech Stack:** Python 3.14, BeautifulSoup4 + lxml, sqlite3 (stdlib), pytest. All parsing from static HTML — no Playwright.

---

## Branch Context

Work happens on branch `feat/data-collection-expansion`. The orders/sellers DB-migration work has **already been merged into this branch** (via `git merge main`), so `storage/db.py` now contains `orders`, `order_watermarks`, `order_run_meta`, and `sellers` tables at SCHEMA_VERSION 2. This plan ADDS to that same v2 schema (items detail columns + `item_images`/`stats_history`/`item_variants`) — it does NOT bump the version and MUST preserve the orders/sellers tables. The full suite currently passes at 81 tests (includes the merged orders tests). There is no remaining cross-branch conflict.

---

## File Structure

**Modify:**
- `crawler/item_detail.py` — parser: drop `raw_meta_json`, change `description` source, add 8 new keys (`image_urls`, `view_count`, `fav_count`, `brand_model_number`, `themes`, `variants`, `size_guide_text`, `size_chart`)
- `storage/db.py` — SCHEMA_VERSION 1→2, items columns, 3 new tables
- `storage/items_repo.py` — revise `update_detail_fields`, add `replace_item_images`, `replace_item_variants`, `record_stats_observation`
- `crawler/monitor.py` — `apply_enrich` wires new writers; `enrich_new_item` wrapper unchanged in signature
- `tests/test_item_detail.py` — new field assertions
- `tests/test_items_repo.py` — new writer tests
- `tests/test_db.py` — new table/column assertions
- `tests/test_monitor.py` — `apply_enrich` integration: 4 tables written

**Create:**
- None (all changes modify existing files)

**Reset:**
- `data/items.db` (+ `-wal`/`-shm`) — deleted manually before first run after merge; not a code change

---

## Verified Facts (from live page `https://www.buyma.com/item/133033222/`)

These selectors were confirmed against the live page and the committed fixture `tests/fixtures/item_detail_normal.html` (item 133150020). Note the fixture is a **different item** than the live sample — the fixture is LEMAIRE bag (item 133150020), used by existing tests. New tests must assert against whatever the fixture actually contains, so each task includes a discovery step to read real values from the fixture before writing assertions.

- All images: `ProductGroup.hasVariant[].image[]` (str or list), flatten + dedupe preserving order.
- Full description: `ProductGroup.description`.
- View count: `<span class="ac_count">` text → int.
- Fav count: `<span class="fav_count">` text (e.g. "0人") → int.
- Brand model number: `<dt>` whose text contains "品番", its next `<dd>` text.
- Themes: anchors under the `タグ` (tag) area; exclude UI labels `もっと見る`, `閉じる`.
- Variants: `ProductGroup.hasVariant[]` → `{variant_sku, color, size, price, availability, stock_min, stock_max}`. `availability` = last path segment of schema.org URL (e.g. `.../InStock` → `InStock`). `price` = `offers.price` or `offers.lowPrice`. Stock range from `offers.inventoryLevel`/QuantitativeValue `minValue`/`maxValue` if present.
- Size guide: `<h3>` containing "色・サイズ" — its section text. May be a BUYMA widget (no seller text) → can be empty.
- Size chart (measurements): an HTML `<table>` whose data cells contain "cm" (distinct from the stock-matrix table whose header cells are color names and cells are ○/×). Parse to `{size_name: {measure_name: value}}`.

---

# Tasks

### Task 1: Schema v2 — items columns + new tables

**Files:**
- Modify: `storage/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_schema_v2_items_has_new_columns(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
        for c in ("size_guide_text", "view_count", "fav_count",
                  "brand_model_number", "themes", "size_chart_json"):
            assert c in cols, f"missing column {c}"
        assert "raw_meta_json" not in cols, "raw_meta_json should be removed"
    finally:
        conn.close()


def test_schema_v2_new_tables_exist(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"item_images", "stats_history", "item_variants"}.issubset(names)
    finally:
        conn.close()


def test_schema_version_is_2(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_item_variants_pk_and_index(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    try:
        init_schema(conn)
        idx = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_item_images_item" in idx
        assert "idx_stats_history_item" in idx
        assert "idx_item_variants_item" in idx
    finally:
        conn.close()
```

Note: the existing `test_db.py` has `test_items_pk_and_indexes` and `test_init_schema_creates_tables` and `test_init_schema_is_idempotent`. Those must still pass — `items` keeps `item_id` PK and existing indexes.

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_db.py -v
```

Expected: `test_schema_v2_items_has_new_columns` and `test_schema_v2_new_tables_exist` FAIL (items columns/new tables missing). `test_schema_version_is_2` may already PASS — the orders-migration work (merged into this branch) already set SCHEMA_VERSION=2. `test_item_variants_pk_and_index` FAILS (new indexes missing). Existing orders/sellers tests must keep passing.

- [ ] **Step 3: Implement schema in storage/db.py**

**IMPORTANT:** the merged `storage/db.py` already contains `orders`, `order_watermarks`, `order_run_meta`, and `sellers` tables from the orders-migration work. Those MUST be preserved. This task: (a) changes the `items` table — drop `raw_meta_json`, add 6 detail columns; (b) adds 3 new tables; (c) keeps everything else and SCHEMA_VERSION at 2.

Replace the entire contents of `storage/db.py` with (note orders/sellers tables retained):

```python
"""SQLite connection and schema for the product monitoring pipeline."""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS items (
  item_id             TEXT PRIMARY KEY,
  seller_id           TEXT NOT NULL,
  name                TEXT NOT NULL,
  current_price       INTEGER,
  brand               TEXT,
  category_path       TEXT,
  origin_country      TEXT,
  image_url           TEXT,
  description         TEXT,
  size_guide_text     TEXT,
  view_count          INTEGER,
  fav_count           INTEGER,
  brand_model_number  TEXT,
  themes              TEXT,
  size_chart_json     TEXT,
  status              TEXT NOT NULL,
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  sold_out_at         TEXT,
  deleted_at          TEXT,
  detail_fetched_at   TEXT
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

CREATE TABLE IF NOT EXISTS item_images (
  item_id    TEXT NOT NULL,
  position   INTEGER NOT NULL,
  image_url  TEXT NOT NULL,
  PRIMARY KEY (item_id, position)
);
CREATE INDEX IF NOT EXISTS idx_item_images_item ON item_images(item_id);

CREATE TABLE IF NOT EXISTS stats_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  view_count   INTEGER,
  fav_count    INTEGER,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_stats_history_item ON stats_history(item_id);

CREATE TABLE IF NOT EXISTS item_variants (
  item_id       TEXT NOT NULL,
  variant_sku   TEXT NOT NULL,
  color         TEXT,
  size          TEXT,
  price         INTEGER,
  availability  TEXT,
  stock_min     INTEGER,
  stock_max     INTEGER,
  PRIMARY KEY (item_id, variant_sku)
);
CREATE INDEX IF NOT EXISTS idx_item_variants_item ON item_variants(item_id);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
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

Expected: all tests pass (existing 3 + new 4).

- [ ] **Step 5: Commit**

```bash
git add storage/db.py tests/test_db.py
git commit -m "feat(db): schema v2 — items detail columns + item_images/stats_history/item_variants"
```

---

### Task 2: Parser — image_urls extraction

**Files:**
- Modify: `crawler/item_detail.py`
- Test: `tests/test_item_detail.py`

- [ ] **Step 1: Discover real fixture values**

Run this to learn what the fixture actually contains (assertions must match real data):

```bash
.venv/bin/python3 -c "
from crawler.item_detail import parse_item_detail
import json
html = open('tests/fixtures/item_detail_normal.html', encoding='utf-8').read()
from bs4 import BeautifulSoup
soup = BeautifulSoup(html, 'lxml')
imgs = []
for s in soup.select('script[type=\"application/ld+json\"]'):
    try: d = json.loads(s.string)
    except: continue
    if d.get('@type')=='ProductGroup':
        for v in d.get('hasVariant', []):
            im = v.get('image', [])
            imgs.extend(im if isinstance(im, list) else [im])
uniq = list(dict.fromkeys(imgs))
print('total refs:', len(imgs), 'unique:', len(uniq))
print('first:', uniq[0] if uniq else None)
"
```

Record the printed `unique` count and `first` URL — use them in Step 2's assertions.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_item_detail.py` (replace `<UNIQUE_COUNT>` and `<FIRST_URL>` with the values from Step 1):

```python
def test_parse_item_detail_image_urls():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert isinstance(meta["image_urls"], list)
    assert len(meta["image_urls"]) == <UNIQUE_COUNT>
    # dedupe preserves order, no duplicates
    assert len(meta["image_urls"]) == len(set(meta["image_urls"]))
    assert meta["image_urls"][0] == "<FIRST_URL>"
    assert all(u.startswith("https://") for u in meta["image_urls"])


def test_parse_item_detail_image_urls_empty_when_no_variants():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["image_urls"] == []
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_item_detail.py::test_parse_item_detail_image_urls -v
```

Expected: KeyError on `image_urls`.

- [ ] **Step 4: Implement image extraction**

In `crawler/item_detail.py`, add this helper after `_extract_pg_description` (around line 109):

```python
def _extract_image_urls(product_group: dict | None) -> list[str]:
    """Flatten all variant images, dedupe preserving first-seen order."""
    if not product_group:
        return []
    seen: dict[str, None] = {}
    for v in product_group.get("hasVariant", []) or []:
        if not isinstance(v, dict):
            continue
        img = v.get("image")
        if isinstance(img, list):
            for u in img:
                if isinstance(u, str) and u:
                    seen.setdefault(u, None)
        elif isinstance(img, str) and img:
            seen.setdefault(img, None)
    return list(seen.keys())
```

Then add `"image_urls": _extract_image_urls(product_group),` to the dict returned by `parse_item_detail` (insert before the closing `}` of the return, alongside the other keys).

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_item_detail.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crawler/item_detail.py tests/test_item_detail.py
git commit -m "feat(parser): extract all variant images (deduped, ordered) as image_urls"
```

---

### Task 3: Parser — view_count, fav_count

**Files:**
- Modify: `crawler/item_detail.py`
- Test: `tests/test_item_detail.py`

- [ ] **Step 1: Discover real fixture values**

```bash
.venv/bin/python3 -c "
from bs4 import BeautifulSoup
html = open('tests/fixtures/item_detail_normal.html', encoding='utf-8').read()
soup = BeautifulSoup(html, 'lxml')
ac = soup.select_one('span.ac_count')
fav = soup.select_one('span.fav_count')
print('ac_count:', repr(ac.get_text(strip=True)) if ac else None)
print('fav_count:', repr(fav.get_text(strip=True)) if fav else None)
"
```

Record the values. If a span is absent in the fixture, the test asserts `None` for that field; if present, assert the parsed int.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_item_detail.py`. Adjust the two assertions to match Step 1 (if `ac_count` printed `'12'`, assert `== 12`; if the span was absent/None, assert `is None`):

```python
def test_parse_item_detail_view_fav_counts():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    # Replace with discovered values from Step 1:
    assert meta["view_count"] == <VIEW_INT_OR_None>
    assert meta["fav_count"] == <FAV_INT_OR_None>


def test_parse_item_detail_counts_none_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["view_count"] is None
    assert meta["fav_count"] is None


def test_parse_item_detail_fav_count_strips_suffix():
    # fav_count text like "5人" must parse to 5
    html = '<html><body><span class="fav_count">5人</span></body></html>'
    meta = parse_item_detail(html)
    assert meta["fav_count"] == 5
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_item_detail.py::test_parse_item_detail_fav_count_strips_suffix -v
```

Expected: KeyError on `view_count`/`fav_count`.

- [ ] **Step 4: Implement count extraction**

In `crawler/item_detail.py`, add at the top after the existing imports (the file already has `import json`):

```python
import re

_DIGITS = re.compile(r"[\d,]+")
```

Add this helper after `_extract_image_urls`:

```python
def _extract_int_from_selector(soup, selector: str) -> int | None:
    el = soup.select_one(selector)
    if el is None:
        return None
    m = _DIGITS.search(el.get_text())
    if m is None:
        return None
    return int(m.group(0).replace(",", ""))
```

Add to the `parse_item_detail` return dict:

```python
        "view_count": _extract_int_from_selector(soup, "span.ac_count"),
        "fav_count": _extract_int_from_selector(soup, "span.fav_count"),
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_item_detail.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crawler/item_detail.py tests/test_item_detail.py
git commit -m "feat(parser): extract view_count and fav_count"
```

---

### Task 4: Parser — brand_model_number, themes

**Files:**
- Modify: `crawler/item_detail.py`
- Test: `tests/test_item_detail.py`

- [ ] **Step 1: Discover real fixture values**

```bash
.venv/bin/python3 -c "
from bs4 import BeautifulSoup
import re
html = open('tests/fixtures/item_detail_normal.html', encoding='utf-8').read()
soup = BeautifulSoup(html, 'lxml')
# 品番
bm = None
for dt in soup.find_all('dt'):
    if '品番' in dt.get_text():
        dd = dt.find_next_sibling('dd')
        bm = dd.get_text(' ', strip=True) if dd else None
        break
print('brand_model_number:', repr(bm))
# tags
tagp = soup.find('p', string=re.compile('タグ'))
tags = []
if tagp:
    cont = tagp.find_next(['div','ul'])
    if cont:
        tags = [a.get_text(strip=True) for a in cont.select('a')]
print('raw tags:', tags)
"
```

Record `brand_model_number` and the filtered tag list (drop `もっと見る`, `閉じる`).

- [ ] **Step 2: Write the failing test**

Append to `tests/test_item_detail.py` (substitute discovered values; if `brand_model_number` is None in fixture, assert `is None`):

```python
def test_parse_item_detail_brand_model_number():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["brand_model_number"] == <BM_STR_OR_None>


def test_parse_item_detail_themes_filters_ui_labels():
    html = (
        '<html><body><p>タグ</p><div>'
        '<a href="#">ユニセックス</a><a href="#">ロゴ</a>'
        '<a href="#">もっと見る</a><a href="#">閉じる</a>'
        '</div></body></html>'
    )
    meta = parse_item_detail(html)
    assert meta["themes"] == ["ユニセックス", "ロゴ"]


def test_parse_item_detail_themes_empty_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["themes"] == []
    assert meta["brand_model_number"] is None
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_item_detail.py::test_parse_item_detail_themes_filters_ui_labels -v
```

Expected: KeyError.

- [ ] **Step 4: Implement**

Add module-level constant near the other constants in `crawler/item_detail.py`:

```python
_THEME_UI_LABELS = {"もっと見る", "閉じる"}
```

Add helpers after `_extract_int_from_selector`:

```python
def _extract_brand_model_number(soup) -> str | None:
    for dt in soup.find_all("dt"):
        if "品番" in dt.get_text():
            dd = dt.find_next_sibling("dd")
            if dd is None:
                return None
            text = dd.get_text(" ", strip=True)
            return text or None
    return None


def _extract_themes(soup) -> list[str]:
    tag_p = soup.find("p", string=lambda s: s and "タグ" in s)
    if tag_p is None:
        return []
    container = tag_p.find_next(["div", "ul"])
    if container is None:
        return []
    out: list[str] = []
    for a in container.select("a"):
        t = a.get_text(strip=True)
        if t and t not in _THEME_UI_LABELS:
            out.append(t)
    return out
```

Add to the `parse_item_detail` return dict:

```python
        "brand_model_number": _extract_brand_model_number(soup),
        "themes": _extract_themes(soup),
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_item_detail.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crawler/item_detail.py tests/test_item_detail.py
git commit -m "feat(parser): extract brand_model_number and themes (UI labels filtered)"
```

---

### Task 5: Parser — variants (color/size/price/availability/stock)

**Files:**
- Modify: `crawler/item_detail.py`
- Test: `tests/test_item_detail.py`

- [ ] **Step 1: Discover real fixture values**

```bash
.venv/bin/python3 -c "
from bs4 import BeautifulSoup
import json
html = open('tests/fixtures/item_detail_normal.html', encoding='utf-8').read()
soup = BeautifulSoup(html, 'lxml')
for s in soup.select('script[type=\"application/ld+json\"]'):
    try: d = json.loads(s.string)
    except: continue
    if d.get('@type')=='ProductGroup':
        hv = d.get('hasVariant', [])
        print('variant count:', len(hv))
        if hv:
            v = hv[0]
            off = v.get('offers', {})
            print('sku:', v.get('sku'), 'color:', v.get('color'), 'size:', v.get('size'))
            print('offers keys:', list(off.keys()) if isinstance(off,dict) else type(off))
            print('availability raw:', off.get('availability'))
            print('price:', off.get('price') or off.get('lowPrice'))
        break
"
```

Record variant count, first variant's sku/color/size/price, and the availability format.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_item_detail.py` (substitute discovered values):

```python
def test_parse_item_detail_variants_shape():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert isinstance(meta["variants"], list)
    assert len(meta["variants"]) == <VARIANT_COUNT>
    v = meta["variants"][0]
    assert set(v.keys()) == {
        "variant_sku", "color", "size", "price",
        "availability", "stock_min", "stock_max",
    }
    assert isinstance(v["variant_sku"], str) and v["variant_sku"]
    # availability is bare token, not a URL
    if v["availability"] is not None:
        assert "/" not in v["availability"]


def test_parse_variants_availability_strips_url():
    html = '''<html><body><script type="application/ld+json">
    {"@type":"ProductGroup","hasVariant":[
      {"@type":"Product","sku":"ABC","color":"RED","size":"M",
       "offers":{"price":1000,"availability":"https://schema.org/InStock"}}
    ]}
    </script></body></html>'''
    meta = parse_item_detail(html)
    v = meta["variants"][0]
    assert v["variant_sku"] == "ABC"
    assert v["color"] == "RED"
    assert v["size"] == "M"
    assert v["price"] == 1000
    assert v["availability"] == "InStock"
    assert v["stock_min"] is None
    assert v["stock_max"] is None


def test_parse_variants_stock_range():
    html = '''<html><body><script type="application/ld+json">
    {"@type":"ProductGroup","hasVariant":[
      {"@type":"Product","sku":"X","offers":{"lowPrice":500,
       "inventoryLevel":{"@type":"QuantitativeValue","minValue":7,"maxValue":15}}}
    ]}
    </script></body></html>'''
    meta = parse_item_detail(html)
    v = meta["variants"][0]
    assert v["price"] == 500
    assert v["stock_min"] == 7
    assert v["stock_max"] == 15


def test_parse_variants_empty_when_no_product_group():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["variants"] == []
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_item_detail.py::test_parse_variants_availability_strips_url -v
```

Expected: KeyError on `variants`.

- [ ] **Step 4: Implement**

Add helpers after `_extract_themes`:

```python
def _availability_token(raw) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    return raw.rstrip("/").rsplit("/", 1)[-1]


def _variant_price(offers: dict) -> int | None:
    for key in ("price", "lowPrice"):
        v = offers.get(key)
        if v is None:
            continue
        try:
            return int(float(v))
        except (TypeError, ValueError):
            continue
    return None


def _variant_stock(offers: dict) -> tuple[int | None, int | None]:
    inv = offers.get("inventoryLevel")
    if not isinstance(inv, dict):
        return (None, None)
    lo = inv.get("minValue")
    hi = inv.get("maxValue")
    def _i(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    return (_i(lo), _i(hi))


def _extract_variants(product_group: dict | None) -> list[dict]:
    if not product_group:
        return []
    out: list[dict] = []
    for v in product_group.get("hasVariant", []) or []:
        if not isinstance(v, dict):
            continue
        sku = v.get("sku")
        if sku is None:
            continue
        offers = v.get("offers")
        if not isinstance(offers, dict):
            offers = {}
        stock_min, stock_max = _variant_stock(offers)
        out.append({
            "variant_sku": str(sku),
            "color": v.get("color"),
            "size": v.get("size"),
            "price": _variant_price(offers),
            "availability": _availability_token(offers.get("availability")),
            "stock_min": stock_min,
            "stock_max": stock_max,
        })
    return out
```

Add to `parse_item_detail` return dict:

```python
        "variants": _extract_variants(product_group),
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_item_detail.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crawler/item_detail.py tests/test_item_detail.py
git commit -m "feat(parser): extract color/size/price/availability/stock variants"
```

---

### Task 6: Parser — size_guide_text, size_chart

**Files:**
- Modify: `crawler/item_detail.py`
- Test: `tests/test_item_detail.py`

- [ ] **Step 1: Discover real fixture values**

```bash
.venv/bin/python3 -c "
from bs4 import BeautifulSoup
import re
html = open('tests/fixtures/item_detail_normal.html', encoding='utf-8').read()
soup = BeautifulSoup(html, 'lxml')
# size_guide: h3 색・사이즈
h3 = soup.find('h3', string=re.compile('色・サイズ'))
print('size_guide h3 found:', h3 is not None)
# measurement table: cells with cm
meas = None
for t in soup.find_all('table'):
    if t.get_text().count('cm') >= 2:
        rows = t.find_all('tr')
        hdr = [c.get_text(strip=True) for c in rows[0].find_all(['th','td'])]
        print('measurement table header:', hdr)
        # first data row
        if len(rows) > 1:
            cells = [c.get_text(strip=True) for c in rows[1].find_all(['th','td'])]
            print('first data row:', cells)
        meas = True
        break
print('has measurement table:', meas is not None)
"
```

Record whether the fixture has a measurement table and its header/first row. The fixture (LEMAIRE bag) may have a different chart shape than the shorts sample — write assertions against what actually prints. If the fixture has no measurement table, `size_chart` asserts `None` for the fixture and the synthetic test below covers the parse logic.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_item_detail.py`:

```python
def test_parse_size_chart_from_measurement_table():
    html = '''<html><body><table>
      <tr><th>サイズの名称</th><th>ウエスト</th><th>ヒップ</th></tr>
      <tr><td>S</td><td>68.0cm</td><td>110.0cm</td></tr>
      <tr><td>M</td><td>76.0cm</td><td>114.0cm</td></tr>
    </table></body></html>'''
    meta = parse_item_detail(html)
    assert meta["size_chart"] == {
        "S": {"ウエスト": "68.0cm", "ヒップ": "110.0cm"},
        "M": {"ウエスト": "76.0cm", "ヒップ": "114.0cm"},
    }


def test_size_chart_ignores_stock_matrix_table():
    # stock matrix has color-name headers and ○/× cells, NO cm — must be ignored
    html = '''<html><body><table>
      <tr><th>サイズの名称</th><th>DARK GREY</th><th>NAVY</th></tr>
      <tr><td>S</td><td>○</td><td>○</td></tr>
    </table></body></html>'''
    meta = parse_item_detail(html)
    assert meta["size_chart"] is None


def test_size_guide_text_present_or_none():
    # synthetic seller-written size section
    html = '<html><body><h3>色・サイズ</h3><div>S=韓国95 M=韓国100</div></body></html>'
    meta = parse_item_detail(html)
    assert meta["size_guide_text"] is not None
    assert "韓国95" in meta["size_guide_text"]


def test_size_guide_none_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["size_guide_text"] is None
    assert meta["size_chart"] is None
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_item_detail.py::test_parse_size_chart_from_measurement_table -v
```

Expected: KeyError.

- [ ] **Step 4: Implement**

Add helpers after `_extract_variants`:

```python
def _extract_size_chart(soup) -> dict | None:
    """Parse the measurement table (data cells contain 'cm') into
    {size_name: {measure_name: value}}. The stock-matrix table (○/× cells,
    color-name headers, no 'cm') is ignored."""
    for table in soup.find_all("table"):
        if table.get_text().count("cm") < 1:
            continue
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if len(header) < 2:
            continue
        measures = header[1:]  # first column is the size-name label
        chart: dict[str, dict] = {}
        for row in rows[1:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if not cells:
                continue
            size_name = cells[0]
            values = cells[1:]
            if not size_name:
                continue
            chart[size_name] = {
                measures[i]: values[i]
                for i in range(min(len(measures), len(values)))
            }
        return chart or None
    return None


def _extract_size_guide_text(soup) -> str | None:
    h3 = soup.find("h3", string=lambda s: s and "色・サイズ" in s)
    if h3 is None:
        return None
    parts: list[str] = []
    for sib in h3.find_all_next():
        if sib.name in ("h3", "h2"):
            break
        if sib.name in ("div", "p", "dd", "td", "span"):
            t = sib.get_text(" ", strip=True)
            if t:
                parts.append(t)
    text = " ".join(parts).strip()
    return text or None
```

Add to `parse_item_detail` return dict:

```python
        "size_guide_text": _extract_size_guide_text(soup),
        "size_chart": _extract_size_chart(soup),
```

Note on `_extract_size_guide_text`: it collects following siblings' text until the next `<h3>`/`<h2>`. This is a broad heuristic (the spec flagged this for runtime refinement). If the discovery step in Step 1 shows the fixture's `色・サイズ` h3 is a BUYMA widget producing noisy text, that's acceptable — the synthetic test `test_size_guide_text_present_or_none` validates the extraction mechanism, and real seller text will be captured when present.

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_item_detail.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crawler/item_detail.py tests/test_item_detail.py
git commit -m "feat(parser): extract size_chart (measurement table) and size_guide_text"
```

---

### Task 7: Parser — switch description to ProductGroup, drop raw_meta_json

> **⚠️ ATOMIC GROUP (7+8+10):** This task is part of the raw_meta_json removal contract change. Execute Tasks 7, 8, 10 back-to-back; the full-suite green gate is Task 10 Step 4 (not here). Task 9 must already be done before Task 10. See the "Ordering — ATOMIC GROUP" note in Self-Review.

**Files:**
- Modify: `crawler/item_detail.py`
- Test: `tests/test_item_detail.py`

- [ ] **Step 1: Discover real fixture values**

```bash
.venv/bin/python3 -c "
from bs4 import BeautifulSoup
import json
html = open('tests/fixtures/item_detail_normal.html', encoding='utf-8').read()
soup = BeautifulSoup(html, 'lxml')
og = soup.select_one('meta[property=\"og:description\"]')
print('og len:', len(og.get('content','')) if og else 0)
pgd = ''
for s in soup.select('script[type=\"application/ld+json\"]'):
    try: d = json.loads(s.string)
    except: continue
    if d.get('@type')=='ProductGroup': pgd = d.get('description','')
print('ProductGroup desc len:', len(pgd))
"
```

Confirm ProductGroup description length ≥ og length. Record the ProductGroup length for the assertion.

- [ ] **Step 2: Write the failing test**

The existing `test_parse_item_detail_raw_meta_is_json_string` test asserts `raw_meta_json` exists — it must be DELETED. Open `tests/test_item_detail.py`, remove that whole test function, and append:

```python
def test_parse_item_detail_no_raw_meta_json():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert "raw_meta_json" not in meta


def test_parse_item_detail_description_is_full_productgroup():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    # ProductGroup description is the full (untruncated) body
    assert len(meta["description"]) == <PG_DESC_LEN>


def test_description_prefers_productgroup_over_og():
    html = '''<html><head>
      <meta property="og:description" content="SHORT">
      </head><body><script type="application/ld+json">
      {"@type":"ProductGroup","description":"FULL LONG BODY TEXT"}
      </script></body></html>'''
    meta = parse_item_detail(html)
    assert meta["description"] == "FULL LONG BODY TEXT"
```

Also check whether the existing `test_parse_item_detail_extracts_core_fields` asserts anything about `raw_meta_json` or relies on og description length; if it asserts `meta["description"]` is non-empty that still holds. Leave it unless it references `raw_meta_json`.

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_item_detail.py::test_description_prefers_productgroup_over_og tests/test_item_detail.py::test_parse_item_detail_no_raw_meta_json -v
```

Expected: `test_description_prefers_productgroup_over_og` FAILS (current code prefers og), `test_parse_item_detail_no_raw_meta_json` FAILS (raw_meta_json still present).

- [ ] **Step 4: Implement**

In `crawler/item_detail.py`, in `parse_item_detail`:

1. Remove the `raw_meta` dict construction (the block assigning `raw_meta = {"og": og, "twitter": twitter, "json_ld": json_ld}`).
2. Remove `"raw_meta_json": json.dumps(raw_meta, ensure_ascii=False),` from the return dict.
3. Change the description line from:
   ```python
   "description": og.get("description", "") or _extract_pg_description(product_group),
   ```
   to:
   ```python
   "description": _extract_pg_description(product_group) or og.get("description", ""),
   ```
4. Update the function docstring: remove the sentence about `raw_meta_json` and update the "Keys returned" list to include the new keys. The accurate docstring keys line becomes:
   ```
   Keys returned: name, brand, category_path, origin_country, image_url,
   description, image_urls, view_count, fav_count, brand_model_number,
   themes, variants, size_guide_text, size_chart. Missing fields are None
   (or "" / [] ), never raise.
   ```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_item_detail.py -v
```

Expected: all pass (the removed `raw_meta_json` test is gone; new tests pass).

- [ ] **Step 6: Commit**

```bash
git add crawler/item_detail.py tests/test_item_detail.py
git commit -m "feat(parser): description uses full ProductGroup body, drop raw_meta_json"
```

---

### Task 8: Storage — revise update_detail_fields

> **⚠️ ATOMIC GROUP (7+8+10):** Part of the raw_meta_json removal contract change. Do NOT run the full suite after this task — `apply_enrich` (Task 10) has not yet been updated to the new signature. Run only `tests/test_items_repo.py` here; the full-suite gate is Task 10.

**Files:**
- Modify: `storage/items_repo.py`
- Test: `tests/test_items_repo.py`

- [ ] **Step 1: Write the failing test**

The existing `test_update_detail_fields` in `tests/test_items_repo.py` calls `update_detail_fields(... raw_meta_json='{"a":1}' ...)`. That signature is changing. Replace that whole test with:

```python
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
        description="full body",
        size_guide_text="S=95",
        view_count=42,
        fav_count=7,
        brand_model_number="BG0074",
        themes='["ロゴ"]',
        size_chart_json='{"S":{"胸囲":"90cm"}}',
        fetched_at="2026-06-09T11:00:00+09:00",
    )
    row = get_item(conn, "100")
    assert row["brand"] == "LEMAIRE"
    assert row["category_path"] == "バッグ > ショルダー"
    assert row["origin_country"] == "韓国"
    assert row["image_url"] == "https://example.com/x.jpg"
    assert row["description"] == "full body"
    assert row["size_guide_text"] == "S=95"
    assert row["view_count"] == 42
    assert row["fav_count"] == 7
    assert row["brand_model_number"] == "BG0074"
    assert row["themes"] == '["ロゴ"]'
    assert row["size_chart_json"] == '{"S":{"胸囲":"90cm"}}'
    assert row["detail_fetched_at"] == "2026-06-09T11:00:00+09:00"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_items_repo.py::test_update_detail_fields -v
```

Expected: TypeError (unexpected keyword `size_guide_text`, missing `raw_meta_json`).

- [ ] **Step 3: Implement**

Replace `update_detail_fields` in `storage/items_repo.py` with:

```python
def update_detail_fields(
    conn: sqlite3.Connection,
    item_id: str,
    brand: str | None,
    category_path: str | None,
    origin_country: str | None,
    image_url: str | None,
    description: str | None,
    size_guide_text: str | None,
    view_count: int | None,
    fav_count: int | None,
    brand_model_number: str | None,
    themes: str | None,
    size_chart_json: str | None,
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
          size_guide_text = ?,
          view_count = ?,
          fav_count = ?,
          brand_model_number = ?,
          themes = ?,
          size_chart_json = ?,
          detail_fetched_at = ?
        WHERE item_id = ?
        """,
        (brand, category_path, origin_country, image_url, description,
         size_guide_text, view_count, fav_count, brand_model_number,
         themes, size_chart_json, fetched_at, item_id),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_items_repo.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add storage/items_repo.py tests/test_items_repo.py
git commit -m "feat(repo): update_detail_fields writes new detail columns, drops raw_meta_json"
```

---

### Task 9: Storage — replace_item_images, replace_item_variants, record_stats_observation

**Files:**
- Modify: `storage/items_repo.py`
- Test: `tests/test_items_repo.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_items_repo.py`:

```python
from storage.items_repo import (
    replace_item_images, replace_item_variants, record_stats_observation,
)


def test_replace_item_images_inserts_ordered(tmp_path: Path):
    conn = make_conn(tmp_path)
    replace_item_images(conn, "100", ["a.jpg", "b.jpg", "c.jpg"])
    rows = list(conn.execute(
        "SELECT position, image_url FROM item_images WHERE item_id='100' ORDER BY position"
    ))
    assert [(r["position"], r["image_url"]) for r in rows] == [
        (0, "a.jpg"), (1, "b.jpg"), (2, "c.jpg"),
    ]


def test_replace_item_images_replaces_existing(tmp_path: Path):
    conn = make_conn(tmp_path)
    replace_item_images(conn, "100", ["a.jpg", "b.jpg"])
    replace_item_images(conn, "100", ["x.jpg"])
    rows = list(conn.execute(
        "SELECT image_url FROM item_images WHERE item_id='100' ORDER BY position"
    ))
    assert [r["image_url"] for r in rows] == ["x.jpg"]


def test_replace_item_images_empty_clears(tmp_path: Path):
    conn = make_conn(tmp_path)
    replace_item_images(conn, "100", ["a.jpg"])
    replace_item_images(conn, "100", [])
    cnt = conn.execute("SELECT COUNT(*) FROM item_images WHERE item_id='100'").fetchone()[0]
    assert cnt == 0


def test_replace_item_variants(tmp_path: Path):
    conn = make_conn(tmp_path)
    variants = [
        {"variant_sku": "v1", "color": "RED", "size": "S", "price": 1000,
         "availability": "InStock", "stock_min": None, "stock_max": None},
        {"variant_sku": "v2", "color": "RED", "size": "M", "price": 1100,
         "availability": "OutOfStock", "stock_min": 7, "stock_max": 15},
    ]
    replace_item_variants(conn, "100", variants)
    rows = list(conn.execute(
        "SELECT variant_sku, color, size, price, availability, stock_min, stock_max "
        "FROM item_variants WHERE item_id='100' ORDER BY variant_sku"
    ))
    assert len(rows) == 2
    assert rows[0]["variant_sku"] == "v1"
    assert rows[0]["price"] == 1000
    assert rows[1]["availability"] == "OutOfStock"
    assert rows[1]["stock_min"] == 7
    assert rows[1]["stock_max"] == 15


def test_replace_item_variants_replaces_existing(tmp_path: Path):
    conn = make_conn(tmp_path)
    replace_item_variants(conn, "100", [
        {"variant_sku": "v1", "color": "RED", "size": "S", "price": 1,
         "availability": None, "stock_min": None, "stock_max": None}])
    replace_item_variants(conn, "100", [
        {"variant_sku": "v2", "color": "BLUE", "size": "L", "price": 2,
         "availability": None, "stock_min": None, "stock_max": None}])
    rows = list(conn.execute(
        "SELECT variant_sku FROM item_variants WHERE item_id='100'"))
    assert [r["variant_sku"] for r in rows] == ["v2"]


def test_record_stats_observation_inserts_every_time(tmp_path: Path):
    conn = make_conn(tmp_path)
    record_stats_observation(conn, "100", 10, 2, "2026-06-09T10:00:00+09:00")
    # same values, different timestamp → still a new row (unchanged is recorded)
    record_stats_observation(conn, "100", 10, 2, "2026-06-10T10:00:00+09:00")
    rows = list(conn.execute(
        "SELECT observed_at, view_count, fav_count FROM stats_history "
        "WHERE item_id='100' ORDER BY observed_at"
    ))
    assert len(rows) == 2
    assert rows[0]["view_count"] == 10 and rows[0]["fav_count"] == 2


def test_record_stats_observation_ignores_duplicate_timestamp(tmp_path: Path):
    conn = make_conn(tmp_path)
    record_stats_observation(conn, "100", 10, 2, "2026-06-09T10:00:00+09:00")
    record_stats_observation(conn, "100", 99, 99, "2026-06-09T10:00:00+09:00")
    rows = list(conn.execute(
        "SELECT view_count FROM stats_history WHERE item_id='100'"))
    assert len(rows) == 1
    assert rows[0]["view_count"] == 10  # first wins (INSERT OR IGNORE)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_items_repo.py -v -k "images or variants or stats"
```

Expected: ImportError on the three new functions.

- [ ] **Step 3: Implement**

Append to `storage/items_repo.py`:

```python
def replace_item_images(
    conn: sqlite3.Connection,
    item_id: str,
    image_urls: list[str],
) -> None:
    """Replace all images for an item with the given ordered list."""
    conn.execute("DELETE FROM item_images WHERE item_id = ?", (item_id,))
    conn.executemany(
        "INSERT INTO item_images (item_id, position, image_url) VALUES (?, ?, ?)",
        [(item_id, i, url) for i, url in enumerate(image_urls)],
    )


def replace_item_variants(
    conn: sqlite3.Connection,
    item_id: str,
    variants: list[dict],
) -> None:
    """Replace all variants for an item with the given list."""
    conn.execute("DELETE FROM item_variants WHERE item_id = ?", (item_id,))
    conn.executemany(
        """
        INSERT INTO item_variants
          (item_id, variant_sku, color, size, price, availability, stock_min, stock_max)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (item_id, v["variant_sku"], v.get("color"), v.get("size"),
             v.get("price"), v.get("availability"),
             v.get("stock_min"), v.get("stock_max"))
            for v in variants
        ],
    )


def record_stats_observation(
    conn: sqlite3.Connection,
    item_id: str,
    view_count: int | None,
    fav_count: int | None,
    observed_at: str,
) -> None:
    """Record a view/fav observation. Every observation is recorded (unchanged
    too) so that 'missing' and 'unchanged' can be distinguished. Duplicate
    (item_id, observed_at) is ignored."""
    conn.execute(
        "INSERT OR IGNORE INTO stats_history "
        "(item_id, observed_at, view_count, fav_count) VALUES (?, ?, ?, ?)",
        (item_id, observed_at, view_count, fav_count),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_items_repo.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add storage/items_repo.py tests/test_items_repo.py
git commit -m "feat(repo): add replace_item_images, replace_item_variants, record_stats_observation"
```

---

### Task 10: Orchestrator — apply_enrich writes all new data

> **⚠️ ATOMIC GROUP (7+8+10) — GREEN GATE:** This task closes the contract change. Tasks 9, 7, 8 must be done first. Step 4 here runs the FULL suite — that is the green checkpoint for the whole 7+8+10 group.

**Files:**
- Modify: `crawler/monitor.py`
- Test: `tests/test_monitor.py`

- [ ] **Step 1: Write the failing test**

The existing `test_enrich_new_item_writes_detail_fields` test in `tests/test_monitor.py` asserts `brand`, `category_path`, `image_url`, `detail_fetched_at`, and `raw_meta_json` length. The `raw_meta_json` assertion will break. Open `tests/test_monitor.py`, find that test, and replace its body's `raw_meta_json` assertion. Then append a new integration test:

```python
def test_apply_enrich_writes_all_tables(tmp_path: Path):
    conn = make_conn(tmp_path)
    upsert_scanned_item(conn, "133150020", "S1", "x", 100, "2026-06-09T10:00:00+09:00")
    from crawler.monitor import apply_enrich
    html = (Path(__file__).parent / "fixtures" / "item_detail_normal.html").read_text(encoding="utf-8")
    apply_enrich(conn, "133150020", html, "2026-06-09T11:00:00+09:00")

    row = get_item(conn, "133150020")
    assert row["detail_fetched_at"] == "2026-06-09T11:00:00+09:00"
    assert row["description"]  # full body present
    # images
    img_cnt = conn.execute(
        "SELECT COUNT(*) FROM item_images WHERE item_id='133150020'").fetchone()[0]
    assert img_cnt >= 1
    # variants
    var_cnt = conn.execute(
        "SELECT COUNT(*) FROM item_variants WHERE item_id='133150020'").fetchone()[0]
    assert var_cnt >= 1
    # stats observation recorded
    stat_cnt = conn.execute(
        "SELECT COUNT(*) FROM stats_history WHERE item_id='133150020'").fetchone()[0]
    assert stat_cnt == 1
```

For the existing `test_enrich_new_item_writes_detail_fields`: replace the line
`assert row["raw_meta_json"] and len(row["raw_meta_json"]) > 100`
with
`assert row["description"]`.

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_monitor.py::test_apply_enrich_writes_all_tables -v
```

Expected: FAIL — apply_enrich does not yet write images/variants/stats, and still calls update_detail_fields with the old signature (TypeError).

- [ ] **Step 3: Implement**

In `crawler/monitor.py`, find the imports from `storage.items_repo`. They currently include `update_detail_fields`. Add the three new writers:

```python
from storage.items_repo import (
    # ... existing imports ...
    update_detail_fields,
    replace_item_images,
    replace_item_variants,
    record_stats_observation,
)
```

(Merge into the existing import block; do not duplicate names already imported.)

Add `import json` at the top of `crawler/monitor.py` if not already present.

Replace `apply_enrich` with:

```python
def apply_enrich(conn: sqlite3.Connection, item_id: str, html: str, now: str) -> None:
    """DB write only — caller holds the db_lock. Writes items detail columns
    plus item_images, item_variants, and a stats_history observation."""
    meta = parse_item_detail(html)

    themes = meta["themes"]
    size_chart = meta["size_chart"]
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
        brand_model_number=meta["brand_model_number"],
        themes=json.dumps(themes, ensure_ascii=False) if themes else None,
        size_chart_json=json.dumps(size_chart, ensure_ascii=False) if size_chart else None,
        fetched_at=now,
    )
    replace_item_images(conn, item_id, meta["image_urls"])
    replace_item_variants(conn, item_id, meta["variants"])
    record_stats_observation(conn, item_id, meta["view_count"], meta["fav_count"], now)
```

- [ ] **Step 4: Run FULL suite — green gate for the 7+8+10 atomic group**

```bash
.venv/bin/pytest tests/ -v
```

Expected: ALL tests pass. This is the green checkpoint that closes the raw_meta_json contract change. If anything in `test_item_detail.py` or `test_monitor.py` still references `raw_meta_json`, fix it now (the key no longer exists).

- [ ] **Step 5: Commit**

```bash
git add crawler/monitor.py tests/test_monitor.py
git commit -m "feat(monitor): apply_enrich writes detail columns + images + variants + stats"
```

---

### Task 11: Full suite + DB reset note

**Files:**
- Verify only; no code change unless a regression surfaces.

- [ ] **Step 1: Run the full test suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests pass. The `raw_meta_json` references in `test_item_detail.py` and `test_monitor.py` were removed in Tasks 7 and 10. If any test still references `raw_meta_json`, fix it to match the new contract (it should not exist).

- [ ] **Step 2: Grep for leftover raw_meta_json references**

```bash
grep -rn "raw_meta_json" "buyma market monitor/crawler" "buyma market monitor/storage" "buyma market monitor/tests" 2>/dev/null || echo "no references"
```

Expected: `no references`. If any remain in non-test code, they are bugs — fix them.

- [ ] **Step 3: Confirm clean working tree**

```bash
git status --short
```

Expected: clean (everything committed).

- [ ] **Step 4: Document DB reset in commit (no code)**

The DB reset is operational, not code. When this branch is run for the first time, the operator deletes `data/items.db*` so `init_schema` creates v2. Verify the CLI path works against a fresh DB:

```bash
.venv/bin/python3 -c "
import tempfile, os
from storage.db import connect, init_schema
p = tempfile.mktemp(suffix='.db')
conn = connect(p); init_schema(conn)
cols = {r[1] for r in conn.execute('PRAGMA table_info(items)')}
assert 'raw_meta_json' not in cols and 'view_count' in cols
print('fresh v2 schema OK')
conn.close(); os.remove(p)
"
```

Expected: `fresh v2 schema OK`.

No commit needed if the tree is already clean.

---

## Self-Review

**Spec coverage:**
- ✅ raw_meta_json removal (Task 1 schema, Task 7 parser, Task 8 repo)
- ✅ All images → item_images (Task 1, Task 2, Task 9, Task 10)
- ✅ Full description (Task 7)
- ✅ size_guide_text (Task 6, Task 8)
- ✅ view_count/fav_count + stats_history every-observation (Task 1, Task 3, Task 9, Task 10)
- ✅ brand_model_number, themes (Task 4, Task 8)
- ✅ variants color/size/price/availability/stock (Task 1, Task 5, Task 9, Task 10)
- ✅ size_chart_json (Task 1, Task 6, Task 8)
- ✅ DB reset to v2 (Task 1, Task 11)
- ✅ Out of scope honored: no adaptive scheduler, no cart count, no variant-image color mapping

**Placeholder scan:** The `<UNIQUE_COUNT>`, `<FIRST_URL>`, `<VIEW_INT_OR_None>`, `<FAV_INT_OR_None>`, `<BM_STR_OR_None>`, `<VARIANT_COUNT>`, `<PG_DESC_LEN>` tokens are intentional: each is preceded by a discovery step that prints the exact value to substitute. This is required because the committed fixture (item 133150020, LEMAIRE bag) differs from the live verification sample (item 133033222, shorts), so real fixture values must be read at implementation time rather than guessed. Every such token has a Step 1 that produces its value. No "TBD"/"implement later" exists.

**Type consistency:** `update_detail_fields` parameter order (brand, category_path, origin_country, image_url, description, size_guide_text, view_count, fav_count, brand_model_number, themes, size_chart_json, fetched_at) is identical in Task 8 definition and Task 10 caller. Variant dict keys (`variant_sku, color, size, price, availability, stock_min, stock_max`) match across Task 5 parser, Task 9 repo, and Task 10. `parse_item_detail` return keys used in Task 10's `apply_enrich` (`image_urls`, `view_count`, `fav_count`, `brand_model_number`, `themes`, `variants`, `size_guide_text`, `size_chart`, plus existing `brand`/`category_path`/`origin_country`/`image_url`/`description`) all exist after Tasks 2–7.

**Ordering — ATOMIC GROUP (Tasks 7+8+10):** Removing `raw_meta_json` is a contract change that spans three files simultaneously: the parser stops producing the key (Task 7), the repo writer stops accepting it (Task 8), and the orchestrator `apply_enrich` stops passing it (Task 10). These three CANNOT be committed independently and stay green — `apply_enrich` is the seam binding parser output to repo input, so it must flip in lockstep.

**Therefore Tasks 7, 8, and 10 form ONE atomic unit.** The controller/implementer executes them back-to-back as a single logical change and runs the FULL suite (`.venv/bin/pytest tests/ -v`) only after Task 10 — that is the green checkpoint for the whole group. Task 9 (new writers — `replace_item_images`/`replace_item_variants`/`record_stats_observation`) is a pure addition with no contract change, so it can land green on its own; but because Task 10's `apply_enrich` CALLS those writers, **Task 9 must be completed before Task 10.** Recommended execution order: 1→2→3→4→5→6→**9**→(**7**+**8**+**10** atomic)→11. The per-task Step 4/5 "run tests" checks inside Tasks 7 and 8 run only their own file's tests (parser tests, repo tests) as unit-level checkpoints; the suite-wide green gate is Task 10 Step 4. Subagent reviewers should treat 7+8+10 as one reviewable unit and NOT flag the parser-only / repo-only intermediate state as a defect.
