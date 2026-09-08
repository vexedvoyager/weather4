"""
Generates a self-contained HTML dashboard from trades.db, written to
docs/index.html for GitHub Pages to serve as a static file. Run after
every Price Check (see .github/workflows/price-check.yml) so the page
reflects the latest data.

No server, no build step, no live database access at view time - this
script bakes the current data directly into the HTML/JS at generation
time. Chart.js is loaded from a public CDN for the P&L line chart; that
CDN script tag is the only "code" involved in Chart.js - nothing to
install or maintain.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import brier_tracker, db
from src.config import load_config, resolve_path


def _get_all_time_stats(db_path: str) -> dict:
    with db.get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as settled_count,
                COALESCE(SUM(pnl_cents), 0) as total_pnl_cents,
                SUM(CASE WHEN pnl_cents > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_cents < 0 THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN pnl_cents = 0 THEN 1 ELSE 0 END) as pushes
            FROM trades WHERE status = 'settled'
            """
        ).fetchone()
    return dict(row)


def _get_per_city_stats(db_path: str) -> list:
    with db.get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                city,
                COUNT(*) as settled_count,
                COALESCE(SUM(pnl_cents), 0) as pnl_cents,
                SUM(CASE WHEN pnl_cents > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_cents < 0 THEN 1 ELSE 0 END) as losses
            FROM trades WHERE status = 'settled'
            GROUP BY city ORDER BY city
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _get_open_positions(db_path: str) -> list:
    with db.get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM trades WHERE status IN ('open', 'partially_filled')
            ORDER BY opened_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _get_recent_settled(db_path: str, limit: int = 25) -> list:
    with db.get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status = 'settled' ORDER BY settled_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_pnl_series(db_path: str, num_days: int = 45) -> list:
    today = datetime.now(timezone.utc).date()
    series = []
    for i in range(num_days - 1, -1, -1):
        date_str = (today - timedelta(days=i)).isoformat()
        pnl = db.daily_pnl_cents(db_path, date_str)
        series.append({"date": date_str, "pnl_cents": pnl})
    return series


def _fmt_usd(cents: int) -> str:
    return f"${cents/100:+,.2f}" if cents != 0 else "$0.00"


def _trade_row_html(t: dict, settled: bool) -> str:
    description = t.get("threshold_description") or t["ticker"]
    side_label = t["side"].upper()
    if settled:
        pnl = t["pnl_cents"] or 0
        pnl_class = "gain" if pnl > 0 else ("loss" if pnl < 0 else "neutral")
        outcome_col = f'<td class="{pnl_class}">{t["outcome"].upper()} &middot; {_fmt_usd(pnl)}</td>'
    else:
        outcome_col = '<td class="neutral">open</td>'
    return f"""
    <tr>
      <td>{t['city']}</td>
      <td>{description}</td>
      <td>{side_label}</td>
      <td>{t['count']}x @ {t['entry_price_cents']}&cent;</td>
      <td>{t['forecast_prob']*100:.0f}%</td>
      {outcome_col}
    </tr>"""


def generate_dashboard_html(cfg: dict, db_path: str) -> str:
    stats = _get_all_time_stats(db_path)
    per_city = _get_per_city_stats(db_path)
    open_positions = _get_open_positions(db_path)
    recent_settled = _get_recent_settled(db_path)
    pnl_series = _get_pnl_series(db_path)
    brier = brier_tracker.compute_brier_summary(db_path)

    total_deployed = db.total_deployed_cents(db_path)
    open_count = db.count_open_trades(db_path)
    budget_usd = cfg["risk"]["total_budget_usd"]

    total_settled = stats["settled_count"]
    win_rate = (stats["wins"] / total_settled * 100) if total_settled else None
    total_pnl_cents = stats["total_pnl_cents"]
    pnl_sign_class = "gain" if total_pnl_cents > 0 else ("loss" if total_pnl_cents < 0 else "neutral")

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # --- Chart data -----------------------------------------------------------
    chart_labels = [p["date"][5:] for p in pnl_series]  # MM-DD, shorter axis labels
    cumulative_values = []
    running = 0
    for p in pnl_series:
        running += p["pnl_cents"]
        cumulative_values.append(round(running / 100, 2))

    # --- Per-city table ---------------------------------------------------------
    city_rows = ""
    for c in per_city:
        pnl_class = "gain" if c["pnl_cents"] > 0 else ("loss" if c["pnl_cents"] < 0 else "neutral")
        city_rows += f"""
        <tr>
          <td>{c['city']}</td>
          <td>{c['settled_count']}</td>
          <td>{c['wins']}</td>
          <td>{c['losses']}</td>
          <td class="{pnl_class}">{_fmt_usd(c['pnl_cents'])}</td>
        </tr>"""
    if not city_rows:
        city_rows = '<tr><td colspan="5" class="neutral">No settled trades yet.</td></tr>'

    open_rows = "".join(_trade_row_html(t, settled=False) for t in open_positions) or \
        '<tr><td colspan="6" class="neutral">No open positions.</td></tr>'
    settled_rows = "".join(_trade_row_html(t, settled=True) for t in recent_settled) or \
        '<tr><td colspan="6" class="neutral">No settled trades yet.</td></tr>'

    # --- Brier section -----------------------------------------------------------
    if brier["n"] == 0:
        brier_html = '<p class="neutral">No settled trades yet - nothing to audit.</p>'
    elif brier["confidence_level"] == "insufficient":
        brier_html = (
            f'<p class="neutral">{brier["n"]} settled trade(s) so far - '
            f'need at least {brier_tracker.MIN_TRADES_FOR_ANY_READING} for a meaningful reading.</p>'
        )
    else:
        verdict_class = "gain" if brier["verdict"] == "SKILL" else "loss"
        confidence_note = (
            "" if brier["confidence_level"] == "confident"
            else f'(early read, {brier_tracker.MIN_TRADES_FOR_CONFIDENT_VERDICT}+ needed for full confidence)'
        )
        brier_html = f"""
        <div class="brier-grid">
          <div><span class="label">Trades audited</span><span class="value">{brier['n']}</span></div>
          <div><span class="label">Model Brier score</span><span class="value">{brier['model_brier']:.4f}</span></div>
          <div><span class="label">Base rate Brier score</span><span class="value">{brier['base_rate_brier']:.4f}</span></div>
          <div><span class="label">Verdict</span><span class="value {verdict_class}">{brier['verdict']}</span></div>
        </div>
        <p class="neutral">{confidence_note}</p>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Predict Weather Bot — Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #131A24;
    --surface: #1B2430;
    --rule: #2B3644;
    --text: #EDEFF2;
    --muted: #8A96A3;
    --gain: #E8A33D;
    --loss: #4FA8D8;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Sans', sans-serif;
    margin: 0;
    padding: 0;
    line-height: 1.5;
  }}
  .wrap {{
    max-width: 840px;
    margin: 0 auto;
    padding: 48px 24px 80px;
  }}
  .mono {{ font-family: 'IBM Plex Mono', monospace; }}
  h1 {{
    font-size: 1.1rem;
    font-weight: 500;
    color: var(--muted);
    margin: 0 0 4px;
  }}
  .hero {{
    margin: 8px 0 24px;
  }}
  .hero-number {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 3.4rem;
    font-weight: 600;
    line-height: 1.1;
  }}
  .hero-label {{
    color: var(--muted);
    font-size: 0.95rem;
    margin-top: 4px;
  }}
  .conditions {{
    display: flex;
    flex-wrap: wrap;
    gap: 24px;
    margin: 20px 0 8px;
    padding: 16px 0;
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
  }}
  .conditions div {{
    display: flex;
    flex-direction: column;
  }}
  .label {{
    color: var(--muted);
    font-size: 0.8rem;
    margin-bottom: 2px;
  }}
  .value {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.1rem;
    font-weight: 500;
  }}
  section {{
    margin-top: 40px;
  }}
  section h2 {{
    font-size: 1rem;
    font-weight: 500;
    color: var(--muted);
    margin: 0 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--rule);
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  th {{
    text-align: left;
    color: var(--muted);
    font-weight: 500;
    font-size: 0.8rem;
    padding: 6px 8px;
    border-bottom: 1px solid var(--rule);
  }}
  td {{
    padding: 8px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
    border-bottom: 1px solid var(--rule);
  }}
  td:first-child, th:first-child {{ padding-left: 0; }}
  .gain {{ color: var(--gain); }}
  .loss {{ color: var(--loss); }}
  .neutral {{ color: var(--muted); }}
  .chart-wrap {{
    position: relative;
    height: 280px;
    margin-top: 8px;
  }}
  .brier-grid {{
    display: flex;
    flex-wrap: wrap;
    gap: 32px;
  }}
  .brier-grid > div {{
    display: flex;
    flex-direction: column;
  }}
  footer {{
    margin-top: 56px;
    padding-top: 16px;
    border-top: 1px solid var(--rule);
    color: var(--muted);
    font-size: 0.8rem;
  }}
</style>
</head>
<body>
<div class="wrap">

  <h1>Predict Weather Bot</h1>
  <div class="hero">
    <div class="hero-number {pnl_sign_class}">{_fmt_usd(total_pnl_cents)}</div>
    <div class="hero-label">Total settled P&amp;L, {total_settled} trade(s) settled{f", {win_rate:.0f}% win rate" if win_rate is not None else ""}</div>
  </div>

  <div class="conditions">
    <div><span class="label">Mode</span><span class="value">{cfg['mode'].upper()}</span></div>
    <div><span class="label">Deployed</span><span class="value">${total_deployed/100:.2f} / ${budget_usd:.2f}</span></div>
    <div><span class="label">Open positions</span><span class="value">{open_count}</span></div>
    <div><span class="label">Last updated</span><span class="value">{now_str}</span></div>
  </div>

  <section>
    <h2>P&amp;L over time</h2>
    <div class="chart-wrap"><canvas id="pnlChart"></canvas></div>
  </section>

  <section>
    <h2>By city</h2>
    <table>
      <tr><th>City</th><th>Settled</th><th>Wins</th><th>Losses</th><th>Net P&amp;L</th></tr>
      {city_rows}
    </table>
  </section>

  <section>
    <h2>Open positions ({open_count})</h2>
    <table>
      <tr><th>City</th><th>Contract</th><th>Side</th><th>Position</th><th>Model</th><th>Status</th></tr>
      {open_rows}
    </table>
  </section>

  <section>
    <h2>Recent settled trades</h2>
    <table>
      <tr><th>City</th><th>Contract</th><th>Side</th><th>Position</th><th>Model</th><th>Outcome</th></tr>
      {settled_rows}
    </table>
  </section>

  <section>
    <h2>Model self-audit (Brier score)</h2>
    {brier_html}
  </section>

  <footer>
    Paper trading — simulated, no real money at risk. Generated automatically after every Price Check run.
  </footer>
</div>

<script>
const ctx = document.getElementById('pnlChart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: {json.dumps(chart_labels)},
    datasets: [
      {{
        label: 'Cumulative P&L ($)',
        data: {json.dumps(cumulative_values)},
        borderColor: '#E8A33D',
        backgroundColor: 'rgba(232, 163, 61, 0.12)',
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.25,
      }},
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
    }},
    scales: {{
      x: {{
        grid: {{ color: '#2B3644' }},
        ticks: {{ color: '#8A96A3', maxTicksLimit: 10 }},
      }},
      y: {{
        grid: {{ color: '#2B3644' }},
        ticks: {{ color: '#8A96A3', callback: (v) => '$' + v }},
      }},
    }},
  }}
}});
</script>
</body>
</html>
"""
    return html


if __name__ == "__main__":
    cfg = load_config()
    db_path = str(resolve_path(cfg, "database"))
    db.init_db(db_path)

    html = generate_dashboard_html(cfg, db_path)

    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    out_path = docs_dir / "index.html"
    out_path.write_text(html)
    print(f"Dashboard written to {out_path}")
