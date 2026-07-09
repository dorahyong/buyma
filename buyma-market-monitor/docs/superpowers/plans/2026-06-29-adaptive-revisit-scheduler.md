# 적응형 관측 스케줄러 (Project 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 인기 절대 3-티어를 시드로, 상품별 상세 재방문을 시간 예산 기반 우선순위로 차등 실행해 view/fav/inquiry 시계열을 누적하고 velocity(일평균 증가율)로 급등 상품을 탐지한다.

**Architecture:** 재방문 전용 `revisit_cli.py`를 신설(기존 monitor는 신규 발견 담당 유지). 한 실행은 0단계(미시드 상품 일괄 backfill, fetch 없음) → 1순위(미관측 stranded 첫 fetch) → 2순위(`next_revisit_at` 빠른 순 재방문) 순으로, `--max-hours` 시간 예산 안에서 워커 풀이 처리한다. 순수 스케줄링 로직(`crawler/revisit_scheduler.py`)과 DB 접근(`storage/revisit_repo.py`), 오케스트레이션(`crawler/revisit.py`)을 분리한다.

**Tech Stack:** Python 3.14, SQLite(WAL), httpx, BeautifulSoup4, pytest. 기존 `crawler/monitor.py`·`storage/items_repo.py` 패턴 재사용.

---

## 배경 (구현자가 알아야 할 도메인 사실)

- DB는 `data/items.db`. 스키마는 `storage/db.py`의 `_DDL`(전부 `CREATE TABLE IF NOT EXISTS`)로 관리. **컬럼 ALTER 로직이 없으므로** 기존 테이블을 바꾸지 말고 새 테이블만 추가한다.
- 시각 문자열은 전부 `storage/store.py`의 `now_iso()` = KST(`+09:00`) ISO, 초 단위(`2026-06-29T15:04:05+09:00`). 모두 같은 타임존이라 **문자열 사전식 비교 = 시간순 비교**가 성립한다. Δ시간은 `datetime.fromisoformat()`으로 파싱해 계산한다.
- `crawler/item_detail.py::parse_item_detail(html)`은 `view_count`/`fav_count`/`inquiry_count`(int|None) 등을 담은 dict를 반환한다.
- `crawler/monitor.py::apply_enrich(conn, item_id, html, now)`는 상세를 파싱해 `update_detail_fields` + `replace_item_images` + `replace_item_variants` + `record_stats_observation`을 한 트랜잭션(BEGIN/COMMIT/ROLLBACK)으로 수행한다. 재방문도 동일 작업이므로 그대로 재사용한다.
- `crawler/monitor.py`의 `fetch_for_enrich(client,item_id,on_error)`, `fetch_for_classification(client,item_id,on_error)`, `apply_classification(conn,item_id,status_code,now)`, `build_item_detail_url`도 재사용한다.
- `crawler/circuit_breaker.py::CircuitBreaker`, `crawler/client.py::HttpClient`는 monitor와 동일하게 쓴다.
- 인기 절대 3-티어 (확정): HOT `fav≥50 OR view≥2000 OR inquiry≥5`, WARM `fav≥10 OR view≥500 OR inquiry≥1`, COLD 나머지.

## File Structure

| 파일 | 책임 |
|---|---|
| `storage/db.py` (수정) | `revisit_state` 테이블 DDL 추가, `SCHEMA_VERSION` 4로 |
| `crawler/revisit_scheduler.py` (신규) | 순수 로직: 티어 판정, 간격/다음시각, velocity, 급등 승급 |
| `storage/revisit_repo.py` (신규) | `revisit_state` CRUD, 시드 backfill, 큐/미관측 조회, 최근 2관측 조회, 리포트 쿼리 |
| `crawler/revisit.py` (신규) | 오케스트레이션: `apply_revisit`(관측 1건 반영), `run_revisit`(워커 풀 + 시간예산) |
| `revisit_cli.py` (신규) | 진입점: `run`/`report` 서브커맨드 |
| `tests/test_revisit_scheduler.py` (신규) | 순수 로직 테스트 |
| `tests/test_revisit_repo.py` (신규) | DB 함수 테스트 (인메모리 SQLite) |
| `tests/test_revisit.py` (신규) | 오케스트레이션 통합 테스트 (가짜 client) |

---

## Task 1: revisit_state 스키마 추가

**Files:**
- Modify: `storage/db.py`
- Test: `tests/test_revisit_repo.py`

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_repo.py` 생성:

```python
import sqlite3
from storage.db import connect, init_schema


def _mem():
    conn = connect(":memory:")
    init_schema(conn)
    return conn


def test_revisit_state_table_exists():
    conn = _mem()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(revisit_state)")}
    assert cols == {
        "item_id", "tier", "base_tier", "last_observed_at",
        "next_revisit_at", "obs_count", "last_velocity",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_repo.py::test_revisit_state_table_exists -v`
Expected: FAIL (no such table: revisit_state)

- [ ] **Step 3: Implement — add DDL**

`storage/db.py`에서 `SCHEMA_VERSION = 3`을 `SCHEMA_VERSION = 4`로 바꾸고, `_DDL` 문자열 끝(닫는 `"""` 직전)에 추가:

```sql

CREATE TABLE IF NOT EXISTS revisit_state (
  item_id          TEXT PRIMARY KEY,
  tier             TEXT,
  base_tier        TEXT,
  last_observed_at TEXT,
  next_revisit_at  TEXT,
  obs_count        INTEGER,
  last_velocity    REAL
);
CREATE INDEX IF NOT EXISTS idx_revisit_next ON revisit_state(next_revisit_at);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_repo.py::test_revisit_state_table_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add storage/db.py tests/test_revisit_repo.py
git commit -m "feat(revisit): add revisit_state table (schema v4)"
```

---

## Task 2: 절대 티어 판정 (classify_tier)

**Files:**
- Create: `crawler/revisit_scheduler.py`
- Test: `tests/test_revisit_scheduler.py`

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_scheduler.py` 생성:

```python
from crawler.revisit_scheduler import classify_tier


def test_classify_tier_hot_by_each_signal():
    assert classify_tier(view=0, fav=50, inquiry=0) == "HOT"
    assert classify_tier(view=2000, fav=0, inquiry=0) == "HOT"
    assert classify_tier(view=0, fav=0, inquiry=5) == "HOT"


def test_classify_tier_warm_boundaries():
    assert classify_tier(view=500, fav=0, inquiry=0) == "WARM"
    assert classify_tier(view=0, fav=10, inquiry=0) == "WARM"
    assert classify_tier(view=0, fav=0, inquiry=1) == "WARM"


def test_classify_tier_cold_below_all():
    assert classify_tier(view=499, fav=9, inquiry=0) == "COLD"


def test_classify_tier_handles_none_as_zero():
    assert classify_tier(view=None, fav=None, inquiry=None) == "COLD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py -v`
Expected: FAIL (ModuleNotFoundError: crawler.revisit_scheduler)

- [ ] **Step 3: Implement**

`crawler/revisit_scheduler.py` 생성:

```python
"""Pure scheduling logic for adaptive revisits — no I/O, no DB.

Tier thresholds are the confirmed absolute popularity 3-tier (see
docs/superpowers/specs/2026-06-29-adaptive-revisit-scheduler-design.md).
"""


def classify_tier(view: int | None, fav: int | None, inquiry: int | None) -> str:
    """Absolute popularity tier from current cumulative metrics."""
    v = view or 0
    f = fav or 0
    q = inquiry or 0
    if f >= 50 or v >= 2000 or q >= 5:
        return "HOT"
    if f >= 10 or v >= 500 or q >= 1:
        return "WARM"
    return "COLD"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add crawler/revisit_scheduler.py tests/test_revisit_scheduler.py
git commit -m "feat(revisit): classify_tier absolute 3-tier"
```

---

## Task 3: 티어 간격 + 다음 재방문 시각 (next_revisit_at_from)

**Files:**
- Modify: `crawler/revisit_scheduler.py`
- Test: `tests/test_revisit_scheduler.py`

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_scheduler.py`에 추가 (기대값은 KST 유지 + 날짜 덧셈으로 검산 완료):

```python
from crawler.revisit_scheduler import TIER_INTERVAL_DAYS, next_revisit_at_from


def test_tier_intervals():
    assert TIER_INTERVAL_DAYS == {"HOT": 1, "WARM": 4, "COLD": 30}


def test_next_revisit_at_adds_interval_keeping_kst():
    # HOT +1d: 06-29 -> 06-30
    assert next_revisit_at_from("2026-06-29T15:00:00+09:00", "HOT") \
        == "2026-06-30T15:00:00+09:00"
    # WARM +4d: 06-29 -> 07-03
    assert next_revisit_at_from("2026-06-29T15:00:00+09:00", "WARM") \
        == "2026-07-03T15:00:00+09:00"
    # COLD +30d: 06-01 -> 07-01
    assert next_revisit_at_from("2026-06-01T00:00:00+09:00", "COLD") \
        == "2026-07-01T00:00:00+09:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py::test_next_revisit_at_adds_interval_keeping_kst -v`
Expected: FAIL (ImportError: next_revisit_at_from)

- [ ] **Step 3: Implement**

`crawler/revisit_scheduler.py` 상단 import에 추가하고 함수 추가:

```python
from datetime import datetime, timedelta

TIER_INTERVAL_DAYS = {"HOT": 1, "WARM": 4, "COLD": 30}


def next_revisit_at_from(observed_at: str, tier: str) -> str:
    """observed_at(ISO, KST) + 티어 목표간격을 더한 ISO 문자열."""
    dt = datetime.fromisoformat(observed_at)
    dt2 = dt + timedelta(days=TIER_INTERVAL_DAYS[tier])
    return dt2.isoformat()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crawler/revisit_scheduler.py tests/test_revisit_scheduler.py
git commit -m "feat(revisit): tier intervals + next_revisit_at_from"
```

---

## Task 4: velocity 산출 (compute_velocity)

**Files:**
- Modify: `crawler/revisit_scheduler.py`
- Test: `tests/test_revisit_scheduler.py`

velocity는 "최근 2개 관측"의 (Δ값 / Δ일)이다. 입력은 `[(observed_at, view, fav, inquiry), ...]`를 **최신순(DESC)** 으로 받는다(repo가 그 순서로 준다). 관측이 1개뿐이거나 Δ일=0이면 `None`.

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_scheduler.py`에 추가:

```python
from crawler.revisit_scheduler import compute_velocity


def test_velocity_none_when_single_observation():
    obs = [("2026-06-29T00:00:00+09:00", 100, 10, 1)]
    assert compute_velocity(obs) == {"fav_velocity": None, "view_velocity": None}


def test_velocity_per_day_from_two_observations():
    # newest first: 2 days apart, fav 10->20 (+10), view 100->300 (+200)
    obs = [
        ("2026-06-29T00:00:00+09:00", 300, 20, 5),
        ("2026-06-27T00:00:00+09:00", 100, 10, 1),
    ]
    v = compute_velocity(obs)
    assert v["fav_velocity"] == 5.0      # 10 / 2 days
    assert v["view_velocity"] == 100.0   # 200 / 2 days


def test_velocity_none_when_same_timestamp():
    obs = [
        ("2026-06-29T00:00:00+09:00", 300, 20, 5),
        ("2026-06-29T00:00:00+09:00", 100, 10, 1),
    ]
    assert compute_velocity(obs) == {"fav_velocity": None, "view_velocity": None}


def test_velocity_treats_none_counts_as_zero():
    obs = [
        ("2026-06-29T00:00:00+09:00", None, 20, 5),
        ("2026-06-28T00:00:00+09:00", 100, None, 1),
    ]
    v = compute_velocity(obs)
    assert v["fav_velocity"] == 20.0     # (20-0)/1
    assert v["view_velocity"] == -100.0  # (0-100)/1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py::test_velocity_per_day_from_two_observations -v`
Expected: FAIL (ImportError: compute_velocity)

- [ ] **Step 3: Implement**

`crawler/revisit_scheduler.py`에 추가:

```python
def compute_velocity(observations: list[tuple]) -> dict:
    """observations: 최신순 [(observed_at, view, fav, inquiry), ...].

    최근 2개로 일평균 증가율을 계산. 관측 1개 이하 또는 Δ일=0이면 None.
    """
    none = {"fav_velocity": None, "view_velocity": None}
    if len(observations) < 2:
        return none
    (t_new, v_new, f_new, _q_new) = observations[0]
    (t_old, v_old, f_old, _q_old) = observations[1]
    days = (datetime.fromisoformat(t_new) - datetime.fromisoformat(t_old)).total_seconds() / 86400.0
    if days <= 0:
        return none
    return {
        "fav_velocity": ((f_new or 0) - (f_old or 0)) / days,
        "view_velocity": ((v_new or 0) - (v_old or 0)) / days,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crawler/revisit_scheduler.py tests/test_revisit_scheduler.py
git commit -m "feat(revisit): compute_velocity (per-day, 2-obs)"
```

---

## Task 5: 급등 일시 승급 (apply_promotion)

**Files:**
- Modify: `crawler/revisit_scheduler.py`
- Test: `tests/test_revisit_scheduler.py`

급등(`fav_velocity ≥ 5/일` 또는 `view_velocity ≥ 100/일`)이면 base_tier를 한 단계 승급. COLD→WARM→HOT, HOT은 상한.

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_scheduler.py`에 추가:

```python
from crawler.revisit_scheduler import apply_promotion


def test_promotion_bumps_one_step_on_fav_surge():
    assert apply_promotion("COLD", fav_velocity=5.0, view_velocity=0.0) == "WARM"
    assert apply_promotion("WARM", fav_velocity=5.0, view_velocity=0.0) == "HOT"


def test_promotion_bumps_on_view_surge():
    assert apply_promotion("COLD", fav_velocity=0.0, view_velocity=100.0) == "WARM"


def test_promotion_hot_is_capped():
    assert apply_promotion("HOT", fav_velocity=999.0, view_velocity=999.0) == "HOT"


def test_no_promotion_below_threshold_or_none():
    assert apply_promotion("COLD", fav_velocity=4.9, view_velocity=99.0) == "COLD"
    assert apply_promotion("WARM", fav_velocity=None, view_velocity=None) == "WARM"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py::test_promotion_bumps_one_step_on_fav_surge -v`
Expected: FAIL (ImportError: apply_promotion)

- [ ] **Step 3: Implement**

`crawler/revisit_scheduler.py`에 추가:

```python
SURGE_FAV_PER_DAY = 5.0
SURGE_VIEW_PER_DAY = 100.0
_TIER_ORDER = ["COLD", "WARM", "HOT"]


def apply_promotion(base_tier: str, fav_velocity, view_velocity) -> str:
    """급등이면 base_tier에서 한 단계 승급(COLD→WARM→HOT). 일시적."""
    surge = ((fav_velocity is not None and fav_velocity >= SURGE_FAV_PER_DAY)
             or (view_velocity is not None and view_velocity >= SURGE_VIEW_PER_DAY))
    if not surge:
        return base_tier
    i = _TIER_ORDER.index(base_tier)
    return _TIER_ORDER[min(i + 1, len(_TIER_ORDER) - 1)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crawler/revisit_scheduler.py tests/test_revisit_scheduler.py
git commit -m "feat(revisit): apply_promotion on surge"
```

---

## Task 6: 관측 후 상태 산출 (compute_observation_state)

**Files:**
- Modify: `crawler/revisit_scheduler.py`
- Test: `tests/test_revisit_scheduler.py`

위 함수들을 묶어, "한 번 관측한 결과"로부터 `revisit_state`에 쓸 값들을 계산하는 단일 진입점.

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_scheduler.py`에 추가:

```python
from crawler.revisit_scheduler import compute_observation_state


def test_observation_state_single_obs_uses_base_tier():
    obs = [("2026-06-29T00:00:00+09:00", 600, 0, 0)]  # WARM by view>=500
    st = compute_observation_state(obs, now="2026-06-29T00:00:00+09:00")
    assert st["base_tier"] == "WARM"
    assert st["tier"] == "WARM"               # 관측 1개라 승급 없음
    assert st["last_velocity"] is None
    assert st["next_revisit_at"] == "2026-07-03T00:00:00+09:00"  # +4d
    assert st["last_observed_at"] == "2026-06-29T00:00:00+09:00"


def test_observation_state_promotes_on_surge_and_shortens_interval():
    obs = [
        ("2026-06-29T00:00:00+09:00", 600, 20, 0),  # base WARM (view 600)
        ("2026-06-28T00:00:00+09:00", 0, 0, 0),     # fav +20/day -> surge
    ]
    st = compute_observation_state(obs, now="2026-06-29T00:00:00+09:00")
    assert st["base_tier"] == "WARM"
    assert st["tier"] == "HOT"                  # surge bumps WARM->HOT
    assert st["last_velocity"] == 20.0          # fav_velocity stored
    assert st["next_revisit_at"] == "2026-06-30T00:00:00+09:00"  # HOT +1d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py::test_observation_state_single_obs_uses_base_tier -v`
Expected: FAIL (ImportError: compute_observation_state)

- [ ] **Step 3: Implement**

`crawler/revisit_scheduler.py`에 추가:

```python
def compute_observation_state(observations: list[tuple], now: str) -> dict:
    """관측 직후 revisit_state에 반영할 값 산출.

    observations: 최신순 [(observed_at, view, fav, inquiry), ...] (now 관측 포함).
    """
    newest = observations[0]
    _t, v, f, q = newest
    base_tier = classify_tier(view=v, fav=f, inquiry=q)
    vel = compute_velocity(observations)
    tier = apply_promotion(base_tier, vel["fav_velocity"], vel["view_velocity"])
    return {
        "base_tier": base_tier,
        "tier": tier,
        "last_velocity": vel["fav_velocity"],
        "last_observed_at": now,
        "next_revisit_at": next_revisit_at_from(now, tier),
        "obs_count": len(observations),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crawler/revisit_scheduler.py tests/test_revisit_scheduler.py
git commit -m "feat(revisit): compute_observation_state aggregator"
```

---

## Task 7: revisit_repo — upsert + 최근 2관측 조회

**Files:**
- Create: `storage/revisit_repo.py`
- Test: `tests/test_revisit_repo.py`

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_repo.py`에 추가 (상단 import도 추가):

```python
from storage import revisit_repo
from storage.items_repo import record_stats_observation


def test_upsert_and_get_revisit_state():
    conn = _mem()
    revisit_repo.upsert_revisit_state(
        conn, item_id="1", tier="HOT", base_tier="WARM",
        last_observed_at="2026-06-29T00:00:00+09:00",
        next_revisit_at="2026-06-30T00:00:00+09:00",
        obs_count=2, last_velocity=5.0,
    )
    row = conn.execute("SELECT * FROM revisit_state WHERE item_id='1'").fetchone()
    assert row["tier"] == "HOT"
    assert row["base_tier"] == "WARM"
    assert row["obs_count"] == 2
    assert row["last_velocity"] == 5.0

    # upsert again overwrites
    revisit_repo.upsert_revisit_state(
        conn, item_id="1", tier="COLD", base_tier="COLD",
        last_observed_at="2026-07-01T00:00:00+09:00",
        next_revisit_at="2026-07-31T00:00:00+09:00",
        obs_count=3, last_velocity=None,
    )
    row = conn.execute("SELECT * FROM revisit_state WHERE item_id='1'").fetchone()
    assert row["tier"] == "COLD"
    assert row["obs_count"] == 3
    assert row["last_velocity"] is None


def test_get_recent_two_observations_desc():
    conn = _mem()
    record_stats_observation(conn, "1", 100, 10, 1, "2026-06-27T00:00:00+09:00")
    record_stats_observation(conn, "1", 300, 20, 5, "2026-06-29T00:00:00+09:00")
    record_stats_observation(conn, "1", 50, 5, 0, "2026-06-25T00:00:00+09:00")
    obs = revisit_repo.get_recent_two_observations(conn, "1")
    assert [o[0] for o in obs] == [
        "2026-06-29T00:00:00+09:00", "2026-06-27T00:00:00+09:00",
    ]
    assert obs[0] == ("2026-06-29T00:00:00+09:00", 300, 20, 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_repo.py -v`
Expected: FAIL (ModuleNotFoundError: storage.revisit_repo)

- [ ] **Step 3: Implement**

`storage/revisit_repo.py` 생성:

```python
"""revisit_state CRUD + queue/seed queries for the adaptive revisit scheduler."""
import sqlite3


def upsert_revisit_state(
    conn: sqlite3.Connection,
    item_id: str,
    tier: str,
    base_tier: str,
    last_observed_at: str,
    next_revisit_at: str,
    obs_count: int,
    last_velocity: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO revisit_state
          (item_id, tier, base_tier, last_observed_at, next_revisit_at, obs_count, last_velocity)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
          tier=excluded.tier,
          base_tier=excluded.base_tier,
          last_observed_at=excluded.last_observed_at,
          next_revisit_at=excluded.next_revisit_at,
          obs_count=excluded.obs_count,
          last_velocity=excluded.last_velocity
        """,
        (item_id, tier, base_tier, last_observed_at, next_revisit_at, obs_count, last_velocity),
    )


def get_recent_two_observations(conn: sqlite3.Connection, item_id: str) -> list[tuple]:
    """최신순 최대 2개: [(observed_at, view, fav, inquiry), ...]."""
    rows = conn.execute(
        "SELECT observed_at, view_count, fav_count, inquiry_count "
        "FROM stats_history WHERE item_id = ? ORDER BY observed_at DESC LIMIT 2",
        (item_id,),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_repo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add storage/revisit_repo.py tests/test_revisit_repo.py
git commit -m "feat(revisit): revisit_repo upsert + recent observations"
```

---

## Task 8: revisit_repo — 시드 backfill + 큐/미관측 조회

**Files:**
- Modify: `storage/revisit_repo.py`
- Test: `tests/test_revisit_repo.py`

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_repo.py`에 추가:

```python
def _add_item(conn, item_id, seller="s1", status="ACTIVE",
              view=None, fav=None, inq=None, detail="2026-06-20T00:00:00+09:00"):
    conn.execute(
        "INSERT INTO items (item_id, seller_id, name, status, first_seen_at, "
        "last_seen_at, view_count, fav_count, inquiry_count, detail_fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (item_id, seller, "n", status, "2026-06-01T00:00:00+09:00",
         "2026-06-20T00:00:00+09:00", view, fav, inq, detail),
    )


def test_seed_backfill_registers_enriched_active_only():
    conn = _mem()
    _add_item(conn, "hot", view=3000)                     # HOT
    _add_item(conn, "warm", fav=12)                       # WARM
    _add_item(conn, "cold", view=1)                       # COLD
    _add_item(conn, "stranded", detail=None)              # not enriched -> skip
    _add_item(conn, "dead", status="DELETED", view=9999)  # not active -> skip
    record_stats_observation(conn, "hot", 3000, 0, 0, "2026-06-20T00:00:00+09:00")

    n = revisit_repo.seed_backfill(conn)
    assert n == 3
    seeded = {r[0]: r for r in conn.execute(
        "SELECT item_id, tier, base_tier, last_observed_at, obs_count FROM revisit_state")}
    assert set(seeded) == {"hot", "warm", "cold"}
    assert seeded["hot"][1] == "HOT"
    # last_observed_at falls back to detail_fetched_at when no stats row
    assert seeded["warm"][3] == "2026-06-20T00:00:00+09:00"
    # uses latest stats observed_at when present
    assert seeded["hot"][3] == "2026-06-20T00:00:00+09:00"

    # idempotent: running again seeds nothing new
    assert revisit_repo.seed_backfill(conn) == 0


def test_get_unobserved_active_returns_stranded():
    conn = _mem()
    _add_item(conn, "ok", view=1)
    _add_item(conn, "stranded", detail=None)
    ids = revisit_repo.get_unobserved_active(conn)
    assert ids == ["stranded"]


def test_get_revisit_queue_orders_by_next_revisit_at():
    conn = _mem()
    for iid, nxt in [("a", "2026-07-10"), ("b", "2026-07-01"), ("c", "2026-07-05")]:
        revisit_repo.upsert_revisit_state(
            conn, item_id=iid, tier="COLD", base_tier="COLD",
            last_observed_at="2026-06-01T00:00:00+09:00",
            next_revisit_at=nxt + "T00:00:00+09:00",
            obs_count=1, last_velocity=None,
        )
    assert revisit_repo.get_revisit_queue(conn, limit=2) == ["b", "c"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_repo.py -v`
Expected: FAIL (AttributeError: seed_backfill)

- [ ] **Step 3: Implement**

`storage/revisit_repo.py`에 추가. base_tier는 SQL `CASE`로, last_observed_at은 최신 stats(없으면 detail_fetched_at)로 채운다. next_revisit_at은 시드 단계에서는 last_observed_at과 동일하게 둔다(전부 과거라 즉시 후보가 되며, 정렬은 next_revisit_at ASC = 가장 오래된 순):

```python
def seed_backfill(conn: sqlite3.Connection) -> int:
    """revisit_state에 없는 enriched·ACTIVE 상품을 base_tier로 일괄 등록.

    fetch 없이 기존 items 지표로 티어를 매긴다. last_observed_at은 그 상품의
    최신 stats observed_at(없으면 detail_fetched_at). next_revisit_at은
    last_observed_at과 동일(전부 과거 → 즉시 재방문 후보). 이미 등록된 건 건드리지 않음.
    Returns: 새로 등록된 행 수.
    """
    cur = conn.execute(
        """
        INSERT INTO revisit_state
          (item_id, tier, base_tier, last_observed_at, next_revisit_at, obs_count, last_velocity)
        SELECT
          i.item_id, t.tier, t.tier, t.last_obs, t.last_obs, t.cnt, NULL
        FROM (
          SELECT
            i.item_id,
            CASE
              WHEN COALESCE(i.fav_count,0) >= 50 OR COALESCE(i.view_count,0) >= 2000
                   OR COALESCE(i.inquiry_count,0) >= 5 THEN 'HOT'
              WHEN COALESCE(i.fav_count,0) >= 10 OR COALESCE(i.view_count,0) >= 500
                   OR COALESCE(i.inquiry_count,0) >= 1 THEN 'WARM'
              ELSE 'COLD'
            END AS tier,
            COALESCE(
              (SELECT MAX(s.observed_at) FROM stats_history s WHERE s.item_id = i.item_id),
              i.detail_fetched_at
            ) AS last_obs,
            (SELECT COUNT(*) FROM stats_history s WHERE s.item_id = i.item_id) AS cnt
          FROM items i
          WHERE i.status = 'ACTIVE' AND i.detail_fetched_at IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM revisit_state r WHERE r.item_id = i.item_id)
        ) AS i
        JOIN (SELECT 1) ON 1=1
        """,
        # NOTE: the subquery alias `i` provides item_id/tier/last_obs/cnt; the
        # outer SELECT references them via `i.` (column names match).
    )
    return cur.rowcount


def get_unobserved_active(conn: sqlite3.Connection, limit: int | None = None) -> list[str]:
    """첫 상세 fetch가 필요한 ACTIVE 상품(detail_fetched_at IS NULL)."""
    sql = "SELECT item_id FROM items WHERE status='ACTIVE' AND detail_fetched_at IS NULL"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [r[0] for r in conn.execute(sql)]


def get_revisit_queue(conn: sqlite3.Connection, limit: int) -> list[str]:
    """재방문 후보를 next_revisit_at 빠른(오래된) 순으로. urgency DESC와 동치."""
    return [r[0] for r in conn.execute(
        "SELECT item_id FROM revisit_state ORDER BY next_revisit_at ASC LIMIT ?",
        (int(limit),),
    )]
```

> 구현 주의: 위 `seed_backfill`의 SQL에서 외부 `SELECT i.item_id, t.tier ...`는 서브쿼리 별칭과 충돌하지 않도록, 아래 "정리된 최종 SQL"을 사용한다 (별칭 단순화):

```python
def seed_backfill(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        INSERT INTO revisit_state
          (item_id, tier, base_tier, last_observed_at, next_revisit_at, obs_count, last_velocity)
        SELECT item_id, tier, tier, last_obs, last_obs, cnt, NULL
        FROM (
          SELECT
            i.item_id AS item_id,
            CASE
              WHEN COALESCE(i.fav_count,0) >= 50 OR COALESCE(i.view_count,0) >= 2000
                   OR COALESCE(i.inquiry_count,0) >= 5 THEN 'HOT'
              WHEN COALESCE(i.fav_count,0) >= 10 OR COALESCE(i.view_count,0) >= 500
                   OR COALESCE(i.inquiry_count,0) >= 1 THEN 'WARM'
              ELSE 'COLD'
            END AS tier,
            COALESCE(
              (SELECT MAX(s.observed_at) FROM stats_history s WHERE s.item_id = i.item_id),
              i.detail_fetched_at
            ) AS last_obs,
            (SELECT COUNT(*) FROM stats_history s WHERE s.item_id = i.item_id) AS cnt
          FROM items i
          WHERE i.status = 'ACTIVE' AND i.detail_fetched_at IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM revisit_state r WHERE r.item_id = i.item_id)
        )
        """
    )
    return cur.rowcount
```

엔지니어는 위 "정리된 최종 SQL" 버전만 파일에 넣는다(앞의 설명용 버전은 무시).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_repo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add storage/revisit_repo.py tests/test_revisit_repo.py
git commit -m "feat(revisit): seed_backfill + queue/unobserved queries"
```

---

## Task 9: 관측 1건 반영 (apply_revisit)

**Files:**
- Create: `crawler/revisit.py`
- Test: `tests/test_revisit.py`

`apply_revisit`은 락을 쥔 호출자가 부르는 DB 쓰기 함수. monitor의 `apply_enrich`로 상세/이미지/variants/stats를 한 번에 쓰고, 이어서 최근 2관측으로 `revisit_state`를 갱신한다.

- [ ] **Step 1: Write the failing test**

`tests/test_revisit.py` 생성:

```python
from storage.db import connect, init_schema
from storage.items_repo import upsert_scanned_item, update_detail_fields
from crawler.revisit import apply_revisit

# 최소 유효 상세 HTML: parse_item_detail가 view/fav/inquiry를 뽑을 수 있는 형태.
# 실제 파서 의존을 피하기 위해, 이 테스트는 parse 결과를 단순화한 HTML 대신
# monkeypatch로 parse_item_detail을 대체한다.
import crawler.monitor as monitor_mod


def _seed_item(conn, item_id, now):
    upsert_scanned_item(conn, item_id=item_id, seller_id="s1", name="n", price=100, now=now)


def test_apply_revisit_records_observation_and_state(monkeypatch):
    conn = connect(":memory:")
    init_schema(conn)
    now1 = "2026-06-27T00:00:00+09:00"
    now2 = "2026-06-29T00:00:00+09:00"
    _seed_item(conn, "1", now1)

    fake = {
        "name": "n", "brand": None, "category_path": None, "origin_country": None,
        "image_url": None, "description": None, "size_guide_text": None,
        "view_count": 100, "fav_count": 10, "inquiry_count": 1,
        "brand_model_number": None, "themes": None, "size_chart": None,
        "image_urls": [], "variants": [],
    }
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: fake)

    # first observation
    apply_revisit(conn, "1", "<html>", now1)
    row = conn.execute("SELECT * FROM revisit_state WHERE item_id='1'").fetchone()
    assert row["base_tier"] == "WARM"     # fav 10
    assert row["tier"] == "WARM"          # single obs, no surge
    assert row["obs_count"] == 1
    assert row["last_velocity"] is None

    # second observation: fav 10->40 over 2 days = 15/day -> surge -> HOT
    fake2 = dict(fake, fav_count=40, view_count=100)
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: fake2)
    apply_revisit(conn, "1", "<html>", now2)
    row = conn.execute("SELECT * FROM revisit_state WHERE item_id='1'").fetchone()
    assert row["base_tier"] == "WARM"
    assert row["tier"] == "HOT"
    assert row["obs_count"] == 2
    assert row["last_velocity"] == 15.0
    assert row["next_revisit_at"] == "2026-06-30T00:00:00+09:00"  # HOT +1d

    # two stats rows recorded
    assert conn.execute("SELECT COUNT(*) FROM stats_history WHERE item_id='1'").fetchone()[0] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit.py -v`
Expected: FAIL (ModuleNotFoundError: crawler.revisit)

- [ ] **Step 3: Implement**

`crawler/revisit.py` 생성:

```python
"""Adaptive revisit orchestration — reuses monitor's fetch/enrich helpers."""
import sqlite3

from crawler.monitor import apply_enrich
from crawler.revisit_scheduler import compute_observation_state
from storage import revisit_repo


def apply_revisit(conn: sqlite3.Connection, item_id: str, html: str, now: str) -> None:
    """DB write only — caller holds db_lock.

    1) apply_enrich: 상세/이미지/variants 갱신 + stats_history 관측 1행 (트랜잭션).
    2) 최근 2관측으로 revisit_state 갱신.
    """
    apply_enrich(conn, item_id, html, now)  # writes stats_history row at `now`
    obs = revisit_repo.get_recent_two_observations(conn, item_id)
    state = compute_observation_state(obs, now=now)
    revisit_repo.upsert_revisit_state(
        conn, item_id=item_id,
        tier=state["tier"], base_tier=state["base_tier"],
        last_observed_at=state["last_observed_at"],
        next_revisit_at=state["next_revisit_at"],
        obs_count=state["obs_count"], last_velocity=state["last_velocity"],
    )
```

> 주의: `apply_enrich`는 자체 BEGIN/COMMIT을 수행한 뒤 반환하므로, 이어지는 `upsert_revisit_state`는 자동커밋(`isolation_level=None`)으로 즉시 반영된다. `obs_count`는 stats_history의 실제 행 수가 아니라 `compute_observation_state`가 돌려준 "최근 관측 묶음 길이"가 아니라 — 정확히는 누적 관측 수가 필요하다. 아래 Step 3b로 보정한다.

- [ ] **Step 3b: 누적 obs_count 보정**

`compute_observation_state`의 `obs_count`는 최근 2개만 보므로 최대 2다. 누적 관측 수를 쓰도록 `apply_revisit`에서 실제 카운트로 덮어쓴다. `crawler/revisit.py`의 `upsert_revisit_state` 호출 직전에 추가:

```python
    total_obs = conn.execute(
        "SELECT COUNT(*) FROM stats_history WHERE item_id = ?", (item_id,)
    ).fetchone()[0]
```

그리고 `obs_count=state["obs_count"]`를 `obs_count=total_obs`로 변경.

테스트 `test_apply_revisit_records_observation_and_state`의 `obs_count` 기대값(1,2)은 누적 카운트와 일치하므로 그대로 통과한다.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crawler/revisit.py tests/test_revisit.py
git commit -m "feat(revisit): apply_revisit (enrich + state update)"
```

---

## Task 10: 오케스트레이션 run_revisit (워커 풀 + 시간 예산)

**Files:**
- Modify: `crawler/revisit.py`
- Test: `tests/test_revisit.py`

monitor의 워커 패턴을 차용: HTTP fetch는 락 밖, DB 쓰기는 락 안. 시작 시 `seed_backfill`을 1회 수행하고, 큐는 [미관측(1순위)] + [재방문(2순위)] 순. deadline(시작 시각 + max_hours)을 지나면 워커가 자발 종료.

- [ ] **Step 1: Write the failing test**

`tests/test_revisit.py`에 추가:

```python
from crawler.revisit import run_revisit


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _FakeClient:
    """모든 상세 요청에 동일한 200 HTML 반환."""
    def __init__(self):
        self.gets = 0

    def get(self, url):
        self.gets += 1
        return _FakeResp("<html>")

    def get_allowing_4xx(self, url):
        return _FakeResp("<html>")

    def close(self):
        pass


def test_run_revisit_seeds_then_fetches(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    # 1 enriched item (already observed) + 1 stranded (unobserved)
    upsert_scanned_item(conn, item_id="enriched", seller_id="s1", name="n", price=1, now=now0)
    update_detail_fields(
        conn, item_id="enriched", brand=None, category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None,
        view_count=600, fav_count=0, inquiry_count=0, brand_model_number=None,
        themes=None, size_chart_json=None, fetched_at=now0)
    conn.execute("INSERT INTO stats_history VALUES ('enriched', ?, 600, 0, 0)", (now0,))
    upsert_scanned_item(conn, item_id="stranded", seller_id="s1", name="n", price=1, now=now0)
    conn.close()

    fake = {
        "name": "n", "brand": None, "category_path": None, "origin_country": None,
        "image_url": None, "description": None, "size_guide_text": None,
        "view_count": 700, "fav_count": 1, "inquiry_count": 0,
        "brand_model_number": None, "themes": None, "size_chart": None,
        "image_urls": [], "variants": [],
    }
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: fake)

    client = _FakeClient()
    summary = run_revisit(
        db_path=str(db),
        scan_client_factory=lambda: client,
        num_workers=1,
        now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None,
        max_hours=1.0,
    )
    # both stranded(first fetch) and enriched(revisit) processed
    assert summary.observed == 2
    assert summary.seeded == 1   # only "enriched" was backfilled

    conn = connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM revisit_state").fetchone()[0] == 2
    # stranded now enriched
    assert conn.execute(
        "SELECT detail_fetched_at FROM items WHERE item_id='stranded'").fetchone()[0] is not None


def test_run_revisit_respects_deadline(tmp_path, monkeypatch):
    db = tmp_path / "t2.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    for i in range(5):
        upsert_scanned_item(conn, item_id=f"s{i}", seller_id="s1", name="n", price=1, now=now0)
    conn.close()
    fake = {
        "name": "n", "brand": None, "category_path": None, "origin_country": None,
        "image_url": None, "description": None, "size_guide_text": None,
        "view_count": 1, "fav_count": 0, "inquiry_count": 0,
        "brand_model_number": None, "themes": None, "size_chart": None,
        "image_urls": [], "variants": [],
    }
    monkeypatch.setattr(monitor_mod, "parse_item_detail", lambda html: fake)
    summary = run_revisit(
        db_path=str(db), scan_client_factory=lambda: _FakeClient(),
        num_workers=1, now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None, max_hours=0.0,  # already past deadline
    )
    assert summary.observed == 0   # deadline passed before any fetch
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit.py::test_run_revisit_seeds_then_fetches -v`
Expected: FAIL (ImportError: run_revisit)

- [ ] **Step 3: Implement**

`crawler/revisit.py`에 추가 (상단 import 보강):

```python
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from crawler.item_detail import build_item_detail_url
from crawler.item_status import ItemStatus
from crawler.monitor import (
    fetch_for_enrich, fetch_for_classification, apply_classification,
)
from storage.db import connect, init_schema


@dataclass
class RevisitSummary:
    seeded: int = 0
    observed: int = 0
    deleted: int = 0
    sold_out: int = 0
    errors: int = 0


def run_revisit(
    db_path: Path | str,
    scan_client_factory: Callable[[], object],
    num_workers: int,
    now: str,
    on_error: Callable[..., None],
    max_hours: float,
    circuit_breaker=None,
    revisit_limit: int = 1_000_000,
) -> RevisitSummary:
    """One revisit pass: seed backfill, then fetch unobserved(1st) + due revisits(2nd)
    within the time budget. Stops promptly past deadline or on circuit breaker."""
    db_path = Path(db_path)
    summary = RevisitSummary()

    main_conn = connect(db_path)
    init_schema(main_conn)
    summary.seeded = revisit_repo.seed_backfill(main_conn)

    # Build work queue: unobserved (priority 1) then revisit queue (priority 2).
    work: queue.Queue = queue.Queue()
    unobserved = revisit_repo.get_unobserved_active(main_conn)
    for iid in unobserved:
        work.put(("new", iid))
    for iid in revisit_repo.get_revisit_queue(main_conn, limit=revisit_limit):
        work.put(("revisit", iid))

    db_lock = threading.Lock()
    deadline = time.monotonic() + max_hours * 3600.0
    counts_lock = threading.Lock()

    def _expired() -> bool:
        return time.monotonic() >= deadline

    def _cb_open() -> bool:
        return circuit_breaker is not None and circuit_breaker.is_open()

    def worker():
        client = scan_client_factory()
        try:
            while not _expired() and not _cb_open():
                try:
                    kind, iid = work.get_nowait()
                except queue.Empty:
                    return
                try:
                    body = fetch_for_enrich(client, iid, on_error)
                    if body is not None:
                        try:
                            with db_lock:
                                apply_revisit(main_conn, iid, body, now)
                            with counts_lock:
                                summary.observed += 1
                        except Exception as e:
                            on_error(stage="revisit", url=build_item_detail_url(iid),
                                     status=None, reason=repr(e))
                            with counts_lock:
                                summary.errors += 1
                finally:
                    work.task_done()
        finally:
            if hasattr(client, "close"):
                client.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    main_conn.close()
    return summary
```

> 주의: 이 태스크 범위에서 404→DELETED 분기는 다음 태스크(11)에서 추가한다. 지금은 모든 fetch가 enrich 경로다. `errors` 카운트는 `counts_lock`으로 보호한다.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crawler/revisit.py tests/test_revisit.py
git commit -m "feat(revisit): run_revisit worker pool + time budget"
```

---

## Task 11: 재방문 중 삭제/품절 판정 (404 처리)

**Files:**
- Modify: `crawler/revisit.py`
- Test: `tests/test_revisit.py`

재방문 시 상세가 404면 DELETED로 마킹해야 한다(상품이 사라짐). monitor의 `fetch_for_classification` + `apply_classification` 재사용. 정책: fetch를 `get_allowing_4xx`로 받아 404/410 → DELETED, 200 → 정상 enrich 경로.

- [ ] **Step 1: Write the failing test**

`tests/test_revisit.py`에 추가:

```python
class _NotFoundClient:
    def get_allowing_4xx(self, url):
        return _FakeResp("", status=404)

    def get(self, url):
        return _FakeResp("", status=404)

    def close(self):
        pass


def test_run_revisit_marks_deleted_on_404(tmp_path, monkeypatch):
    db = tmp_path / "t3.db"
    conn = connect(str(db))
    init_schema(conn)
    now0 = "2026-06-20T00:00:00+09:00"
    upsert_scanned_item(conn, item_id="gone", seller_id="s1", name="n", price=1, now=now0)
    update_detail_fields(
        conn, item_id="gone", brand=None, category_path=None, origin_country=None,
        image_url=None, description=None, size_guide_text=None,
        view_count=600, fav_count=0, inquiry_count=0, brand_model_number=None,
        themes=None, size_chart_json=None, fetched_at=now0)
    conn.execute("INSERT INTO stats_history VALUES ('gone', ?, 600, 0, 0)", (now0,))
    conn.close()

    summary = run_revisit(
        db_path=str(db), scan_client_factory=lambda: _NotFoundClient(),
        num_workers=1, now="2026-06-29T00:00:00+09:00",
        on_error=lambda **kw: None, max_hours=1.0)
    assert summary.deleted == 1
    conn = connect(str(db))
    assert conn.execute("SELECT status FROM items WHERE item_id='gone'").fetchone()[0] == "DELETED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit.py::test_run_revisit_marks_deleted_on_404 -v`
Expected: FAIL (summary.deleted == 0, status still ACTIVE)

- [ ] **Step 3: Implement**

`crawler/revisit.py`의 `worker()` 내부 처리 블록을 교체. fetch를 분류용으로 먼저 받아 상태코드로 분기:

```python
                try:
                    fetched = fetch_for_classification(client, iid, on_error)
                    if fetched is not None:
                        status_code, body = fetched
                        if status_code == 200:
                            try:
                                with db_lock:
                                    apply_revisit(main_conn, iid, body, now)
                                with counts_lock:
                                    summary.observed += 1
                            except Exception as e:
                                on_error(stage="revisit", url=build_item_detail_url(iid),
                                         status=None, reason=repr(e))
                                with counts_lock:
                                    summary.errors += 1
                        else:
                            with db_lock:
                                status = apply_classification(main_conn, iid, status_code, now)
                            with counts_lock:
                                if status is ItemStatus.DELETED:
                                    summary.deleted += 1
                                elif status is ItemStatus.SOLD_OUT:
                                    summary.sold_out += 1
                finally:
                    work.task_done()
```

기존 `fetch_for_enrich` 사용 블록은 이 블록으로 대체한다. (import의 `fetch_for_enrich`는 더 이상 쓰지 않으면 제거.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit.py -v`
Expected: PASS (모든 기존 테스트 포함)

- [ ] **Step 5: Commit**

```bash
git add crawler/revisit.py tests/test_revisit.py
git commit -m "feat(revisit): mark DELETED/SOLD_OUT on revisit 4xx"
```

---

## Task 12: 급등 리포트 쿼리 (top_surging, coverage_stats)

**Files:**
- Modify: `storage/revisit_repo.py`
- Test: `tests/test_revisit_repo.py`

- [ ] **Step 1: Write the failing test**

`tests/test_revisit_repo.py`에 추가:

```python
def test_top_surging_orders_by_velocity_desc():
    conn = _mem()
    _add_item(conn, "a", view=1); _add_item(conn, "b", view=1)
    for iid, vel in [("a", 3.0), ("b", 50.0)]:
        revisit_repo.upsert_revisit_state(
            conn, item_id=iid, tier="HOT", base_tier="WARM",
            last_observed_at="2026-06-29T00:00:00+09:00",
            next_revisit_at="2026-06-30T00:00:00+09:00",
            obs_count=2, last_velocity=vel)
    rows = revisit_repo.top_surging(conn, limit=10)
    assert [r["item_id"] for r in rows] == ["b", "a"]
    assert rows[0]["last_velocity"] == 50.0
    assert rows[0]["name"] == "n"   # joined from items


def test_coverage_stats_counts_observed():
    conn = _mem()
    _add_item(conn, "a", view=1); _add_item(conn, "b", view=1)
    revisit_repo.upsert_revisit_state(
        conn, item_id="a", tier="HOT", base_tier="HOT",
        last_observed_at="x", next_revisit_at="y", obs_count=3, last_velocity=1.0)
    revisit_repo.upsert_revisit_state(
        conn, item_id="b", tier="COLD", base_tier="COLD",
        last_observed_at="x", next_revisit_at="y", obs_count=1, last_velocity=None)
    stats = revisit_repo.coverage_stats(conn)
    assert stats["total"] == 2
    assert stats["multi_observed"] == 1   # obs_count >= 2
    assert stats["by_tier"]["HOT"] == 1
    assert stats["by_tier"]["COLD"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_repo.py -v`
Expected: FAIL (AttributeError: top_surging)

- [ ] **Step 3: Implement**

`storage/revisit_repo.py`에 추가:

```python
def top_surging(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """fav_velocity 높은 순 급등 상품 (이름·티어 포함)."""
    return conn.execute(
        """
        SELECT r.item_id, r.tier, r.base_tier, r.last_velocity, r.obs_count,
               i.name, i.current_price, i.view_count, i.fav_count, i.inquiry_count
        FROM revisit_state r JOIN items i ON i.item_id = r.item_id
        WHERE r.last_velocity IS NOT NULL
        ORDER BY r.last_velocity DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()


def coverage_stats(conn: sqlite3.Connection) -> dict:
    """스케줄 커버리지 요약: 총 등록 수, 2회+ 관측 수, 티어 분포."""
    total = conn.execute("SELECT COUNT(*) FROM revisit_state").fetchone()[0]
    multi = conn.execute("SELECT COUNT(*) FROM revisit_state WHERE obs_count >= 2").fetchone()[0]
    by_tier = {r[0]: r[1] for r in conn.execute(
        "SELECT tier, COUNT(*) FROM revisit_state GROUP BY tier")}
    return {"total": total, "multi_observed": multi, "by_tier": by_tier}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest tests/test_revisit_repo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add storage/revisit_repo.py tests/test_revisit_repo.py
git commit -m "feat(revisit): top_surging + coverage_stats reports"
```

---

## Task 13: CLI 진입점 (revisit_cli.py)

**Files:**
- Create: `revisit_cli.py`
- Test: 수동 스모크 (아래)

`monitor_cli.py`를 본떠 `run`/`report` 서브커맨드를 제공한다.

- [ ] **Step 1: Implement**

`revisit_cli.py` 생성:

```python
"""Adaptive revisit scheduler CLI.

  python revisit_cli.py run --max-hours 2 --workers 5 --sleep 0.3
  python revisit_cli.py report --top 30
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from crawler.circuit_breaker import CircuitBreaker
from crawler.client import HttpClient
from crawler.revisit import run_revisit
from storage.db import connect, init_schema
from storage import revisit_repo
from storage.store import now_iso

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "items.db"
ERRORS_PATH = DATA_DIR / "errors.log"


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def append_error_jsonl(**kw) -> None:
    with ERRORS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": now_iso(), **kw}, ensure_ascii=False) + "\n")


def cmd_run(args) -> int:
    setup_logging(args.verbose)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cb = CircuitBreaker(threshold=args.cb_threshold, window_seconds=args.cb_window_seconds)

    def factory():
        return HttpClient(sleep_seconds=args.sleep, circuit_breaker=cb)

    now = now_iso()
    logging.info("revisit run start %s (max_hours=%.2f, workers=%d, sleep=%.2f)",
                 now, args.max_hours, args.workers, args.sleep)
    summary = run_revisit(
        db_path=DB_PATH, scan_client_factory=factory, num_workers=args.workers,
        now=now, on_error=append_error_jsonl, max_hours=args.max_hours,
        circuit_breaker=cb)
    if cb.is_open():
        logging.warning("Circuit breaker tripped — likely IP block. Investigate before rerun.")
    logging.info("Done: %s", summary)
    return 0


def cmd_report(args) -> int:
    conn = connect(DB_PATH)
    init_schema(conn)
    cov = revisit_repo.coverage_stats(conn)
    print(f"등록 {cov['total']:,} / 2회+관측 {cov['multi_observed']:,}  티어 {cov['by_tier']}")
    print(f"\n급등 TOP {args.top} (fav velocity/day):")
    print(f"{'item_id':>10}  {'tier':>4}  {'vel/day':>8}  {'obs':>3}  name")
    for r in revisit_repo.top_surging(conn, limit=args.top):
        print(f"{r['item_id']:>10}  {r['tier']:>4}  {r['last_velocity']:>8.1f}  "
              f"{r['obs_count']:>3}  {(r['name'] or '')[:40]}")
    conn.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="BUYMA adaptive revisit scheduler")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one revisit pass within a time budget")
    r.add_argument("--max-hours", type=float, required=True)
    r.add_argument("--workers", type=int, default=5)
    r.add_argument("--sleep", type=float, default=0.3)
    r.add_argument("--cb-threshold", type=int, default=5)
    r.add_argument("--cb-window-seconds", type=int, default=60)
    r.add_argument("--verbose", action="store_true")
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="show surge ranking + coverage")
    rep.add_argument("--top", type=int, default=30)
    rep.set_defaults(func=cmd_report)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke test — help + report on empty schedule**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 revisit_cli.py --help`
Expected: `run`, `report` 서브커맨드가 보임.

Run: `PYTHONPATH="$PWD" .venv/bin/python3 revisit_cli.py report --top 5`
Expected: 커버리지 한 줄 + 빈 급등 표 헤더 (에러 없이 종료).

- [ ] **Step 3: Run full test suite**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 -m pytest -q`
Expected: 전체 PASS (기존 + 신규).

- [ ] **Step 4: Commit**

```bash
git add revisit_cli.py
git commit -m "feat(revisit): revisit_cli run/report entrypoint"
```

---

## Task 14: 라이브 스모크 (실데이터 소량 검증)

**Files:** 없음 (실행만)

실제 DB(`data/items.db`)에서 시드와 한 번의 짧은 재방문을 검증한다.

- [ ] **Step 1: 백업**

```bash
cp data/items.db data/items.db.pre-revisit-bak
```

- [ ] **Step 2: 짧은 재방문 실행 (3분)**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 revisit_cli.py run --max-hours 0.05 --workers 5 --sleep 0.3`
Expected 로그: `seeded=<큰 수>` (첫 실행이라 ~130만 등록), `observed=<수백~수천>`, `errors` 소수, deadline에 자발 종료.

- [ ] **Step 3: 결과 확인**

Run:
```bash
PYTHONPATH="$PWD" .venv/bin/python3 -c "
import sqlite3
c=sqlite3.connect('data/items.db')
print('revisit_state:', c.execute('SELECT COUNT(*) FROM revisit_state').fetchone()[0])
print('multi-obs    :', c.execute('SELECT COUNT(*) FROM revisit_state WHERE obs_count>=2').fetchone()[0])
print('by tier      :', dict(c.execute('SELECT tier,COUNT(*) FROM revisit_state GROUP BY tier')))
"
```
Expected: `revisit_state`가 활성·enriched 상품 수에 근접(~126만), `by tier`에 HOT/WARM/COLD 분포.

- [ ] **Step 4: 리포트 확인**

Run: `PYTHONPATH="$PWD" .venv/bin/python3 revisit_cli.py report --top 10`
Expected: 커버리지 요약 출력. (급등은 2회 관측이 쌓여야 나오므로 첫 실행 직후엔 비거나 적을 수 있음 — 정상.)

- [ ] **Step 5: 정리 커밋 (코드 변경 없으면 생략)**

라이브 스모크는 코드 변경이 없으므로 커밋 없음. 문제가 발견되면 해당 Task로 돌아가 수정.

---

## Self-Review (작성자 체크)

**Spec coverage:**
- 0단계 시드 backfill → Task 8 (`seed_backfill`) + Task 10(run에서 호출)
- 1순위 미관측 fetch / 2순위 재방문 → Task 10 (큐 구성)
- urgency = next_revisit_at ASC 정렬 → Task 8 (`get_revisit_queue`)
- 티어 간격(1/4/30) → Task 3
- base_tier 재평가 → Task 2 + Task 6
- velocity Δ/Δ일, 1관측 None, 0-division 가드 → Task 4
- 급등 일시 승급 → Task 5 + Task 6
- 시간 예산 무손실 중단 → Task 10 (`deadline`, deadline 테스트)
- 404→DELETED/품절 → Task 11
- 산출물(급등 리포트/커버리지) → Task 12 + Task 13(report)
- revisit_state 테이블(items 불변) → Task 1
- CircuitBreaker/에러로그/락 규칙 → Task 10·11·13
- 파일 구조(scheduler/repo/revisit/cli 분리) → 전 Task 일치

**Type consistency:** `compute_observation_state` 반환 키(base_tier/tier/last_velocity/last_observed_at/next_revisit_at/obs_count)를 `apply_revisit`이 그대로 사용. `upsert_revisit_state` 인자명 일치. `get_recent_two_observations` 반환 `(observed_at,view,fav,inquiry)` 순서를 `compute_velocity`/`compute_observation_state`가 동일하게 소비. `obs_count`는 Task 9 Step 3b에서 누적 카운트로 보정(스키마 의미와 일치).

**비범위(YAGNI):** velocity 피드백 완전 자동화, 카테고리/가격 보정, cron 자동화 제외(spec과 일치).
