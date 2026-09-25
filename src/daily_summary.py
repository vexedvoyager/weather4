"""
Generates a plain-English daily summary. This is the one file you should
need to read each day - no code-reading required.

v5 REDESIGN (item #15 in IMPROVEMENTS.md): consolidates items #6
(yesterday's P&L), #7 (actual outcome context), #8 (trade side shown on
settled lines), #9 (decimal precision on extreme %), #12 (open positions
with age), #13 (stuck-position flagging), and #14 (the rolling-window
fix for invisible late-day trades) into one coherent design, rather than
seven independent patches that would have produced a cluttered report.

Design principle: a short, always-present CORE, plus CONDITIONAL
sections that only appear when something is actually worth flagging -
length should reflect how much needs attention, not grow by default.

Honest scope note on item #7: the originally-discussed "show the actual
winning bracket for the whole city" would need a new live Kalshi query
(to see brackets we didn't personally trade) - not built here. What IS
built: for single-threshold ("greater/less than X") trades, the real
outcome is directly derivable from our own settlement data alone (e.g.
a loss on "less than 88" tells us the high DID reach 88+), so that
context is shown. For "between" brackets we didn't trade, we don't have
enough information to state the true winner without guessing, so none
is shown - accurate is better than complete here.

Run manually with: python -m src.daily_summary
Or scheduled via .github/workflows/daily-summary.yml
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import brier_tracker, db
from src.config import load_config, resolve_path

SPARK_CHARS = "▁▂▃▄▅▆▇█"

# A position open longer than this without settling gets flagged - see
# IMPROVEMENTS.md item #13. Kalshi's own rulebook allows settlement as
# late as 10 AM ET the day after a period ends, so this is set loosely
# enough to not false-alarm on normal timing.
STUCK_POSITION_HOURS = 36


def ascii_sparkline(values: list) -> str:
    """
    Renders a list of numbers as a compact text sparkline, e.g. '▂▃▅█▇▄▆'.
    Handles all-equal and empty inputs without dividing by zero.
    """
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        mid_char = SPARK_CHARS[len(SPARK_CHARS) // 2]
        return mid_char * len(values)
    span = hi - lo
    chars = []
    for v in values:
        idx = int((v - lo) / span * (len(SPARK_CHARS) - 1))
        chars.append(SPARK_CHARS[idx])
    return "".join(chars)


def get_daily_pnl_series(db_path: str, num_days: int = 14) -> list:
    """Returns [(date_str, pnl_cents), ...] for the last num_days, oldest first."""
    today = datetime.now(timezone.utc).date()
    series = []
    for i in range(num_days - 1, -1, -1):
        date_str = (today - timedelta(days=i)).isoformat()
        pnl = db.daily_pnl_cents(db_path, date_str)
        series.append((date_str, pnl))
    return series


def format_probability(prob: float) -> str:
    """
    Item #9: whole-percent rounding makes a genuine 0.3% or 99.7% read
    display as a flat "0%"/"100%" - visually indistinguishable from a
    broken computation. Show one decimal place near the extremes so a
    real tail estimate is distinguishable from something suspicious.

    Uses the SAME bounds as _is_extreme() (the v5 confidence floor/ceiling)
    rather than a separate hardcoded threshold - a clamped real-trade
    value will always land at exactly PROBABILITY_FLOOR/CEILING, so this
    keeps "shown with decimal precision" and "flagged as extreme"
    consistent. Unclamped (raw/shadow-trade) values can be genuinely
    finer-grained than that (e.g. 99.7%), which decimal precision still
    correctly surfaces.
    """
    from src.probability import PROBABILITY_FLOOR, PROBABILITY_CEILING
    pct = prob * 100
    if prob <= PROBABILITY_FLOOR or prob >= PROBABILITY_CEILING:
        return f"{pct:.1f}%"
    return f"{pct:.0f}%"


def _is_extreme(prob: float) -> bool:
    """Marks a trade with [!] for extra scrutiny - see the raw/clamped
    percentile-context line below. Extreme here means AT the v5
    floor/ceiling clamp (see src/probability.py), since that's exactly
    where a reading is least trustworthy."""
    from src.probability import PROBABILITY_FLOOR, PROBABILITY_CEILING
    return prob <= PROBABILITY_FLOOR or prob >= PROBABILITY_CEILING


def _actual_outcome_note(description: str, outcome: str) -> str:
    """
    Item #7 (honest, derivable-only-from-our-own-data version - see
    module docstring). Only single-threshold ("high < X" / "high > X")
    descriptions can be cleanly negated into a real statement about what
    actually happened; "between" brackets are left alone rather than
    guessing at a bracket we don't have visibility into.
    """
    if not description or outcome not in ("yes", "no"):
        return ""
    if description.startswith("high < ") or description.startswith("high > "):
        symbol = description[5]
        value = description[7:]
        if outcome == "yes":
            return f"confirmed: {description}"
        else:
            flipped = "≥" if symbol == "<" else "≤"
            return f"actual: high {flipped} {value}"
    return ""


def _format_new_trade_line(t: dict) -> str:
    description = t["threshold_description"] or t["ticker"]
    flag = " [!]" if _is_extreme(t["forecast_prob"]) else ""
    return (
        f"  {t['city']}: {t['side'].upper()} on {description} @ "
        f"{t['entry_price_cents']}c ({format_probability(t['forecast_prob'])} conf)"
        f"{flag} [{t['ticker']}]"
    )


def _format_settled_trade_line(t: dict) -> str:
    pnl = t["pnl_cents"] or 0
    result = "WON" if pnl > 0 else ("LOST" if pnl < 0 else "PUSH")
    description = t["threshold_description"] or t["ticker"]
    flag = " [!]" if _is_extreme(t["forecast_prob"]) else ""
    line = (
        f"  {t['city']}: {t['side'].upper()} on {description} — "
        f"{result} {pnl/100:+.2f}{flag}"
    )
    note = _actual_outcome_note(description, t["outcome"])
    if note:
        line += f"  |  {note}"
    line += f" [{t['ticker']}]"
    return line


def _get_stuck_positions(db_path: str) -> list:
    """Item #13: flag positions open longer than STUCK_POSITION_HOURS,
    rather than silently listing every open position's age every day."""
    open_trades = db.get_open_trade_rows(db_path)
    now = datetime.now(timezone.utc)
    stuck = []
    for t in open_trades:
        opened_at = datetime.fromisoformat(t["opened_at"])
        age_hours = (now - opened_at).total_seconds() / 3600
        if age_hours > STUCK_POSITION_HOURS:
            stuck.append((t, age_hours))
    return stuck


def generate_summary(cfg: dict, date_str: str = None) -> str:
    db_path = str(resolve_path(cfg, "database"))
    db.init_db(db_path)  # ensures schema migrations are applied even if this
    # workflow runs before Price Check has had a chance to (found while
    # testing this against real production data - a real gap, not just
    # a test artifact).
    now = datetime.now(timezone.utc)
    if date_str is None:
        date_str = now.date().isoformat()

    # --- Rolling 24h window (item #14 fix) ------------------------------------
    # Replaces calendar-day string matching, which silently dropped any
    # trade opened after a fixed-time daily summary run - confirmed via
    # cross-referencing 15 real days of summaries against settlement data.
    cutoff_iso = (now - timedelta(hours=24)).isoformat()
    opened_today = db.get_trades_opened_since(db_path, cutoff_iso)
    settled_today = db.get_trades_settled_since(db_path, cutoff_iso)

    lines = []
    lines.append(f"=== Predict Weather Bot — {date_str} ===")
    lines.append(f"Mode: {cfg['mode'].upper()}")
    lines.append("")

    # --- Core stats line -------------------------------------------------------
    yesterday_str = (now.date() - timedelta(days=1)).isoformat()
    yesterday_pnl = db.daily_pnl_cents(db_path, yesterday_str)
    with db.get_connection(db_path) as conn:
        yesterday_rows = conn.execute(
            "SELECT pnl_cents FROM trades WHERE status='settled' AND settled_at LIKE ?",
            (f"{yesterday_str}%",),
        ).fetchall()
    y_wins = sum(1 for r in yesterday_rows if (r["pnl_cents"] or 0) > 0)
    y_losses = sum(1 for r in yesterday_rows if (r["pnl_cents"] or 0) < 0)

    total_deployed = db.total_deployed_cents(db_path)
    open_count = db.count_open_trades(db_path)

    lines.append(
        f"Yesterday: ${yesterday_pnl/100:+.2f} ({y_wins}W / {y_losses}L)  |  "
        f"Deployed: ${total_deployed/100:.2f}/${cfg['risk']['total_budget_usd']:.0f}  |  "
        f"Open: {open_count}"
    )
    lines.append("")

    # --- Conditional: pipeline health (only shown if something's actually wrong) ---
    with db.get_connection(db_path) as conn:
        scans_today = conn.execute(
            "SELECT * FROM scan_log WHERE run_at >= ?", (cutoff_iso,)
        ).fetchall()
    mismatches = [s for s in scans_today if s["position_mismatch"]]
    totals = db.get_todays_scan_totals(db_path, date_str)
    cache_coverage = db.get_forecast_cache_coverage(db_path)

    health_notes = []
    if mismatches:
        health_notes.append(
            f"⚠ {len(mismatches)} scan(s) flagged a POSITION MISMATCH - "
            f"check logs before trusting today's numbers."
        )
    if not cache_coverage:
        health_notes.append(
            "⚠ Forecast cache is completely EMPTY - Price Check has nothing "
            "to compare prices against. Check the latest Forecast Refresh log."
        )
    if health_notes:
        for note in health_notes:
            lines.append(note)
        lines.append("")

    # --- Conditional: stuck positions -------------------------------------------
    stuck = _get_stuck_positions(db_path)
    if stuck:
        lines.append(f"⚠ {len(stuck)} position(s) open longer than {STUCK_POSITION_HOURS}h - "
                     f"worth checking directly on Kalshi:")
        for t, age_hours in stuck:
            description = t["threshold_description"] or t["ticker"]
            lines.append(f"  {t['city']}: {description}, open {age_hours:.0f}h [{t['ticker']}]")
        lines.append("")

    # --- New trades --------------------------------------------------------------
    lines.append(f"New trades, last 24h ({len(opened_today)}):")
    for t in opened_today:
        lines.append(_format_new_trade_line(t))
    lines.append("")

    # --- Settled -------------------------------------------------------------------
    lines.append(f"Settled, last 24h ({len(settled_today)}):")
    total_pnl = 0
    for t in settled_today:
        total_pnl += t["pnl_cents"] or 0
        lines.append(_format_settled_trade_line(t))
    lines.append("")
    lines.append(f"Net P&L, last 24h: ${total_pnl/100:+.2f}")
    lines.append("")

    # --- Conditional: shadow trades (item #10) ----------------------------------
    shadow_settled_today = db.get_shadow_trades_settled_since(db_path, cutoff_iso)
    shadow_pending = db.get_pending_shadow_trades(db_path)
    if shadow_settled_today or shadow_pending:
        lines.append(
            "Shadow trades (excluded by the confidence floor/ceiling - "
            "NOT real money, tracked to check if the floor was right):"
        )
        for s in shadow_settled_today:
            pnl = s["would_have_pnl_cents"] or 0
            result = "would have WON" if pnl > 0 else ("would have LOST" if pnl < 0 else "push")
            description = s["threshold_description"] or s["ticker"]
            lines.append(
                f"  {s['city']}: {s['side'].upper()} on {description} — {result} "
                f"{pnl/100:+.2f} (raw model said {format_probability(s['raw_prob'])}, "
                f"clamped to {format_probability(s['clamped_prob'])}) [{s['ticker']}]"
            )
        if shadow_pending:
            lines.append(f"  ({len(shadow_pending)} more still pending settlement)")
        shadow_alltime = db.get_shadow_trade_summary(db_path)
        if shadow_alltime["n"] > 0:
            lines.append(
                f"  All-time shadow record: {shadow_alltime['n']} settled, "
                f"{shadow_alltime['wins']}W/{shadow_alltime['losses']}L, "
                f"would-have net ${shadow_alltime['total_pnl_cents']/100:+.2f}"
            )
        lines.append("")

    # --- Model self-audit --------------------------------------------------------
    brier_summary = brier_tracker.compute_brier_summary(db_path)
    if brier_summary["n"] > 0:
        if brier_summary["confidence_level"] == "insufficient":
            lines.append(
                f"Model self-audit: {brier_summary['n']} settled trade(s) - "
                f"need {brier_tracker.MIN_TRADES_FOR_ANY_READING}+ for a meaningful reading."
            )
        else:
            warn = " ⚠" if brier_summary["verdict"] == "NO SKILL" else ""
            lines.append(
                f"Model self-audit ({brier_summary['n']} trades): "
                f"Brier {brier_summary['model_brier']:.4f} vs base rate "
                f"{brier_summary['base_rate_brier']:.4f} — {brier_summary['verdict']}{warn}"
            )
        lines.append("")

    # --- 14-day P&L sparkline --------------------------------------------------
    pnl_series = get_daily_pnl_series(db_path, num_days=14)
    daily_values = [cents / 100 for _, cents in pnl_series]
    cumulative_values = []
    running = 0
    for _, cents in pnl_series:
        running += cents
        cumulative_values.append(running / 100)

    lines.append(
        f"14-day P&L: {ascii_sparkline(daily_values)}  "
        f"(range ${min(daily_values):+.0f} to ${max(daily_values):+.0f}, "
        f"cumulative ${cumulative_values[-1]:+.2f})"
    )

    if cfg["mode"] == "paper":
        lines.append("")
        lines.append(
            "Simulated — no real money at risk. Consider live mode once these "
            "numbers look sane for a couple of weeks."
        )

    return "\n".join(lines)


if __name__ == "__main__":
    cfg = load_config()
    summary = generate_summary(cfg)
    print(summary)

    out_dir = resolve_path(cfg, "daily_summary_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).date().isoformat()
    out_path = out_dir / f"{date_str}.txt"
    out_path.write_text(summary)
