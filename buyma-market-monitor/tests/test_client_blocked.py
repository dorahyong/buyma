import httpx
import pytest

from crawler.circuit_breaker import CircuitBreaker, CircuitOpenError
from crawler.client import HttpClient, BlockedByServer


def _transport(responses: list[int]):
    """Mock transport: returns canned status codes in order, then loops on the last."""
    state = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return httpx.Response(responses[i], text=f"status {responses[i]}")

    return httpx.MockTransport(handler)


def test_get_raises_blocked_by_server_on_403():
    client = HttpClient(transport=_transport([403]), sleep_seconds=0)
    try:
        with pytest.raises(BlockedByServer):
            client.get("https://example.test/")
    finally:
        client.close()


def test_get_records_block_in_circuit_breaker_on_403():
    cb = CircuitBreaker(threshold=10, window_seconds=60)
    client = HttpClient(transport=_transport([403]), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(BlockedByServer):
            client.get("https://example.test/")
        assert len(cb._events) == 1
    finally:
        client.close()


def test_get_records_block_in_circuit_breaker_on_429():
    cb = CircuitBreaker(threshold=10, window_seconds=60)
    # 429 will retry; provide enough responses for all attempts plus a final non-retry
    client = HttpClient(transport=_transport([429, 429, 429, 429]), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(Exception):  # MaxRetriesExceeded after retries
            client.get("https://example.test/")
        # Each attempt that saw 429 records a block
        assert len(cb._events) >= 1
    finally:
        client.close()


def test_get_short_circuits_when_breaker_open():
    """If CB is already open, get() must raise CircuitOpenError without hitting transport."""
    cb = CircuitBreaker(threshold=1, window_seconds=60)
    cb.record_block()
    assert cb.is_open()

    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="ok")

    client = HttpClient(transport=httpx.MockTransport(handler), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(CircuitOpenError):
            client.get("https://example.test/")
        assert calls["n"] == 0  # transport never invoked
    finally:
        client.close()


def test_get_passes_normally_with_closed_breaker():
    cb = CircuitBreaker(threshold=5, window_seconds=60)
    client = HttpClient(transport=_transport([200]), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        r = client.get("https://example.test/")
        assert r.status_code == 200
    finally:
        client.close()


def test_get_works_without_circuit_breaker():
    """Backward compat: CB is optional."""
    client = HttpClient(transport=_transport([200]), sleep_seconds=0)
    try:
        r = client.get("https://example.test/")
        assert r.status_code == 200
    finally:
        client.close()


def test_blocked_by_server_carries_last_status_403():
    """For on_error compatibility — callbacks read getattr(e, 'last_status', None)."""
    client = HttpClient(transport=_transport([403]), sleep_seconds=0)
    try:
        client.get("https://example.test/")
    except BlockedByServer as e:
        assert e.last_status == 403
        assert "blocked" in str(e).lower() or "403" in str(e)
    finally:
        client.close()


def test_get_allowing_4xx_raises_blocked_on_403():
    client = HttpClient(transport=_transport([403]), sleep_seconds=0)
    try:
        with pytest.raises(BlockedByServer):
            client.get_allowing_4xx("https://example.test/")
    finally:
        client.close()


def test_get_allowing_4xx_still_returns_404():
    """404 must still pass through as a normal FetchResult, not raise."""
    client = HttpClient(transport=_transport([404]), sleep_seconds=0)
    try:
        r = client.get_allowing_4xx("https://example.test/")
        assert r.status_code == 404
    finally:
        client.close()


def test_get_allowing_4xx_short_circuits_when_breaker_open():
    cb = CircuitBreaker(threshold=1, window_seconds=60)
    cb.record_block()

    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="x")

    client = HttpClient(transport=httpx.MockTransport(handler), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(CircuitOpenError):
            client.get_allowing_4xx("https://example.test/")
        assert calls["n"] == 0
    finally:
        client.close()


def test_get_allowing_4xx_records_block_on_403():
    cb = CircuitBreaker(threshold=10, window_seconds=60)
    client = HttpClient(transport=_transport([403]), sleep_seconds=0,
                       circuit_breaker=cb)
    try:
        with pytest.raises(BlockedByServer):
            client.get_allowing_4xx("https://example.test/")
        assert len(cb._events) == 1
    finally:
        client.close()
