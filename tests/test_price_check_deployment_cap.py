import tempfile
import os
from unittest.mock import MagicMock, patch

from src import db
from src.price_check import run_price_check


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def _minimal_cfg(db_path, max_new_trades_per_run=None):
    risk = {
        "total_budget_usd": 50.0,
        "max_cost_per_trade_usd": 5.0,
        "max_positions_per_city": 10,  # high, so it doesn't interfere with this test
        "daily_loss_limit_usd": 10.0,
    }
    if max_new_trades_per_run is not None:
        risk["max_new_trades_per_run"] = max_new_trades_per_run

    return {
        "mode": "paper",
        "paths": {"database": db_path},
        "risk": risk,
        "operations": {"forecast_cache_max_age_hours": 8, "min_volume_contracts": 5},
        "edge": {
            "min_probability_gap": 0.05,
            "min_composite_score": 0.0,  # accept everything for this test
            "weights": {"spread": 0.3, "volume": 0.2, "imbalance": 0.25, "mispricing": 0.25},
        },
        "cities": [
            {"name": "Chicago", "kalshi_series_prefix": "KXHIGHCHI"},
            {"name": "Miami", "kalshi_series_prefix": "KXHIGHMIA"},
        ],
    }


def _make_market(ticker):
    # Market prices YES cheaply (yes_ask ~20c via no_bid=0.80), while the
    # cached model_prob in these tests is 0.9 - a large, clearly eligible
    # gap, so these tests exercise the deployment cap itself rather than
    # accidentally getting rejected by the edge-gap threshold.
    return {
        "ticker": ticker,
        "yes_bid_dollars": "0.15",
        "no_bid_dollars": "0.80",
        "volume_24h_fp": "100.00",
    }


def _make_orderbook():
    return {
        "yes_dollars": [["0.10", "50.00"]],
        "no_dollars": [["0.10", "50.00"]],
    }


def _build_client_with_n_eligible_markets_per_city(tickers_by_city: dict):
    client = MagicMock()

    def get_markets_by_series(series_prefix, **kwargs):
        return [_make_market(t) for t in tickers_by_city.get(series_prefix, [])]

    client.get_markets_by_series.side_effect = get_markets_by_series
    client.get_orderbook.return_value = _make_orderbook()
    client.get_positions.return_value = []
    return client


def test_deployment_cap_limits_trades_opened_in_one_run():
    path = _temp_db()

    # Pre-populate the forecast cache with a big edge for 3 markets per city
    # (6 total eligible candidates), all pointing strongly toward YES.
    tickers_by_city = {
        "KXHIGHCHI": ["CHI-1", "CHI-2", "CHI-3"],
        "KXHIGHMIA": ["MIA-1", "MIA-2", "MIA-3"],
    }
    for prefix, tickers in tickers_by_city.items():
        city = "Chicago" if prefix == "KXHIGHCHI" else "Miami"
        for t in tickers:
            db.upsert_forecast_cache(path, t, city, model_prob=0.9, nbm_run_id="test-run")

    client = _build_client_with_n_eligible_markets_per_city(tickers_by_city)
    cfg = _minimal_cfg(path, max_new_trades_per_run=2)

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)

    assert db.count_open_trades(path) == 2  # capped, even though 6 were eligible


def test_no_cap_configured_opens_all_eligible_trades():
    path = _temp_db()

    tickers_by_city = {
        "KXHIGHCHI": ["CHI-1", "CHI-2"],
        "KXHIGHMIA": ["MIA-1"],
    }
    for prefix, tickers in tickers_by_city.items():
        city = "Chicago" if prefix == "KXHIGHCHI" else "Miami"
        for t in tickers:
            db.upsert_forecast_cache(path, t, city, model_prob=0.9, nbm_run_id="test-run")

    client = _build_client_with_n_eligible_markets_per_city(tickers_by_city)
    cfg = _minimal_cfg(path, max_new_trades_per_run=None)  # no cap set

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)

    assert db.count_open_trades(path) == 3  # all eligible, uncapped
