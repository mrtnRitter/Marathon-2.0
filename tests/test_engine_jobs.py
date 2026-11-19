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
        "INSERT INTO workers (id, name, status, capabilities) "
        "VALUES (:id, :name, 'ready', :capabilities) ON CONFLICT (id) DO NOTHING"
    ).bindparams(bindparam("capabilities", type_=JSONB))
    with db_engine.begin() as connection:
        connection.execute(
            statement,
            {"id": worker_id, "name": name, "capabilities": ["copy_file", "move_file"]},
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


# RELEASE WORKER TESTS

def _create_batch(db_engine, asset_ids: list[str], *, attempts: list[int] | None = None, module_id: str = "copy"):
    from marathon.server.engine import jobs as job_ops

    workflow_id = _workflow(db_engine)
    attempts = attempts or [0] * len(asset_ids)
    for index, (asset_id, attempt) in enumerate(zip(asset_ids, attempts, strict=True), start=1):
        _asset(db_engine, workflow_id, asset_id, current_module_id=module_id, attempts=attempt, list_position=index)
    _worker(db_engine)
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        job_id = job_ops.create_batch_job(connection, asset_ids, graph, "worker-1", NOW, 5)
    return workflow_id, job_id


def _start_batch_job(db_engine, job_id: str) -> None:
    from marathon.server.engine import jobs as job_ops

    with db_engine.begin() as connection:
        assert job_ops.start_job(connection, job_id, "worker-1", NOW) is True


def _create_wait_job(db_engine, asset_id: str = "wait-asset"):
    from marathon.server.engine import jobs as job_ops

    workflow_id = _workflow(
        db_engine,
        modules=[_module("watch", "watchfolder"), _module("wait", "wait"), _module("move", "move_file")],
        connections=[_connection("watch", "success", "wait"), _connection("wait", "success", "move")],
    )
    _asset(db_engine, workflow_id, asset_id, current_module_id="wait")
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        job_id = job_ops.create_engine_job(connection, asset_id, graph, NOW)
    return workflow_id, job_id


def test_create_batch_job_three_assets_tracks_attempts_and_assignment_logs(db_engine):
    workflow_id, job_id = _create_batch(db_engine, ["batch-a", "batch-b", "batch-c"], attempts=[1, 4, 2])
    job = _job_row(db_engine, job_id)
    assert job["attempt"] == 5
    with db_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT asset_id FROM job_assets WHERE job_id = :job_id ORDER BY asset_id"), {"job_id": job_id}
        ).scalars().all()
        logs = connection.execute(
            text("SELECT asset_id, text FROM log_lines WHERE job_id = :job_id ORDER BY id"), {"job_id": job_id}
        ).mappings().all()
    assert rows == ["batch-a", "batch-b", "batch-c"]
    assert [(row["asset_id"], row["text"]) for row in logs] == [
        (asset_id, "Zugeteilt an GPU") for asset_id in rows
    ]
    assert _job_row(db_engine, job_id)["workflow_id"] == workflow_id


@pytest.mark.parametrize(
    ("outputs", "job_output"),
    [(["success", "success", "success"], "success"), (["success", None, "success"], None)],
)
def test_asset_results_track_progress_and_common_output(db_engine, outputs, job_output):
    from marathon.server.engine import jobs as job_ops

    _workflow_id, job_id = _create_batch(db_engine, ["result-a", "result-b", "result-c"])
    _start_batch_job(db_engine, job_id)
    for index, (asset_id, output) in enumerate(zip(["result-a", "result-b", "result-c"], outputs, strict=True)):
        with db_engine.begin() as connection:
            assert job_ops.apply_asset_result(
                connection, job_id, asset_id, "worker-1", {"outcome": "result", "output": output}, NOW, 3
            ) is True
        job = _job_row(db_engine, job_id)
        if index == 0:
            assert (job["status"], job["progress"]) == ("running", 33)
    job = _job_row(db_engine, job_id)
    assert (job["status"], job["progress"], job["output_id"]) == ("succeeded", 100, job_output)
    with db_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT outcome, finished_at FROM job_assets WHERE job_id = :job_id"), {"job_id": job_id}
        ).mappings().all()
    assert len(rows) == 3 and all(row["outcome"] == "result" and row["finished_at"] is not None for row in rows)


def test_mixed_asset_error_and_result_fails_job_with_first_error(db_engine):
    from marathon.server.engine import jobs as job_ops

    _workflow_id, job_id = _create_batch(db_engine, ["mixed-a", "mixed-b"])
    _start_batch_job(db_engine, job_id)
    with db_engine.begin() as connection:
        assert job_ops.apply_asset_result(
            connection, job_id, "mixed-a", "worker-1", {"outcome": "error", "error": "Erster Fehler"}, NOW, 3
        ) is True
    assert _job_row(db_engine, job_id)["status"] == "running"
    with db_engine.begin() as connection:
        assert job_ops.apply_asset_result(
            connection, job_id, "mixed-b", "worker-1", {"outcome": "result", "output": "success"}, NOW, 3
        ) is True
    job = _job_row(db_engine, job_id)
    assert (job["status"], job["error"]) == ("failed", "Erster Fehler")


def test_late_result_for_finished_asset_is_rejected_and_logged(db_engine):
    from marathon.server.engine import jobs as job_ops

    workflow_id, job_id = _create_batch(db_engine, ["late-a", "late-b"])
    _start_batch_job(db_engine, job_id)
    result = {"outcome": "result", "output": "success"}
    with db_engine.begin() as connection:
        assert job_ops.apply_asset_result(connection, job_id, "late-a", "worker-1", result, NOW, 3) is True
    with db_engine.begin() as connection:
        assert job_ops.apply_asset_result(connection, job_id, "late-a", "worker-1", result, NOW, 3) is False
    with db_engine.connect() as connection:
        log = connection.execute(
            text("SELECT asset_id, text FROM log_lines WHERE job_id = :job_id ORDER BY id DESC LIMIT 1"),
            {"job_id": job_id},
        ).mappings().one()
    assert (log["asset_id"], log["text"]) == ("late-a", "Spätes Ergebnis verworfen")
    assert _job_row(db_engine, job_id)["progress"] == 50
    assert _asset_row(db_engine, "late-a")["current_module_id"] == "move"
    assert workflow_id == _asset_row(db_engine, "late-a")["workflow_id"]


def test_asset_result_for_asset_outside_job_raises_job_not_found(db_engine):
    from marathon.server.engine import jobs as job_ops

    _workflow_id, job_id = _create_batch(db_engine, ["member-a"])
    with db_engine.begin() as connection, pytest.raises(JobNotFound):
        job_ops.apply_asset_result(
            connection, job_id, "not-a-member", "worker-1", {"outcome": "result", "output": "success"}, NOW, 3
        )


def test_cancelling_batch_finishes_and_returns_each_cancelled_asset(db_engine):
    from marathon.server.engine import jobs as job_ops

    _workflow_id, job_id = _create_batch(db_engine, ["cancel-a", "cancel-b"])
    _start_batch_job(db_engine, job_id)
    with db_engine.begin() as connection:
        job_ops.cancel_job(connection, job_id, "Workflow gestoppt", NOW)
    assert _job_row(db_engine, job_id)["status"] == "cancelling"
    for asset_id in ("cancel-a", "cancel-b"):
        with db_engine.begin() as connection:
            assert job_ops.apply_asset_result(
                connection, job_id, asset_id, "worker-1", {"outcome": "cancelled"}, NOW, 3
            ) is True
    job = _job_row(db_engine, job_id)
    assert job["status"] == "cancelled"
    with db_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT outcome, finished_at FROM job_assets WHERE job_id = :job_id"), {"job_id": job_id}
        ).mappings().all()
    assert len(rows) == 2 and all(row["outcome"] == "cancelled" and row["finished_at"] is not None for row in rows)
    assert all(_asset_row(db_engine, asset_id)["step_status"] == "waiting" for asset_id in ("cancel-a", "cancel-b"))


def test_interrupt_batch_only_affects_open_assets_and_rejects_late_results(db_engine):
    from marathon.server.engine import jobs as job_ops

    _workflow_id, job_id = _create_batch(db_engine, ["interrupt-a", "interrupt-b", "interrupt-c"])
    _start_batch_job(db_engine, job_id)
    with db_engine.begin() as connection:
        assert job_ops.apply_asset_result(
            connection, job_id, "interrupt-a", "worker-1", {"outcome": "result", "output": "success"}, NOW, 3
        ) is True
    with db_engine.begin() as connection:
        job_ops.interrupt_job(connection, job_id, "Worker antwortet nicht", NOW, 3)
    assert _job_row(db_engine, job_id)["status"] == "interrupted"
    assert _asset_row(db_engine, "interrupt-a")["current_module_id"] == "move"
    assert _asset_row(db_engine, "interrupt-a")["attempts"] == 0
    with db_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT asset_id, outcome, finished_at FROM job_assets WHERE job_id = :job_id ORDER BY asset_id"),
            {"job_id": job_id},
        ).mappings().all()
    assert [(row["asset_id"], row["outcome"]) for row in rows] == [
        ("interrupt-a", "result"), ("interrupt-b", "interrupted"), ("interrupt-c", "interrupted")
    ]
    assert all(row["finished_at"] is not None for row in rows)
    with db_engine.begin() as connection:
        assert job_ops.apply_asset_result(
            connection, job_id, "interrupt-b", "worker-1", {"outcome": "result", "output": "success"}, NOW, 3
        ) is False
    assert _asset_row(db_engine, "interrupt-b")["current_module_id"] == "copy"


def test_drop_unstarted_batch_finishes_and_restores_all_assets(db_engine):
    from marathon.server.engine import jobs as job_ops

    _workflow_id, job_id = _create_batch(db_engine, ["drop-a", "drop-b"])
    with db_engine.begin() as connection:
        job_ops.drop_unstarted_job(connection, job_id, NOW)
    assert _job_row(db_engine, job_id)["status"] == "cancelled"
    with db_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT outcome, finished_at FROM job_assets WHERE job_id = :job_id"), {"job_id": job_id}
        ).mappings().all()
    assert len(rows) == 2 and all(row["outcome"] == "cancelled" and row["finished_at"] is not None for row in rows)
    assert all(_asset_row(db_engine, asset_id)["step_status"] == "waiting" for asset_id in ("drop-a", "drop-b"))


def test_engine_wait_job_applies_result_and_asset_logs(db_engine):
    from marathon.server.engine import jobs as job_ops

    workflow_id, job_id = _create_wait_job(db_engine)
    with db_engine.connect() as connection:
        job = connection.execute(text("SELECT worker_id, status FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()
        start_log = connection.execute(
            text("SELECT asset_id, text FROM log_lines WHERE job_id = :id ORDER BY id"), {"id": job_id}
        ).mappings().one()
    assert (job["worker_id"], job["status"]) == (None, "running")
    assert (start_log["asset_id"], start_log["text"]) == ("wait-asset", "Wartezeit gestartet: 60 s")
    with db_engine.begin() as connection:
        assert job_ops.apply_asset_result(
            connection,
            job_id,
            "wait-asset",
            None,
            {"outcome": "result", "output": "success", "log": ["Wartezeit abgelaufen"]},
            NOW + timedelta(seconds=60),
            3,
        ) is True
    assert _job_row(db_engine, job_id)["status"] == "succeeded"
    with db_engine.connect() as connection:
        logs = connection.execute(
            text("SELECT asset_id, text FROM log_lines WHERE job_id = :id ORDER BY id"), {"id": job_id}
        ).mappings().all()
        asset_job = connection.execute(
            text("SELECT outcome, finished_at FROM job_assets WHERE job_id = :id"), {"id": job_id}
        ).mappings().one()
    assert [(row["asset_id"], row["text"]) for row in logs] == [
        ("wait-asset", "Wartezeit gestartet: 60 s"),
        ("wait-asset", "Wartezeit abgelaufen"),
        ("wait-asset", "Ergebnis: Erfolg"),
    ]
    assert asset_job["outcome"] == "result" and asset_job["finished_at"] is not None
    assert _asset_row(db_engine, "wait-asset")["current_module_id"] == "move"
    assert _asset_row(db_engine, "wait-asset")["workflow_id"] == workflow_id


@pytest.mark.parametrize("action", ["cancel", "pause"])
def test_engine_job_cancel_and_pause_are_immediate_and_clear_wait(db_engine, action):
    from marathon.server.engine import jobs as job_ops

    workflow_id, job_id = _create_wait_job(db_engine, f"wait-{action}")
    with db_engine.begin() as connection:
        if action == "cancel":
            job_ops.cancel_job(connection, job_id, "Workflow gestoppt", NOW)
        else:
            assert job_ops.request_pause(connection, workflow_id, NOW) is True
    assert _job_row(db_engine, job_id)["status"] == "cancelled"
    with db_engine.connect() as connection:
        asset_job = connection.execute(
            text("SELECT outcome, finished_at FROM job_assets WHERE job_id = :id"), {"id": job_id}
        ).mappings().one()
    assert asset_job["outcome"] == "cancelled" and asset_job["finished_at"] is not None
    asset = _asset_row(db_engine, f"wait-{action}")
    assert asset["wait_until"] is None and asset["step_status"] == "waiting"


def test_engine_job_removed_module_is_completed_once(db_engine):
    from marathon.server.engine import jobs as job_ops

    workflow_id, job_id = _create_wait_job(db_engine, "wait-removed")
    with db_engine.begin() as connection:
        save_graph(
            connection,
            workflow_id,
            2,
            [_module("watch", "watchfolder"), _module("move", "move_file")],
            [_connection("watch", "success", "move")],
        )
        job_ops.remove_missing_modules(connection, workflow_id, NOW)
    assert _job_row(db_engine, job_id)["status"] == "cancelled"
    with db_engine.connect() as connection:
        asset_job = connection.execute(
            text("SELECT outcome, finished_at FROM job_assets WHERE job_id = :id"), {"id": job_id}
        ).mappings().one()
    assert asset_job["outcome"] == "cancelled" and asset_job["finished_at"] is not None
    assert _asset_row(db_engine, "wait-removed")["wait_until"] is None
