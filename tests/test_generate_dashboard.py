import tempfile
import os

from src import db
from src.generate_dashboard import generate_dashboard_html, _fmt_usd, _get_all_time_stats


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def _minimal_cfg():
    return {"mode": "paper", "risk": {"total_budget_usd": 50.0}}


def test_fmt_usd_positive_negative_zero():
    assert _fmt_usd(500) == "$+5.00"
    assert _fmt_usd(-500) == "$-5.00"
    assert _fmt_usd(0) == "$0.00"


def test_generates_valid_html_with_empty_database():
    """Must not crash on a brand-new database with zero trades - this is
    the actual state right after first setup, and is the most important
    case to not break."""
    path = _temp_db()
    html = generate_dashboard_html(_minimal_cfg(), path)
    assert "<!DOCTYPE html>" in html
    assert "No open positions" in html
    assert "No settled trades yet" in html
    assert "nothing to audit" in html


def test_generates_valid_html_with_real_trade_data():
    path = _temp_db()
    open_id = db.insert_trade(
        path, "KXHIGHCHI-TEST", "Chicago", "yes", 10, 60, 0.7, 0.8, "paper",
        threshold_description="high < 88°F",
    )
    settled_id = db.insert_trade(
        path, "KXHIGHNY-TEST", "New York", "no", 5, 70, 0.3, 0.75, "paper",
        threshold_description="high > 90°F",
    )
    db.settle_trade(path, settled_id, outcome="no", pnl_cents=150)

    html = generate_dashboard_html(_minimal_cfg(), path)
    assert "high < 88°F" in html   # open position shows readable description
    assert "high > 90°F" in html  # settled position shows readable description
    assert "Chicago" in html
    assert "New York" in html
    assert "$+1.50" in html  # the settled trade's P&L


def test_html_escapes_are_not_double_escaped_for_ampersand_in_title():
    path = _temp_db()
    html = generate_dashboard_html(_minimal_cfg(), path)
    # Title/labels use &amp; for literal ampersands in HTML output
    assert "P&amp;L" in html


def test_per_city_stats_only_include_settled_trades():
    path = _temp_db()
    db.insert_trade(path, "T1", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper")  # still open
    settled_id = db.insert_trade(path, "T2", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper")
    db.settle_trade(path, settled_id, outcome="yes", pnl_cents=200)

    stats = _get_all_time_stats(path)
    assert stats["settled_count"] == 1  # the open one should not be counted
    assert stats["total_pnl_cents"] == 200


def test_void_outcome_does_not_count_as_win_or_loss():
    path = _temp_db()
    trade_id = db.insert_trade(path, "T1", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper")
    db.settle_trade(path, trade_id, outcome="void", pnl_cents=0)

    stats = _get_all_time_stats(path)
    assert stats["wins"] == 0
    assert stats["losses"] == 0
    assert stats["pushes"] == 1


def test_missing_threshold_description_falls_back_to_ticker():
    """Old trades from before this feature existed have NULL descriptions -
    the dashboard must still render something sensible, not crash or show
    'None'."""
    path = _temp_db()
    trade_id = db.insert_trade(
        path, "OLD-TICKER-NO-DESC", "Chicago", "yes", 5, 60, 0.7, 0.8, "paper",
        threshold_description=None,
    )
    html = generate_dashboard_html(_minimal_cfg(), path)
    assert "OLD-TICKER-NO-DESC" in html
    assert "None" not in html
