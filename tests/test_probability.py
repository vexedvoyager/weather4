from src.probability import probability_of_exceeding


def test_median_is_fifty_percent():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    assert abs(probability_of_exceeding(pct, 76) - 0.5) < 1e-6


def test_p10_is_ninety_percent():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    assert abs(probability_of_exceeding(pct, 68) - 0.9) < 1e-6


def test_p90_is_ten_percent():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    assert abs(probability_of_exceeding(pct, 84) - 0.1) < 1e-6


def test_far_below_p10_approaches_one():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    prob = probability_of_exceeding(pct, 40)
    assert 0.99 < prob <= 1.0


def test_far_above_p90_approaches_zero():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    prob = probability_of_exceeding(pct, 120)
    assert 0.0 <= prob < 0.01


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


def test_fatter_tail_multiplier_widens_extreme_probability():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    thin_tail = probability_of_exceeding(pct, 100, sigma_multiplier=1.0)
    fat_tail = probability_of_exceeding(pct, 100, sigma_multiplier=1.15)
    # A fatter tail should assign MORE probability to an extreme outcome.
    assert fat_tail > thin_tail
