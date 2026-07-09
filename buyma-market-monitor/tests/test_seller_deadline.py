import time
from crawler.seller import crawl_sellers_with_factory
from crawler.orders import crawl_all_orders_with_factory


class _SlowClient:
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
    result = crawl_sellers_with_factory(
        client_factory=lambda: _SlowClient(seen),
        seller_ids=[f"s{i}" for i in range(50)],
        on_error=lambda **kw: None,
        num_workers=2,
        deadline=time.monotonic() - 1.0,
    )
    assert result == []
    assert seen == []


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
    assert done == []
