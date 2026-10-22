import tempfile
import os
from unittest.mock import MagicMock

from src import db
from src.settle_check import run_settle_check


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def _minimal_cfg(db_path):
    return {"paths": {"database": db_path}}


def test_pending_shadow_trade_settles_when_market_resolves():
    path = _temp_db()
    shadow_id = db.insert_shadow_trade(
        path, "KXHIGHAUS-TEST", "Austin", "yes", raw_prob=0.97, clamped_prob=0.55,
        price_cents=4,
    )

    client = MagicMock()
    client.get_market.return_value = {"result": "no"}  # the shadow "yes" bet lost

    summary = run_settle_check(_minimal_cfg(path), client=client)

    assert summary["shadow_checked"] == 1
    assert summary["shadow_settled"] == 1
    assert db.get_pending_shadow_trades(path) == []

    settled = db.get_shadow_trade_summary(path)
    assert settled["n"] == 1
    assert settled["losses"] == 1
    assert settled["total_pnl_cents"] == -4  # per-contract loss of the 4c price


def test_pending_shadow_trade_stays_pending_when_unresolved():
    path = _temp_db()
    db.insert_shadow_trade(path, "KXHIGHAUS-TEST", "Austin", "yes", 0.97, 0.55, 4)

    client = MagicMock()
    client.get_market.return_value = {"result": None}

    summary = run_settle_check(_minimal_cfg(path), client=client)

    assert summary["shadow_checked"] == 1
    assert summary["shadow_settled"] == 0
    assert len(db.get_pending_shadow_trades(path)) == 1


def test_shadow_trade_win_computed_correctly():
    path = _temp_db()
    db.insert_shadow_trade(path, "KXHIGHAUS-TEST", "Austin", "yes", 0.97, 0.55, price_cents=4)

    client = MagicMock()
    client.get_market.return_value = {"result": "yes"}  # the shadow "yes" bet won

    run_settle_check(_minimal_cfg(path), client=client)

    settled = db.get_shadow_trade_summary(path)
    assert settled["wins"] == 1
    assert settled["total_pnl_cents"] == 96  # per-contract win: 100-4


def test_shadow_trade_void_outcome_handled():
    path = _temp_db()
    db.insert_shadow_trade(path, "KXHIGHAUS-TEST", "Austin", "no", 0.03, 0.45, price_cents=90)

    client = MagicMock()
    client.get_market.return_value = {"result": "void"}

    run_settle_check(_minimal_cfg(path), client=client)

    settled = db.get_shadow_trade_summary(path)
    assert settled["n"] == 1
    assert settled["total_pnl_cents"] == 0


def test_shadow_trade_api_error_leaves_it_pending_without_raising():
    path = _temp_db()
    db.insert_shadow_trade(path, "KXHIGHAUS-TEST", "Austin", "yes", 0.97, 0.55, 4)

    client = MagicMock()
    client.get_market.side_effect = Exception("500 server error")

    summary = run_settle_check(_minimal_cfg(path), client=client)  # should not raise

    assert summary["shadow_settled"] == 0
    assert len(db.get_pending_shadow_trades(path)) == 1


def test_real_trades_and_shadow_trades_settle_independently_in_same_run():
    path = _temp_db()
    real_trade_id = db.insert_trade(
        path, "REAL-TICKER", "Chicago", "no", 5, 60, 0.9, 0.8, "paper"
    )
    db.insert_shadow_trade(path, "SHADOW-TICKER", "Austin", "yes", 0.97, 0.55, 4)

    client = MagicMock()
    def get_market(ticker):
        if ticker == "REAL-TICKER":
            return {"result": "no"}
        return {"result": "yes"}
    client.get_market.side_effect = get_market

    summary = run_settle_check(_minimal_cfg(path), client=client)

    assert summary["settled"] == 1
    assert summary["shadow_settled"] == 1
    assert db.count_open_trades(path) == 0
    assert db.get_pending_shadow_trades(path) == []
