import tempfile
import os
import time

from src import db


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


def test_cached_forecast_roundtrip():
    path = _temp_db()
    db.upsert_forecast_cache(path, "TICKER-1", "Chicago", 0.73, "2026-08-24T13Z")

    cached = db.get_cached_forecast(path, "TICKER-1", max_age_hours=8)
    assert cached is not None
    assert cached["model_prob"] == 0.73
    assert cached["nbm_run_id"] == "2026-08-24T13Z"


def test_missing_ticker_returns_none():
    path = _temp_db()
    assert db.get_cached_forecast(path, "NEVER-CACHED", max_age_hours=8) is None


def test_stale_forecast_beyond_max_age_returns_none():
    path = _temp_db()
    with db.get_connection(path) as conn:
        # Manually insert a cache entry with a cached_at far in the past,
        # simulating a forecast that's aged out.
        from datetime import datetime, timedelta, timezone
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
        conn.execute(
            "INSERT INTO forecast_cache (ticker, city, model_prob, nbm_run_id, cached_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("STALE-TICKER", "Miami", 0.6, "2026-08-23T19Z", stale_time),
        )
        conn.commit()

    assert db.get_cached_forecast(path, "STALE-TICKER", max_age_hours=8) is None


def test_upsert_overwrites_existing_entry_for_same_ticker():
    path = _temp_db()
    db.upsert_forecast_cache(path, "TICKER-1", "Chicago", 0.60, "run-A")
    db.upsert_forecast_cache(path, "TICKER-1", "Chicago", 0.75, "run-B")

    cached = db.get_cached_forecast(path, "TICKER-1", max_age_hours=8)
    assert cached["model_prob"] == 0.75
    assert cached["nbm_run_id"] == "run-B"


def test_clear_forecast_cache_for_city_only_clears_that_city():
    path = _temp_db()
    db.upsert_forecast_cache(path, "CHI-TICKER", "Chicago", 0.6, "run-A")
    db.upsert_forecast_cache(path, "MIA-TICKER", "Miami", 0.7, "run-A")

    db.clear_forecast_cache_for_city(path, "Chicago")

    assert db.get_cached_forecast(path, "CHI-TICKER", max_age_hours=8) is None
    assert db.get_cached_forecast(path, "MIA-TICKER", max_age_hours=8) is not None
