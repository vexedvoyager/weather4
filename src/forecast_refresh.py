"""
Forecast-refresh: the HEAVY tier of the two-tier scan design.

Fetches the NBM NBP bulletin, re-verifies each city's settlement station,
and recomputes model probability for every open market - then caches that
probability to the database. Run this on NBP's actual publish schedule
(see .github/workflows/forecast-refresh.yml), NOT frequently - NBP only
updates 4x/day at 01/07/13/19 UTC, so running this more often just
re-reads the same bulletin.

The price_check.py script (the LIGHT tier) reads what this script caches;
it does not re-fetch NBM itself.
"""
import logging
import sys
from collections import Counter
from datetime import datetime, timezone

from src import db, nbm
from src.config import load_config, resolve_path
from src.market_parsing import describe_threshold, extract_threshold
from src.probability import probability_of_exceeding, probability_within_range
from src.scan_common import build_client
from src.stations import verify_station

logger = logging.getLogger("forecast_refresh")


def model_probability_for_market(threshold: dict, pct: dict, sigma_multiplier: float) -> float:
    """
    Computes model probability for a market's threshold. "below" markets
    correctly use the complement of "probability of exceeding" - see
    IMPROVEMENTS.md's resolved-issues log for the real bug this fixed.
    """
    if threshold["kind"] == "single":
        p_exceeds = probability_of_exceeding(pct, threshold["value"], sigma_multiplier)
        if threshold["direction"] == "above":
            return p_exceeds
        else:  # "below"
            return round(1 - p_exceeds, 6)
    else:  # "between"
        return probability_within_range(pct, threshold["floor"], threshold["cap"], sigma_multiplier)


def target_forecast_hour(run_id: str, close_time_str: str) -> int | None:
    """
    Computes hours from the NBM run time to a SPECIFIC market's close
    time - not a fixed constant.

    FOUND VIA A REAL PRODUCTION INCIDENT: earlier versions used one fixed
    "min_forecast_hour" value (24) as the target forecast hour for every
    market, every run, regardless of what time of day the NBM run itself
    was issued. That happened to work when the working run was at 01Z
    (where "24 hours ahead" naturally lines up with tomorrow's forecast),
    but silently produced zero usable results when a run was at 19Z
    instead - "24 hours ahead of 19Z" lands somewhere completely
    different in the bulletin's real forecast ladder. Same fixed number,
    different real-world meaning depending on run time - caught via a
    rejection-reason breakdown showing 100% of markets failing at the
    exact same stage, for every city, uniformly.

    run_id format: "YYYY-MM-DDTHHZ" (as returned by nbm.fetch_latest_bulletin).
    Returns None if close_time is missing or unparseable - skip the
    market rather than guess.
    """
    if not close_time_str:
        return None
    try:
        close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
    except ValueError:
        return None

    try:
        run_date_str, run_hour_str = run_id.split("T")
        run_hour = int(run_hour_str.rstrip("Z"))
        run_dt = datetime.fromisoformat(run_date_str).replace(hour=run_hour, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None

    return int((close_time - run_dt).total_seconds() // 3600)


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
    rejection_counts = Counter()
    failed_coverage_hours_by_city = {}  # city -> sorted set of hours that failed lookup

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
        logger.info(
            "city=%s real NBM forecast_hours available this run: %s",
            city, parsed_station.get("forecast_hours"),
        )

        for market in markets:
            ticker = market.get("ticker")

            if not verify_station(client, ticker, city_cfg["expected_station"]):
                rejection_counts["station_verify_failed"] += 1
                continue

            threshold = extract_threshold(market)
            if threshold is None:
                rejection_counts["no_usable_strike_info"] += 1
                continue

            close_time_str = market.get("close_time")
            hour = target_forecast_hour(run_id, close_time_str)
            if hour is None:
                rejection_counts["no_or_unparseable_close_time"] += 1
                continue
            if hour < cfg["probability_model"]["min_forecast_hour"]:
                # Deliberately skipping same-day-ish markets NBM doesn't
                # reliably cover yet - this is the ORIGINAL intent of
                # min_forecast_hour, now used as a floor rather than the
                # lookup target itself.
                rejection_counts["too_close_same_day_market"] += 1
                continue

            pct = nbm.get_forecast_for_target_hour(parsed_station, hour)
            if pct is None:
                rejection_counts["no_nbm_coverage_at_target_hour"] += 1
                failed_coverage_hours_by_city.setdefault(city, set()).add(hour)
                continue

            model_prob = model_probability_for_market(
                threshold, pct, cfg["probability_model"]["sigma_multiplier"]
            )
            description = describe_threshold(threshold)
            db.upsert_forecast_cache(db_path, ticker, city, model_prob, run_id, description)
            markets_cached += 1

    if rejection_counts:
        logger.info("Rejection breakdown (why markets weren't cached): %s", dict(rejection_counts))
    for city, hours in failed_coverage_hours_by_city.items():
        logger.info("city=%s target hours that had NO coverage: %s", city, sorted(hours))

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
