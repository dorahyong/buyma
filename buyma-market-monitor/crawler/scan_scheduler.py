"""Pure logic for value-prioritized seller listing scan cadence."""
from datetime import datetime, timedelta

SELLER_SCAN_INTERVAL_DAYS = {"HIGH": 1, "MID": 4, "LOW": 21}

TIER_HIGH_FRAC = 0.15
TIER_MID_FRAC = 0.50


def tier_for_rank(rank_fraction: float,
                  high_frac: float = TIER_HIGH_FRAC,
                  mid_frac: float = TIER_MID_FRAC) -> str:
    """rank_fraction = position-from-top / total (0.0 = highest value).
    HIGH if in top high_frac, MID if in next up to mid_frac, else LOW."""
    if rank_fraction < high_frac:
        return "HIGH"
    if rank_fraction < mid_frac:
        return "MID"
    return "LOW"


def next_scan_at_from(last_scanned_at: str, tier: str) -> str:
    dt = datetime.fromisoformat(last_scanned_at) + timedelta(days=SELLER_SCAN_INTERVAL_DAYS[tier])
    return dt.isoformat()
