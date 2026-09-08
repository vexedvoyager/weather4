"""
Self-audit Brier score tracking, computed directly from the bot's own
settled paper (or live) trades - no external historical data needed.

This is the direct, working replacement for the backtest feature dropped
in v2.0 after finding NOAA doesn't archive the needed forecast bulletins
long-term. It mirrors the exact methodology the source material's own
team uses on their own trades: Brier score against actual outcomes,
compared to the naive base-rate Brier score, with an explicit
SKILL / NO SKILL verdict.

The source material's own guidance on sample size (echoed here):
  - Below 30 settled trades: too little data, variance too high to trust
  - 30-50: an early, rough read
  - 100+: real confidence in the verdict

This module does nothing on its own - it's read by src/daily_summary.py
once enough trades have settled, and stays silent before that threshold
so the daily summary doesn't show a misleadingly precise-looking verdict
on a handful of trades.
"""
from src import db

MIN_TRADES_FOR_ANY_READING = 30
MIN_TRADES_FOR_CONFIDENT_VERDICT = 100


def _actual_outcome(side: str, outcome: str) -> float | None:
    """
    Converts a trade's (side, outcome) into 1.0/0.0 for Brier scoring,
    where forecast_prob was always the model's confidence that its
    chosen side would win. Returns None for void outcomes - these
    shouldn't be scored as either a win or a loss.
    """
    if outcome == "void":
        return None
    won = (side == "yes" and outcome == "yes") or (side == "no" and outcome == "no")
    return 1.0 if won else 0.0


def compute_brier_summary(db_path: str) -> dict:
    """
    Pulls every settled trade's forecast_prob and actual outcome, and
    computes the model's Brier score against the naive base-rate Brier
    score - the same audit that caught the source material's own model
    having no skill.

    Returns a dict with n, model_brier, base_rate_brier, base_rate,
    verdict, and confidence_level ("insufficient" / "early_read" /
    "confident"). Returns n=0 fields as None if there's no settled data
    at all.
    """
    with db.get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT side, outcome, forecast_prob FROM trades "
            "WHERE status = 'settled' AND outcome IS NOT NULL"
        ).fetchall()

    predictions = []
    outcomes = []
    for row in rows:
        actual = _actual_outcome(row["side"], row["outcome"])
        if actual is None:
            continue  # void - not scored
        predictions.append(row["forecast_prob"])
        outcomes.append(actual)

    n = len(predictions)
    if n == 0:
        return {
            "n": 0, "model_brier": None, "base_rate_brier": None,
            "base_rate": None, "verdict": None, "confidence_level": "insufficient",
        }

    base_rate = sum(outcomes) / n
    model_brier = sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / n
    base_rate_brier = sum((base_rate - o) ** 2 for o in outcomes) / n

    if n < MIN_TRADES_FOR_ANY_READING:
        confidence_level = "insufficient"
    elif n < MIN_TRADES_FOR_CONFIDENT_VERDICT:
        confidence_level = "early_read"
    else:
        confidence_level = "confident"

    verdict = "SKILL" if model_brier < base_rate_brier else "NO SKILL"

    return {
        "n": n,
        "model_brier": round(model_brier, 4),
        "base_rate_brier": round(base_rate_brier, 4),
        "base_rate": round(base_rate, 4),
        "verdict": verdict,
        "confidence_level": confidence_level,
    }


def format_brier_summary(summary: dict) -> str:
    """Plain-English lines for inclusion in the daily summary."""
    lines = []

    if summary["n"] == 0:
        return ""  # nothing settled yet - stay silent, not "N/A" noise

    if summary["confidence_level"] == "insufficient":
        lines.append(
            f"Model self-audit: {summary['n']} settled trade(s) so far - need at "
            f"least {MIN_TRADES_FOR_ANY_READING} before a Brier score reading is "
            f"meaningful. Too early to draw conclusions."
        )
        return "\n".join(lines)

    lines.append(f"Model self-audit ({summary['n']} settled trades):")
    lines.append(f"  Model Brier score:      {summary['model_brier']:.4f}  (lower is better)")
    lines.append(f"  Base rate Brier score:  {summary['base_rate_brier']:.4f}")
    lines.append(f"  Verdict: {summary['verdict']}")

    if summary["confidence_level"] == "early_read":
        lines.append(
            f"  (Early read - {MIN_TRADES_FOR_CONFIDENT_VERDICT}+ trades needed "
            f"for real confidence in this verdict)"
        )

    if summary["verdict"] == "NO SKILL":
        lines.append(
            "  ⚠ The model is currently performing no better than guessing the "
            "historical base rate. This is exactly the warning sign that led to "
            "a full rebuild in the source material this project drew on - worth "
            "taking seriously, especially before considering live mode."
        )

    return "\n".join(lines)
