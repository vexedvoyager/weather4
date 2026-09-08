"""
Forecast-refresh: the HEAVY tier of the two-tier scan design.

Fetches the NBM NBP bulletin, re-verifies each city's settlement station,
and recomputes model probability for every open market - then caches that
probability to the database. Run this on NBP's actual publish schedule
(see .github/workflows/forecast-refresh.yml), NOT frequently - NBP only
updates 6x/day at irregular hours, so running this more often just
re-reads the same bulletin.

The price_check.py script (the LIGHT tier) reads what this script caches;
it does not re-fetch NBM itself.
"""
import logging
import sys

from src import db, nbm
from src.config import load_config, resolve_path
from src.market_parsing import describe_threshold, extract_threshold
from src.probability import probability_of_exceeding, probability_within_range
from src.scan_common import build_client
from src.stations import verify_station

logger = logging.getLogger("forecast_refresh")


def model_probability_for_market(threshold: dict, pct: dict, sigma_multiplier: float) -> float:
    """
    CORRECTED: earlier versions always computed "probability of
    exceeding" for any single-threshold market, regardless of whether it
    was a "greater than" or "less than" contract. That inverted the
    model's confidence for every "less than" market - confirmed by
    comparing real trade output against Kalshi's own settlement text,
    which showed the model reporting e.g. 75%+ confidence on "less than
    88" contracts where the forecast high was well above 88, when the
    correct YES-probability for that contract is the complement.
    """
    if threshold["kind"] == "single":
        p_exceeds = probability_of_exceeding(pct, threshold["value"], sigma_multiplier)
        if threshold["direction"] == "above":
            return p_exceeds
        else:  # "below"
            return round(1 - p_exceeds, 6)
    else:  # "between"
        return probability_within_range(pct, threshold["floor"], threshold["cap"], sigma_multiplier)


def run_forecast_refresh(cfg: dict):
    db_path = str(resolve_path(cfg, "database"))
    db.init_db(db_path)
    client = build_client()

    try:
        bulletin_text, run_id = nbm.fetch_latest_bulletin()
        logger.info("Using NBP run %s", run_id)
    except RuntimeError as e:
        logger.error(
            "Could not fetch NBP bulletin this cycle: %s. Skipping this "
            "refresh - price_check.py will keep using the last cached "
            "forecast until the next scheduled refresh succeeds.", e,
        )
        sys.exit(0)  # not a hard failure - this is an expected occasional gap

    cities_refreshed = 0
    markets_cached = 0

    for city_cfg in cfg["cities"]:
        city = city_cfg["name"]
        try:
            markets = client.get_markets_by_series(city_cfg["kalshi_series_prefix"])
        except Exception as e:
            logger.error("city=%s could not fetch markets: %s", city, e)
            continue

        if not markets:
            logger.warning(
                "city=%s found ZERO markets for series_prefix=%s - if this "
                "persists, the series prefix guess in config.yaml is likely wrong",
                city, city_cfg["kalshi_series_prefix"],
            )
            continue

        parsed_station = nbm.parse_station_maxt(bulletin_text, city_cfg["expected_station"])
        if parsed_station is None:
            logger.warning("city=%s no usable NBM percentile data for station=%s, skipping",
                           city, city_cfg["expected_station"])
            continue

        db.clear_forecast_cache_for_city(db_path, city)
        cities_refreshed += 1

        for market in markets:
            ticker = market.get("ticker")

            if not verify_station(client, ticker, city_cfg["expected_station"]):
                continue

            threshold = extract_threshold(market)
            if threshold is None:
                logger.debug("ticker=%s no usable strike info, skipping", ticker)
                continue

            pct = nbm.get_forecast_for_target_hour(
                parsed_station, cfg["probability_model"]["min_forecast_hour"]
            )
            if pct is None:
                logger.debug("ticker=%s no NBM coverage at target hour, skipping", ticker)
                continue

            model_prob = model_probability_for_market(
                threshold, pct, cfg["probability_model"]["sigma_multiplier"]
            )
            description = describe_threshold(threshold)
            db.upsert_forecast_cache(db_path, ticker, city, model_prob, run_id, description)
            markets_cached += 1

    logger.info(
        "Forecast refresh complete. nbm_run=%s cities_refreshed=%d markets_cached=%d",
        run_id, cities_refreshed, markets_cached,
    )


if __name__ == "__main__":
    cfg = load_config()
    try:
        run_forecast_refresh(cfg)
    except Exception:
        logger.exception("Forecast refresh failed with an unhandled exception")
        sys.exit(1)
