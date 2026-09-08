"""
Standalone connectivity check for NOAA's NBM bulletin fetch.

Run this FIRST, manually, before trusting Forecast Refresh - it has no
trading logic attached at all, just a direct test of every candidate
URL pattern against every recent candidate run, with clear pass/fail
output for each one.

This exists because the exact folder structure for NBM's text bulletins
could not be independently verified before v3 shipped (see src/nbm.py's
module docstring for the full explanation) - this script is how you find
out, from GitHub Actions' own network access, rather than trusting a
guess.

Run with: python -m src.check_nbm_connectivity
"""
import logging
import sys
from datetime import datetime, timezone

import requests

from src.nbm import NBM_HOST, PATH_TEMPLATES, RUN_HOURS, _candidate_runs

logging.basicConfig(level=logging.WARNING)  # keep noise down, this script prints its own output


def run_connectivity_check(max_candidates: int = 8, timeout: int = 15, stations_to_probe: list = None):
    now = datetime.now(timezone.utc)
    print(f"=== NBM Connectivity Check ===")
    print(f"Current time (UTC): {now.isoformat()}")
    print(f"Host: {NBM_HOST}")
    print(f"Confirmed NBP schedule (UTC hours): {sorted(RUN_HOURS)}")
    print(f"Path templates to try, in order: {len(PATH_TEMPLATES)}")
    print()

    results = []
    checked = 0
    first_success_text = None

    for run_date, run_hour in _candidate_runs(now):
        if checked >= max_candidates:
            break
        checked += 1
        print(f"--- Candidate run: {run_date} {run_hour:02d}Z ---")

        for idx, template in enumerate(PATH_TEMPLATES):
            url = template.format(host=NBM_HOST, date=run_date.strftime("%Y%m%d"), hour=run_hour)
            try:
                resp = requests.get(url, timeout=timeout)
                status = resp.status_code
                if resp.ok:
                    size = len(resp.text)
                    print(f"  [template {idx}] ✓ SUCCESS  status={status} size={size} chars")
                    print(f"    url: {url}")
                    print(f"    first 200 chars: {resp.text[:200]!r}")
                    results.append((run_date, run_hour, idx, url, True, status, size))
                    if first_success_text is None:
                        first_success_text = resp.text
                else:
                    print(f"  [template {idx}] ✗ HTTP {status}  url: {url}")
                    results.append((run_date, run_hour, idx, url, False, status, 0))
            except requests.RequestException as e:
                print(f"  [template {idx}] ✗ FAILED  {type(e).__name__}: {e}")
                print(f"    url: {url}")
                results.append((run_date, run_hour, idx, url, False, str(e), 0))
        print()

    successes = [r for r in results if r[4] is True]
    print("=== Summary ===")
    if successes:
        print(f"✓ {len(successes)} successful fetch(es) found.")
        best = successes[0]
        print(f"First success: run={best[0]} {best[1]:02d}Z, template index {best[2]}")
        print(f"Working URL: {best[3]}")
        print()
        print("If template index is not 0, consider reordering PATH_TEMPLATES in "
              "src/nbm.py to put the working pattern first, for faster real fetches.")

        # --- Station-format probe -------------------------------------------
        # Confirms our actual configured stations appear in the bulletin in
        # the format src/nbm.py's parser expects, using REAL fetched data
        # instead of synthetic test data - the first time this has been
        # checked against a live file.
        if stations_to_probe:
            print()
            print("=== Station format probe (checking real data, not synthetic) ===")
            for station in stations_to_probe:
                idx = first_success_text.upper().find(f"{station.upper()} NBM")
                if idx == -1:
                    # Try without requiring a space right after, in case
                    # formatting differs slightly from our assumption.
                    idx = first_success_text.upper().find(station.upper())
                if idx == -1:
                    print(f"  {station}: ✗ NOT FOUND anywhere in the bulletin")
                else:
                    snippet = first_success_text[max(0, idx - 5):idx + 150]
                    print(f"  {station}: found at character {idx}")
                    print(f"    context: {snippet!r}")
        return 0
    else:
        print("✗ NO successful fetches across any candidate run or path template.")
        print("This means Forecast Refresh will also fail. The host or path structure")
        print("needs further investigation before relying on this pipeline.")
        return 1


if __name__ == "__main__":
    sys.exit(run_connectivity_check(stations_to_probe=["KMDW", "KNYC", "KMIA", "KAUS", "KLAX"]))
