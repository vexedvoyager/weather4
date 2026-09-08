"""
Verifies settlement stations against Kalshi's own market metadata rather
than trusting a hardcoded city->airport guess.

Why this matters: Kalshi settles Chicago temperature contracts on Midway
(KMDW), not O'Hare (KORD). Houston settles on Hobby (KHOU), not Bush
Intercontinental. Getting this wrong means forecasting the wrong station
entirely while looking completely fine in the logs.

config.yaml's "expected_station" is a starting guess. This module confirms
it against the live market's settlement details before the bot ever prices
a contract for that city. If verification fails, the city is skipped for
that run (logged clearly) rather than traded on an unconfirmed guess.

CONFIRMED against real settlement text from a live Forecast Refresh run:
Kalshi's rules text does NOT use the plain ICAO code. It uses its own
identifier, consistently formatted as "CLI" + the ICAO code with its
leading "K" dropped - e.g. KMDW -> "CLIMDW", KNYC -> "CLINYC". This
pattern was confirmed identically across all 5 configured cities in one
real run, not inferred from documentation. The original check (looking
for the raw ICAO code) would never match this format, which is why every
single city was being skipped despite the mapping actually being correct.
"""
import logging

from src.kalshi_client import KalshiClient

logger = logging.getLogger(__name__)


def _kalshi_style_identifier(station_id: str) -> str | None:
    """
    Converts a 4-letter ICAO code (e.g. 'KMDW') into Kalshi's own
    settlement-text identifier style (e.g. 'CLIMDW'), confirmed against
    real settlement text. Returns None if station_id doesn't match the
    expected 4-letter-starting-with-K shape, so callers can skip this
    check rather than construct a nonsense string.
    """
    if len(station_id) == 4 and station_id.upper().startswith("K"):
        return "CLI" + station_id[1:].upper()
    return None


def verify_station(client: KalshiClient, market_ticker: str, expected_station: str) -> bool:
    """
    Pulls a market's settlement details and checks whether expected_station
    appears in it - trying both the raw ICAO code AND Kalshi's own
    "CLI"-prefixed identifier style, since real settlement text uses the
    latter, not the former.

    Returns True if confirmed, False otherwise (and logs why).
    """
    try:
        market = client.get_market(market_ticker)
    except Exception as e:
        logger.warning(
            "station_verify_failed ticker=%s reason=api_error error=%s",
            market_ticker, e,
        )
        return False

    settlement_source = str(market.get("settlement_sources", market.get("rules_primary", "")))
    settlement_upper = settlement_source.upper()

    candidates = [expected_station.upper()]
    kalshi_style = _kalshi_style_identifier(expected_station)
    if kalshi_style:
        candidates.append(kalshi_style)

    for candidate in candidates:
        if candidate in settlement_upper:
            logger.debug(
                "station_verify_ok ticker=%s station=%s matched_as=%s",
                market_ticker, expected_station, candidate,
            )
            return True

    logger.warning(
        "station_verify_MISMATCH ticker=%s expected=%s (tried: %s) settlement_source=%r — "
        "skipping this city until mapping is confirmed manually",
        market_ticker, expected_station, candidates, settlement_source,
    )
    return False
