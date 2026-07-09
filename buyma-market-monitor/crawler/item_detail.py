"""Parse a product detail page (/item/{id}/) into a full metadata dict.

Heavy lifting via JSON-LD (ProductGroup + BreadcrumbList) and OpenGraph/Twitter
meta tags.
"""
import json
import re
from typing import Any

from bs4 import BeautifulSoup


ITEM_DETAIL_URL_TEMPLATE = "https://www.buyma.com/item/{item_id}/"

_DIGITS = re.compile(r"[\d,]+")
_THEME_UI_LABELS = {"もっと見る", "閉じる"}


def build_item_detail_url(item_id: str) -> str:
    return ITEM_DETAIL_URL_TEMPLATE.format(item_id=item_id)


def parse_item_detail(html: str) -> dict[str, Any]:
    """Return a flat dict of product detail fields.

    Keys returned: name, brand, category_path, origin_country, image_url,
    description, image_urls, view_count, fav_count, inquiry_count,
    brand_model_number, themes, variants, size_guide_text, size_chart.
    Missing fields are None (or "" for strings), never raise.

    NOTE on ``name``: this key is returned for completeness and forensics.
    The listing-page name was already stored via ``upsert_scanned_item``, so
    ``name`` is intentionally NOT part of the ``update_detail_fields`` contract
    in ``storage/items_repo.py``.  Callers must pick keys explicitly — never
    splat (**parse_item_detail(html)) into update_detail_fields or you will get
    a TypeError.
    """
    soup = BeautifulSoup(html, "lxml")

    og = _extract_meta(soup, key="property", prefix="og:")
    twitter = _extract_meta(soup, key="name", prefix="twitter:")
    json_ld = _extract_json_ld(soup)

    product_group = next(
        (b for b in json_ld if b.get("@type") == "ProductGroup"), None
    )
    breadcrumbs = [b for b in json_ld if b.get("@type") == "BreadcrumbList"]

    return {
        "name": og.get("title", "") or _h1_text(soup),
        "brand": _extract_brand(product_group),
        "category_path": _extract_category_path(breadcrumbs),
        "origin_country": _extract_origin_country(twitter),
        "image_url": og.get("image"),
        "image_urls": _extract_image_urls(product_group),
        "description": _extract_pg_description(product_group) or og.get("description", ""),
        "view_count": _extract_int_from_selector(soup, "span.ac_count"),
        "fav_count": _extract_int_from_selector(soup, "span.fav_count"),
        "inquiry_count": _extract_int_from_selector(soup, "#tabmenu_inqcnt"),
        "brand_model_number": _extract_brand_model_number(soup),
        "themes": _extract_themes(soup),
        "variants": _extract_variants(product_group),
        "size_guide_text": _extract_size_guide_text(soup),
        "size_chart": _extract_size_chart(soup),
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
    return product_group.get("description", "")


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


def _extract_brand_model_number(soup) -> str | None:
    for dt in soup.find_all("dt"):
        if "品番" in dt.get_text():
            dd = dt.find_next_sibling("dd")
            if dd is None:
                return None
            text = dd.get_text(" ", strip=True)
            return text or None
    return None


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


_STOCK_MARKS = {"○", "×", "△", "✓", "-", "◯", "✕", ""}


def _is_stock_matrix(rows: list) -> bool:
    """Return True if data cells (rows after header, columns after first) are
    dominated by availability marks rather than numeric measurements.

    A table is treated as a stock matrix when NONE of its data cells contain
    a digit — meaning there are no real measurement values present.
    """
    for row in rows[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        for cell in cells[1:]:  # skip first column (size name)
            if any(ch.isdigit() for ch in cell):
                return False  # found a digit → must be a measurement value
    return True


def _extract_size_chart(soup) -> dict | None:
    """Parse the measurement table (data cells contain numeric measurements) into
    {size_name: {measure_name: value}}. Stock-matrix tables (○/× availability
    cells) are ignored even when size names themselves contain 'cm' (e.g. shoes:
    '23(23cm)', '24(24cm)' → data cells are ○/×, not measurements)."""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if len(header) < 2:
            continue
        if _is_stock_matrix(rows):
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
    h3 = soup.find(lambda tag: tag.name == "h3" and "色・サイズ" in tag.get_text())
    if h3 is None:
        return None
    parts: list[str] = []
    collected = []
    for sib in h3.find_all_next():
        if sib.name in ("h3", "h2"):
            break
        # skip elements nested inside one we already captured (avoids dup text)
        if any(sib in c.descendants for c in collected):
            continue
        if sib.name in ("div", "p", "dd", "td", "span"):
            t = sib.get_text(" ", strip=True)
            if t:
                parts.append(t)
                collected.append(sib)
    text = " ".join(parts).strip()
    return text or None


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


def _extract_int_from_selector(soup, selector: str) -> int | None:
    el = soup.select_one(selector)
    if el is None:
        return None
    m = _DIGITS.search(el.get_text())
    if m is None:
        return None
    return int(m.group(0).replace(",", ""))


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
    """Scan twitter:label{N} for '購入地' and return the matching twitter:data{N}."""
    for i in range(1, 5):
        if twitter.get(f"label{i}") == "購入地":
            v = twitter.get(f"data{i}", "").strip()
            return v or None
    return None
