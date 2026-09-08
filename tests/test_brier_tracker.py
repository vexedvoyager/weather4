import tempfile
import os

from src import db
from src.brier_tracker import (
    compute_brier_summary, format_brier_summary,
    MIN_TRADES_FOR_ANY_READING, MIN_TRADES_FOR_CONFIDENT_VERDICT,
)


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def _settle_trade(db_path, side, forecast_prob, outcome, pnl_cents=0):
    trade_id = db.insert_trade(
        db_path, "TICKER", "Chicago", side, 10, 60, forecast_prob, 0.7, "paper"
    )
    db.settle_trade(db_path, trade_id, outcome=outcome, pnl_cents=pnl_cents)
    return trade_id


def test_no_settled_trades_returns_zero_n():
    path = _temp_db()
    summary = compute_brier_summary(path)
    assert summary["n"] == 0
    assert summary["verdict"] is None


def test_void_outcomes_are_excluded_from_scoring():
    path = _temp_db()
    _settle_trade(path, "yes", 0.7, "void")
    summary = compute_brier_summary(path)
    assert summary["n"] == 0  # the only trade was void, so nothing scored


def test_perfect_model_scores_zero_brier():
    path = _temp_db()
    # Model says 100% confidence, always right.
    for _ in range(35):
        _settle_trade(path, "yes", 1.0, "yes")
    summary = compute_brier_summary(path)
    assert summary["model_brier"] == 0.0


def test_side_no_win_condition_is_correct():
    """Buying 'no' and having the market resolve 'no' should count as a win."""
    path = _temp_db()
    for _ in range(35):
        _settle_trade(path, "no", 0.8, "no")  # predicted no, correct
    summary = compute_brier_summary(path)
    assert abs(summary["model_brier"] - 0.04) < 1e-6  # (0.8-1)^2 = 0.04


def test_insufficient_confidence_level_below_minimum():
    path = _temp_db()
    for _ in range(MIN_TRADES_FOR_ANY_READING - 1):
        _settle_trade(path, "yes", 0.7, "yes")
    summary = compute_brier_summary(path)
    assert summary["confidence_level"] == "insufficient"


def test_early_read_confidence_level_between_thresholds():
    path = _temp_db()
    for _ in range(MIN_TRADES_FOR_ANY_READING + 5):
        _settle_trade(path, "yes", 0.7, "yes")
    summary = compute_brier_summary(path)
    assert summary["confidence_level"] == "early_read"


def test_confident_level_at_or_above_100():
    path = _temp_db()
    for _ in range(MIN_TRADES_FOR_CONFIDENT_VERDICT):
        _settle_trade(path, "yes", 0.7, "yes")
    summary = compute_brier_summary(path)
    assert summary["confidence_level"] == "confident"


def test_no_skill_verdict_when_model_worse_than_base_rate():
    """
    Mirrors the exact source-material scenario: a model that's confidently
    wrong should score worse than the naive base rate and get a NO SKILL
    verdict.
    """
    path = _temp_db()
    # Model is very confident but wrong about half the time - the classic
    # overconfidence failure mode the source material described.
    for _ in range(20):
        _settle_trade(path, "yes", 0.95, "yes")  # confident and right
    for _ in range(20):
        _settle_trade(path, "yes", 0.95, "no")   # confident and WRONG
    summary = compute_brier_summary(path)
    assert summary["verdict"] == "NO SKILL"


def test_format_summary_empty_when_no_trades():
    summary = {"n": 0, "model_brier": None, "base_rate_brier": None,
               "base_rate": None, "verdict": None, "confidence_level": "insufficient"}
    assert format_brier_summary(summary) == ""


def test_format_summary_shows_insufficient_message():
    summary = {"n": 10, "model_brier": None, "base_rate_brier": None,
               "base_rate": None, "verdict": None, "confidence_level": "insufficient"}
    text = format_brier_summary(summary)
    assert "10 settled trade" in text
    assert "Too early" in text


def test_format_summary_flags_no_skill_verdict_prominently():
    summary = {"n": 100, "model_brier": 0.28, "base_rate_brier": 0.24,
               "base_rate": 0.5, "verdict": "NO SKILL", "confidence_level": "confident"}
    text = format_brier_summary(summary)
    assert "NO SKILL" in text
    assert "rebuild" in text.lower()
