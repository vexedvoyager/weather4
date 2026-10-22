from src.probability import probability_of_exceeding, PROBABILITY_FLOOR, PROBABILITY_CEILING


def test_median_is_fifty_percent():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    assert abs(probability_of_exceeding(pct, 76) - 0.5) < 1e-6


def test_p10_is_ninety_percent():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    assert abs(probability_of_exceeding(pct, 68) - 0.9) < 1e-6


def test_p90_is_ten_percent():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    assert abs(probability_of_exceeding(pct, 84) - 0.1) < 1e-6


def test_far_below_p10_is_clamped_to_ceiling_not_approaching_one():
    """
    UPDATED for v5: earlier versions expected this to approach 1.0 - that
    was the exact mathematical behavior found to cause a real 0-for-12
    losing streak on far-tail markets (see IMPROVEMENTS.md item #10).
    Now it should clamp at PROBABILITY_CEILING, not climb toward 1.0.
    """
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    prob = probability_of_exceeding(pct, 40)
    assert prob == PROBABILITY_CEILING


def test_far_above_p90_is_clamped_to_floor_not_approaching_zero():
    """UPDATED for v5 - see test_far_below_p10_is_clamped_to_ceiling_not_approaching_one."""
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    prob = probability_of_exceeding(pct, 120)
    assert prob == PROBABILITY_FLOOR


def test_clamp_false_exposes_the_old_unclamped_behavior():
    """The raw (clamp=False) computation should still show the original
    approach-to-0/1 behavior - this is what shadow-tracking inspects."""
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    raw_low = probability_of_exceeding(pct, 40, clamp=False)
    raw_high = probability_of_exceeding(pct, 120, clamp=False)
    assert raw_low > 0.99
    assert raw_high < 0.01


def test_moderate_tail_multiplier_still_has_effect_before_hitting_the_clamp():
    """
    UPDATED for v5: the original version of this test used a threshold
    (100) far enough out that BOTH multiplier values already clamp to the
    same floor, making the comparison meaningless post-clamp. Using a
    more moderate tail threshold (just past P90) confirms sigma_multiplier
    still does something for cases that don't immediately hit the clamp.
    """
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    thin_tail = probability_of_exceeding(pct, 86, sigma_multiplier=1.0)
    fat_tail = probability_of_exceeding(pct, 86, sigma_multiplier=1.15)
    assert fat_tail > thin_tail


def test_clamp_never_exceeds_bounds_across_a_range_of_extreme_thresholds():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    for threshold in [-500, -100, -50, 200, 500, 1000]:
        prob = probability_of_exceeding(pct, threshold)
        assert PROBABILITY_FLOOR <= prob <= PROBABILITY_CEILING


def test_real_world_far_tail_below_scenario_no_longer_claims_near_certainty():
    """
    Regression test for the actual, confirmed real-world failure pattern:
    12 real trades on far-upper-tail "below X" single-threshold markets
    went 0-for-12 despite the model claiming 95-100% confidence (see
    IMPROVEMENTS.md item #10 for the full data). This uses forecast
    numbers matching the real shape of those trades (e.g. Austin, P50 in
    the high 80s, threshold well above P90) and confirms the clamped
    "below" probability - the complement of exceeding - no longer climbs
    to a false near-certainty.
    """
    pct = {"p10": 82, "p25": 85, "p50": 88, "p75": 91, "p90": 94}
    threshold = 96  # a real "less than 96" style threshold, past P90

    p_exceeds = probability_of_exceeding(pct, threshold)
    p_stays_below = round(1 - p_exceeds, 6)  # what forecast_refresh.py computes for "below" markets

    assert p_exceeds >= PROBABILITY_FLOOR  # can no longer look "impossible to exceed"
    assert p_stays_below <= PROBABILITY_CEILING  # can no longer look "certain to stay below"


def test_monotonic_probability_decreases_with_threshold():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    thresholds = [50, 60, 70, 76, 80, 90, 100]
    probs = [probability_of_exceeding(pct, t) for t in thresholds]
    assert all(probs[i] >= probs[i + 1] for i in range(len(probs) - 1))


def test_rejects_non_monotonic_percentiles():
    bad_pct = {"p10": 80, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    try:
        probability_of_exceeding(bad_pct, 76)
        assert False, "should have raised on non-monotonic percentiles"
    except ValueError:
        pass
