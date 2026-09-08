"""
Regression tests for the fixed-width column alignment bug found while
verifying v2.0: naive whitespace-splitting of NBM bulletin rows silently
shifts values into the wrong forecast-hour column whenever a row has
blank cells (which MaxT percentile rows do, by design - they only print
a value at the relevant forecast hour, not every column).

These tests build bulletins programmatically with exact column widths
so alignment is guaranteed by construction, not by eyeballing spacing.
"""
from src.nbm import parse_station_maxt, get_forecast_for_target_hour

COL_WIDTH = 4


def _fmt_row(label, values):
    out = f"{label:<6}"
    for v in values:
        cell = "" if v is None else str(v)
        out += cell.rjust(COL_WIDTH)
    return out


def _build_bulletin(station_id, fhrs, percentiles: dict):
    utcs = [h % 24 for h in fhrs]
    lines = [
        f"{station_id} NBM V5.0 NBP GUIDANCE 8/24/2026 1300 UTC",
        _fmt_row("UTC", utcs),
        _fmt_row("FHR", fhrs),
        _fmt_row("TXNP1", percentiles["p10"]),
        _fmt_row("TXNP2", percentiles["p25"]),
        _fmt_row("TXNP5", percentiles["p50"]),
        _fmt_row("TXNP7", percentiles["p75"]),
        _fmt_row("TXNP9", percentiles["p90"]),
    ]
    return "\n".join(lines)


def test_sparse_columns_do_not_shift_values_into_wrong_hour():
    """
    The core regression: percentile values present at only SOME forecast
    hours (realistic - MaxT is only meaningful once a day) must land in
    the correct column, not get compacted leftward by a naive split().
    """
    fhrs = [11, 17, 23, 29, 35, 41, 47, 53]
    percentiles = {
        "p10": [None, None, None, None, 68, None, None, 65],
        "p25": [None, None, None, None, 71, None, None, 68],
        "p50": [None, None, None, None, 75, None, None, 72],
        "p75": [None, None, None, None, 79, None, None, 76],
        "p90": [None, None, None, None, 83, None, None, 80],
    }
    bulletin = _build_bulletin("KMDW", fhrs, percentiles)
    parsed = parse_station_maxt(bulletin, "KMDW")

    assert parsed is not None
    result = get_forecast_for_target_hour(parsed, target_forecast_hour=35)
    assert result == {"p10": 68, "p25": 71, "p50": 75, "p75": 79, "p90": 83}


def test_genuinely_blank_column_returns_none_not_a_neighboring_value():
    fhrs = [11, 17, 23, 29, 35, 41, 47, 53]
    percentiles = {
        "p10": [None, None, None, None, 68, None, None, 65],
        "p25": [None, None, None, None, 71, None, None, 68],
        "p50": [None, None, None, None, 75, None, None, 72],
        "p75": [None, None, None, None, 79, None, None, 76],
        "p90": [None, None, None, None, 83, None, None, 80],
    }
    bulletin = _build_bulletin("KMDW", fhrs, percentiles)
    parsed = parse_station_maxt(bulletin, "KMDW")

    result = get_forecast_for_target_hour(parsed, target_forecast_hour=11, tolerance_hours=0)
    assert result is None


def test_two_digit_and_three_digit_values_both_align_correctly():
    """Right-justified values of different widths (e.g. a cold snap giving
    a negative or the occasional 3-digit heat value) must still land in
    their correct column."""
    fhrs = [11, 35, 59]
    percentiles = {
        "p10": [-5, 68, 101],
        "p25": [-2, 71, 104],
        "p50": [1, 75, 108],
        "p75": [4, 79, 111],
        "p90": [7, 83, 114],
    }
    bulletin = _build_bulletin("KMDW", fhrs, percentiles)
    parsed = parse_station_maxt(bulletin, "KMDW")

    assert get_forecast_for_target_hour(parsed, 11) == {
        "p10": -5, "p25": -2, "p50": 1, "p75": 4, "p90": 7,
    }
    assert get_forecast_for_target_hour(parsed, 59) == {
        "p10": 101, "p25": 104, "p50": 108, "p75": 111, "p90": 114,
    }


def test_multiple_stations_in_one_bulletin_do_not_bleed_into_each_other():
    fhrs = [11, 35]
    station_a_pct = {
        "p10": [60, 62], "p25": [63, 65], "p50": [67, 69], "p75": [70, 72], "p90": [73, 75],
    }
    station_b_pct = {
        "p10": [40, 42], "p25": [43, 45], "p50": [47, 49], "p75": [50, 52], "p90": [53, 55],
    }
    bulletin = (
        _build_bulletin("KMDW", fhrs, station_a_pct) + "\n" +
        _build_bulletin("KORD", fhrs, station_b_pct)
    )

    parsed_mdw = parse_station_maxt(bulletin, "KMDW")
    parsed_ord = parse_station_maxt(bulletin, "KORD")

    assert get_forecast_for_target_hour(parsed_mdw, 11)["p50"] == 67
    assert get_forecast_for_target_hour(parsed_ord, 11)["p50"] == 47


def test_missing_percentile_field_returns_none_not_partial_data():
    """If even one of the five expected percentile rows is missing
    entirely, we should refuse to return partial data rather than
    silently compute a probability off four percentiles."""
    fhrs = [11, 35]
    lines = [
        "KMDW NBM V5.0 NBP GUIDANCE 8/24/2026 1300 UTC",
        _fmt_row("UTC", [h % 24 for h in fhrs]),
        _fmt_row("FHR", fhrs),
        _fmt_row("TXNP1", [68, 70]),
        _fmt_row("TXNP2", [71, 73]),
        _fmt_row("TXNP5", [75, 77]),
        # TXNP7 (75th) intentionally omitted
        _fmt_row("TXNP9", [83, 85]),
    ]
    bulletin = "\n".join(lines)

    parsed = parse_station_maxt(bulletin, "KMDW")
    assert parsed is None


def test_station_not_found_returns_none():
    bulletin = _build_bulletin("KMDW", [11], {
        "p10": [68], "p25": [71], "p50": [75], "p75": [79], "p90": [83],
    })
    assert parse_station_maxt(bulletin, "KXYZ") is None


def test_real_leading_space_format_is_parsed_correctly():
    """
    Regression test using the EXACT header format confirmed from a real
    live NOAA bulletin fetch (not synthetic): station lines have a
    leading space before the station code, e.g. ' KMDW    NBM V5.0 NBP
    GUIDANCE...'. The original regex anchored the station code to the
    exact start of the line and would have silently matched nothing
    against real data - this is what that live check caught.

    Uses the same precise _build_bulletin helper as the other tests in
    this file (for guaranteed column alignment), with a leading space
    added to the header line specifically, isolating exactly the fix
    that was made.
    """
    bulletin = _build_bulletin("KMDW", [11, 35], {
        "p10": [68, None], "p25": [71, None], "p50": [75, None],
        "p75": [79, None], "p90": [83, None],
    })
    # Simulate the real confirmed format: a leading space before the
    # station code on the header line only.
    bulletin = bulletin.replace("KMDW NBM", " KMDW NBM", 1)

    parsed = parse_station_maxt(bulletin, "KMDW")
    assert parsed is not None
    result = get_forecast_for_target_hour(parsed, target_forecast_hour=11)
    assert result == {"p10": 68, "p25": 71, "p50": 75, "p75": 79, "p90": 83}
