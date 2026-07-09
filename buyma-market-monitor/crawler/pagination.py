import re
from bs4 import BeautifulSoup


_PAGE_HREF_PATTERN = re.compile(r"-A2002003000_(\d+)/")


def parse_max_pages(html: str) -> int:
    """목록 페이지 1의 HTML에서 마지막 페이지 번호를 추출한다.

    페이지네이션 컨트롤의 모든 `_{N}/` 링크를 찾아 그중 최댓값을 반환.
    페이지네이션이 없으면 1을 반환한다.
    """
    soup = BeautifulSoup(html, "lxml")
    max_n = 1
    for a in soup.find_all("a", href=True):
        m = _PAGE_HREF_PATTERN.search(a["href"])
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return max_n
