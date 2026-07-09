import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from bs4 import BeautifulSoup


LISTING_URL_TEMPLATE = "https://www.buyma.com/r/-A2002003000_{n}/"
LISTING_MAX_WORKERS = 3
_BUYER_HREF_PATTERN = re.compile(r"^/buyer/(\d+)\.html")


def build_listing_url(page: int) -> str:
    return LISTING_URL_TEMPLATE.format(n=page)


def parse_seller_ids(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    ids: set[str] = set()
    for a in soup.select('a[href^="/buyer/"]'):
        href = a.get("href", "")
        m = _BUYER_HREF_PATTERN.match(href)
        if m:
            ids.add(m.group(1))
    return ids


def crawl_listing_pages(
    client,
    max_pages: int,
    on_error: Callable[..., None],
) -> set[str]:
    """Fetch all listing pages in parallel and aggregate seller IDs."""
    all_ids: set[str] = set()
    urls = [build_listing_url(n) for n in range(1, max_pages + 1)]

    with ThreadPoolExecutor(max_workers=LISTING_MAX_WORKERS) as executor:
        future_to_url = {executor.submit(client.get, url): url for url in urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                response = future.result()
                ids = parse_seller_ids(response.text)
                all_ids.update(ids)
            except Exception as e:
                status = getattr(e, "last_status", None)
                on_error(
                    stage="listing",
                    url=url,
                    status=status,
                    reason=repr(e),
                )
    return all_ids
