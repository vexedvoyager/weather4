"""
Paper-trading broker. Simulates order fills using real market prices, but
never calls KalshiClient.place_order. This module physically cannot place a
real order - there is no code path in here that reaches the write endpoint.

This is the default and recommended mode while you're validating the bot.
"""
import logging

logger = logging.getLogger(__name__)


class PaperBroker:
    def __init__(self):
        pass

    def simulate_fill(
        self, ticker: str, side: str, count: int, market_price_cents: int
    ) -> dict:
        """
        Simulates buying `count` contracts of `side` at the current market
        price. Assumes a fill at the displayed price - a simplification vs.
        real execution (no slippage modeling yet), which is a known and
        documented limitation, not a hidden one.
        """
        cost_cents = count * market_price_cents
        logger.info(
            "paper_fill ticker=%s side=%s count=%d price_cents=%d cost_cents=%d",
            ticker, side, count, market_price_cents, cost_cents,
        )
        return {
            "ticker": ticker,
            "side": side,
            "count": count,
            "fill_price_cents": market_price_cents,
            "cost_cents": cost_cents,
            "simulated": True,
        }
