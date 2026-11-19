"""Database and thread lifecycle tests for the schedule runner."""

# IMPORTS
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from marathon.server.scheduler import ScheduleRunner, run_schedule_tick


# CONFIG
utc = timezone.utc


# FUNC
def _insert_workflow(
    engine,
    workflow_id: str,
    now: datetime,
    *,
    start: datetime | None = None,
    next_run_at: datetime | None = None,
    every: int | None = 1,
    unit: str = "hours",
    active: bool = False,
    pausing: bool = False,
    schedule_waiting: bool = False,
    run_started_at: datetime | None = None,
) -> None:
    if every is not None:
        start = start or now - timedelta(hours=2)
        next_run_at = next_run_at or now - timedelta(minutes=30)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workflows "
                "(id, name, active, pausing, schedule_every, schedule_unit, schedule_start, next_run_at, "
                "schedule_waiting, run_started_at) "
                "VALUES (:id, :name, :active, :pausing, :every, :unit, :start, :next_run_at, "
                ":schedule_waiting, :run_started_at)"
            ),
            {
                "id": workflow_id,
                "name": workflow_id,
                "active": active,
                "pausing": pausing,
                "every": every,
                "unit": unit if every is not None else None,
                "start": start,
                "next_run_at": next_run_at,
                "schedule_waiting": schedule_waiting,
                "run_started_at": run_started_at,
            },
        )


def _workflow(engine, workflow_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT active, pausing, schedule_waiting, run_started_at, schedule_every, "
                "schedule_unit, schedule_start, next_run_at FROM workflows WHERE id = :id"
            ),
            {"id": workflow_id},
        ).mappings().one()
    return dict(row)


def _insert_asset(engine, asset_id: str, workflow_id: str, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO assets (id, workflow_id, name, status) VALUES (:id, :workflow_id, :name, :status)"),
            {"id": asset_id, "workflow_id": workflow_id, "name": asset_id, "status": status},
        )


def _insert_watchfolder_module(connection, workflow_id: str, module_id: str = "watch-one") -> None:
    connection.execute(
        text(
            "INSERT INTO module_types (id, name, category, icon, has_input) "
            "VALUES ('watchfolder', 'Watchfolder', 'source', 'folder', false) ON CONFLICT (id) DO NOTHING"
        )
    )
    connection.execute(
        text("INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, 1)"),
        {"id": workflow_id},
    )
    connection.execute(
        text(
            "INSERT INTO workflow_modules "
            "(workflow_id, version, id, type_id, name, position_x, position_y) "
            "VALUES (:workflow_id, 1, :module_id, 'watchfolder', 'Watchfolder', 0, 0)"
        ),
        {"workflow_id": workflow_id, "module_id": module_id},
    )


def _insert_watch_state(
    connection,
    workflow_id: str,
    module_id: str,
    last_scan_at: datetime | None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO watch_modules (workflow_id, module_id, next_scan_at, last_scan_at) "
            "VALUES (:workflow_id, :module_id, :now, :last_scan_at)"
        ),
        {"workflow_id": workflow_id, "module_id": module_id, "now": datetime.now(utc), "last_scan_at": last_scan_at},
    )


@pytest.fixture(autouse=True)
def _no_scheduler_threads_after_each_test():
    yield
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and any(thread.name == "marathon-scheduler" for thread in threading.enumerate()):
        time.sleep(0.01)
    assert not any(thread.name == "marathon-scheduler" for thread in threading.enumerate())


# DATABASE TESTS
def test_due_waiting_active_workflow_starts_and_records_time(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "scheduled-pending"
    _insert_workflow(db_engine, workflow_id, now, active=True, schedule_waiting=True)
    _insert_asset(db_engine, "asset-pending", workflow_id, "pending")

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"started": [workflow_id], "waiting": []}
    assert state["active"] is True
    assert state["schedule_waiting"] is False
    assert state["run_started_at"] == now
    assert state["next_run_at"] > now


def test_due_inactive_workflow_is_never_started(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "inactive-scheduled"
    _insert_workflow(db_engine, workflow_id, now, schedule_waiting=True)

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"started": [], "waiting": []}
    assert state["active"] is False
    assert state["schedule_waiting"] is True
    assert state["next_run_at"] > now


def test_due_running_workflow_only_advances_next_run(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "already-running"
    run_started_at = now - timedelta(minutes=10)
    _insert_workflow(
        db_engine,
        workflow_id,
        now,
        active=True,
        schedule_waiting=False,
        run_started_at=run_started_at,
    )
    _insert_asset(db_engine, "asset-running", workflow_id, "pending")

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"started": [], "waiting": []}
    assert state["schedule_waiting"] is False
    assert state["run_started_at"] == run_started_at
    assert state["next_run_at"] > now


def test_pausing_workflow_does_not_start_due_run(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "stopping-scheduled"
    _insert_workflow(db_engine, workflow_id, now, active=True, pausing=True, schedule_waiting=True)

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"started": [], "waiting": []}
    assert state["active"] is True
    assert state["schedule_waiting"] is True
    assert state["next_run_at"] > now


def test_invalid_settings_keep_workflow_waiting_and_log_each_module(db_engine, monkeypatch):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "invalid-settings"
    _insert_workflow(db_engine, workflow_id, now, active=True, schedule_waiting=True)
    monkeypatch.setattr(
        "marathon.server.scheduler.workflow_invalid_modules",
        lambda connection, current_id: [{"id": "bad-one"}, {"id": "bad-two"}],
    )

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    with db_engine.connect() as connection:
        logs = connection.execute(
            text("SELECT module_id, level, text FROM log_lines WHERE workflow_id = :id ORDER BY module_id"),
            {"id": workflow_id},
        ).all()
    assert result == {"started": [], "waiting": []}
    assert state["active"] is True
    assert state["schedule_waiting"] is True
    assert state["run_started_at"] is None
    assert state["next_run_at"] > now
    assert [tuple(row) for row in logs] == [
        ("bad-one", "warn", "Zeitgesteuerter Lauf übersprungen: Einstellungen prüfen"),
        ("bad-two", "warn", "Zeitgesteuerter Lauf übersprungen: Einstellungen prüfen"),
    ]


def test_running_workflow_without_work_enters_schedule_waiting(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "run-complete"
    _insert_workflow(
        db_engine,
        workflow_id,
        now,
        active=True,
        schedule_waiting=False,
        run_started_at=now - timedelta(minutes=1),
        next_run_at=now + timedelta(hours=1),
    )

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"started": [], "waiting": [workflow_id]}
    assert state["active"] is True
    assert state["schedule_waiting"] is True


def test_running_workflow_with_open_asset_does_not_wait(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "run-open-asset"
    _insert_workflow(
        db_engine,
        workflow_id,
        now,
        active=True,
        schedule_waiting=False,
        run_started_at=now - timedelta(minutes=1),
        next_run_at=now + timedelta(hours=1),
    )
    _insert_asset(db_engine, "asset-open", workflow_id, "in_progress")

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    assert result == {"started": [], "waiting": []}
    assert _workflow(db_engine, workflow_id)["schedule_waiting"] is False


def test_running_workflow_with_watchfolder_missing_watch_state_does_not_wait(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "watch-state-missing"
    _insert_workflow(
        db_engine,
        workflow_id,
        now,
        active=True,
        schedule_waiting=False,
        run_started_at=now - timedelta(minutes=1),
        next_run_at=now + timedelta(hours=1),
    )
    with db_engine.begin() as connection:
        _insert_watchfolder_module(connection, workflow_id)
        result = run_schedule_tick(connection, now)

    assert result == {"started": [], "waiting": []}
    assert _workflow(db_engine, workflow_id)["active"] is True
    assert _workflow(db_engine, workflow_id)["schedule_waiting"] is False


def test_running_watchfolder_requires_scan_since_run_start(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    run_started_at = now - timedelta(minutes=1)
    workflow_id = "watch-scan-required"
    _insert_workflow(
        db_engine,
        workflow_id,
        now,
        active=True,
        schedule_waiting=False,
        run_started_at=run_started_at,
        next_run_at=now + timedelta(hours=1),
    )
    with db_engine.begin() as connection:
        _insert_watchfolder_module(connection, workflow_id)
        _insert_watch_state(connection, workflow_id, "watch-one", None)
        result = run_schedule_tick(connection, now)
    assert result == {"started": [], "waiting": []}

    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE watch_modules SET last_scan_at = :scanned_at WHERE workflow_id = :id"),
            {"scanned_at": run_started_at - timedelta(seconds=1), "id": workflow_id},
        )
        result = run_schedule_tick(connection, now)
    assert result == {"started": [], "waiting": []}

    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE watch_modules SET last_scan_at = :now WHERE workflow_id = :id"),
            {"now": now, "id": workflow_id},
        )
        result = run_schedule_tick(connection, now)

    assert result == {"started": [], "waiting": [workflow_id]}
    assert _workflow(db_engine, workflow_id)["schedule_waiting"] is True


def test_running_workflow_with_unstable_watchfolder_file_does_not_wait(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "watch-file-unstable"
    _insert_workflow(
        db_engine,
        workflow_id,
        now,
        active=True,
        schedule_waiting=False,
        run_started_at=now - timedelta(minutes=1),
        next_run_at=now + timedelta(hours=1),
    )
    with db_engine.begin() as connection:
        _insert_watchfolder_module(connection, workflow_id)
        _insert_watch_state(connection, workflow_id, "watch-one", now)
        connection.execute(
            text(
                "INSERT INTO watch_files "
                "(workflow_id, module_id, path, size, mtime_ns, stable_since, asset_id, missing_since) "
                "VALUES (:workflow_id, 'watch-one', '/input/new.mov', 10, 20, :now, NULL, NULL)"
            ),
            {"workflow_id": workflow_id, "now": now},
        )
        result = run_schedule_tick(connection, now)

    assert result == {"started": [], "waiting": []}
    assert _workflow(db_engine, workflow_id)["schedule_waiting"] is False


def test_invalid_schedule_is_cleared_without_orphaned_wait_state(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "invalid-schedule"
    _insert_workflow(db_engine, workflow_id, now, every=5000, active=True, schedule_waiting=True)

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"started": [], "waiting": []}
    assert state["schedule_every"] is None
    assert state["schedule_waiting"] is False
    assert state["run_started_at"] is None


def test_manually_active_workflow_is_not_automatically_ended(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "manually-active"
    _insert_workflow(
        db_engine,
        workflow_id,
        now,
        active=True,
        every=None,
        schedule_waiting=False,
        next_run_at=None,
    )

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    assert result == {"started": [], "waiting": []}
    assert _workflow(db_engine, workflow_id)["active"] is True


# RUNNER TESTS
def test_runner_survives_errors():
    calls = []
    called_twice = threading.Event()

    def engine_provider():
        calls.append(None)
        if len(calls) >= 2:
            called_twice.set()
        raise RuntimeError("database unavailable")

    runner = ScheduleRunner(engine_provider, interval_s=0.05)
    runner.start()
    started = time.monotonic()
    try:
        assert called_twice.wait(1)
        assert len(calls) >= 2
    finally:
        assert runner.stop() is True
    assert time.monotonic() - started < 1


def test_stop_interrupts_wait():
    called = threading.Event()

    def engine_provider():
        called.set()
        raise RuntimeError("database unavailable")

    runner = ScheduleRunner(engine_provider, interval_s=60)
    runner.start()
    try:
        assert called.wait(1)
        started = time.monotonic()
        assert runner.stop() is True
        assert time.monotonic() - started < 1
    finally:
        runner.stop()


def test_stop_with_hanging_tick_returns_false():
    release_tick = threading.Event()
    tick_started = threading.Event()

    def engine_provider():
        tick_started.set()
        release_tick.wait()
        raise RuntimeError("released")

    runner = ScheduleRunner(engine_provider, interval_s=60)
    runner.start()
    try:
        assert tick_started.wait(1)
        started = time.monotonic()
        assert runner.stop(timeout_s=0.2) is False
        assert time.monotonic() - started < 1
    finally:
        release_tick.set()
        assert runner.stop(timeout_s=1) is True


def test_create_app_starts_runner_only_when_configured(db_engine, tmp_path):
    app_without_runner = create_app(static_dir=tmp_path)
    with TestClient(app_without_runner):
        assert not any(thread.name == "marathon-scheduler" for thread in threading.enumerate())

    app_with_runner = create_app(static_dir=tmp_path, engine=db_engine, scheduler_interval_s=0.05)
    with TestClient(app_with_runner):
        assert any(thread.name == "marathon-scheduler" and thread.is_alive() for thread in threading.enumerate())
    assert not any(thread.name == "marathon-scheduler" for thread in threading.enumerate())
