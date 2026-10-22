"""
Market observations (v6.0, IMPROVEMENTS.md item #22).

For EVERY configured market - traded cities and shadow-only cities alike -
record ONE snapshot of "what did the model say, and what did the market say"
the first time Price Check sees a fresh cached forecast for that ticker.
Later, settle_check fills in the outcome.

Why this exists: the `trades` table only contains markets where the model
disagreed with the market enough to trade. That is a heavily selected
sample - exactly the cases where the model is most likely to be wrong -
so Brier scores computed from it are not an honest scorecard for the model.
This table records the whole population, so model Brier and market Brier
can be compared fairly, per city, with no trades required. It is also how
shadow-only cities accumulate evidence without risking anything.
"""
import logging

from src import db
from src.market_parsing import build_market_snapshot

logger = logging.getLogger("observations")


def record_market_observations(cfg: dict, client, db_path: str) -> dict:
    """
    Returns {"recorded": N, "already_seen": N, "no_forecast": N, "errors": N}.
    Never raises on a per-market problem - one bad market must not stop the
    pass (or the trading logic that runs after it).
    """
    stats = {"recorded": 0, "already_seen": 0, "no_forecast": 0, "errors": 0}
    max_age = cfg["operations"]["forecast_cache_max_age_hours"]

    city_entries = [(c, False) for c in cfg["cities"]]
    city_entries += [(c, True) for c in (cfg.get("shadow_cities") or [])]

    for city_cfg, is_shadow in city_entries:
        city = city_cfg["name"]
        try:
            markets = client.get_markets_by_series(city_cfg["kalshi_series_prefix"])
        except Exception as e:
            logger.error("observations city=%s could not fetch markets: %s", city, e)
            stats["errors"] += 1
            continue

        for market in markets:
            ticker = market.get("ticker")
            if not ticker:
                continue
            if db.has_market_observation(db_path, ticker):
                stats["already_seen"] += 1
                continue

            cached = db.get_cached_forecast(db_path, ticker, max_age)
            if cached is None:
                stats["no_forecast"] += 1
                continue

            try:
                orderbook = client.get_orderbook(ticker)
                snap = build_market_snapshot(ticker, market, orderbook)
            except Exception as e:
                logger.debug("observations ticker=%s orderbook failed: %s", ticker, e)
                stats["errors"] += 1
                continue
            if snap is None:
                stats["errors"] += 1
                continue

            wrote = db.insert_market_observation(
                db_path, ticker, city, is_shadow,
                cached.get("threshold_description"), cached["model_prob"],
                cached.get("raw_model_prob"), snap.yes_ask_cents, snap.yes_bid_cents,
                snap.volume_24h, nbm_run_id=cached.get("nbm_run_id"),
                nbm_forecast_hour=cached.get("nbm_forecast_hour"),
                nbm_valid_utc=cached.get("nbm_valid_utc"),
                ladder_json=cached.get("ladder_json"),
            )
            if wrote:
                stats["recorded"] += 1

    logger.info(
        "observations recorded=%d already_seen=%d no_forecast=%d errors=%d",
        stats["recorded"], stats["already_seen"], stats["no_forecast"], stats["errors"],
    )
    return stats
