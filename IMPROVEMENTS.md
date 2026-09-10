# Improvements for Next Version

A running list of things to fold into the next rebuild — gathered from
real usage, not speculative "nice to haves."

---

## Open items

### 1. Monitor whether the 5-minute Price Check schedule is actually reliable

**Why:** GitHub's cron scheduling is documented as best-effort even at
its minimum 5-minute interval.

**Proposed fix (only if this turns out to be a real problem):** an
external pinger (e.g. cron-job.org) calling GitHub's API to trigger the
workflow, bypassing GitHub's own scheduler reliability.

**Priority:** low until observed data says otherwise.

---

### 2. Migrate to Herbie for NBM data fetching

**Why:** Herbie (https://herbie.readthedocs.io) is a real, actively
maintained open-source package built specifically to download weather
model data - including NBM - from NOAA's various sources. It has a
genuinely valuable built-in solution to the hardest remaining unverified
piece of this project: `pick_points`, a proper haversine/BallTree-based
nearest-neighbor accessor that finds the correct grid cell for any
station on NBM's curvilinear grid without manual coordinate-transform code.

This is a direct instance of the "stop building, use what exists" lesson
from the source material - handwritten bulletin-fetching code has now
caused several of this project's most serious bugs (wrong bulletin type
in v1→v2, wrong hostname in v2→v3, station-format and Kalshi-parameter
mismatches found via live testing). A maintained library used by real
practitioners is less likely to have these specific failure modes.

**What's confirmed:**
- A real, working example URL for NBM's GRIB2 (binary) files exists and
  was independently verified across multiple Herbie documentation versions
- `pick_points` genuinely solves the curvilinear-grid nearest-station problem

**What's NOT yet confirmed (why this hasn't been built):**
- The exact GRIB2 variable name / search string needed to extract
  percentile temperature fields from NBM's "qmd" (quantile-mapped)
  product files specifically - a different, unverified detail from the
  core "co"/"core" files that have a confirmed working example
- Whether `cfgrib`'s system dependency (`eccodes`, a C library, not a
  pure Python package) installs cleanly and quickly enough on a GitHub
  Actions runner to be worth the added workflow complexity and runtime

**Proposed approach when this is picked up:** scope a small, standalone
proof-of-concept first (same pattern as the NBM connectivity-check
script) - install cfgrib/herbie in a throwaway workflow, fetch one real
qmd file, and confirm the percentile field names and values look sane -
before committing to a full migration.

**Priority:** medium - a meaningful quality improvement, but real
unverified surface area remains. The text-bulletin approach is now
confirmed working in production, which reduces the urgency somewhat.

---

### 3. Document the "CLI" naming discovery and cross-check resource

**Why:** the user found https://www.clilax.com/ during research, which
confirms "CLI" stands for NWS's official "Climatological Report" product
- explaining exactly why Kalshi's settlement text uses identifiers like
"CLIMDW" rather than the plain ICAO code (the exact mismatch fixed in
`src/stations.py`). The site also independently confirms the "Local
Standard Time climate day" settlement methodology, which matches
`weather_day.py`'s existing DST-handling logic - good external
validation, not something requiring a code change itself.

The same operator also runs equivalent live-tracking pages for NYC and
Chicago (`clilax.com/nyc`, `clilax.com/chicago`) - two more of the 5
configured cities. Worth keeping as a manual cross-check resource, and
worth reviewing again once (if) the Herbie/GRIB2 migration (item #2) is
scoped, since it describes a "precision ladder" of temperature data
sources that may be relevant.

**Proposed fix:** add a short note to `src/stations.py`'s docstring
referencing this as the confirmed source for the "CLI" naming
convention. Purely documentation - no logic change needed.

**Priority:** low, easy.

---

### 4. Two settlement/market-type edge cases from Kalshi's official rulebook (low priority)

**Why:** found while reading `Kalshi_Global_Temperature_Terms.pdf`
(Kalshi's own official "GLOBALTEMPERATURE" contract terms). Neither has
caused a real problem - both are just gaps the bot hasn't been tested
against.

**Gap A — a third settlement outcome.** The rulebook states: *"If no
data is available for [the time period] by the Expiration Date, all
strikes shall resolve to the last fair price as determined in the sole
discretion of the Exchange."* This doesn't map to yes/no/void. **v4
update:** `settle_check.py` now detects this case (any result value it
doesn't recognize) and raises an alert rather than guessing - so this is
now handled defensively, even without a specific "last fair price"
outcome type built into the database schema.

**Gap B — an unsupported market type ("exactly").** Never seen in real
data across any of the 5 cities so far. `extract_threshold()` would
safely skip such a market (fail-safe design) rather than mishandle it.

**Priority:** low. Gap A now has real handling (alert-and-skip); Gap B
remains purely theoretical.

---

### 5. Confirm Kalshi's real fee structure and model it in settlement P&L

**Why:** `settle_check.py` (new in v4) computes P&L using a simple
win-pays-$1.00/loss-pays-$0 model. No confirmed fee formula was
available while building it, so real P&L (once live) will be slightly
worse than what paper trading reports. This matters more once live
trading is being considered seriously - right now it's a small,
consistent optimism bias in the numbers, not a functional bug.

**Proposed fix:** find Kalshi's official fee schedule (likely in their
docs or a rulebook PDF) and add fee calculation to `_compute_pnl_cents()`
in `src/settle_check.py`.

**Priority:** medium - worth doing before live mode, not urgent for
paper trading.

---

### 6. Add "yesterday's P&L" line to the daily summary

**Why:** the daily summary currently only shows today's activity. Once
positions start settling regularly, a quick "yesterday's net P&L" line
gives an at-a-glance read on the most recently completed day without
needing to scroll back through prior summaries.

**Proposed fix:** pull `db.daily_pnl_cents()` for yesterday's date
(already exists, just needs to be called with the right date) and add a
line like "Yesterday's net P&L: $+2.24 (2 wins, 1 loss)" near the top of
the summary, before today's in-progress numbers.

**Priority:** low, easy - small, well-scoped addition to
`src/daily_summary.py`.

---

### 7. Show the winning bracket/outcome per city once a day's contracts settle

**Why:** once a city's full set of same-day contracts has settled, the
data already tells us which single bracket actually won (e.g. "85-86°F"
resolved YES, all others resolved NO) - but nothing currently surfaces
this. Knowing "what actually happened" in plain terms is more readable
than inferring it from a list of individual trade outcomes.

**Scope decision:** this is "Option A" from the two choices discussed -
deriving the winning bracket from Kalshi's own settlement data we
already have, NOT the literal precise temperature reading (which would
require a new integration with NWS's official CLI report, e.g. via the
clilax.com-style sources documented in item #3). Option A needs no new
data source, just new logic over data already being pulled.

**Proposed fix:** for each city, once all of that day's contracts for a
given settlement date have a `result`, identify whichever one resolved
`yes` and display it (e.g. "Chicago: 85-86°F won"). Needs a new query
grouping settled trades (or, more robustly, all cached markets for that
date+city, not just ones we happened to trade) by city+date to find the
YES outcome - worth checking whether we have visibility into contracts
we *didn't* trade, since the winning bracket might not be one we
actually bought.

**Priority:** medium - a real quality-of-life improvement to the daily
summary once enough real settlements are happening to make it useful.
---

## Decisions (things considered and deliberately not done)

**Dropped the backtest feature entirely (in v2.0).** After finding that
NOAA doesn't retain the needed forecast bulletin archive beyond about a
week for free, the feature's original value proposition wasn't
achievable as designed. The v3.0 self-audit Brier tracker is the direct
replacement - it validates against the bot's own accumulating trade
history instead of external historical data.

**Did not migrate to Herbie yet.** See item #2 above - real unverified
surface area (qmd percentile field names, eccodes install feasibility)
remains; the text-bulletin approach is confirmed working in production
now, which reduces the urgency.

---

## Log of resolved issues (for context, not action)

**From v2.0 setup (GitHub/workflow mechanics):**
- `.github`, `.gitignore`, `data/.gitkeep` are dotfiles invisible during
  drag-and-drop uploads unless hidden files are shown — documented in
  `QUICKSTART.md`
- `git add data/alerts/` failed on an empty/nonexistent folder — fixed
  with `mkdir -p` + placeholder file before the add
- `gh issue create --json` isn't a valid flag — fixed by parsing the
  plain URL output instead
- `KALSHI_ID_KEY` vs `KALSHI_KEY_ID` naming mismatch — user-side typo;
  reminder that GitHub always displays secret names in uppercase
  regardless of how they're typed, which can mask other typos

**From v2.0 pre-build verification (found before any code shipped):**
- v1 fetched the wrong NBM bulletin (NBH instead of NBP) — root-cause fixed
- NBM bulletins are fixed-width with intentionally blank columns; naive
  whitespace-splitting silently misaligned values — fixed with
  column-position-based parsing, covered by regression tests
- Kalshi's documented production base URL is `external-api.kalshi.com`,
  not the `api.elections.kalshi.com` alias v1 used — fixed
- No handling for Kalshi's 429 rate-limit responses — added exponential
  backoff, tested

**From v3.0 (found via real production incidents, not pre-build review):**
- v2.0's NBM fetch used `blend.nomads.ncep.noaa.gov` — a hostname that
  does not exist (confirmed DNS resolution failure in a live Forecast
  Refresh log). Root-cause fixed: correct host is `nomads.ncep.noaa.gov`.
- NBP's true publish schedule is 01, 07, 13, 19 UTC only (4x/day) — not
  the 6-value schedule v2.0 used. Confirmed twice, independently,
  against NOAA's own product page.
- The daily summary had no way to distinguish "scans are running but
  finding nothing eligible" from "the pipeline is silently broken
  upstream" — fixed with scan-total and forecast-cache diagnostics.
- Station header lines have a leading space before the station code
  (e.g. " KMDW NBM..."), which the original detection regex — anchored
  to the exact start of the line — would have silently never matched.
  Fixed, confirmed with a regression test built from real captured data.
- Kalshi's `/markets` endpoint uniformly rejected every city with a 400
  Bad Request. Root cause: `status="active"` was never a valid filter
  value. Kalshi's own documented example uses `status="open"`. Fixed.
- Every city's markets were being skipped at station verification
  despite the mapping being correct. Root cause: Kalshi's real
  settlement text uses "CLI" + ICAO-minus-leading-K (e.g. "CLIMDW"), not
  the plain ICAO code. Fixed, with tests built from real captured text.
- `model_probability_for_market()` always computed "probability of
  exceeding," even for "less than" contracts, inverting the model's
  confidence for every one of them (5 of the first 10 real trades were
  affected). Found by the user manually cross-referencing real trade
  output against Kalshi's settlement text. Fixed: `extract_threshold()`
  now returns a `direction` field, used to correctly return the
  complement probability for "below" markets.
- Daily summary showed raw tickers (e.g. "T88") instead of their actual
  meaning, requiring manual cross-referencing to understand a trade.
  Fixed: `describe_threshold()` generates plain-English descriptions
  ("high < 88°F"), stored on both the forecast cache and the trade
  itself, displayed in the daily summary.
- `KXHIGHLAX` (Los Angeles' series prefix), previously an unverified
  guess, is now confirmed correct — real LA trades appeared in
  production runs.

**From v4 (the settlement lifecycle was entirely missing):**
- **The core v4 fix:** nothing in v1-v3 ever checked whether an open
  position had actually resolved on Kalshi's side. `settle_trade()` was
  built and tested from day one, but nothing called it against real
  data - positions stayed "open" forever, and Net P&L always showed
  $0.00 regardless of real (simulated) outcomes. Found by the user
  asking why a day's results weren't showing up anywhere, days after
  positions should have settled. Fixed: `src/settle_check.py` now runs
  at the start of every Price Check and closes out anything Kalshi shows
  a real result for, with dedicated handling for void outcomes and
  unrecognized result values (see item #4, Gap A).
- The first successful run deployed 97% of the total budget in a single
  pass (10 trades, hitting the per-city cap on all 5 cities
  simultaneously). Fixed: `max_new_trades_per_run` in `config.yaml`
  caps how many trades a single Price Check run can open.
- `get_positions()` failing with `401 Unauthorized` (while `/markets`
  calls in the same run succeeded) was logged but with no actionable
  explanation. Fixed: the error message now explains the likely cause
  (a Kalshi API key permissions/scope issue) directly.
- A visual dashboard (`docs/index.html`, via GitHub Pages) was built:
  P&L chart, per-city breakdown, open/settled positions tables using the
  plain-English trade descriptions, and the Brier self-audit — all
  regenerated automatically after every Price Check run.
