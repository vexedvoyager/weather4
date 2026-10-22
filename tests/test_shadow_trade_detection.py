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


def _minimal_cfg(db_path):
    return {
        "mode": "paper",
        "paths": {"database": db_path},
        "risk": {
            "total_budget_usd": 50.0, "max_cost_per_trade_usd": 5.0,
            "max_positions_per_city": 10, "daily_loss_limit_usd": 10.0,
        },
        "operations": {"forecast_cache_max_age_hours": 8, "min_volume_contracts": 5},
        "edge": {
            "min_probability_gap": 0.08,
            "min_composite_score": 0.0,
            "weights": {"spread": 0.3, "volume": 0.2, "imbalance": 0.25, "mispricing": 0.25},
        },
        "cities": [{"name": "Austin", "kalshi_series_prefix": "KXHIGHAUS"}],
    }


def _make_market(ticker, yes_bid="0.50", no_bid="0.49"):
    # Market prices near 50/50 by default - small enough gap that the
    # CLAMPED model probability won't clear the edge threshold, but a
    # far-more-extreme RAW probability would.
    return {
        "ticker": ticker, "yes_bid_dollars": yes_bid, "no_bid_dollars": no_bid,
        "volume_24h_fp": "100.00",
    }


def _make_orderbook():
    return {"yes_dollars": [["0.10", "50.00"]], "no_dollars": [["0.10", "50.00"]]}


def _build_client(tickers):
    client = MagicMock()
    client.get_markets_by_series.return_value = [_make_market(t) for t in tickers]
    client.get_orderbook.return_value = _make_orderbook()
    client.get_positions.return_value = []
    return client


def test_shadow_trade_recorded_when_raw_would_clear_but_clamped_does_not():
    path = _temp_db()

    # market_prob ~= yes_ask = 100 - no_bid_cents = 100-49 = 51 -> 0.51
    # clamped model_prob = 0.55 -> gap = 0.04, BELOW the 0.08 threshold - no real trade
    # raw_model_prob = 0.97 -> gap = 0.46, comfortably clears - shadow candidate
    db.upsert_forecast_cache(
        path, "KXHIGHAUS-TEST", "Austin", model_prob=0.55, nbm_run_id="test-run",
        threshold_description="high < 96°F", raw_model_prob=0.97,
    )

    client = _build_client(["KXHIGHAUS-TEST"])
    cfg = _minimal_cfg(path)

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)

    # No real trade should have opened - clamped gap didn't clear.
    assert db.count_open_trades(path) == 0

    # But a shadow trade SHOULD have been recorded.
    pending = db.get_pending_shadow_trades(path)
    assert len(pending) == 1
    assert pending[0]["ticker"] == "KXHIGHAUS-TEST"
    assert pending[0]["raw_prob"] == 0.97
    assert pending[0]["clamped_prob"] == 0.55
    assert pending[0]["side"] == "yes"  # raw prob (0.97) > market prob -> yes


def test_no_shadow_trade_when_clamped_already_clears_the_gap():
    """A normal, real trade shouldn't ALSO get logged as a shadow trade -
    shadow tracking is only for candidates the clamp specifically excluded."""
    path = _temp_db()
    db.upsert_forecast_cache(
        path, "KXHIGHAUS-TEST", "Austin", model_prob=0.90, nbm_run_id="test-run",
        raw_model_prob=0.97,
    )
    client = _build_client(["KXHIGHAUS-TEST"])
    cfg = _minimal_cfg(path)

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)

    assert db.count_open_trades(path) == 1  # real trade happened
    assert db.get_pending_shadow_trades(path) == []  # no shadow needed


def test_no_shadow_trade_when_raw_also_fails_to_clear_the_gap():
    """If even the raw probability wouldn't have cleared the edge
    threshold, there's nothing interesting to shadow-track."""
    path = _temp_db()
    db.upsert_forecast_cache(
        path, "KXHIGHAUS-TEST", "Austin", model_prob=0.52, nbm_run_id="test-run",
        raw_model_prob=0.54,  # barely different from clamped, still no real gap
    )
    client = _build_client(["KXHIGHAUS-TEST"])
    cfg = _minimal_cfg(path)

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)

    assert db.count_open_trades(path) == 0
    assert db.get_pending_shadow_trades(path) == []


def test_shadow_trade_not_duplicated_across_multiple_runs():
    """The same still-excluded candidate shouldn't get re-logged as a new
    shadow trade every single Price Check run (every 5 minutes)."""
    path = _temp_db()
    db.upsert_forecast_cache(
        path, "KXHIGHAUS-TEST", "Austin", model_prob=0.55, nbm_run_id="test-run",
        raw_model_prob=0.97,
    )
    client = _build_client(["KXHIGHAUS-TEST"])
    cfg = _minimal_cfg(path)

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)
        run_price_check(cfg)  # second run, same still-eligible candidate

    pending = db.get_pending_shadow_trades(path)
    assert len(pending) == 1  # not two


def test_no_raw_model_prob_means_no_shadow_tracking_attempted():
    """Older cached rows (from before this migration) have
    raw_model_prob=None - must not crash, and must not attempt shadow
    detection with missing data."""
    path = _temp_db()
    db.upsert_forecast_cache(
        path, "KXHIGHAUS-TEST", "Austin", model_prob=0.55, nbm_run_id="test-run",
        raw_model_prob=None,
    )
    client = _build_client(["KXHIGHAUS-TEST"])
    cfg = _minimal_cfg(path)

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)  # should not raise

    assert db.get_pending_shadow_trades(path) == []
