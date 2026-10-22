"""
NBM column diagnostic (v6.0, IMPROVEMENTS.md item #19).

QUESTION THIS ANSWERS: is the model reading the right NBM column for a
daily HIGH temperature?

BACKGROUND: v5 data showed the model is biased in ONE direction (it keeps
predicting highs far too low - e.g. Austin "less than" markets: model said
97% YES, reality was 4%). NBM's temperature (TXN) rows describe both daily
maximums and overnight minimums, and nbm.py picks whichever column is
nearest the market's close time without checking which kind it is. If it is
picking minimum-temperature columns, that would produce exactly this bias.
This script does not assume the answer - it prints the evidence.

For each configured city (plus any extra stations given on the command
line) it prints:
  1. The RAW first lines of the station's bulletin block, exactly as NOAA
     published them (so row labels and layout can be checked by eye).
  2. Every distinct row label in the block.
  3. A column table: forecast hour, the UTC and LOCAL time the column is
     valid for, and the five percentile values (blank = '.').
  4. The median forecast (TXNP5) grouped by the UTC hour each column is
     valid for. If columns at some hours are ~10-25F lower than others,
     they are overnight minimums, not daily highs.
  5. If Kalshi credentials are available: for every open market, which
     column the bot ACTUALLY picks, what time of day it represents, and
     whether it lands on the market's own date.

Run it from GitHub Actions (Actions -> "Diagnose NBM Columns" -> Run
workflow), then upload the resulting text file back to Claude. It only
READS data; it never trades and never writes to the database.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src import nbm
from src.config import load_config

RAW_HEAD_LINES = 24
MAX_FHR_SHOWN = 168

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def market_date_from_ticker(ticker: str) -> date | None:
    """'KXHIGHCHI-26SEP24-T66' -> date(2026, 9, 24). None if no date found."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)", ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
    except ValueError:
        return None


def classify_local_hour(hour: int) -> str:
    """
    What a column valid at this LOCAL clock hour probably represents.
    Daily highs typically occur mid-afternoon to early evening; daily lows
    just before and around sunrise. Anything else is labeled OTHER.
    """
    if 13 <= hour <= 21:
        return "AFTERNOON/EVENING (daily-max time)"
    if 2 <= hour <= 9:
        return "MORNING (daily-min time)"
    return "OTHER"


def column_table(parsed: dict, run_id: str, tz_name: str) -> list:
    """One dict per forecast column: fhr, valid_utc, valid_local, values."""
    tz = ZoneInfo(tz_name)
    rows = []
    for idx, fhr in enumerate(parsed["forecast_hours"]):
        valid_utc = nbm.valid_time_utc(run_id, fhr)
        valid_local = valid_utc.astimezone(tz) if valid_utc else None
        values = {k: (parsed[k][idx] if idx < len(parsed[k]) else None)
                  for k in ("p10", "p25", "p50", "p75", "p90")}
        rows.append({
            "index": idx, "fhr": fhr, "valid_utc": valid_utc,
            "valid_local": valid_local, "values": values,
            "has_data": all(v is not None for v in values.values()),
        })
    return rows


def median_by_utc_hour(rows: list) -> dict:
    """{utc_hour: [p50 values]} over columns that have complete data."""
    out = {}
    for r in rows:
        if r["has_data"] and r["valid_utc"] is not None:
            out.setdefault(r["valid_utc"].hour, []).append(r["values"]["p50"])
    return out


def _fmt_dt(dt) -> str:
    return dt.strftime("%m-%d %H:%MZ") if dt else "?"


def _fmt_local(dt) -> str:
    return dt.strftime("%m-%d %H:%M %Z") if dt else "?"


def render_station_report(city: str, station: str, tz_name: str, bulletin_text: str,
                          run_id: str) -> tuple[list, dict | None]:
    """Returns (report_lines, parsed_or_None)."""
    out = [f"\n{'=' * 78}", f"CITY: {city}   STATION: {station}   TZ: {tz_name}", "=" * 78]
    block = nbm.get_station_block(bulletin_text, station)
    if block is None:
        out.append("  Station NOT FOUND in the bulletin.")
        return out, None

    out.append(f"\n--- 1. RAW bulletin lines (first {min(RAW_HEAD_LINES, len(block))} of {len(block)}) ---")
    out.extend(block[:RAW_HEAD_LINES])

    labels = {}
    for line in block:
        tokens = line.split()
        if tokens:
            labels[tokens[0]] = labels.get(tokens[0], 0) + 1
    out.append("\n--- 2. Row labels in this station block (label: times seen) ---")
    out.append("  " + ", ".join(f"{k}:{v}" for k, v in labels.items()))

    parsed = nbm.parse_station_maxt(bulletin_text, station)
    if parsed is None:
        out.append("\n  Parser returned None (missing FHR row or incomplete percentile rows).")
        return out, None

    rows = column_table(parsed, run_id, tz_name)
    out.append(f"\n--- 3. Columns the parser sees (run {run_id}; '.' = blank cell) ---")
    out.append(f"  {'idx':>3} {'FHR':>4}  {'valid UTC':<13} {'valid local':<19} "
               f"{'p10':>4} {'p25':>4} {'p50':>4} {'p75':>4} {'p90':>4}  kind-by-local-hour")
    for r in rows:
        if r["fhr"] > MAX_FHR_SHOWN:
            continue
        v = r["values"]
        cells = " ".join(f"{('.' if v[k] is None else v[k]):>4}" for k in ("p10", "p25", "p50", "p75", "p90"))
        kind = classify_local_hour(r["valid_local"].hour) if (r["valid_local"] and r["has_data"]) else ""
        out.append(f"  {r['index']:>3} {r['fhr']:>4}  {_fmt_dt(r['valid_utc']):<13} "
                   f"{_fmt_local(r['valid_local']):<19} {cells}  {kind}")

    by_hour = median_by_utc_hour(rows)
    out.append("\n--- 4. Median forecast (TXNP5) grouped by the UTC hour each column is valid ---")
    if not by_hour:
        out.append("  No columns with complete data.")
    for h in sorted(by_hour):
        vals = by_hour[h]
        out.append(f"  valid {h:02d}Z: n={len(vals)}  mean p50={sum(vals) / len(vals):.1f}  values={vals}")
    if len(by_hour) >= 2:
        means = {h: sum(v) / len(v) for h, v in by_hour.items()}
        lo, hi = min(means, key=means.get), max(means, key=means.get)
        out.append(f"  -> {hi:02d}Z columns average {means[hi] - means[lo]:.1f}F higher than {lo:02d}Z columns. "
                   f"A gap of ~10F or more means the two hours are mixing daily MAX and overnight MIN values.")
    return out, parsed


def render_market_report(client, city_cfg: dict, parsed: dict, run_id: str, cfg: dict) -> list:
    """For each open market: which column does the bot actually use?"""
    from src.forecast_refresh import model_probability_for_market, target_forecast_hour
    from src.market_parsing import describe_threshold, extract_threshold

    tz = ZoneInfo(city_cfg["timezone"])
    out = [f"\n--- 5. Column the bot ACTUALLY picks for each open {city_cfg['name']} market ---"]
    try:
        markets = client.get_markets_by_series(city_cfg["kalshi_series_prefix"])
    except Exception as e:
        out.append(f"  Could not fetch markets: {e}")
        return out
    if not markets:
        out.append("  No open markets.")
        return out

    flags = {"AFTERNOON/EVENING (daily-max time)": 0, "MORNING (daily-min time)": 0,
             "OTHER": 0, "DATE_MISMATCH": 0, "no_column": 0}
    for m in markets:
        ticker = m.get("ticker", "?")
        threshold = extract_threshold(m)
        desc = describe_threshold(threshold) if threshold else "?"
        hour = target_forecast_hour(run_id, m.get("close_time"))
        if hour is None:
            out.append(f"  {ticker}: no/unparseable close_time")
            continue
        chosen = nbm.get_forecast_with_meta(parsed, hour)
        if chosen is None:
            flags["no_column"] += 1
            out.append(f"  {ticker} [{desc}] close={m.get('close_time')} target_hour={hour}: NO usable column")
            continue
        valid_utc = nbm.valid_time_utc(run_id, chosen["forecast_hour"])
        valid_local = valid_utc.astimezone(tz)
        kind = classify_local_hour(valid_local.hour)
        flags[kind] += 1
        mdate = market_date_from_ticker(ticker)
        # A daily-high column describes the afternoon of ITS date; shifting back
        # 6h keeps a 00Z column (evening local) on the previous calendar date,
        # which is the date the high actually happened.
        represented = (valid_local - timedelta(hours=6)).date()
        mismatch = bool(mdate and represented != mdate)
        if mismatch:
            flags["DATE_MISMATCH"] += 1
        p = None
        if threshold:
            p = model_probability_for_market(threshold, chosen["values"],
                                             cfg["probability_model"]["sigma_multiplier"])
        out.append(
            f"  {ticker} [{desc}] close={m.get('close_time')} target_hour={hour} -> "
            f"FHR {chosen['forecast_hour']} valid {_fmt_dt(valid_utc)} = {_fmt_local(valid_local)} "
            f"[{kind}]{' DATE_MISMATCH(market ' + str(mdate) + ' vs col ' + str(represented) + ')' if mismatch else ''} "
            f"p50={chosen['values']['p50']} p10..p90={json.dumps(list(chosen['values'].values()))}"
            + (f" model_P(YES)={p['clamped']:.3f} raw={p['raw']:.3f}" if p else "")
        )
    out.append(f"  SUMMARY: {json.dumps(flags)}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Diagnose which NBM columns the bot reads")
    ap.add_argument("--extra-stations", default="",
                    help="comma-separated extra ICAO stations to inspect (no market lookup)")
    ap.add_argument("--no-markets", action="store_true", help="skip the Kalshi market lookup")
    args = ap.parse_args(argv)

    cfg = load_config()
    bulletin_text, run_id = nbm.fetch_latest_bulletin()
    print(f"NBM run: {run_id}   generated: {datetime.now(timezone.utc).isoformat()}")

    client = None
    if not args.no_markets:
        try:
            from src.scan_common import build_client
            client = build_client()
        except Exception as e:  # missing credentials etc. - NBM part still useful
            print(f"(Kalshi lookup skipped: {e})")

    cities = list(cfg["cities"]) + list(cfg.get("shadow_cities") or [])
    for city_cfg in cities:
        lines, parsed = render_station_report(
            city_cfg["name"], city_cfg["expected_station"], city_cfg["timezone"],
            bulletin_text, run_id)
        print("\n".join(lines))
        if parsed is not None and client is not None:
            print("\n".join(render_market_report(client, city_cfg, parsed, run_id, cfg)))

    for st in [s.strip().upper() for s in args.extra_stations.split(",") if s.strip()]:
        lines, _ = render_station_report(st, st, "UTC", bulletin_text, run_id)
        print("\n".join(lines))

    print("\nDONE. Upload this output to Claude for analysis.")


if __name__ == "__main__":
    sys.exit(main())
