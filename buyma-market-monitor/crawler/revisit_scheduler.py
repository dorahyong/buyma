"""Pure scheduling logic for adaptive revisits — no I/O, no DB.

Tier thresholds are the confirmed absolute popularity 3-tier (see
docs/superpowers/specs/2026-06-29-adaptive-revisit-scheduler-design.md).
"""

from datetime import datetime, timedelta


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


def compute_observation_state(observations: list[tuple], now: str) -> dict:
    """관측 직후 revisit_state에 반영할 값 산출.

    observations: 최신순 [(observed_at, view, fav, inquiry), ...] (now 관측 포함, 비어있지 않아야 함).
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
        # obs_count here = recent-window length (<=2); apply_revisit overrides
        # this with the cumulative stats_history count before persisting.
        "obs_count": len(observations),
    }
