"""Tests for engine job lifecycle and worker results."""

# IMPORTS
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.modules import load_catalog
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.workflow_writes import create_workflow, save_graph
from marathon.server.engine.flow import load_graph
from marathon.server.engine.jobs import (
    JobNotFound,
    JobWorkerMismatch,
    apply_job_result,
    cancel_job,
    create_job,
    drop_unstarted_job,
    graph_impact,
    interrupt_job,
    remove_missing_modules,
    request_pause,
    start_job,
)


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


def _workflow(db_engine, modules: list[dict] | None = None, connections: list[dict] | None = None) -> str:
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
        workflow_id = create_workflow(connection, f"Job test {uuid4().hex[:8]}", "")
        save_graph(connection, workflow_id, 1, modules, connections)
    return workflow_id


def _asset(
    db_engine,
    workflow_id: str,
    asset_id: str = "asset-1",
    *,
    status: str = "in_progress",
    current_module_id: str = "copy",
    step_status: str | None = "waiting",
    metadata: dict | None = None,
    from_module_id: str | None = "watch",
    from_output_id: str | None = "success",
    attempts: int = 0,
    wait_until: datetime | None = None,
    list_position: int = 1,
) -> None:
    statement = text(
        "INSERT INTO assets "
        "(id, workflow_id, name, status, current_module_id, step_status, metadata, from_module_id, "
        "from_output_id, attempts, wait_until, list_position) "
        "VALUES (:id, :workflow_id, :name, :status, :current_module_id, :step_status, :metadata, "
        ":from_module_id, :from_output_id, :attempts, :wait_until, :list_position)"
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
                "metadata": metadata or {},
                "from_module_id": from_module_id,
                "from_output_id": from_output_id,
                "attempts": attempts,
                "wait_until": wait_until,
                "list_position": list_position,
            },
        )


def _worker(db_engine, worker_id: str = "worker-1", name: str = "GPU") -> None:
    statement = text(
        "INSERT INTO workers (id, name, status, capabilities, priorities, max_parallel, client_slots) "
        "VALUES (:id, :name, 'ready', :capabilities, :priorities, 2, 2) ON CONFLICT (id) DO NOTHING"
    ).bindparams(bindparam("capabilities", type_=JSONB), bindparam("priorities", type_=JSONB))
    with db_engine.begin() as connection:
        connection.execute(
            statement,
            {"id": worker_id, "name": name, "capabilities": ["copy_file", "move_file"], "priorities": {}},
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
    attempt: int = 1,
    error: str | None = None,
) -> None:
    _worker(db_engine, worker_id)
    statement = text(
        "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, type_id, version, "
        "settings, created_at, error) VALUES (:id, :workflow_id, :module_id, :status, :worker_id, "
        ":attempt, :type_id, 1, '{}'::jsonb, :created_at, :error)"
    )
    with db_engine.begin() as connection:
        connection.execute(
            statement,
            {
                "id": job_id,
                "workflow_id": workflow_id,
                "module_id": module_id,
                "status": status,
                "worker_id": worker_id,
                "attempt": attempt,
                "type_id": "copy_file" if module_id == "copy" else "move_file",
                "created_at": NOW,
                "error": error,
            },
        )
        connection.execute(
            text("INSERT INTO job_assets (job_id, asset_id) VALUES (:job_id, :asset_id)"),
            {"job_id": job_id, "asset_id": asset_id},
        )


def _row(db_engine, table: str, key: str, value: str) -> dict:
    with db_engine.connect() as connection:
        return dict(connection.execute(text(f"SELECT * FROM {table} WHERE {key} = :value"), {"value": value}).mappings().one())


def _asset_row(db_engine, asset_id: str = "asset-1") -> dict:
    return _row(db_engine, "assets", "id", asset_id)


def _job_row(db_engine, job_id: str = "job-1") -> dict:
    return _row(db_engine, "jobs", "id", job_id)


def _history(db_engine, asset_id: str = "asset-1") -> list[dict]:
    with db_engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            text("SELECT kind, text FROM asset_history WHERE asset_id = :id ORDER BY id"), {"id": asset_id}
        ).mappings()]


def _logs(db_engine, workflow_id: str) -> list[dict]:
    with db_engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            text("SELECT workflow_id, module_id, job_id, level, text FROM log_lines "
                 "WHERE workflow_id = :id ORDER BY id"), {"id": workflow_id}
        ).mappings()]


NOW = datetime(2025, 3, 4, 12, 0, tzinfo=timezone.utc)


def _create_running_job(db_engine, *, status: str = "running", attempts: int = 0, asset_id: str = "asset-1"):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, asset_id, step_status="running", attempts=attempts)
    _job(db_engine, workflow_id, asset_id=asset_id, status=status, attempt=attempts + 1)
    return workflow_id


# TESTS
def test_create_job(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id)
    _worker(db_engine)
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        job_id = create_job(connection, "asset-1", graph, "worker-1", NOW, 15)

    job = _job_row(db_engine, job_id)
    assert job_id.startswith("job-") and len(job_id) == 16
    assert (job["status"], job["worker_id"], job["attempt"], job["type_id"], job["version"]) == (
        "assigned", "worker-1", 1, "copy_file", 2
    )
    assert (job["settings"], job["created_at"], job["pickup_deadline"]) == (
        {}, NOW, NOW + timedelta(seconds=15)
    )
    assert _asset_row(db_engine)["step_status"] == "running"
    assert _logs(db_engine, workflow_id) == [{
        "workflow_id": workflow_id, "module_id": "copy", "job_id": job_id, "level": "info", "text": "Zugeteilt an GPU"
    }]
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT asset_id FROM job_assets WHERE job_id = :id"), {"id": job_id}).scalar_one() == "asset-1"


def test_start_job(db_engine):
    workflow_id = _create_running_job(db_engine, status="assigned")
    with db_engine.begin() as connection:
        assert start_job(connection, "job-1", "worker-other", NOW) is False
        assert start_job(connection, "job-1", "worker-1", NOW) is True
        with pytest.raises(JobNotFound):
            start_job(connection, "missing", "worker-1", NOW)
    assert (_job_row(db_engine)["status"], _job_row(db_engine)["started_at"]) == ("running", NOW)


def test_result_routes_to_next_module(db_engine):
    workflow_id = _create_running_job(db_engine)
    with db_engine.begin() as connection:
        assert apply_job_result(connection, "job-1", "worker-1", {"outcome": "result", "output": "success", "log": []}, NOW, 3)
    asset = _asset_row(db_engine)
    assert (asset["current_module_id"], asset["step_status"]) == ("move", "waiting")
    assert (_job_row(db_engine)["status"], _job_row(db_engine)["output_id"], _job_row(db_engine)["finished_at"]) == (
        "succeeded", "success", NOW
    )


def test_result_without_output_marks_done(db_engine):
    _create_running_job(db_engine)
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {"outcome": "result", "output": None, "log": []}, NOW, 3)
    assert _asset_row(db_engine)["status"] == "done"
    assert _history(db_engine) == [{"kind": "success", "text": "Erledigt an „Kopieren“"}]


def test_result_updates_path(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, step_status="running", metadata={"source_path": "/old/a.mov", "keep": True})
    _job(db_engine, workflow_id)
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {
            "outcome": "result", "output": None, "asset_path": "/new/a.mov", "log": []
        }, NOW, 3)
    assert _asset_row(db_engine)["metadata"] == {"source_path": "/old/a.mov", "keep": True, "path": "/new/a.mov"}


def test_result_spawns_assets(db_engine):
    workflow_id = _create_running_job(db_engine)
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {
            "outcome": "result", "output": None, "log": [],
            "spawned": [{"path": "/render/out.mov", "output": "success", "log": ["Render erstellt"]}],
        }, NOW, 3)
    with db_engine.connect() as connection:
        spawned = dict(connection.execute(
            text("SELECT * FROM assets WHERE id <> 'asset-1' AND workflow_id = :id"), {"id": workflow_id}
        ).mappings().one())
    assert spawned["name"] == "out.mov"
    assert spawned["metadata"] == {"path": "/render/out.mov"}
    assert (spawned["status"], spawned["current_module_id"], spawned["step_status"], spawned["list_position"]) == (
        "in_progress", "move", "waiting", 2
    )
    assert _history(db_engine, spawned["id"]) == [{"kind": "info", "text": "Render erstellt"}]


def test_result_log_lines(db_engine):
    workflow_id = _create_running_job(db_engine)
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {
            "outcome": "result", "output": "error", "log": ["Fehlerdetail"]
        }, NOW, 3)
    log = _logs(db_engine, workflow_id)[0]
    assert (log["job_id"], log["module_id"], log["level"], log["text"]) == (
        "job-1", "copy", "error", "Fehlerdetail"
    )
    assert {"kind": "error", "text": "Fehlerdetail"} in _history(db_engine)


def test_error_routes_via_error_output(db_engine):
    workflow_id = _workflow(db_engine, connections=[
        _connection("watch", "success", "copy"), _connection("copy", "error", "move")
    ])
    _asset(db_engine, workflow_id, step_status="running")
    _job(db_engine, workflow_id)
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {
            "outcome": "error", "error": "Kopierfehler", "log": []
        }, NOW, 3)
    assert (_job_row(db_engine)["status"], _job_row(db_engine)["error"]) == ("failed", "Kopierfehler")
    assert (_asset_row(db_engine)["current_module_id"], _asset_row(db_engine)["step_status"]) == ("move", "waiting")
    assert {"kind": "error", "text": "Kopierfehler"} in _history(db_engine)


def test_error_without_error_output_fails_asset(db_engine):
    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
        connection.execute(text(
            "INSERT INTO module_types (id, name, category, icon, has_input, outputs, settings, execution) "
            "VALUES ('no-error-test', 'Testtyp', 'Test', 'test', true, "
            "'[{\"id\": \"success\"}]'::jsonb, '[]'::jsonb, 'worker')"
        ))
        workflow_id = create_workflow(connection, f"Job test {uuid4().hex[:8]}", "")
        save_graph(connection, workflow_id, 1, [_module("custom", "no-error-test", "Testmodul")], [])
    _asset(db_engine, workflow_id, current_module_id="custom", step_status="running", from_module_id=None, from_output_id=None)
    _job(db_engine, workflow_id, module_id="custom")
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {
            "outcome": "error", "error": "Kein Fehlerausgang", "log": []
        }, NOW, 3)
    assert _asset_row(db_engine)["status"] == "failed"
    assert {"kind": "error", "text": "Kein Fehlerausgang"} in _history(db_engine)
    assert _job_row(db_engine)["status"] == "failed"


def test_invalid_output_treated_as_error(db_engine):
    workflow_id = _create_running_job(db_engine)
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {
            "outcome": "result", "output": "nope", "log": []
        }, NOW, 3)
    assert (_job_row(db_engine)["status"], _job_row(db_engine)["error"]) == ("failed", "Ungültiger Ausgang: nope")
    assert {"kind": "error", "text": "Ungültiger Ausgang: nope"} in _history(db_engine)


def test_cancel_confirmed_returns_asset(db_engine):
    _create_running_job(db_engine, status="cancelling", attempts=2)
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {"outcome": "cancelled", "log": []}, NOW, 3)
    assert _job_row(db_engine)["status"] == "cancelled"
    assert (_asset_row(db_engine)["step_status"], _asset_row(db_engine)["attempts"]) == ("waiting", 2)


def test_unrequested_cancel_interrupts(db_engine):
    _create_running_job(db_engine)
    with db_engine.begin() as connection:
        apply_job_result(connection, "job-1", "worker-1", {"outcome": "cancelled", "log": []}, NOW, 3)
    assert (_job_row(db_engine)["status"], _job_row(db_engine)["error"]) == ("interrupted", "Worker beendet")
    assert (_asset_row(db_engine)["attempts"], _asset_row(db_engine)["step_status"]) == (1, "waiting")


def test_result_while_cancelling_is_applied(db_engine):
    _create_running_job(db_engine, status="cancelling")
    with db_engine.begin() as connection:
        assert apply_job_result(connection, "job-1", "worker-1", {
            "outcome": "result", "output": "success", "log": []
        }, NOW, 3)
    assert _job_row(db_engine)["status"] == "succeeded"
    assert _asset_row(db_engine)["current_module_id"] == "move"


def test_late_result_is_discarded(db_engine):
    workflow_id = _create_running_job(db_engine, status="succeeded")
    with db_engine.begin() as connection:
        assert apply_job_result(connection, "job-1", "worker-1", {"outcome": "result", "output": None, "log": []}, NOW, 3) is False
    assert _logs(db_engine, workflow_id)[0]["level"] == "warn"
    assert _logs(db_engine, workflow_id)[0]["text"] == "Spätes Ergebnis verworfen"


def test_result_from_other_worker_raises(db_engine):
    _create_running_job(db_engine)
    with db_engine.begin() as connection:
        with pytest.raises(JobWorkerMismatch):
            apply_job_result(connection, "job-1", "worker-other", {"outcome": "result", "output": None}, NOW, 3)


def test_asset_mismatch_only_finishes_job(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, current_module_id="move", step_status="running")
    _job(db_engine, workflow_id)
    with db_engine.begin() as connection:
        assert apply_job_result(connection, "job-1", "worker-1", {"outcome": "result", "output": "success", "log": []}, NOW, 3)
    assert _job_row(db_engine)["status"] == "succeeded"
    assert (_asset_row(db_engine)["current_module_id"], _asset_row(db_engine)["step_status"]) == ("move", "running")
    assert _logs(db_engine, workflow_id)[0]["text"] == "Asset passt nicht zum Job"


def test_interrupt_returns_asset(db_engine):
    workflow_id = _create_running_job(db_engine)
    with db_engine.begin() as connection:
        interrupt_job(connection, "job-1", "Worker antwortet nicht", NOW, 3)
    assert _job_row(db_engine)["status"] == "interrupted"
    assert (_asset_row(db_engine)["attempts"], _asset_row(db_engine)["step_status"]) == (1, "waiting")
    assert _history(db_engine) == [{"kind": "error", "text": "Versuch 1 an „Kopieren“ unterbrochen: Worker antwortet nicht"}]


def test_interrupt_max_attempts_fails(db_engine):
    _create_running_job(db_engine, attempts=2)
    with db_engine.begin() as connection:
        interrupt_job(connection, "job-1", "Worker antwortet nicht", NOW, 3)
    assert _job_row(db_engine)["status"] == "interrupted"
    assert (_asset_row(db_engine)["status"], _asset_row(db_engine)["attempts"]) == ("failed", 3)
    assert _history(db_engine)[0]["text"] == "Versuch 3 an „Kopieren“ unterbrochen: Worker antwortet nicht"
    assert _history(db_engine)[1]["text"] == "Nach 3 unterbrochenen Versuchen fehlgeschlagen"


def test_cancel_job_assigned_and_running(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, "assigned-asset")
    _job(db_engine, workflow_id, job_id="assigned-job", asset_id="assigned-asset", status="assigned")
    _asset(db_engine, workflow_id, "running-asset", list_position=2, step_status="running")
    _job(db_engine, workflow_id, job_id="running-job", asset_id="running-asset", status="running")
    with db_engine.begin() as connection:
        cancel_job(connection, "assigned-job", "Workflow gestoppt", NOW)
        cancel_job(connection, "running-job", "Workflow gestoppt", NOW)
    assert _row(db_engine, "jobs", "id", "assigned-job")["status"] == "cancelled"
    assert _row(db_engine, "jobs", "id", "assigned-job")["finished_at"] == NOW
    assert _row(db_engine, "assets", "id", "assigned-asset")["step_status"] == "waiting"
    assert _row(db_engine, "jobs", "id", "running-job")["status"] == "cancelling"


def test_drop_unstarted_job(db_engine):
    workflow_id = _create_running_job(db_engine, status="assigned", attempts=2)
    with db_engine.begin() as connection:
        drop_unstarted_job(connection, "job-1", NOW)
    assert (_job_row(db_engine)["status"], _job_row(db_engine)["error"], _job_row(db_engine)["finished_at"]) == (
        "cancelled", "Nicht abgeholt", NOW
    )
    assert (_asset_row(db_engine)["step_status"], _asset_row(db_engine)["attempts"]) == ("waiting", 2)


def test_request_pause(db_engine):
    workflow_id = _workflow(db_engine)
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE workflows SET active = true, schedule_waiting = true, run_started_at = :now WHERE id = :id"), {"id": workflow_id, "now": NOW})
    _asset(db_engine, workflow_id, "assigned-asset")
    _job(db_engine, workflow_id, job_id="assigned-job", asset_id="assigned-asset", status="assigned")
    _asset(db_engine, workflow_id, "running-asset", list_position=2, step_status="running")
    _job(db_engine, workflow_id, job_id="running-job", asset_id="running-asset", status="running")
    _asset(db_engine, workflow_id, "waiting-asset", list_position=3, step_status="running", wait_until=NOW + timedelta(hours=1))
    with db_engine.begin() as connection:
        assert request_pause(connection, workflow_id, NOW) is True
    state = _row(db_engine, "workflows", "id", workflow_id)
    assert (state["active"], state["schedule_waiting"], state["run_started_at"], state["pausing"]) == (False, False, None, True)
    assert state["updated_at"] == NOW
    assert _row(db_engine, "jobs", "id", "assigned-job")["status"] == "cancelled"
    assert _row(db_engine, "jobs", "id", "running-job")["status"] == "cancelling"
    waiting = _row(db_engine, "assets", "id", "waiting-asset")
    assert (waiting["step_status"], waiting["wait_until"]) == ("waiting", None)

    finished_id = _workflow(db_engine)
    with db_engine.begin() as connection:
        connection.execute(text(
            "UPDATE workflows SET active = true, schedule_waiting = true, run_started_at = :now WHERE id = :id"
        ), {"id": finished_id, "now": NOW})
        assert request_pause(connection, finished_id, NOW) is True
        paused = dict(connection.execute(
            text("SELECT * FROM workflows WHERE id=:id"), {"id": finished_id}
        ).mappings().one())
        assert (paused["active"], paused["schedule_waiting"], paused["run_started_at"], paused["pausing"]) == (False, False, None, False)
        assert request_pause(connection, finished_id, NOW) is True
        after = dict(connection.execute(
            text("SELECT * FROM workflows WHERE id=:id"), {"id": finished_id}
        ).mappings().one())
        assert (after["active"], after["pausing"], after["updated_at"]) == (
            paused["active"], paused["pausing"], paused["updated_at"]
        )
        assert request_pause(connection, "missing", NOW) is False


def test_remove_missing_modules(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, "waiting-asset")
    _asset(db_engine, workflow_id, "running-asset", list_position=2, step_status="running")
    _job(db_engine, workflow_id, job_id="running-job", asset_id="running-asset", status="running")
    with db_engine.begin() as connection:
        assert save_graph(connection, workflow_id, 2, [
            _module("watch", "watchfolder", "Watchfolder"), _module("move", "move_file", "Verschieben")
        ], [_connection("watch", "success", "move")]) == 3
        remove_missing_modules(connection, workflow_id, NOW)
    assert (_asset_row(db_engine, "waiting-asset")["current_module_id"], _asset_row(db_engine, "waiting-asset")["step_status"]) == (
        "move", "waiting"
    )
    assert _row(db_engine, "jobs", "id", "running-job")["status"] == "cancelling"


def test_result_of_removed_module_fails_asset(db_engine):
    workflow_id = _create_running_job(db_engine)
    with db_engine.begin() as connection:
        save_graph(connection, workflow_id, 2, [
            _module("watch", "watchfolder", "Watchfolder"), _module("move", "move_file", "Verschieben")
        ], [_connection("watch", "success", "move")])
        assert apply_job_result(connection, "job-1", "worker-1", {
            "outcome": "result", "output": "success", "log": []
        }, NOW, 3)
    assert _asset_row(db_engine)["status"] == "failed"
    assert _history(db_engine)[0] == {"kind": "error", "text": "Modul nicht mehr im Workflow"}


def test_graph_impact(db_engine):
    workflow_id = _workflow(db_engine)
    _asset(db_engine, workflow_id, current_module_id="copy")
    _job(db_engine, workflow_id, status="running")
    _asset(db_engine, workflow_id, "move-asset", current_module_id="move", list_position=2)

    with db_engine.begin() as connection:
        rerouted = graph_impact(connection, workflow_id, ["watch", "move"], [("watch", "success", "move")])
    assert rerouted["outcomes"] == {"rerouted": 1, "done": 0, "failed": 0}
    assert rerouted["assets"] == sum(rerouted["outcomes"].values()) == 1

    with db_engine.begin() as connection:
        completed = graph_impact(connection, workflow_id, ["watch", "move"], [])
    assert completed["outcomes"] == {"rerouted": 0, "done": 1, "failed": 0}

    with db_engine.begin() as connection:
        failed_source = graph_impact(connection, workflow_id, ["move"], [])
    assert failed_source["outcomes"] == {"rerouted": 0, "done": 0, "failed": 1}

    _asset(db_engine, workflow_id, "missing-source", current_module_id="copy", from_module_id=None, list_position=3)
    _asset(db_engine, workflow_id, "error-output", current_module_id="copy", from_output_id="error", list_position=4)
    with db_engine.begin() as connection:
        edge_cases = graph_impact(connection, workflow_id, ["watch", "move"], [("watch", "success", "move")])
    assert edge_cases["outcomes"] == {"rerouted": 1, "done": 0, "failed": 2}
    assert edge_cases["assets"] == sum(edge_cases["outcomes"].values()) == 3
