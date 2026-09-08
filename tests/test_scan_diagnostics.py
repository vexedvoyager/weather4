import tempfile
import os
from datetime import datetime, timezone

from src import db


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def test_scan_totals_sum_across_multiple_scans_same_day():
    path = _temp_db()
    db.log_scan(path, tickers_scanned=50, tickers_eligible=3, trades_opened=1,
                db_open_count=1, live_open_count=1)
    db.log_scan(path, tickers_scanned=45, tickers_eligible=0, trades_opened=0,
                db_open_count=1, live_open_count=1)

    today = datetime.now(timezone.utc).date().isoformat()
    totals = db.get_todays_scan_totals(path, today)

    assert totals["scan_count"] == 2
    assert totals["total_scanned"] == 95
    assert totals["total_eligible"] == 3
    assert totals["total_opened"] == 1


def test_scan_totals_zero_when_no_scans_today():
    path = _temp_db()
    today = datetime.now(timezone.utc).date().isoformat()
    totals = db.get_todays_scan_totals(path, today)

    assert totals["scan_count"] == 0
    assert totals["total_scanned"] == 0
    assert totals["total_eligible"] == 0
    assert totals["total_opened"] == 0


def test_scan_totals_mismatch_count_reflects_flagged_scans():
    path = _temp_db()
    # db_open=5, live_open=1 -> mismatch (diff > 1)
    db.log_scan(path, tickers_scanned=10, tickers_eligible=0, trades_opened=0,
                db_open_count=5, live_open_count=1)
    # db_open=2, live_open=2 -> no mismatch
    db.log_scan(path, tickers_scanned=10, tickers_eligible=0, trades_opened=0,
                db_open_count=2, live_open_count=2)

    today = datetime.now(timezone.utc).date().isoformat()
    totals = db.get_todays_scan_totals(path, today)
    assert totals["mismatch_count"] == 1


def test_forecast_cache_coverage_empty_when_nothing_cached():
    path = _temp_db()
    coverage = db.get_forecast_cache_coverage(path)
    assert coverage == {}


def test_forecast_cache_coverage_groups_by_city():
    path = _temp_db()
    db.upsert_forecast_cache(path, "CHI-1", "Chicago", 0.6, "run-A")
    db.upsert_forecast_cache(path, "CHI-2", "Chicago", 0.7, "run-A")
    db.upsert_forecast_cache(path, "MIA-1", "Miami", 0.5, "run-A")

    coverage = db.get_forecast_cache_coverage(path)
    assert coverage == {"Chicago": 2, "Miami": 1}
