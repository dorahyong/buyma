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
    assert max_p == 1642


def test_parse_seller_items_max_page_single_page():
    # A page with no pagination links should return 1
    html = "<html><body><ul><li class='buyeritemtable_info'>" \
           "<p class='buyeritem_name'><a href='/item/1/'>x</a></p>" \
           "<p class='buyeritem_price'>¥100</p></li></ul></body></html>"
    assert parse_seller_items_max_page(html) == 1
