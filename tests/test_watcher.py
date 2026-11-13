"""Integration tests for the persistent watchfolder runner."""

# IMPORTS
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.modules import load_catalog
from marathon.modules.watchfolder import scan
from marathon.modules.watchfolder.scan import FileStat
from marathon.server import __main__ as server_main
from marathon.server.app import create_app
from marathon.server.config import DatabaseConfig
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server import watcher


# FUNC
def _add_watch_workflow(
    db_engine,
    path: Path,
    *,
    workflow_id: str = "wf-watch",
    active: bool = True,
    module_ids: tuple[str, ...] = ("watch-1",),
) -> None:
    """Insert a workflow with one or more watchfolder modules."""
    settings = {"path": str(path), "include": "", "exclude": "", "wait_s": 5, "recursive": False}
    module_insert = text(
        "INSERT INTO workflow_modules "
        "(workflow_id, version, id, type_id, name, position_x, position_y, settings) "
        "VALUES (:workflow_id, 1, :module_id, 'watchfolder', :module_name, 0, 0, :settings)"
    ).bindparams(bindparam("settings", type_=JSONB()))
    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
        connection.execute(
            text("INSERT INTO workflows (id, name, active, current_version) VALUES (:id, :id, :active, 1)"),
            {"id": workflow_id, "active": active},
        )
        connection.execute(
            text("INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, 1)"),
            {"id": workflow_id},
        )
        for module_id in module_ids:
            connection.execute(
                module_insert,
                {
                    "workflow_id": workflow_id,
                    "module_id": module_id,
                    "module_name": module_id,
                    "settings": settings,
                },
            )


def _tick(db_engine, now: datetime, stop: threading.Event | None = None) -> dict[str, int]:
    return watcher.run_watch_tick(db_engine, lambda: now, stop if stop is not None else threading.Event())


def _log_lines(db_engine, workflow_id: str = "wf-watch") -> list[str]:
    with db_engine.connect() as connection:
        return connection.execute(
            text("SELECT text FROM log_lines WHERE workflow_id = :workflow_id ORDER BY id"),
            {"workflow_id": workflow_id},
        ).scalars().all()


def test_scanned_at_is_captured_after_folder_listing(db_engine, tmp_path, monkeypatch):
    _add_watch_workflow(db_engine, tmp_path)
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    observed_at = now
    scanned_at = []

    def clock():
        return observed_at

    def list_folder(settings, timeout_s, stop):
        nonlocal observed_at
        observed_at += timedelta(seconds=10)
        return {}

    def apply_scan(connection, claim, listing, timestamp):
        scanned_at.append(timestamp)
        return []

    monkeypatch.setattr(watcher, "list_folder", list_folder)
    monkeypatch.setattr(watcher, "apply_scan", apply_scan)

    result = watcher.run_watch_tick(db_engine, clock, threading.Event())

    assert result["claimed"] == 1
    assert scanned_at == [observed_at]


def test_watch_tick_ingests_file_after_stability_wait(db_engine, tmp_path):
    _add_watch_workflow(db_engine, tmp_path)
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert _tick(db_engine, now) == {"claimed": 1, "assets": 0, "errors": 0, "stale": 0}

    (tmp_path / "clip.mov").write_bytes(b"clip")
    assert _tick(db_engine, now + timedelta(seconds=5))["assets"] == 0
    assert _tick(db_engine, now + timedelta(seconds=10))["assets"] == 1
    assert _tick(db_engine, now + timedelta(seconds=15))["assets"] == 0
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM assets WHERE workflow_id = 'wf-watch'")).scalar_one() == 1


def test_file_growth_restarts_stability_wait(db_engine, tmp_path):
    path = tmp_path / "growing.mov"
    path.write_bytes(b"small")
    _add_watch_workflow(db_engine, tmp_path)
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    _tick(db_engine, now)

    previous = path.stat()
    path.write_bytes(b"larger file")
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 5_000_000_000))
    assert _tick(db_engine, now + timedelta(seconds=5))["assets"] == 0
    assert _tick(db_engine, now + timedelta(seconds=10))["assets"] == 1


def test_inactive_workflow_is_not_claimed(db_engine, tmp_path):
    _add_watch_workflow(db_engine, tmp_path, active=False)
    result = _tick(db_engine, datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert result == {"claimed": 0, "assets": 0, "errors": 0, "stale": 0}
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM assets")).scalar_one() == 0


def test_missing_folder_error_is_deduplicated_then_recovered(db_engine, tmp_path):
    folder = tmp_path / "missing"
    _add_watch_workflow(db_engine, folder)
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert _tick(db_engine, now)["errors"] == 1
    assert _log_lines(db_engine) == [f"Ordner nicht gefunden: {folder}"]
    assert _tick(db_engine, now + timedelta(seconds=5))["errors"] == 1
    assert _log_lines(db_engine) == [f"Ordner nicht gefunden: {folder}"]

    folder.mkdir()
    assert _tick(db_engine, now + timedelta(seconds=10))["errors"] == 0
    assert _log_lines(db_engine) == [
        f"Ordner nicht gefunden: {folder}",
        f"Ordner wieder erreichbar: {folder}",
    ]


def test_hung_listing_times_out_and_logs_error(db_engine, tmp_path, monkeypatch):
    _add_watch_workflow(db_engine, tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocked_listing(settings):
        entered.set()
        release.wait()
        return {}

    monkeypatch.setattr(scan, "_list_folder_sync", blocked_listing)
    monkeypatch.setattr(watcher, "listing_timeout_s", 0.2)
    started = time.monotonic()
    try:
        result = _tick(db_engine, datetime(2025, 1, 1, tzinfo=timezone.utc))
        elapsed = time.monotonic() - started
        assert entered.is_set()
        assert elapsed < 2
        assert result["errors"] == 1
        assert f"Ordner antwortet nicht: {tmp_path}" in _log_lines(db_engine)
    finally:
        release.set()


def test_runner_stop_interrupts_hung_listing(db_engine, tmp_path, monkeypatch):
    _add_watch_workflow(db_engine, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked_listing(settings):
        entered.set()
        release.wait()
        finished.set()
        return {}

    monkeypatch.setattr(scan, "_list_folder_sync", blocked_listing)
    runner = watcher.WatchRunner(
        lambda: db_engine,
        interval_s=10,
        clock=lambda: datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    runner.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        assert runner.stop() is True
        assert time.monotonic() - started < 1.5
    finally:
        release.set()
    assert finished.wait(1)


def test_one_module_failure_does_not_block_next_module(db_engine, tmp_path, monkeypatch):
    path = tmp_path / "ready.mov"
    path.write_bytes(b"ready")
    _add_watch_workflow(db_engine, tmp_path, module_ids=("watch-a", "watch-b"))
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    stat = path.stat()
    with db_engine.begin() as connection:
        for module_id in ("watch-a", "watch-b"):
            connection.execute(
                text("INSERT INTO watch_modules (workflow_id, module_id, next_scan_at) VALUES ('wf-watch', :module_id, :now)"),
                {"module_id": module_id, "now": now},
            )
        connection.execute(
            text(
                "INSERT INTO watch_files "
                "(workflow_id, module_id, path, size, mtime_ns, stable_since) "
                "VALUES ('wf-watch', 'watch-b', :path, :size, :mtime_ns, :stable_since)"
            ),
            {
                "path": str(path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "stable_since": now - timedelta(seconds=5),
            },
        )

    apply_scan = watcher.apply_scan

    def fail_first(connection, claim, listing, scanned_at):
        if claim.module_id == "watch-a":
            raise RuntimeError("test failure")
        return apply_scan(connection, claim, listing, scanned_at)

    monkeypatch.setattr(watcher, "apply_scan", fail_first)
    result = _tick(db_engine, now)
    assert result == {"claimed": 2, "assets": 1, "errors": 1, "stale": 0}
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM assets WHERE workflow_id = 'wf-watch'")).scalar_one() == 1


def test_app_starts_and_stops_watcher_only_when_configured(db_engine, tmp_path):
    app = create_app(static_dir=tmp_path, engine=db_engine, watch_interval_s=0.05)
    with TestClient(app):
        assert any(thread.name == "marathon-watcher" and thread.is_alive() for thread in threading.enumerate())
    assert not any(thread.name == "marathon-watcher" and thread.is_alive() for thread in threading.enumerate())

    app_without_watcher = create_app(static_dir=tmp_path, engine=db_engine)
    with TestClient(app_without_watcher):
        assert not any(thread.name == "marathon-watcher" and thread.is_alive() for thread in threading.enumerate())



def test_server_main_starts_and_stops_watcher_with_test_database(db_engine, tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(server_main, "default_static_dir", static_dir)
    monkeypatch.setattr(
        server_main,
        "load_server_config",
        lambda: DatabaseConfig(host="127.0.0.1", port=1, name="test", user="test"),
    )
    original_create_app = server_main.create_app

    def create_test_app(**kwargs):
        assert kwargs["watch_interval_s"] == watcher.tick_interval_s
        return original_create_app(engine=db_engine, static_dir=static_dir, **kwargs)

    def run_test_server(app, **kwargs):
        with TestClient(app):
            assert any(thread.name == "marathon-watcher" and thread.is_alive() for thread in threading.enumerate())

    monkeypatch.setattr(server_main, "create_app", create_test_app)
    monkeypatch.setattr(server_main.uvicorn, "run", run_test_server)
    assert server_main.main() == 0
    assert not any(thread.name == "marathon-watcher" and thread.is_alive() for thread in threading.enumerate())



def test_runner_logs_tick_exception_and_continues(db_engine, monkeypatch, caplog):
    attempts = 0
    recovered = threading.Event()

    def engine_provider():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("test tick failure")
        recovered.set()
        return db_engine

    runner = watcher.WatchRunner(
        engine_provider,
        interval_s=0.01,
        clock=lambda: datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    with caplog.at_level(logging.WARNING, logger="marathon.watcher"):
        runner.start()
        try:
            assert recovered.wait(2)
        finally:
            assert runner.stop() is True
    assert "RuntimeError" in caplog.text
