# Predict Weather Bot v6.0 — release notes

**Theme: diagnose and instrument. Trading behavior is unchanged from v5.**
v6.0 exists to find out WHY the model loses to the market, and to start
collecting the data needed to fix it properly in v6.1.

## What changed

| Item | What | Where |
|---|---|---|
| #19 | NBM column diagnostic: shows which forecast column the bot reads for each market (daily max vs overnight min) | `src/diagnose_columns.py`, workflow **Diagnose NBM Columns** |
| #20 | Brier tracker fix: NO trades were scored on the wrong probability. Now also reports the market-price Brier and splits by version | `src/brier_tracker.py` |
| #18 | `bot_version` stamped on trades, shadow trades, observations; version printed in the summary and dashboard; v4/v5 history backfilled by go-live date | `src/version.py`, `src/db.py` |
| #21 | Each trade records which NBM run/column/valid-time/percentile ladder produced its probability, plus Kalshi's `expiration_value` at settlement | `src/db.py`, `forecast_refresh.py`, `settle_check.py` |
| #22 | Market observations: one model-vs-market snapshot per market for EVERY city (including shadow-only cities), settled later — an unbiased scorecard | `src/observations.py`, `config.yaml: shadow_cities` |
| #23 | Series discovery: lists Kalshi's daily-high series, their settlement stations, and whether NBM can forecast them | `src/list_kalshi_series.py`, workflow **List Kalshi Series** |
| #5 | Fee modeling: estimated entry fee subtracted from paper P&L | `src/fees.py`, `config.yaml: fees` |

## Upgrading

1. Replace/add the files listed in the "update" zip (same folder structure).
   Do NOT upload any `data/trades.db` — your live database stays as is. It
   is migrated automatically the first time v6.0 runs (new columns are added
   and old trades are stamped `v4` / `v5`; nothing is deleted).
2. In GitHub: Actions → **Diagnose NBM Columns** → Run workflow. Download
   the `nbm-column-diagnostic` artifact (or copy the log) and share it.
3. Actions → **List Kalshi Series** → Run workflow. Share its output; we
   pick shadow cities from it.

## What to expect afterwards

* The Daily Summary header reads `v6.0`, the Brier line shows model vs
  market price vs base rate, and a "Model vs market, ALL markets observed"
  section appears once observed markets start settling (about a day).
* The corrected Brier on the v4+v5 history is 0.469 (the old tracker
  reported 0.6235). It is still above the 0.243 base rate: the model has no
  skill yet. That is the problem v6.1 addresses.
* Paper P&L from v6.0 onward includes the estimated fee, so it will look
  slightly worse than the same trades did before.
