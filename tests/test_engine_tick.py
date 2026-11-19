"""Tests for the release engine tick and runner."""

# IMPORTS
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.modules import load_catalog
from marathon.server.config import EngineConfig
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.workflow_writes import create_workflow, save_graph
from marathon.server.engine.jobs import apply_job_result
from marathon.server.engine.tick import (
    EngineRunner,
    advance_assets,
    assign_jobs,
    expire_pickups,
    fail_lost_workers,
    finish_pausing,
    run_engine_tick,
    update_worker_busy,
)


# CONFIG
NOW = datetime(2025, 3, 4, 12, 0, tzinfo=timezone.utc)


# FUNC
def _module(module_id: str, type_id: str, name: str | None = None) -> dict:
    return {
        "id": module_id,
        "type_id": type_id,
        "name": name or module_id,
        "description": "",
        "position": {"x": 0, "y": 0},
        "settings": {},
        "locked": False,
    }


def _connection(source: str, output: str, target: str) -> dict:
    return {"source_module_id": source, "source_output_id": output, "target_module_id": target}


def _workflow(
    db_engine,
    modules: list[dict] | None = None,
    connections: list[dict] | None = None,
    *,
    active: bool = True,
    pausing: bool = False,
) -> str:
    modules = modules or [
        _module("watch", "watchfolder", "Watchfolder"),
        _module("copy", "copy_file", "Kopieren"),
        _module("move", "move_file", "Verschieben"),
    ]
    connections = connections if connections is not None else [
        _connection("watch", "success", "copy"),
        _connection("copy", "success", "move"),
    ]
    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
        workflow_id = create_workflow(connection, f"Tick test {uuid4().hex[:8]}", "")
        save_graph(connection, workflow_id, 1, modules, connections)
        connection.execute(
            text("UPDATE workflows SET active = :active, pausing = :pausing WHERE id = :id"),
            {"id": workflow_id, "active": active, "pausing": pausing},
        )
    return workflow_id


def _asset(
    db_engine,
    workflow_id: str,
    asset_id: str = "asset-1",
    *,
    status: str = "in_progress",
    current_module_id: str | None = "copy",
    step_status: str | None = "waiting",
    metadata: dict | None = None,
    attempts: int = 0,
    wait_until: datetime | None = None,
    list_position: int = 1,
) -> None:
    metadata = {"path": f"/data/{asset_id}.mov"} if metadata is None else metadata
    statement = text(
        "INSERT INTO assets "
        "(id, workflow_id, name, status, current_module_id, step_status, metadata, attempts, wait_until, list_position) "
        "VALUES (:id, :workflow_id, :name, :status, :current_module_id, :step_status, :metadata, "
        ":attempts, :wait_until, :list_position)"
    ).bindparams(bindparam("metadata", type_=JSONB))
    with db_engine.begin() as connection:
        connection.execute(
            statement,
            {
                "id": asset_id,
                "workflow_id": workflow_id,
                "name": f"{asset_id}.mov",
                "status": status,
                "current_module_id": current_module_id,
                "step_status": step_status,
                "metadata": metadata,
                "attempts": attempts,
                "wait_until": wait_until,
                "list_position": list_position,
            },
        )


def _worker(
    db_engine,
    worker_id: str = "worker-1",
    *,
    name: str = "GPU",
    status: str = "ready",
    capabilities: list[str] | None = None,
    priorities: dict[str, str] | None = None,
    max_parallel: int = 2,
    client_slots: int = 2,
    last_seen: datetime | None = NOW,
    created_at: datetime = NOW,
) -> None:
    statement = text(
        "INSERT INTO workers "
        "(id, name, status, capabilities, priorities, max_parallel, client_slots, last_seen, created_at) "
        "VALUES (:id, :name, :status, :capabilities, :priorities, :max_parallel, :client_slots, :last_seen, :created_at) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(bindparam("capabilities", type_=JSONB), bindparam("priorities", type_=JSONB))
    with db_engine.begin() as connection:
        connection.execute(
            statement,
            {
                "id": worker_id,
                "name": name,
                "status": status,
                "capabilities": capabilities or ["copy_file", "move_file"],
                "priorities": priorities or {},
                "max_parallel": max_parallel,
                "client_slots": client_slots,
                "last_seen": last_seen,
                "created_at": created_at,
            },
        )


def _job(
    db_engine,
    workflow_id: str,
    *,
    job_id: str = "job-1",
    asset_id: str = "asset-1",
    module_id: str = "copy",
    worker_id: str = "worker-1",
    status: str = "running",
    created_at: datetime = NOW,
    pickup_deadline: datetime | None = NOW + timedelta(seconds=10),
) -> None:
    _worker(db_engine, worker_id)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, type_id, version, "
                "settings, pickup_deadline, created_at, started_at) "
                "VALUES (:id, :workflow_id, :module_id, :status, :worker_id, 1, :type_id, 1, '{}'::jsonb, "
                ":pickup_deadline, :created_at, :started_at)"
            ),
            {
                "id": job_id,
                "workflow_id": workflow_id,
                "module_id": module_id,
                "status": status,
                "worker_id": worker_id,
                "type_id": "copy_file" if module_id == "copy" else "move_file",
                "pickup_deadline": pickup_deadline,
                "created_at": created_at,
                "started_at": created_at if status in {"running", "cancelling"} else None,
            },
        )
        connection.execute(
            text("INSERT INTO job_assets (job_id, asset_id) VALUES (:job_id, :asset_id)"),
            {"job_id": job_id, "asset_id": asset_id},
        )


def _rows(db_engine, query: str, **params) -> list[dict]:
    with db_engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(query), params).mappings()]


def _asset_row(db_engine, asset_id: str) -> dict:
    return _rows(db_engine, "SELECT * FROM assets WHERE id = :id", id=asset_id)[0]


def _job_row(db_engine, job_id: str) -> dict:
    return _rows(db_engine, "SELECT * FROM jobs WHERE id = :id", id=job_id)[0]


def _history(db_engine, asset_id: str) -> list[dict]:
    return _rows(
        db_engine,
        "SELECT kind, text FROM asset_history WHERE asset_id = :id ORDER BY id",
        id=asset_id,
    )


def _step(db_engine, function, *args) -> int:
    with db_engine.begin() as connection:
        return function(connection, *args)


def _job_counts(db_engine, workflow_ids: list[str]) -> dict[str, int]:
    return {
        workflow_id: count
        for workflow_id, count in (
            (row["workflow_id"], row["count"])
            for row in _rows(
                db_engine,
                "SELECT workflow_id, count(*) AS count FROM jobs WHERE workflow_id = ANY(:ids) "
                "AND status IN ('assigned', 'running', 'cancelling') GROUP BY workflow_id",
                ids=workflow_ids,
            )
        )
    }


# TESTS
def test_lost_worker_jobs(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, "running-asset", step_status="running", attempts=0)
    _asset(db_engine, workflow_id, "assigned-asset", step_status="running", attempts=0)
    _asset(db_engine, workflow_id, "locked-asset", step_status="running", attempts=0)
    _worker(db_engine, "silent", last_seen=NOW - timedelta(seconds=31))
    _worker(db_engine, "locked", status="locked", last_seen=NOW - timedelta(seconds=31))
    _job(db_engine, workflow_id, job_id="running-job", asset_id="running-asset", worker_id="silent")
    _job(db_engine, workflow_id, job_id="assigned-job", asset_id="assigned-asset", worker_id="silent", status="assigned")
    _job(db_engine, workflow_id, job_id="locked-job", asset_id="locked-asset", worker_id="locked")

    count = _step(db_engine, fail_lost_workers, NOW, EngineConfig(worker_timeout_s=30))

    assert count >= 3
    assert _job_row(db_engine, "running-job")["status"] == "interrupted"
    assert _job_row(db_engine, "running-job")["error"] == "Worker antwortet nicht"
    assert _job_row(db_engine, "assigned-job")["status"] == "cancelled"
    assert _job_row(db_engine, "assigned-job")["error"] == "Nicht abgeholt"
    assert _asset_row(db_engine, "assigned-asset")["step_status"] == "waiting"
    assert _job_row(db_engine, "locked-job")["status"] == "interrupted"
    assert _rows(db_engine, "SELECT status FROM workers WHERE id = 'silent'")[0]["status"] == "offline"
    assert _rows(db_engine, "SELECT status FROM workers WHERE id = 'locked'")[0]["status"] == "locked"


def test_expire_pickups(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, step_status="running")
    _job(db_engine, workflow_id, status="assigned", pickup_deadline=NOW - timedelta(seconds=1))

    assert _step(db_engine, expire_pickups, NOW) == 1
    assert _job_row(db_engine, "job-1")["status"] == "cancelled"
    assert _job_row(db_engine, "job-1")["error"] == "Nicht abgeholt"
    assert _asset_row(db_engine, "asset-1")["step_status"] == "waiting"
    assert _asset_row(db_engine, "asset-1")["attempts"] == 0


def test_finish_pausing(db_engine):
    workflow_id = _workflow(db_engine, active=False, pausing=True)

    assert _step(db_engine, finish_pausing) == 1
    assert _rows(db_engine, "SELECT pausing FROM workflows WHERE id = :id", id=workflow_id)[0]["pausing"] is False


def test_schedule_waiting_still_processes_running_job_failures(db_engine):
    workflow_id = _workflow(db_engine)
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE workflows SET schedule_waiting = true WHERE id = :id"), {"id": workflow_id})
    _asset(db_engine, workflow_id, "running-asset", step_status="running")
    _worker(db_engine, "silent", last_seen=NOW - timedelta(seconds=31))
    _job(db_engine, workflow_id, job_id="running-job", asset_id="running-asset", worker_id="silent")

    count = _step(db_engine, fail_lost_workers, NOW, EngineConfig(worker_timeout_s=30))

    assert count >= 1
    assert _job_row(db_engine, "running-job")["status"] == "interrupted"


def test_enter_only_running_workflows(db_engine):
    inactive_id = _workflow(db_engine, active=False)
    pausing_id = _workflow(db_engine, active=False, pausing=True)
    _asset(db_engine, inactive_id, "inactive-asset", status="pending", current_module_id=None, step_status=None)
    _asset(db_engine, pausing_id, "pausing-asset", status="pending", current_module_id=None, step_status=None)

    assert _step(db_engine, advance_assets, NOW) == 0
    assert _asset_row(db_engine, "inactive-asset")["status"] == "pending"
    assert _asset_row(db_engine, "pausing-asset")["status"] == "pending"


def test_wait_module(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("wait", "wait"), _module("copy", "copy_file")],
        [_connection("watch", "success", "wait"), _connection("wait", "success", "copy")],
    )
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_modules SET settings = '{\"wait_s\": 5}'::jsonb WHERE workflow_id = :id AND id = 'wait'"),
            {"id": workflow_id},
        )
    _asset(db_engine, workflow_id, current_module_id="wait", step_status="waiting")

    assert _step(db_engine, advance_assets, NOW) == 1
    asset = _asset_row(db_engine, "asset-1")
    assert asset["step_status"] == "running"
    assert asset["wait_until"] == NOW + timedelta(seconds=5)

    assert _step(db_engine, advance_assets, NOW + timedelta(seconds=6)) == 1
    asset = _asset_row(db_engine, "asset-1")
    assert (asset["current_module_id"], asset["step_status"]) == ("copy", "waiting")
    assert _history(db_engine, "asset-1") == [{"kind": "info", "text": "Wartezeit abgelaufen"}]


def test_schedule_waiting_blocks_engine_work_until_schedule_starts(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("wait", "wait"), _module("copy", "copy_file")],
        [_connection("watch", "success", "wait"), _connection("wait", "success", "copy")],
    )
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_modules SET settings = '{\"wait_s\": 5}'::jsonb WHERE workflow_id = :id AND id = 'wait'"),
            {"id": workflow_id},
        )
        connection.execute(text("UPDATE workflows SET schedule_waiting = true WHERE id = :id"), {"id": workflow_id})
    _asset(db_engine, workflow_id, "worker-asset", current_module_id="copy", step_status="waiting")
    _asset(db_engine, workflow_id, "wait-asset", current_module_id="wait", step_status="waiting", list_position=2)
    _worker(db_engine, capabilities=["copy_file"])

    assert _step(db_engine, advance_assets, NOW) == 0
    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 0
    assert _rows(db_engine, "SELECT id FROM jobs WHERE workflow_id = :id", id=workflow_id) == []
    assert _asset_row(db_engine, "wait-asset")["step_status"] == "waiting"

    with db_engine.begin() as connection:
        connection.execute(text("UPDATE workflows SET schedule_waiting = false WHERE id = :id"), {"id": workflow_id})
    assert _step(db_engine, advance_assets, NOW) == 1
    assert _asset_row(db_engine, "wait-asset")["step_status"] == "running"
    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert len(_rows(db_engine, "SELECT id FROM jobs WHERE workflow_id = :id", id=workflow_id)) == 1


def test_non_worker_module_fails(db_engine):
    custom_type = {
        "id": "engine_none_test",
        "name": "Nicht ausführbar",
        "category": "Test",
        "icon": "x",
        "description": "",
        "has_input": True,
        "outputs": [],
        "settings": [],
        "sort_order": 999,
        "execution": "none",
    }
    with db_engine.begin() as connection:
        sync_module_types(connection, [*load_catalog(), custom_type])
    workflow_id = _workflow(db_engine, [_module("none", "engine_none_test")], [])
    _asset(db_engine, workflow_id, current_module_id="none", step_status="waiting")

    assert _step(db_engine, advance_assets, NOW) == 1
    assert _asset_row(db_engine, "asset-1")["status"] == "failed"
    assert _history(db_engine, "asset-1")[0]["text"] == "Modul kann nicht ausgeführt werden: engine_none_test"


def test_assign_prefers_priority(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine, "normal", priorities={"copy_file": "normal"})
    _worker(db_engine, "preferred", priorities={"copy_file": "preferred"})

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert _job_row(db_engine, _rows(db_engine, "SELECT id FROM jobs")[0]["id"])["worker_id"] == "preferred"


def test_assign_skips_off_and_missing_capability(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine, "off", priorities={"copy_file": "off"})
    _worker(db_engine, "missing", capabilities=["move_file"])

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 0
    assert _rows(db_engine, "SELECT id FROM jobs") == []
    assert _asset_row(db_engine, "asset-1")["step_status"] == "waiting"


def test_assign_respects_slots(db_engine):
    workflow_id = _workflow(db_engine)
    _worker(db_engine, max_parallel=3, client_slots=2, status="busy")
    _asset(db_engine, workflow_id, "active", step_status="running")
    _job(db_engine, workflow_id, job_id="active-job", asset_id="active", status="running")
    _asset(db_engine, workflow_id, "waiting-1", list_position=2)
    _asset(db_engine, workflow_id, "waiting-2", list_position=3)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert _rows(db_engine, "SELECT id FROM jobs WHERE status = 'assigned'")
    assert len(_rows(db_engine, "SELECT id FROM jobs WHERE status IN ('assigned', 'running', 'cancelling')")) == 2


def test_no_jobs_for_new_offline_locked_workers(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    for status in ("new", "offline", "locked"):
        _worker(db_engine, status, status=status)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 0
    assert _rows(db_engine, "SELECT id FROM jobs") == []


def test_round_robin_within_tick(db_engine):
    workflow_a = _workflow(db_engine)
    workflow_b = _workflow(db_engine)
    for workflow_id, prefix in ((workflow_a, "a"), (workflow_b, "b")):
        for position in range(3):
            _asset(db_engine, workflow_id, f"{prefix}-{position}", list_position=position)
    _worker(db_engine, max_parallel=2, client_slots=2)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 2
    counts = _job_counts(db_engine, [workflow_a, workflow_b])
    assert counts == {workflow_a: 1, workflow_b: 1}


def test_round_robin_across_ticks(db_engine):
    workflow_a = _workflow(db_engine)
    workflow_b = _workflow(db_engine)
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflows SET last_assigned_at = :at WHERE id = :id"),
            {"at": NOW - timedelta(days=1), "id": workflow_b},
        )
    _asset(db_engine, workflow_a, "a-asset")
    _asset(db_engine, workflow_b, "b-asset")
    _worker(db_engine, max_parallel=1, client_slots=1)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    first = _rows(db_engine, "SELECT workflow_id, id FROM jobs WHERE status = 'assigned'")[0]
    assert first["workflow_id"] == workflow_a
    with db_engine.begin() as connection:
        apply_job_result(connection, first["id"], "worker-1", {"outcome": "success", "output": "success"}, NOW, 3)

    assert _step(db_engine, assign_jobs, NOW + timedelta(seconds=1), EngineConfig()) == 1
    second = _rows(db_engine, "SELECT workflow_id FROM jobs WHERE workflow_id = :id", id=workflow_b)
    assert len(second) == 1


def test_asset_without_path_fails(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, metadata={})
    _worker(db_engine)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert _asset_row(db_engine, "asset-1")["status"] == "failed"
    assert _history(db_engine, "asset-1")[0]["text"] == "Asset hat keine Datei"
    assert _rows(db_engine, "SELECT id FROM jobs") == []


def test_update_worker_busy(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, step_status="running")
    _job(db_engine, workflow_id)

    assert _step(db_engine, update_worker_busy) == 1
    assert _rows(db_engine, "SELECT status FROM workers WHERE id = 'worker-1'")[0]["status"] == "busy"
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET status = 'succeeded' WHERE id = 'job-1'"))
    assert _step(db_engine, update_worker_busy) == 1
    assert _rows(db_engine, "SELECT status FROM workers WHERE id = 'worker-1'")[0]["status"] == "ready"


def test_step_error_does_not_stop_tick(db_engine, monkeypatch, caplog):
    from marathon.server.engine import tick

    calls = []
    monkeypatch.setattr(tick, "fail_lost_workers", lambda *args: calls.append("lost") or 1)

    def raise_error(*args):
        calls.append("expired")
        raise RuntimeError("test")

    monkeypatch.setattr(tick, "expire_pickups", raise_error)
    monkeypatch.setattr(tick, "finish_pausing", lambda *args: calls.append("paused") or 1)
    monkeypatch.setattr(tick, "advance_assets", lambda *args: calls.append("advanced") or 1)
    monkeypatch.setattr(tick, "assign_jobs", lambda *args: calls.append("assigned") or 1)
    monkeypatch.setattr(tick, "update_worker_busy", lambda *args: calls.append("busy") or 1)

    with caplog.at_level("WARNING", logger="marathon.engine"):
        result = run_engine_tick(db_engine, NOW, EngineConfig())

    assert calls == ["lost", "expired", "paused", "advanced", "assigned", "busy"]
    assert result == {"lost": 1, "expired": 0, "paused": 1, "advanced": 1, "assigned": 1, "busy": 1}
    assert "Engine-Schritt expired fehlgeschlagen (RuntimeError)" in caplog.text


def test_advisory_lock_skips_steps(db_engine):
    with db_engine.connect() as blocker:
        blocker.exec_driver_sql("BEGIN")
        blocker.execute(text("SELECT pg_advisory_xact_lock(hashtext('marathon.engine'))"))
        try:
            result = run_engine_tick(db_engine, NOW, EngineConfig())
        finally:
            blocker.rollback()

    assert result == {"lost": 0, "expired": 0, "paused": 0, "advanced": 0, "assigned": 0, "busy": 0}


def test_runner_stop_is_prompt(db_engine, monkeypatch):
    from marathon.server.engine import tick

    called = Event()
    monkeypatch.setattr(tick, "run_engine_tick", lambda *args: called.set() or {})
    runner = EngineRunner(lambda: db_engine, EngineConfig(tick_interval_s=10), clock=lambda: NOW)
    runner.start()
    assert called.wait(1)
    started = monotonic()
    assert runner.stop()
    assert monotonic() - started < 1


def test_assign_does_not_lock_idle_workers(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine, "a-idle", capabilities=["move_file"])
    _worker(db_engine, "b-target", capabilities=["copy_file"])

    with db_engine.connect() as blocker:
        blocker.exec_driver_sql("BEGIN")
        blocker.execute(text("SELECT id FROM workers WHERE id = 'a-idle' FOR UPDATE"))
        try:
            started = monotonic()
            with db_engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                assigned = assign_jobs(connection, NOW, EngineConfig())
            elapsed = monotonic() - started
        finally:
            blocker.rollback()

    assert assigned == 1
    assert elapsed < 2
    assert _rows(db_engine, "SELECT worker_id FROM jobs")[0]["worker_id"] == "b-target"
