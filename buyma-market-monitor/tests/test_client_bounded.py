import httpx
from crawler.client import HttpClient


def test_max_retries_override_limits_attempts(monkeypatch):
    calls = {"n": 0}
    client = HttpClient(sleep_seconds=0.0, max_retries=1)
    def fake_get(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(503, request=httpx.Request("GET", url))
    monkeypatch.setattr(client._client, "get", fake_get)
    try:
        client.get("https://example.com/x")
    except Exception:
        pass
    assert calls["n"] == 2   # 1 initial + 1 retry
    client.close()
