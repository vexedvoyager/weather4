# Troubleshooting: What Your Daily Summary Is Telling You

You don't need to read code to use this. Find the phrase you're seeing,
read what it means, and what (if anything) to do.

---

### I don't see all four workflows in the Actions tab

**What it means:** most likely, the `.github` folder didn't upload —
files/folders starting with a dot are hidden by default on most
computers and silently get skipped during drag-and-drop uploads.

**What to do:** turn on "show hidden files" (Windows: File Explorer →
View → Show → Hidden items; Mac: `Cmd+Shift+.` in Finder), then re-upload
just the `.github` folder via **Add file → Upload files**. See
`QUICKSTART.md` Part 2 for the full steps.

---

### "Check NBM Connectivity" reports "✗ NO successful fetches"

**What it means:** the bot genuinely cannot reach NOAA's forecast data
right now — not a guess, an actual confirmed test result from GitHub's
own network.

**What to do:** share the full log output (Actions tab → the run →
expand the log). It shows every URL pattern tried and the exact error
for each. This is exactly the information needed to find the correct
path structure if NOAA's changed something.

---

### "⚠ Forecast cache is completely EMPTY across all cities"

**What it means:** Forecast Refresh hasn't successfully cached any
predictions — Price Check has nothing to compare prices against, which
fully explains zero trades regardless of thresholds. This was the exact
situation v2.0 was silently stuck in from day one.

**What to do:** run **Check NBM Connectivity** first (see above) to
confirm the data fetch itself works. Then check the most recent
**Forecast Refresh** run's log for the specific error.

---

### "Markets scanned today" is high but "cleared all eligibility checks" is 0

**What it means:** on its own, this is expected — the bot is designed to
reject the vast majority of candidates. This is different from the
forecast-cache-empty case above; scanning is working, the thresholds
just haven't found a strong enough edge yet.

**What to do:** nothing, usually. Long stretches of zero eligible markets
are normal and expected, per the source material's own experience (real
edge validation took them 100+ trades over 4-6 months).

---

### "Model self-audit" shows a "NO SKILL" verdict

**What it means:** the bot's own settled trades show its forecast
probabilities aren't beating the naive historical base rate — the exact
warning sign that led to a full model rebuild in the source material
this project drew on.

**What to do:** don't go live. If this appears with fewer than 100
settled trades, treat it as an early, noisy signal — worth watching, not
yet acting on. If it persists past 100+ trades, the probability model
likely needs the same kind of audit the source material describes.

---

### The Forecast Refresh schedule times look strange/uneven

**What it means:** this is intentional, not a bug. NOAA's forecast
bulletin (the one with the percentile data this bot needs) publishes at
00, 01, 07, 12, 13, 19 UTC — a genuinely irregular schedule, confirmed
directly against NOAA's own documentation. Forecast Refresh is timed to
match those exact hours (plus a short buffer), rather than a clean,
evenly-spaced interval.

---

### "no usable NBM percentile data for station" appears often in logs

**What it means:** the forecast bulletin didn't have complete percentile
data for that station on that run — could be a temporary NOAA data gap,
or (less likely, but worth ruling out if this happens on every run) a
sign the bulletin format changed since this was built.

**What to do:** if it happens occasionally, no action needed — the bot
correctly skips rather than guesses. If it happens on every single run
for every city, share a recent log excerpt and it's worth investigating.

---

### "⚠ N scan(s) flagged a POSITION MISMATCH"

**What it means:** the bot's own records disagree with what Kalshi shows
as your actual open positions. This is one of the two conditions that
also triggers an immediate alert (a separate GitHub Issue titled "🚨
Predict Weather Bot Alert"), so you may have already seen this.

**What to do:** don't trust that day's P&L numbers until this clears.
In paper mode this is lower-stakes (no real money involved) but still
worth noticing, since it may point to a bug worth fixing before going live.
If you're in live mode, log into Kalshi directly and compare your actual
positions against what the bot's report claims.

---

### "🚨 Predict Weather Bot Alert" (a separate GitHub Issue, not a comment)

**What it means:** either a position mismatch (above) or the daily loss
limit was hit. This is designed to reach you the same day, not wait for
tomorrow's summary.

**What to do:** open the issue and read the specific message. If it's the
daily loss limit, the bot has already stopped opening new trades for the
rest of that day on its own — no action is required, but it's worth
understanding why before the next trading day.

---

### "Scans run today: 0" (or a much lower number than expected)

**What it means:** the scheduled GitHub Action didn't run, or ran and
failed before finishing.

**What to do:** click the **Actions** tab in your repo → **Price
Check** or **Forecast Refresh** → look for a run with a red ✗. Click it to see the error message
near the bottom of the log. Common causes: a typo in a GitHub secret
(re-check Part 3 of `QUICKSTART.md`), or Kalshi's site being briefly
unavailable — the latter usually resolves itself by the next scheduled run.

---

### "New positions opened today: 0" for several days in a row

**What it means:** on its own, this isn't a problem — the bot is
supposed to reject the vast majority of candidates. Long stretches with
no trades are expected and not a sign of malfunction.

**What to do:** nothing, usually. If you want to check whether the
thresholds are simply too strict for your risk tolerance, `min_probability_gap`
and `min_composite_score` in `config.yaml` control how selective it is —
lowering them (carefully) means more trades, not necessarily better ones.

---

### The daily P&L sparkline looks completely flat

**What it means:** either no trades have settled yet (normal in the
first days), or something is silently preventing trades from resolving.

**What to do:** check "Positions settled today" in the same report over
several days. If it stays at 0 while "New positions opened" is greater
than 0 for more than a few days, something may be wrong with settlement
timing — feel free to share the daily summary text and I can help
diagnose it.

---

### "no_forecast_available" or similar showing up if you look at raw logs

**What it means:** NOAA's forecast data doesn't cover this specific
market yet (common for same-day markets — this is intentional, not a bug).

**What to do:** nothing. The bot is correctly declining to guess rather
than trade on missing data.

---

### "Positions settled today: 0" even though positions should have closed

**What it means (fixed in v4, described here for context):** through v1-v3,
nothing in the bot ever checked whether an open position had actually
resolved on Kalshi's side. Settlement logic existed and was tested, but
nothing called it against real data - positions stayed "open" forever
regardless of what actually happened. v4 adds a settlement checker that
runs at the start of every Price Check, before anything else, and closes
out any position that Kalshi now shows a real result for.

**What to check if you're on v4 and still seeing this:** look for a log
line like `Settlement check: checked=N settled=N still_open=N` near the
top of a Price Check run. If `still_open` stays high day after day for
positions that should clearly have resolved by now, check that specific
market on Kalshi's site directly - it's possible the market genuinely
hasn't settled yet, or (rarer) it hit the "unrecognized result" case
below.

---

### An alert titled "Unrecognized settlement result"

**What it means:** the settlement checker found a market with a result
value that isn't `yes`, `no`, or `void` - most likely Kalshi's own
"resolve to last fair price" exchange-discretion outcome for markets
where no data was available (documented in Kalshi's official rulebook,
see `KNOWN_LIMITATIONS.md`). This is rare.

**What to do:** the trade is deliberately left open rather than guessed
at. Check the specific market on Kalshi's site to see what actually
happened, then it can be manually settled if needed - share the details
here and it's straightforward to handle.

---

### "Could not fetch live positions for consistency check: 401 Unauthorized"

**What it means:** your Kalshi API key doesn't have permission to read
your portfolio/positions, even though it can read market data fine
(confirmed by comparing this error against successful `/markets` calls
in the same run). This is a Kalshi account/API-key settings issue, not a
bug in the bot.

**What to do:** check your Kalshi account's API key settings for a
permissions/scope option and make sure portfolio/positions read access
is enabled. Until fixed, the position-mismatch safety check can't
actually verify anything - worth fixing before ever considering live mode.

---

### I changed something in `config.yaml` and now nothing works

**What to do:** YAML (the format `config.yaml` uses) is picky about
spacing — it uses indentation (spaces at the start of a line) to show
structure, similar to an outline. If you added or removed a line, make
sure the spacing matches the lines around it exactly. When in doubt,
paste your changed section back to me and I can check it.
