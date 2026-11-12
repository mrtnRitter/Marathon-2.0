"""Normalize schedule intervals and calculate their next occurrence."""

# IMPORTS
import calendar
from datetime import datetime, timedelta, timezone, tzinfo


# CONFIG
app_name = "Marathon schedule"
app_version = "1.0.0"
schedule_units: tuple[str, ...] = ("minutes", "hours", "days", "weeks", "months")
_MAX_INTERVALS = {"minutes": 1439, "hours": 167, "days": 365, "weeks": 52, "months": 12}
_UNIT_LABELS = {"minutes": "Minuten", "hours": "Stunden", "days": "Tage", "weeks": "Wochen", "months": "Monate"}


# FUNC
class ScheduleError(ValueError):
    """Raised when a schedule interval or timestamp is invalid."""


def normalize_interval(every: int, unit: str) -> tuple[int, str]:
    """Validate and reduce an interval to its largest exact supported unit."""
    if unit not in schedule_units:
        raise ScheduleError(f"Unbekannte Einheit: {unit}")
    if not isinstance(every, int) or isinstance(every, bool):
        raise ScheduleError("Intervall muss eine ganze Zahl sein")
    if every < 1:
        raise ScheduleError("Intervall muss mindestens 1 sein")

    conversions = {"minutes": (60, "hours"), "hours": (24, "days"), "days": (7, "weeks")}
    while unit in conversions and every % conversions[unit][0] == 0:
        divisor, unit = conversions[unit]
        every //= divisor

    maximum = _MAX_INTERVALS[unit]
    if every > maximum:
        raise ScheduleError(f"Intervall zu groß: höchstens {maximum} {_UNIT_LABELS[unit]}")
    return every, unit


def _month_occurrence(start: datetime, every: int, k: int, tz: tzinfo | None) -> datetime:
    month_index = start.year * 12 + start.month - 1 + k * every
    year, month_offset = divmod(month_index, 12)
    month = month_offset + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    local = start.replace(year=year, month=month, day=day)
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def _wall_occurrence(start: datetime, every: int, unit: str, k: int, tz: tzinfo | None) -> datetime:
    if unit == "months":
        return _month_occurrence(start, every, k, tz)
    days = every * k * (7 if unit == "weeks" else 1)
    local = start + timedelta(days=days)
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def next_occurrence(start: datetime, every: int, unit: str, after: datetime, tz: tzinfo | None = None) -> datetime:
    """Return the first scheduled instant strictly later than ``after`` in UTC."""
    every, unit = normalize_interval(every, unit)
    if start.utcoffset() is None or after.utcoffset() is None:
        raise ScheduleError("Startzeit braucht eine Zeitzone")

    after_utc = after.astimezone(timezone.utc)
    if unit in ("minutes", "hours"):
        start_utc = start.astimezone(timezone.utc)
        step = timedelta(minutes=every) if unit == "minutes" else timedelta(hours=every)
        elapsed = (after_utc - start_utc).total_seconds()
        k = max(0, int(elapsed // step.total_seconds()))

        def occurrence(index: int) -> datetime:
            return start_utc + index * step
    else:
        local_start = start.astimezone(tz) if tz is not None else start.astimezone()
        local_after = after.astimezone(tz) if tz is not None else after.astimezone()
        start_naive = local_start.replace(tzinfo=None)
        after_naive = local_after.replace(tzinfo=None)
        if unit == "months":
            month_delta = (after_naive.year - start_naive.year) * 12 + after_naive.month - start_naive.month
            k = max(0, month_delta // every)
        else:
            day_step = every * (7 if unit == "weeks" else 1)
            day_delta = (after_naive - start_naive).days
            k = max(0, day_delta // day_step)

        def occurrence(index: int) -> datetime:
            return _wall_occurrence(start_naive, every, unit, index, tz)

    current = occurrence(k)
    while current <= after_utc:
        k += 1
        current = occurrence(k)
    while k > 0:
        previous = occurrence(k - 1)
        if previous <= after_utc:
            break
        k -= 1
        current = previous
    return current


# MAIN

def main() -> None:
    return None


# EXEC
if __name__ == "__main__":
    main()
