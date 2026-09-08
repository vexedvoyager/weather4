from unittest.mock import MagicMock

from src.stations import verify_station, _kalshi_style_identifier


def _client_returning(rules_primary_text: str) -> MagicMock:
    client = MagicMock()
    client.get_market.return_value = {"rules_primary": rules_primary_text}
    return client


def test_kalshi_style_identifier_conversion():
    assert _kalshi_style_identifier("KMDW") == "CLIMDW"
    assert _kalshi_style_identifier("KNYC") == "CLINYC"
    assert _kalshi_style_identifier("KMIA") == "CLIMIA"
    assert _kalshi_style_identifier("KAUS") == "CLIAUS"
    assert _kalshi_style_identifier("KLAX") == "CLILAX"


def test_kalshi_style_identifier_returns_none_for_non_icao_shape():
    assert _kalshi_style_identifier("XYZ") is None
    assert _kalshi_style_identifier("MDW") is None  # doesn't start with K
    assert _kalshi_style_identifier("") is None


def test_verify_station_matches_real_kalshi_settlement_text_chicago():
    """
    Regression test using the EXACT real settlement text confirmed from a
    live Forecast Refresh run. The original check looked for the raw
    ICAO code "KMDW", which never appears in Kalshi's actual rules text -
    it uses "CLIMDW" instead. This is what caused every single city to be
    incorrectly skipped despite the station mapping being correct.
    """
    real_text = (
        "If the maximum temperature recorded at Chicago (CLIMDW) for Sep 3, "
        "2026, is greater than 95° fahrenheit according to The Weather "
        "Company, then the market resolves to Yes."
    )
    client = _client_returning(real_text)
    assert verify_station(client, "KXHIGHCHI-26SEP03-T95", "KMDW") is True


def test_verify_station_matches_real_kalshi_settlement_text_all_five_cities():
    real_texts = {
        "KMDW": "...at Chicago (CLIMDW) for Sep 3, 2026, is greater than 95...",
        "KNYC": "...at New York City (CLINYC) for Sep 3, 2026, is greater than 90...",
        "KMIA": "...at Miami (CLIMIA) for Sep 3, 2026, is greater than 94...",
        "KAUS": "...at Austin (CLIAUS) for Sep 3, 2026, is less than 95...",
        "KLAX": "...at Los Angeles (CLILAX) for Sep 3, 2026, is greater than 79...",
    }
    for station, text in real_texts.items():
        client = _client_returning(text)
        assert verify_station(client, "TEST-TICKER", station) is True, (
            f"failed to verify {station} against real text: {text!r}"
        )


def test_verify_station_still_matches_raw_icao_code_as_fallback():
    """Some settlement text might use the plain ICAO code directly - the
    fix should not have removed support for that, only added the new
    CLI-style check alongside it."""
    client = _client_returning("Settled using data from KMDW station directly.")
    assert verify_station(client, "TEST-TICKER", "KMDW") is True


def test_verify_station_genuine_mismatch_still_fails():
    """A real wrong-station mismatch (e.g. O'Hare instead of Midway)
    should still correctly fail, not be masked by the new check."""
    client = _client_returning("Settled using data from CLIORD station.")
    assert verify_station(client, "TEST-TICKER", "KMDW") is False


def test_verify_station_api_error_returns_false():
    client = MagicMock()
    client.get_market.side_effect = Exception("500 server error")
    assert verify_station(client, "TEST-TICKER", "KMDW") is False
