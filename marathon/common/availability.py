# IMPORTS
from collections.abc import Iterable, Mapping
from datetime import datetime
import re
from typing import Any


# CONFIG
day_keys = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_day_labels = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_time_pattern = re.compile(r"^([0-9]{2}):([0-9]{2})$")


# FUNC
class AvailabilityError(ValueError):
    """Raised when availability settings are invalid."""


def default_availability() -> dict[str, Any]:
    """Return a fresh canonical configuration with availability unrestricted."""
    return {
        "schedule": {"enabled": False, "days": {day: [] for day in day_keys}},
        "idle": {
            "enabled": False,
            "minutes": 15,
            "max_cpu_percent": 25,
            "max_ram_percent": 75,
            "max_gpu_percent": 20,
        },
        "blocked_processes": [],
    }


def _check_keys(value: Mapping, allowed: set[str]) -> None:
    for key in value:
        if key not in allowed:
            raise AvailabilityError(f"Unbekannte Einstellung: {key}")


def _parse_time(value: object, *, is_end: bool = False) -> int:
    if isinstance(value, str):
        match = _time_pattern.fullmatch(value)
        if match:
            hour, minute = (int(part) for part in match.groups())
            if minute < 60 and (hour < 24 or (is_end and hour == 24 and minute == 0)):
                return hour * 60 + minute
    raise AvailabilityError(f"Ungültige Uhrzeit: {value} (Format HH:MM)")


def _normalize_enabled(section: Mapping, key: str, field: str) -> bool:
    value = section.get(key, False)
    if not isinstance(value, bool):
        raise AvailabilityError(f"{field} muss true oder false sein")
    return value


def _normalize_integer(section: Mapping, key: str, default: int, minimum: int, maximum: int, message: str) -> int:
    value = section.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise AvailabilityError(message)
    return value


def _normalize_schedule_days(value: object) -> dict[str, list[dict[str, str]]]:
    if not isinstance(value, Mapping):
        raise AvailabilityError("Zeitplan: Tage müssen ein Objekt sein")
    normalized = {day: [] for day in day_keys}
    for day, windows in value.items():
        if day not in day_keys:
            raise AvailabilityError(f"Unbekannter Wochentag: {day}")
        label = _day_labels[day_keys.index(day)]
        if not isinstance(windows, list):
            raise AvailabilityError(f"Zeitplan: Liste von Zeitfenstern erwartet ({label})")
        if len(windows) > 10:
            raise AvailabilityError(f"Höchstens 10 Zeitfenster je Tag ({label})")

        parsed_windows = []
        for window in windows:
            if not isinstance(window, Mapping):
                raise AvailabilityError(f"Zeitfenster {label}: Objekt erwartet")
            _check_keys(window, {"start", "end"})
            start = window.get("start")
            end = window.get("end")
            start_minute = _parse_time(start)
            end_minute = _parse_time(end, is_end=True)
            if start_minute == end_minute:
                raise AvailabilityError(f"Zeitfenster {label} {start}–{end}: Beginn und Ende sind gleich")
            absolute_end = end_minute if end_minute > start_minute else end_minute + 1440
            parsed_windows.append((start_minute, absolute_end, start, end))

        parsed_windows.sort(key=lambda item: item[0])
        for index, first in enumerate(parsed_windows):
            for second in parsed_windows[index + 1 :]:
                if any(
                    max(first[0], second[0] + offset) < min(first[1], second[1] + offset)
                    for offset in (-1440, 0, 1440)
                ):
                    raise AvailabilityError(
                        f"Zeitfenster überschneiden sich: {label} {first[2]}–{first[3]} und {second[2]}–{second[3]}"
                    )
        normalized[day] = [
            {"start": start, "end": end}
            for _, _, start, end in parsed_windows
        ]
    return normalized


def _normalize_blocked_processes(value: object) -> list[str]:
    if not isinstance(value, list):
        raise AvailabilityError("Programme: Liste von Programmnamen erwartet")
    normalized = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            raise AvailabilityError("Programme: Liste von Programmnamen erwartet")
        name = item.strip()
        if not name:
            continue
        if "/" in name or "\\" in name:
            raise AvailabilityError(f"Programmname ohne Pfad angeben: {name}")
        if len(name) > 260:
            raise AvailabilityError(f"Programmname zu lang: {name}")
        key = process_key(name)
        if key not in seen:
            seen.add(key)
            normalized.append(name)
    if len(normalized) > 50:
        raise AvailabilityError("Höchstens 50 Programme")
    return normalized


def normalize_availability(value: object) -> dict[str, Any]:
    """Validate settings and return their complete canonical representation."""
    if not isinstance(value, Mapping):
        raise AvailabilityError("Verfügbarkeitsregeln müssen ein Objekt sein")
    _check_keys(value, {"schedule", "idle", "blocked_processes"})
    result = default_availability()

    schedule = value.get("schedule", {})
    if not isinstance(schedule, Mapping):
        raise AvailabilityError("Zeitplan muss ein Objekt sein")
    _check_keys(schedule, {"enabled", "days"})
    result["schedule"] = {
        "enabled": _normalize_enabled(schedule, "enabled", "schedule.enabled"),
        "days": _normalize_schedule_days(schedule.get("days", {})),
    }

    idle = value.get("idle", {})
    if not isinstance(idle, Mapping):
        raise AvailabilityError("Leerlauf muss ein Objekt sein")
    _check_keys(idle, {"enabled", "minutes", "max_cpu_percent", "max_ram_percent", "max_gpu_percent"})
    result["idle"] = {
        "enabled": _normalize_enabled(idle, "enabled", "idle.enabled"),
        "minutes": _normalize_integer(
            idle, "minutes", 15, 1, 1440, "Leerlauf: Minuten müssen zwischen 1 und 1440 liegen"
        ),
        "max_cpu_percent": _normalize_integer(
            idle, "max_cpu_percent", 25, 1, 100, "Leerlauf: CPU-Grenze muss zwischen 1 und 100 % liegen"
        ),
        "max_ram_percent": _normalize_integer(
            idle, "max_ram_percent", 75, 1, 100, "Leerlauf: RAM-Grenze muss zwischen 1 und 100 % liegen"
        ),
        "max_gpu_percent": _normalize_integer(
            idle, "max_gpu_percent", 20, 1, 100, "Leerlauf: GPU-Grenze muss zwischen 1 und 100 % liegen"
        ),
    }
    result["blocked_processes"] = _normalize_blocked_processes(value.get("blocked_processes", []))
    return result


def schedule_allows(config: Mapping[str, Any], now: datetime) -> bool:
    """Check whether a normalized schedule allows jobs at the local time."""
    schedule = config["schedule"]
    if not schedule["enabled"]:
        return True

    minute = now.hour * 60 + now.minute
    today = day_keys[now.weekday()]
    yesterday = day_keys[(now.weekday() - 1) % len(day_keys)]
    for window in schedule["days"][today]:
        start = _parse_time(window["start"])
        end = _parse_time(window["end"], is_end=True)
        if start <= minute < (1440 if end < start else end):
            return True
    for window in schedule["days"][yesterday]:
        start = _parse_time(window["start"])
        end = _parse_time(window["end"], is_end=True)
        if end < start and minute < end:
            return True
    return False


def process_key(name: str) -> str:
    """Return a case-insensitive process key without one trailing .exe."""
    key = name.strip().casefold()
    return key[:-4] if key.endswith(".exe") else key


def blocked_process(config: Mapping[str, Any], running: Iterable[str]) -> str | None:
    """Return the first configured process that is currently running."""
    running_keys = {process_key(name) for name in running}
    return next(
        (name for name in config["blocked_processes"] if process_key(name) in running_keys),
        None,
    )
