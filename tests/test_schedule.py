import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from marathon.server.schedule import ScheduleError, next_occurrence, normalize_interval

U = timezone.utc


@pytest.mark.parametrize(
    ("every", "unit", "expected"),
    [
        (60, "minutes", (1, "hours")),
        (120, "minutes", (2, "hours")),
        (1440, "minutes", (1, "days")),
        (1500, "minutes", (25, "hours")),
        (90, "minutes", (90, "minutes")),
        (24, "hours", (1, "days")),
        (168, "hours", (1, "weeks")),
        (14, "days", (2, "weeks")),
        (30, "days", (30, "days")),
        (12, "months", (12, "months")),
    ],
)
def test_normalize_interval(every, unit, expected):
    assert normalize_interval(every, unit) == expected


@pytest.mark.parametrize(
    ("every", "unit", "message"),
    [
        (0, "days", "Intervall muss mindestens 1 sein"),
        (1501, "minutes", "Intervall zu groß: höchstens 1439 Minuten"),
        (170, "hours", "Intervall zu groß: höchstens 167 Stunden"),
        (366, "days", "Intervall zu groß: höchstens 365 Tage"),
        (53, "weeks", "Intervall zu groß: höchstens 52 Wochen"),
        (13, "months", "Intervall zu groß: höchstens 12 Monate"),
        (1, "years", "Unbekannte Einheit: years"),
        (True, "days", "Intervall muss eine ganze Zahl sein"),
        (1.5, "days", "Intervall muss eine ganze Zahl sein"),
    ],
)
def test_normalize_interval_errors(every, unit, message):
    with pytest.raises(ScheduleError, match=re.escape(message)):
        normalize_interval(every, unit)


def test_daily_occurrences():
    start = datetime(2026, 1, 1, 6, tzinfo=U)
    assert next_occurrence(start, 1, "days", datetime(2025, 12, 31, tzinfo=U), U) == start
    assert next_occurrence(start, 1, "days", start, U) == datetime(2026, 1, 2, 6, tzinfo=U)
    assert next_occurrence(start, 1, "days", datetime(2026, 1, 10, 7, tzinfo=U), U) == datetime(2026, 1, 11, 6, tzinfo=U)


def test_minute_occurrence():
    result = next_occurrence(
        datetime(2026, 1, 1, tzinfo=U), 15, "minutes", datetime(2026, 1, 1, 0, 31, tzinfo=U), U
    )
    assert result == datetime(2026, 1, 1, 0, 45, tzinfo=U)


def test_monthly_occurrences_are_calculated_from_start():
    start = datetime(2026, 1, 31, 8, tzinfo=U)
    assert next_occurrence(start, 1, "months", datetime(2026, 2, 1, tzinfo=U), U) == datetime(2026, 2, 28, 8, tzinfo=U)
    assert next_occurrence(start, 1, "months", datetime(2026, 3, 1, tzinfo=U), U) == datetime(2026, 3, 31, 8, tzinfo=U)


def test_daily_occurrence_across_daylight_saving_time():
    try:
        berlin = ZoneInfo("Europe/Berlin")
    except ZoneInfoNotFoundError:
        pytest.skip("Europe/Berlin timezone data is unavailable")
    start = datetime(2026, 3, 28, 6, tzinfo=berlin)
    after = datetime(2026, 3, 28, 12, tzinfo=berlin)
    assert next_occurrence(start, 1, "days", after, berlin) == datetime(2026, 3, 29, 4, tzinfo=U)


def test_hourly_occurrences_are_absolute():
    try:
        berlin = ZoneInfo("Europe/Berlin")
    except ZoneInfoNotFoundError:
        pytest.skip("Europe/Berlin timezone data is unavailable")
    result = next_occurrence(
        datetime(2026, 3, 28, 23, tzinfo=U), 2, "hours", datetime(2026, 3, 29, 0, 30, tzinfo=U), berlin
    )
    assert result == datetime(2026, 3, 29, 1, tzinfo=U)


def test_occurrence_lookup_is_fast_for_late_after_time():
    start = datetime(2016, 1, 1, tzinfo=U)
    after = datetime(2026, 1, 1, 0, 0, 30, tzinfo=U)
    began = time.monotonic()
    result = next_occurrence(start, 1, "minutes", after, U)
    elapsed = time.monotonic() - began
    assert result == datetime(2026, 1, 1, 0, 1, tzinfo=U)
    assert elapsed < 1


def test_naive_start_is_rejected():
    with pytest.raises(ScheduleError, match=re.escape("Startzeit braucht eine Zeitzone")):
        next_occurrence(datetime(2026, 1, 1), 1, "days", datetime(2026, 1, 2, tzinfo=U), U)


def test_result_is_utc_aware():
    result = next_occurrence(
        datetime(2026, 1, 1, tzinfo=U), 1, "days", datetime(2026, 1, 1, tzinfo=U), U
    )
    assert result.utcoffset() == timedelta(0)
