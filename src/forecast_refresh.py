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
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone

from src import db, nbm
from src.config import load_config, resolve_path
from src.market_parsing import describe_threshold, extract_threshold, market_date_from_ticker
from src.probability import probability_of_exceeding, probability_within_range
from src.scan_common import build_client
from src.stations import verify_station

logger = logging.getLogger("forecast_refresh")


# Kalshi daily-high markets settle on the WHOLE-DEGREE high temperature the
# NWS reports, so every contract is a statement about integers:
#     "greater than 75"  (floor_strike 75)  -> high >= 76 -> temperature > 75.5
#     "less than 68"     (cap_strike 68)    -> high <= 67 -> temperature < 67.5
#     "70-71"  (floor 70, cap 71)           -> high in {70, 71} -> 69.5 < t < 71.5
# The forecast is a continuous distribution, so each integer boundary sits half
# a degree outside the printed strike. Before v6.1 the raw strikes were used,
# which made a full ladder of markets sum to only 0.44-0.68 instead of 1.0 -
# harmless when forecasts were wildly wrong, but a large error now that NBM's
# spread is only 2-3F. (v6.1, IMPROVEMENTS.md item #24.)
WHOLE_DEGREE_HALF_WIDTH = 0.5


def model_probability_for_market(threshold: dict, pct: dict, sigma_multiplier: float) -> dict:
    """
    Computes model probability that a market resolves YES, accounting for
    whole-degree settlement (see WHOLE_DEGREE_HALF_WIDTH). "below" markets
    use the complement of "probability of exceeding" - see IMPROVEMENTS.md's
    resolved-issues log for the real bug that fixed.

    NOTE: "greater than X" and "less than X" for the SAME X are NOT
    complements - the exact whole-degree value X belongs to neither.

    Returns {"clamped": float, "raw": float} - the clamped value is what
    drives real trading decisions everywhere; the raw value is kept
    alongside for shadow-tracking (see src/price_check.py and
    IMPROVEMENTS.md item #10).
    """
    h = WHOLE_DEGREE_HALF_WIDTH
    if threshold["kind"] == "single":
        if threshold["direction"] == "above":
            boundary = threshold["value"] + h      # high >= value + 1  <=>  t > value + 0.5
        else:  # "below"
            boundary = threshold["value"] - h      # high <= value - 1  <=>  t < value - 0.5
        p_exceeds_clamped = probability_of_exceeding(pct, boundary, sigma_multiplier, clamp=True)
        p_exceeds_raw = probability_of_exceeding(pct, boundary, sigma_multiplier, clamp=False)
        if threshold["direction"] == "above":
            return {"clamped": p_exceeds_clamped, "raw": p_exceeds_raw}
        return {"clamped": round(1 - p_exceeds_clamped, 6), "raw": round(1 - p_exceeds_raw, 6)}

    # "between": high in [floor, cap] <=> floor - 0.5 < t < cap + 0.5
    lo, hi = threshold["floor"] - h, threshold["cap"] + h
    clamped = probability_within_range(pct, lo, hi, sigma_multiplier, clamp=True)
    raw = probability_within_range(pct, lo, hi, sigma_multiplier, clamp=False)
    return {"clamped": clamped, "raw": raw}


def target_forecast_hour(run_id: str, close_time_str: str) -> int | None:
    """
    Computes hours from the NBM run time to a SPECIFIC market's close
    time - not a fixed constant. See IMPROVEMENTS.md resolved-issues log
    for the real production incident that made this necessary.

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
    failed_coverage_hours_by_city = {}   # city -> set of hours that had NO NBM coverage
    too_close_hours_by_city = {}         # city -> set of hours rejected by the same-day floor

    # v6.0 (item #22): shadow-only cities get the same forecast treatment,
    # so Price Check can record model-vs-market observations for them -
    # they are simply never traded. See config.yaml's shadow_cities.
    all_cities = list(cfg["cities"]) + list(cfg.get("shadow_cities") or [])

    for city_cfg in all_cities:
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
                rejection_counts["too_close_same_day_market"] += 1
                too_close_hours_by_city.setdefault(city, set()).add(hour)
                continue

            # v6.1: pick the daily-MAX column for the market's own date (see
            # nbm.get_max_temp_forecast) - NOT whichever column is nearest the
            # close time, which was an overnight minimum in v5 and earlier.
            market_date = market_date_from_ticker(ticker)
            if market_date is None:
                rejection_counts["no_market_date_in_ticker"] += 1
                continue
            chosen = nbm.get_max_temp_forecast(parsed_station, run_id, market_date)
            if chosen is None:
                rejection_counts["no_nbm_max_column_for_market_date"] += 1
                failed_coverage_hours_by_city.setdefault(city, set()).add(str(market_date))
                continue
            pct = chosen["values"]
            valid_dt = nbm.valid_time_utc(run_id, chosen["forecast_hour"])

            probs = model_probability_for_market(
                threshold, pct, cfg["probability_model"]["sigma_multiplier"]
            )
            description = describe_threshold(threshold)
            db.upsert_forecast_cache(
                db_path, ticker, city, probs["clamped"], run_id, description,
                raw_model_prob=probs["raw"],
                nbm_forecast_hour=chosen["forecast_hour"],
                nbm_valid_utc=valid_dt.isoformat() if valid_dt else None,
                ladder_json=json.dumps(pct),
            )
            markets_cached += 1

    if rejection_counts:
        logger.info("Rejection breakdown (why markets weren't cached): %s", dict(rejection_counts))
    for city, hours in failed_coverage_hours_by_city.items():
        logger.info("city=%s market dates with NO NBM daily-max column: %s", city, sorted(hours))
    for city, hours in too_close_hours_by_city.items():
        logger.info("city=%s target hours rejected by the same-day floor (min_forecast_hour): %s",
                    city, sorted(hours))

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
