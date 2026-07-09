# BUYMA 주문실적 증분 크롤러 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 셀러 크롤러로 수집된 401명의 한국 셀러에 대해 각자의 sales 페이지를 순회하여 증분 주문 데이터를 JSONL로 누적 저장하는 서브커맨드를 기존 프로젝트에 추가한다.

**Architecture:** 기존 `main.py`를 argparse subcommand 패턴으로 확장 (`crawl-sellers`, `crawl-orders`). 신규 모듈 `crawler/orders.py`와 `storage/orders_store.py`를 추가하고 기존 `crawler/client.py` (PlaywrightClient)와 `storage/store.py` (errors.log)를 재사용. 시그니처 시퀀스 워터마크로 중복 없는 증분 수집. 셀러 단위로 orders.jsonl append + 워터마크 즉시 flush.

**Tech Stack:** Python 3.11+, httpx (재사용), Playwright Chromium (셀러 페이지), BeautifulSoup4 + lxml

**Spec Reference:** [docs/superpowers/specs/2026-06-09-buyma-orders-crawler-design.md](../specs/2026-06-09-buyma-orders-crawler-design.md)

**테스트 정책:** 기존 프로젝트가 `tests/` 디렉토리와 `pytest` 의존성을 제거한 상태이므로 본 계획도 단위 테스트 파일을 만들지 않는다. 검증은 `python3 -c "..."` 인라인 스니펫 또는 실제 BUYMA 페이지에 대한 제한적 스모크 테스트로 수행한다.

---

## File Structure

```
buyma market monitor/
├── main.py                              # MODIFY: argparse subparser 패턴
├── crawler/
│   ├── client.py                        # (재사용) PlaywrightClient
│   ├── listing.py, pagination.py        # (재사용) 셀러 크롤 모듈
│   ├── seller.py                        # (재사용) 셀러 크롤 모듈
│   └── orders.py                        # CREATE: sales 페이지 파싱 + 워터마크 + 증분 순회
├── storage/
│   ├── store.py                         # (재사용) Store: errors.log append
│   └── orders_store.py                  # CREATE: OrdersStore: orders.jsonl + orders_config.json
└── data/
    ├── orders.jsonl                     # 런타임 자동 생성
    └── orders_config.json               # 런타임 자동 생성
```

**파일 책임:**
- `crawler/orders.py`: 셀러 1명의 sales 페이지 증분 크롤링 전 흐름. 외부 의존성은 `client.get(url)` 인터페이스. 순수 파싱 함수 + 1셀러 처리 함수 + N셀러 오케스트레이션 함수.
- `storage/orders_store.py`: orders.jsonl 동시성 안전 append + orders_config.json 즉시 flush.
- `main.py`: 위 모듈 호출만 담당. subcommand 라우팅.

---

## Task 1: argparse subcommand 패턴 도입

**목표:** 기존 `main.py`의 단일 명령을 `crawl-sellers` 서브커맨드로 옮기고, 새 서브커맨드(`crawl-orders`)를 추가할 자리를 마련한다.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 기존 main.py 백업 후 새 구조 작성**

기존 `main.py`는 함수 `run(reset_pagination, dry_run)`을 가지고 있다. 이를 `run_crawl_sellers(args)`로 개명하고 argparse 서브파서를 추가한다.

전체를 다시 작성:

```python
# main.py
"""BUYMA Market Monitor CLI entry point."""
import argparse
import logging
import sys
from pathlib import Path

from crawler.client import HttpClient, PlaywrightClient, MaxRetriesExceeded
from crawler.listing import build_listing_url, crawl_listing_pages
from crawler.pagination import parse_max_pages
from crawler.seller import crawl_sellers_with_factory
from storage.store import Store, now_iso


DATA_DIR = Path(__file__).parent / "data"


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def detect_max_pages(http_client: HttpClient) -> int:
    url = build_listing_url(1)
    logging.info("Detecting max pages from %s", url)
    response = http_client.get(url)
    max_pages = parse_max_pages(response.text)
    logging.info("Detected max_pages = %d", max_pages)
    return max_pages


def run_crawl_sellers(args) -> int:
    store = Store(DATA_DIR)
    config = store.load_config()

    with HttpClient() as http_client:
        if args.reset_pagination or "max_pages" not in config:
            try:
                max_pages = detect_max_pages(http_client)
            except MaxRetriesExceeded as e:
                logging.error("Failed to detect max pages: %s", e)
                return 1
            config["max_pages"] = max_pages
            config["max_pages_detected_at"] = now_iso()
            if not args.dry_run:
                store.save_config(config)
        else:
            max_pages = config["max_pages"]
            logging.info("Using cached max_pages = %d", max_pages)

        logging.info("Stage 1: scanning %d listing pages", max_pages)
        seller_ids = crawl_listing_pages(
            http_client,
            max_pages=max_pages,
            on_error=store.append_error,
        )
        logging.info("Found %d unique seller IDs in listings", len(seller_ids))

    logging.info("Stage 2: fetching %d seller pages via Playwright", len(seller_ids))
    collected = crawl_sellers_with_factory(
        client_factory=lambda: PlaywrightClient(),
        seller_ids=seller_ids,
        on_error=store.append_error,
        num_workers=3,
    )
    logging.info(
        "Collected %d sellers (filtered out %d)",
        len(collected),
        len(seller_ids) - len(collected),
    )

    timestamp = now_iso()
    new_sellers_map: dict[str, dict] = {}
    for seller in collected:
        seller["first_seen_at"] = timestamp
        seller["updated_at"] = timestamp
        new_sellers_map[seller["seller_id"]] = seller

    existing = store.load_sellers()
    merged = Store.merge_sellers(existing, new_sellers_map)

    errors_count = _count_errors_today(store.errors_path)

    config["last_run_at"] = timestamp
    config["last_run_stats"] = {
        "pages_scanned": max_pages,
        "sellers_found_in_listing": len(seller_ids),
        "sellers_collected": len(collected),
        "sellers_filtered_out": len(seller_ids) - len(collected),
        "errors": errors_count,
    }

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

    return 0


def run_crawl_orders(args) -> int:
    # Task 7에서 채울 placeholder
    logging.error("crawl-orders not yet implemented")
    return 1


def _count_errors_today(errors_path: Path) -> int:
    if not errors_path.exists():
        return 0
    today = now_iso()[:10]
    count = 0
    with errors_path.open(encoding="utf-8") as f:
        for line in f:
            if today in line:
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="BUYMA Market Monitor CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # crawl-sellers
    sp_sellers = subparsers.add_parser("crawl-sellers", help="Crawl Korean sellers from BUYMA listing")
    sp_sellers.add_argument("--reset-pagination", action="store_true",
                            help="Force re-detection of max_pages")
    sp_sellers.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    sp_sellers.add_argument("--dry-run", action="store_true", help="Do not write to data files")
    sp_sellers.set_defaults(func=run_crawl_sellers)

    # crawl-orders
    sp_orders = subparsers.add_parser("crawl-orders", help="Crawl incremental orders for known sellers")
    sp_orders.add_argument("--max-pages", type=int, default=10,
                           help="Max sales pages per seller (default: 10)")
    sp_orders.add_argument("--seller-id", type=str, default=None,
                           help="Crawl only this single seller (for debugging)")
    sp_orders.add_argument("--full-rescan", action="store_true",
                           help="Ignore existing watermarks and re-collect everything")
    sp_orders.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    sp_orders.add_argument("--dry-run", action="store_true", help="Do not write to data files")
    sp_orders.set_defaults(func=run_crawl_orders)

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        return args.func(args)
    except KeyboardInterrupt:
        logging.warning("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 기존 호출법 호환성 확인 (--help)**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects/buyma market monitor"
source .venv/bin/activate
python main.py --help
```

Expected: 도움말에 `crawl-sellers`와 `crawl-orders` 서브커맨드가 나타남.

```bash
python main.py crawl-sellers --help
```

Expected: `--reset-pagination`, `--verbose`, `--dry-run` 옵션이 나타남.

```bash
python main.py crawl-orders --help
```

Expected: `--max-pages`, `--seller-id`, `--full-rescan`, `--verbose`, `--dry-run` 옵션이 나타남.

- [ ] **Step 3: 기존 셀러 크롤 회귀 검증 (dry-run)**

기존 데이터를 잠시 백업하고 dry-run으로 정상 동작 확인:

```bash
# data/는 그대로 유지 (cached max_pages 활용)
python main.py crawl-sellers --dry-run --verbose 2>&1 | tail -20
```

Expected: 기존과 동일하게 작동, "DRY RUN: skipping save" 로그 출력, 종료 코드 0. data/sellers.json은 변경되지 않음.

확인:
```bash
md5sum data/sellers.json  # 기존과 동일해야 함
```

- [ ] **Step 4: 커밋**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects"
git add "buyma market monitor/main.py"
git commit -m "refactor: convert main.py to argparse subcommand pattern"
cd "buyma market monitor"
```

---

## Task 2: orders.py 파싱 함수 (build_sales_url, parse_sales_page)

**목표:** sales 페이지 한 장을 HTML 문자열에서 OrderEntry 리스트로 변환하는 순수 함수.

**Files:**
- Create: `crawler/orders.py`

- [ ] **Step 1: 모듈 초기 골격 작성**

```python
# crawler/orders.py
"""BUYMA sales page parsing and incremental orders crawling."""
import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup


SALES_URL_TEMPLATE = "https://www.buyma.com/buyer/{seller_id}/sales_{page}.html"
ITEM_URL_TEMPLATE = "https://www.buyma.com/item/{item_id}/"

_ITEM_HREF_PATTERN = re.compile(r"/item/(\d+)/")
_QTY_PATTERN = re.compile(r"注文\s*数\s*[：:]\s*(\d+)")
_DATE_PATTERN = re.compile(r"成約日\s*[：:]\s*(\d{4}/\d{2}/\d{2})")


@dataclass(frozen=True)
class OrderEntry:
    sale_date: str   # "YYYY/MM/DD"
    item_id: str
    qty: int
    item_name: str
    item_url: str


def build_sales_url(seller_id: str, page: int) -> str:
    return SALES_URL_TEMPLATE.format(seller_id=seller_id, page=page)


def parse_sales_page(html: str) -> list[OrderEntry]:
    """페이지 등장 순서대로 OrderEntry 리스트 반환. 파싱 실패 entry는 스킵."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[OrderEntry] = []
    for li in soup.find_all("li", class_="buyeritemtable_info"):
        entry = _parse_li(li)
        if entry is not None:
            entries.append(entry)
    return entries


def _parse_li(li) -> Optional[OrderEntry]:
    a = li.select_one("p.buyeritem_name a[href]")
    if a is None:
        return None
    href = a.get("href", "")
    m_id = _ITEM_HREF_PATTERN.search(href)
    if m_id is None:
        return None
    item_id = m_id.group(1)
    item_name = a.get_text(strip=True)
    if not item_name:
        return None

    qty: Optional[int] = None
    sale_date: Optional[str] = None
    for p in li.find_all("p"):
        text = p.get_text(" ", strip=True)
        if qty is None:
            m_q = _QTY_PATTERN.search(text)
            if m_q:
                qty = int(m_q.group(1))
        if sale_date is None:
            m_d = _DATE_PATTERN.search(text)
            if m_d:
                sale_date = m_d.group(1)
        if qty is not None and sale_date is not None:
            break

    if qty is None or sale_date is None:
        return None

    return OrderEntry(
        sale_date=sale_date,
        item_id=item_id,
        qty=qty,
        item_name=item_name,
        item_url=ITEM_URL_TEMPLATE.format(item_id=item_id),
    )
```

- [ ] **Step 2: 합성 HTML로 파싱 검증**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects/buyma market monitor"
source .venv/bin/activate
python3 << 'EOF'
from crawler.orders import build_sales_url, parse_sales_page, OrderEntry

# URL 빌더
assert build_sales_url("1415418", 1) == "https://www.buyma.com/buyer/1415418/sales_1.html"
assert build_sales_url("1415418", 7) == "https://www.buyma.com/buyer/1415418/sales_7.html"
print("build_sales_url OK")

# 일본어 풀와이드 콜론 포함 entry 1개
html = '''
<ul>
  <li class="buyeritemtable_info">
    <p class="buyeritem_name">
      <a href="/item/119166289/" data-vt="/vt/buyer/sales_tab/item_images">[STUSSY] STOCK DOG TEE</a>
    </p>
    <p>注文数：1個</p>
    <p>成約日：2026/06/09</p>
  </li>
  <li class="buyeritemtable_info">
    <p class="buyeritem_name">
      <a href="/item/123/">PRODUCT A</a>
    </p>
    <p>注文数：3個</p>
    <p>成約日：2026/06/08</p>
  </li>
</ul>
'''
entries = parse_sales_page(html)
assert len(entries) == 2, f"expected 2, got {len(entries)}"
assert entries[0] == OrderEntry(
    sale_date="2026/06/09", item_id="119166289", qty=1,
    item_name="[STUSSY] STOCK DOG TEE",
    item_url="https://www.buyma.com/item/119166289/",
), entries[0]
assert entries[1].qty == 3 and entries[1].sale_date == "2026/06/08"
print("parse_sales_page (2 entries) OK")

# 빈 페이지
assert parse_sales_page("<html></html>") == []
print("empty page OK")

# 불완전 entry는 스킵
html_bad = '''
<li class="buyeritemtable_info">
  <p class="buyeritem_name"><a href="/item/999/">X</a></p>
  <p>注文数：1個</p>
  <!-- 성약일 누락 -->
</li>
'''
assert parse_sales_page(html_bad) == []
print("incomplete entry skipped OK")

print("ALL parse tests passed")
EOF
```

Expected: 모든 assert 통과, 마지막에 "ALL parse tests passed" 출력.

- [ ] **Step 3: 실제 BUYMA sales 페이지로 검증**

```bash
python3 << 'EOF'
from playwright.sync_api import sync_playwright
from crawler.orders import parse_sales_page

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, locale="ja-JP")
    page = ctx.new_page()
    page.goto("https://www.buyma.com/buyer/1415418/sales_1.html",
              wait_until="domcontentloaded", timeout=30000)
    html = page.content()
    browser.close()

entries = parse_sales_page(html)
print(f"Parsed {len(entries)} entries from real sales_1 (momoha closet)")
assert len(entries) > 0, "expected entries on a real page"
for e in entries[:3]:
    print(f"  {e.sale_date} | item={e.item_id} | qty={e.qty} | {e.item_name[:50]}")
    assert e.sale_date and len(e.sale_date) == 10
    assert e.item_id.isdigit()
    assert e.qty >= 1
    assert e.item_url == f"https://www.buyma.com/item/{e.item_id}/"
print("real page parse OK")
EOF
```

Expected: 30개 내외 entry 출력, 모든 assert 통과.

- [ ] **Step 4: 커밋**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects"
git add "buyma market monitor/crawler/orders.py"
git commit -m "feat: add sales page parser (orders.py)"
cd "buyma market monitor"
```

---

## Task 3: 워터마크 매칭 함수 (find_watermark_boundary)

**목표:** 페이지 entry 리스트 안에서 저장된 워터마크 시퀀스가 등장하는 위치를 찾는 순수 함수.

**Files:**
- Modify: `crawler/orders.py` (append)

- [ ] **Step 1: 함수 추가**

`crawler/orders.py` 파일 끝에 추가:

```python
WatermarkTuple = tuple[str, str, int]  # (sale_date, item_id, qty)


def find_watermark_boundary(
    page_signatures: list[WatermarkTuple],
    watermark: list[WatermarkTuple],
) -> int:
    """페이지 entry signature 리스트에서 워터마크 시퀀스가
    연속된 sublist로 등장하는 시작 위치 반환.

    매칭 안 되면 -1.
    watermark가 빈 리스트면 -1 (신규 셀러).

    page_signatures[:boundary] = 신규 entry로 간주.
    """
    if not watermark:
        return -1
    L = len(watermark)
    P = len(page_signatures)
    if L > P:
        return -1
    for i in range(P - L + 1):
        if page_signatures[i:i + L] == watermark:
            return i
    return -1
```

- [ ] **Step 2: 동작 검증**

```bash
python3 << 'EOF'
from crawler.orders import find_watermark_boundary

# 빈 워터마크 → -1
assert find_watermark_boundary([("2026/06/09", "A", 1)], []) == -1
print("empty watermark OK")

# 페이지에 새 entry 3개 + 워터마크 일치
page = [
    ("2026/06/09", "NEW1", 1),
    ("2026/06/09", "NEW2", 1),
    ("2026/06/09", "NEW3", 1),
    ("2026/06/07", "OLD1", 1),
    ("2026/06/07", "OLD2", 1),
]
wm = [("2026/06/07", "OLD1", 1), ("2026/06/07", "OLD2", 1)]
assert find_watermark_boundary(page, wm) == 3
print("3 new + 2 match OK (boundary=3)")

# 워터마크가 페이지 첫머리부터 매치 (신규 0건)
page2 = [
    ("2026/06/07", "OLD1", 1),
    ("2026/06/07", "OLD2", 1),
]
assert find_watermark_boundary(page2, wm) == 0
print("0 new (boundary=0) OK")

# 워터마크 미발견
page3 = [("2026/06/09", "X", 1), ("2026/06/08", "Y", 1)]
assert find_watermark_boundary(page3, wm) == -1
print("no match (-1) OK")

# 같은 날 중복 entry도 정확히 처리
page4 = [
    ("2026/06/09", "NEW", 1),
    ("2026/06/07", "OLD1", 1),  # 워터마크 첫 요소
    ("2026/06/07", "OLD2", 1),
    ("2026/06/07", "OLD1", 1),  # 우연한 부분일치 (1요소만)
]
wm2 = [("2026/06/07", "OLD1", 1), ("2026/06/07", "OLD2", 1), ("2026/06/07", "OLD1", 1)]
# 페이지에서 wm2 전체 시퀀스 매칭은 위치 1부터
assert find_watermark_boundary(page4, wm2) == 1
print("same-day duplicates matched correctly OK")

# 워터마크가 페이지보다 김 → -1
assert find_watermark_boundary([("2026/06/09", "A", 1)], wm) == -1
print("watermark longer than page → -1 OK")

print("ALL watermark tests passed")
EOF
```

Expected: 모든 assert 통과.

- [ ] **Step 3: 커밋**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects"
git add "buyma market monitor/crawler/orders.py"
git commit -m "feat: add watermark sequence matching"
cd "buyma market monitor"
```

---

## Task 4: 단일 셀러 증분 크롤 함수 (crawl_seller_orders)

**목표:** 한 셀러에 대해 워터마크가 매칭될 때까지 sales_1, sales_2, ... 순회하며 신규 주문을 수집.

**Files:**
- Modify: `crawler/orders.py` (append)

- [ ] **Step 1: 함수 추가**

`crawler/orders.py` 파일 끝에 추가:

```python
WATERMARK_SIZE = 30
DEFAULT_MAX_PAGES = 10


def crawl_seller_orders(
    client,
    seller_id: str,
    watermark: list[WatermarkTuple],
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[OrderEntry], list[WatermarkTuple], list[str]]:
    """한 셀러의 sales 페이지를 순회하여 워터마크 이후 신규 주문만 수집.

    Returns:
      (new_orders, new_watermark, warnings)
      - new_orders: 워터마크 매칭점 이전의 신규 OrderEntry 리스트 (페이지 순서)
      - new_watermark: 셀러의 새 워터마크 (sales_1의 상위 WATERMARK_SIZE개 또는 그 이하)
      - warnings: 경고 메시지 리스트 (max_pages 도달 등)
    """
    accumulated: list[OrderEntry] = []
    warnings: list[str] = []
    page_num_done = 0

    for page_num in range(1, max_pages + 1):
        url = build_sales_url(seller_id, page_num)
        response = client.get(url)
        page_entries = parse_sales_page(response.text)
        page_num_done = page_num
        if not page_entries:
            # 페이지에 entry 없음 → 이 셀러는 더 가질 게 없음. 종료.
            break
        accumulated.extend(page_entries)
        signatures = [(e.sale_date, e.item_id, e.qty) for e in accumulated]
        boundary = find_watermark_boundary(signatures, watermark)
        if boundary >= 0:
            new_orders = accumulated[:boundary]
            new_watermark = _build_new_watermark(accumulated)
            return new_orders, new_watermark, warnings

    # max_pages 도달했는데 매칭 못 함 (또는 페이지에 entry가 더 이상 없어서 break)
    if watermark and page_num_done == max_pages:
        warnings.append(
            f"max_pages={max_pages} reached without watermark match "
            f"for seller {seller_id}: collecting all {len(accumulated)} entries (over-collection)"
        )
    new_watermark = _build_new_watermark(accumulated)
    return accumulated, new_watermark, warnings


def _build_new_watermark(entries: list[OrderEntry]) -> list[WatermarkTuple]:
    return [(e.sale_date, e.item_id, e.qty) for e in entries[:WATERMARK_SIZE]]
```

- [ ] **Step 2: Mock client로 단위 동작 검증**

```bash
python3 << 'EOF'
from unittest.mock import MagicMock
from crawler.orders import crawl_seller_orders, OrderEntry

def make_page_html(entries):
    """entries: list of (sale_date, item_id, qty, item_name)"""
    items = []
    for date, iid, qty, name in entries:
        items.append(f'''
        <li class="buyeritemtable_info">
          <p class="buyeritem_name"><a href="/item/{iid}/">{name}</a></p>
          <p>注文数：{qty}個</p>
          <p>成約日：{date}</p>
        </li>''')
    return "<html><body>" + "".join(items) + "</body></html>"

# Case 1: 워터마크 없음 (신규 셀러)
page1 = [("2026/06/09", "A", 1, "A_name"), ("2026/06/09", "B", 1, "B_name")]
client = MagicMock()
client.get.return_value = MagicMock(text=make_page_html(page1))
new_orders, new_wm, warns = crawl_seller_orders(client, "S1", watermark=[], max_pages=10)
# 첫 페이지 처리 후 sales_2가 호출됨 → MagicMock은 같은 페이지 다시 반환 → 같은 페이지 반복으로 인한 무한루프 방지를 위해 다음 케이스에서 sales_2가 빈 페이지가 되도록 변경
# 여기서는 모킹이 단순해서 모든 페이지가 같음 → max_pages까지 가서 종료됨
print(f"Case 1 (no watermark, max_pages반복): new={len(new_orders)}, wm_len={len(new_wm)}, warns={len(warns)}")
# 모든 페이지 같으니 entry 20개 누적, 워터마크 미설정으로 warning 안 남
assert len(new_orders) == 20
assert warns == [], warns

# Case 2: 워터마크 일치 (수확 0건)
client2 = MagicMock()
client2.get.return_value = MagicMock(text=make_page_html(page1))
wm = [("2026/06/09", "A", 1), ("2026/06/09", "B", 1)]
new_orders, new_wm, warns = crawl_seller_orders(client2, "S1", watermark=wm, max_pages=10)
print(f"Case 2 (perfect match): new={len(new_orders)}, wm={new_wm}, warns={warns}")
assert new_orders == [], new_orders
assert new_wm == wm

# Case 3: 워터마크 부분 일치 + 신규
page3 = [
    ("2026/06/09", "NEW1", 1, "N1"),
    ("2026/06/09", "NEW2", 1, "N2"),
    ("2026/06/07", "OLD1", 1, "O1"),
    ("2026/06/07", "OLD2", 1, "O2"),
]
client3 = MagicMock()
client3.get.return_value = MagicMock(text=make_page_html(page3))
wm3 = [("2026/06/07", "OLD1", 1), ("2026/06/07", "OLD2", 1)]
new_orders, new_wm, warns = crawl_seller_orders(client3, "S1", watermark=wm3, max_pages=10)
print(f"Case 3 (2 new + match): new={len(new_orders)}, ids={[o.item_id for o in new_orders]}")
assert len(new_orders) == 2
assert new_orders[0].item_id == "NEW1"
assert new_orders[1].item_id == "NEW2"
# 새 워터마크는 첫 페이지의 전체 4 entry (30 이하)
assert len(new_wm) == 4

# Case 4: 빈 페이지 도달 (entry 0건)
calls = {"count": 0}
def side(url):
    calls["count"] += 1
    if calls["count"] == 1:
        return MagicMock(text=make_page_html(page3))
    return MagicMock(text="<html></html>")  # 빈 페이지
client4 = MagicMock()
client4.get.side_effect = side
new_orders, new_wm, warns = crawl_seller_orders(client4, "S1", watermark=[], max_pages=10)
print(f"Case 4 (empty page 2): new={len(new_orders)}, wm_len={len(new_wm)}, calls={calls['count']}")
assert calls["count"] == 2  # sales_1 + sales_2 (빈 페이지에서 break)
assert len(new_orders) == 4
assert warns == []  # watermark 없으니 max_pages 경고 안 남

# Case 5: max_pages 도달 + 워터마크 미매치
calls5 = {"count": 0}
def side5(url):
    calls5["count"] += 1
    return MagicMock(text=make_page_html(page3))
client5 = MagicMock()
client5.get.side_effect = side5
wm5 = [("2099/01/01", "Z", 1)]  # 절대 매치 안 될 워터마크
new_orders, new_wm, warns = crawl_seller_orders(client5, "S1", watermark=wm5, max_pages=3)
print(f"Case 5 (max_pages reached): new={len(new_orders)}, warns={warns}")
assert calls5["count"] == 3
assert len(new_orders) == 12  # 3페이지 × 4 entry
assert len(warns) == 1
assert "max_pages=3" in warns[0]

print("ALL crawl_seller_orders tests passed")
EOF
```

Expected: 모든 assert 통과, "ALL crawl_seller_orders tests passed" 출력.

- [ ] **Step 3: 실제 셀러로 통합 검증 (1셀러만)**

```bash
python3 << 'EOF'
from crawler.client import PlaywrightClient
from crawler.orders import crawl_seller_orders

# momoha closet
with PlaywrightClient() as client:
    new_orders, new_wm, warns = crawl_seller_orders(
        client, "1415418", watermark=[], max_pages=2
    )

print(f"Collected {len(new_orders)} new orders (first run, no watermark, max 2 pages)")
print(f"New watermark size: {len(new_wm)}")
print(f"Warnings: {warns}")
print("First 3 orders:")
for o in new_orders[:3]:
    print(f"  {o.sale_date} | {o.item_id} | qty={o.qty} | {o.item_name[:50]}")

assert len(new_orders) > 0
# 워터마크 없는 신규 셀러 → max_pages 경고 안 나와야 함
assert warns == [], warns
EOF
```

Expected: 약 60개(2페이지) entry 수집, 경고 없음, 워터마크 30개 설정.

- [ ] **Step 4: 커밋**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects"
git add "buyma market monitor/crawler/orders.py"
git commit -m "feat: add per-seller incremental orders crawl"
cd "buyma market monitor"
```

---

## Task 5: OrdersStore (JSONL append + orders_config.json)

**목표:** orders.jsonl 동시성 안전 append와 orders_config.json 즉시 flush 기능을 가진 저장소 모듈.

**Files:**
- Create: `storage/orders_store.py`

- [ ] **Step 1: 모듈 작성**

```python
# storage/orders_store.py
"""Orders storage: append-only JSONL + watermark config."""
import json
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


class OrdersStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.orders_path = self.data_dir / "orders.jsonl"
        self.config_path = self.data_dir / "orders_config.json"
        self._append_lock = threading.Lock()
        self._config_lock = threading.Lock()

    def append_orders(self, orders: list[dict]) -> None:
        """orders.jsonl에 줄 단위 JSON으로 append. 스레드 안전."""
        if not orders:
            return
        lines = []
        for o in orders:
            if is_dataclass(o):
                o = asdict(o)
            lines.append(json.dumps(o, ensure_ascii=False))
        with self._append_lock:
            with self.orders_path.open("a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")

    def load_orders_config(self) -> dict:
        """orders_config.json 로드. 없으면 빈 구조 반환."""
        if not self.config_path.exists():
            return {"watermarks": {}, "last_run_at": None, "last_run_stats": None}
        with self.config_path.open(encoding="utf-8") as f:
            data = json.load(f)
        if "watermarks" not in data:
            data["watermarks"] = {}
        return data

    def save_orders_config(self, config: dict) -> None:
        """전체 config을 디스크에 flush. 스레드 안전."""
        with self._config_lock:
            self.config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2, sort_keys=False),
                encoding="utf-8",
            )

    def update_seller_watermark(
        self,
        config: dict,
        seller_id: str,
        watermark: list,
        last_run_at: str,
        pages_scanned: int,
        orders_added: int,
    ) -> None:
        """주어진 config dict의 watermarks[seller_id]를 갱신하고 디스크에 flush.

        in-place로 config을 변경한 뒤 save_orders_config 호출.
        """
        with self._config_lock:
            config.setdefault("watermarks", {})[seller_id] = {
                "signature": [list(t) for t in watermark],
                "last_run_at": last_run_at,
                "pages_scanned_last_run": pages_scanned,
                "orders_added_last_run": orders_added,
            }
            # flush (with lock 안에서 직접 쓰기)
            self.config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2, sort_keys=False),
                encoding="utf-8",
            )
```

- [ ] **Step 2: 동작 검증**

```bash
python3 << 'EOF'
import json, tempfile
from pathlib import Path
from storage.orders_store import OrdersStore

tmp = Path(tempfile.mkdtemp())
store = OrdersStore(tmp)

# 빈 상태에서 config 로드
cfg = store.load_orders_config()
assert cfg == {"watermarks": {}, "last_run_at": None, "last_run_stats": None}, cfg
print("empty config OK")

# orders append
orders = [
    {"seller_id": "S1", "item_id": "A", "qty": 1, "sale_date": "2026/06/09",
     "item_name": "n1", "item_url": "u1", "collected_at": "2026-06-09T16:00:00+09:00"},
    {"seller_id": "S1", "item_id": "B", "qty": 2, "sale_date": "2026/06/09",
     "item_name": "n2", "item_url": "u2", "collected_at": "2026-06-09T16:00:00+09:00"},
]
store.append_orders(orders)
lines = (tmp / "orders.jsonl").read_text().strip().split("\n")
assert len(lines) == 2, lines
parsed = [json.loads(l) for l in lines]
assert parsed[0]["item_id"] == "A"
assert parsed[1]["qty"] == 2
print("append_orders OK")

# 다시 append (누적 확인)
store.append_orders([{"seller_id": "S2", "item_id": "C", "qty": 1, "sale_date": "2026/06/09",
                      "item_name": "n3", "item_url": "u3", "collected_at": "2026-06-09T16:01:00+09:00"}])
lines = (tmp / "orders.jsonl").read_text().strip().split("\n")
assert len(lines) == 3
print("multiple appends OK")

# 빈 리스트 append (no-op)
store.append_orders([])
lines = (tmp / "orders.jsonl").read_text().strip().split("\n")
assert len(lines) == 3
print("empty append no-op OK")

# update_seller_watermark
cfg2 = store.load_orders_config()
store.update_seller_watermark(
    cfg2, "S1",
    watermark=[("2026/06/09", "A", 1), ("2026/06/09", "B", 2)],
    last_run_at="2026-06-09T16:00:00+09:00",
    pages_scanned=1,
    orders_added=2,
)
cfg_disk = json.loads((tmp / "orders_config.json").read_text())
assert "S1" in cfg_disk["watermarks"]
assert cfg_disk["watermarks"]["S1"]["signature"] == [
    ["2026/06/09", "A", 1], ["2026/06/09", "B", 2]
]
assert cfg_disk["watermarks"]["S1"]["pages_scanned_last_run"] == 1
print("update_seller_watermark + flush OK")

# 동일 store에서 다시 로드 후 다른 셀러 추가
cfg3 = store.load_orders_config()
assert "S1" in cfg3["watermarks"]
store.update_seller_watermark(
    cfg3, "S2",
    watermark=[("2026/06/09", "C", 1)],
    last_run_at="2026-06-09T16:01:00+09:00",
    pages_scanned=1,
    orders_added=1,
)
cfg_disk2 = json.loads((tmp / "orders_config.json").read_text())
assert "S1" in cfg_disk2["watermarks"] and "S2" in cfg_disk2["watermarks"]
print("incremental update preserves prior sellers OK")

import shutil
shutil.rmtree(tmp)
print("ALL OrdersStore tests passed")
EOF
```

Expected: 모든 assert 통과.

- [ ] **Step 3: 커밋**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects"
git add "buyma market monitor/storage/orders_store.py"
git commit -m "feat: add OrdersStore for JSONL append and watermark config"
cd "buyma market monitor"
```

---

## Task 6: 멀티 셀러 병렬 오케스트레이션 (crawl_all_orders_with_factory)

**목표:** 401명의 셀러를 3워커 병렬로 처리. 워커당 PlaywrightClient. 셀러 완료마다 콜백으로 즉시 저장.

**Files:**
- Modify: `crawler/orders.py` (append)

- [ ] **Step 1: `crawl_seller_orders` 반환값에 `pages_scanned` 추가**

기존 함수의 반환 튜플을 3개에서 4개로 확장하여 통계 집계에 정확한 페이지 수를 제공.

`crawler/orders.py` 안의 `crawl_seller_orders` 함수 전체를 다음으로 교체:

```python
def crawl_seller_orders(
    client,
    seller_id: str,
    watermark: list[WatermarkTuple],
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[OrderEntry], list[WatermarkTuple], list[str], int]:
    """한 셀러의 sales 페이지를 순회하여 워터마크 이후 신규 주문만 수집.

    Returns:
      (new_orders, new_watermark, warnings, pages_scanned)
    """
    accumulated: list[OrderEntry] = []
    warnings: list[str] = []
    page_num_done = 0

    for page_num in range(1, max_pages + 1):
        url = build_sales_url(seller_id, page_num)
        response = client.get(url)
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
            return new_orders, new_watermark, warnings, page_num_done

    if watermark and page_num_done == max_pages:
        warnings.append(
            f"max_pages={max_pages} reached without watermark match "
            f"for seller {seller_id}: collecting all {len(accumulated)} entries (over-collection)"
        )
    new_watermark = _build_new_watermark(accumulated)
    return accumulated, new_watermark, warnings, page_num_done
```

- [ ] **Step 2: 오케스트레이션 함수 추가**

`crawler/orders.py` 파일 끝에 추가:

```python
import queue
import threading
from typing import Callable


SELLER_MAX_WORKERS = 3


def crawl_all_orders_with_factory(
    client_factory: Callable[[], object],
    seller_watermarks: dict[str, list[WatermarkTuple]],
    on_seller_done: Callable[[str, list[OrderEntry], list[WatermarkTuple], int, list[str]], None],
    on_error: Callable[..., None],
    max_pages: int = DEFAULT_MAX_PAGES,
    num_workers: int = SELLER_MAX_WORKERS,
) -> dict:
    """N 셀러를 N워커 병렬 처리. 워커당 client_factory() 호출하여 client 생성.

    각 셀러 완료 시 on_seller_done(seller_id, new_orders, new_watermark, pages_scanned, warnings) 호출.
    셀러 처리 중 예외 발생 시 on_error(stage="orders", url=..., status=..., reason=...) 호출.

    Returns: 통계 dict {"sellers_processed", "sellers_with_new_orders", "total_new_orders",
                       "max_pages_reached_warnings"}
    """
    work_queue: queue.Queue = queue.Queue()
    for sid in seller_watermarks:
        work_queue.put(sid)

    stats = {
        "sellers_processed": 0,
        "sellers_with_new_orders": 0,
        "total_new_orders": 0,
        "max_pages_reached_warnings": 0,
    }
    stats_lock = threading.Lock()

    def worker():
        client = client_factory()
        try:
            while True:
                try:
                    sid = work_queue.get_nowait()
                except queue.Empty:
                    return
                watermark = seller_watermarks.get(sid, [])
                try:
                    new_orders, new_watermark, warnings, pages_scanned = crawl_seller_orders(
                        client, sid, watermark, max_pages=max_pages,
                    )
                    on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings)
                    with stats_lock:
                        stats["sellers_processed"] += 1
                        if new_orders:
                            stats["sellers_with_new_orders"] += 1
                            stats["total_new_orders"] += len(new_orders)
                        if warnings:
                            stats["max_pages_reached_warnings"] += len(warnings)
                except Exception as e:
                    status = getattr(e, "last_status", None)
                    on_error(
                        stage="orders",
                        url=build_sales_url(sid, 1),
                        status=status,
                        reason=repr(e),
                    )
                    with stats_lock:
                        stats["sellers_processed"] += 1
                finally:
                    work_queue.task_done()
        finally:
            if hasattr(client, "close"):
                client.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return stats
```

- [ ] **Step 2: Mock으로 오케스트레이션 검증**

```bash
python3 << 'EOF'
from unittest.mock import MagicMock
from crawler.orders import crawl_all_orders_with_factory

results = {}
errors = []

def on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings):
    results[sid] = {
        "new_orders": len(new_orders),
        "new_watermark": new_watermark,
        "pages_scanned": pages_scanned,
        "warnings": warnings,
    }

def on_error(**kwargs):
    errors.append(kwargs)

def make_html_with_entries(entries):
    items = "".join(
        f'<li class="buyeritemtable_info">'
        f'<p class="buyeritem_name"><a href="/item/{iid}/">{name}</a></p>'
        f'<p>注文数：{q}個</p><p>成約日：{d}</p></li>'
        for d, iid, q, name in entries
    )
    return f"<html><body><ul>{items}</ul></body></html>"

# 셀러 2명: 둘 다 신규 워터마크 (빈 리스트)
def make_client_factory():
    def factory():
        client = MagicMock()
        # 첫 호출은 entry, 두 번째는 빈 페이지 (정상 종료)
        responses = [
            MagicMock(text=make_html_with_entries([
                ("2026/06/09", "A", 1, "An"),
                ("2026/06/09", "B", 1, "Bn"),
            ])),
            MagicMock(text="<html></html>"),
        ]
        idx = {"i": 0}
        def get(url):
            i = idx["i"]
            idx["i"] += 1
            return responses[min(i, len(responses) - 1)]
        client.get.side_effect = get
        return client
    return factory

seller_wms = {"S1": [], "S2": []}
stats = crawl_all_orders_with_factory(
    client_factory=make_client_factory(),
    seller_watermarks=seller_wms,
    on_seller_done=on_seller_done,
    on_error=on_error,
    max_pages=5,
    num_workers=2,
)
print(f"stats: {stats}")
print(f"results: {results}")
print(f"errors: {errors}")

assert stats["sellers_processed"] == 2
assert stats["sellers_with_new_orders"] == 2
assert stats["total_new_orders"] == 4
assert stats["max_pages_reached_warnings"] == 0
assert errors == []
assert set(results.keys()) == {"S1", "S2"}
for sid, r in results.items():
    assert r["new_orders"] == 2
    assert r["pages_scanned"] == 2  # sales_1 (entry) + sales_2 (빈 페이지)
    assert r["warnings"] == []

print("ALL orchestration tests passed")
EOF
```

Expected: 모든 assert 통과.

- [ ] **Step 3: 커밋**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects"
git add "buyma market monitor/crawler/orders.py"
git commit -m "feat: add multi-seller parallel orders orchestration"
cd "buyma market monitor"
```

---

## Task 7: main.py의 run_crawl_orders 구현

**목표:** Task 1에서 placeholder로 둔 `run_crawl_orders`를 채워서 sellers.json → orders 수집 → 즉시 저장 흐름 완성.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: import 추가 및 run_crawl_orders 구현**

`main.py`의 import 블록에 추가:

```python
from crawler.orders import (
    crawl_all_orders_with_factory,
    DEFAULT_MAX_PAGES,
)
from storage.orders_store import OrdersStore
from dataclasses import asdict
```

기존 `run_crawl_orders(args)` placeholder를 다음으로 교체:

```python
def run_crawl_orders(args) -> int:
    store = Store(DATA_DIR)
    orders_store = OrdersStore(DATA_DIR)

    sellers = store.load_sellers()
    if not sellers:
        logging.error("No sellers.json found. Run `crawl-sellers` first.")
        return 1

    config = orders_store.load_orders_config()

    # 워터마크 dict 구성 (튜플로 변환)
    seller_ids_all = sorted(sellers.keys())
    if args.seller_id:
        if args.seller_id not in sellers:
            logging.error("Seller %s not found in sellers.json", args.seller_id)
            return 1
        seller_ids = [args.seller_id]
    else:
        seller_ids = seller_ids_all

    watermarks: dict[str, list] = {}
    for sid in seller_ids:
        if args.full_rescan:
            watermarks[sid] = []
        else:
            wm_data = config.get("watermarks", {}).get(sid, {})
            sig = wm_data.get("signature", [])
            watermarks[sid] = [tuple(t) for t in sig]

    logging.info(
        "Starting orders crawl: %d sellers, max_pages=%d, full_rescan=%s",
        len(watermarks), args.max_pages, args.full_rescan,
    )

    timestamp = now_iso()

    def on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings):
        if new_orders:
            order_dicts = []
            for e in new_orders:
                d = asdict(e)
                d["seller_id"] = sid
                d["collected_at"] = timestamp
                order_dicts.append(d)
            if not args.dry_run:
                orders_store.append_orders(order_dicts)
        if not args.dry_run:
            orders_store.update_seller_watermark(
                config, sid, new_watermark,
                last_run_at=timestamp,
                pages_scanned=pages_scanned,
                orders_added=len(new_orders),
            )
        if warnings:
            for w in warnings:
                logging.warning(w)
        logging.info("seller %s: +%d orders (pages=%d)", sid, len(new_orders), pages_scanned)

    stats = crawl_all_orders_with_factory(
        client_factory=lambda: PlaywrightClient(),
        seller_watermarks=watermarks,
        on_seller_done=on_seller_done,
        on_error=store.append_error,
        max_pages=args.max_pages,
        num_workers=3,
    )

    errors_today = _count_errors_today(store.errors_path)
    stats["errors"] = errors_today
    config["last_run_at"] = timestamp
    config["last_run_stats"] = stats
    if not args.dry_run:
        orders_store.save_orders_config(config)

    logging.info("Stats: %s", stats)
    return 0
```

- [ ] **Step 2: 단일 셀러로 스모크 테스트 (--seller-id)**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects/buyma market monitor"
source .venv/bin/activate
# 깨끗한 상태에서 시작
rm -f data/orders.jsonl data/orders_config.json
python main.py crawl-orders --seller-id 1415418 --max-pages 2 --verbose 2>&1 | tail -20
```

Expected:
- `seller 1415418: +N orders (pages=...)` 로그 (N은 보통 30~60)
- 종료 코드 0
- `data/orders.jsonl` 파일 생성, N줄
- `data/orders_config.json` 생성, watermarks.1415418.signature 길이 ≤ 30

확인:
```bash
wc -l data/orders.jsonl
python3 -c "
import json
cfg = json.load(open('data/orders_config.json'))
wm = cfg['watermarks']['1415418']
print('signature length:', len(wm['signature']))
print('orders_added_last_run:', wm['orders_added_last_run'])
print('pages_scanned_last_run:', wm['pages_scanned_last_run'])
print('first 3 signatures:', wm['signature'][:3])
"
```

- [ ] **Step 3: 2회차 실행 (증분 0건이어야 함)**

```bash
python main.py crawl-orders --seller-id 1415418 --max-pages 2 --verbose 2>&1 | tail -10
```

Expected: `seller 1415418: +0 orders (pages=1)` — 워터마크 매칭으로 sales_1만 페치하고 종료.

확인:
```bash
wc -l data/orders.jsonl  # 1회차와 동일해야 함 (증분 0)
```

- [ ] **Step 4: dry-run 확인**

```bash
python main.py crawl-orders --seller-id 1415418 --max-pages 2 --dry-run --verbose 2>&1 | tail -10
md5sum data/orders.jsonl data/orders_config.json
```

Expected: dry-run 전후 md5sum 동일.

- [ ] **Step 5: 커밋**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects"
git add "buyma market monitor/main.py"
git commit -m "feat: implement crawl-orders subcommand"
cd "buyma market monitor"
```

---

## Task 8: 전체 셀러 풀 실행 검증

**목표:** 401명 전체 셀러에 대해 풀 실행하여 정상 동작 + 통계 합리성 확인.

**Files:** (없음 — 검증만)

- [ ] **Step 1: 깨끗한 상태에서 1회차 실행**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects/buyma market monitor"
source .venv/bin/activate
rm -f data/orders.jsonl data/orders_config.json
time python main.py crawl-orders --max-pages 5 2>&1 | tee /tmp/orders_run1.log | tail -30
```

Expected:
- 401명 처리, 정상 종료
- 예상 시간: ~10-20분 (셀러당 sales_1 1페이지 평균, Playwright 3워커)
- `Stats: {'sellers_processed': 401, ...}` 로그
- 종료 코드 0

- [ ] **Step 2: 결과 검증**

```bash
python3 << 'EOF'
import json
from collections import Counter

# orders.jsonl 검증
orders = [json.loads(l) for l in open('data/orders.jsonl')]
print(f"Total orders: {len(orders)}")
assert len(orders) > 0

# 필수 필드 확인
required = {'seller_id', 'item_id', 'item_name', 'item_url', 'qty', 'sale_date', 'collected_at'}
sample = orders[0]
missing = required - set(sample.keys())
assert not missing, f"missing fields: {missing}"
print(f"Required fields OK. Sample: {sample}")

# 셀러별 주문 분포
by_seller = Counter(o['seller_id'] for o in orders)
print(f"Sellers with orders: {len(by_seller)}/401")
print(f"Top 5 sellers by order count: {by_seller.most_common(5)}")

# orders_config.json 검증
cfg = json.load(open('data/orders_config.json'))
print(f"Watermarks saved for {len(cfg['watermarks'])} sellers")
assert len(cfg['watermarks']) == 401
print(f"last_run_stats: {cfg['last_run_stats']}")
EOF
```

Expected:
- Total orders > 0 (몇천 건 예상)
- Sellers with orders ≤ 401 (activity 없는 셀러도 있음)
- Watermarks 401개 모두 저장됨

- [ ] **Step 3: 2회차 실행 (증분 매우 적어야 함)**

```bash
time python main.py crawl-orders --max-pages 5 2>&1 | tee /tmp/orders_run2.log | tail -10
```

Expected:
- 대부분 셀러 0건 증분 (`seller X: +0 orders (pages=1)`)
- 일부 셀러만 1-2건 증분 (1회차와 2회차 사이 발생한 새 주문)
- 예상 시간: ~5-10분 (페이지당 sales_1만 페치하고 끝나는 경우 많음)

확인:
```bash
# 2회차 후 총 주문 수
wc -l data/orders.jsonl
# 1회차와 비교: 추가분만 늘어야 함
grep -oE '"total_new_orders":\s*\d+' /tmp/orders_run2.log
```

- [ ] **Step 4: 에러/경고 확인**

```bash
if [ -f data/errors.log ]; then
  echo "errors today:"
  grep "$(date +%Y-%m-%d)" data/errors.log | wc -l
  echo "sample:"
  grep "$(date +%Y-%m-%d)" data/errors.log | head -3
fi

echo "max_pages reached warnings:"
grep "max_pages=" /tmp/orders_run1.log | wc -l
```

Expected:
- 에러 0건 또는 매우 적음
- max_pages reached 경고 0건 또는 매우 적음 (대부분 셀러는 5페이지 이내에 매칭)

- [ ] **Step 5: 데이터 무결성 sanity check**

```bash
python3 << 'EOF'
import json

orders = [json.loads(l) for l in open('data/orders.jsonl')]

# 모든 sale_date가 YYYY/MM/DD 형식
import re
date_re = re.compile(r'^\d{4}/\d{2}/\d{2}$')
bad_dates = [o for o in orders if not date_re.match(o['sale_date'])]
assert not bad_dates, f"bad dates: {bad_dates[:3]}"

# 모든 qty가 양수
bad_qty = [o for o in orders if not (isinstance(o['qty'], int) and o['qty'] >= 1)]
assert not bad_qty, f"bad qty: {bad_qty[:3]}"

# 모든 item_url이 https://www.buyma.com/item/<id>/ 패턴
url_re = re.compile(r'^https://www\.buyma\.com/item/\d+/$')
bad_url = [o for o in orders if not url_re.match(o['item_url'])]
assert not bad_url, f"bad url: {bad_url[:3]}"

# 모든 seller_id가 sellers.json에 존재
sellers = json.load(open('data/sellers.json'))
unknown = [o for o in orders if o['seller_id'] not in sellers]
assert not unknown, f"unknown sellers: {[o['seller_id'] for o in unknown[:3]]}"

print("All data integrity checks passed")
print(f"Total orders: {len(orders)}")
print(f"Date range: {min(o['sale_date'] for o in orders)} ~ {max(o['sale_date'] for o in orders)}")
EOF
```

Expected: 모든 검증 통과.

---

## Definition of Done

- [ ] `python main.py crawl-sellers` 가 기존과 동일하게 작동 (회귀 없음)
- [ ] `python main.py crawl-orders --seller-id <id>` 가 정상 동작
- [ ] `python main.py crawl-orders` 전체 실행이 정상 종료, 401 셀러 처리 완료
- [ ] `data/orders.jsonl` 에 jsonl 포맷으로 누적 저장됨
- [ ] `data/orders_config.json` 에 셀러별 워터마크 저장됨
- [ ] 2회차 실행 시 증분 수집 (대부분 0건 또는 소량)
- [ ] 에러는 `data/errors.log` 에 기록되고 전체 프로세스 계속됨
- [ ] `--dry-run` 으로 저장 없이 동작 확인 가능
