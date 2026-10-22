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

v6.0 FIXES (IMPROVEMENTS.md item #20):
  1. forecast_prob in the database is the model's probability that the
     market resolves YES. Earlier versions scored it as the probability
     that the TRADED side wins, which is wrong for every NO trade: a NO
     trade with forecast_prob 0.10 (model: "10% chance of YES") was scored
     as a confident 10% prediction of winning. This inflated the reported
     score (0.6235 reported vs 0.469 corrected on 219 real trades).
  2. The market's own price is now scored alongside the model, because
     beating the base rate is not enough - the model has to beat what the
     market already knows. (On the same 219 trades: model 0.469, base rate
     0.243, market price 0.137.)
  3. Results can be split per bot_version, and an unbiased per-city
     scorecard is computed from market_observations (every market, not
     just the ones that were traded).

This module does nothing on its own - it's read by src/daily_summary.py
once enough trades have settled, and stays silent before that threshold
so the daily summary doesn't show a misleadingly precise-looking verdict
on a handful of trades.
"""
from src import db

MIN_TRADES_FOR_ANY_READING = 30
MIN_TRADES_FOR_CONFIDENT_VERDICT = 100


def _traded_side_probability(side: str, forecast_prob: float) -> float:
    """Model's probability that the TRADED side wins. forecast_prob is
    P(YES), so a NO trade wins with probability 1 - forecast_prob."""
    return forecast_prob if side == "yes" else 1.0 - forecast_prob


def _actual_outcome(side: str, outcome: str) -> float | None:
    """
    Converts a trade's (side, outcome) into 1.0/0.0 for Brier scoring:
    1.0 if the traded side won. Returns None for void outcomes - these
    shouldn't be scored as either a win or a loss.
    """
    if outcome == "void":
        return None
    won = (side == "yes" and outcome == "yes") or (side == "no" and outcome == "no")
    return 1.0 if won else 0.0


def _score_rows(rows) -> dict | None:
    """Model / market / base-rate Brier over trade rows (traded-side basis).
    Returns None if nothing scoreable."""
    preds, mkt, outs = [], [], []
    for row in rows:
        actual = _actual_outcome(row["side"], row["outcome"])
        if actual is None:
            continue  # void - not scored
        preds.append(_traded_side_probability(row["side"], row["forecast_prob"]))
        mkt.append(row["entry_price_cents"] / 100.0)
        outs.append(actual)
    n = len(outs)
    if n == 0:
        return None
    base_rate = sum(outs) / n
    return {
        "n": n,
        "base_rate": base_rate,
        "model_brier": sum((p - o) ** 2 for p, o in zip(preds, outs)) / n,
        "market_brier": sum((m - o) ** 2 for m, o in zip(mkt, outs)) / n,
        "base_rate_brier": sum((base_rate - o) ** 2 for o in outs) / n,
    }


def compute_brier_summary(db_path: str, bot_version: str = None) -> dict:
    """
    Pulls every settled trade and computes the model's Brier score against
    the naive base-rate Brier AND the market-price Brier.

    bot_version: if given, only trades stamped with that version are scored.

    Returns a dict with n, model_brier, market_brier, base_rate_brier,
    base_rate, verdict ("SKILL"/"NO SKILL" vs the base rate),
    beats_market (bool), confidence_level ("insufficient" / "early_read" /
    "confident"), and by_version ({version: {n, model_brier, market_brier,
    base_rate_brier}}). n=0 fields are None if there's no settled data.
    """
    query = (
        "SELECT side, outcome, forecast_prob, entry_price_cents, bot_version "
        "FROM trades WHERE status = 'settled' AND outcome IS NOT NULL"
    )
    params = ()
    if bot_version is not None:
        query += " AND bot_version = ?"
        params = (bot_version,)
    with db.get_connection(db_path) as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    scored = _score_rows(rows)
    if scored is None:
        return {
            "n": 0, "model_brier": None, "market_brier": None, "base_rate_brier": None,
            "base_rate": None, "verdict": None, "beats_market": None,
            "confidence_level": "insufficient", "by_version": {},
        }

    n = scored["n"]
    if n < MIN_TRADES_FOR_ANY_READING:
        confidence_level = "insufficient"
    elif n < MIN_TRADES_FOR_CONFIDENT_VERDICT:
        confidence_level = "early_read"
    else:
        confidence_level = "confident"

    by_version = {}
    versions = sorted({r["bot_version"] or "unversioned" for r in rows})
    for v in versions:
        v_rows = [r for r in rows if (r["bot_version"] or "unversioned") == v]
        v_scored = _score_rows(v_rows)
        if v_scored:
            by_version[v] = {
                "n": v_scored["n"],
                "model_brier": round(v_scored["model_brier"], 4),
                "market_brier": round(v_scored["market_brier"], 4),
                "base_rate_brier": round(v_scored["base_rate_brier"], 4),
            }

    return {
        "n": n,
        "model_brier": round(scored["model_brier"], 4),
        "market_brier": round(scored["market_brier"], 4),
        "base_rate_brier": round(scored["base_rate_brier"], 4),
        "base_rate": round(scored["base_rate"], 4),
        "verdict": "SKILL" if scored["model_brier"] < scored["base_rate_brier"] else "NO SKILL",
        "beats_market": scored["model_brier"] < scored["market_brier"],
        "confidence_level": confidence_level,
        "by_version": by_version,
    }


def compute_observation_brier(db_path: str) -> dict:
    """
    Unbiased scorecard from market_observations (every market observed, not
    just traded ones): model P(YES) vs market YES price, both scored
    against the actual YES/NO outcome. Returns
    {"overall": {...}|None, "by_city": {city: {n, model_brier, market_brier,
    is_shadow}}}.
    """
    rows = db.get_settled_market_observations(db_path)

    def score(rs):
        n = len(rs)
        if n == 0:
            return None
        outs = [1.0 if r["outcome"] == "yes" else 0.0 for r in rs]
        return {
            "n": n,
            "model_brier": round(sum((r["model_prob"] - o) ** 2 for r, o in zip(rs, outs)) / n, 4),
            "market_brier": round(
                sum((r["market_yes_cents"] / 100.0 - o) ** 2 for r, o in zip(rs, outs)) / n, 4),
        }

    by_city = {}
    for city in sorted({r["city"] for r in rows}):
        c_rows = [r for r in rows if r["city"] == city]
        scored = score(c_rows)
        scored["is_shadow"] = bool(c_rows[0]["is_shadow_city"])
        by_city[city] = scored
    return {"overall": score(rows), "by_city": by_city}


def format_observation_brier(obs: dict) -> str:
    """Per-city lines for the daily summary; empty string until something settles."""
    if not obs["overall"]:
        return ""
    lines = [
        "Model vs market, ALL markets observed (unbiased; lower is better):",
    ]
    for city, d in obs["by_city"].items():
        tag = " (shadow)" if d["is_shadow"] else ""
        better = "model" if d["model_brier"] < d["market_brier"] else "market"
        lines.append(
            f"  {city}{tag}: n={d['n']}  model {d['model_brier']:.3f}  "
            f"market {d['market_brier']:.3f}  -> {better} closer"
        )
    o = obs["overall"]
    lines.append(f"  ALL: n={o['n']}  model {o['model_brier']:.3f}  market {o['market_brier']:.3f}")
    return "\n".join(lines)


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

    lines.append(f"Model self-audit ({summary['n']} settled trades; lower Brier is better):")
    lines.append(f"  Model:       {summary['model_brier']:.4f}")
    lines.append(f"  Market price:{summary['market_brier']:>8.4f}   <- the real benchmark")
    lines.append(f"  Base rate:   {summary['base_rate_brier']:.4f}   <- the minimum bar")
    lines.append(f"  Verdict: {summary['verdict']} vs base rate; "
                 f"{'BEATS' if summary['beats_market'] else 'WORSE THAN'} the market price")

    by_version = summary.get("by_version") or {}
    if len(by_version) > 1:
        lines.append("  By version:")
        for v, d in by_version.items():
            lines.append(
                f"    {v}: n={d['n']}  model {d['model_brier']:.3f}  "
                f"market {d['market_brier']:.3f}  base {d['base_rate_brier']:.3f}"
            )

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
