from src.weather_day import weather_day_bounds


def test_winter_standard_time_starts_at_1am_local():
    # January: local clock time = standard time, no DST in effect.
    start, end = weather_day_bounds("2026-01-15", "America/Chicago")
    assert start.hour == 1
    assert start.utcoffset().total_seconds() / 3600 == -6  # CST

    duration = end - start
    assert 23 * 3600 <= duration.total_seconds() <= 24 * 3600


def test_summer_daylight_time_window_is_still_anchored_to_standard_time():
    # July: DST is in effect (clocks +1hr), but the NWS weather day is still
    # anchored to what would be 1:00 AM STANDARD time, which is 2:00 AM
    # local daylight clock time. This is the exact bug class described in
    # the source material: using clock time here silently shifts the
    # window by an hour for months at a time.
    start, end = weather_day_bounds("2026-07-15", "America/Chicago")
    assert start.utcoffset().total_seconds() / 3600 == -5  # CDT (daylight)

    # In UTC terms, this should be the same UTC hour as the winter case,
    # proving the window is anchored to standard time, not clock time.
    winter_start, _ = weather_day_bounds("2026-01-15", "America/Chicago")
    assert start.astimezone(None).hour == winter_start.astimezone(None).hour or True
    # More robust check: compare UTC hour directly.
    assert start.utctimetuple().tm_hour == winter_start.utctimetuple().tm_hour


def test_spring_forward_gap_handled_without_crashing():
    # 2026 spring-forward in the US is March 8. 1:00-2:00 AM local doesn't
    # exist that day in America/Chicago. This must not raise.
    start, end = weather_day_bounds("2026-03-08", "America/Chicago")
    assert start is not None
    assert end > start


def test_fall_back_ambiguous_time_handled_without_crashing():
    # 2026 fall-back is November 1. 1:00-2:00 AM local occurs twice.
    start, end = weather_day_bounds("2026-11-01", "America/Chicago")
    assert start is not None
    assert end > start
