"""Tests for the v6.0 NBM column diagnostic (IMPROVEMENTS.md item #19)."""
from datetime import date

from src import nbm
from src.diagnose_columns import (
    classify_local_hour, column_table, market_date_from_ticker,
    median_by_utc_hour, render_market_report, render_station_report,
)

COL = 4


def _row(label, values):
    return f"{label:<6}" + "".join(("" if v is None else str(v)).rjust(COL) for v in values)


def _alternating_bulletin(station="KAUS"):
    """01Z run; 12h ladder 23,35,47,59: 23/47 -> 00Z (daily MAX ~96),
    35/59 -> 12Z (overnight MIN ~72) - the suspected real-world layout."""
    fhrs = [23, 35, 47, 59]
    maxes, mins = 96, 72
    def col(offset):
        return [maxes + offset, mins + offset, maxes + offset, mins + offset]
    lines = [
        f" {station}    NBM V5.0 NBP GUIDANCE    9/03/2026  0100 UTC",
        _row("UTC", [h_utc for h_utc in (0, 12, 0, 12)]),
        _row("FHR", fhrs),
        _row("TXNP1", col(-6)), _row("TXNP2", col(-3)), _row("TXNP5", col(0)),
        _row("TXNP7", col(3)), _row("TXNP9", col(6)),
    ]
    return "\n".join(lines)


def test_market_date_from_ticker():
    assert market_date_from_ticker("KXHIGHCHI-26SEP24-T66") == date(2026, 9, 24)
    assert market_date_from_ticker("KXHIGHNY-26OCT05-B85.5") == date(2026, 10, 5)
    assert market_date_from_ticker("garbage") is None
    assert market_date_from_ticker("KXHIGHX-26XXX24-T1") is None


def test_classify_local_hour():
    assert classify_local_hour(19).startswith("AFTERNOON")
    assert classify_local_hour(7).startswith("MORNING")
    assert classify_local_hour(0) == "OTHER"


def test_valid_time_utc_adds_forecast_hour_to_run():
    dt = nbm.valid_time_utc("2026-09-03T01Z", 23)
    assert (dt.month, dt.day, dt.hour) == (9, 4, 0)


def test_column_table_and_median_by_utc_hour_expose_max_min_gap():
    parsed = nbm.parse_station_maxt(_alternating_bulletin(), "KAUS")
    rows = column_table(parsed, "2026-09-03T01Z", "America/Chicago")
    assert [r["valid_utc"].hour for r in rows] == [0, 12, 0, 12]
    by_hour = median_by_utc_hour(rows)
    assert by_hour[0] == [96, 96] and by_hour[12] == [72, 72]


def test_station_report_flags_the_max_min_gap():
    lines, parsed = render_station_report(
        "Austin", "KAUS", "America/Chicago", _alternating_bulletin(), "2026-09-03T01Z")
    text = "\n".join(lines)
    assert parsed is not None
    assert "RAW bulletin lines" in text
    assert "TXNP5" in text                      # raw row label shown
    assert "24.0F higher" in text               # 96 vs 72
    assert "mixing daily MAX and overnight MIN" in text


def test_station_report_handles_missing_station():
    lines, parsed = render_station_report(
        "Nowhere", "KZZZ", "UTC", _alternating_bulletin(), "2026-09-03T01Z")
    assert parsed is None
    assert "NOT FOUND" in "\n".join(lines)


class _FakeClient:
    def __init__(self, markets):
        self._markets = markets

    def get_markets_by_series(self, prefix, status="open"):
        return self._markets


def test_market_report_shows_new_pick_vs_old_pick():
    parsed = nbm.parse_station_maxt(_alternating_bulletin(), "KAUS")
    cfg = {"probability_model": {"sigma_multiplier": 1.15}}
    city_cfg = {"name": "Austin", "kalshi_series_prefix": "KXHIGHAUS", "timezone": "America/Chicago"}
    # 01Z Sep 3 run. Market date Sep 3 -> NEW pick = 00Z Sep 4 = FHR 23 (the daily MAX, 96F).
    # Close 12:00Z Sep 4 is 35h out -> the OLD rule picked FHR 35 (12Z, the overnight MIN, 72F).
    markets = [{"ticker": "KXHIGHAUS-26SEP03-T90", "close_time": "2026-09-04T12:00:00Z",
                "strike_type": "greater", "floor_strike": 90}]
    text = "\n".join(render_market_report(_FakeClient(markets), city_cfg, parsed, "2026-09-03T01Z", cfg))
    assert "NEW: FHR 23" in text and "p50=96" in text
    assert "OLD: FHR 35" in text and "p50=72" in text
    assert '"old_was_min_column": 1' in text and '"old_differs_from_new": 1' in text
    assert "model_P(YES)=0.875" in text    # P(high >= 91F): boundary 90.5 sits just above p10=90
