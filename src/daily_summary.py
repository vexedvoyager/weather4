"""
Generates a plain-English daily summary. This is the one file you should
need to read each day - no code-reading required.

Run manually with: python -m src.daily_summary
Or scheduled via .github/workflows/daily-summary.yml
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import brier_tracker, db
from src.config import load_config, resolve_path

SPARK_CHARS = "▁▂▃▄▅▆▇█"


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


def generate_summary(cfg: dict, date_str: str = None) -> str:
    db_path = str(resolve_path(cfg, "database"))
    if date_str is None:
        date_str = datetime.now(timezone.utc).date().isoformat()

    lines = []
    lines.append(f"=== Predict Weather Bot — Daily Summary: {date_str} ===")
    lines.append(f"Mode: {cfg['mode'].upper()}")
    lines.append("")

    with db.get_connection(db_path) as conn:
        opened_today = conn.execute(
            "SELECT * FROM trades WHERE opened_at LIKE ?", (f"{date_str}%",)
        ).fetchall()
        settled_today = conn.execute(
            "SELECT * FROM trades WHERE settled_at LIKE ?", (f"{date_str}%",)
        ).fetchall()
        scans_today = conn.execute(
            "SELECT * FROM scan_log WHERE run_at LIKE ?", (f"{date_str}%",)
        ).fetchall()

    lines.append(f"Scans run today: {len(scans_today)}")
    mismatches = [s for s in scans_today if s["position_mismatch"]]
    if mismatches:
        lines.append(
            f"  ⚠ {len(mismatches)} scan(s) flagged a POSITION MISMATCH between "
            f"the local database and your live Kalshi account. Check the logs "
            f"before trusting today's numbers."
        )
    lines.append("")

    # --- v3 diagnostics: the "why is nothing happening" answer ---------------
    # Added after a real incident where scans ran (nonzero count) but every
    # single one failed silently upstream (wrong NBM hostname), producing
    # zero trades with no visible explanation in the old summary format.
    totals = db.get_todays_scan_totals(db_path, date_str)
    lines.append(f"Markets scanned today (total across all scans): {totals['total_scanned']}")
    lines.append(f"Markets that cleared all eligibility checks: {totals['total_eligible']}")

    cache_coverage = db.get_forecast_cache_coverage(db_path)
    if not cache_coverage:
        lines.append(
            "  ⚠ Forecast cache is completely EMPTY across all cities. Price "
            "Check has nothing to compare market prices against, which fully "
            "explains zero trades regardless of how selective the thresholds "
            "are. Check the most recent Forecast Refresh run's logs."
        )
    else:
        cache_summary = ", ".join(f"{city}: {count}" for city, count in cache_coverage.items())
        lines.append(f"Forecast cache coverage by city: {cache_summary}")
    lines.append("")

    lines.append(f"New positions opened today: {len(opened_today)}")
    for t in opened_today:
        description = t["threshold_description"] or t["ticker"]
        lines.append(
            f"  - {t['city']}: bought {t['count']}x {t['side'].upper()} — {description} — "
            f"at {t['entry_price_cents']}c "
            f"(model said {t['forecast_prob']*100:.0f}% chance, edge score "
            f"{t['composite_edge_score']:.2f}) [{t['ticker']}]"
        )
    lines.append("")

    lines.append(f"Positions settled today: {len(settled_today)}")
    total_pnl = 0
    for t in settled_today:
        pnl = t["pnl_cents"] or 0
        total_pnl += pnl
        result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "PUSH/VOID")
        description = t["threshold_description"] or t["ticker"]
        lines.append(
            f"  - {t['city']}: {description} settled {t['outcome']} → {result} "
            f"({pnl/100:+.2f} USD after fees) [{t['ticker']}]"
        )
    lines.append("")
    lines.append(f"Net P&L settled today: ${total_pnl/100:+.2f}")

    total_deployed = db.total_deployed_cents(db_path)
    lines.append(f"Total currently deployed (open positions): ${total_deployed/100:.2f} "
                 f"of ${cfg['risk']['total_budget_usd']:.2f} budget")

    open_count = db.count_open_trades(db_path)
    lines.append(f"Open positions right now: {open_count}")
    lines.append("")

    # --- 14-day P&L trend, as a quick-glance sparkline -----------------------
    pnl_series = get_daily_pnl_series(db_path, num_days=14)
    daily_values = [cents / 100 for _, cents in pnl_series]
    cumulative_values = []
    running = 0
    for _, cents in pnl_series:
        running += cents
        cumulative_values.append(running / 100)

    lines.append("Last 14 days (daily P&L):      " + ascii_sparkline(daily_values))
    lines.append("Last 14 days (cumulative P&L): " + ascii_sparkline(cumulative_values))
    lines.append(
        f"  (range: ${min(daily_values):+.2f} to ${max(daily_values):+.2f} daily, "
        f"cumulative now ${cumulative_values[-1]:+.2f})"
    )
    lines.append("")

    if cfg["mode"] == "paper":
        lines.append(
            "This is all simulated — no real money is at risk. Once these "
            "numbers look sane over a couple of weeks, consider switching "
            "config.yaml's `mode` to 'live' with your small risk capital."
        )

    # --- Self-audit Brier tracking (v3) ---------------------------------------
    # The direct replacement for the backtest dropped in v2.0 - computed
    # from the bot's own settled trades, no external data needed.
    brier_summary = brier_tracker.compute_brier_summary(db_path)
    brier_text = brier_tracker.format_brier_summary(brier_summary)
    if brier_text:
        lines.append("")
        lines.append(brier_text)

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
