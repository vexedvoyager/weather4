"""
Composite edge score. A candidate trade must clear ALL of:
  - a minimum probability gap (model vs. market), AND
  - a minimum composite score across four weighted signals

before it's eligible to trade. In practice most candidates should be
rejected - that's by design, not a bug. Weights (30/20/25/25) match the
methodology described on the Predict & Profit product pages, which this
project draws on for structure while building the implementation fresh.

IMPORTANT: Kalshi's orderbook only returns BIDS (see src/kalshi_client.py).
There is no separate "ask" array. The ask price on one side is derived as
$1.00 minus the best bid on the OTHER side:
    YES ask = $1.00 - (best NO bid)
    NO ask  = $1.00 - (best YES bid)
This module expects prices already in integer cents (post-conversion from
Kalshi's dollar strings) and bid sizes already extracted from the raw
orderbook - see build_market_snapshot() in src/main.py for that plumbing.
"""
from dataclasses import dataclass


@dataclass
class MarketSnapshot:
    ticker: str
    yes_bid_cents: int          # best YES bid, straight from Kalshi
    yes_ask_cents: int          # DERIVED: 100 - best NO bid
    no_bid_cents: int           # best NO bid, straight from Kalshi
    no_ask_cents: int           # DERIVED: 100 - best YES bid
    volume_24h: int
    yes_bid_size: int           # total resting size at best YES bid
    no_bid_size: int            # total resting size at best NO bid


def spread_score(snap: MarketSnapshot) -> float:
    """Tighter spread -> higher score. Normalized against a 10-cent-wide spread as 'bad'."""
    spread = max(snap.yes_ask_cents - snap.yes_bid_cents, 0)
    return max(0.0, 1.0 - spread / 10.0)


def volume_score(snap: MarketSnapshot, min_volume: int) -> float:
    """Scales from 0 at min_volume down to 1 at 5x min_volume, capped at 1.0."""
    if snap.volume_24h < min_volume:
        return 0.0
    return min(1.0, (snap.volume_24h - min_volume) / (4 * min_volume) + 0.2)


def imbalance_score(snap: MarketSnapshot, want_side: str) -> float:
    """
    want_side: "yes" or "no" - which side the model wants to buy.

    Since we only have resting BID sizes (no asks), this reads as: does the
    order book show more resting demand on our intended side than the
    other side? More YES-bid size relative to NO-bid size is a soft signal
    that other traders are leaning the same direction we are.
    """
    total = snap.yes_bid_size + snap.no_bid_size
    if total == 0:
        return 0.0
    if want_side == "yes":
        return snap.yes_bid_size / total
    else:
        return snap.no_bid_size / total


def mispricing_score(model_prob: float, market_price_cents: int, max_gap: float = 0.30) -> float:
    """Distance between model probability and market-implied probability, normalized."""
    market_prob = market_price_cents / 100.0
    gap = abs(model_prob - market_prob)
    return min(1.0, gap / max_gap)


def composite_score(
    snap: MarketSnapshot, model_prob: float, want_side: str,
    min_volume: int, weights: dict,
) -> dict:
    """
    Returns a dict with each component score plus the weighted composite,
    so the caller can log the full breakdown (not just the final number) -
    a plain "rejected" log line is much less useful than seeing which
    factor failed.
    """
    s_spread = spread_score(snap)
    s_volume = volume_score(snap, min_volume)
    s_imbalance = imbalance_score(snap, want_side)
    price_for_mispricing = snap.yes_ask_cents if want_side == "yes" else snap.no_ask_cents
    s_mispricing = mispricing_score(model_prob, price_for_mispricing)

    composite = (
        weights["spread"] * s_spread
        + weights["volume"] * s_volume
        + weights["imbalance"] * s_imbalance
        + weights["mispricing"] * s_mispricing
    )

    return {
        "spread": round(s_spread, 4),
        "volume": round(s_volume, 4),
        "imbalance": round(s_imbalance, 4),
        "mispricing": round(s_mispricing, 4),
        "composite": round(composite, 4),
    }
