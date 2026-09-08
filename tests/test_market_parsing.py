from src.market_parsing import extract_threshold, build_market_snapshot, _best_level_size


def test_greater_or_equal_uses_floor_strike():
    market = {"strike_type": "greater_or_equal", "floor_strike": 80.0, "cap_strike": None}
    result = extract_threshold(market)
    assert result == {"kind": "single", "value": 80.0, "direction": "above"}


def test_greater_uses_floor_strike():
    market = {"strike_type": "greater", "floor_strike": 75.5, "cap_strike": None}
    result = extract_threshold(market)
    assert result == {"kind": "single", "value": 75.5, "direction": "above"}


def test_less_or_equal_uses_cap_strike():
    market = {"strike_type": "less_or_equal", "floor_strike": None, "cap_strike": 60.0}
    result = extract_threshold(market)
    assert result == {"kind": "single", "value": 60.0, "direction": "below"}


def test_between_uses_both_floor_and_cap():
    market = {"strike_type": "between", "floor_strike": 78.0, "cap_strike": 80.0}
    result = extract_threshold(market)
    assert result == {"kind": "between", "floor": 78.0, "cap": 80.0}


def test_missing_required_field_returns_none_not_a_guess():
    market = {"strike_type": "greater_or_equal", "floor_strike": None, "cap_strike": None}
    assert extract_threshold(market) is None


def test_unrecognized_strike_type_returns_none():
    market = {"strike_type": "functional", "floor_strike": 1, "cap_strike": 2}
    assert extract_threshold(market) is None


def test_best_level_size_reads_last_element_ascending_order():
    # Kalshi orderbook levels are ascending by price; best bid is the LAST one.
    levels = [["0.10", "5.00"], ["0.20", "3.00"], ["0.55", "12.00"]]
    assert _best_level_size(levels) == 12


def test_best_level_size_empty_list():
    assert _best_level_size([]) == 0


def test_build_market_snapshot_derives_ask_from_opposite_bid():
    market = {
        "yes_bid_dollars": "0.5600",
        "no_bid_dollars": "0.4200",
        "volume_24h_fp": "150.00",
    }
    orderbook = {
        "yes_dollars": [["0.50", "10.00"], ["0.56", "25.00"]],
        "no_dollars": [["0.40", "8.00"], ["0.42", "15.00"]],
    }
    snap = build_market_snapshot("TEST-TICKER", market, orderbook)

    assert snap.yes_bid_cents == 56
    assert snap.no_bid_cents == 42
    # YES ask should be 100 - no_bid = 100 - 42 = 58
    assert snap.yes_ask_cents == 58
    # NO ask should be 100 - yes_bid = 100 - 56 = 44
    assert snap.no_ask_cents == 44
    assert snap.volume_24h == 150
    assert snap.yes_bid_size == 25
    assert snap.no_bid_size == 15


def test_less_than_market_probability_is_correctly_inverted():
    """
    THE critical regression test for a real, confirmed bug: earlier
    versions always computed "probability of exceeding the threshold"
    for single-threshold markets, even for "less than" contracts, where
    the correct YES-probability is the COMPLEMENT of that.

    Found by comparing real trade output against Kalshi's own settlement
    text: a Chicago "less than 88" contract was reported with 75%
    confidence when the forecast clearly implied a high in the 90s -
    meaning the model was ~75% confident of EXCEEDING 88, which should
    have translated to only ~25% confidence in the "less than 88"
    contract resolving YES.
    """
    from src.forecast_refresh import model_probability_for_market
    from src.probability import probability_of_exceeding

    pct = {"p10": 88, "p25": 90, "p50": 93, "p75": 96, "p90": 99}  # a hot day, like the real Chicago case

    p_exceeds_88 = probability_of_exceeding(pct, 88.0, sigma_multiplier=1.15)
    assert p_exceeds_88 > 0.85  # confirms this really is a "very likely to exceed 88" scenario

    above_threshold = {"kind": "single", "value": 88.0, "direction": "above"}
    below_threshold = {"kind": "single", "value": 88.0, "direction": "below"}

    prob_above = model_probability_for_market(above_threshold, pct, 1.15)
    prob_below = model_probability_for_market(below_threshold, pct, 1.15)

    # "Greater than 88" should match probability_of_exceeding directly.
    assert abs(prob_above - p_exceeds_88) < 1e-6

    # "Less than 88" must be the COMPLEMENT, not the same value - this is
    # the exact inversion that was missing before the fix.
    assert abs(prob_below - (1 - p_exceeds_88)) < 1e-6
    assert prob_below < 0.15  # should be LOW confidence, not 75%+ as the bug produced


def test_above_and_below_thresholds_always_sum_to_one():
    from src.forecast_refresh import model_probability_for_market

    pct = {"p10": 68, "p25": 72, "p50": 76, "p75": 80, "p90": 84}
    for value in (60, 70, 76, 82, 95):
        above = model_probability_for_market(
            {"kind": "single", "value": value, "direction": "above"}, pct, 1.15
        )
        below = model_probability_for_market(
            {"kind": "single", "value": value, "direction": "below"}, pct, 1.15
        )
        assert abs((above + below) - 1.0) < 1e-6
