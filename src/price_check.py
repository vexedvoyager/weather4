"""
Price-check: the LIGHT tier of the two-tier scan design.

Runs frequently (see .github/workflows/price-check.yml, default every 5
minutes). Does NOT fetch NBM. Instead, for every market with a fresh
cached model probability (written by forecast_refresh.py), re-fetches
just the current Kalshi price/orderbook and decides whether to trade.

This is what lets paper trading react to market price movements roughly
as often as live trading would, without wastefully re-fetching a forecast
that only changes 6x/day.
"""
import logging
import sys
import uuid
from datetime import datetime, timezone

from src import alerts, db, edge
from src.config import load_config, resolve_path
from src.market_parsing import build_market_snapshot
from src.paper_broker import PaperBroker
from src.scan_common import build_client
from src.settle_check import run_settle_check

logger = logging.getLogger("price_check")


def run_price_check(cfg: dict):
    db_path = str(resolve_path(cfg, "database"))
    db.init_db(db_path)

    client = build_client()
    broker = PaperBroker() if cfg["mode"] == "paper" else None

    # --- Settlement check (v4) -----------------------------------------------
    # Runs FIRST, before anything else - this is what was entirely missing
    # through v1-v3. Checks every open position against Kalshi's current
    # market data and closes out anything that has actually resolved,
    # freeing up budget/position-count for the rest of this same run.
    settle_summary = run_settle_check(cfg, client=client)
    logger.info(
        "Settlement check: checked=%d settled=%d still_open=%d unrecognized=%d",
        settle_summary["checked"], settle_summary["settled"],
        settle_summary["still_open"], settle_summary["unrecognized_result"],
    )

    # --- Consistency check ---------------------------------------------------
    db_open = db.count_open_trades(db_path)
    try:
        live_positions = client.get_positions()
        live_open = len(live_positions)
    except Exception as e:
        logger.error(
            "Could not fetch live positions for consistency check: %s. "
            "If this is a 401 Unauthorized (and /markets calls in this same "
            "run succeeded), your Kalshi API key likely doesn't have "
            "portfolio/positions read permission - check the key's scopes "
            "in your Kalshi account settings. Until fixed, this safety check "
            "cannot actually verify anything.", e,
        )
        live_open = db_open

    if abs(db_open - live_open) > 1:
        msg = (
            f"POSITION MISMATCH: local database shows {db_open} open "
            f"position(s), but Kalshi shows {live_open}. Review before "
            f"trusting today's numbers."
        )
        logger.warning(msg)
        alerts.write_alert(cfg, "Position mismatch", msg)

    # --- Daily loss limit ------------------------------------------------------
    today_str = datetime.now(timezone.utc).date().isoformat()
    todays_pnl = db.daily_pnl_cents(db_path, today_str)
    daily_loss_cap_cents = int(cfg["risk"]["daily_loss_limit_usd"] * 100)
    if todays_pnl <= -daily_loss_cap_cents:
        msg = (
            f"Daily loss limit reached (today's P&L: ${todays_pnl/100:.2f}, "
            f"limit: -${cfg['risk']['daily_loss_limit_usd']:.2f}). No new trades this run."
        )
        logger.warning(msg)
        alerts.write_alert(cfg, "Daily loss limit hit", msg)
        db.log_scan(db_path, 0, 0, 0, db_open, live_open, notes="daily_loss_limit_hit")
        return

    tickers_scanned = 0
    tickers_eligible = 0
    trades_opened = 0
    total_deployed = db.total_deployed_cents(db_path)
    budget_cap_cents = int(cfg["risk"]["total_budget_usd"] * 100)
    max_trade_cents = int(cfg["risk"]["max_cost_per_trade_usd"] * 100)
    max_forecast_age_hours = cfg["operations"]["forecast_cache_max_age_hours"]
    max_new_trades_per_run = cfg["risk"].get("max_new_trades_per_run")

    for city_cfg in cfg["cities"]:
        if max_new_trades_per_run and trades_opened >= max_new_trades_per_run:
            logger.info(
                "max_new_trades_per_run cap (%d) reached - stopping scan early "
                "this run. Remaining eligible candidates will be picked up on "
                "the next Price Check run instead.", max_new_trades_per_run,
            )
            break

        city = city_cfg["name"]
        try:
            markets = client.get_markets_by_series(city_cfg["kalshi_series_prefix"])
        except Exception as e:
            logger.error("city=%s could not fetch markets: %s", city, e)
            continue

        for market in markets:
            if max_new_trades_per_run and trades_opened >= max_new_trades_per_run:
                break

            tickers_scanned += 1
            ticker = market.get("ticker")

            cached = db.get_cached_forecast(db_path, ticker, max_forecast_age_hours)
            if cached is None:
                # No fresh forecast for this market yet - normal for a
                # market that's new since the last forecast refresh, or
                # one where NBM coverage doesn't reach. Not an error.
                continue

            model_prob = cached["model_prob"]
            threshold_description = cached.get("threshold_description")

            try:
                orderbook = client.get_orderbook(ticker)
            except Exception as e:
                logger.debug("ticker=%s orderbook fetch failed: %s", ticker, e)
                continue

            snap = build_market_snapshot(ticker, market, orderbook)
            if snap is None:
                continue

            market_prob = snap.yes_ask_cents / 100.0
            gap = model_prob - market_prob
            want_side = "yes" if gap > 0 else "no"

            if abs(gap) < cfg["edge"]["min_probability_gap"]:
                continue
            if snap.volume_24h < cfg["operations"]["min_volume_contracts"]:
                continue

            scores = edge.composite_score(
                snap, model_prob, want_side,
                cfg["operations"]["min_volume_contracts"], cfg["edge"]["weights"],
            )
            if scores["composite"] < cfg["edge"]["min_composite_score"]:
                continue

            tickers_eligible += 1

            if db.count_open_trades(db_path, city=city) >= cfg["risk"]["max_positions_per_city"]:
                continue

            price_cents = snap.yes_ask_cents if want_side == "yes" else snap.no_ask_cents
            count = max(1, max_trade_cents // max(price_cents, 1))
            cost_cents = count * price_cents

            if total_deployed + cost_cents > budget_cap_cents:
                continue
            if cost_cents > max_trade_cents:
                count = max(1, max_trade_cents // price_cents)
                cost_cents = count * price_cents

            if cfg["mode"] == "paper":
                broker.simulate_fill(ticker, want_side, count, price_cents)
            else:
                client_order_id = str(uuid.uuid4())
                price_dollars = f"{price_cents / 100:.2f}"
                client.place_order(
                    ticker=ticker, side=want_side, action="buy", count=count,
                    yes_price_dollars=price_dollars if want_side == "yes" else None,
                    no_price_dollars=price_dollars if want_side == "no" else None,
                    client_order_id=client_order_id,
                )

            db.insert_trade(
                db_path, ticker, city, want_side, count, price_cents,
                model_prob, scores["composite"], cfg["mode"],
                threshold_description=threshold_description,
            )
            total_deployed += cost_cents
            trades_opened += 1
            logger.info(
                "TRADE ticker=%s city=%s side=%s count=%d price_cents=%d "
                "threshold=%r cached_model_prob=%.3f (from nbm_run=%s) edge_scores=%s mode=%s",
                ticker, city, want_side, count, price_cents, threshold_description,
                model_prob, cached["nbm_run_id"], scores, cfg["mode"],
            )

    db.log_scan(db_path, tickers_scanned, tickers_eligible, trades_opened, db_open, live_open)
    logger.info(
        "Price check complete. scanned=%d eligible=%d opened=%d",
        tickers_scanned, tickers_eligible, trades_opened,
    )


if __name__ == "__main__":
    cfg = load_config()
    logger.info("Starting price check in mode=%s", cfg["mode"])
    try:
        run_price_check(cfg)
    except Exception:
        logger.exception("Price check failed with an unhandled exception")
        sys.exit(1)
