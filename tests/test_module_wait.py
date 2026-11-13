"""Tests for the wait control module."""

# IMPORTS
import threading
import time

import pytest

from marathon.modules import load_catalog
from marathon.modules._job import JobStopped, ModuleJob
from marathon.modules.wait import MODULE, run, validate_settings


# FUNC
def test_wait_catalog_entry():
    spec = next(spec for spec in load_catalog() if spec["id"] == "wait")

    assert [setting["key"] for setting in spec["settings"]] == ["wait_s"]
    assert spec["settings"][0]["default"] == 60
    assert spec["has_input"] is True
    assert [output["id"] for output in spec["outputs"]] == ["success"]


def test_wait_validate_settings():
    assert validate_settings({}) == {}
    assert validate_settings({"wait_s": 0}) == {"wait_s": "Mindestens 1 s"}
    assert validate_settings({"wait_s": 86401}) == {"wait_s": "Höchstens 86400 s"}
    assert validate_settings({"wait_s": True}) == {"wait_s": "Zahl erwartet"}
    assert validate_settings({"wait_s": "5"}) == {"wait_s": "Zahl erwartet"}


def test_wait_run_success():
    stop = threading.Event()
    started = time.monotonic()

    result = run(ModuleJob("asset.mov", {"wait_s": 1}, stop))

    elapsed = time.monotonic() - started
    assert result.output == "success"
    assert result.log == ("1 s gewartet",)
    assert 0.9 <= elapsed <= 3


def test_wait_stop_during_wait():
    stop = threading.Event()
    timer = threading.Timer(0.1, stop.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(JobStopped):
            run(ModuleJob("asset.mov", {"wait_s": 60}, stop))
    finally:
        timer.cancel()
        timer.join()

    assert time.monotonic() - started < 2


def test_wait_stop_before_start():
    stop = threading.Event()
    stop.set()

    with pytest.raises(JobStopped):
        run(ModuleJob("asset.mov", {"wait_s": 60}, stop))


def test_wait_invalid_settings_raise():
    with pytest.raises(ValueError, match="Dauer: Mindestens 1 s"):
        run(ModuleJob("asset.mov", {"wait_s": 0}, threading.Event()))
