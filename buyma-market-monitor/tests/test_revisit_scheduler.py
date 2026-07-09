from crawler.revisit_scheduler import classify_tier, TIER_INTERVAL_DAYS, next_revisit_at_from, compute_velocity, apply_promotion, compute_observation_state


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


def test_tier_intervals():
    assert TIER_INTERVAL_DAYS == {"HOT": 1, "WARM": 4, "COLD": 30}


def test_next_revisit_at_adds_interval_keeping_kst():
    assert next_revisit_at_from("2026-06-29T15:00:00+09:00", "HOT") \
        == "2026-06-30T15:00:00+09:00"
    assert next_revisit_at_from("2026-06-29T15:00:00+09:00", "WARM") \
        == "2026-07-03T15:00:00+09:00"
    assert next_revisit_at_from("2026-06-01T00:00:00+09:00", "COLD") \
        == "2026-07-01T00:00:00+09:00"


def test_velocity_none_when_single_observation():
    obs = [("2026-06-29T00:00:00+09:00", 100, 10, 1)]
    assert compute_velocity(obs) == {"fav_velocity": None, "view_velocity": None}


def test_velocity_per_day_from_two_observations():
    obs = [
        ("2026-06-29T00:00:00+09:00", 300, 20, 5),
        ("2026-06-27T00:00:00+09:00", 100, 10, 1),
    ]
    v = compute_velocity(obs)
    assert v["fav_velocity"] == 5.0
    assert v["view_velocity"] == 100.0


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
    assert v["fav_velocity"] == 20.0
    assert v["view_velocity"] == -100.0


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


def test_observation_state_single_obs_uses_base_tier():
    obs = [("2026-06-29T00:00:00+09:00", 600, 0, 0)]
    st = compute_observation_state(obs, now="2026-06-29T00:00:00+09:00")
    assert st["base_tier"] == "WARM"
    assert st["tier"] == "WARM"
    assert st["last_velocity"] is None
    assert st["next_revisit_at"] == "2026-07-03T00:00:00+09:00"
    assert st["last_observed_at"] == "2026-06-29T00:00:00+09:00"


def test_observation_state_promotes_on_surge_and_shortens_interval():
    obs = [
        ("2026-06-29T00:00:00+09:00", 600, 20, 0),
        ("2026-06-28T00:00:00+09:00", 0, 0, 0),
    ]
    st = compute_observation_state(obs, now="2026-06-29T00:00:00+09:00")
    assert st["base_tier"] == "WARM"
    assert st["tier"] == "HOT"
    assert st["last_velocity"] == 20.0
    assert st["next_revisit_at"] == "2026-06-30T00:00:00+09:00"
