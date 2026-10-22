# Predict Weather Bot v6.1 — release notes

## v6.1 — the model fix (2026-10-22)

The v6.0 column diagnostic, run on a real NOAA bulletin, found why the model
never beat the market. **From v4 through v6.0, every daily-high market was
forecast from NBM's overnight-MINIMUM column.**

* NBM's `TXN` rows alternate: the **12Z** columns are overnight minimums, the
  **00Z** columns are daytime maximums. (Chicago, run 2026-10-21T19Z: 12Z =
  52°F, 00Z = 70°F. Chicago's market ladder for that day was priced around
  70°F.)
* The bulletin separates cells with `|` after every 12Z column. The parser's
  character window picked up that `|`, so **every 00Z column read as blank**
  and the bot could only ever see the minimums. (It also dropped most columns
  at 100+ forecast hours.)
* Result: the model said "the high will be ~55°F" about days that reached
  ~87°F. It bought YES on every "less than" market (8% won) and NO on nearly
  every bracket. That is the YES-loses / NO-wins pattern seen since week one.

| Change | Where |
|---|---|
| Parser reads the real cell layout (4-character cells, `\|` separators, 3-digit hours) | `src/nbm.py` |
| Daily high comes from the **00Z column of the day after the market date**; never "nearest column" | `nbm.get_max_temp_forecast`, `forecast_refresh.py` |
| **Whole-degree settlement**: "greater than 75" = 76+, "less than 68" = 67−, "70–71" = 69.5 to 71.5. A full ladder of markets now sums to 1.0 (it summed to 0.44–0.68) | `forecast_refresh.model_probability_for_market` |
| Headline Brier scores **only the current version**; v4/v5/v6.0 are listed with a `*` as not a test of NBM | `daily_summary.py`, `generate_dashboard.py`, `version.py` |
| 8 shadow cities prefilled (Denver, Phoenix, Seattle, Atlanta, Dallas, Houston, Boston, Minneapolis) | `config.yaml` |
| Scorecard shows Brier with and without the 5%/95% clamp | `brier_tracker.py` |
| Regression tests built from the **real** bulletin | `tests/test_real_bulletin_v61.py`, `tests/fixtures/` |

Trading rules (edge thresholds, risk limits, the 5%/95% clamp, the
"≥24h to close" same-day floor) are **unchanged**. v6.1 is a clean restart of
the evidence: nothing before it measures how good NBM is.

## v6.0 — diagnose and instrument (2026-10-22)

Brier tracker fix (NO trades were scored on the wrong probability), market
price Brier benchmark, version stamps, forecast provenance, estimated fees,
market observations, shadow cities, series discovery, column diagnostic.

## Upgrading from v6.0

1. Upload the files in the v6.1 update zip (same folder structure), including
   the new `tests/fixtures/` folder. Do NOT upload any `data/trades.db`.
2. Actions → **Diagnose NBM Columns** → Run workflow. Section 5 now shows the
   NEW pick next to the OLD pick for every market; every NEW pick should read
   `[AFTERNOON/EVENING]` at a 00Z time.
3. Wait for the next **Forecast Refresh**. Its log should show markets cached
   for all cities (previously New York and Miami often showed none).

## What to expect

* The Daily Summary header reads `v6.1`; the Brier headline reads "no settled
  v6.1 trades yet" until the first v6.1 trades settle (about 2 days), and
  needs 30+ to be meaningful.
* "Model vs market, ALL markets observed" fills in as v6.1 markets settle —
  including the 8 shadow cities — and is the honest scorecard.
* Verdict gates are unchanged: no live mode until the corrected model has
  100+ settled trades, beats the base rate, and is not worse than the market.
