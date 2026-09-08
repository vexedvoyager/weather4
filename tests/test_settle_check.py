import tempfile
import os
from unittest.mock import MagicMock

from src import db
from src.settle_check import run_settle_check, _compute_pnl_cents


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def _minimal_cfg(db_path):
    return {"paths": {"database": db_path}}


def test_compute_pnl_yes_side_wins():
    # Bought YES at 60c, resolved YES -> win. Pays $1.00 per contract, cost 60c.
    pnl = _compute_pnl_cents("yes", 60, 10, "yes")
    assert pnl == 400  # 10 * (100-60)


def test_compute_pnl_yes_side_loses():
    pnl = _compute_pnl_cents("yes", 60, 10, "no")
    assert pnl == -600  # 10 * -60


def test_compute_pnl_no_side_wins():
    pnl = _compute_pnl_cents("no", 68, 7, "no")
    assert pnl == 224  # 7 * (100-68)


def test_compute_pnl_no_side_loses():
    pnl = _compute_pnl_cents("no", 83, 6, "yes")
    assert pnl == -498  # 6 * -83


def test_compute_pnl_void_is_zero_regardless_of_side():
    assert _compute_pnl_cents("yes", 60, 10, "void") == 0
    assert _compute_pnl_cents("no", 60, 10, "void") == 0


def test_settle_check_settles_a_resolved_market():
    path = _temp_db()
    trade_id = db.insert_trade(path, "TICKER-1", "Chicago", "no", 5, 85, 0.1, 0.85, "paper")

    client = MagicMock()
    client.get_market.return_value = {"result": "no"}  # NO won

    summary = run_settle_check(_minimal_cfg(path), client=client)

    assert summary["checked"] == 1
    assert summary["settled"] == 1
    assert db.count_open_trades(path) == 0

    with db.get_connection(path) as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    assert row["status"] == "settled"
    assert row["outcome"] == "no"
    assert row["pnl_cents"] == 75  # 5 * (100-85)


def test_settle_check_leaves_unresolved_market_open():
    path = _temp_db()
    db.insert_trade(path, "TICKER-1", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper")

    client = MagicMock()
    client.get_market.return_value = {"result": None}  # not resolved yet

    summary = run_settle_check(_minimal_cfg(path), client=client)

    assert summary["checked"] == 1
    assert summary["settled"] == 0
    assert summary["still_open"] == 1
    assert db.count_open_trades(path) == 1


def test_settle_check_handles_void_outcome():
    path = _temp_db()
    trade_id = db.insert_trade(path, "TICKER-1", "Miami", "yes", 5, 60, 0.7, 0.8, "paper")

    client = MagicMock()
    client.get_market.return_value = {"result": "void"}

    run_settle_check(_minimal_cfg(path), client=client)

    with db.get_connection(path) as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    assert row["outcome"] == "void"
    assert row["pnl_cents"] == 0
    assert row["status"] == "settled"


def test_settle_check_flags_unrecognized_result_without_guessing():
    """The 'last fair price' edge case from Kalshi's rulebook - must not
    be silently coerced into yes/no/void."""
    path = _temp_db()
    db.insert_trade(path, "TICKER-1", "Austin", "yes", 5, 60, 0.7, 0.8, "paper")

    client = MagicMock()
    client.get_market.return_value = {"result": "last_fair_price_47"}

    summary = run_settle_check(_minimal_cfg(path), client=client)

    assert summary["unrecognized_result"] == 1
    assert summary["settled"] == 0
    assert db.count_open_trades(path) == 1  # left open, not guessed at


def test_settle_check_handles_api_error_gracefully():
    path = _temp_db()
    db.insert_trade(path, "TICKER-1", "LA", "yes", 5, 60, 0.7, 0.8, "paper")

    client = MagicMock()
    client.get_market.side_effect = Exception("500 server error")

    summary = run_settle_check(_minimal_cfg(path), client=client)

    assert summary["still_open"] == 1
    assert db.count_open_trades(path) == 1


def test_settle_check_processes_multiple_trades_independently():
    path = _temp_db()
    db.insert_trade(path, "TICKER-RESOLVED", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper")
    db.insert_trade(path, "TICKER-PENDING", "Miami", "no", 3, 70, 0.5, 0.7, "paper")

    client = MagicMock()
    def get_market_side_effect(ticker):
        if ticker == "TICKER-RESOLVED":
            return {"result": "yes"}
        return {"result": None}
    client.get_market.side_effect = get_market_side_effect

    summary = run_settle_check(_minimal_cfg(path), client=client)

    assert summary["checked"] == 2
    assert summary["settled"] == 1
    assert summary["still_open"] == 1
