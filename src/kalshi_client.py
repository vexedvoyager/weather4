"""
Kalshi REST API client.

This version is built against Kalshi's CONFIRMED published schema
(docs.kalshi.com), not guesses. Key facts that shaped this code:

  - Prices are returned as DOLLAR STRINGS, e.g. yes_bid_dollars: "0.5600",
    not plain integer cents. Legacy integer-cent fields still exist
    alongside them but are marked deprecated by Kalshi. We read the
    dollar fields and convert to integer cents ourselves.

  - The orderbook only ever returns BIDS, never asks. This is because in
    a binary market, a YES ask is mathematically the same as $1.00 minus
    the NO bid at that price level (and vice versa). There is no separate
    "ask" array to read.

  - A market's threshold isn't just "whichever strike field is present" -
    it depends on strike_type (greater, greater_or_equal, less,
    less_or_equal, between). Reading the wrong field for a "between"
    market would silently misprice it.

  - The positions endpoint returns "market_positions"/"event_positions" -
    confirmed directly against Kalshi's docs, matching what this project
    defended against from the start.
"""
import logging
import time

import requests

from src.kalshi_auth import KalshiAuth

logger = logging.getLogger(__name__)

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

# Kalshi's rate limiter is a token bucket that refills continuously, with
# no Retry-After header and no cooldown penalty on a 429 - per their docs,
# the very next request usually succeeds once the bucket has refilled
# (often within milliseconds at Basic tier). Exponential backoff here is
# a safety net for the unlikely case of hitting the limit, not something
# expected to trigger often at this bot's request volume.
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1


def dollars_to_cents(dollar_str) -> int:
    """Kalshi returns prices as dollar strings like '0.5600'. Convert to
    integer cents, rounding to the nearest cent."""
    if dollar_str is None:
        return 0
    return round(float(dollar_str) * 100)


class KalshiClient:
    def __init__(self, key_id: str, private_key_pem: str, timeout: int = 15):
        self.auth = KalshiAuth(key_id, private_key_pem)
        self.timeout = timeout

    def _request(self, method: str, path: str, params: dict = None, json_body: dict = None):
        headers = self.auth.sign_request(method, path)
        headers["Content-Type"] = "application/json"
        url = BASE_URL + path

        for attempt in range(MAX_RETRIES + 1):
            resp = requests.request(
                method, url, headers=headers, params=params, json=json_body, timeout=self.timeout
            )
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp.json()

            if attempt == MAX_RETRIES:
                resp.raise_for_status()  # give up, raise the 429 as an error

            wait_seconds = BACKOFF_BASE_SECONDS * (2 ** attempt)
            logger.warning(
                "429 rate limited on %s %s, retrying in %ds (attempt %d/%d). "
                "Kalshi's bucket refills continuously with no penalty, so this "
                "is expected to resolve quickly.",
                method, path, wait_seconds, attempt + 1, MAX_RETRIES,
            )
            time.sleep(wait_seconds)
            # Re-sign the request: the timestamp used in the signature must
            # be current, and a stale timestamp would itself cause a
            # rejection on retry.
            headers = self.auth.sign_request(method, path)
            headers["Content-Type"] = "application/json"

    # --- Market data (read-only, safe in any mode) ---------------------------

    def get_markets_by_series(self, series_ticker: str, status: str = "open") -> list[dict]:
        """
        Fetch open markets under a given series (e.g. weather series for one city).

        CORRECTED: earlier versions used status="active", based on an
        inferred (never independently confirmed) status enum. This
        caused a uniform 400 Bad Request across every single city's
        market fetch - confirmed via a real Forecast Refresh run.
        Kalshi's own official "Quick Start: Market Data" documentation
        (docs.kalshi.com, dated May 2026) uses status="open" in its
        working example - that's the confirmed correct value for
        "currently tradeable" markets.
        """
        path = "/markets"
        params = {"series_ticker": series_ticker, "status": status, "limit": 100}
        data = self._request("GET", path, params=params)
        return data.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        path = f"/markets/{ticker}"
        data = self._request("GET", path)
        return data.get("market", {})

    def get_orderbook(self, ticker: str) -> dict:
        """
        Returns Kalshi's raw orderbook_fp structure:
            {"yes_dollars": [[price_str, count_str], ...],
             "no_dollars":  [[price_str, count_str], ...]}
        Both arrays are BIDS ONLY, sorted ascending by price. See
        src/edge.py / src/main.py for how the complementary ask price is
        derived from this (YES ask = $1.00 - best NO bid, and vice versa).
        """
        path = f"/markets/{ticker}/orderbook"
        data = self._request("GET", path)
        return data.get("orderbook_fp", {"yes_dollars": [], "no_dollars": []})

    def get_series_list(self, category: str = "Climate and Weather") -> list[dict]:
        """
        NOTE: the correct category string is "Climate and Weather".
        "Climate" alone matches nothing on Kalshi's API and silently
        returns a near-empty result - a documented bug in earlier bot
        versions this project drew on.
        """
        path = "/series"
        params = {"category": category}
        data = self._request("GET", path, params=params)
        return data.get("series", [])

    def get_historical_markets(self, series_ticker: str, limit: int = 200, cursor: str = None) -> dict:
        """
        Settled/historical markets from Kalshi's historical-tier endpoint.
        Not currently used by any active part of the bot (the backtest
        feature that used this was removed - see IMPROVEMENTS.md for why),
        kept here as a working, tested utility in case it's useful again.
        Returns the raw response (markets + cursor) so callers can paginate.
        """
        path = "/historical/markets"
        params = {"series_ticker": series_ticker, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", path, params=params)

    # --- Portfolio / positions (read-only) ------------------------------------

    def get_positions(self) -> list[dict]:
        """
        Returns market-level positions. Confirmed against Kalshi's published
        schema: the response keys really are "market_positions" and
        "event_positions". We still guard with .get() and log loudly if
        that ever stops being true (e.g. after an API version change),
        rather than trusting it silently forever.
        """
        path = "/portfolio/positions"
        response = self._request("GET", path)

        if "market_positions" not in response:
            logger.warning(
                "get_positions: 'market_positions' key missing from response "
                "(keys were: %s) - Kalshi's schema may have changed. "
                "Treating as zero positions, which could be WRONG.",
                list(response.keys()),
            )
        positions = response.get("market_positions", [])
        logger.debug("get_positions market_position_count=%d", len(positions))
        return positions

    # --- Orders (WRITE - only ever called in live mode, gated by caller) ------

    def place_order(
        self, ticker: str, side: str, action: str, count: int,
        yes_price_dollars: str = None, no_price_dollars: str = None,
        order_type: str = "limit", client_order_id: str = None,
    ) -> dict:
        """
        Places a real order. side: "yes" or "no". action: "buy" or "sell".
        Prices are dollar strings (e.g. "0.56"), matching Kalshi's current
        schema.

        Caller is responsible for ensuring this is only invoked when
        config.mode == "live" - this client does not check mode itself,
        by design, so that a paper-mode bug can never accidentally
        reach this method (see src/paper_broker.py for the paper path).
        """
        path = "/portfolio/orders"
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
        }
        if yes_price_dollars is not None:
            body["yes_price_dollars"] = yes_price_dollars
        if no_price_dollars is not None:
            body["no_price_dollars"] = no_price_dollars
        if client_order_id:
            body["client_order_id"] = client_order_id

        logger.info("place_order LIVE ticker=%s side=%s action=%s count=%d",
                    ticker, side, action, count)
        return self._request("POST", path, json_body=body)
