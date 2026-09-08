# Known Limitations (v4)

Being upfront about what this is and isn't. Worth noting: even the
fully-built, professionally maintained paid product this project drew on
for structure had **no validated edge** as of its own most recent public
status page. Nothing here should be read as "the free version is
behind" — nobody in this picture has a proven track record yet.

## What changed in v4

**The critical fix: a settlement checker now exists.** Through v1-v3, the
bot could open positions but had no code that ever checked whether they'd
resolved - `settle_trade()` was built and tested from the start, but
nothing called it against real Kalshi data. Positions stayed "open"
forever, and Net P&L always showed $0.00 regardless of real outcomes.
Found by the user asking why a day's real (simulated) wins and losses
weren't showing up anywhere. `src/settle_check.py` now runs at the start
of every Price Check and closes out anything Kalshi shows a real result
for.

**Also new:**
- A visual dashboard (`docs/index.html`, via GitHub Pages), regenerated
  after every Price Check run
- A per-run deployment cap (`max_new_trades_per_run` in `config.yaml`) -
  the first successful run deployed 97% of budget in one pass; this
  spreads deployment across multiple runs instead
- Clearer, more actionable error messaging for the 401 Unauthorized on
  `/portfolio/positions` (a real Kalshi API key permissions issue, not a
  code bug - see `TROUBLESHOOTING.md`)
- `KXHIGHLAX` (the Los Angeles series prefix) is now **confirmed
  correct** - real LA trades appeared in production runs, resolving the
  "best guess, unverified" status it had through v1-v3

**Two settlement/market-type edge cases documented, not built (low
priority, unlikely to matter):**
- Kalshi's rulebook allows resolving to "last fair price" at the
  exchange's discretion when no data is available - a third settlement
  outcome beyond yes/no/void. `settle_check.py` detects this (any result
  value it doesn't recognize) and flags it via alert rather than
  guessing how to score it.
- An "exactly" market type exists in Kalshi's rulebook but has never
  appeared in real data across any city so far; `extract_threshold()`
  would safely skip such a market rather than mishandle it.

## Status

v3.0, no confirmed real trades placed yet. 70 automated tests cover the
trap-prone logic (probability math, DST bucketing, settlement accounting,
strike-type parsing, NBM fixed-width column alignment, 429 backoff,
resilient multi-pattern URL fetching, self-audit Brier scoring).

## What changed from v2.0, and why it matters

v2.0 had a critical, silent bug: its NBM fetch used the hostname
`blend.nomads.ncep.noaa.gov`, which doesn't exist — a DNS resolution
failure. Every single Forecast Refresh run failed at the very first
network call, from the moment v2.0 launched. This meant Price Check was
always comparing prices against a completely empty forecast cache,
which fully explains why zero trades were ever recorded. Nothing else in
v2.0 was broken — the rest of the pipeline was faithfully executing
correctly on top of a data source that was never actually reachable.

This was found by reading an actual Forecast Refresh log line by line,
not by reasoning about the code - a reminder that "all tests pass"
only proves the code does what its synthetic test data says it should,
not that its assumptions about the outside world are correct.

## What's now confirmed vs. still a best guess

**Confirmed directly against official documentation and/or a real,
independently-verified example:**
- Kalshi's REST base URL, RSA-PSS signing parameters
- Kalshi's dollar-string price fields, bid-only orderbook structure,
  strike-type-aware threshold fields
- NBM's TXNP1/2/5/7/9 percentile field names
- The correct NBM host (`nomads.ncep.noaa.gov`, no subdomain) — verified
  independently across NOAA's own pages, an official Service Change
  Notice, and Herbie (a real, actively-used Python package for this
  exact data)
- NBP's current publish schedule: 01, 07, 13, 19 UTC — verified twice,
  directly, against NOAA's own product page text

**Still not independently verified (see the connectivity check, below):**
- The exact folder/filename structure for the NBM **text** bulletin
  specifically. A confirmed real example exists for the GRIB2 (binary)
  format at a similar path, but the text-bulletin path was inferred by
  analogy, not confirmed. v3.0 tries two plausible patterns and logs
  which one works — **run the "Check NBM Connectivity" workflow first**
  before trusting Forecast Refresh, per `QUICKSTART.md`.
- The exact Kalshi series-ticker prefix for each city (e.g. `KXHIGHLAX`
  for Los Angeles) — if wrong, that city's scan will simply return zero
  markets and log it clearly, rather than trade on a wrong assumption

## Specific known gaps

- **Settlement P&L doesn't model Kalshi's real fee structure.** No
  confirmed fee formula was available while building the settlement
  checker, so it uses a simple win-pays-$1.00/loss-pays-$0 model. Real
  P&L (once live) will be slightly worse than what paper trading reports.

- **No slippage modeling in paper mode.** Assumes fills at the displayed
  ask/bid.
- **GitHub Actions scheduling is best-effort**, even at 5-minute
  intervals. Real run frequency may be lower than configured, especially
  during high platform load. See `IMPROVEMENTS.md` for the plan if this
  proves to be a persistent problem.
- **No backtest feature.** Removed in v2.0 after confirming NOAA doesn't
  retain the needed forecast bulletin archive beyond about a week for
  free. The self-audit Brier tracker (new in v3.0) is the direct
  replacement — it validates against the bot's own accumulating trade
  history instead of external historical data.
- **Single forecast source.** Only NBM is used (deliberately — a
  hand-rolled multi-model ensemble was the exact approach that scored
  worse than a naive base-rate guess in the research this project drew on).
- **Herbie/GRIB2 migration considered, not yet built.** Herbie is a
  real, maintained package that could replace this project's hand-rolled
  NBM fetch entirely, solving the exact "stop building, use what
  exists" lesson this project has learned the hard way twice now. Not
  pursued in v3.0 because it requires a system-level `eccodes`
  dependency and the exact GRIB2 percentile field names weren't
  independently confirmed before this build. See `IMPROVEMENTS.md`.

## Bug classes this was deliberately built to avoid

- Silent `dict.get(key, default)` fallbacks hiding API response shape
  mismatches
- Settlement updates that touch pnl but not status
- Treating "void" outcomes as losses
- DST-related date bucketing errors
- Wrong settlement station guesses — verified against Kalshi's live
  market metadata every forecast refresh
- Wrong strike-price field for "between" (range) markets
- Wrong NOAA bulletin type / silently misaligned forecast columns
- **Wrong/nonexistent hostnames shipped without live verification** —
  the exact v2.0 bug. v3.0's response isn't "be more careful" (that
  doesn't scale) but structural: a standalone connectivity check that
  must be run and confirmed before the real pipeline is trusted.

## What to actually watch for in your daily summaries

See `TROUBLESHOOTING.md` for a full phrase-by-phrase guide. The short version:
- Run **Check NBM Connectivity** first, always, before trusting anything else
- Any `POSITION MISMATCH` alert — investigate before trusting that day's numbers
- A daily loss limit alert — expected to occasionally happen
- "Forecast cache is completely EMPTY" — the exact symptom of the v2.0 bug;
  should not appear if the connectivity check passed
- A "NO SKILL" Brier verdict at 100+ settled trades — treat as a serious
  signal to pause before going live
- `nbm_parse` warnings appearing on every single run — worth investigating

