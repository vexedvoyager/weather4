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


# --- v6.1.1: clamp the bracket result, not each end ---------------------------
from src.probability import PROBABILITY_FLOOR, PROBABILITY_CEILING

# Real Chicago bulletin values (run 2026-10-22T01Z, FHR 23)
CHI = {"p10": 70, "p25": 70, "p50": 71, "p75": 72, "p90": 73}


def test_tail_bracket_is_never_reported_as_zero():
    # Raw ~3.3%: used to come out exactly 0.0 because both ends clamped to 0.95.
    raw = probability_within_range(CHI, 67.5, 69.5, clamp=False)
    clamped = probability_within_range(CHI, 67.5, 69.5)
    assert raw < PROBABILITY_FLOOR
    assert clamped == PROBABILITY_FLOOR


def test_clamped_bracket_always_within_bounds():
    for lo in range(55, 90, 2):
        p = probability_within_range(CHI, lo - 0.5, lo + 1.5)
        assert PROBABILITY_FLOOR <= p <= PROBABILITY_CEILING


def test_mid_bracket_inside_bounds_is_not_altered_by_clamp():
    raw = probability_within_range(CHI, 69.5, 71.5, clamp=False)
    assert PROBABILITY_FLOOR < raw < PROBABILITY_CEILING
    assert probability_within_range(CHI, 69.5, 71.5) == raw


def test_bracket_near_tail_is_not_shrunk_by_end_clamping():
    # Old behavior clamped P(exceed 69.5) at 0.95 and shrank this bracket.
    raw = probability_within_range(CHI, 69.5, 70.5, clamp=False)
    assert probability_within_range(CHI, 69.5, 70.5) == max(raw, PROBABILITY_FLOOR)
