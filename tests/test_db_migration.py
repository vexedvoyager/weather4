import sqlite3
import tempfile
import os

from src import db


def test_migration_adds_column_to_pre_existing_database_without_it():
    """
    Simulates exactly the real situation: a live database created by
    earlier code (before threshold_description existed) needs to keep
    working once newer code runs against it, without losing existing data.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)

    # Manually create an "old-style" trades table, missing the new column,
    # with a real row already in it - simulating a live database with
    # existing trades from before this migration existed.
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            city TEXT NOT NULL,
            side TEXT NOT NULL,
            count INTEGER NOT NULL,
            entry_price_cents INTEGER NOT NULL,
            forecast_prob REAL NOT NULL,
            composite_edge_score REAL NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            outcome TEXT,
            pnl_cents INTEGER,
            fee_cents INTEGER DEFAULT 0,
            opened_at TEXT NOT NULL,
            settled_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO trades (ticker, city, side, count, entry_price_cents, "
        "forecast_prob, composite_edge_score, mode, opened_at) "
        "VALUES ('OLD-TICKER', 'Chicago', 'yes', 5, 60, 0.7, 0.8, 'paper', '2026-09-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    # Now run the real init_db - this should migrate in place, not fail,
    # and not lose the existing row.
    db.init_db(path)

    with db.get_connection(path) as conn2:
        row = conn2.execute("SELECT * FROM trades WHERE ticker = 'OLD-TICKER'").fetchone()
        assert row is not None
        assert row["threshold_description"] is None  # new column, old row has no value

        columns = {r["name"] for r in conn2.execute("PRAGMA table_info(trades)")}
        assert "threshold_description" in columns


def test_migration_is_idempotent_running_twice_does_not_error():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)

    db.init_db(path)
    db.init_db(path)  # should not raise "duplicate column" or similar

    with db.get_connection(path) as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(trades)")}
        assert "threshold_description" in columns


def test_fresh_database_has_column_from_the_start():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)

    db.init_db(path)
    trade_id = db.insert_trade(
        path, "TICKER", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper",
        threshold_description="high < 88°F",
    )
    with db.get_connection(path) as conn:
        row = conn.execute("SELECT threshold_description FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert row["threshold_description"] == "high < 88°F"
