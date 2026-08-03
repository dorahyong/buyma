"""Pure scheduling logic for adaptive revisits — no I/O, no DB.

Tier thresholds are the confirmed absolute popularity 3-tier (see
docs/superpowers/specs/2026-06-29-adaptive-revisit-scheduler-design.md).
"""

from datetime import datetime, timedelta

# 판매 신호(상품 단위) — 조회/찜/문의와 OR 로 블렌드.
# 럭셔리는 조회·찜이 낮아도 매출이 커서 COLD(30일)에 묻히던 문제를 판매로 구제한다.
SALES_WINDOW_DAYS = 30       # 일반 상품 판매 관측 창
SALES_HOT_ORDERS = 3         # 최근 창 판매 N건 이상 → HOT
SALES_WARM_ORDERS = 1        # 최근 창 판매 N건 이상 → WARM

# 셀러 판매력(좁은 완화) — "통째 승급"은 상위 셀러가 DB의 79%를 차지해 위험.
# 대신 잘 파는 셀러의 상품만 판매 창을 길게 봐서, 드물게 팔리는 럭셔리를 놓치지 않는다.
STRONG_SELLER_ORDER_COUNT = 1000   # 셀러 누적 판매수 기준선
STRONG_SELLER_WINDOW_DAYS = 90     # 강한 셀러 상품의 완화된 판매 창
STRONG_SELLER_WARM_ORDERS = 1      # 그 창에서 N건 이상 → WARM


def sales_cutoff(now: str, days: int) -> str:
    """now(ISO, KST)에서 days 전 날짜를 orders.sale_date 형식('YYYY/MM/DD')로."""
    return (datetime.fromisoformat(now) - timedelta(days=days)).strftime("%Y/%m/%d")


def classify_tier(
    view: int | None,
    fav: int | None,
    inquiry: int | None,
    sales_recent: int | None = 0,
    sales_extended: int | None = 0,
    seller_strong: bool = False,
) -> str:
    """Absolute popularity tier from current cumulative metrics + sales signal.

    sales_recent: 최근 SALES_WINDOW_DAYS(30일) 판매 건수.
    sales_extended: 강한 셀러용 확장 창(STRONG_SELLER_WINDOW_DAYS=90일) 판매 건수.
    seller_strong: 셀러 누적판매 >= STRONG_SELLER_ORDER_COUNT 인지.
    """
    v = view or 0
    f = fav or 0
    q = inquiry or 0
    sr = sales_recent or 0
    se = sales_extended or 0
    if f >= 50 or v >= 2000 or q >= 5 or sr >= SALES_HOT_ORDERS:
        return "HOT"
    if (f >= 10 or v >= 500 or q >= 1 or sr >= SALES_WARM_ORDERS
            or (seller_strong and se >= STRONG_SELLER_WARM_ORDERS)):
        return "WARM"
    return "COLD"


TIER_INTERVAL_DAYS = {"HOT": 1, "WARM": 4, "COLD": 30}


def next_revisit_at_from(observed_at: str, tier: str) -> str:
    """observed_at(ISO, KST) + 티어 목표간격을 더한 ISO 문자열."""
    dt = datetime.fromisoformat(observed_at)
    dt2 = dt + timedelta(days=TIER_INTERVAL_DAYS[tier])
    return dt2.isoformat()


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


SURGE_FAV_PER_DAY = 5.0
SURGE_VIEW_PER_DAY = 100.0
_TIER_ORDER = ["COLD", "WARM", "HOT"]


def apply_promotion(base_tier: str, fav_velocity: float | None, view_velocity: float | None) -> str:
    """급등이면 base_tier에서 한 단계 승급(COLD→WARM→HOT). 일시적."""
    surge = ((fav_velocity is not None and fav_velocity >= SURGE_FAV_PER_DAY)
             or (view_velocity is not None and view_velocity >= SURGE_VIEW_PER_DAY))
    if not surge:
        return base_tier
    i = _TIER_ORDER.index(base_tier)
    return _TIER_ORDER[min(i + 1, len(_TIER_ORDER) - 1)]


def compute_observation_state(
    observations: list[tuple],
    now: str,
    sales_recent: int | None = 0,
    sales_extended: int | None = 0,
    seller_strong: bool = False,
) -> dict:
    """관측 직후 revisit_state에 반영할 값 산출.

    observations: 최신순 [(observed_at, view, fav, inquiry), ...] (now 관측 포함, 비어있지 않아야 함).
    sales_recent/sales_extended/seller_strong: 판매 블렌드 신호 (classify_tier 참고).
    """
    newest = observations[0]
    _t, v, f, q = newest
    base_tier = classify_tier(
        view=v, fav=f, inquiry=q,
        sales_recent=sales_recent, sales_extended=sales_extended,
        seller_strong=seller_strong,
    )
    vel = compute_velocity(observations)
    tier = apply_promotion(base_tier, vel["fav_velocity"], vel["view_velocity"])
    return {
        "base_tier": base_tier,
        "tier": tier,
        "last_velocity": vel["fav_velocity"],
        "last_observed_at": now,
        "next_revisit_at": next_revisit_at_from(now, tier),
        # obs_count here = recent-window length (<=2); apply_revisit overrides
        # this with the cumulative stats_history count before persisting.
        "obs_count": len(observations),
    }
