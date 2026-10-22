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


def _minimal_cfg(db_path, mode="paper"):
    return {
        "mode": mode,
        "paths": {"database": db_path},
        "risk": {
            "total_budget_usd": 50.0, "max_cost_per_trade_usd": 5.0,
            "max_positions_per_city": 10, "daily_loss_limit_usd": 10.0,
        },
        "operations": {"forecast_cache_max_age_hours": 8, "min_volume_contracts": 5},
        "edge": {
            "min_probability_gap": 0.08, "min_composite_score": 0.0,
            "weights": {"spread": 0.3, "volume": 0.2, "imbalance": 0.25, "mispricing": 0.25},
        },
        "cities": [],
    }


def test_paper_mode_never_calls_get_positions():
    """The whole point of item #17: in paper mode, the consistency check
    is skipped entirely - get_positions() should never even be called."""
    path = _temp_db()
    client = MagicMock()
    cfg = _minimal_cfg(path, mode="paper")

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)

    client.get_positions.assert_not_called()


def test_paper_mode_does_not_raise_position_mismatch_alert_even_with_open_trades():
    """Before this fix, a paper-mode account with real (simulated) open
    positions compared against Kalshi's real (always empty) account would
    have triggered a mismatch alert on nearly every run."""
    path = _temp_db()
    db.insert_trade(path, "T1", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper")
    db.insert_trade(path, "T2", "Miami", "no", 5, 60, 0.7, 0.8, "paper")
    db.insert_trade(path, "T3", "Austin", "yes", 5, 60, 0.7, 0.8, "paper")

    client = MagicMock()
    cfg = _minimal_cfg(path, mode="paper")

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}), \
         patch("src.price_check.alerts.write_alert") as mock_alert:
        run_price_check(cfg)

    # No "Position mismatch" alert should have fired, despite 3 open
    # simulated positions vs. an unqueried (and would-be-empty) real account.
    mismatch_calls = [c for c in mock_alert.call_args_list if c.args[1] == "Position mismatch"]
    assert len(mismatch_calls) == 0


def test_live_mode_still_calls_get_positions():
    """The check should still run normally once real trading begins -
    this fix only disables it specifically for paper mode."""
    path = _temp_db()
    client = MagicMock()
    client.get_positions.return_value = []
    cfg = _minimal_cfg(path, mode="live")

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)

    client.get_positions.assert_called_once()


def test_live_mode_still_flags_a_real_mismatch():
    path = _temp_db()
    db.insert_trade(path, "T1", "Chicago", "yes", 5, 60, 0.7, 0.8, "live")
    db.insert_trade(path, "T2", "Miami", "no", 5, 60, 0.7, 0.8, "live")
    db.insert_trade(path, "T3", "Austin", "yes", 5, 60, 0.7, 0.8, "live")

    client = MagicMock()
    client.get_positions.return_value = []  # Kalshi shows 0, we think we have 3
    cfg = _minimal_cfg(path, mode="live")

    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check", return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}), \
         patch("src.price_check.alerts.write_alert") as mock_alert:
        run_price_check(cfg)

    mismatch_calls = [c for c in mock_alert.call_args_list if c.args[1] == "Position mismatch"]
    assert len(mismatch_calls) == 1
