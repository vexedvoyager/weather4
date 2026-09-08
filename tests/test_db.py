import tempfile
import os

from src import db


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # let init_db create it fresh
    db.init_db(path)
    return path


def test_settle_trade_updates_status_not_just_pnl():
    """
    Regression test for the exact bug described in the source material:
    a settle function that updated outcome/pnl but never touched `status`,
    leaving every settled trade looking 'open' forever.
    """
    path = _temp_db()
    trade_id = db.insert_trade(
        path, "TEST-TICKER", "Chicago", "yes", 10, 60, 0.65, 0.80, "paper"
    )
    assert db.count_open_trades(path) == 1

    db.settle_trade(path, trade_id, outcome="yes", pnl_cents=400)

    assert db.count_open_trades(path) == 0, (
        "settle_trade must move the position out of the open count"
    )


def test_void_outcome_is_not_treated_as_a_loss():
    path = _temp_db()
    trade_id = db.insert_trade(
        path, "TEST-TICKER", "Miami", "no", 5, 50, 0.55, 0.75, "paper"
    )
    # Void: stake returned, no fee assumed in this simple case.
    db.settle_trade(path, trade_id, outcome="void", pnl_cents=0)

    with db.get_connection(path) as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    assert row["outcome"] == "void"
    assert row["pnl_cents"] == 0
    assert row["status"] == "settled"


def test_daily_pnl_only_counts_settled_trades():
    path = _temp_db()
    open_trade = db.insert_trade(
        path, "OPEN-TICKER", "Austin", "yes", 10, 60, 0.65, 0.80, "paper"
    )
    settled_trade = db.insert_trade(
        path, "SETTLED-TICKER", "Austin", "yes", 10, 60, 0.65, 0.80, "paper"
    )
    db.settle_trade(path, settled_trade, outcome="yes", pnl_cents=350)

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    assert db.daily_pnl_cents(path, today) == 350


def test_max_positions_per_city_counts_partially_filled_as_open():
    path = _temp_db()
    db.insert_trade(
        path, "T1", "Chicago", "yes", 10, 60, 0.65, 0.80, "live",
        status="partially_filled",
    )
    assert db.count_open_trades(path, city="Chicago") == 1
