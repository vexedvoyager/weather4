from src.probability import probability_within_range, probability_of_exceeding


def test_range_probability_is_positive_and_bounded():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    prob = probability_within_range(pct, 74, 78)
    assert 0.0 <= prob <= 1.0


def test_range_probability_equals_difference_of_exceed_probabilities():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    floor, cap = 74, 78
    expected = probability_of_exceeding(pct, floor) - probability_of_exceeding(pct, cap)
    result = probability_within_range(pct, floor, cap)
    assert abs(result - round(max(0.0, expected), 6)) < 1e-6


def test_wide_range_has_higher_probability_than_narrow_range():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    narrow = probability_within_range(pct, 75, 77)
    wide = probability_within_range(pct, 60, 100)
    assert wide > narrow


def test_range_with_cap_less_than_floor_raises():
    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    try:
        probability_within_range(pct, 80, 70)
        assert False, "should have raised on cap < floor"
    except ValueError:
        pass
