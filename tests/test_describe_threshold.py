from src.market_parsing import describe_threshold


def test_describe_above_threshold():
    assert describe_threshold({"kind": "single", "value": 95.0, "direction": "above"}) == "high > 95°F"


def test_describe_below_threshold():
    assert describe_threshold({"kind": "single", "value": 88.0, "direction": "below"}) == "high < 88°F"


def test_describe_between_range():
    assert describe_threshold({"kind": "between", "floor": 85.0, "cap": 86.0}) == "high 85-86°F"


def test_describe_handles_decimal_values_cleanly():
    # :g formatting should show 95.5 as "95.5", not "95.500000"
    result = describe_threshold({"kind": "single", "value": 95.5, "direction": "above"})
    assert result == "high > 95.5°F"


def test_describe_handles_whole_numbers_without_trailing_decimal():
    result = describe_threshold({"kind": "single", "value": 88.0, "direction": "below"})
    assert "88°F" in result
    assert "88.0" not in result
