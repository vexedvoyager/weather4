from src.daily_summary import ascii_sparkline


def test_sparkline_empty():
    assert ascii_sparkline([]) == ""


def test_sparkline_all_equal_values_no_crash():
    result = ascii_sparkline([5, 5, 5, 5])
    assert len(result) == 4
    assert len(set(result)) == 1  # all the same character


def test_sparkline_increasing_values_increase_in_height():
    result = ascii_sparkline([1, 2, 3, 4, 5])
    from src.daily_summary import SPARK_CHARS
    indices = [SPARK_CHARS.index(c) for c in result]
    assert indices == sorted(indices)
    assert indices[0] == 0
    assert indices[-1] == len(SPARK_CHARS) - 1


def test_sparkline_handles_negative_values():
    result = ascii_sparkline([-5, 0, 5])
    assert len(result) == 3
