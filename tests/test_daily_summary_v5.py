import tempfile
import os
from datetime import datetime, timedelta, timezone

from src import db
from src.daily_summary import (
    generate_summary, format_probability, _is_extreme, _actual_outcome_note,
    _get_stuck_positions, STUCK_POSITION_HOURS,
)


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def _minimal_cfg(db_path):
    return {
        "mode": "paper",
        "paths": {"database": db_path, "daily_summary_dir": "/tmp/does_not_matter"},
        "risk": {"total_budget_usd": 50.0},
    }


def test_format_probability_shows_decimal_near_extremes():
    assert format_probability(0.003) == "0.3%"
    assert format_probability(0.997) == "99.7%"


def test_format_probability_whole_number_in_normal_range():
    assert format_probability(0.5) == "50%"
    assert format_probability(0.85) == "85%"


def test_is_extreme_matches_the_v5_clamp_bounds():
    from src.probability import PROBABILITY_FLOOR, PROBABILITY_CEILING
    assert _is_extreme(PROBABILITY_FLOOR) is True
    assert _is_extreme(PROBABILITY_CEILING) is True
    assert _is_extreme(0.5) is False
    assert _is_extreme(0.10) is False


def test_actual_outcome_note_for_below_threshold_win():
    assert _actual_outcome_note("high < 88°F", "yes") == "confirmed: high < 88°F"


def test_actual_outcome_note_for_below_threshold_loss():
    note = _actual_outcome_note("high < 88°F", "no")
    assert "≥ 88°F" in note


def test_actual_outcome_note_for_above_threshold_loss():
    note = _actual_outcome_note("high > 95°F", "no")
    assert "≤ 95°F" in note


def test_actual_outcome_note_omitted_for_bracket_markets():
    """Honest scope limit: we don't have enough info to state the true
    winner for a bracket we didn't trade - no note should be fabricated."""
    assert _actual_outcome_note("high 85-86°F", "no") == ""
    assert _actual_outcome_note("high 85-86°F", "yes") == ""


def test_stuck_position_detection():
    path = _temp_db()
    old_time = (datetime.now(timezone.utc) - timedelta(hours=STUCK_POSITION_HOURS + 5)).isoformat()
    with db.get_connection(path) as conn:
        conn.execute(
            "INSERT INTO trades (ticker, city, side, count, entry_price_cents, "
            "forecast_prob, composite_edge_score, mode, status, opened_at) "
            "VALUES ('STUCK', 'Chicago', 'yes', 5, 60, 0.7, 0.8, 'paper', 'open', ?)",
            (old_time,),
        )
        conn.commit()
    recent_id = db.insert_trade(path, "RECENT", "Miami", "no", 5, 60, 0.7, 0.8, "paper")

    stuck = _get_stuck_positions(path)
    stuck_tickers = [t["ticker"] for t, age in stuck]
    assert "STUCK" in stuck_tickers
    assert "RECENT" not in stuck_tickers


def test_generate_summary_runs_cleanly_on_empty_database():
    """Must not crash on a brand-new database - the actual state right
    after first setup - and should correctly show an all-zero state."""
    path = _temp_db()
    cfg = _minimal_cfg(path)
    summary = generate_summary(cfg)
    assert "New trades" in summary
    assert "Settled" in summary
    assert "0 (0W / 0L)" in summary  # correct zero-state on a fresh database


def test_generate_summary_shows_side_on_settled_trades():
    path = _temp_db()
    trade_id = db.insert_trade(
        path, "T1", "Chicago", "no", 5, 60, 0.7, 0.8, "paper",
        threshold_description="high < 88°F",
    )
    db.settle_trade(path, trade_id, outcome="no", pnl_cents=200)

    cfg = _minimal_cfg(path)
    summary = generate_summary(cfg)
    assert "NO on high < 88°F" in summary


def test_generate_summary_includes_shadow_section_only_when_relevant():
    path = _temp_db()
    cfg = _minimal_cfg(path)
    summary_without = generate_summary(cfg)
    assert "Shadow trades" not in summary_without

    db.insert_shadow_trade(path, "SHADOW-1", "Austin", "yes", 0.97, 0.55, 4)
    summary_with = generate_summary(cfg)
    assert "Shadow trades" in summary_with


def test_generate_summary_flags_extreme_trades_with_marker():
    path = _temp_db()
    db.insert_trade(
        path, "T1", "Chicago", "yes", 5, 3, 0.95, 0.8, "paper",  # 0.95 = PROBABILITY_CEILING
        threshold_description="high < 66°F",
    )
    cfg = _minimal_cfg(path)
    summary = generate_summary(cfg)
    assert "[!]" in summary


def test_generate_summary_does_not_crash_with_migration_needed():
    """Confirms generate_summary() calls db.init_db() itself - found as a
    real gap while testing against production data (an old database
    missing the v5 shadow_trades table would otherwise crash here)."""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, city TEXT, side TEXT,
            count INTEGER, entry_price_cents INTEGER, forecast_prob REAL,
            composite_edge_score REAL, mode TEXT, status TEXT DEFAULT 'open',
            outcome TEXT, pnl_cents INTEGER, fee_cents INTEGER DEFAULT 0,
            opened_at TEXT, settled_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    cfg = _minimal_cfg(path)
    summary = generate_summary(cfg)  # should not raise despite missing shadow_trades table
    assert "Predict Weather Bot" in summary
