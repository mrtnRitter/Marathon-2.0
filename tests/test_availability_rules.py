from datetime import datetime

import pytest

from marathon.common.availability import (
    AvailabilityError,
    blocked_process,
    day_keys,
    default_availability,
    normalize_availability,
    process_key,
    schedule_allows,
)


def test_empty_configuration_is_canonical_default():
    expected = {
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
    assert default_availability() == expected
    assert normalize_availability({}) == expected


def test_partial_configuration_receives_defaults_and_sorts_windows():
    result = normalize_availability(
        {
            "schedule": {
                "days": {
                    "mon": [
                        {"start": "13:00", "end": "14:00"},
                        {"start": "09:00", "end": "10:00"},
                    ]
                }
            },
            "idle": {"minutes": 30},
        }
    )
    assert result["schedule"]["enabled"] is False
    assert result["schedule"]["days"]["mon"] == [
        {"start": "09:00", "end": "10:00"},
        {"start": "13:00", "end": "14:00"},
    ]
    assert result["schedule"]["days"]["tue"] == []
    assert result["idle"] == {
        "enabled": False,
        "minutes": 30,
        "max_cpu_percent": 25,
        "max_ram_percent": 75,
        "max_gpu_percent": 20,
    }
    assert result["blocked_processes"] == []


def assert_error(value, message):
    with pytest.raises(AvailabilityError) as error:
        normalize_availability(value)
    assert str(error.value) == message


def test_root_must_be_an_object():
    assert_error([], "Verfügbarkeitsregeln müssen ein Objekt sein")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"mystery": 1}, "Unbekannte Einstellung: mystery"),
        ({"schedule": {"mystery": 1}}, "Unbekannte Einstellung: mystery"),
        ({"idle": {"mystery": 1}}, "Unbekannte Einstellung: mystery"),
        (
            {"schedule": {"days": {"noday": []}}},
            "Unbekannter Wochentag: noday",
        ),
        (
            {"schedule": {"days": {"mon": {}}}},
            "Zeitplan: Liste von Zeitfenstern erwartet (Mo)",
        ),
        (
            {"schedule": {"days": {"mon": [{}] * 11}}},
            "Höchstens 10 Zeitfenster je Tag (Mo)",
        ),
        (
            {"schedule": {"days": {"mon": [{"start": "9:00", "end": "10:00"}]}}},
            "Ungültige Uhrzeit: 9:00 (Format HH:MM)",
        ),
        (
            {"schedule": {"days": {"mon": [{"start": "09:00", "end": "09:00"}]}}},
            "Zeitfenster Mo 09:00–09:00: Beginn und Ende sind gleich",
        ),
        (
            {
                "schedule": {
                    "days": {
                        "mon": [
                            {"start": "09:00", "end": "11:00"},
                            {"start": "10:00", "end": "12:00"},
                        ]
                    }
                }
            },
            "Zeitfenster überschneiden sich: Mo 09:00–11:00 und 10:00–12:00",
        ),
        ({"schedule": {"enabled": 1}}, "schedule.enabled muss true oder false sein"),
        ({"idle": {"enabled": None}}, "idle.enabled muss true oder false sein"),
        (
            {"idle": {"minutes": True}},
            "Leerlauf: Minuten müssen zwischen 1 und 1440 liegen",
        ),
        (
            {"idle": {"minutes": 0}},
            "Leerlauf: Minuten müssen zwischen 1 und 1440 liegen",
        ),
        (
            {"idle": {"max_cpu_percent": 101}},
            "Leerlauf: CPU-Grenze muss zwischen 1 und 100 % liegen",
        ),
        (
            {"idle": {"max_ram_percent": 0}},
            "Leerlauf: RAM-Grenze muss zwischen 1 und 100 % liegen",
        ),
        (
            {"idle": {"max_gpu_percent": False}},
            "Leerlauf: GPU-Grenze muss zwischen 1 und 100 % liegen",
        ),
        ( {"blocked_processes": "resolve"}, "Programme: Liste von Programmnamen erwartet"),
        (
            {"blocked_processes": ["folder/resolve.exe"]},
            "Programmname ohne Pfad angeben: folder/resolve.exe",
        ),
        (
            {"blocked_processes": ["x" * 261]},
            f"Programmname zu lang: {'x' * 261}",
        ),
        (
            {"blocked_processes": [f"app{i}" for i in range(51)]},
            "Höchstens 50 Programme",
        ),
    ],
)
def test_validation_errors_are_exact(value, message):
    assert_error(value, message)


def test_window_and_idle_validation_details():
    assert_error(
        {"schedule": {"days": {"mon": [{"other": 1}]}}},
        "Unbekannte Einstellung: other",
    )
    assert_error(
        {"schedule": {"days": {"mon": [{"start": "24:00", "end": "24:00"}]}}},
        "Ungültige Uhrzeit: 24:00 (Format HH:MM)",
    )
    assert normalize_availability(
        {"schedule": {"days": {"mon": [{"start": "23:00", "end": "24:00"}]}}}
    )["schedule"]["days"]["mon"] == [{"start": "23:00", "end": "24:00"}]
    for field in ("minutes", "max_cpu_percent", "max_ram_percent", "max_gpu_percent"):
        assert_error(
            {"idle": {field: 1.0}},
            {
                "minutes": "Leerlauf: Minuten müssen zwischen 1 und 1440 liegen",
                "max_cpu_percent": "Leerlauf: CPU-Grenze muss zwischen 1 und 100 % liegen",
                "max_ram_percent": "Leerlauf: RAM-Grenze muss zwischen 1 und 100 % liegen",
                "max_gpu_percent": "Leerlauf: GPU-Grenze muss zwischen 1 und 100 % liegen",
            }[field],
        )


def test_schedule_window_crosses_midnight():
    config = normalize_availability(
        {
            "schedule": {
                "enabled": True,
                "days": {"mon": [{"start": "19:00", "end": "07:00"}]},
            }
        }
    )
    assert schedule_allows(config, datetime(2026, 11, 23, 23, 59))
    assert schedule_allows(config, datetime(2026, 11, 24, 6, 59))
    assert not schedule_allows(config, datetime(2026, 11, 24, 7, 0))
    assert not schedule_allows(config, datetime(2026, 11, 23, 18, 59))


def test_sunday_window_allows_early_monday():
    config = normalize_availability(
        {
            "schedule": {
                "enabled": True,
                "days": {"sun": [{"start": "22:00", "end": "02:00"}]},
            }
        }
    )
    assert schedule_allows(config, datetime(2026, 11, 23, 1, 0))


def test_overlapping_windows_rejected_but_touching_windows_allowed():
    assert_error(
        {
            "schedule": {
                "days": {
                    "mon": [
                        {"start": "10:00", "end": "12:00"},
                        {"start": "11:59", "end": "13:00"},
                    ]
                }
            }
        },
        "Zeitfenster überschneiden sich: Mo 10:00–12:00 und 11:59–13:00",
    )
    config = normalize_availability(
        {
            "schedule": {
                "days": {
                    "mon": [
                        {"start": "12:00", "end": "14:00"},
                        {"start": "10:00", "end": "12:00"},
                    ]
                }
            }
        }
    )
    assert config["schedule"]["days"]["mon"] == [
        {"start": "10:00", "end": "12:00"},
        {"start": "12:00", "end": "14:00"},
    ]


def test_overlapping_midnight_windows_rejected_but_touching_allowed():
    assert_error(
        {
            "schedule": {
                "days": {
                    "mon": [
                        {"start": "22:00", "end": "02:00"},
                        {"start": "01:00", "end": "03:00"},
                    ]
                }
            }
        },
        "Zeitfenster überschneiden sich: Mo 01:00–03:00 und 22:00–02:00",
    )
    normalize_availability(
        {
            "schedule": {
                "days": {
                    "mon": [
                        {"start": "22:00", "end": "02:00"},
                        {"start": "02:00", "end": "04:00"},
                    ]
                }
            }
        }
    )


def test_disabled_schedule_is_always_available():
    config = normalize_availability({"schedule": {"enabled": False}})
    assert schedule_allows(config, datetime(2026, 11, 25, 3, 0))


def test_process_keys_and_blocked_process_matching():
    assert process_key("Resolve.EXE") == "resolve"
    config = normalize_availability({"blocked_processes": ["resolve"]})
    assert blocked_process(config, ["Resolve.exe"]) == "resolve"
    assert blocked_process(config, ["other.exe"]) is None


def test_blocked_processes_are_trimmed_and_deduplicated_stably():
    config = normalize_availability(
        {"blocked_processes": [" Resolve.EXE ", "resolve", " blender.exe "]}
    )
    assert config["blocked_processes"] == ["Resolve.EXE", "blender.exe"]
