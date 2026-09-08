"""
Turns raw Kalshi market + orderbook responses into the clean inputs the
rest of the bot needs: a threshold temperature, and a MarketSnapshot with
real bid/ask prices in cents.

Split into its own module because both pieces have a "looks obvious but
isn't" quality that's worth documenting close to the code:
  - which strike field to trust depends on strike_type
  - Kalshi's orderbook only has bids; asks must be derived
"""
import logging

from src.edge import MarketSnapshot
from src.kalshi_client import dollars_to_cents

logger = logging.getLogger(__name__)


def extract_threshold(market: dict) -> dict | None:
    """
    Returns {"kind": "single", "value": float, "direction": "above"|"below"}
    for greater/greater_or_equal/less/less_or_equal markets, or
    {"kind": "between", "floor": float, "cap": float} for between markets.

    The "direction" field is critical and was MISSING in earlier versions -
    a real, confirmed bug found by comparing actual trade output against
    real Kalshi settlement text. Without it, callers could not distinguish
    a "greater than X" market from a "less than X" market once this
    function returned, and the probability calculation downstream always
    computed "chance of exceeding X" regardless of which type of contract
    it actually was - inverting the model's confidence for every single
    "less than" market (e.g. reporting 75% confidence in a "less than 88"
    contract when the model actually meant 75% chance of EXCEEDING 88,
    i.e. only ~25% chance the "less than" contract itself resolves YES).

    Returns None if strike_type is missing or unrecognized - skip the
    market rather than guess.
    """
    strike_type = market.get("strike_type")
    floor_strike = market.get("floor_strike")
    cap_strike = market.get("cap_strike")

    if strike_type in ("greater", "greater_or_equal"):
        if floor_strike is None:
            return None
        return {"kind": "single", "value": float(floor_strike), "direction": "above"}

    elif strike_type in ("less", "less_or_equal"):
        if cap_strike is None:
            return None
        return {"kind": "single", "value": float(cap_strike), "direction": "below"}

    elif strike_type == "between":
        if floor_strike is None or cap_strike is None:
            return None
        return {"kind": "between", "floor": float(floor_strike), "cap": float(cap_strike)}

    else:
        logger.debug("extract_threshold: unrecognized/missing strike_type=%r", strike_type)
        return None


def describe_threshold(threshold: dict) -> str:
    """
    Converts a threshold dict into a plain-English description for
    display in logs/summaries, e.g. "high > 95°F", "high < 88°F", or
    "high 85-86°F" - so a daily summary or log line is readable on its
    own, without needing to cross-reference Kalshi's settlement text
    (which is exactly what was needed, manually, to catch the
    above/below inversion bug this same threshold dict was involved in).
    """
    if threshold["kind"] == "single":
        symbol = ">" if threshold["direction"] == "above" else "<"
        return f"high {symbol} {threshold['value']:g}°F"
    else:  # "between"
        floor = threshold["floor"]
        cap = threshold["cap"]
        return f"high {floor:g}-{cap:g}°F"


def build_market_snapshot(ticker: str, market: dict, orderbook: dict) -> MarketSnapshot | None:
    """
    market: raw response from KalshiClient.get_market()
    orderbook: raw orderbook_fp dict from KalshiClient.get_orderbook(),
               i.e. {"yes_dollars": [[price, count], ...], "no_dollars": [...]}
               Both arrays are BIDS ONLY, ascending by price.
    """
    try:
        yes_bid_cents = dollars_to_cents(market.get("yes_bid_dollars"))
        no_bid_cents = dollars_to_cents(market.get("no_bid_dollars"))
        volume_24h = int(float(market.get("volume_24h_fp", market.get("volume_fp", "0")) or 0))
    except (TypeError, ValueError) as e:
        logger.debug("build_market_snapshot: could not parse market fields for %s: %s", ticker, e)
        return None

    # Asks are derived, not given directly - see module docstring.
    yes_ask_cents = 100 - no_bid_cents
    no_ask_cents = 100 - yes_bid_cents

    yes_levels = orderbook.get("yes_dollars", []) or []
    no_levels = orderbook.get("no_dollars", []) or []

    yes_bid_size = _best_level_size(yes_levels)
    no_bid_size = _best_level_size(no_levels)

    return MarketSnapshot(
        ticker=ticker,
        yes_bid_cents=yes_bid_cents,
        yes_ask_cents=yes_ask_cents,
        no_bid_cents=no_bid_cents,
        no_ask_cents=no_ask_cents,
        volume_24h=volume_24h,
        yes_bid_size=yes_bid_size,
        no_bid_size=no_bid_size,
    )


def _best_level_size(levels: list) -> int:
    """
    levels: list of [price_str, count_str] pairs, ascending by price.
    The BEST bid is the highest price, i.e. the LAST element per Kalshi's
    docs (ascending order).
    """
    if not levels:
        return 0
    try:
        _, count_str = levels[-1]
        return int(float(count_str))
    except (ValueError, IndexError):
        return 0
