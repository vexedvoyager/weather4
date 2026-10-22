"""
v6.1 regression tests built on a REAL NOAA bulletin (tests/fixtures/
nbm_real_2026-10-21T19Z.txt - the first 24 lines of five station blocks,
copied verbatim from the v6.0 column diagnostic).

What the real bulletin taught us (IMPROVEMENTS.md items #19, #24):
  * the layout is 4-character cells, with '|' after every 12Z column;
  * the 12Z TXN columns are overnight MINIMUMS, the 00Z columns are daily
    MAXIMUMS (Chicago: FHR 17 / 12Z = 52F, FHR 29 / 00Z = 70F);
  * v5 and earlier could only read the 12Z columns (a parsing bug), so every
    daily-high market was forecast from an overnight low.
"""
from datetime import date
from pathlib import Path

import pytest

from src import nbm
from src.forecast_refresh import model_probability_for_market
from src.market_parsing import market_date_from_ticker

RUN = "2026-10-21T19Z"
BULLETIN = (Path(__file__).parent / "fixtures" / "nbm_real_2026-10-21T19Z.txt").read_text()

# Printed straight from the real bulletin (TXNP5 row, columns FHR 17, 29, 41, ...)
EXPECTED_P50 = {
    "KMDW": [52, 70, 53, 70, 54, 70, 55, 72, 56, 68, 54, 63, 52, 63, 51, 66, 51, 66],
    "KNYC": [49, 64, 49, 57, 44, 58, 46, 62, 48, 66, 52, 63, 50, 57, 49, 59, 50, 59],
    "KMIA": [71, 83, 71, 83, 73, 83, 74, 82, 73, 83, 74, 83, 74, 84, 74, 85, 74, 85],
    "KAUS": [59, 87, 58, 86, 59, 87, 62, 83, 58, 83, 60, 83, 59, 84, 59, 83, 63, 83],
    "KLAX": [61, 68, 59, 68, 59, 73, 62, 76, 62, 78, 62, 77, 61, 73, 61, 70, 60, 70],
}
# The 00Z column for market date 2026-10-22 (FHR 29): the daily-high forecast.
EXPECTED_OCT22_HIGH = {
    "KMDW": {"p10": 69, "p25": 70, "p50": 70, "p75": 71, "p90": 72},
    "KNYC": {"p10": 62, "p25": 63, "p50": 64, "p75": 64, "p90": 65},
    "KMIA": {"p10": 81, "p25": 82, "p50": 83, "p75": 84, "p90": 86},
    "KAUS": {"p10": 85, "p25": 86, "p50": 87, "p75": 88, "p90": 89},
    "KLAX": {"p10": 67, "p25": 67, "p50": 68, "p75": 69, "p90": 70},
}


@pytest.mark.parametrize("station", sorted(EXPECTED_P50))
def test_every_column_is_parsed_including_00z_and_three_digit_hours(station):
    parsed = nbm.parse_station_maxt(BULLETIN, station)
    assert parsed["forecast_hours"][:4] == [17, 29, 41, 53]
    assert parsed["forecast_hours"][-1] == 221
    assert parsed["p50"] == EXPECTED_P50[station]          # no blanks anywhere
    assert all(v is not None for key in ("p10", "p25", "p50", "p75", "p90") for v in parsed[key])


@pytest.mark.parametrize("station", sorted(EXPECTED_OCT22_HIGH))
def test_daily_high_for_oct22_comes_from_the_00z_column(station):
    parsed = nbm.parse_station_maxt(BULLETIN, station)
    pick = nbm.get_max_temp_forecast(parsed, RUN, date(2026, 10, 22))
    assert pick["forecast_hour"] == 29
    assert nbm.valid_time_utc(RUN, pick["forecast_hour"]).hour == 0     # 00Z = daytime max
    assert pick["values"] == EXPECTED_OCT22_HIGH[station]


def test_max_column_is_far_above_the_min_column_the_old_code_read():
    parsed = nbm.parse_station_maxt(BULLETIN, "KAUS")
    high = nbm.get_max_temp_forecast(parsed, RUN, date(2026, 10, 22))["values"]["p50"]
    # The v5 rule: nearest column to hours-until-close. A market closing 08Z Oct 23 is 37h
    # after the 19Z run (the real Los Angeles case) -> nearest column is FHR 41 = 12Z, an
    # overnight MIN. Even with the parser fixed, that rule picks the wrong column, which is
    # why selection now goes by the market's DATE instead.
    old_pick = nbm.get_forecast_with_meta(parsed, 37)
    assert old_pick["forecast_hour"] == 41
    assert high - old_pick["values"]["p50"] >= 25   # 87F high vs a ~58F overnight low


def test_same_day_market_has_no_column_and_is_never_guessed():
    parsed = nbm.parse_station_maxt(BULLETIN, "KMDW")
    assert nbm.get_max_temp_forecast(parsed, RUN, date(2026, 10, 21)) is None   # its 00Z column is FHR 5
    assert nbm.get_max_temp_forecast(parsed, RUN, None) is None
    assert nbm.get_max_temp_forecast(parsed, RUN, date(2027, 1, 1)) is None     # beyond the bulletin


def test_far_dates_are_readable_now_that_three_digit_hours_parse():
    parsed = nbm.parse_station_maxt(BULLETIN, "KMDW")
    pick = nbm.get_max_temp_forecast(parsed, RUN, date(2026, 10, 28))   # 00Z Oct 29 = FHR 173
    assert pick["forecast_hour"] == 173 and pick["values"]["p50"] == 63


# --- whole-degree settlement -------------------------------------------------

LADDERS = {   # the real open markets for 2026-10-22, from the v6.0 diagnostic output
    "KMDW": [("above", 75), ("below", 68), (70, 71), (72, 73), (74, 75), (68, 69)],
    "KAUS": [("above", 91), ("below", 84), (90, 91), (88, 89), (86, 87), (84, 85)],
    "KLAX": [("above", 71), ("below", 64), (70, 71), (68, 69), (66, 67), (64, 65)],
}


def _threshold(spec):
    if spec[0] == "above":
        return {"kind": "single", "value": float(spec[1]), "direction": "above"}
    if spec[0] == "below":
        return {"kind": "single", "value": float(spec[1]), "direction": "below"}
    return {"kind": "between", "floor": float(spec[0]), "cap": float(spec[1])}


@pytest.mark.parametrize("station", sorted(LADDERS))
def test_a_full_market_ladder_sums_to_one(station):
    """A complete ladder partitions every possible whole-degree high, so the
    raw model probabilities must sum to 1. (Pre-v6.1 they summed to 0.44-0.68.)"""
    parsed = nbm.parse_station_maxt(BULLETIN, station)
    pct = nbm.get_max_temp_forecast(parsed, RUN, date(2026, 10, 22))["values"]
    total = sum(model_probability_for_market(_threshold(s), pct, 1.15)["raw"] for s in LADDERS[station])
    assert abs(total - 1.0) < 0.02


def test_probabilities_peak_on_the_market_bracket_containing_the_forecast():
    parsed = nbm.parse_station_maxt(BULLETIN, "KMDW")
    pct = nbm.get_max_temp_forecast(parsed, RUN, date(2026, 10, 22))["values"]   # median 70
    probs = {s: model_probability_for_market(_threshold(s), pct, 1.15)["raw"] for s in LADDERS["KMDW"]}
    assert max(probs, key=probs.get) == (70, 71)
    assert probs[(70, 71)] > 0.5
    # v5 said 95% for "high < 68" (it was reading a 52F overnight low); now it is tiny.
    assert probs[("below", 68)] < 0.05
    assert probs[("above", 75)] < 0.05


def test_ticker_dates():
    assert market_date_from_ticker("KXHIGHCHI-26OCT22-T75") == date(2026, 10, 22)


# --- end to end: forecast_refresh on the real bulletin + the real open markets ---

def _market(ticker, strike_type, floor=None, cap=None, close="2026-10-23T06:00:00Z"):
    m = {"ticker": ticker, "strike_type": strike_type, "close_time": close}
    if floor is not None:
        m["floor_strike"] = floor
    if cap is not None:
        m["cap_strike"] = cap
    return m


def test_forecast_refresh_end_to_end_on_real_bulletin(tmp_path):
    import json
    from unittest.mock import MagicMock, patch

    from src import db
    from src.forecast_refresh import run_forecast_refresh

    path = str(tmp_path / "t.db")
    db.init_db(path)
    chicago = [   # real Chicago markets for 2026-10-22 (from the v6.0 diagnostic)
        _market("KXHIGHCHI-26OCT22-T75", "greater", floor=75),
        _market("KXHIGHCHI-26OCT22-T68", "less", cap=68),
        _market("KXHIGHCHI-26OCT22-B74.5", "between", floor=74, cap=75),
        _market("KXHIGHCHI-26OCT22-B72.5", "between", floor=72, cap=73),
        _market("KXHIGHCHI-26OCT22-B70.5", "between", floor=70, cap=71),
        _market("KXHIGHCHI-26OCT22-B68.5", "between", floor=68, cap=69),
        _market("KXHIGHCHI-26OCT21-T75", "greater", floor=75, close="2026-10-22T06:00:00Z"),  # same-day
    ]
    cfg = {
        "paths": {"database": path},
        "cities": [{"name": "Chicago", "kalshi_series_prefix": "KXHIGHCHI",
                    "expected_station": "KMDW", "timezone": "America/Chicago"}],
        "probability_model": {"sigma_multiplier": 1.15, "min_forecast_hour": 24},
    }
    client = MagicMock()
    client.get_markets_by_series.return_value = chicago
    with patch("src.forecast_refresh.build_client", return_value=client), \
         patch("src.forecast_refresh.nbm.fetch_latest_bulletin", return_value=(BULLETIN, RUN)), \
         patch("src.forecast_refresh.verify_station", return_value=True):
        run_forecast_refresh(cfg)

    got = {t: db.get_cached_forecast(path, t, 8) for t in (m["ticker"] for m in chicago)}
    # same-day market is rejected by the floor, never guessed
    assert got["KXHIGHCHI-26OCT21-T75"] is None
    # everything else uses the 00Z Oct 23 column (FHR 29): median 70F
    assert abs(got["KXHIGHCHI-26OCT22-B70.5"]["model_prob"] - 0.65) < 1e-6   # P(69.5 < t < 71.5)
    assert got["KXHIGHCHI-26OCT22-B70.5"]["nbm_forecast_hour"] == 29
    assert got["KXHIGHCHI-26OCT22-B70.5"]["nbm_valid_utc"].startswith("2026-10-23T00:00")
    assert json.loads(got["KXHIGHCHI-26OCT22-B70.5"]["ladder_json"])["p50"] == 70
    # v5 gave "high < 68" 95% (it forecast from a 52F overnight low); now it is the floor
    assert got["KXHIGHCHI-26OCT22-T68"]["model_prob"] == 0.05
    assert got["KXHIGHCHI-26OCT22-T75"]["model_prob"] == 0.05
