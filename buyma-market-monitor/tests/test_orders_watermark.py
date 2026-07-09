"""Watermark over-collection guard behavior in crawl_seller_orders."""
import crawler.orders as om
from crawler.orders import crawl_seller_orders, OrderEntry


def _e(sale_date, item_id, qty=1):
    return OrderEntry(
        sale_date=sale_date, item_id=item_id, qty=qty,
        item_name=f"item {item_id}", item_url="",
    )


class FakeClient:
    """Returns a trivial response per URL; parse_sales_page is monkeypatched."""
    def get(self, url):
        class R:
            text = url
            status_code = 200
        return R()


def _patch_pages(monkeypatch, pages):
    """Make parse_sales_page yield each page's entries in order."""
    seq = iter(pages)
    monkeypatch.setattr(om, "parse_sales_page", lambda html: next(seq))


def test_pattern_match_returns_increment_not_overcollected(monkeypatch):
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1")],   # genuinely new
        [_e("2026/06/13", "2")],   # watermark boundary
    ])
    watermark = [("2026/06/13", "2", 1)]
    new_orders, _wm, warnings, _pages, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S3", watermark=watermark, max_pages=100,
    )
    assert [e.item_id for e in new_orders] == ["1"]
    assert overcollected is False
    assert warnings == []


def test_pattern_block_spanning_below_prev_max_still_matches(monkeypatch):
    # The watermark block spans below prev_max (down to wm_min). It completes only
    # on page 2. A prev_max-based early stop would break after page 1 (06/12 < 06/13)
    # and wrongly fall back; the correct wm_min stop must let page 2 complete the
    # block so the pattern matches -> clean increment, NOT over-collection.
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1"), _e("2026/06/13", "A"), _e("2026/06/12", "B")],
        [_e("2026/06/11", "C")],  # completes the 3-tuple watermark block
    ])
    watermark = [("2026/06/13", "A", 1), ("2026/06/12", "B", 1), ("2026/06/11", "C", 1)]
    # prev_max=06/13, wm_min=06/11
    new_orders, _wm, warnings, _pages, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S5", watermark=watermark, max_pages=100,
    )
    assert overcollected is False
    assert [e.item_id for e in new_orders] == ["1"]
    assert warnings == []


def test_fallback_bounds_output_to_prev_max(monkeypatch):
    # Watermark never matches. prev_max=2026/06/12, wm_min=2026/06/10.
    # 06/11 is between wm_min and prev_max: kept in scan but EXCLUDED from output
    # (below ceiling). Page 3's 06/09 (< wm_min) triggers the early stop.
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1"), _e("2026/06/13", "2")],
        [_e("2026/06/12", "3"), _e("2026/06/11", "4")],
        [_e("2026/06/09", "5")],  # < wm_min 06/10 -> early stop here
    ])
    watermark = [("2026/06/12", "99", 1), ("2026/06/10", "98", 1)]  # prev_max=06/12, wm_min=06/10
    new_orders, _wm, warnings, _pages, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S1", watermark=watermark, max_pages=100,
    )
    assert overcollected is True
    # 06/14, 06/13, 06/12 kept (>= prev_max); 06/11 and 06/09 dropped
    assert sorted(e.sale_date for e in new_orders) == ["2026/06/12", "2026/06/13", "2026/06/14"]
    assert any("watermark not matched" in w for w in warnings)


def test_fallback_early_stops_at_wm_min(monkeypatch):
    # After page 2 the oldest accumulated date (06/09) < wm_min (06/10): must stop,
    # never fetching page 3. If it fetched page 3, next(seq) would raise StopIteration.
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1")],
        [_e("2026/06/09", "2")],   # 06/09 < wm_min 06/10 -> early stop here
        # no page 3 provided on purpose
    ])
    watermark = [("2026/06/12", "99", 1), ("2026/06/10", "98", 1)]  # prev_max=06/12, wm_min=06/10
    new_orders, _wm, warnings, pages_scanned, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S2", watermark=watermark, max_pages=100,
    )
    assert pages_scanned == 2
    assert overcollected is True
    assert [e.sale_date for e in new_orders] == ["2026/06/14"]  # only >= prev_max(06/12)


def test_empty_watermark_full_scan_not_overcollected(monkeypatch):
    _patch_pages(monkeypatch, [
        [_e("2026/06/14", "1")],
        [],
    ])
    new_orders, _wm, warnings, _pages, overcollected = crawl_seller_orders(
        FakeClient(), seller_id="S4", watermark=[], max_pages=100,
    )
    assert len(new_orders) == 1
    assert overcollected is False
    assert warnings == []
