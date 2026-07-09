# BUYMA Seller Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BUYMA 한국 카테고리 목록을 순회하여 한국 personal shopper / shop 셀러를 발견·수집하고 JSON으로 누적 저장하는 CLI 크롤러를 구축한다.

**Architecture:** 단일 CLI 엔트리포인트(`main.py`)가 5단계 파이프라인(config 로드 → max_pages 감지 → 목록 순회 → 셀러 페이지 순회 → 저장)을 실행한다. HTTP는 `httpx` 동기 클라이언트 + `ThreadPoolExecutor(3)`로 병렬화, 각 요청 후 0.2초 sleep. 파싱 로직은 순수 함수로 모듈화하여 fixture 기반 테스트 가능.

**Tech Stack:** Python 3.11+, httpx (목록 페이지), Playwright Chromium (셀러 페이지 — follower JS 로드), BeautifulSoup4, lxml, pytest

**⚠️ 설계 업데이트 (Task 2 완료 후 발견):**
- 셀러 페이지의 `#js_fan_count`는 정적 HTML에서 비어있고, JS XHR로 로드됨
- → 셀러 페이지는 Playwright로 렌더링 후 HTML 추출 필요
- → 목록 페이지는 정적이므로 httpx 유지 (오버헤드 회피)
- → Task 2의 셀러 fixture 4개는 Playwright로 재캡처 필요
- → Task 6은 hybrid 구조 (HttpClient + PlaywrightClient)
- → SHOP 페이지도 PERSONAL SHOPPER와 동일 DOM (분기 불필요)
- → 팔로워 셀렉터 변경: `span.fan_cnt` → `#js_fan_count`
- → 주문실적 셀렉터 보강: `p.buyer_eva_text` 3개 중 `<h3>注文実績</h3>` 다음 것 선택

**Spec Reference:** [docs/superpowers/specs/2026-06-02-buyma-seller-crawler-design.md](../specs/2026-06-02-buyma-seller-crawler-design.md)

---

## File Structure

```
buyma market monitor/
├── main.py                          # CLI 엔트리포인트
├── requirements.txt                 # 의존성
├── .gitignore                       # data/ 제외
├── crawler/
│   ├── __init__.py
│   ├── client.py                    # BuyMaClient: HTTP + 재시도 + sleep
│   ├── pagination.py                # max_pages 자동 감지
│   ├── listing.py                   # 목록 페이지 → seller_id 추출
│   └── seller.py                    # 셀러 페이지 → 필터링 + 데이터 추출
├── storage/
│   ├── __init__.py
│   └── store.py                     # config.json / sellers.json / errors.log
├── data/                            # 자동 생성, gitignore
└── tests/
    ├── __init__.py
    ├── fixtures/
    │   ├── listing_page.html        # 목록 페이지 샘플
    │   ├── seller_premium_kr.html   # PREMIUM PERSONAL SHOPPER 한국
    │   ├── seller_personal_kr.html  # PERSONAL SHOPPER 한국
    │   ├── seller_personal_jp.html  # PERSONAL SHOPPER 일본 (스킵 대상)
    │   └── seller_shop.html         # SHOP (국가 없음)
    ├── test_listing.py
    ├── test_seller.py
    ├── test_pagination.py
    └── test_store.py
```

각 파일 책임:
- `client.py`: 외부 HTTP 호출의 유일한 경계. UA/timeout/재시도/sleep 모두 캡슐화
- `pagination.py`, `listing.py`, `seller.py`: 순수 파싱 함수 (HTML 문자열 → 데이터)
- `store.py`: 파일 시스템 I/O의 유일한 경계
- `main.py`: 위 모듈들의 조립만 담당, 비즈니스 로직 없음

---

## Task 1: 프로젝트 초기 설정

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `crawler/__init__.py`
- Create: `storage/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: requirements.txt 작성**

```
httpx[http2]==0.27.2
beautifulsoup4==4.12.3
lxml==5.3.0
playwright==1.48.0
pytest==8.3.3
```

**참고:** Task 1은 이미 실행되어 commit됨. Playwright 추가는 별도 Task에서 처리 (Task 2 재실행 시 동시 처리).

- [ ] **Step 2: .gitignore 작성**

```
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
venv/
data/
*.egg-info/
.DS_Store
```

- [ ] **Step 3: 빈 패키지 init 파일 생성**

```python
# crawler/__init__.py
```

```python
# storage/__init__.py
```

```python
# tests/__init__.py
```

- [ ] **Step 4: 가상환경 생성 및 의존성 설치**

```bash
cd "/Users/tedlim/Desktop/Claude Code Projects/buyma market monitor"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Expected: 모든 패키지가 에러 없이 설치됨

- [ ] **Step 5: 첫 커밋**

```bash
git add requirements.txt .gitignore crawler/__init__.py storage/__init__.py tests/__init__.py
git commit -m "chore: initialize buyma seller crawler project"
```

---

## Task 2: HTML 픽스처 수집

**Files:**
- Create: `tests/fixtures/listing_page.html`
- Create: `tests/fixtures/seller_premium_kr.html`
- Create: `tests/fixtures/seller_personal_kr.html`
- Create: `tests/fixtures/seller_personal_jp.html`
- Create: `tests/fixtures/seller_shop.html`

이후 작업의 단위 테스트는 모두 이 픽스처를 사용한다. 실제 페이지 응답을 캡처해야 한다.

- [ ] **Step 1: 픽스처 디렉토리 생성**

```bash
mkdir -p tests/fixtures
```

- [ ] **Step 2: 목록 페이지 픽스처 캡처**

```bash
curl -s -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
     -H "Accept-Language: ja,en-US;q=0.9,ko;q=0.8" \
     "https://www.buyma.com/r/-A2002003000_1/" > tests/fixtures/listing_page.html
```

확인: 파일이 수십~수백 KB 크기이고, `grep -c '/buyer/' tests/fixtures/listing_page.html` 결과가 10 이상.

- [ ] **Step 3: PREMIUM PERSONAL SHOPPER 한국 셀러 픽스처 캡처**

```bash
curl -s -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
     -H "Accept-Language: ja,en-US;q=0.9,ko;q=0.8" \
     "https://www.buyma.com/buyer/1415418/sales_1.html" > tests/fixtures/seller_premium_kr.html
```

확인: `grep -c 'label_premium' tests/fixtures/seller_premium_kr.html` 결과가 1 이상.

- [ ] **Step 4: PERSONAL SHOPPER 한국 셀러 픽스처 캡처**

```bash
curl -s -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
     -H "Accept-Language: ja,en-US;q=0.9,ko;q=0.8" \
     "https://www.buyma.com/buyer/13053653/sales_1.html" > tests/fixtures/seller_personal_kr.html
```

확인: `grep -c 'KONNECT' tests/fixtures/seller_personal_kr.html` 결과가 1 이상이고, `label_premium`은 없음.

- [ ] **Step 5: SHOP 셀러 픽스처 캡처**

```bash
curl -s -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
     -H "Accept-Language: ja,en-US;q=0.9,ko;q=0.8" \
     "https://www.buyma.com/buyer/12210564/sales_1.html" > tests/fixtures/seller_shop.html
```

확인: `grep -c 'label_shop' tests/fixtures/seller_shop.html` 결과가 1 이상.

**⚠️ SHOP 페이지 DOM 차이 주의:** SHOP 셀러(`12210564` setof)의 페이지는 PERSONAL SHOPPER 페이지와 DOM 구조가 다를 수 있다. 사용자가 직접 확인한 실제 값은:
- 셀러명: `setof`
- 팔로워: `985`
- 출품수: `138`
- 주문실적: `1179`

캡처한 fixture에서 위 값들이 어떤 HTML 구조에 있는지 직접 확인하라:
```bash
grep -A2 -B2 '985\|setof\|138\|1179' tests/fixtures/seller_shop.html | head -40
```

만약 PERSONAL SHOPPER 페이지의 셀렉터(`#buyer_name h1 a`, `span.fan_cnt`, `span.syohin_cnt_text`, `p.buyer_eva_text`)로 위 값들이 추출되지 않는다면, Task 5 구현 시 SHOP 페이지용 별도 셀렉터를 식별하여 분기 처리해야 한다.

- [ ] **Step 6: PERSONAL SHOPPER 일본 셀러 픽스처 캡처 (필터 탈락 케이스)**

목록 페이지에서 일본 셀러 ID 하나를 찾는다:

```bash
python3 -c "
from bs4 import BeautifulSoup
with open('tests/fixtures/listing_page.html', 'r', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'lxml')
ids = set()
for a in soup.select('a[href^=\"/buyer/\"]'):
    import re
    m = re.match(r'/buyer/(\d+)\.html', a.get('href', ''))
    if m:
        ids.add(m.group(1))
print(list(ids)[:20])
"
```

위에서 출력된 ID들 중 하나를 골라 셀러 페이지에 접속하여 일본 국가(alt="日本") 표시가 있는 셀러를 찾는다. 첫 ID부터 시도:

```bash
SELLER_ID=4842306  # 위 출력에서 고른 ID로 교체
curl -s -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
     -H "Accept-Language: ja,en-US;q=0.9,ko;q=0.8" \
     "https://www.buyma.com/buyer/${SELLER_ID}/sales_1.html" > /tmp/check.html
grep 'alt="' /tmp/check.html | head -5
```

`alt="日本"` 이 보이는 셀러를 찾으면 해당 ID로 픽스처 저장:

```bash
cp /tmp/check.html tests/fixtures/seller_personal_jp.html
```

확인: `grep 'alt="日本"' tests/fixtures/seller_personal_jp.html` 결과가 1줄 이상.

- [ ] **Step 7: 셀러 fixture를 Playwright로 재캡처 (CRITICAL)**

Task 2의 초기 curl 캡처는 정적 HTML만 가져오므로 follower count가 비어있다.
Playwright로 셀러 fixture 4개(premium_kr, personal_kr, shop, personal_jp)를 재캡처한다.
listing_page.html은 정적이므로 재캡처 불필요.

설치:
```bash
pip install playwright==1.48.0
playwright install chromium
```

requirements.txt에도 추가:
```
playwright==1.48.0
```

스크립트 생성: `scripts/capture_seller_fixtures.py`

```python
"""Re-capture seller page fixtures using Playwright to ensure follower count is rendered."""
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright


SELLERS = [
    ("seller_premium_kr.html", "1415418"),
    ("seller_personal_kr.html", "13053653"),
    ("seller_shop.html", "12210564"),
    ("seller_personal_jp.html", "<JP_ID>"),  # Task 2 Step 6에서 식별된 ID로 교체
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

OUT_DIR = Path("tests/fixtures")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            locale="ja-JP",
            extra_http_headers={"Accept-Language": "ja,en-US;q=0.9,ko;q=0.8"},
        )
        page = ctx.new_page()
        for filename, seller_id in SELLERS:
            url = f"https://www.buyma.com/buyer/{seller_id}/sales_1.html"
            print(f"Fetching {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # follower count가 JS로 채워질 때까지 대기 (최대 5초)
            try:
                page.wait_for_selector("#js_fan_count:not(:empty)", timeout=5000)
            except Exception as e:
                print(f"  warning: js_fan_count not populated within 5s: {e}")
            html = page.content()
            out_path = OUT_DIR / filename
            out_path.write_text(html, encoding="utf-8")
            print(f"  -> {out_path} ({len(html)} bytes)")
            time.sleep(0.5)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

실행:
```bash
python scripts/capture_seller_fixtures.py
```

검증 — 모든 셀러 fixture에 follower 값이 들어있어야 함:
```bash
echo "=== Each seller fixture should have non-empty js_fan_count ==="
for f in tests/fixtures/seller_*.html; do
  echo "--- $f ---"
  grep -oE 'id="js_fan_count"[^>]*>[^<]*<' "$f" || echo "  (selector not found)"
done

echo "=== setof should now show 985 followers ==="
grep -oE 'js_fan_count[^"]*"[^"]*">[^<]+<' tests/fixtures/seller_shop.html || true
```

setof fixture에서 985 (또는 비슷한 현재값)이 보여야 함. 다른 fixture도 0이 아닌 숫자가 보여야 함.

- [ ] **Step 8: 커밋**

```bash
git add tests/fixtures/ scripts/ requirements.txt
git commit -m "test: re-capture seller fixtures via Playwright for follower count"
```

---

(아래 `Step 7: 커밋`은 초기 curl 캡처에 대한 commit이며 이미 완료됨. 무시.)

- [ ] **Step 7: 커밋 (기존 — 완료됨)**

```bash
git add tests/fixtures/
git commit -m "test: capture HTML fixtures for listing and seller pages"
```

---

## Task 3: 목록 페이지 파서 (`crawler/listing.py`)

**Files:**
- Create: `tests/test_listing.py`
- Create: `crawler/listing.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_listing.py
from pathlib import Path
from crawler.listing import build_listing_url, parse_seller_ids


FIXTURE = Path(__file__).parent / "fixtures" / "listing_page.html"


def test_build_listing_url_page_1():
    assert build_listing_url(1) == "https://www.buyma.com/r/-A2002003000_1/"


def test_build_listing_url_page_67():
    assert build_listing_url(67) == "https://www.buyma.com/r/-A2002003000_67/"


def test_parse_seller_ids_returns_set_of_strings():
    html = FIXTURE.read_text(encoding="utf-8")
    ids = parse_seller_ids(html)
    assert isinstance(ids, set)
    assert len(ids) >= 10
    for sid in ids:
        assert isinstance(sid, str)
        assert sid.isdigit()


def test_parse_seller_ids_deduplicates():
    # 동일 ID가 동일 페이지에 여러 번 나타나도 set으로 dedupe됨
    html = '<a href="/buyer/123.html">A</a><a href="/buyer/123.html">B</a>'
    ids = parse_seller_ids(html)
    assert ids == {"123"}


def test_parse_seller_ids_ignores_non_buyer_links():
    html = '<a href="/item/999.html">A</a><a href="/buyer/123.html">B</a>'
    ids = parse_seller_ids(html)
    assert ids == {"123"}
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
source .venv/bin/activate
pytest tests/test_listing.py -v
```

Expected: ImportError 또는 ModuleNotFoundError — `crawler.listing` 모듈 없음

- [ ] **Step 3: 최소 구현**

```python
# crawler/listing.py
import re
from bs4 import BeautifulSoup


LISTING_URL_TEMPLATE = "https://www.buyma.com/r/-A2002003000_{n}/"
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
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_listing.py -v
```

Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add crawler/listing.py tests/test_listing.py
git commit -m "feat: parse seller IDs from listing pages"
```

---

## Task 4: 페이지네이션 감지 (`crawler/pagination.py`)

**Files:**
- Create: `tests/test_pagination.py`
- Create: `crawler/pagination.py`

목록 페이지 1을 분석해 마지막 페이지 번호를 추출한다. BUYMA는 페이지네이션 컨트롤에 마지막 페이지 링크가 있다. (`<a href="/r/-A2002003000_100/">100</a>` 와 같은 패턴)

- [ ] **Step 1: 픽스처에서 페이지네이션 구조 확인**

```bash
python3 -c "
from bs4 import BeautifulSoup
with open('tests/fixtures/listing_page.html') as f:
    soup = BeautifulSoup(f.read(), 'lxml')
import re
hrefs = []
for a in soup.find_all('a', href=True):
    if re.search(r'-A2002003000_\d+', a['href']):
        hrefs.append(a['href'])
print(sorted(set(hrefs))[-10:])
"
```

마지막 페이지 번호가 어떤 패턴으로 노출되는지 확인 (예: `_99/`, `_100/` 등).

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/test_pagination.py
from pathlib import Path
from crawler.pagination import parse_max_pages


FIXTURE = Path(__file__).parent / "fixtures" / "listing_page.html"


def test_parse_max_pages_returns_positive_int():
    html = FIXTURE.read_text(encoding="utf-8")
    max_pages = parse_max_pages(html)
    assert isinstance(max_pages, int)
    assert max_pages >= 1


def test_parse_max_pages_extracts_highest_number():
    html = '''
        <a href="/r/-A2002003000_1/">1</a>
        <a href="/r/-A2002003000_2/">2</a>
        <a href="/r/-A2002003000_99/">99</a>
        <a href="/r/-A2002003000_100/">最後</a>
    '''
    assert parse_max_pages(html) == 100


def test_parse_max_pages_returns_1_when_no_pagination():
    html = '<html><body>no pagination</body></html>'
    assert parse_max_pages(html) == 1
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
pytest tests/test_pagination.py -v
```

Expected: ImportError

- [ ] **Step 4: 최소 구현**

```python
# crawler/pagination.py
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
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
pytest tests/test_pagination.py -v
```

Expected: 3 passed

- [ ] **Step 6: 커밋**

```bash
git add crawler/pagination.py tests/test_pagination.py
git commit -m "feat: detect max pagination from listing page"
```

---

## Task 5: 셀러 페이지 파서 (`crawler/seller.py`)

**Files:**
- Create: `tests/test_seller.py`
- Create: `crawler/seller.py`

가장 복잡한 파싱 로직. 필터링 + 7개 필드 추출. 픽스처 4개로 모든 분기 커버.

- [ ] **Step 1: URL 빌더 테스트 작성**

```python
# tests/test_seller.py
from pathlib import Path
from crawler.seller import build_seller_url, parse_seller_page


FIXTURES = Path(__file__).parent / "fixtures"


def test_build_seller_url():
    assert build_seller_url("13053653") == "https://www.buyma.com/buyer/13053653/sales_1.html"


def test_build_seller_url_accepts_int_like_string():
    assert build_seller_url("1") == "https://www.buyma.com/buyer/1/sales_1.html"
```

- [ ] **Step 2: 필터 통과 테스트 작성 (PREMIUM 한국)**

```python
def test_parse_premium_kr_returns_full_data():
    html = (FIXTURES / "seller_premium_kr.html").read_text(encoding="utf-8")
    result = parse_seller_page(html, "1415418")

    assert result is not None
    assert result["seller_id"] == "1415418"
    assert result["seller_type"] == "PREMIUM PERSONAL SHOPPER"
    assert result["country"] == "한국"
    assert result["seller_url"] == "https://www.buyma.com/buyer/1415418/sales_1.html"
    assert isinstance(result["seller_name"], str) and len(result["seller_name"]) > 0
    assert isinstance(result["follower_count"], int) and result["follower_count"] >= 0
    assert isinstance(result["listing_count"], int) and result["listing_count"] >= 0
    assert isinstance(result["order_count"], int) and result["order_count"] >= 0
```

- [ ] **Step 3: 필터 통과 테스트 작성 (PERSONAL 한국)**

```python
def test_parse_personal_kr_returns_full_data():
    html = (FIXTURES / "seller_personal_kr.html").read_text(encoding="utf-8")
    result = parse_seller_page(html, "13053653")

    assert result is not None
    assert result["seller_type"] == "PERSONAL SHOPPER"
    assert result["country"] == "한국"
    assert result["seller_name"] == "KONNECT"
```

- [ ] **Step 4: 필터 통과 테스트 작성 (SHOP, 국가 없음)**

```python
def test_parse_shop_returns_data_with_null_country():
    html = (FIXTURES / "seller_shop.html").read_text(encoding="utf-8")
    result = parse_seller_page(html, "12210564")

    assert result is not None
    assert result["seller_type"] == "SHOP"
    assert result["country"] is None
    # 사용자가 실제 페이지에서 확인한 값
    assert result["seller_name"] == "setof"
    assert result["follower_count"] == 985
    assert result["listing_count"] == 138
    assert result["order_count"] == 1179
```

**⚠️ SHOP 페이지 DOM 차이 가능성:** 위 assertion이 실패하면 SHOP 페이지가 PERSONAL SHOPPER 페이지와 다른 DOM 구조를 가지고 있다는 의미. 그 경우 다음을 수행:
1. `tests/fixtures/seller_shop.html`을 열어 셀러명/팔로워/출품수/주문실적이 위치한 실제 HTML 구조 확인
2. `parse_seller_page`에서 `seller_type == "SHOP"` 분기 시 별도 셀렉터 사용하도록 구현 분기
3. 또는 PERSONAL/SHOP 양쪽 셀렉터를 try-fallback 패턴으로 시도

- [ ] **Step 5: 필터 탈락 테스트 작성 (PERSONAL 일본)**

```python
def test_parse_personal_jp_returns_none():
    html = (FIXTURES / "seller_personal_jp.html").read_text(encoding="utf-8")
    # 일본 셀러 ID는 픽스처 캡처 시 실제 값으로 알려져 있음.
    # 파싱 결과만 검증하면 되므로 ID는 자리값 사용.
    result = parse_seller_page(html, "4842306")

    assert result is None
```

- [ ] **Step 6: 콤마 포함 숫자 파싱 테스트 작성**

```python
def test_parse_handles_comma_in_numbers():
    html = '''
    <html>
      <div id="buyer_name">
        <h1>
          <a href="/buyer/999.html">TEST_SHOP</a>
          <img src="x" alt="한국">
        </h1>
      </div>
      <p class="label">
        <span class="label_premium">PREMIUM PERSONAL SHOPPER</span>
      </p>
      <span class="fan_text">
        <span class="fan_cnt">팔로워 1,234</span> 명
      </span>
      <span class="syohin_cnt_text">12,345 <span class="syohin_text">出品数</span></span>
      <p class="buyer_eva_text">5,678件</p>
    </html>
    '''
    result = parse_seller_page(html, "999")
    assert result is not None
    assert result["follower_count"] == 1234
    assert result["listing_count"] == 12345
    assert result["order_count"] == 5678
```

- [ ] **Step 7: 누락 셀렉터 fallback 테스트 작성**

```python
def test_parse_missing_count_fields_default_to_zero():
    html = '''
    <html>
      <div id="buyer_name">
        <h1>
          <a href="/buyer/999.html">TEST</a>
          <img src="x" alt="한국">
        </h1>
      </div>
      <p class="label">
        <span class="label_premium">PREMIUM PERSONAL SHOPPER</span>
      </p>
    </html>
    '''
    result = parse_seller_page(html, "999")
    assert result is not None
    assert result["follower_count"] == 0
    assert result["listing_count"] == 0
    assert result["order_count"] == 0


def test_parse_missing_label_returns_none():
    html = '''
    <html>
      <div id="buyer_name">
        <h1>
          <a href="/buyer/999.html">TEST</a>
        </h1>
      </div>
    </html>
    '''
    result = parse_seller_page(html, "999")
    assert result is None


def test_parse_missing_name_returns_none():
    html = '''
    <html>
      <p class="label">
        <span class="label_shop">SHOP</span>
      </p>
    </html>
    '''
    result = parse_seller_page(html, "999")
    assert result is None
```

- [ ] **Step 8: 테스트 실패 확인**

```bash
pytest tests/test_seller.py -v
```

Expected: ImportError

- [ ] **Step 9: 최소 구현**

```python
# crawler/seller.py
import re
from bs4 import BeautifulSoup, Tag


SELLER_URL_TEMPLATE = "https://www.buyma.com/buyer/{seller_id}/sales_1.html"
KOREA_COUNTRY_LABEL = "한국"
_DIGIT_PATTERN = re.compile(r"[\d,]+")


def build_seller_url(seller_id: str) -> str:
    return SELLER_URL_TEMPLATE.format(seller_id=seller_id)


def parse_seller_page(html: str, seller_id: str) -> dict | None:
    """셀러 페이지 HTML을 파싱하여 필터링 후 데이터를 반환.

    필터 탈락(한국이 아닌 personal shopper)이거나, 필수 필드(이름/라벨)
    파싱 실패 시 None 반환.
    """
    soup = BeautifulSoup(html, "lxml")

    seller_type = _extract_seller_type(soup)
    if seller_type is None:
        return None

    seller_name = _extract_seller_name(soup)
    if not seller_name:
        return None

    country = _extract_country(soup)

    if not _passes_filter(seller_type, country):
        return None

    return {
        "seller_id": seller_id,
        "seller_name": seller_name,
        "seller_type": seller_type,
        "seller_url": build_seller_url(seller_id),
        "country": country,
        "follower_count": _extract_int(soup, "span.fan_cnt"),
        "listing_count": _extract_int(soup, "span.syohin_cnt_text"),
        "order_count": _extract_int(soup, "p.buyer_eva_text"),
    }


def _extract_seller_type(soup: BeautifulSoup) -> str | None:
    label = soup.select_one("p.label")
    if label is None:
        return None
    if label.select_one("span.label_shop"):
        return "SHOP"
    if label.select_one("span.label_premium"):
        return "PREMIUM PERSONAL SHOPPER"
    text = label.get_text(strip=True).upper()
    if not text:
        return None
    return text


def _extract_seller_name(soup: BeautifulSoup) -> str:
    el = soup.select_one("#buyer_name h1 a")
    if el is None:
        return ""
    return el.get_text(strip=True)


def _extract_country(soup: BeautifulSoup) -> str | None:
    img = soup.select_one("#buyer_name h1 img")
    if img is None:
        return None
    alt = img.get("alt", "").strip()
    return alt or None


def _extract_int(soup: BeautifulSoup, selector: str) -> int:
    el = soup.select_one(selector)
    if el is None:
        return 0
    m = _DIGIT_PATTERN.search(el.get_text())
    if m is None:
        return 0
    return int(m.group(0).replace(",", ""))


def _passes_filter(seller_type: str, country: str | None) -> bool:
    if seller_type == "SHOP":
        return True
    if seller_type in ("PREMIUM PERSONAL SHOPPER", "PERSONAL SHOPPER"):
        return country == KOREA_COUNTRY_LABEL
    return False
```

- [ ] **Step 10: 테스트 통과 확인**

```bash
pytest tests/test_seller.py -v
```

Expected: 모든 테스트 통과

만약 PREMIUM 한국 / PERSONAL 한국 테스트가 셀러명/팔로워수 등 픽스처 의존 부분에서 실패하면, 픽스처를 직접 열어 실제 값과 셀렉터를 확인하여 테스트 또는 파서를 보정한다. 이 작업은 picking-up-real-html 절차이므로 정상 절차.

- [ ] **Step 11: 커밋**

```bash
git add crawler/seller.py tests/test_seller.py
git commit -m "feat: parse and filter seller pages by type and country"
```

---

## Task 6: HTTP 클라이언트 (`crawler/client.py`)

**Files:**
- Create: `crawler/client.py`

외부 호출의 단일 경계. 단위 테스트는 `httpx.MockTransport`로 격리.

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_client.py
import httpx
import pytest

from crawler.client import BuyMaClient, MaxRetriesExceeded


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return BuyMaClient(transport=transport, sleep_seconds=0.0)


def test_get_returns_response_on_200():
    def handler(request):
        return httpx.Response(200, text="ok")

    client = make_client(handler)
    resp = client.get("https://example.com/")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_get_retries_on_500_then_succeeds():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(500, text="server error")
        return httpx.Response(200, text="ok")

    client = make_client(handler)
    resp = client.get("https://example.com/")
    assert resp.status_code == 200
    assert calls["count"] == 3


def test_get_raises_after_max_retries():
    def handler(request):
        return httpx.Response(503, text="unavailable")

    client = make_client(handler)
    with pytest.raises(MaxRetriesExceeded):
        client.get("https://example.com/")


def test_get_does_not_retry_on_404():
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(404, text="not found")

    client = make_client(handler)
    with pytest.raises(MaxRetriesExceeded):
        client.get("https://example.com/")
    assert calls["count"] == 1


def test_get_sets_user_agent():
    captured = {}

    def handler(request):
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text="ok")

    client = make_client(handler)
    client.get("https://example.com/")
    assert captured["ua"] is not None
    assert "Chrome" in captured["ua"]
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_client.py -v
```

Expected: ImportError

- [ ] **Step 3: 구현**

```python
# crawler/client.py
import time

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


class MaxRetriesExceeded(Exception):
    def __init__(self, url: str, last_status: int | None, last_error: str | None):
        self.url = url
        self.last_status = last_status
        self.last_error = last_error
        super().__init__(
            f"max retries exceeded for {url} "
            f"(last_status={last_status}, last_error={last_error})"
        )


class BuyMaClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        sleep_seconds: float = SLEEP_SECONDS,
    ):
        self._client = httpx.Client(
            http2=True,
            headers=DEFAULT_HEADERS,
            timeout=TIMEOUT,
            transport=transport,
            follow_redirects=True,
        )
        self._sleep_seconds = sleep_seconds

    def get(self, url: str) -> httpx.Response:
        last_status: int | None = None
        last_error: str | None = None

        for attempt in range(len(RETRY_BACKOFFS) + 1):
            try:
                response = self._client.get(url)
            except httpx.RequestError as e:
                last_error = repr(e)
                last_status = None
            else:
                last_status = response.status_code
                last_error = None
                if response.status_code not in RETRY_STATUSES:
                    self._sleep()
                    if response.status_code >= 400:
                        raise MaxRetriesExceeded(url, last_status, None)
                    return response

            if attempt < len(RETRY_BACKOFFS):
                time.sleep(RETRY_BACKOFFS[attempt])

        self._sleep()
        raise MaxRetriesExceeded(url, last_status, last_error)

    def _sleep(self) -> None:
        if self._sleep_seconds > 0:
            time.sleep(self._sleep_seconds)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BuyMaClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_client.py -v
```

Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add crawler/client.py tests/test_client.py
git commit -m "feat: add HTTP client with retry, UA, sleep policy"
```

---

## Task 7: 저장소 모듈 (`storage/store.py`)

**Files:**
- Create: `tests/test_store.py`
- Create: `storage/store.py`

파일 시스템 I/O의 단일 경계. config/sellers/errors 3개 파일 관리.

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_store.py
import json
from pathlib import Path

import pytest

from storage.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(data_dir=tmp_path)


def test_load_config_returns_empty_dict_when_missing(store: Store):
    assert store.load_config() == {}


def test_save_and_load_config_roundtrip(store: Store):
    cfg = {"max_pages": 67, "url": "https://example.com"}
    store.save_config(cfg)
    assert store.load_config() == cfg


def test_load_sellers_returns_empty_dict_when_missing(store: Store):
    assert store.load_sellers() == {}


def test_save_and_load_sellers_roundtrip(store: Store):
    sellers = {
        "123": {"seller_id": "123", "seller_name": "A", "follower_count": 10}
    }
    store.save_sellers(sellers)
    assert store.load_sellers() == sellers


def test_merge_sellers_preserves_first_seen_at():
    existing = {
        "123": {
            "seller_id": "123",
            "seller_name": "old name",
            "follower_count": 5,
            "first_seen_at": "2026-01-01T00:00:00+09:00",
            "updated_at": "2026-01-01T00:00:00+09:00",
        }
    }
    new = {
        "123": {
            "seller_id": "123",
            "seller_name": "new name",
            "follower_count": 10,
            "first_seen_at": "2026-06-02T00:00:00+09:00",
            "updated_at": "2026-06-02T00:00:00+09:00",
        }
    }
    merged = Store.merge_sellers(existing, new)
    assert merged["123"]["seller_name"] == "new name"
    assert merged["123"]["follower_count"] == 10
    assert merged["123"]["first_seen_at"] == "2026-01-01T00:00:00+09:00"
    assert merged["123"]["updated_at"] == "2026-06-02T00:00:00+09:00"


def test_merge_sellers_adds_new_seller():
    existing = {
        "123": {
            "seller_id": "123",
            "first_seen_at": "2026-01-01T00:00:00+09:00",
        }
    }
    new = {
        "456": {
            "seller_id": "456",
            "first_seen_at": "2026-06-02T00:00:00+09:00",
        }
    }
    merged = Store.merge_sellers(existing, new)
    assert "123" in merged
    assert "456" in merged
    assert merged["456"]["first_seen_at"] == "2026-06-02T00:00:00+09:00"


def test_append_error_creates_log_file(store: Store, tmp_path: Path):
    store.append_error(
        stage="seller",
        url="https://example.com/x",
        status=503,
        reason="server error",
    )
    log_file = tmp_path / "errors.log"
    assert log_file.exists()
    line = log_file.read_text().strip()
    record = json.loads(line)
    assert record["stage"] == "seller"
    assert record["url"] == "https://example.com/x"
    assert record["status"] == 503
    assert record["reason"] == "server error"
    assert "timestamp" in record


def test_append_error_appends_multiple_lines(store: Store, tmp_path: Path):
    store.append_error("a", "u1", 500, "x")
    store.append_error("b", "u2", 404, "y")
    log_file = tmp_path / "errors.log"
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_store.py -v
```

Expected: ImportError

- [ ] **Step 3: 구현**

```python
# storage/store.py
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


KST = timezone(timedelta(hours=9))


def now_iso() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "config.json"
        self.sellers_path = self.data_dir / "sellers.json"
        self.errors_path = self.data_dir / "errors.log"

    def load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def save_config(self, config: dict) -> None:
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_sellers(self) -> dict[str, dict]:
        if not self.sellers_path.exists():
            return {}
        return json.loads(self.sellers_path.read_text(encoding="utf-8"))

    def save_sellers(self, sellers: dict[str, dict]) -> None:
        self.sellers_path.write_text(
            json.dumps(sellers, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def merge_sellers(
        existing: dict[str, dict], new: dict[str, dict]
    ) -> dict[str, dict]:
        """Merge new sellers into existing.

        For each seller in `new`, all fields overwrite the existing entry
        EXCEPT `first_seen_at` which is preserved from the existing entry
        when present.
        """
        merged = dict(existing)
        for seller_id, data in new.items():
            if seller_id in merged and "first_seen_at" in merged[seller_id]:
                preserved = merged[seller_id]["first_seen_at"]
                merged[seller_id] = {**data, "first_seen_at": preserved}
            else:
                merged[seller_id] = dict(data)
        return merged

    def append_error(
        self,
        stage: str,
        url: str,
        status: int | None,
        reason: str,
    ) -> None:
        record = {
            "timestamp": now_iso(),
            "stage": stage,
            "url": url,
            "status": status,
            "reason": reason,
        }
        with self.errors_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_store.py -v
```

Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add storage/store.py tests/test_store.py
git commit -m "feat: add file-based storage for config, sellers, errors"
```

---

## Task 8: 병렬 크롤링 오케스트레이션 (`crawler/listing.py`, `crawler/seller.py` 확장)

**Files:**
- Modify: `crawler/listing.py`
- Modify: `crawler/seller.py`
- Modify: `tests/test_listing.py`
- Modify: `tests/test_seller.py`

목록/셀러 페이지 순회를 ThreadPoolExecutor로 병렬화. 클라이언트를 주입받는 함수로 분리.

- [ ] **Step 1: 목록 순회 함수 테스트 추가**

```python
# tests/test_listing.py 끝에 추가
from unittest.mock import MagicMock
from crawler.listing import crawl_listing_pages


def test_crawl_listing_pages_aggregates_seller_ids_across_pages():
    page1 = '<a href="/buyer/100.html">A</a><a href="/buyer/101.html">B</a>'
    page2 = '<a href="/buyer/101.html">B</a><a href="/buyer/102.html">C</a>'

    client = MagicMock()
    responses = {
        "https://www.buyma.com/r/-A2002003000_1/": page1,
        "https://www.buyma.com/r/-A2002003000_2/": page2,
    }

    def fake_get(url):
        resp = MagicMock()
        resp.text = responses[url]
        return resp

    client.get.side_effect = fake_get

    on_error = MagicMock()
    ids = crawl_listing_pages(client, max_pages=2, on_error=on_error)
    assert ids == {"100", "101", "102"}
    on_error.assert_not_called()


def test_crawl_listing_pages_records_error_and_continues():
    page2 = '<a href="/buyer/200.html">X</a>'

    client = MagicMock()

    def fake_get(url):
        if url.endswith("_1/"):
            raise RuntimeError("boom")
        resp = MagicMock()
        resp.text = page2
        return resp

    client.get.side_effect = fake_get

    on_error = MagicMock()
    ids = crawl_listing_pages(client, max_pages=2, on_error=on_error)
    assert ids == {"200"}
    assert on_error.call_count == 1
    args = on_error.call_args.kwargs or dict(zip(["stage", "url", "status", "reason"], on_error.call_args.args))
    assert args["stage"] == "listing"
```

- [ ] **Step 2: 셀러 순회 함수 테스트 추가**

```python
# tests/test_seller.py 끝에 추가
from unittest.mock import MagicMock
from crawler.seller import crawl_sellers


def test_crawl_sellers_returns_only_filter_passed_dicts():
    shop_html = (FIXTURES / "seller_shop.html").read_text(encoding="utf-8")
    jp_html = (FIXTURES / "seller_personal_jp.html").read_text(encoding="utf-8")

    client = MagicMock()
    responses = {
        "https://www.buyma.com/buyer/SHOP_ID/sales_1.html": shop_html,
        "https://www.buyma.com/buyer/JP_ID/sales_1.html": jp_html,
    }

    def fake_get(url):
        resp = MagicMock()
        resp.text = responses[url]
        return resp

    client.get.side_effect = fake_get
    on_error = MagicMock()
    results = crawl_sellers(client, {"SHOP_ID", "JP_ID"}, on_error=on_error)

    assert len(results) == 1
    assert results[0]["seller_id"] == "SHOP_ID"
    on_error.assert_not_called()


def test_crawl_sellers_records_error_and_continues():
    client = MagicMock()

    def fake_get(url):
        if "FAIL" in url:
            raise RuntimeError("boom")
        return MagicMock(text="<html></html>")

    client.get.side_effect = fake_get
    on_error = MagicMock()
    results = crawl_sellers(client, {"FAIL", "OK"}, on_error=on_error)

    assert results == []  # OK도 빈 HTML이라 필터 탈락
    assert on_error.call_count == 1
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
pytest tests/test_listing.py tests/test_seller.py -v
```

Expected: ImportError on `crawl_listing_pages` / `crawl_sellers`

- [ ] **Step 4: `crawler/listing.py`에 함수 추가**

기존 파일 끝에 다음 추가:

```python
# crawler/listing.py (기존 내용 아래에 추가)
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable


MAX_WORKERS = 3


def crawl_listing_pages(
    client,
    max_pages: int,
    on_error: Callable[..., None],
) -> set[str]:
    """모든 목록 페이지를 병렬로 가져와 seller_id 집합을 반환."""
    all_ids: set[str] = set()
    urls = [build_listing_url(n) for n in range(1, max_pages + 1)]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
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
```

- [ ] **Step 5: `crawler/seller.py`에 함수 추가**

기존 파일 끝에 다음 추가:

```python
# crawler/seller.py (기존 내용 아래에 추가)
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable


MAX_WORKERS = 3


def crawl_sellers(
    client,
    seller_ids: set[str],
    on_error: Callable[..., None],
) -> list[dict]:
    """각 셀러 페이지를 병렬로 가져와 필터 통과 항목만 리스트로 반환."""
    results: list[dict] = []
    id_to_url = {sid: build_seller_url(sid) for sid in seller_ids}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {
            executor.submit(client.get, url): sid for sid, url in id_to_url.items()
        }
        for future in as_completed(future_to_id):
            sid = future_to_id[future]
            url = id_to_url[sid]
            try:
                response = future.result()
                parsed = parse_seller_page(response.text, sid)
                if parsed is not None:
                    results.append(parsed)
            except Exception as e:
                status = getattr(e, "last_status", None)
                on_error(
                    stage="seller",
                    url=url,
                    status=status,
                    reason=repr(e),
                )
    return results
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
pytest tests/test_listing.py tests/test_seller.py -v
```

Expected: 모든 테스트 통과

- [ ] **Step 7: 커밋**

```bash
git add crawler/listing.py crawler/seller.py tests/test_listing.py tests/test_seller.py
git commit -m "feat: add parallel crawl orchestration for listing and seller pages"
```

---

## Task 9: 메인 CLI (`main.py`)

**Files:**
- Create: `main.py`

전체 파이프라인 조립. 비즈니스 로직 없이 위 모듈들의 호출 순서와 로깅만.

- [ ] **Step 1: 구현**

```python
# main.py
import argparse
import logging
import sys
from pathlib import Path

from crawler.client import BuyMaClient, MaxRetriesExceeded
from crawler.listing import build_listing_url, crawl_listing_pages
from crawler.pagination import parse_max_pages
from crawler.seller import crawl_sellers
from storage.store import Store, now_iso


DATA_DIR = Path(__file__).parent / "data"


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def detect_max_pages(client: BuyMaClient) -> int:
    url = build_listing_url(1)
    logging.info("Detecting max pages from %s", url)
    response = client.get(url)
    max_pages = parse_max_pages(response.text)
    logging.info("Detected max_pages = %d", max_pages)
    return max_pages


def run(reset_pagination: bool, dry_run: bool) -> int:
    store = Store(DATA_DIR)
    config = store.load_config()

    with BuyMaClient() as client:
        if reset_pagination or "max_pages" not in config:
            try:
                max_pages = detect_max_pages(client)
            except MaxRetriesExceeded as e:
                logging.error("Failed to detect max pages: %s", e)
                return 1
            config["max_pages"] = max_pages
            config["max_pages_detected_at"] = now_iso()
            if not dry_run:
                store.save_config(config)
        else:
            max_pages = config["max_pages"]
            logging.info("Using cached max_pages = %d", max_pages)

        logging.info("Stage 1: scanning %d listing pages", max_pages)
        seller_ids = crawl_listing_pages(
            client,
            max_pages=max_pages,
            on_error=store.append_error,
        )
        logging.info("Found %d unique seller IDs in listings", len(seller_ids))

        logging.info("Stage 2: fetching %d seller pages", len(seller_ids))
        collected = crawl_sellers(
            client,
            seller_ids=seller_ids,
            on_error=store.append_error,
        )
        logging.info(
            "Collected %d sellers (filtered out %d)",
            len(collected),
            len(seller_ids) - len(collected),
        )

    timestamp = now_iso()
    new_sellers_map: dict[str, dict] = {}
    for seller in collected:
        seller["first_seen_at"] = timestamp
        seller["updated_at"] = timestamp
        new_sellers_map[seller["seller_id"]] = seller

    existing = store.load_sellers()
    merged = Store.merge_sellers(existing, new_sellers_map)

    errors_count = _count_errors_today(store.errors_path)

    config["last_run_at"] = timestamp
    config["last_run_stats"] = {
        "pages_scanned": max_pages,
        "sellers_found_in_listing": len(seller_ids),
        "sellers_collected": len(collected),
        "sellers_filtered_out": len(seller_ids) - len(collected),
        "errors": errors_count,
    }

    if dry_run:
        logging.info("DRY RUN: skipping save. Stats: %s", config["last_run_stats"])
    else:
        store.save_sellers(merged)
        store.save_config(config)
        logging.info(
            "Saved %d total sellers (was %d, added/updated %d)",
            len(merged),
            len(existing),
            len(new_sellers_map),
        )

    return 0


def _count_errors_today(errors_path: Path) -> int:
    if not errors_path.exists():
        return 0
    today = now_iso()[:10]  # YYYY-MM-DD
    count = 0
    with errors_path.open(encoding="utf-8") as f:
        for line in f:
            if today in line:
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="BUYMA Korean seller crawler")
    parser.add_argument(
        "--reset-pagination",
        action="store_true",
        help="Force re-detection of max_pages",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write to data files",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    try:
        return run(reset_pagination=args.reset_pagination, dry_run=args.dry_run)
    except KeyboardInterrupt:
        logging.warning("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 전체 단위 테스트 통과 확인**

```bash
pytest -v
```

Expected: 모든 테스트 통과

- [ ] **Step 3: 통합 스모크 테스트 (적은 페이지로)**

데이터 파일을 별도 위치에 잠시 두고 실제 BUYMA에 1페이지만 호출하는 식의 검증. config.json을 직접 작성하여 max_pages=2로 강제:

```bash
mkdir -p data
cat > data/config.json <<'EOF'
{"max_pages": 2}
EOF

python main.py --verbose
```

Expected:
- 진행 로그 출력
- `data/sellers.json` 생성, 0건 이상의 셀러 수집
- `data/config.json` 의 `last_run_stats` 업데이트
- 종료코드 0

문제 발생 시 `data/errors.log` 확인.

- [ ] **Step 4: dry-run 검증**

```bash
rm -rf data/
mkdir -p data
echo '{"max_pages": 2}' > data/config.json
python main.py --dry-run --verbose
```

Expected: `sellers.json` 미생성, `config.json` 변경 없음, 로그에 "DRY RUN" 출력

- [ ] **Step 5: 커밋**

```bash
git add main.py
git commit -m "feat: add CLI orchestrator for full crawl pipeline"
```

---

## Task 10: 전체 실행 검증 및 마무리

**Files:**
- (none)

- [ ] **Step 1: 데이터 초기화**

```bash
rm -rf data/
```

- [ ] **Step 2: 최초 실행 (max_pages 자동 감지)**

```bash
python main.py --verbose 2>&1 | tee /tmp/first_run.log
```

Expected:
- "Detecting max pages from ..." 로그
- "Detected max_pages = N" 로그 (N은 통상 100 또는 그 이하)
- 두 스테이지 모두 정상 진행
- `data/config.json` 에 `max_pages` 저장됨

- [ ] **Step 3: 결과 sanity 검증**

```bash
python3 -c "
import json
sellers = json.load(open('data/sellers.json'))
print('총 셀러:', len(sellers))
shop = [s for s in sellers.values() if s['seller_type'] == 'SHOP']
premium_kr = [s for s in sellers.values() if s['seller_type'] == 'PREMIUM PERSONAL SHOPPER']
personal_kr = [s for s in sellers.values() if s['seller_type'] == 'PERSONAL SHOPPER']
print('SHOP:', len(shop))
print('PREMIUM PERSONAL SHOPPER (한국):', len(premium_kr))
print('PERSONAL SHOPPER (한국):', len(personal_kr))
# 모든 personal shopper는 country=한국 이어야 함
for s in premium_kr + personal_kr:
    assert s['country'] == '한국', f'Filter violation: {s}'
print('모든 personal shopper의 country가 한국임을 검증 완료')
print('샘플:', list(sellers.values())[0])
"
```

Expected:
- 총 셀러 수 > 0
- 모든 personal shopper의 country == "한국"
- 샘플 셀러에 모든 필드(seller_id, seller_name, ..., first_seen_at, updated_at) 존재

- [ ] **Step 4: 2회차 실행 검증 (config 재사용 + 머지)**

```bash
python main.py --verbose 2>&1 | tee /tmp/second_run.log
```

Expected:
- "Using cached max_pages" 로그
- "Detecting max pages" 로그 없음
- 셀러 수가 1회차와 비슷하거나 약간 증가
- 기존 셀러의 `first_seen_at`이 1회차 값으로 보존됨

검증:

```bash
python3 -c "
import json
sellers = json.load(open('data/sellers.json'))
sample = list(sellers.values())[0]
# first_seen_at <= updated_at 이어야 함
print('first_seen_at:', sample['first_seen_at'])
print('updated_at:', sample['updated_at'])
assert sample['first_seen_at'] <= sample['updated_at']
print('first_seen_at 보존 검증 완료')
"
```

- [ ] **Step 5: 에러 로그 확인 (있다면)**

```bash
if [ -f data/errors.log ]; then
  echo "에러 발생 건수: $(wc -l < data/errors.log)"
  head -3 data/errors.log
else
  echo "에러 없음"
fi
```

3회 재시도 후에도 실패한 페이지가 errors.log에 기록되어 있는지, 또는 비어있는지 확인. 일부 에러는 정상이며 전체 프로세스는 완료되어야 함.

- [ ] **Step 6: README 작성 (선택)**

이번 단계에서는 명시적 요청 없으면 생략. 필요 시 다음 단계로.

- [ ] **Step 7: 최종 커밋 (검증 결과 변경 없음 시 스킵)**

스펙 이행 완료. 데이터 파일은 .gitignore로 제외되어 커밋 없음.

---

## Definition of Done

- [ ] 모든 단위 테스트 통과 (`pytest`)
- [ ] `python main.py` 최초 실행 시 max_pages 자동 감지 후 `data/config.json` 저장
- [ ] `data/sellers.json` 에 필터링된 셀러가 7개 필수 필드로 저장됨
- [ ] 2회차 실행에서 `first_seen_at` 보존, `updated_at` 갱신
- [ ] 모든 personal shopper의 country == "한국"
- [ ] SHOP 셀러는 country 무관 수집됨
- [ ] 실패한 요청은 `data/errors.log`에 jsonl로 기록되고 전체 프로세스는 완료됨
