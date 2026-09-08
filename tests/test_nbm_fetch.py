from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from src.nbm import fetch_latest_bulletin, _candidate_runs, RUN_HOURS, PATH_TEMPLATES, NBM_HOST


def test_correct_host_is_used_not_the_old_broken_subdomain():
    """
    Regression test for the exact v2.0 bug: the host must be
    nomads.ncep.noaa.gov, NOT blend.nomads.ncep.noaa.gov (which doesn't
    resolve - confirmed via a live failed run).
    """
    assert NBM_HOST == "https://nomads.ncep.noaa.gov"
    assert "blend.nomads" not in NBM_HOST


def test_run_hours_matches_confirmed_nbp_schedule():
    """NBP is confirmed (twice, independently, against NOAA's own product
    page) to publish only at 01, 07, 13, 19 UTC - not 00/06/12/18, not
    hourly, and not the six-value schedule v2.0 incorrectly used."""
    assert sorted(RUN_HOURS) == [1, 7, 13, 19]


def test_second_path_template_used_when_first_fails():
    """If the first URL pattern 404s but the second succeeds, the second
    result should be returned - this is the resilience the multi-pattern
    design exists for."""
    import requests as requests_module

    ok_resp = MagicMock()
    ok_resp.raise_for_status = MagicMock()
    ok_resp.text = "BULLETIN CONTENT"

    fail_resp = MagicMock()
    fail_resp.raise_for_status.side_effect = requests_module.RequestException("404")

    call_count = {"n": 0}

    def side_effect(url, timeout):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return fail_resp
        return ok_resp

    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)  # after the 19Z run
    with patch("src.nbm.requests.get", side_effect=side_effect):
        text, run_id = fetch_latest_bulletin(now=now)

    assert text == "BULLETIN CONTENT"
    assert call_count["n"] == 2  # first template failed, second succeeded


def test_all_patterns_failing_for_a_run_moves_to_next_candidate_run():
    """If BOTH path templates fail for the most recent run, the fetch
    should move on to the next-oldest candidate run rather than giving up
    immediately."""
    import requests as requests_module
    fail_resp = MagicMock()
    fail_resp.raise_for_status.side_effect = requests_module.RequestException("404")

    ok_resp = MagicMock()
    ok_resp.raise_for_status = MagicMock()
    ok_resp.text = "OLDER BULLETIN"

    call_count = {"n": 0}

    def side_effect(url, timeout):
        call_count["n"] += 1
        # Fail the first 2 calls (both templates for the most recent run),
        # succeed on the 3rd (first template of the next candidate run).
        if call_count["n"] <= 2:
            return fail_resp
        return ok_resp

    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    with patch("src.nbm.requests.get", side_effect=side_effect):
        text, run_id = fetch_latest_bulletin(now=now)

    assert text == "OLDER BULLETIN"
    assert call_count["n"] == 3


def test_error_message_includes_recent_attempts_when_all_fail():
    import requests as requests_module
    fail_resp = MagicMock()
    fail_resp.raise_for_status.side_effect = requests_module.RequestException("connection failed")

    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    with patch("src.nbm.requests.get", return_value=fail_resp):
        try:
            fetch_latest_bulletin(now=now, timeout=1)
            assert False, "should have raised"
        except RuntimeError as e:
            assert "Most recent attempts" in str(e)
            assert "nomads.ncep.noaa.gov" in str(e)


def test_candidate_runs_only_yields_confirmed_schedule_hours():
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    candidates = list(_candidate_runs(now, max_lookback_hours=48))
    hours_seen = set(h for _, h in candidates)
    assert hours_seen.issubset({1, 7, 13, 19})
    assert hours_seen == {1, 7, 13, 19}  # confirms all 4 actually appear given enough lookback
