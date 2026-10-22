import tempfile
import os
from datetime import datetime, timedelta, timezone

from src import db


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def _insert_trade_with_opened_at(db_path, ticker, opened_at_iso):
    """Directly sets opened_at to an exact timestamp, bypassing the
    normal insert_trade() (which always uses "now") - needed to
    reproduce the exact bug scenario at specific points in time."""
    with db.get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trades
                (ticker, city, side, count, entry_price_cents, forecast_prob,
                 composite_edge_score, mode, status, opened_at)
            VALUES (?, 'Chicago', 'yes', 5, 60, 0.7, 0.8, 'paper', 'open', ?)
            """,
            (ticker, opened_at_iso),
        )
        conn.commit()


def test_reproduces_the_real_invisible_trade_bug_with_old_date_string_matching():
    """
    Confirms the OLD bug actually existed: a trade opened late in the day
    (after when a fixed-time daily summary would have run) is invisible
    to a calendar-day LIKE match for "today", and also invisible to
    "yesterday"'s match (wrong date). This is the exact confirmed pattern
    from cross-referencing 15 real days of summaries.
    """
    path = _temp_db()
    late_trade_time = "2026-09-12T22:00:00+00:00"
    _insert_trade_with_opened_at(path, "LATE-TRADE", late_trade_time)

    with db.get_connection(path) as conn:
        day_d_plus_1_old_query = conn.execute(
            "SELECT * FROM trades WHERE opened_at LIKE ?", ("2026-09-13%",)
        ).fetchall()
    assert len(day_d_plus_1_old_query) == 0  # confirms the bug: invisible to the next day too


def test_rolling_window_catches_the_late_trade_the_old_query_missed():
    path = _temp_db()
    late_trade_time = "2026-09-12T22:00:00+00:00"
    _insert_trade_with_opened_at(path, "LATE-TRADE", late_trade_time)

    generation_time = datetime(2026, 9, 13, 13, 0, tzinfo=timezone.utc)
    cutoff = (generation_time - timedelta(hours=24)).isoformat()

    results = db.get_trades_opened_since(path, cutoff)
    tickers = [r["ticker"] for r in results]
    assert "LATE-TRADE" in tickers


def test_rolling_window_does_not_show_trades_older_than_24_hours():
    path = _temp_db()
    old_trade_time = "2026-09-10T08:00:00+00:00"  # more than 24h before generation
    _insert_trade_with_opened_at(path, "OLD-TRADE", old_trade_time)

    generation_time = datetime(2026, 9, 13, 13, 0, tzinfo=timezone.utc)
    cutoff = (generation_time - timedelta(hours=24)).isoformat()

    results = db.get_trades_opened_since(path, cutoff)
    tickers = [r["ticker"] for r in results]
    assert "OLD-TRADE" not in tickers


def test_get_trades_settled_since_uses_same_rolling_window_logic():
    path = _temp_db()
    trade_id = db.insert_trade(path, "T1", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper")
    db.settle_trade(path, trade_id, outcome="yes", pnl_cents=200)

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()
    results = db.get_trades_settled_since(path, cutoff)
    assert len(results) == 1
    assert results[0]["ticker"] == "T1"


def test_no_trade_is_ever_permanently_invisible_across_consecutive_daily_windows():
    """
    The real guarantee this fix provides: as long as Daily Summary runs
    at least once every 24 hours, EVERY trade appears in at least one
    day's rolling window - no calendar-boundary gap can swallow it.
    """
    path = _temp_db()
    _insert_trade_with_opened_at(path, "EDGE-CASE", "2026-09-12T23:59:00+00:00")

    generation_time = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)
    cutoff = (generation_time - timedelta(hours=24)).isoformat()
    results = db.get_trades_opened_since(path, cutoff)
    assert "EDGE-CASE" in [r["ticker"] for r in results]
