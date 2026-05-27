# products_api 성능 작업 이력 및 후속 작업 가이드

작성일: 2026-05-14
대상: `/manage/products/` (products.html + data.json/sources.json/images.json)

이 문서는 **왜 지금 이런 구조가 되었는지**, **다음에 서버사이드 페이징(옵션 B)으로 옮길 때 무엇을 알아야 하는지**를 한 번에 파악할 수 있도록 작성됨. 의사결정 맥락과 측정 숫자를 그대로 남겼으니, AI에게 작업 시킬 때도 이 문서를 먼저 읽히면 동일한 실수를 반복하지 않게 할 수 있다.

---

## 0. TL;DR

- `data.json` 504 타임아웃 → **백그라운드 5분 캐시 + 풀스캔 통합쿼리 + gzip + JSON pre-serialize** 조합으로 해결
- 첫 새로고침 약 10초, 페이지/정렬/검색 0초, 응답 사이즈 125MB → **10.7MB** (gzip 8.5%)
- raw 데이터 규모: 150k / unique model_id 124k / ace 134k / images 500k / stats 47k
- **클라이언트 페이징 구조는 raw 200~300k가 천장**. 그 이상 가면 옵션 B(서버사이드 + 사전집계 테이블)로 전환 필요

---

## 1. 발단 (2026-05-14 이전)

기존 구조는 `manage_server/build_cache.py`가 dict를 만들어 `data_cache.json`에 저장하고, 화면은 그 파일을 fetch하는 방식이었음.

hyeji가 "products.html DB기반 fetch로 전환 + 캐시 파일 방식 설계"(커밋 `a587f23`)를 push한 직후 사이트 접속 불가 발생. 그 후 다른 AI들이 여러 차례 시도했으나 모두 504 미해결.

오늘 작업 전 main 브랜치 누적 시도:

| 커밋 | 시도 | 결과 |
|---|---|---|
| `2193660` | sys.path 추가 (ImportError 의심) | 실제 원인 아님. EC2 자체 다운 |
| - | EC2 재부팅 | 사이트 복구 |
| - | t3.micro → t3.medium 업그레이드 | OOM 방지 |
| `122fec2` | 캐시 파일 제거, DB 직접 조회로 변경 | 페이지 열림, 데이터 없음 |
| `ea1250e` | 100건 LIMIT 테스트 | 30초 동작 확인 |
| `8def87d` | build_cache.py 삭제, 코드 정리 | 정리만 |
| `653fc4f` | LIMIT 제한 해제 → 전체 조회 | **504 타임아웃** |
| `562a739` | URL `?limit=` 파라미터 지원 추가 (실제로는 클라만, 서버는 무시) | 504 미해결 |
| `0d58fc8` | SQL JOIN 전면 재작성 (correlated subquery) | 504 지속 |
| `2412af4` | ANY_VALUE() → MAX() (MariaDB 호환) | 500은 수정, 504는 지속 |
| `e56e06f` | `_fetch_first_images` correlated subquery → INNER JOIN | 504 지속 |

이 시점 사용자 호소: "다른 AI가 쿼리 실행해보지도 않고 배포해서 버렸어. 너가 원인을 정확하게 분석해서 해결해줘."

---

## 2. 진짜 원인 진단 (오늘 작업의 핵심)

다른 AI들이 빠진 함정이 두 가지 있었음.

### 함정 #1 — "페이징 100개씩이니까 느릴 리 없다"는 잘못된 가정

`products.html`에 `pageSize = 100` 이 있어서 "100개씩 페이징"으로 보였지만, 실제로는:
- 클라이언트가 `fetch('./data.json')`로 **전체 데이터를 한 번에 받음** (products.html:611)
- 그 후 JS에서 `view.slice(start, start + 100)` 으로 화면에 100개씩만 표시 (products.html:412~413)
- 즉 페이징은 **클라이언트 사이드 메모리 슬라이스**일 뿐, 서버는 매번 전체 데이터를 풀어서 반환

`?limit=100`을 URL에 붙이면 클라이언트가 그 값을 인지하긴 하지만 (products.html:609), `app.py`의 `/data.json` 핸들러는 `request.args`를 보지 않았고 `build_payload(db_cfg)`도 limit 인자가 없었음 → 서버는 항상 전체.

### 함정 #2 — IN-chunk 누적 비용 무시

기존 `products_api.py`는 4개 단계로 데이터를 모았는데, ace_products / first_images / buyma_stats는 각각 model_id 또는 ace_id를 1000개씩 IN 절에 넣어 chunk 단위로 쿼리. 1 chunk당 1초여도 100+ chunk면 1~2분.

---

## 3. 측정 결과 (2026-05-14 시점)

전체 측정은 `_measure_products.py`로 수행 (read-only). 이 파일은 commit하지 않고 untracked로 남김 — 다음에 같은 진단이 필요하면 그대로 재실행.

### 3.1 데이터 규모

| 테이블 | 건수 |
|---|---|
| `raw_scraped_data` | 150,688 |
| → unique model_id | **124,602** |
| `ace_products` | 134,827 |
| `ace_product_images` | 500,198 |
| `buyma_product_stats` | 46,983 |

### 3.2 기존 build_payload (IN-chunk 방식) 전체 실행 시간

| 단계 | 시간 |
|---|---|
| step1 `_fetch_raw_aggregated` (GROUP BY) | 35초 |
| step2 `_fetch_ace_products` (125 chunk × 0.52s) | 65초 |
| step3 `_fetch_first_images` (135 chunk × 0.22s) | 30초 |
| step4 `_fetch_buyma_stats` (66 chunk × 0.02s) | 1.5초 |
| **TOTAL** | **131초** ← 504 타임아웃 원인 |

### 3.3 풀스캔 통합 방식 시간

| 단계 | 시간 |
|---|---|
| step1 GROUP BY (변경 없음) | 35초 |
| step2 ace 풀스캔 | **11초** (6배 빠름) |
| step3 first_image 풀스캔 + GROUP BY | **8.5초** (3.5배 빠름) |
| step4 stats 풀스캔 | 0.9초 |
| **TOTAL** | **55초** |

### 3.4 응답 사이즈 / 클라이언트 시점

- 서버 응답 (jsonify dict): **125.6 MB**
- gzip 압축 후: **10.7 MB** (8.5%, 약 1/12)
- 브라우저 새로고침 시 처리 시간:
  - 적용 전: 약 1분 (다운로드 30~50초 + 파싱 5~10초). DevTools가 응답 메모리에 잡혀서 OOM으로 자동 종료될 정도
  - 적용 후: 약 10초

---

## 4. 적용한 해결책 (옵션 A)

옵션 A = "캐싱 + 쿼리 단순화". 작업 1시간, 미래 한계 있음.
옵션 B = "서버사이드 페이징 + 사전집계 테이블". 작업 2~3일, 근본 해결.

A를 먼저 선택한 이유:
1. 504를 즉시 풀어야 함
2. A의 풀스캔 통합쿼리 로직이 B의 집계 잡 SQL에 그대로 재활용됨 — 헛수고 아님
3. B는 status 판정 + 정렬 + 필터 + 검색을 전부 SQL로 옮겨야 해서 검증 시간이 필요. 함부로 배포 못 함

### 4.1 변경된 파일 목록

| 파일 | 변경 |
|---|---|
| `manage_server/products_api.py` | `_fetch_ace_products`/`_fetch_first_images`/`_fetch_buyma_stats` 3개 함수에서 IN-chunk 루프 제거. 시그니처 `(conn, ids)` → `(conn)`. `build_payload`에서 IN-chunk용 보조변수(model_ids, ace_ids, bp_ids) 삭제 |
| `manage_server/products_cache.py` (신규) | 백그라운드 스레드가 부팅 직후 + 5분마다 `build_payload` 실행. dict가 아니라 **JSON bytes + gzip bytes**로 미리 직렬화/압축해서 보관. `get()`은 `(json_bytes, gzip_bytes)` 튜플 반환 |
| `manage_server/app.py` | import 시점에 `products_cache.start(...)` 호출. `/data.json` 핸들러는 `products_cache.get()` 결과를 `flask.Response`로 직접 송신. `Accept-Encoding: gzip` 헤더 있으면 gzip bytes를, 없으면 raw JSON bytes를. 캐시 None이면 503 + `loading:true` |
| `buyma_stats/products.html` | fetch 블록을 `loadData(retry)` async 함수로 추출. 응답이 503이면 5초 후 자동 재시도, "재시도 N" 메시지 표시 |
| `.github/workflows/deploy-ec2.yml` | 죽은 `python3 build_cache.py` 라인 제거 (build_cache.py는 122fec2에서 삭제됨). 매 배포 시마다 WARN 로그만 찍히던 잔재 |

관련 커밋:
- `0e64b0c` — 캐시 + 풀스캔 (1차)
- `f677dcb` — gzip + JSON pre-serialize (2차)

### 4.2 EC2 운영 구조

- systemd unit: `/etc/systemd/system/buyma-manage.service`
- ExecStart: `/home/ubuntu/buyma/buyma/manage_server/.venv/bin/python app.py`
- **gunicorn 아니라 Flask 개발 서버 단일 프로세스**. worker 1개, `--preload` 없음 → 백그라운드 스레드 안전하게 동작
- Repo path: `/home/ubuntu/buyma/buyma`
- nginx가 8001 포트로 reverse proxy
- 배포: main 브랜치 push → GitHub Actions가 SSH로 git pull + `systemctl restart buyma-manage`

(Flask dev server를 운영에 쓰는 것 자체는 별개 이슈. gunicorn으로 옮길 때는 `--preload` 사용 여부 + worker 수에 따라 캐시 스레드 시작 방식 다시 봐야 함 — 6.4 참조)

### 4.3 실제 동작 흐름 (시점별)

**A. 사용자가 페이지 열거나 새로고침할 때 (1회/세션)**
1. `GET /manage/products/` → products.html 전송
2. JS: `fetch('./data.json')` (Accept-Encoding: gzip 자동 헤더)
3. 서버 (`app.py /data.json`): `products_cache.get()`으로 gzip bytes 즉시 반환 (Content-Encoding: gzip)
4. 브라우저: gzip 해제 → JSON 파싱 → `DATA` 변수 → `renderAll()` → 첫 100건 표시
- 비용: 10MB 다운로드 + 파싱 ≈ **10초**

**B. 페이지 안에서 조작 (페이지 이동/정렬/필터/검색)**
- 서버 호출 0번. JS 메모리 `DATA` 배열을 slice/filter/sort. **0초**

**C. 모달 클릭 (이미지/sources 팝업)**
- 별도 fetch: `images.json?model_id=XXX` / `sources.json?model_id=XXX`
- 캐시 안 거치고 매번 단일 model_id DB 쿼리. 50~200ms

**D. 백그라운드 (사용자 못 느낌)**
- 5분마다 `_refresh_loop`가 `build_payload` 실행 (약 55초 DB + 0.7초 json + 1.1초 gzip ≈ 57~60초)
- 끝나면 `_LOCK` 잡고 `_CACHE['json']` / `_CACHE['gzip']` 교체
- 사용자 응답은 그 동안 이전 캐시 반환

**E. 서버 재시작 직후 (~1~2분)**
- 캐시 None → `/data.json`이 503 + `loading:true` 반환
- 클라이언트가 5초 간격으로 자동 재시도 ("재시도 N" 표시)
- 첫 빌드 완료(약 60초) 후 자동으로 정상 동작

---

## 5. 알려진 한계 (옵션 A의 천장)

### 5.1 응답 사이즈 / 클라이언트 메모리

- 124k items × item당 약 1KB = **125MB 원본 JSON**. gzip 후 10.7MB 전송.
- 브라우저가 받아서 파싱하면 메모리에 **80~150MB** 정도 잡힘 (V8 객체 오버헤드 포함)
- 데스크탑 OK. 모바일/저사양 PC는 부담
- DevTools는 응답 본문을 별도로 메모리 보관하므로 100MB 넘으면 자체 OOM (오늘 발생함)

### 5.2 raw 증가 시 곡선

| raw 행 수 (추정) | unique model | build_payload 시간 | gzip 응답 사이즈 | 평가 |
|---|---|---|---|---|
| 150k (현재) | 124k | ~60초 | 10.7MB | OK |
| 200k | ~165k | ~80초 | ~14MB | OK (캐시 갱신 주기 5→7분으로) |
| 300k | ~250k | ~120초 | ~21MB | 경계 (모바일 부담 / 캐시 갱신 10분) |
| 500k+ | ~400k+ | ~200초 | ~35MB | **옵션 B 전환 필수** |

### 5.3 stale 허용 범위

- 캐시 갱신 주기 5분 → 최악의 경우 5분 stale 데이터를 봄
- buyma_product_stats, ace_products price 변경이 즉시 화면에 반영되지 않음
- 수동 갱신 버튼이 필요해지면 `/manage/products/cache/refresh` POST 엔드포인트 추가하면 됨 (현재 없음)

### 5.4 단일 프로세스 의존

- Flask dev server 단일 프로세스라 캐시가 하나만 존재
- gunicorn 다중 worker로 전환하면 worker별 독립 캐시 → DB 쿼리가 worker 수만큼 동시 실행
- 다중 worker 시: 외부 cron으로 한 번만 빌드해서 파일/redis에 저장하고 worker는 읽기만 하도록 변경 권장

---

## 6. 다음 단계: 옵션 B 전환 가이드

raw가 300k 넘어가거나 모바일 사용자 부담이 커지면 옵션 B로 옮긴다. **단순 페이징 SQL이 아니라 "사전집계 테이블" 도입이 본질**임을 기억할 것.

### 6.1 왜 단순 페이징 SQL로는 부족한가

`products_api.py:_determine_status()` 가 `status` 컬럼을 **4개 테이블 조합으로 즉석 계산**:

```python
def _determine_status(raw_agg, ace_list, in_seller_listing):
    if in_seller_listing: return 'on_sale'      # buyma_product_stats 매칭됨
    if ace_list:
        if any(is_published==1 and is_active==1 for a in ace_list): return 'on_sale'
        if any(is_ready_to_publish==1 and is_published==0 ...):    return 'waiting'
        if any(is_lowest_price==0 and is_published==0 ...):        return 'no_lowest'
    if total > 0 and oos_count >= total: return 'sold_out'
    return 'unknown'
```

페이징할 때 `WHERE status = ?` 처럼 쓰려면 매 쿼리마다 위 로직을 SQL CASE WHEN으로 펼쳐야 함. 거기에 정렬(`ORDER BY margin_amount_krw DESC` 등 다른 테이블 컬럼) + 검색(`LIKE '%X%'` 다국어 컬럼 4개)까지 합치면 매 페이지 fetch가 수십 초 걸릴 가능성 높음. 인덱스도 못 탐.

→ **status, 정렬 키, 검색 키를 모두 평탄화한 사전집계 테이블이 필요.**

### 6.2 권장 스키마: `product_summary`

```sql
CREATE TABLE product_summary (
  model_id              VARCHAR(100) PRIMARY KEY,
  -- 표시용
  buyma_product_id      VARCHAR(50),
  status                ENUM('on_sale','waiting','no_lowest','sold_out','unknown') NOT NULL,
  db_mismatch_reason    VARCHAR(200),
  name_ja               VARCHAR(500),
  name_ko               VARCHAR(500),
  brand_name_en         VARCHAR(200),
  brand_name_kr         VARCHAR(200),
  image_url             VARCHAR(1000),
  source_count          INT NOT NULL DEFAULT 0,
  -- buyma_product_stats
  access_count          INT,
  cart_count            INT,
  favorite_count        INT,
  access_7d             INT,
  -- ace_products
  buyma_lowest_price    DECIMAL(12,2),
  available_lowest_price_jpy DECIMAL(12,2),
  price_yen             DECIMAL(12,2),
  margin_amount_krw     DECIMAL(14,2),
  margin_rate           DECIMAL(6,2),
  price_updated_at      DATETIME,
  source_updated_at     DATETIME,
  registered_at         DATETIME,
  expire_at             DATE,
  -- 정렬/필터/검색용 인덱스
  INDEX idx_status_registered (status, registered_at DESC),
  INDEX idx_access  (status, access_count DESC),
  INDEX idx_fav     (status, favorite_count DESC),
  INDEX idx_margin  (status, margin_amount_krw DESC),
  INDEX idx_name_ja (name_ja),         -- LIKE 'X%' 인덱스 활용
  INDEX idx_name_ko (name_ko),
  INDEX idx_buyma_product_id (buyma_product_id),
  -- 메타
  updated_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

(컬럼명은 products.html이 기대하는 키 이름과 1:1로 맞추는 게 좋음 — 그래야 JSON 응답 변환이 단순)

### 6.3 집계 잡

별도 스크립트 `manage_server/build_product_summary.py`:
- 현재 `build_payload`의 로직을 그대로 옮기되, dict 만드는 대신 `INSERT ... ON DUPLICATE KEY UPDATE`로 `product_summary` 채움
- 실행 빈도: cron으로 5~10분에 한 번
- 또는 raw_scraped_data / ace_products / buyma_product_stats 가 변경되는 시점에 트리거 (이벤트 기반 갱신)

### 6.4 페이징 엔드포인트

```python
@app.route("/manage/products/data.json")
def manage_products_data():
    page    = int(request.args.get('page', 1))
    size    = min(int(request.args.get('size', 100)), 500)
    status  = request.args.get('status')        # 'on_sale' / 'waiting' / ...
    sort    = request.args.get('sort', 'registered_at')
    order   = 'DESC' if request.args.get('order', 'desc') == 'desc' else 'ASC'
    search  = request.args.get('q')

    where, args = ['1=1'], []
    if status: where.append('status = %s'); args.append(status)
    if search:
        # name_ja / name_ko / model_id / buyma_product_id
        where.append('(name_ja LIKE %s OR name_ko LIKE %s OR model_id LIKE %s OR buyma_product_id LIKE %s)')
        like = f'%{search}%'; args.extend([like, like, like, like])

    sql_count = f"SELECT COUNT(*) c FROM product_summary WHERE {' AND '.join(where)}"
    sql_page  = f"""SELECT * FROM product_summary
                    WHERE {' AND '.join(where)}
                    ORDER BY {sort} {order}
                    LIMIT %s OFFSET %s"""
    ...
```

검색에 `LIKE '%X%'` prefix wildcard는 인덱스 못 탐. 검색 빈도가 높다면 FULLTEXT 인덱스 또는 별도 search column 고려.

### 6.5 클라이언트 변경

products.html에서:
- 페이지/정렬/필터/검색 바뀔 때마다 fetch 호출 (현재는 메모리만)
- 응답 받기 전 로딩 스피너 표시
- 캐시 전략 추가 (같은 페이지 다시 가면 캐시에서 보기)

이건 UX가 현재보다 후퇴함 (현재는 0초). 그래서 응답 50~200ms 이내로 보장되어야 함 → product_summary 테이블의 인덱스가 핵심.

### 6.6 작업 분량 추정

| 작업 | 예상 |
|---|---|
| product_summary 스키마 + 마이그레이션 | 0.5일 |
| build_product_summary.py (집계 잡) | 0.5일 |
| 페이징 엔드포인트 + 인덱스 튜닝 | 1일 |
| 클라이언트 fetch 재작성 + UX (로딩/캐시) | 0.5일 |
| 정합성 검증 (status 판정이 옵션 A와 일치하는지) | 0.5일 |
| **합계** | **3일** |

### 6.7 전환 시 안전장치

- **옵션 A 구조를 한동안 유지**. `?engine=summary` 같은 플래그로 새 엔진을 점진 활성화
- 두 엔진을 한 화면에서 비교하는 디버그 페이지 만들기 (status 판정 차이 즉시 확인)
- product_summary 갱신 잡이 실패하면 옵션 A 캐시로 자동 폴백

---

## 7. 부록

### 7.1 측정 도구 (`_measure_products.py`)

- 위치: 프로젝트 루트 (`/buyma/_measure_products.py`)
- 상태: untracked (commit 안 함)
- 목적: read-only DB 측정. 다음에 raw 규모 늘었을 때 같은 진단 반복용
- 실행: `python _measure_products.py`
- 출력: 테이블 건수 + 단계별 시간 + EXPLAIN + 풀스캔 vs IN-chunk 비교

만약 다시 504가 나거나 응답이 갑자기 느려지면 이 스크립트부터 돌려서 어느 단계가 늦어졌는지 확인.

### 7.2 트러블슈팅 체크리스트

| 증상 | 1차 확인 |
|---|---|
| `data.json` 503 응답 | EC2 systemd 로그에 `[cache] build done` 찍히는지. 첫 부팅 1~2분 정상 |
| `data.json` 504 응답 | 캐시 빌드가 실패 중일 가능성. `[cache] build failed` 트레이스백 확인. DB 연결/쿼리 변경 의심 |
| 데이터가 stale | 마지막 `[cache] build done` 로그 시간 확인. 5분 안 지났으면 정상 |
| 응답이 갑자기 1분 걸림 | Content-Encoding 헤더에 gzip이 빠졌나? products_cache의 gz_bytes가 None인지 확인. 사이트 nginx에서 헤더 떼고 있는지 의심 |
| 페이지 자체가 안 열림 (HTML 못 받음) | systemd 서비스 자체가 죽은 상태. `systemctl status buyma-manage` |
| build_payload 시간이 갑자기 2배 | raw가 늘었는지, 인덱스가 깨졌는지(특히 idx_model_id). `_measure_products.py`로 EXPLAIN 재확인 |

### 7.3 관련 파일 인덱스

| 파일 | 역할 |
|---|---|
| `manage_server/app.py` | Flask 앱. 엔드포인트 정의. 부팅 시 캐시 워밍업 시작 |
| `manage_server/products_api.py` | DB 조회 함수 (`build_payload`, `get_sources`, `get_images`) |
| `manage_server/products_cache.py` | 백그라운드 캐시 (JSON + gzip bytes 보관) |
| `buyma_stats/products.html` | 단일 페이지 앱. fetch + 클라이언트 렌더링 |
| `buyma_stats/products.css` | 스타일 |
| `.github/workflows/deploy-ec2.yml` | main push → EC2 자동 배포 |
| `/etc/systemd/system/buyma-manage.service` (EC2) | 서비스 정의 |
| `_measure_products.py` (untracked) | 성능 측정 도구 |

### 7.4 인덱스 현황 (2026-05-14)

```
raw_scraped_data
  PRIMARY (id)
  uk_source_product (source_site, mall_product_id) UNIQUE
  uk_source_mall_product (source_site, mall_product_id) UNIQUE  ← 중복인 듯, 정리 검토
  idx_brand_site (brand_name_en, source_site)
  idx_mall_product_id (mall_product_id)
  idx_model_id (model_id)   ← build_payload step1에서 사용

ace_products
  PRIMARY (id)
  uk_raw_data_id, uk_reference_number
  idx_model_no (model_no)   ← step2에서 사용
  idx_published_active, idx_active_published_model 등 다수
  (체크인 필요 항목: 정렬용 idx_margin / idx_access 등은 ace_products엔 없음
   → 옵션 B 가면 product_summary에 추가)

ace_product_images
  PRIMARY (id)
  uk_ace_product_position (ace_product_id, position) UNIQUE  ← step3 GROUP BY가 이걸 탐
  idx_is_uploaded

buyma_product_stats
  PRIMARY (buyma_product_id)
  idx_ace (ace_product_id)
  idx_favorite, idx_access
```

### 7.5 오늘의 의사결정 로그 (왜 이렇게 갔는지)

1. **사용자가 "함부로 수정/배포 X" 강조** → 코드 작성 전 측정 먼저, 변경 안은 보여주고 OK 받은 후 적용
2. **A vs B 비교 후 A 선택** → 504를 즉시 풀어야 + A의 풀스캔 로직이 B에 재활용 가능
3. **풀스캔이 IN-chunk보다 빠른 이유** → 124k model_id IN 조회는 random seek가 누적. 인덱스 풀스캔(연속 I/O)이 6~3.5배 빠름
4. **dict 캐시 대신 JSON+gzip bytes 캐시** → jsonify 비용 (5~10초)을 매 요청에서 백그라운드 1회로 이동
5. **gzip이 압축률 8.5% 나오는 이유** → JSON은 키 이름이 반복(items 12만 개의 동일 키), 한국어/일본어/영어 패턴도 비슷해 매우 잘 압축됨
6. **build_cache.py 호출 라인 제거** → 122fec2에서 파일 자체가 삭제되었는데 워크플로우는 그대로 호출 시도, 매 배포마다 WARN. 캐시 로직이 코드 안으로 들어왔으므로 이 라인 자체가 불필요
