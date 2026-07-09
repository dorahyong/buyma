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
