"""
Fetches and parses NOAA's National Blend of Models (NBM) PROBABILISTIC
(NBP) text bulletin.

v1 of this bot fetched the NBH ("Hourly") bulletin, which does NOT contain
percentile data - it only has deterministic values. The percentile fields
(TXNP1/2/5/7/9 for the 10th/25th/50th/75th/90th percentile of daily
max/min temperature) live in a different bulletin entirely: NBP.

v2.0 fixed the bulletin type but used a WRONG HOSTNAME
(blend.nomads.ncep.noaa.gov, which does not exist - a DNS resolution
failure, confirmed via a live Forecast Refresh run that failed at the
very first network call). This meant v2.0 never actually fetched a
single real forecast in production, despite passing all local tests
built against synthetic data.

v3 uses the CONFIRMED correct host: nomads.ncep.noaa.gov (no "blend."
subdomain), verified independently against NOAA's own product pages, an
official Service Change Notice, and Herbie (a real, actively-used Python
package for downloading this exact data - https://herbie.readthedocs.io).

IMPORTANT HONESTY NOTE: the exact folder structure for the TEXT bulletin
specifically (as opposed to the GRIB2 binary files, which have a
confirmed real example URL) was NOT independently verified before this
build - the tooling available while building this couldn't fetch or
browse the NOMADS directory listing directly. Rather than guess once and
hope, this module tries multiple plausible path patterns per candidate
run and logs which one actually works, so the very first live run tells
us definitively rather than failing silently on a second wrong guess.
See PATH_TEMPLATES below and check the "nbm_fetch success" log line
after the first real run to see which pattern matched.

Confirmed current NBP publish schedule (independently verified twice
against NOAA's own nbm_text product page, which explicitly states
"For NBP products, only 01Z, 07Z, 13Z, 19Z cycles are produced"):
01, 07, 13, 19 UTC. This SUPERSEDES v2.0's schedule (00, 01, 07, 12, 13,
19), which incorrectly included two extra hours misattributed from a
different NBM product's schedule in an SCN document.

Docs: https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/
"""
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

NBM_HOST = "https://nomads.ncep.noaa.gov"

# Multiple candidate path structures, tried in order for each candidate
# run. The first one confirmed to work should be promoted to the top on
# the next code update once we have real telemetry - see
# IMPROVEMENTS.md for this as an open follow-up item.
PATH_TEMPLATES = [
    # Matches the confirmed-real GRIB2 example structure (bare 2-digit
    # cycle folder, e.g. ".../blend.20220501/12/core/..."), applied by
    # analogy to the text bulletin.
    "{host}/pub/data/nccf/com/blend/prod/blend.{date}/{hour:02d}/text/blend_nbptx.t{hour:02d}z",
    # Alternate structure using a "tCCz"-style cycle folder name, in case
    # text products are organized differently from the GRIB2 core files.
    "{host}/pub/data/nccf/com/blend/prod/blend.{date}/t{hour:02d}z/text/blend_nbptx.t{hour:02d}z",
]

# Confirmed current NBP publish schedule - see module docstring.
RUN_HOURS = [19, 13, 7, 1]

PERCENTILE_FIELD_MAP = {
    "TXNP1": "p10",
    "TXNP2": "p25",
    "TXNP5": "p50",
    "TXNP7": "p75",
    "TXNP9": "p90",
}


def _candidate_runs(now: datetime, max_lookback_hours: int = 30):
    """Yield (run_date, run_hour) candidates, most recent first, using
    NBP's actual irregular schedule rather than assuming even spacing."""
    now = now.astimezone(timezone.utc)
    cursor = now
    seen = 0
    while seen < max_lookback_hours:
        for hour in sorted(RUN_HOURS, reverse=True):
            candidate = cursor.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate <= now:
                yield candidate.date(), hour
        cursor -= timedelta(days=1)
        seen += 24


def fetch_latest_bulletin(now: datetime = None, timeout: int = 30) -> tuple[str, str]:
    """
    Fetch the most recent available NBP text bulletin.

    Tries each entry in PATH_TEMPLATES for each candidate run before
    moving to the next-oldest candidate run, so a wrong path-structure
    guess doesn't take down the whole fetch - only a genuinely
    unavailable run does.

    Returns (bulletin_text, run_identifier) where run_identifier includes
    which path template succeeded, for visibility in logs.

    Raises RuntimeError if no recent run is fetchable via any pattern.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    attempts = []
    for run_date, run_hour in _candidate_runs(now):
        for template_idx, template in enumerate(PATH_TEMPLATES):
            url = template.format(host=NBM_HOST, date=run_date.strftime("%Y%m%d"), hour=run_hour)
            try:
                resp = requests.get(url, timeout=timeout)
                resp.raise_for_status()
                run_id = f"{run_date.isoformat()}T{run_hour:02d}Z"
                logger.info(
                    "nbm_fetch success run=%s url=%s path_template_index=%d",
                    run_id, url, template_idx,
                )
                return resp.text, run_id
            except requests.RequestException as e:
                attempts.append((url, str(e)))
                logger.debug("nbm_fetch miss url=%s error=%s", url, e)
                continue

    # Give the last few attempts in the error so a real failure (as
    # opposed to a code bug) is diagnosable from the log alone.
    recent_attempts = "; ".join(f"{u}: {err}" for u, err in attempts[-4:])
    raise RuntimeError(
        f"Could not fetch any NBP bulletin via any known path pattern. "
        f"Most recent attempts: {recent_attempts}"
    )


def parse_station_maxt(bulletin_text: str, station_id: str) -> dict | None:
    """
    Extract MaxT (daily max temperature) percentile forecasts for a station
    from an NBP bulletin.

    station_id: 4-character ICAO code, e.g. 'KMDW'

    Returns a dict like:
        {
            "forecast_hours": [24, 36, 48, ...],   # hours from model run time
            "p10": [68, 70, 72, ...],
            "p25": [...],
            "p50": [...],
            "p75": [...],
            "p90": [...],
        }
    or None if the station wasn't found, or if the expected percentile
    fields weren't present - skip the market rather than guess.
    """
    lines = bulletin_text.splitlines()

    # CONFIRMED against a real live bulletin (not assumed): station lines
    # have a leading space before the station code, e.g. " KMDW    NBM
    # V5.0 NBP GUIDANCE...". The original regex anchored the station code
    # to the exact start of the line and would have silently failed to
    # match every real station - allowing optional leading whitespace here.
    station_pattern = re.compile(rf"^\s*{re.escape(station_id)}\s", re.IGNORECASE)
    station_start = None
    for i, line in enumerate(lines):
        if station_pattern.match(line):
            station_start = i
            break

    if station_start is None:
        logger.warning("nbm_parse station_not_found station=%s", station_id)
        return None

    # Find where the NEXT station's block begins, so we never accidentally
    # read past our station's data into the next one's. NBM station header
    # lines look like "KBWI NBM V5.0 NBP GUIDANCE 5/18/2026 1300 UTC".
    # Same leading-whitespace fix as station_pattern above - confirmed
    # against real data.
    next_station_pattern = re.compile(r"^\s*[A-Z0-9]{3,4}\s+NBM\s+V", re.IGNORECASE)
    station_end = len(lines)
    for i in range(station_start + 1, len(lines)):
        if next_station_pattern.match(lines[i]):
            station_end = i
            break
    block = lines[station_start:station_end]

    # Find the forecast-HOUR header row within the station block. This
    # must be the "FHR" row (elapsed hours since the model run started),
    # NOT the "UTC" row - the UTC row just cycles 00/06/12/18 repeatedly
    # and does not tell us how far into the future each column is.
    forecast_hours = None
    fhr_line = None
    for line in block[:10]:
        if line.strip().upper().startswith("FHR"):
            fhr_line = line
            hour_tokens = re.findall(r"\d+", line)
            if hour_tokens:
                forecast_hours = [int(h) for h in hour_tokens]
                break

    if fhr_line is None or forecast_hours is None:
        logger.warning("nbm_parse no_fhr_row station=%s", station_id)
        return None

    # CRITICAL: NBM text bulletins are fixed-width. Percentile rows like
    # MaxT only print a value in the column matching the relevant forecast
    # hour (e.g. the 00Z column for a max) and leave OTHER columns blank -
    # not zero, not omitted, just whitespace. A naive line.split() collapses
    # that whitespace and silently shifts every subsequent value into the
    # wrong column. To avoid this, we derive each column's fixed character
    # position from the FHR header row itself, then read every data row at
    # those exact character offsets rather than splitting on whitespace.
    column_spans = [m.span() for m in re.finditer(r"\d+", fhr_line)]

    def read_row_by_column(line: str) -> list:
        values = []
        for start, end in column_spans:
            # Numeric tokens in data rows are right-justified within a
            # column of the same width as the header token, so we widen
            # the read window slightly to the left to catch right-aligned
            # values that start before the header token's own start
            # position (e.g. a 2-digit value under a 1-digit hour header).
            window_start = max(0, start - 2)
            segment = line[window_start:end].strip()
            if segment == "" or segment == "-" or not re.fullmatch(r"-?\d+", segment):
                values.append(None)
            else:
                values.append(int(segment))
        return values

    percentile_rows = {}
    for line in block:
        stripped_start = line.strip()
        if not stripped_start:
            continue
        label = stripped_start.split()[0].upper() if stripped_start.split() else ""

        if label in PERCENTILE_FIELD_MAP:
            percentile_rows[PERCENTILE_FIELD_MAP[label]] = read_row_by_column(line)

    missing = set(PERCENTILE_FIELD_MAP.values()) - set(percentile_rows.keys())
    if missing:
        logger.warning(
            "nbm_parse incomplete_percentiles station=%s missing=%s "
            "(found=%s) - skipping rather than guessing",
            station_id, missing, list(percentile_rows.keys()),
        )
        return None

    result = {"forecast_hours": forecast_hours}
    result.update(percentile_rows)
    return result


def get_forecast_for_target_hour(
    parsed: dict, target_forecast_hour: int, tolerance_hours: int = 6
) -> dict | None:
    """
    From a parsed station dict, pull the percentile values closest to
    target_forecast_hour (the forecast hour corresponding to the Kalshi
    contract's settlement day).

    NBP's forecast-hour spacing is coarser than NBH's (this bulletin
    covers a longer range with less granularity), so the default
    tolerance here is wider than the previous NBH-based version used.

    Returns {"p10": val, "p25": val, "p50": val, "p75": val, "p90": val}
    or None if nothing within tolerance_hours is available, or if any
    matched value is a missing placeholder (None).
    """
    hours = parsed.get("forecast_hours")
    if not hours:
        return None

    best_idx, best_diff = None, None
    for idx, h in enumerate(hours):
        diff = abs(h - target_forecast_hour)
        if best_diff is None or diff < best_diff:
            best_idx, best_diff = idx, diff

    if best_idx is None or best_diff > tolerance_hours:
        return None

    out = {}
    for key in ("p10", "p25", "p50", "p75", "p90"):
        values = parsed.get(key)
        if not values or best_idx >= len(values) or values[best_idx] is None:
            return None
        out[key] = values[best_idx]
    return out
