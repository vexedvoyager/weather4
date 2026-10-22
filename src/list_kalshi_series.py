"""
Kalshi series discovery (v6.0, IMPROVEMENTS.md item #23).

Lists the daily-high-temperature series Kalshi ACTUALLY offers, so shadow
cities (config.yaml -> shadow_cities) are chosen from real data instead of
guessed tickers. For each series it also:
  - reads one open market's settlement text and extracts Kalshi's own
    "CLI" station identifier (e.g. CLIMDW), the same convention
    src/stations.py already relies on (IMPROVEMENTS.md item #3);
  - converts that to the ICAO code the NBM bulletin uses (CLIMDW -> KMDW);
  - checks whether that station really exists in the latest NBM bulletin
    and whether its percentile rows parse (i.e. whether the bot could
    forecast it at all);
  - reports whether the city is already configured.

Read-only: never trades, never touches the database.

Run from GitHub Actions (Actions -> "List Kalshi Series" -> Run workflow)
and upload the output file to Claude.
"""
import re
import sys
from datetime import datetime, timezone

from src import nbm
from src.config import load_config

CLI_PATTERN = re.compile(r"\bCLI([A-Z]{3})\b")


def extract_cli_code(text: str) -> str | None:
    """'...see CLIMDW climate report...' -> 'MDW'. None if absent."""
    m = CLI_PATTERN.search((text or "").upper())
    return m.group(1) if m else None


def icao_from_cli(code: str | None) -> str | None:
    """'MDW' -> 'KMDW' (continental-US stations carry a leading K)."""
    return "K" + code if code and len(code) == 3 else None


def is_daily_high_series(series: dict) -> bool:
    """Daily high-temperature series use tickers like KXHIGHCHI / KXHIGHNY."""
    return str(series.get("ticker", "")).upper().startswith("KXHIGH")


def inspect_series(client, series: dict, bulletin_text: str, configured: dict) -> dict:
    """Returns one row of facts about a series (never raises)."""
    ticker = series.get("ticker", "?")
    row = {
        "series": ticker, "title": series.get("title", ""), "open_markets": 0,
        "cli": None, "icao": None, "in_nbm": None, "nbm_parses": None,
        "configured_as": configured.get(ticker), "note": "",
    }
    try:
        markets = client.get_markets_by_series(ticker)
    except Exception as e:
        row["note"] = f"market fetch failed: {e}"
        return row
    row["open_markets"] = len(markets)
    if not markets:
        row["note"] = "no open markets right now"
        return row
    m = markets[0]
    try:
        detail = client.get_market(m.get("ticker"))
    except Exception:
        detail = m
    text = " ".join(str(detail.get(k, "")) for k in ("rules_primary", "rules_secondary", "settlement_sources"))
    row["cli"] = extract_cli_code(text)
    row["icao"] = icao_from_cli(row["cli"])
    if row["icao"] and bulletin_text:
        row["in_nbm"] = nbm.get_station_block(bulletin_text, row["icao"]) is not None
        row["nbm_parses"] = nbm.parse_station_maxt(bulletin_text, row["icao"]) is not None if row["in_nbm"] else False
    elif not row["icao"]:
        row["note"] = "no CLI station code found in settlement text"
    return row


def format_rows(rows: list) -> list:
    out = [f"{'series':<14} {'open':>4} {'CLI':<5} {'ICAO':<6} {'in NBM':<7} {'parses':<7} "
           f"{'configured':<14} title / note"]
    for r in rows:
        def yn(v):
            return "-" if v is None else ("yes" if v else "NO")
        out.append(
            f"{r['series']:<14} {r['open_markets']:>4} {(r['cli'] or '-'):<5} {(r['icao'] or '-'):<6} "
            f"{yn(r['in_nbm']):<7} {yn(r['nbm_parses']):<7} {(r['configured_as'] or '-'):<14} "
            f"{r['title']}{(' | ' + r['note']) if r['note'] else ''}"
        )
    return out


def candidate_snippet(rows: list) -> list:
    """YAML to paste under shadow_cities for viable, not-yet-configured series.
    Timezone cannot be derived reliably, so it is left for a human to fill in."""
    out = []
    for r in rows:
        if r["configured_as"] or not (r["icao"] and r["nbm_parses"] and r["open_markets"]):
            continue
        name = re.sub(r"(?i)\b(highest|high|temperature|daily|in|of)\b", "", r["title"]).strip(" ,-") or r["series"]
        out += [
            f'  - name: "{name}"',
            f'    kalshi_series_prefix: "{r["series"]}"',
            f'    expected_station: "{r["icao"]}"',
            '    timezone: "FILL_IN"   # IANA name, e.g. America/Denver',
            "",
        ]
    return out


def main():
    from src.scan_common import build_client

    cfg = load_config()
    client = build_client()
    configured = {c["kalshi_series_prefix"]: c["name"] for c in cfg["cities"]}
    configured.update({c["kalshi_series_prefix"]: c["name"] + " (shadow)"
                       for c in (cfg.get("shadow_cities") or [])})

    print(f"Kalshi series discovery   generated {datetime.now(timezone.utc).isoformat()}")
    try:
        all_series = client.get_series_list("Climate and Weather")
    except Exception as e:
        print(f"Could not list series: {e}")
        return 1
    print(f"Series in 'Climate and Weather': {len(all_series)}")
    highs = sorted((s for s in all_series if is_daily_high_series(s)), key=lambda s: s.get("ticker", ""))
    print(f"Daily-high series (ticker starts with KXHIGH): {len(highs)}")
    if not highs:
        print("None matched. First 40 series tickers, to see the naming pattern:")
        print("  " + ", ".join(str(s.get("ticker")) for s in all_series[:40]))
        return 0

    try:
        bulletin_text, run_id = nbm.fetch_latest_bulletin()
        print(f"NBM bulletin: {run_id}")
    except Exception as e:
        bulletin_text = ""
        print(f"(NBM bulletin unavailable, station checks skipped: {e})")

    rows = [inspect_series(client, s, bulletin_text, configured) for s in highs]
    print("\n".join(format_rows(rows)))

    snippet = candidate_snippet(rows)
    print("\nViable, not-yet-configured candidates (paste under shadow_cities in config.yaml; "
          "fill in each timezone):" if snippet else "\nNo new viable candidates found.")
    print("\n".join(snippet))
    print("\nDONE. Upload this output to Claude.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
