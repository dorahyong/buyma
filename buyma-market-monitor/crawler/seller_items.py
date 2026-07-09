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
