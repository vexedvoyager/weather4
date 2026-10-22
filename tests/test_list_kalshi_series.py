"""Tests for the v6.0 Kalshi series discovery script (IMPROVEMENTS.md item #23)."""
from src.list_kalshi_series import (
    candidate_snippet, extract_cli_code, format_rows, icao_from_cli,
    inspect_series, is_daily_high_series,
)

COL = 4


def _row(label, values):
    return f"{label:<6}" + "".join(("" if v is None else str(v)).rjust(COL) for v in values)


def _bulletin(station):
    fhrs = [23, 35]
    return "\n".join([
        f" {station}    NBM V5.0 NBP GUIDANCE    9/03/2026  0100 UTC",
        _row("UTC", [0, 12]), _row("FHR", fhrs),
        _row("TXNP1", [80, 60]), _row("TXNP2", [82, 62]), _row("TXNP5", [85, 65]),
        _row("TXNP7", [88, 68]), _row("TXNP9", [90, 70]),
    ])


class _Client:
    def __init__(self, markets, detail):
        self._markets, self._detail = markets, detail

    def get_markets_by_series(self, series, status="open"):
        return self._markets

    def get_market(self, ticker):
        return self._detail


def test_cli_extraction_and_icao():
    assert extract_cli_code("Resolves per the CLIDEN climate report") == "DEN"
    assert extract_cli_code("no code here") is None
    assert icao_from_cli("DEN") == "KDEN"
    assert icao_from_cli(None) is None


def test_is_daily_high_series():
    assert is_daily_high_series({"ticker": "KXHIGHDEN"})
    assert not is_daily_high_series({"ticker": "KXRAINNYC"})


def test_inspect_series_finds_station_in_nbm():
    client = _Client([{"ticker": "KXHIGHDEN-26OCT22-T70"}],
                     {"rules_primary": "Per the NWS CLIDEN report for Denver"})
    row = inspect_series(client, {"ticker": "KXHIGHDEN", "title": "Highest temperature in Denver"},
                         _bulletin("KDEN"), configured={})
    assert row["icao"] == "KDEN" and row["in_nbm"] is True and row["nbm_parses"] is True


def test_inspect_series_flags_station_missing_from_nbm():
    client = _Client([{"ticker": "X-1"}], {"rules_primary": "CLIZZZ"})
    row = inspect_series(client, {"ticker": "KXHIGHZZZ", "title": "t"}, _bulletin("KDEN"), {})
    assert row["in_nbm"] is False and row["nbm_parses"] is False


def test_inspect_series_handles_no_open_markets_and_fetch_errors():
    row = inspect_series(_Client([], {}), {"ticker": "KXHIGHQQQ", "title": "t"}, "", {})
    assert row["open_markets"] == 0 and "no open markets" in row["note"]

    class _Boom:
        def get_markets_by_series(self, *a, **k):
            raise RuntimeError("boom")
    row = inspect_series(_Boom(), {"ticker": "KXHIGHQQQ", "title": "t"}, "", {})
    assert "market fetch failed" in row["note"]


def test_snippet_only_includes_viable_unconfigured_cities():
    client = _Client([{"ticker": "KXHIGHDEN-26OCT22-T70"}], {"rules_primary": "CLIDEN"})
    viable = inspect_series(client, {"ticker": "KXHIGHDEN", "title": "Highest temperature in Denver"},
                            _bulletin("KDEN"), {})
    configured = inspect_series(client, {"ticker": "KXHIGHDEN", "title": "Denver"},
                                _bulletin("KDEN"), {"KXHIGHDEN": "Denver"})
    text = "\n".join(candidate_snippet([viable]))
    assert 'kalshi_series_prefix: "KXHIGHDEN"' in text and 'expected_station: "KDEN"' in text
    assert "FILL_IN" in text
    assert candidate_snippet([configured]) == []
    assert len(format_rows([viable])) == 2
