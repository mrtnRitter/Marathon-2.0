"""Tests for workflow graph loading and asset routing."""

# IMPORTS
from datetime import datetime, timezone

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.modules import load_catalog
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.workflow_writes import create_workflow, save_graph
from marathon.server.engine.flow import (
    asset_file_path,
    enter_asset,
    fail_asset,
    load_graph,
    return_asset,
    route_asset,
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
    return {
        "source_module_id": source,
        "source_output_id": output,
        "target_module_id": target,
    }


def _workflow(db_engine, modules: list[dict], connections: list[dict] | None = None) -> str:
    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
        workflow_id = create_workflow(connection, "Flow test", "")
        save_graph(connection, workflow_id, 1, modules, connections or [])
    return workflow_id


def _asset(
    db_engine,
    workflow_id: str,
    asset_id: str = "asset-1",
    *,
    status: str = "pending",
    current_module_id: str | None = None,
    step_status: str | None = None,
    metadata: dict | None = None,
    from_module_id: str | None = None,
    from_output_id: str | None = None,
    attempts: int = 0,
    wait_until: datetime | None = None,
) -> None:
    statement = text(
        "INSERT INTO assets "
        "(id, workflow_id, name, status, current_module_id, step_status, metadata, "
        "from_module_id, from_output_id, attempts, wait_until) "
        "VALUES (:id, :workflow_id, :name, :status, :current_module_id, :step_status, :metadata, "
        ":from_module_id, :from_output_id, :attempts, :wait_until)"
    ).bindparams(bindparam("metadata", type_=JSONB))
    with db_engine.begin() as connection:
        connection.execute(
            statement,
            {
                "id": asset_id,
                "workflow_id": workflow_id,
                "name": "asset.mov",
                "status": status,
                "current_module_id": current_module_id,
                "step_status": step_status,
                "metadata": metadata or {},
                "from_module_id": from_module_id,
                "from_output_id": from_output_id,
                "attempts": attempts,
                "wait_until": wait_until,
            },
        )


def _asset_row(db_engine, asset_id: str = "asset-1") -> dict:
    with db_engine.connect() as connection:
        return dict(connection.execute(
            text("SELECT * FROM assets WHERE id = :id"), {"id": asset_id}
        ).mappings().one())


def _history(db_engine, asset_id: str = "asset-1") -> list[dict]:
    with db_engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            text("SELECT kind, text FROM asset_history WHERE asset_id = :id ORDER BY id"),
            {"id": asset_id},
        ).mappings()]


def _logs(db_engine, workflow_id: str) -> list[dict]:
    with db_engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            text("SELECT workflow_id, module_id, job_id, level, text FROM log_lines "
                 "WHERE workflow_id = :id ORDER BY id"),
            {"id": workflow_id},
        ).mappings()]


def test_load_graph_excludes_data_outputs(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("priority", "priolist", "Prioliste"), _module("copy", "copy_file", "Kopieren")],
        [_connection("priority", "list", "copy")],
    )

    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)

    assert graph is not None
    assert graph.modules["priority"].outputs == ()
    assert graph.modules["copy"].outputs == ("success", "error")
    assert ("priority", "list") not in graph.routes


def test_load_graph_missing_workflow_returns_none(db_engine):
    with db_engine.begin() as connection:
        assert load_graph(connection, "missing") is None


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [({"path": "/a", "source_path": "/b"}, "/a"), ({"source_path": "/b"}, "/b"), ({}, None)],
)
def test_asset_file_path(metadata, expected):
    assert asset_file_path(metadata) == expected


def test_route_asset_to_target(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("copy", "copy_file")],
        [_connection("watch", "success", "copy")],
    )
    _asset(db_engine, workflow_id, current_module_id="watch", attempts=2)
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        route_asset(connection, "asset-1", graph, "watch", "success")

    asset = _asset_row(db_engine)
    assert (asset["current_module_id"], asset["status"], asset["step_status"]) == (
        "copy", "in_progress", "waiting"
    )
    assert (asset["from_module_id"], asset["from_output_id"], asset["attempts"]) == (
        "watch", "success", 0
    )


def test_route_asset_keep_attempts(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("copy", "copy_file")],
        [_connection("watch", "success", "copy")],
    )
    _asset(db_engine, workflow_id, current_module_id="watch", attempts=2)
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        route_asset(connection, "asset-1", graph, "watch", "success", keep_attempts=True)
    assert _asset_row(db_engine)["attempts"] == 2


def test_route_asset_end_success(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, current_module_id="copy", step_status="running")
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        route_asset(connection, "asset-1", graph, "copy", "success")
    asset = _asset_row(db_engine)
    assert (asset["status"], asset["step_status"], asset["current_module_id"]) == (
        "done", None, "copy"
    )
    assert _history(db_engine) == [{"kind": "success", "text": "Workflow abgeschlossen"}]


def test_route_asset_end_error(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, current_module_id="copy")
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        route_asset(connection, "asset-1", graph, "copy", "error")
    assert _asset_row(db_engine)["status"] == "failed"
    assert _history(db_engine) == [{"kind": "error", "text": "Fehlgeschlagen an „Kopieren“"}]


def test_route_asset_missing_module(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, current_module_id="copy")
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        route_asset(connection, "asset-1", graph, "removed", "success")
    assert _asset_row(db_engine)["status"] == "failed"
    assert _history(db_engine) == [{"kind": "error", "text": "Modul nicht mehr im Workflow"}]
    assert _logs(db_engine, workflow_id)[0]["text"] == "Modul nicht mehr im Workflow"


def test_return_asset_same_module_keeps_attempts(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("copy", "copy_file")],
        [_connection("watch", "success", "copy")],
    )
    _asset(
        db_engine,
        workflow_id,
        status="in_progress",
        current_module_id="copy",
        step_status="running",
        from_module_id="watch",
        from_output_id="success",
        attempts=2,
        wait_until=datetime.now(timezone.utc),
    )
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        return_asset(connection, "asset-1", graph)
    asset = _asset_row(db_engine)
    assert (asset["current_module_id"], asset["step_status"], asset["attempts"]) == (
        "copy", "waiting", 2
    )
    assert asset["wait_until"] is None


def test_return_asset_rerouted_after_graph_change(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("copy", "copy_file"), _module("move", "move_file")],
        [_connection("watch", "success", "copy")],
    )
    _asset(
        db_engine,
        workflow_id,
        status="in_progress",
        current_module_id="copy",
        from_module_id="watch",
        from_output_id="success",
        attempts=2,
    )
    with db_engine.begin() as connection:
        assert save_graph(
            connection,
            workflow_id,
            2,
            [_module("watch", "watchfolder"), _module("copy", "copy_file"), _module("move", "move_file")],
            [_connection("watch", "success", "move")],
        ) == 3
        graph = load_graph(connection, workflow_id)
        return_asset(connection, "asset-1", graph)
    asset = _asset_row(db_engine)
    assert (asset["current_module_id"], asset["attempts"]) == ("move", 0)


def test_return_asset_without_from_module(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file")])
    _asset(
        db_engine,
        workflow_id,
        status="in_progress",
        current_module_id="copy",
        attempts=2,
    )
    _asset(
        db_engine,
        workflow_id,
        "asset-missing-module",
        status="in_progress",
        current_module_id="removed",
    )
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        return_asset(connection, "asset-1", graph)
        return_asset(connection, "asset-missing-module", graph)
    asset = _asset_row(db_engine)
    assert (asset["current_module_id"], asset["step_status"], asset["attempts"]) == (
        "copy", "waiting", 2
    )
    assert _asset_row(db_engine, "asset-missing-module")["status"] == "failed"


def test_enter_asset_from_watchfolder(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("copy", "copy_file")],
        [_connection("watch", "success", "copy")],
    )
    _asset(db_engine, workflow_id, current_module_id="watch")
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        enter_asset(connection, "asset-1", graph)
    asset = _asset_row(db_engine)
    assert (asset["current_module_id"], asset["status"], asset["step_status"]) == (
        "copy", "in_progress", "waiting"
    )
    assert asset["from_module_id"] == "watch"


def test_enter_null_single_source(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder"), _module("copy", "copy_file")],
        [_connection("watch", "success", "copy")],
    )
    _asset(db_engine, workflow_id)
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        enter_asset(connection, "asset-1", graph)
    assert _asset_row(db_engine)["current_module_id"] == "copy"


def test_enter_null_without_source_single_start(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file")])
    _asset(db_engine, workflow_id, from_module_id="stale", from_output_id="success")
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        enter_asset(connection, "asset-1", graph)
    asset = _asset_row(db_engine)
    assert (asset["current_module_id"], asset["step_status"], asset["from_module_id"]) == (
        "copy", "waiting", None
    )


@pytest.mark.parametrize(
    "modules",
    [
        [_module("watch-a", "watchfolder"), _module("watch-b", "watchfolder")],
        [_module("copy-a", "copy_file"), _module("copy-b", "copy_file")],
    ],
)
def test_enter_ambiguous_start(db_engine, modules):
    workflow_id = _workflow(db_engine, modules)
    _asset(db_engine, workflow_id)
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        enter_asset(connection, "asset-1", graph)
    assert _asset_row(db_engine)["status"] == "failed"
    assert _history(db_engine) == [{"kind": "error", "text": "Startpunkt nicht eindeutig"}]


def test_enter_module_missing(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file")])
    _asset(db_engine, workflow_id, current_module_id="removed")
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        enter_asset(connection, "asset-1", graph)
    assert _asset_row(db_engine)["status"] == "failed"
    assert _history(db_engine) == [{"kind": "error", "text": "Modul nicht mehr im Workflow"}]


def test_enter_ignores_data_connection(db_engine):
    workflow_id = _workflow(
        db_engine,
        [_module("priority", "priolist"), _module("copy", "copy_file")],
        [_connection("priority", "list", "copy")],
    )
    _asset(db_engine, workflow_id)
    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        enter_asset(connection, "asset-1", graph)
    asset = _asset_row(db_engine)
    assert (asset["current_module_id"], asset["step_status"]) == ("copy", "waiting")


def test_fail_asset_writes_history_and_log(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file")])
    _asset(db_engine, workflow_id, current_module_id="copy", step_status="running")
    with db_engine.begin() as connection:
        fail_asset(connection, "asset-1", "Fehler aufgetreten")
    asset = _asset_row(db_engine)
    assert (asset["status"], asset["step_status"], asset["wait_until"]) == ("failed", None, None)
    assert _history(db_engine) == [{"kind": "error", "text": "Fehler aufgetreten"}]
    assert _logs(db_engine, workflow_id) == [{
        "workflow_id": workflow_id,
        "module_id": "copy",
        "job_id": None,
        "level": "error",
        "text": "Fehler aufgetreten",
    }]



def test_fail_asset_log_records_asset_id(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file")])
    _asset(db_engine, workflow_id, "logged-failure", status="in_progress", current_module_id="copy", step_status="waiting")
    with db_engine.begin() as connection:
        fail_asset(connection, "logged-failure", "Testfehler")
    with db_engine.connect() as connection:
        log = connection.execute(
            text("SELECT asset_id, text FROM log_lines WHERE workflow_id = :workflow_id ORDER BY id DESC LIMIT 1"),
            {"workflow_id": workflow_id},
        ).mappings().one()
    assert (log["asset_id"], log["text"]) == ("logged-failure", "Testfehler")
