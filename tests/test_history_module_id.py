"""Regression tests for module attribution in asset history."""

# IMPORTS
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.modules import load_catalog
from marathon.modules.watchfolder.scan import FileStat
from marathon.server.db.asset_writes import reprocess_assets
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.macro_writes import ensure_standard_macros
from marathon.server.db.watch_writes import WatchClaim, apply_scan
from marathon.server.db.workflow_writes import create_workflow, save_graph
from marathon.server.engine.flow import load_graph, route_asset
from marathon.server.engine.jobs import apply_asset_result, interrupt_job
from marathon.server.engine.tick import advance_assets


# CONFIG
now = datetime(2025, 3, 4, 12, 0, tzinfo=timezone.utc)


# FUNC
def _module(module_id: str, type_id: str, name: str | None = None, settings: dict | None = None) -> dict:
    return {
        "id": module_id,
        "type_id": type_id,
        "name": name or module_id,
        "description": "",
        "position": {"x": 0, "y": 0},
        "settings": settings or {},
        "locked": False,
    }


def _workflow(db_engine, modules: list[dict], connections: list[dict] | None = None) -> str:
    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
        ensure_standard_macros(connection)
        workflow_id = create_workflow(connection, f"History test {uuid4().hex[:8]}", "")
        connection.execute(text("UPDATE workflows SET active = true WHERE id = :id"), {"id": workflow_id})
        save_graph(connection, workflow_id, 1, modules, connections or [])
    return workflow_id


def _asset(
    db_engine,
    workflow_id: str,
    asset_id: str,
    *,
    module_id: str | None = "copy.main",
    status: str = "in_progress",
    step_status: str | None = "running",
    attempts: int = 0,
    from_module_id: str | None = None,
    wait_until: datetime | None = None,
    metadata: dict | None = None,
) -> None:
    statement = text(
        "INSERT INTO assets "
        "(id, workflow_id, name, status, current_module_id, step_status, attempts, from_module_id, wait_until, metadata) "
        "VALUES (:id, :workflow_id, :name, :status, :module_id, :step_status, :attempts, :from_module_id, :wait_until, :metadata)"
    ).bindparams(bindparam("metadata", type_=JSONB()))
    with db_engine.begin() as connection:
        connection.execute(
            statement,
            {
                "id": asset_id,
                "workflow_id": workflow_id,
                "name": f"{asset_id}.mov",
                "status": status,
                "module_id": module_id,
                "step_status": step_status,
                "attempts": attempts,
                "from_module_id": from_module_id,
                "wait_until": wait_until,
                "metadata": metadata or {},
            },
        )


def _job(db_engine, workflow_id: str, asset_id: str, *, module_id: str = "copy.main", type_id: str = "copy_file") -> str:
    job_id = f"job-{uuid4().hex[:12]}"
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, type_id, version, "
                "settings, created_at, error) VALUES (:id, :workflow_id, :module_id, 'running', NULL, 1, :type_id, 1, "
                "'{}'::jsonb, :created_at, NULL)"
            ),
            {
                "id": job_id,
                "workflow_id": workflow_id,
                "module_id": module_id,
                "type_id": type_id,
                "created_at": now,
            },
        )
        connection.execute(
            text("INSERT INTO job_assets (job_id, asset_id) VALUES (:job_id, :asset_id)"),
            {"job_id": job_id, "asset_id": asset_id},
        )
    return job_id


def _history(db_engine, asset_id: str) -> list[dict]:
    with db_engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text("SELECT module_id, kind, text FROM asset_history WHERE asset_id = :id ORDER BY id"),
                {"id": asset_id},
            ).mappings()
        ]


def test_result_logs_spawned_logs_and_completion_have_job_module(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, "parent")
    job_id = _job(db_engine, workflow_id, "parent")

    with db_engine.begin() as connection:
        assert apply_asset_result(
            connection,
            job_id,
            "parent",
            None,
            {
                "outcome": "result",
                "log": ["Ergebnisprotokoll"],
                "spawned": [{"path": "/out/child.mov", "output": "success", "log": ["Spawn-Protokoll"]}],
            },
            now,
            3,
        )

    parent = _history(db_engine, "parent")
    child_id = None
    with db_engine.connect() as connection:
        child_id = connection.execute(
            text("SELECT id FROM assets WHERE id <> 'parent' AND workflow_id = :workflow_id"),
            {"workflow_id": workflow_id},
        ).scalar_one()
    child = _history(db_engine, child_id)
    assert [(row["module_id"], row["text"]) for row in parent] == [
        ("copy.main", "Ergebnisprotokoll"),
        ("copy.main", "Erledigt an „Kopieren“"),
    ]
    assert [(row["module_id"], row["text"]) for row in child] == [
        ("copy.main", "Spawn-Protokoll"),
        ("copy.main", "Workflow abgeschlossen"),
    ]


def test_error_in_module_and_terminal_error_have_module_id(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, "error-asset")
    job_id = _job(db_engine, workflow_id, "error-asset")

    with db_engine.begin() as connection:
        apply_asset_result(
            connection, job_id, "error-asset", None,
            {"outcome": "error", "error": "Datei konnte nicht kopiert werden"}, now, 3,
        )

    assert [(row["module_id"], row["text"]) for row in _history(db_engine, "error-asset")] == [
        ("copy.main", "Datei konnte nicht kopiert werden"),
        ("copy.main", "Fehlgeschlagen an „Kopieren“"),
    ]


def test_job_interruption_history_has_job_module(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, "interrupted-asset")
    job_id = _job(db_engine, workflow_id, "interrupted-asset")

    with db_engine.begin() as connection:
        interrupt_job(connection, job_id, "Worker antwortet nicht", now, 3)

    row = _history(db_engine, "interrupted-asset")[0]
    assert row == {
        "module_id": "copy.main",
        "kind": "error",
        "text": "Versuch 1 an „Kopieren“ unterbrochen: Worker antwortet nicht",
    }


def test_interrupted_result_history_has_job_module(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, "result-interrupted")
    job_id = _job(db_engine, workflow_id, "result-interrupted")

    with db_engine.begin() as connection:
        apply_asset_result(
            connection, job_id, "result-interrupted", None,
            {"outcome": "cancelled"}, now, 3,
        )

    row = _history(db_engine, "result-interrupted")[0]
    assert row["module_id"] == "copy.main"
    assert row["text"] == "Versuch 1 an „Kopieren“ unterbrochen: Worker beendet"


def test_failure_after_attempts_has_module_id(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, "failed-after-attempts")
    job_id = _job(db_engine, workflow_id, "failed-after-attempts")

    with db_engine.begin() as connection:
        interrupt_job(connection, job_id, "Worker antwortet nicht", now, 1)

    rows = _history(db_engine, "failed-after-attempts")
    assert [row["module_id"] for row in rows] == ["copy.main", "copy.main"]
    assert rows[-1]["text"] == "Nach 1 unterbrochenen Versuchen fehlgeschlagen"


def test_missing_module_failure_uses_asset_current_module(db_engine):
    workflow_id = _workflow(db_engine, [_module("copy", "copy_file", "Kopieren")])
    _asset(db_engine, workflow_id, "missing-module", module_id="copy.main", step_status=None)

    with db_engine.begin() as connection:
        graph = load_graph(connection, workflow_id)
        route_asset(connection, "missing-module", graph, "removed.main", "success")

    row = _history(db_engine, "missing-module")[0]
    assert (row["module_id"], row["text"]) == ("copy.main", "Modul nicht mehr im Workflow")


def test_expired_wait_history_and_workflow_completion_use_wait_module(db_engine):
    workflow_id = _workflow(db_engine, [_module("wait", "wait", "Warten")])
    _asset(
        db_engine,
        workflow_id,
        "wait-expired",
        module_id="wait.main",
        wait_until=now - timedelta(seconds=1),
    )

    with db_engine.begin() as connection:
        assert advance_assets(connection, now) == 1

    assert [(row["module_id"], row["text"]) for row in _history(db_engine, "wait-expired")] == [
        ("wait.main", "Wartezeit abgelaufen"),
        ("wait.main", "Workflow abgeschlossen"),
    ]


def test_reprocess_history_without_module_context_is_null(db_engine):
    modules = [_module("copy", "copy_file", "Kopieren")]
    source_workflow = _workflow(db_engine, modules)
    target_workflow = _workflow(db_engine, modules)
    _asset(db_engine, source_workflow, "reprocess-same", module_id="copy.main", status="failed", step_status=None)
    _asset(db_engine, source_workflow, "reprocess-transfer", module_id="copy.main", status="failed", step_status=None)

    with db_engine.begin() as connection:
        assert reprocess_assets(connection, ["reprocess-same"], None) == ["reprocess-same"]
        new_asset_id = reprocess_assets(connection, ["reprocess-transfer"], target_workflow)[0]

    assert [row["module_id"] for row in _history(db_engine, "reprocess-same")] == [None]
    assert [row["module_id"] for row in _history(db_engine, "reprocess-transfer")] == [None]
    assert [row["module_id"] for row in _history(db_engine, new_asset_id)] == [None]


def test_watchfolder_discovery_history_uses_watch_module(db_engine):
    settings = {"path": "/mnt/in", "wait_s": 5}
    workflow_id = _workflow(
        db_engine,
        [_module("watch", "watchfolder", "Ingest", settings)],
    )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO watch_modules (workflow_id, module_id, next_scan_at) "
                "VALUES (:workflow_id, 'watch.main', :next_scan_at)"
            ),
            {"workflow_id": workflow_id, "next_scan_at": now},
        )

    claim = WatchClaim(workflow_id, "watch.main", "Ingest", settings, now)
    path = "/mnt/in/clip.mxf"
    listing = {path: FileStat(10, 1_700_000_000_123_000_000)}
    with db_engine.begin() as connection:
        assert apply_scan(connection, claim, listing, now) == []
        asset_ids = apply_scan(connection, claim, listing, now + timedelta(seconds=5))

    assert asset_ids and _history(db_engine, asset_ids[0]) == [{
        "module_id": "watch.main",
        "kind": "info",
        "text": f"Vom Watchfolder „Ingest“ übernommen: {path}",
    }]
