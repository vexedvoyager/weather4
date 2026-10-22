"""
Kalshi trading-fee estimate (IMPROVEMENTS.md item #5).

Formula used (Kalshi's published general fee schedule, as widely
documented):

    fee = round_up_to_next_cent( rate * contracts * P * (1 - P) )

where P is the contract price in dollars and rate is 0.07 for the general
schedule. The fee is charged when a position is OPENED; settlement itself
costs nothing extra.

HONEST CAVEAT: this was confirmed from third-party descriptions of the
general fee schedule, not from a weather-specific schedule - Kalshi
publishes some product families with their own rates. Check Kalshi's
official fee schedule and adjust `fees.taker_rate` in config.yaml if
weather markets differ. Paper-trade P&L from v6.0 onward INCLUDES this
estimated fee; trades before v6.0 were recorded without any fee.
"""
import math


def kalshi_fee_cents(count: int, price_cents: int, rate: float = 0.07) -> int:
    """
    Estimated fee in cents for opening `count` contracts at `price_cents`.

    Rounds UP to the next whole cent (Kalshi never rounds a fee down).
    A tiny epsilon is subtracted first so floating-point noise like
    7.000000000000001 doesn't turn an exact 7-cent fee into 8.
    """
    if count <= 0 or rate <= 0:
        return 0
    p = price_cents / 100.0
    fee_dollars = rate * count * p * (1.0 - p)
    return int(math.ceil(fee_dollars * 100.0 - 1e-9))
