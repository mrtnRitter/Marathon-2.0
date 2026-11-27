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
from marathon.server.db.macro_writes import ensure_standard_macros
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
        ensure_standard_macros(connection)
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
    current_module_id: str | None = "copy.main",
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
    skills: list[str] | None = None,
    online: bool = True,
    last_seen: datetime | None = NOW,
    created_at: datetime = NOW,
) -> None:
    capabilities = ["copy_file", "move_file"] if capabilities is None else capabilities
    skills = capabilities if skills is None else skills
    statement = text(
        "INSERT INTO workers "
        "(id, name, status, online, skills, capabilities, last_seen, created_at) "
        "VALUES (:id, :name, :status, :online, :skills, :capabilities, :last_seen, :created_at) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(bindparam("skills", type_=JSONB), bindparam("capabilities", type_=JSONB))
    with db_engine.begin() as connection:
        connection.execute(
            statement,
            {
                "id": worker_id,
                "name": name,
                "status": status,
                "online": online,
                "skills": skills,
                "capabilities": capabilities,
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
    module_id: str = "copy.main",
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
                "type_id": "copy_file" if module_id == "copy.main" else "move_file",
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
    _worker(db_engine, "locked", status="stopped", last_seen=NOW - timedelta(seconds=31))
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
    assert _rows(db_engine, "SELECT status, online FROM workers WHERE id = 'silent'")[0] == {"status": "ready", "online": False}
    assert _rows(db_engine, "SELECT status FROM workers WHERE id = 'locked'")[0]["status"] == "stopped"


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


def test_wait_module_uses_engine_jobs_and_individual_deadlines(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("wait", "wait"), _module("copy", "copy_file")],
        [_connection("watch", "success", "wait"), _connection("wait", "success", "copy")],
    )
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_modules SET settings = '{\"wait_s\": 5}'::jsonb WHERE workflow_id = :id AND id = 'wait.main'"),
            {"id": workflow_id},
        )
    _asset(db_engine, workflow_id, "first", current_module_id="wait.main", step_status="waiting")

    assert _step(db_engine, advance_assets, NOW) == 1
    first = _rows(db_engine, "SELECT * FROM jobs WHERE worker_id IS NULL")[0]
    assert first["status"] == "running"
    assert _asset_row(db_engine, "first")["wait_until"] == NOW + timedelta(seconds=5)

    _asset(db_engine, workflow_id, "second", current_module_id="wait.main", step_status="waiting", list_position=2)
    assert _step(db_engine, advance_assets, NOW + timedelta(seconds=3)) == 1
    wait_until = {row["asset_id"]: row["wait_until"] for row in _rows(
        db_engine,
        "SELECT a.id AS asset_id, a.wait_until FROM assets AS a WHERE a.workflow_id = :id ORDER BY a.id",
        id=workflow_id,
    )}
    assert wait_until == {"first": NOW + timedelta(seconds=5), "second": NOW + timedelta(seconds=8)}

    assert _step(db_engine, advance_assets, NOW + timedelta(seconds=6)) == 1
    assert _job_row(db_engine, first["id"])["status"] == "succeeded"
    assert (_asset_row(db_engine, "first")["current_module_id"], _asset_row(db_engine, "first")["step_status"]) == ("copy.main", "waiting")
    assert _asset_row(db_engine, "second")["step_status"] == "running"


def test_due_legacy_wait_without_engine_job_uses_old_route(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("wait", "wait"), _module("copy", "copy_file")],
        [_connection("watch", "success", "wait"), _connection("wait", "success", "copy")],
    )
    _asset(db_engine, workflow_id, current_module_id="wait.main", step_status="running", wait_until=NOW)

    assert _step(db_engine, advance_assets, NOW) == 1
    assert _asset_row(db_engine, "asset-1")["current_module_id"] == "copy.main"
    assert _history(db_engine, "asset-1")[0]["text"] == "Wartezeit abgelaufen"


def test_schedule_waiting_blocks_engine_work_until_schedule_starts(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("wait", "wait"), _module("copy", "copy_file")],
        [_connection("watch", "success", "wait"), _connection("wait", "success", "copy")],
    )
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_modules SET settings = '{\"wait_s\": 5}'::jsonb WHERE workflow_id = :id AND id = 'wait.main'"),
            {"id": workflow_id},
        )
        connection.execute(text("UPDATE workflows SET schedule_waiting = true WHERE id = :id"), {"id": workflow_id})
    _asset(db_engine, workflow_id, "worker-asset", current_module_id="copy.main", step_status="waiting")
    _asset(db_engine, workflow_id, "wait-asset", current_module_id="wait.main", step_status="waiting", list_position=2)
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
    jobs = _rows(db_engine, "SELECT worker_id, status FROM jobs WHERE workflow_id = :id", id=workflow_id)
    assert len(jobs) == 2
    assert sum(job["worker_id"] is None and job["status"] == "running" for job in jobs) == 1
    assert sum(job["worker_id"] == "worker-1" and job["status"] == "assigned" for job in jobs) == 1


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
        ensure_standard_macros(connection)
    workflow_id = _workflow(db_engine, [_module("none", "engine_none_test")], [])
    _asset(db_engine, workflow_id, current_module_id="none.main", step_status="waiting")

    assert _step(db_engine, advance_assets, NOW) == 1
    assert _asset_row(db_engine, "asset-1")["status"] == "failed"
    assert _history(db_engine, "asset-1")[0]["text"] == "Modul kann nicht ausgeführt werden: engine_none_test"


def test_assign_prefers_skill_rank(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine, "normal", skills=["move_file", "copy_file"])
    _worker(db_engine, "preferred", skills=["copy_file", "move_file"])

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert _rows(db_engine, "SELECT worker_id FROM jobs")[0]["worker_id"] == "preferred"


def test_assign_skips_workers_without_matching_skill_or_capability(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine, "skill-missing", skills=["move_file"])
    _worker(db_engine, "capability-missing", capabilities=["move_file"], skills=["copy_file"])
    _worker(db_engine, "offline", online=False)
    _worker(db_engine, "stopped", status="stopped")

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 0
    assert _rows(db_engine, "SELECT id FROM jobs") == []
    assert _asset_row(db_engine, "asset-1")["step_status"] == "waiting"


def test_worker_with_active_job_gets_no_second_job(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, "active", step_status="running")
    _job(db_engine, workflow_id, job_id="active-job", asset_id="active", status="running")
    _asset(db_engine, workflow_id, "waiting", list_position=2)
    _worker(db_engine, "other", name="Other", skills=["move_file"])

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 0
    assert _rows(db_engine, "SELECT id FROM jobs WHERE status = 'assigned'") == []
    assert _asset_row(db_engine, "waiting")["step_status"] == "waiting"


def test_no_jobs_for_stopped_or_offline_workers(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine, "stopped", status="stopped")
    _worker(db_engine, "offline", status="ready", online=False)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 0
    assert _rows(db_engine, "SELECT id FROM jobs") == []


def test_round_robin_within_tick(db_engine):
    workflow_a = _workflow(db_engine)
    workflow_b = _workflow(db_engine)
    for workflow_id, prefix in ((workflow_a, "a"), (workflow_b, "b")):
        for position in range(3):
            _asset(db_engine, workflow_id, f"{prefix}-{position}", list_position=position)
    _worker(db_engine, "worker-a", name="A")
    _worker(db_engine, "worker-b", name="B")

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
    _worker(db_engine)

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


def test_finish_worker_stops_only_without_active_jobs(db_engine):
    from marathon.server.engine import tick

    workflow_id = _workflow(db_engine)
    _worker(db_engine, "idle-stop", status="stopping")
    _worker(db_engine, "idle-drain", status="draining")
    for status in ("assigned", "running", "cancelling"):
        worker_id = f"stop-{status}"
        asset_id = f"asset-{status}"
        _worker(db_engine, worker_id, status="stopping")
        _asset(db_engine, workflow_id, asset_id, step_status="running")
        _job(
            db_engine,
            workflow_id,
            job_id=f"job-{status}",
            asset_id=asset_id,
            worker_id=worker_id,
            status=status,
        )

    assert _step(db_engine, tick.finish_worker_stops) == 2
    statuses = _rows(
        db_engine,
        "SELECT id, status FROM workers WHERE id IN ('idle-stop', 'idle-drain') OR id LIKE 'stop-%' ORDER BY id",
    )
    assert statuses == [
        {"id": "idle-drain", "status": "stopped"},
        {"id": "idle-stop", "status": "stopped"},
        {"id": "stop-assigned", "status": "stopping"},
        {"id": "stop-cancelling", "status": "stopping"},
        {"id": "stop-running", "status": "stopping"},
    ]


def test_step_error_does_not_stop_tick(db_engine, monkeypatch, caplog):
    from marathon.server.engine import tick

    calls = []
    monkeypatch.setattr(tick, "fail_lost_workers", lambda *args: calls.append("lost") or 1)

    def raise_error(*args):
        calls.append("expired")
        raise RuntimeError("test")

    monkeypatch.setattr(tick, "expire_pickups", raise_error)
    monkeypatch.setattr(tick, "finish_pausing", lambda *args: calls.append("paused") or 1)
    monkeypatch.setattr(tick, "finish_worker_stops", lambda *args: calls.append("stopped") or 1, raising=False)
    monkeypatch.setattr(tick, "advance_assets", lambda *args: calls.append("advanced") or 1)
    monkeypatch.setattr(tick, "assign_jobs", lambda *args: calls.append("assigned") or 1)

    with caplog.at_level("WARNING", logger="marathon.engine"):
        result = run_engine_tick(db_engine, NOW, EngineConfig())

    assert calls == ["lost", "expired", "paused", "stopped", "advanced", "assigned"]
    assert result == {"lost": 1, "expired": 0, "paused": 1, "stopped": 1, "advanced": 1, "assigned": 1}
    assert "Engine-Schritt expired fehlgeschlagen (RuntimeError)" in caplog.text


def test_advisory_lock_skips_steps(db_engine):
    with db_engine.connect() as blocker:
        blocker.exec_driver_sql("BEGIN")
        blocker.execute(text("SELECT pg_advisory_xact_lock(hashtext('marathon.engine'))"))
        try:
            result = run_engine_tick(db_engine, NOW, EngineConfig())
        finally:
            blocker.rollback()

    assert result == {"lost": 0, "expired": 0, "paused": 0, "stopped": 0, "advanced": 0, "assigned": 0}


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


@pytest.mark.parametrize("parallel", ["3", True, 0, 17])
def test_invalid_parallel_setting_uses_single_asset_jobs(db_engine, parallel):
    workflow_id = _workflow(db_engine)
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_modules SET settings = :settings WHERE workflow_id = :id AND id = 'copy.main'").bindparams(
                bindparam("settings", type_=JSONB)
            ),
            {"id": workflow_id, "settings": {"parallel": parallel}},
        )
    for position in range(5):
        _asset(db_engine, workflow_id, f"asset-{position}", list_position=position)
    _worker(db_engine)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert len(_rows(db_engine, "SELECT id FROM jobs")) == 1
    assert len(_rows(db_engine, "SELECT asset_id FROM job_assets")) == 1


def test_parallel_batches_waiting_assets_in_list_order(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("copy", "copy_file")],
        [_connection("watch", "success", "copy")],
    )
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_modules SET settings = '{\"parallel\": 3}'::jsonb WHERE workflow_id = :id AND id = 'copy.main'"),
            {"id": workflow_id},
        )
    for position in range(5):
        _asset(db_engine, workflow_id, f"asset-{position}", list_position=position)
    _worker(db_engine)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 3
    jobs = _rows(db_engine, "SELECT id FROM jobs")
    assert len(jobs) == 1
    assigned_assets = _rows(
        db_engine,
        "SELECT ja.asset_id FROM job_assets AS ja JOIN assets AS a ON a.id = ja.asset_id "
        "WHERE ja.job_id = :job_id ORDER BY a.list_position, a.id",
        job_id=jobs[0]["id"],
    )
    assert [row["asset_id"] for row in assigned_assets] == [f"asset-{index}" for index in range(3)]


def test_asset_without_path_is_failed_and_excluded_from_batch(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("copy", "copy_file")],
        [_connection("watch", "success", "copy")],
    )
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_modules SET settings = '{\"parallel\": 2}'::jsonb WHERE workflow_id = :id AND id = 'copy.main'"),
            {"id": workflow_id},
        )
    _asset(db_engine, workflow_id, "missing", metadata={}, list_position=0)
    _asset(db_engine, workflow_id, "valid-1", list_position=1)
    _asset(db_engine, workflow_id, "valid-2", list_position=2)
    _worker(db_engine)

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 3
    assert _asset_row(db_engine, "missing")["status"] == "failed"
    assert _history(db_engine, "missing")[0]["text"] == "Asset hat keine Datei"
    assert {row["asset_id"] for row in _rows(db_engine, "SELECT asset_id FROM job_assets")} == {"valid-1", "valid-2"}


def test_skill_rank_example_and_tick_worker_limit(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, "copy-asset", current_module_id="copy.main", list_position=1)
    _asset(db_engine, workflow_id, "move-asset", current_module_id="move.main", list_position=2)
    _worker(db_engine, "worker-a", name="A", skills=["copy_file", "move_file"])
    _worker(db_engine, "worker-b", name="B", skills=["move_file", "copy_file"])

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 2
    assigned = {
        row["asset_id"]: row["worker_id"]
        for row in _rows(db_engine, "SELECT ja.asset_id, j.worker_id FROM job_assets AS ja JOIN jobs AS j ON j.id = ja.job_id")
    }
    assert assigned == {"copy-asset": "worker-a", "move-asset": "worker-b"}


def test_busy_best_rank_worker_falls_back_to_next_candidate(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, "active", step_status="running")
    _job(db_engine, workflow_id, job_id="active-job", asset_id="active", worker_id="worker-a")
    _asset(db_engine, workflow_id, "waiting", list_position=2)
    _worker(db_engine, "worker-a", name="A", skills=["copy_file", "move_file"])
    _worker(db_engine, "worker-b", name="B", skills=["move_file", "copy_file"])

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert _rows(db_engine, "SELECT worker_id FROM jobs WHERE id <> 'active-job'")[0]["worker_id"] == "worker-b"


def test_equal_skill_rank_prefers_worker_name(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine, "z-worker", name="Zulu", skills=["copy_file"])
    _worker(db_engine, "b-worker", name="Alpha", skills=["copy_file"])
    _worker(db_engine, "a-worker", name="Alpha", skills=["copy_file"])

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert _rows(db_engine, "SELECT worker_id FROM jobs")[0]["worker_id"] == "a-worker"


def test_asset_without_path_fails_without_available_worker(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, metadata={})

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assert _asset_row(db_engine, "asset-1")["status"] == "failed"
    assert _history(db_engine, "asset-1")[0]["text"] == "Asset hat keine Datei"
    assert _rows(db_engine, "SELECT id FROM jobs") == []


@pytest.mark.parametrize(("approved", "client_available"), [(False, True), (True, False)])
def test_assignment_skips_unapproved_or_unavailable_workers(db_engine, approved, client_available):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine, "blocked", status="ready")
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workers SET approved = :approved, client_available = :available WHERE id = 'blocked'"),
            {"approved": approved, "available": client_available},
        )

    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 0
    assert _rows(db_engine, "SELECT id FROM jobs") == []
    assert _asset_row(db_engine, "asset-1")["step_status"] == "waiting"



def test_drain_returns_unstarted_asset_for_next_assignment(db_engine):
    from marathon.server.db.worker_writes import drain_worker

    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, "drain-asset", step_status="running")
    _job(db_engine, workflow_id, job_id="drain-job", asset_id="drain-asset", status="assigned")
    _worker(db_engine, "replacement", name="Replacement")

    with db_engine.begin() as connection:
        assert drain_worker(connection, "worker-1", NOW) is True
    assert _asset_row(db_engine, "drain-asset")["step_status"] == "waiting"
    assert _asset_row(db_engine, "drain-asset")["attempts"] == 0
    assert _step(db_engine, assign_jobs, NOW, EngineConfig()) == 1
    assignment = _rows(
        db_engine,
        "SELECT jobs.worker_id FROM jobs JOIN job_assets ON job_assets.job_id = jobs.id "
        "WHERE job_assets.asset_id = 'drain-asset' AND jobs.status = 'assigned'",
    )
    assert assignment == [{"worker_id": "replacement"}]
