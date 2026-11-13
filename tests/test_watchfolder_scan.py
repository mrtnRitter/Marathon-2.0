"""Tests for watchfolder scanning and scan planning."""

# IMPORTS
import os
from datetime import datetime, timedelta
import threading
import time

import pytest

from marathon.modules.watchfolder.rules import WatchSettings
from marathon.modules.watchfolder.scan import (
    FileStat,
    FolderUnavailable,
    KnownFile,
    ScanPlan,
    ScanStopped,
    list_folder,
    plan_scan,
)

# FUNC
def assert_no_scan_threads():
    for thread in threading.enumerate():
        if thread.name == "watchfolder-listing":
            thread.join(1)
            assert not thread.is_alive(), "Listing-Thread wurde nicht beendet"


@pytest.fixture(autouse=True)
def ensure_scan_threads_end():
    yield
    assert_no_scan_threads()


def make_settings(path, *, include=(), exclude=(), recursive=False, wait_s=10):
    return WatchSettings(str(path), tuple(include), tuple(exclude), wait_s, recursive)


def make_known(size=10, mtime_ns=100, *, stable_since=None, asset_id=None, missing_since=None):
    return KnownFile(
        size,
        mtime_ns,
        stable_since or datetime(2025, 1, 1),
        asset_id,
        missing_since,
    )


# TESTS
def test_new_listing_entry_is_upserted_but_not_ready():
    now = datetime(2025, 1, 2)
    listing = {"a.mxf": FileStat(12, 34)}

    plan = plan_scan({}, listing, now, 10)

    assert plan.upserts == {"a.mxf": KnownFile(12, 34, now, None, None)}
    assert plan.ready == ()
    assert plan.deletes == ()


@pytest.mark.parametrize("elapsed,ready", [(10, True), (9, False)])
def test_unchanged_unowned_file_becomes_ready_at_wait_boundary(elapsed, ready):
    start = datetime(2025, 1, 1)
    now = start + timedelta(seconds=elapsed)

    plan = plan_scan({"a.mxf": make_known(stable_since=start)}, {"a.mxf": FileStat(10, 100)}, now, 10)

    assert plan.ready == (("a.mxf",) if ready else ())
    assert plan.upserts == {}


@pytest.mark.parametrize("listing", [FileStat(11, 100), FileStat(10, 101)])
def test_size_or_mtime_change_restarts_stability_period(listing):
    now = datetime(2025, 1, 2)

    plan = plan_scan({"a.mxf": make_known()}, {"a.mxf": listing}, now, 10)

    assert plan.upserts == {"a.mxf": KnownFile(listing.size, listing.mtime_ns, now, None, None)}
    assert plan.ready == ()


def test_missing_unowned_file_is_deleted():
    plan = plan_scan({"z.mxf": make_known(), "a.mxf": make_known()}, {}, datetime(2025, 1, 2), 10)

    assert plan.deletes == ("a.mxf", "z.mxf")
    assert plan.upserts == {}


def test_missing_owned_file_is_marked_once():
    now = datetime(2025, 1, 2)
    known = make_known(asset_id="asset-1")

    plan = plan_scan({"a.mxf": known}, {}, now, 10)

    assert plan.upserts == {"a.mxf": KnownFile(10, 100, known.stable_since, "asset-1", now)}
    assert plan.deletes == ()
    assert plan.ready == ()


def test_returned_unchanged_owned_file_clears_missing_without_becoming_ready():
    known = make_known(asset_id="asset-1", missing_since=datetime(2025, 1, 2))

    plan = plan_scan(
        {"a.mxf": known},
        {"a.mxf": FileStat(known.size, known.mtime_ns)},
        datetime(2025, 1, 3),
        10,
    )

    assert plan.upserts == {
        "a.mxf": KnownFile(known.size, known.mtime_ns, known.stable_since, "asset-1", None)
    }
    assert plan.ready == ()


def test_changed_owned_file_is_ignored():
    known = make_known(asset_id="asset-1")

    plan = plan_scan({"a.mxf": known}, {"a.mxf": FileStat(20, 200)}, datetime(2025, 1, 3), 10)

    assert plan == ScanPlan({}, (), ())


def test_ready_paths_are_sorted():
    start = datetime(2025, 1, 1)
    known = {path: make_known(stable_since=start) for path in ("z.mxf", "a.mxf", "m.mxf")}
    listing = {path: FileStat(10, 100) for path in known}

    plan = plan_scan(known, listing, start + timedelta(seconds=10), 10)

    assert plan.ready == ("a.mxf", "m.mxf", "z.mxf")


def test_list_folder_filters_and_recurses(tmp_path):
    (tmp_path / "a.mxf").write_bytes(b"a")
    (tmp_path / "b.mov").write_bytes(b"bb")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.mxf").write_bytes(b"ccc")

    top_level = list_folder(make_settings(tmp_path, include=(".mxf",)), 2)
    recursive = list_folder(make_settings(tmp_path, include=(".mxf",), recursive=True), 2)
    excluded = list_folder(make_settings(tmp_path, exclude=(".mov",), recursive=True), 2)

    a_path = os.path.join(str(tmp_path), "a.mxf")
    c_path = os.path.join(str(tmp_path), "sub", "c.mxf")
    assert set(top_level) == {a_path}
    assert set(recursive) == {a_path, c_path}
    assert os.path.join(str(tmp_path), "b.mov") not in excluded
    for path, stat in recursive.items():
        actual = os.stat(path)
        assert stat == FileStat(actual.st_size, actual.st_mtime_ns)


def test_list_folder_missing_root_raises_folder_unavailable(tmp_path):
    path = tmp_path / "missing"

    with pytest.raises(FolderUnavailable, match=f"Ordner nicht gefunden: {path}"):
        list_folder(make_settings(path), 1)


def test_root_permission_error_has_specific_message(tmp_path, monkeypatch):
    original = os.scandir

    def denied(path):
        if os.fspath(path) == str(tmp_path):
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(os, "scandir", denied)
    with pytest.raises(FolderUnavailable, match=f"Kein Zugriff auf {tmp_path}"):
        list_folder(make_settings(tmp_path), 1)


def test_nested_oserror_uses_unreadable_message(tmp_path, monkeypatch):
    subfolder = tmp_path / "sub"
    subfolder.mkdir()
    original = os.scandir

    def fails_in_subfolder(path):
        if os.fspath(path) == str(subfolder):
            raise OSError("offline")
        return original(path)

    monkeypatch.setattr(os, "scandir", fails_in_subfolder)
    with pytest.raises(FolderUnavailable, match=rf"Ordner nicht lesbar: {tmp_path} \(offline\)"):
        list_folder(make_settings(tmp_path, recursive=True), 1)


def test_disappearing_file_during_stat_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "gone.mxf").write_bytes(b"x")
    original = os.scandir

    class VanishingEntry:
        name = "gone.mxf"
        path = os.path.join(str(tmp_path), name)

        def is_dir(self, *, follow_symlinks=True):
            return False

        def stat(self, *, follow_symlinks=True):
            raise FileNotFoundError(self.path)

    class Entries:
        def __enter__(self):
            return iter([VanishingEntry()])

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(os, "scandir", lambda path: Entries())
    try:
        assert list_folder(make_settings(tmp_path), 1) == {}
    finally:
        monkeypatch.setattr(os, "scandir", original)


def test_timeout_returns_promptly_and_releases_listing_after_worker_finishes(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked_scan(settings):
        entered.set()
        try:
            release.wait(3)
            return {}
        finally:
            finished.set()

    monkeypatch.setattr("marathon.modules.watchfolder.scan._list_folder_sync", blocked_scan)
    started = time.monotonic()
    try:
        with pytest.raises(FolderUnavailable, match=f"Ordner antwortet nicht: {tmp_path}"):
            list_folder(make_settings(tmp_path), 0.2)
        assert entered.is_set()
        assert time.monotonic() - started < 1
    finally:
        release.set()
        assert finished.wait(1), "Listing-Thread wurde nicht beendet"
        assert_no_scan_threads()

    assert list_folder(make_settings(tmp_path), 1) == {}


def test_second_listing_is_rejected_while_previous_worker_runs(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked_scan(settings):
        entered.set()
        try:
            release.wait(3)
            return {}
        finally:
            finished.set()

    monkeypatch.setattr("marathon.modules.watchfolder.scan._list_folder_sync", blocked_scan)
    settings = make_settings(tmp_path)
    try:
        with pytest.raises(FolderUnavailable, match=f"Ordner antwortet nicht: {tmp_path}"):
            list_folder(settings, 0.2)
        assert entered.is_set()
        started = time.monotonic()
        with pytest.raises(FolderUnavailable, match="vorherige Prüfung läuft noch"):
            list_folder(settings, 1)
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        assert finished.wait(1), "Listing-Thread wurde nicht beendet"
        assert_no_scan_threads()

    assert list_folder(settings, 1) == {}


def test_stop_interrupts_wait_and_worker_is_released(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    stop = threading.Event()

    def blocked_scan(settings):
        entered.set()
        try:
            release.wait(3)
            return {}
        finally:
            finished.set()

    monkeypatch.setattr("marathon.modules.watchfolder.scan._list_folder_sync", blocked_scan)
    setter = threading.Thread(target=lambda: (time.sleep(0.1), stop.set()), daemon=True)
    setter.start()
    started = time.monotonic()
    try:
        with pytest.raises(ScanStopped):
            list_folder(make_settings(tmp_path), 5, stop)
        assert entered.is_set()
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        setter.join(1)
        assert not setter.is_alive()
        assert finished.wait(1), "Listing-Thread wurde nicht beendet"
        assert_no_scan_threads()


def test_worker_exception_is_wrapped_without_unhandled_thread_error(tmp_path, monkeypatch):
    unhandled = []
    monkeypatch.setattr(threading, "excepthook", lambda args: unhandled.append(args))

    def broken_scan(settings):
        raise RuntimeError("scan failed")

    monkeypatch.setattr("marathon.modules.watchfolder.scan._list_folder_sync", broken_scan)

    with pytest.raises(FolderUnavailable):
        list_folder(make_settings(tmp_path), 1)
    assert unhandled == []
    assert_no_scan_threads()
