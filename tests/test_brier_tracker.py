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
    """Buying 'no' and having the market resolve 'no' should count as a win.

    forecast_prob is P(YES). A model that says P(YES)=0.2 and sees NO win
    predicted the traded side's win at 0.8, so Brier = (0.8-1)^2 = 0.04.
    (Before v6.0 this test used forecast_prob=0.8 - encoding the bug where
    P(YES) was scored as if it were the traded side's probability.)
    """
    path = _temp_db()
    for _ in range(35):
        _settle_trade(path, "no", 0.2, "no")  # P(YES)=0.2, NO won
    summary = compute_brier_summary(path)
    assert abs(summary["model_brier"] - 0.04) < 1e-6


def test_v6_regression_no_trade_with_low_p_yes_is_not_scored_as_confident_loss():
    """The exact bug found in v5 data: a NO trade whose model P(YES)=0.10
    that WINS must score as a good prediction, not a terrible one."""
    path = _temp_db()
    for _ in range(35):
        _settle_trade(path, "no", 0.10, "no")
    summary = compute_brier_summary(path)
    assert summary["model_brier"] < 0.02   # (0.9 - 1)^2 = 0.01
    # Old (buggy) scoring would have been (0.10 - 1)^2 = 0.81


def test_yes_and_no_trades_are_scored_symmetrically():
    path = _temp_db()
    for _ in range(20):
        _settle_trade(path, "yes", 0.8, "yes")   # right, 0.8 on YES
    for _ in range(20):
        _settle_trade(path, "no", 0.2, "no")     # right, 0.8 on NO
    summary = compute_brier_summary(path)
    assert abs(summary["model_brier"] - 0.04) < 1e-6


def test_market_price_brier_uses_price_paid_for_traded_side():
    path = _temp_db()
    for _ in range(35):
        # entry price 60c for the traded side, which wins: (0.6-1)^2 = 0.16
        _settle_trade(path, "yes", 0.9, "yes")
    summary = compute_brier_summary(path)
    assert abs(summary["market_brier"] - 0.16) < 1e-6
    assert summary["beats_market"] is True   # model 0.01 < market 0.16


def test_beats_market_false_when_model_is_worse():
    path = _temp_db()
    for _ in range(35):
        _settle_trade(path, "yes", 0.99, "no")   # confidently wrong
    summary = compute_brier_summary(path)
    assert summary["beats_market"] is False


def test_by_version_splits_scores():
    path = _temp_db()
    for _ in range(35):
        _settle_trade(path, "yes", 0.9, "yes")
    # re-stamp half as v5 to simulate two versions
    with db.get_connection(path) as conn:
        conn.execute("UPDATE trades SET bot_version = 'v5' WHERE id <= 10")
        conn.commit()
    summary = compute_brier_summary(path)
    from src.version import BOT_VERSION
    assert set(summary["by_version"]) == {"v5", BOT_VERSION}
    assert summary["by_version"]["v5"]["n"] == 10
    only_v5 = compute_brier_summary(path, bot_version="v5")
    assert only_v5["n"] == 10


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
    summary = {"n": 100, "model_brier": 0.28, "market_brier": 0.14, "base_rate_brier": 0.24,
               "base_rate": 0.5, "verdict": "NO SKILL", "beats_market": False,
               "confidence_level": "confident", "by_version": {}}
    text = format_brier_summary(summary)
    assert "NO SKILL" in text
    assert "rebuild" in text.lower()


def test_format_summary_shows_market_benchmark():
    summary = {"n": 100, "model_brier": 0.28, "market_brier": 0.14, "base_rate_brier": 0.24,
               "base_rate": 0.5, "verdict": "NO SKILL", "beats_market": False,
               "confidence_level": "confident", "by_version": {}}
    text = format_brier_summary(summary)
    assert "0.1400" in text
    assert "WORSE THAN" in text


def test_observation_brier_scores_model_and_market_against_yes_outcome():
    from src.brier_tracker import compute_observation_brier, format_observation_brier
    path = _temp_db()
    # model says 0.9 YES, market 0.5, outcome yes
    db.insert_market_observation(path, "T1", "Miami", False, "high 90-91°F", 0.9, 0.9, 50, 48, 100)
    # model says 0.9 YES, market 0.1, outcome no  (shadow city)
    db.insert_market_observation(path, "T2", "Denver", True, "high > 70°F", 0.9, 0.9, 10, 8, 100)
    for obs in db.get_pending_market_observations(path):
        db.settle_market_observation(path, obs["id"], "yes" if obs["ticker"] == "T1" else "no", "91")
    result = compute_observation_brier(path)
    assert result["by_city"]["Miami"]["model_brier"] == 0.01      # (0.9-1)^2
    assert result["by_city"]["Miami"]["market_brier"] == 0.25     # (0.5-1)^2
    assert result["by_city"]["Denver"]["is_shadow"] is True
    assert result["by_city"]["Denver"]["model_brier"] == 0.81     # (0.9-0)^2
    text = format_observation_brier(result)
    assert "Denver (shadow)" in text and "market closer" in text


def test_observation_brier_reports_unclamped_alongside_clamped():
    from src.brier_tracker import compute_observation_brier, format_observation_brier
    path = _temp_db()
    # clamped 0.95 vs raw 0.99, outcome yes
    db.insert_market_observation(path, "T1", "Miami", False, "high > 80°F", 0.95, 0.99, 50, 48, 100)
    for obs in db.get_pending_market_observations(path):
        db.settle_market_observation(path, obs["id"], "yes", None)
    result = compute_observation_brier(path)
    assert result["overall"]["model_brier"] == 0.0025        # (0.95-1)^2
    assert result["overall"]["raw_model_brier"] == 0.0001    # (0.99-1)^2
    assert "unclamped 0.000" in format_observation_brier(result)
