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
    every: int = 1,
    unit: str = "hours",
    active: bool = False,
    scheduled_active: bool = False,
) -> None:
    start = start or now - timedelta(hours=2)
    next_run_at = next_run_at or now - timedelta(minutes=30)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workflows "
                "(id, name, active, scheduled_active, schedule_every, schedule_unit, schedule_start, next_run_at) "
                "VALUES (:id, :name, :active, :scheduled_active, :every, :unit, :start, :next_run_at)"
            ),
            {
                "id": workflow_id,
                "name": workflow_id,
                "active": active,
                "scheduled_active": scheduled_active,
                "every": every,
                "unit": unit,
                "start": start,
                "next_run_at": next_run_at,
            },
        )


def _workflow(engine, workflow_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT active, scheduled_active, schedule_every, schedule_unit, schedule_start, next_run_at "
                "FROM workflows WHERE id = :id"
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


@pytest.fixture(autouse=True)
def _no_scheduler_threads_after_each_test():
    yield
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and any(thread.name == "marathon-scheduler" for thread in threading.enumerate()):
        time.sleep(0.01)
    assert not any(thread.name == "marathon-scheduler" for thread in threading.enumerate())


# DATABASE TESTS
def test_due_paused_workflow_with_pending_asset_is_activated(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    start = now - timedelta(minutes=90)
    workflow_id = "scheduled-pending"
    _insert_workflow(db_engine, workflow_id, now, start=start, next_run_at=now - timedelta(minutes=30))
    _insert_asset(db_engine, "asset-pending", workflow_id, "pending")

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"activated": [workflow_id], "paused": []}
    assert state["active"] is True
    assert state["scheduled_active"] is True
    assert state["next_run_at"] == start + timedelta(hours=2)


def test_due_workflow_without_open_assets_activates_and_pauses(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "scheduled-empty"
    _insert_workflow(db_engine, workflow_id, now)

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"activated": [workflow_id], "paused": [workflow_id]}
    assert state["active"] is False
    assert state["scheduled_active"] is False
    assert state["next_run_at"] > now


def test_not_due_workflow_unchanged(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    next_run_at = now + timedelta(minutes=10)
    workflow_id = "scheduled-future"
    _insert_workflow(db_engine, workflow_id, now, next_run_at=next_run_at)

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"activated": [], "paused": []}
    assert state["active"] is False
    assert state["scheduled_active"] is False
    assert state["next_run_at"] == next_run_at


def test_manually_active_workflow_is_not_auto_paused(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "manually-active"
    _insert_workflow(db_engine, workflow_id, now, active=True, scheduled_active=False)

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"activated": [], "paused": []}
    assert state["active"] is True
    assert state["scheduled_active"] is False
    assert state["next_run_at"] > now


def test_auto_pause_respects_in_progress_assets(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    _insert_workflow(db_engine, "auto-pause-done", now, active=True, scheduled_active=True, next_run_at=now + timedelta(hours=1))
    _insert_workflow(db_engine, "auto-pause-running", now, active=True, scheduled_active=True, next_run_at=now + timedelta(hours=1))
    _insert_asset(db_engine, "asset-done", "auto-pause-done", "done")
    _insert_asset(db_engine, "asset-failed", "auto-pause-done", "failed")
    _insert_asset(db_engine, "asset-running", "auto-pause-running", "in_progress")

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    assert result == {"activated": [], "paused": ["auto-pause-done"]}
    assert _workflow(db_engine, "auto-pause-done")["active"] is False
    assert _workflow(db_engine, "auto-pause-done")["scheduled_active"] is False
    assert _workflow(db_engine, "auto-pause-running")["active"] is True
    assert _workflow(db_engine, "auto-pause-running")["scheduled_active"] is True


def test_missed_runs_are_caught_up_once(db_engine):
    now = datetime(2026, 1, 12, 12, tzinfo=utc)
    workflow_id = "missed-runs"
    _insert_workflow(db_engine, workflow_id, now, start=now - timedelta(days=20), next_run_at=now - timedelta(days=10))

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    next_run_at = _workflow(db_engine, workflow_id)["next_run_at"]
    assert result["activated"] == [workflow_id]
    assert next_run_at > now
    assert next_run_at <= now + timedelta(hours=1)


def test_locked_row_is_skipped(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "locked-workflow"
    _insert_workflow(db_engine, workflow_id, now)

    with db_engine.connect() as lock_connection:
        transaction = lock_connection.begin()
        lock_connection.execute(
            text("SELECT id FROM workflows WHERE id = :id FOR UPDATE"), {"id": workflow_id}
        )
        try:
            started = time.monotonic()
            with db_engine.begin() as connection:
                result = run_schedule_tick(connection, now)
            elapsed = time.monotonic() - started
        finally:
            transaction.rollback()

    assert result == {"activated": [], "paused": []}
    assert elapsed < 3
    assert _workflow(db_engine, workflow_id)["active"] is False


def test_invalid_schedule_is_cleared(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    workflow_id = "invalid-schedule"
    _insert_workflow(db_engine, workflow_id, now, every=5000, unit="minutes")

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    state = _workflow(db_engine, workflow_id)
    assert result == {"activated": [], "paused": []}
    assert state["scheduled_active"] is False
    assert state["schedule_every"] is None
    assert state["schedule_unit"] is None
    assert state["schedule_start"] is None
    assert state["next_run_at"] is None


def test_invalid_workflow_does_not_prevent_due_workflow_activation(db_engine):
    now = datetime(2026, 1, 2, 12, tzinfo=utc)
    _insert_workflow(db_engine, "a-invalid", now, every=5000, unit="minutes")
    _insert_workflow(db_engine, "b-valid", now)
    _insert_asset(db_engine, "asset-valid", "b-valid", "pending")

    with db_engine.begin() as connection:
        result = run_schedule_tick(connection, now)

    assert result == {"activated": ["b-valid"], "paused": []}
    assert _workflow(db_engine, "a-invalid")["next_run_at"] is None
    assert _workflow(db_engine, "b-valid")["active"] is True
    assert _workflow(db_engine, "b-valid")["scheduled_active"] is True


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
