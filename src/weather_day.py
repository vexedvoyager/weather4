"""
The National Weather Service defines a "weather day" using Local Standard
Time, year-round - not local clock time. During daylight saving time this
does NOT line up with midnight-to-midnight clock time, and using clock
time silently shifts the observation window by an hour for roughly half
the year.

This module anchors to standard time regardless of daylight saving,
matching NWS convention.

IMPLEMENTATION NOTE: an earlier version of this function used
tz.localize(naive_dt, is_dst=False), which is a common but incorrect
pattern - pytz's is_dst flag only disambiguates times that fall in the
fall-back overlap; it does NOT force a summer date onto standard-time
offset. That version silently reproduced the exact clock-time bug it was
meant to fix. This version instead computes the timezone's fixed
standard-time UTC offset directly (probed from a known non-DST date) and
applies that offset explicitly, which is correct regardless of season.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

import pytz


def _standard_utc_offset(tz: pytz.BaseTzInfo, year: int) -> timedelta:
    """
    Returns this timezone's fixed standard-time UTC offset, probed from a
    January date (always outside DST in US timezones) for the given year.
    """
    probe = tz.localize(datetime(year, 1, 1, 12, 0), is_dst=False)
    return probe.utcoffset()


def weather_day_bounds(date_str: str, tz_name: str) -> tuple[datetime, datetime]:
    """
    date_str: "YYYY-MM-DD"
    tz_name: IANA timezone name, e.g. "America/Chicago"

    Returns (start, end) as timezone-aware datetimes (in `tz_name`) marking
    the NWS weather day for that date: 1:00 AM to 12:59 AM the following
    day, both in LOCAL STANDARD TIME, regardless of whether daylight saving
    is in effect on that calendar date.
    """
    tz = pytz.timezone(tz_name)
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    std_offset = _standard_utc_offset(tz, target_date.year)

    naive_start = target_date.replace(hour=1, minute=0)
    start_utc = naive_start.replace(tzinfo=dt_timezone.utc) - std_offset
    start = start_utc.astimezone(tz)

    end = start + timedelta(hours=24) - timedelta(minutes=1)
    return start, end
