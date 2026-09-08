# Predict Weather Bot v4.0

An automated weather-market trading bot for Kalshi, built to run for free
on GitHub Actions — no server, no Python installation, no coding
required to operate it day to day.

**New here? Start with [`QUICKSTART.md`](QUICKSTART.md)** — a click-by-click
setup guide that assumes no GitHub or Python experience.

**This is speculative software, not financial advice.** Read
[`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) before ever switching out
of paper-trading mode. You can lose the full amount you deploy.

**Migrating from v3.0?** This is a fresh, separate repo. v3.0's trades
are worth keeping as a record (it was the first version to genuinely
work end-to-end), but v3.0 had no way to ever close out a position —
see "What's new in v4.0" below. Set up v4.0 fresh rather than trying to
upgrade in place.

---

## What's new in v4.0

- **Fixed the biggest remaining gap: positions never actually settled.**
  Through v1-v3, nothing checked whether an open position had resolved
  on Kalshi's side — the settlement code existed and was tested, but
  nothing ever called it against real data. Positions stayed "open"
  forever and Net P&L always showed $0.00, even days after real
  (simulated) wins and losses occurred. Found by the user asking why a
  day's results weren't showing up. `src/settle_check.py` now runs at
  the start of every Price Check and closes out anything Kalshi shows a
  real result for.
- **New: a visual dashboard**, hosted free on GitHub Pages, regenerated
  after every Price Check run — P&L chart, per-city breakdown, open and
  settled positions in plain English, and the Brier self-audit, all on
  one page.
- **New: a per-run deployment cap.** The first successful run deployed
  97% of the total budget in a single pass. `max_new_trades_per_run` in
  `config.yaml` spreads deployment across multiple runs instead.
- **Clearer error messaging** for the 401 Unauthorized on
  `/portfolio/positions` — a real Kalshi API key permissions issue,
  now explained directly in the log rather than just logged as an
  opaque error.
- **`KXHIGHLAX` (Los Angeles) confirmed correct** — real trades appeared
  in production, resolving the "best guess" status it had through v1-v3.

---

## What this does, in one paragraph

Every 5 minutes, it checks current Kalshi weather-market prices against
a forecast-derived probability (refreshed 6x/day, matching NOAA's actual
publish schedule) — and, if the gap between model and market is large
enough and a few quality checks pass, opens a small position. It starts
in **paper mode** (simulated, no real money) and stays there until you
deliberately switch it. Each morning, it posts a plain-English summary to
a GitHub Issue you can read without touching any code.

---

## Where to find things

| I want to... | Go to... |
|---|---|
| Set this up for the first time | [`QUICKSTART.md`](QUICKSTART.md) |
| Understand what a phrase in my daily summary means | [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) |
| Know what this can't do yet, or what's unproven | [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) |
| See what's planned for the next version | [`IMPROVEMENTS.md`](IMPROVEMENTS.md) |
| Adjust cities, budget, or risk settings | `config.yaml` (every line has a comment) |

---

## What's included

- **Weather bot** for 5 cities (Chicago, New York, Miami, Austin, Los
  Angeles) — configurable in `config.yaml`
- **Paper and live trading modes**, one line to switch, hard budget caps
  in either mode
- **A settlement checker** that closes out positions once Kalshi shows a
  real result — the piece that was missing entirely through v1-v3
- **A visual dashboard** on GitHub Pages, regenerated after every scan
- **Daily summary** posted automatically as a GitHub Issue comment,
  including a P&L sparkline and scan/cache diagnostics
- **Self-audit Brier score tracking**, surfaced in both the daily summary
  and the dashboard once enough trades have settled
- **Same-day alerts** (a separate GitHub Issue) for a position mismatch,
  the daily loss limit, or an unrecognized settlement result
- **A per-run deployment cap**, so budget spreads across multiple runs
  instead of deploying almost entirely on the first successful one
- **A standalone NBM connectivity check**, run manually, before trusting
  the automated pipeline
- **109 automated tests**, including dedicated regression coverage for
  the fixed-width column parsing bug and the wrong-hostname bug found
  while building this project

---

## Running tests locally (optional — only if you want to verify changes)

```bash
pip install -r requirements.txt pytest
pytest tests/ -v
```

You don't need to do this to use the bot day-to-day; it's only relevant
if you (or I, on your behalf) change the underlying code.
