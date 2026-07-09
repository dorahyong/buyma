# BUYMA Market Monitor — 운영 참고문서 (Handover)

BUYMA(일본 구매대행 플랫폼)의 **한국 셀러**를 추적하는 크롤링/모니터링 파이프라인입니다.
셀러 목록·주문 실적·등록 상품(가격/조회/찜/문의 시계열)을 SQLite에 축적하고,
상품 "가치"에 따라 크롤링 자원을 차등 배분합니다.

이 문서 하나로 서버 운영 담당자가 프로그램 전반을 이해하고 구동할 수 있도록 작성했습니다.

---

## 1. 무엇을 하는가 — 3가지 수집 활동

| # | 활동 | 하는 일 | 주기 |
|---|---|---|---|
| ① | **셀러 수집** | BUYMA 목록에서 한국 셀러 발견·정보 갱신 | 하루 1회 |
| ② | **주문 실적 수집** | 셀러별 판매(주문) 이력 증분 수집 | 하루 1회 |
| ③ | **상품 수집** | 등록 상품 발견 + 상세(가격/조회/찜/문의) 시계열 축적 | 24시간 상시 |

③은 다시 두 부분:
- **③a 목록 스캔**: 셀러 출품목록을 훑어 **신규 상품 발견 + 가격/품절 변화** 포착
- **③b 상세 재방문**: 각 상품 상세를 주기적으로 재방문해 **조회수·찜·문의 시계열(velocity)** 축적 → "최근 주목받는(급등) 상품" 탐지

**핵심 설계 원칙**: 인기(가치) 높은 셀러/상품은 자주, 낮은 건 드물게 관측하여 크롤링 자원을 가치에 비례 배분한다. (①②는 전수 수집, 가치 우선순위는 ③에만 적용)

---

## 2. 아키텍처

**단일 데몬 `orchestrator.py`** 가 위 4개 활동(①②③a③b)을 하나의 프로세스에서 순차 실행합니다.

```
24시간 사이클 반복:
  ① 셀러 수집     (시간 cap)  ┐
  ② 주문 수집     (시간 cap)  ├─ 매일 배치 (순차 — 같은 IP라 병렬 대신 순차로 차단 위험↓)
  ③a 목록 스캔    (시간 cap)  ┘   ← 가치 높은 셀러 우선, due 셀러만
  ③b 상세 재방문  (남는 시간 전부, --loop)   ← 가치 티어별 주기로 순환
  (24h 경과 시 사이클 반복)
```

- **예산(budget) 배분**: 배치(①②③a)는 각각 시간 상한(cap)을 두고, **남는 시간 전부를 ③b 재방문**이 채움. 배치가 빨리 끝날수록 ③b에 더 많은 시간이 감.
- **차단 대응**: 어느 활동이든 IP 차단(403/429) 감지 시 **전체 일시정지 → 쿨다운 → 중단 지점부터 자동 재개**. 모든 작업이 resumable이라 무손실.
- **정지**: `Ctrl+C`(SIGINT) → 진행 중 작업 마치고 graceful 종료. 재실행 시 이어서 진행.
- **가치 티어**: 상품/셀러를 관측값(조회·찜·문의 velocity + 최근 주문)의 **순위(백분위)** 로 HIGH/MID/LOW(상품은 HOT/WARM/COLD)로 분류 → 티어별 관측 주기 차등.

`orchestrator.py`는 기존 모듈을 재사용합니다:
- ①② → `main.py` 의 `run_crawl_sellers` / `run_crawl_orders` (Playwright 기반)
- ③a → `crawler/value_scan.py::run_value_scan` (가치 우선순위 셀러 선정 → `crawler/page_scan.py` 페이지 단위 스캔)
- ③b → `crawler/revisit.py::run_revisit(loop=True)` (아이템 단위 글로벌 큐)

---

## 3. 요구사항 & 설치

**요구사항**
- Python **3.14** (3.11+ 권장, 개발/검증은 3.14.2)
- 디스크: DB가 상품 130만+ 기준 **약 14GB**이며 시계열 누적으로 계속 증가 → 여유 있게 **50GB+** 권장
- 아웃바운드 인터넷 (BUYMA 접속). 차단 회피를 위해 안정적 IP 권장.

**설치**
```bash
cd buyma-market-monitor
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium          # ①셀러·②주문이 Playwright(브라우저) 사용 — 필수
```
> ②/① 단계는 headless 브라우저(Chromium)를 씁니다. `playwright install`을 빼먹으면 ①②가 실패합니다. ③(상품)은 httpx(HTTP/2)만 사용.

**동작 확인**
```bash
PYTHONPATH="$PWD" .venv/bin/python3 -m pytest -q     # 169 tests 통과해야 정상
```

---

## 4. 초기 데이터 준비 — 빈 상태 부트스트랩

이 패키지는 **데이터 없이 빈 상태에서 새로 시작**하는 것을 전제로 합니다.
DB(`data/items.db`, SQLite)와 스키마는 **첫 실행 시 자동 생성**되므로, 별도 데이터 준비 없이 아래 순서대로 실행하면 됩니다.

```bash
# 1) 셀러 먼저 수집 (이후 활동들이 셀러 목록에 의존 — 반드시 먼저)
PYTHONPATH="$PWD" .venv/bin/python3 main.py crawl-sellers

# 2) 주문 초기 수집 (선택 — 오케스트레이터가 매 사이클 자동 수집하므로 생략 가능)
PYTHONPATH="$PWD" .venv/bin/python3 main.py crawl-orders

# 3) 오케스트레이터 가동 (이후 상품 발견/재방문/주문/셀러를 자동 순환) — 5장 참고
PYTHONPATH="$PWD" .venv/bin/python3 orchestrator.py --workers 8 --sleep 0.3
```

**첫 구동 시 예상 소요/성장**
- ①셀러 ~수 분, ②주문 초기 수집은 판매량에 따라 수십 분~수 시간.
- ③상품 **전수 발견·enrich에는 수 일**이 걸립니다(상품 100만+ 규모). 오케스트레이터를 상시 켜두면 가치 우선순위로 인기 상품부터 채워지고, 며칠에 걸쳐 전체가 커버됩니다(9장 참고).
- DB는 시계열 누적으로 성장 → **디스크 50GB+ 확보 권장**.

> 참고: 만약 이미 수집된 기존 `data/items.db`가 있다면 그 파일을 `data/`에 복사하는 것만으로 이어서 운영할 수도 있습니다(부트스트랩 불필요). 기본 전제는 빈 상태 시작입니다.

---

## 5. 실행 방법

**통합 데몬 (기본 운영 방식)**
```bash
cd buyma-market-monitor
PYTHONPATH="$PWD" .venv/bin/python3 orchestrator.py --workers 8 --sleep 0.3
```
- 백그라운드 상시 실행 권장 (예: `nohup ... &`, `tmux`, 또는 systemd/launchd — 6장 참고).
- `Ctrl+C` 로 graceful 정지, 재실행 시 이어서 진행.

**개별 실행 (디버그/수동 운영용)**
```bash
python main.py crawl-sellers          # ① 셀러만
python main.py crawl-orders           # ② 주문만
python scan_cli.py --value            # ③a 가치 우선순위 목록 스캔만
python revisit_cli.py run --loop      # ③b 재방문만 (상시)
```

**리포트 — 급등 상품 조회**
```bash
python revisit_cli.py report --top 30
```
→ velocity(찜/일) 상위 급등 상품 + 티어 커버리지 요약 출력.

**분석 — 인기 기준/분포 (일회성 분석 도구)**
```bash
python scripts/analyze_popularity.py
```

---

## 6. 설정 (orchestrator.py 주요 옵션)

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `--cycle-hours` | 24 | 한 사이클 길이 |
| `--sellers-cap-hours` | 1 | ① 시간 상한 (실측 ~7분이라 여유 큼) |
| `--orders-cap-hours` | 3 | ② 시간 상한 (실측 ~14분) |
| `--scan-cap-hours` | 6 | ③a 시간 상한 (첫 전수 사이클은 이 cap 도달) |
| `--cooldown-minutes` | 45 | IP 차단 시 쿨다운 대기 |
| `--idle-minutes` | 10 | ③b가 due 없을 때 대기 간격 |
| `--workers` | 6 | 병렬 워커 수 (③ 대상; 권장 8) |
| `--sleep` | 0.3 | 요청당 대기(초) — 낮추면 빠르지만 차단 위험↑ |
| `--cb-threshold` | 5 | 차단 판정 임계(윈도 내 403/429 누적) |
| `--cb-window-seconds` | 60 | 차단 판정 윈도 |

**가치 티어 주기 (코드 상수, 필요 시 조정)**
- ③b 상세 재방문: `crawler/revisit_scheduler.py::SELLER_SCAN_INTERVAL_DAYS` 아님 → `revisit_scheduler`의 티어 간격 **HOT 1일 / WARM 4일 / COLD 30일**
- ③a 목록 스캔: `crawler/scan_scheduler.py::SELLER_SCAN_INTERVAL_DAYS` = **HIGH 1일 / MID 4일 / LOW 21일**, 티어 비율 `TIER_HIGH_FRAC=0.15 / TIER_MID_FRAC=0.50` (상위 15% HIGH, 다음 35% MID, 나머지 LOW)

---

## 7. 데이터 모델 (SQLite `data/items.db`, 11개 테이블)

| 테이블 | 내용 |
|---|---|
| `sellers` | ① 셀러 정보 (id, 이름, 팔로워/출품/주문수 등) |
| `orders` | ② 주문(판매) 이력 |
| `order_watermarks` | ② 셀러별 증분 수집 워터마크 |
| `order_run_meta` | ② 마지막 주문 수집 실행 메타 |
| `items` | ③ 상품 마스터 (가격/브랜드/카테고리/상태/조회·찜·문의 등) |
| `item_images` | ③ 상품별 전체 이미지 |
| `item_variants` | ③ 색/사이즈/가격/재고 변형 |
| `stats_history` | ③ 조회/찜/문의 **시계열** (관측마다 1행) |
| `price_history` | ③ 가격 변동 이력 |
| `revisit_state` | ③b 상품별 재방문 스케줄·티어·velocity |
| `seller_scan_state` | ③a 셀러별 목록스캔 스케줄·가치 티어 |

- `status`: ACTIVE / SOLD_OUT / DELETED. 삭제는 재방문 시 상세 404로 판정.
- 스키마 버전은 `PRAGMA user_version` (현재 6). 스키마는 `storage/db.py` 단일 파일에 정의, 첫 연결 시 자동 생성(CREATE TABLE IF NOT EXISTS).

---

## 8. 운영 가이드

**상시 구동 (예: systemd)** — `/etc/systemd/system/buyma-monitor.service`
```ini
[Unit]
Description=BUYMA Market Monitor
After=network-online.target

[Service]
WorkingDirectory=/opt/buyma-market-monitor
Environment=PYTHONPATH=/opt/buyma-market-monitor
ExecStart=/opt/buyma-market-monitor/.venv/bin/python3 orchestrator.py --workers 8 --sleep 0.3
Restart=on-failure
KillSignal=SIGINT       # graceful 정지
TimeoutStopSec=600

[Install]
WantedBy=multi-user.target
```
```bash
systemctl enable --now buyma-monitor
journalctl -u buyma-monitor -f      # 로그 실시간
```

**정지/재개**: SIGINT(=systemd stop)로 graceful 종료. 모든 작업 resumable → 재시작하면 중단 지점부터 이어감(무손실).

**IP 차단 시**: 데몬이 자동으로 쿨다운 후 재개합니다. 반복 차단되면 로그에 `Circuit breaker tripped` 경고 → IP 교체/네트워크 점검 후 재가동.

**백업**: `data/items.db` 가 전 자산입니다. 정기 백업 권장 (WAL 모드이므로 정지 상태에서 복사하거나 `sqlite3 items.db ".backup ..."` 사용). ⚠️ 파일이 14GB+ 임에 유의.

**로그**: 표준출력(systemd면 journald). 에러 상세는 `data/errors.log`(jsonl).

**디스크**: DB는 시계열 누적으로 계속 성장. 모니터링 권장. (COLD 상품 시계열이 대부분의 증가분 — 필요 시 오래된 `stats_history` 정리 정책 추가 검토)

---

## 9. 커버리지 특성 (예측)

가치 우선순위로 인해 각 상품/셀러의 조회 주기가 다릅니다.

**③b 상세 재방문 (상품별):** 처리능력 여유 3~5배로 아래 주기가 안정적으로 유지됨.
- 🔥 HOT(~4%): **매일** / 🌤 WARM(~12%): **4일** / ❄️ COLD(~84%): **30일**
- → 전체 상품이 **최대 30일 이내 1회 이상** 갱신. 인기 상품은 30배 자주.
- COLD 상품이 급등하면 최대 30일 지연 후 포착 → 다음 관측 시 티어 자동 승급.

**③a 목록 스캔 (셀러별):** HIGH 매일 / MID 4일 / LOW 21일.
- 신규 상품/가격 변화: HIGH 셀러는 하루, LOW 셀러는 최대 21일 내 포착.

---

## 10. 튜닝 / 알려진 한계

- **①② cap 과대**: 실측 ①~7분·②~14분인데 기본 cap 1h/3h. 조여도 무방(완주 후 남는 시간은 ③b로 감).
- **③a 첫 사이클은 cap(6h) 도달**: 전 셀러가 due라 전수 스캔에 6h 소요, 나머지는 다음 사이클 이월. 이후 사이클은 due 셀러만이라 가벼움.
- **느린 목록 페이지(hang) 대비**: ③a fetch는 짧은 타임아웃(read 15s, 재시도 1)로 바운드 → 매달리는 페이지는 즉시 실패 후 다음 사이클 재시도(콘텐츠 손실 없음).
- **개선 여지 (미구현)**: ③a가 목록 전체를 재수집하는 비효율 → "앞 페이지만 신규 발견 + 가격/삭제는 ③b가 커버"로 최적화 가능. `docs/`의 설계 문서 및 아래 참고.

---

## 11. 테스트
```bash
PYTHONPATH="$PWD" .venv/bin/python3 -m pytest -q      # 169 tests
```
순수 로직(티어/velocity/urgency)·리포지토리·오케스트레이션 통합을 커버. 네트워크 없이 가짜 client로 검증.

---

## 12. 파일 구조

```
buyma-market-monitor/
├── README.md              ← 이 문서
├── requirements.txt
├── orchestrator.py        ← 통합 데몬 (메인 진입점)
├── main.py                ← ① crawl-sellers / ② crawl-orders
├── scan_cli.py            ← ③a 수동 실행
├── revisit_cli.py         ← ③b 수동 실행 + report(급등 리포트)
├── crawler/               ← 크롤링·스케줄링 로직
│   ├── client.py            HTTP/Playwright 클라이언트 + 재시도
│   ├── circuit_breaker.py   차단 감지
│   ├── listing.py / seller.py / pagination.py   ① 셀러
│   ├── orders.py            ② 주문
│   ├── seller_items.py / page_scan.py           ③a 목록 스캔
│   ├── item_detail.py / item_status.py          ③ 상세 파싱/상태
│   ├── monitor.py           ③ 공유 헬퍼(enrich/reconcile/classify)
│   ├── revisit.py / revisit_scheduler.py        ③b 재방문
│   ├── value_scan.py / scan_scheduler.py        ③a 가치 우선순위
├── storage/               ← SQLite 접근 계층
│   ├── db.py                스키마(단일 정의) + 연결
│   └── *_repo.py, store.py
├── scripts/analyze_popularity.py   인기 기준 분석 도구
├── tests/                 ← pytest (169개)
├── docs/superpowers/      ← 설계 spec/plan (심화 참고: 결정 배경·근거)
└── data/                  ← SQLite DB 위치 (items.db — 별도 이전/생성)
```

**심화 참고**: `docs/superpowers/specs/` 와 `plans/` 에 각 기능의 설계 배경·의사결정·근거가 시간순으로 남아 있습니다. (예: 가치 우선순위 스캔, 적응형 재방문 스케줄러, 통합 오케스트레이터)

---

*문의/인수인계 시 이 문서와 `docs/`의 설계 문서를 함께 참고하세요.*
