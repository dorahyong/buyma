"""HTTP and Playwright clients for fetching BUYMA pages.

HttpClient: for static pages (listing). Fast, simple, retry+sleep+UA.
PlaywrightClient: for seller pages. JS-renders, waits for #js_fan_count.
Both expose .get(url) -> FetchResult and .close().
"""
import time
from dataclasses import dataclass

import httpx


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "ja,en-US;q=0.9,ko;q=0.8"

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": ACCEPT_LANGUAGE,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_BACKOFFS = [0.5, 1.0, 2.0]
SLEEP_SECONDS = 0.2
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


@dataclass
class FetchResult:
    text: str
    status_code: int


class MaxRetriesExceeded(Exception):
    def __init__(self, url: str, last_status: int | None, last_error: str | None):
        self.url = url
        self.last_status = last_status
        self.last_error = last_error
        super().__init__(
            f"max retries exceeded for {url} "
            f"(last_status={last_status}, last_error={last_error})"
        )


class BlockedByServer(Exception):
    """Raised on HTTP 403 — distinguishes blocking from generic retry exhaustion."""

    def __init__(self, url: str, last_status: int = 403):
        self.url = url
        self.last_status = last_status
        super().__init__(f"blocked by server (status {last_status}): {url}")


class HttpClient:
    """For listing pages — static HTML, retry+sleep+UA."""

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        sleep_seconds: float = SLEEP_SECONDS,
        circuit_breaker=None,
        timeout: httpx.Timeout | None = None,
        max_retries: int | None = None,
    ):
        self._client = httpx.Client(
            http2=True,
            headers=DEFAULT_HEADERS,
            timeout=timeout if timeout is not None else TIMEOUT,
            transport=transport,
            follow_redirects=True,
        )
        self._sleep_seconds = sleep_seconds
        self._cb = circuit_breaker
        self._max_retries = max_retries if max_retries is not None else len(RETRY_BACKOFFS)

    def get(self, url: str) -> FetchResult:
        if self._cb is not None:
            self._cb.assert_not_open()

        last_status: int | None = None
        last_error: str | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.get(url)
            except httpx.RequestError as e:
                last_error = repr(e)
                last_status = None
            else:
                last_status = response.status_code
                if response.status_code == 403:
                    if self._cb is not None:
                        self._cb.record_block()
                    self._sleep()
                    raise BlockedByServer(url, last_status=403)
                if response.status_code == 429 and self._cb is not None:
                    self._cb.record_block()
                if response.status_code not in RETRY_STATUSES:
                    self._sleep()
                    if response.status_code >= 400:
                        raise MaxRetriesExceeded(url, last_status, None)
                    return FetchResult(text=response.text, status_code=response.status_code)

            if attempt < len(RETRY_BACKOFFS):
                time.sleep(RETRY_BACKOFFS[attempt])

        self._sleep()
        raise MaxRetriesExceeded(url, last_status, last_error)

    def get_allowing_4xx(self, url: str) -> FetchResult:
        """Like get(), but returns the response for 404/410 instead of raising.

        Used by status classification: a 404 is meaningful data, not an error.
        Other 4xx (e.g. 403) still raise — 403 raises BlockedByServer specifically.
        5xx still retry/raise as before.
        """
        if self._cb is not None:
            self._cb.assert_not_open()

        last_status: int | None = None
        last_error: str | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.get(url)
            except httpx.RequestError as e:
                last_error = repr(e)
                last_status = None
            else:
                last_status = response.status_code
                if response.status_code == 403:
                    if self._cb is not None:
                        self._cb.record_block()
                    self._sleep()
                    raise BlockedByServer(url, last_status=403)
                if response.status_code == 429 and self._cb is not None:
                    self._cb.record_block()
                if response.status_code in (404, 410):
                    self._sleep()
                    return FetchResult(text=response.text, status_code=response.status_code)
                if response.status_code not in RETRY_STATUSES:
                    self._sleep()
                    if response.status_code >= 400:
                        raise MaxRetriesExceeded(url, last_status, None)
                    return FetchResult(text=response.text, status_code=response.status_code)

            if attempt < len(RETRY_BACKOFFS):
                time.sleep(RETRY_BACKOFFS[attempt])

        self._sleep()
        raise MaxRetriesExceeded(url, last_status, last_error)

    def _sleep(self) -> None:
        if self._sleep_seconds > 0:
            time.sleep(self._sleep_seconds)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class PlaywrightClient:
    """For seller pages — JS-rendered HTML with follower count.

    Launches a headless Chromium browser and reuses a single context+page across all
    fetches. Waits for #js_fan_count to populate before returning HTML. Retries on
    page errors.
    """

    def __init__(self, sleep_seconds: float = SLEEP_SECONDS, circuit_breaker=None):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="ja-JP",
            extra_http_headers={"Accept-Language": ACCEPT_LANGUAGE},
        )
        self._page = self._context.new_page()
        self._sleep_seconds = sleep_seconds
        self._cb = circuit_breaker

    def get(self, url: str) -> FetchResult:
        if self._cb is not None:
            self._cb.assert_not_open()
        last_error: str | None = None
        for attempt in range(len(RETRY_BACKOFFS) + 1):
            try:
                response = self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                status = response.status if response else 0
                if status == 403:
                    if self._cb is not None:
                        self._cb.record_block()
                    self._sleep()
                    raise BlockedByServer(url, last_status=403)
                if status == 429 and self._cb is not None:
                    self._cb.record_block()
                if status and status >= 400 and status not in RETRY_STATUSES:
                    self._sleep()
                    raise MaxRetriesExceeded(url, status, None)
                if status and status in RETRY_STATUSES:
                    last_error = f"status {status}"
                else:
                    try:
                        self._page.wait_for_function(
                            "document.querySelector('#js_fan_count')?.textContent?.trim() !== ''",
                            timeout=5000,
                        )
                    except Exception:
                        pass
                    html = self._page.content()
                    self._sleep()
                    return FetchResult(text=html, status_code=status or 200)
            except MaxRetriesExceeded:
                raise
            except BlockedByServer:
                raise
            except Exception as e:
                last_error = repr(e)

            if attempt < len(RETRY_BACKOFFS):
                time.sleep(RETRY_BACKOFFS[attempt])

        self._sleep()
        raise MaxRetriesExceeded(url, None, last_error)

    def _sleep(self) -> None:
        if self._sleep_seconds > 0:
            time.sleep(self._sleep_seconds)

    def close(self) -> None:
        try:
            self._context.close()
        finally:
            try:
                self._browser.close()
            finally:
                self._pw.stop()

    def __enter__(self) -> "PlaywrightClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
