import tempfile
import os

from src import db


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def test_migration_adds_raw_model_prob_to_existing_forecast_cache():
    """
    Regression test mirroring the real production check: an existing
    database (with the OLD forecast_cache schema, no raw_model_prob) must
    gain the new column without breaking, and without losing existing rows.
    """
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)

    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE forecast_cache (
            ticker TEXT PRIMARY KEY, city TEXT NOT NULL, model_prob REAL NOT NULL,
            nbm_run_id TEXT NOT NULL, cached_at TEXT NOT NULL, threshold_description TEXT
        )
    """)
    conn.execute(
        "INSERT INTO forecast_cache (ticker, city, model_prob, nbm_run_id, cached_at) "
        "VALUES ('OLD-TICKER', 'Chicago', 0.7, 'run-1', '2026-09-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    db.init_db(path)  # runs the real migration

    with db.get_connection(path) as conn2:
        columns = {r["name"] for r in conn2.execute("PRAGMA table_info(forecast_cache)")}
        assert "raw_model_prob" in columns
        row = conn2.execute("SELECT * FROM forecast_cache WHERE ticker = 'OLD-TICKER'").fetchone()
        assert row is not None
        assert row["raw_model_prob"] is None  # old row, no value - expected


def test_upsert_forecast_cache_stores_both_raw_and_clamped():
    path = _temp_db()
    db.upsert_forecast_cache(
        path, "TICKER-1", "Chicago", model_prob=0.95, nbm_run_id="run-1",
        threshold_description="high < 96°F", raw_model_prob=0.998,
    )
    cached = db.get_cached_forecast(path, "TICKER-1", max_age_hours=8)
    assert cached["model_prob"] == 0.95
    assert cached["raw_model_prob"] == 0.998


def test_shadow_trade_insert_and_pending_lookup():
    path = _temp_db()
    assert db.has_pending_shadow_trade(path, "TICKER-1") is False

    db.insert_shadow_trade(
        path, "TICKER-1", "Austin", "yes", raw_prob=0.99, clamped_prob=0.95,
        price_cents=4, threshold_description="high < 96°F", composite_edge_score=0.8,
    )
    assert db.has_pending_shadow_trade(path, "TICKER-1") is True

    pending = db.get_pending_shadow_trades(path)
    assert len(pending) == 1
    assert pending[0]["ticker"] == "TICKER-1"
    assert pending[0]["status"] == "pending"


def test_shadow_trade_settlement():
    path = _temp_db()
    shadow_id = db.insert_shadow_trade(
        path, "TICKER-1", "Austin", "yes", raw_prob=0.99, clamped_prob=0.95, price_cents=4,
    )
    db.settle_shadow_trade(path, shadow_id, outcome="no", would_have_pnl_cents=-4 * 125)

    assert db.has_pending_shadow_trade(path, "TICKER-1") is False
    pending = db.get_pending_shadow_trades(path)
    assert len(pending) == 0


def test_shadow_trade_summary_aggregates_correctly():
    path = _temp_db()
    id1 = db.insert_shadow_trade(path, "T1", "Austin", "yes", 0.99, 0.95, 4)
    id2 = db.insert_shadow_trade(path, "T2", "Chicago", "yes", 0.97, 0.95, 3)
    id3 = db.insert_shadow_trade(path, "T3", "Miami", "no", 0.99, 0.95, 90)

    db.settle_shadow_trade(path, id1, outcome="no", would_have_pnl_cents=-500)  # loss
    db.settle_shadow_trade(path, id2, outcome="yes", would_have_pnl_cents=97 * 1)  # win
    # id3 stays pending

    summary = db.get_shadow_trade_summary(path)
    assert summary["n"] == 2  # only settled ones counted
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["total_pnl_cents"] == -500 + 97


def test_shadow_trades_settled_on_date_filters_correctly():
    path = _temp_db()
    shadow_id = db.insert_shadow_trade(path, "T1", "Austin", "yes", 0.99, 0.95, 4)
    db.settle_shadow_trade(path, shadow_id, outcome="no", would_have_pnl_cents=-400)

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    results = db.get_shadow_trades_settled_on(path, today)
    assert len(results) == 1
    assert results[0]["ticker"] == "T1"
