"""Create, coordinate, and finish worker jobs for workflow assets."""

# IMPORTS
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.engine import Connection

from marathon.server.engine.flow import (
    Graph,
    asset_file_path,
    fail_asset,
    load_graph,
    return_asset,
    route_asset,
)


# CONFIG
app_name = "Marathon workflow engine jobs"
app_version = "1.0.0"
active_job_statuses = ("assigned", "running", "cancelling")


# EXCEPTIONS
class JobNotFound(LookupError):
    """Raised when a requested job is not present."""


class JobWorkerMismatch(Exception):
    """Raised when a worker submits a result for another worker's job."""


# FUNC

def _lock_workflow(connection: Connection, workflow_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        text("SELECT id, active, scheduled_active, pausing FROM workflows WHERE id = :id FOR UPDATE"),
        {"id": workflow_id},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _prepare_job(connection: Connection, job_id: str) -> tuple[dict[str, Any], Any]:
    identity = connection.execute(
        text("SELECT workflow_id FROM jobs WHERE id = :id"), {"id": job_id}
    ).mappings().one_or_none()
    if identity is None:
        raise JobNotFound(f"Job nicht gefunden: {job_id}")

    workflow_id = identity["workflow_id"]
    if _lock_workflow(connection, workflow_id) is None:
        raise JobNotFound(f"Job nicht gefunden: {job_id}")
    graph = load_graph(connection, workflow_id)
    job = connection.execute(
        text(
            "SELECT id, workflow_id, module_id, status, worker_id, attempt, output_id, error "
            "FROM jobs WHERE id = :id FOR UPDATE"
        ),
        {"id": job_id},
    ).mappings().one_or_none()
    if job is None:
        raise JobNotFound(f"Job nicht gefunden: {job_id}")
    return dict(job), graph


def _job_asset_ids(connection: Connection, job_id: str) -> list[str]:
    return list(connection.execute(
        text("SELECT asset_id FROM job_assets WHERE job_id = :job_id ORDER BY asset_id"),
        {"job_id": job_id},
    ).scalars())


def _lock_assets(connection: Connection, asset_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    assets = {}
    for asset_id in sorted(set(asset_ids)):
        row = connection.execute(
            text(
                "SELECT id, workflow_id, status, current_module_id, step_status, from_module_id, "
                "from_output_id, attempts, wait_until, metadata, name FROM assets WHERE id = :id FOR UPDATE"
            ),
            {"id": asset_id},
        ).mappings().one_or_none()
        if row is not None:
            assets[asset_id] = dict(row)
    return assets


def _module_name(graph: Any, module_id: str) -> str:
    module = graph.modules.get(module_id) if graph is not None else None
    return module.name if module is not None else module_id


def _log(
    connection: Connection,
    workflow_id: str,
    module_id: str,
    job_id: str,
    level: str,
    message: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, level, text) "
            "VALUES (:workflow_id, :module_id, :job_id, :level, :text)"
        ),
        {
            "workflow_id": workflow_id,
            "module_id": module_id,
            "job_id": job_id,
            "level": level,
            "text": message,
        },
    )


def _history(connection: Connection, asset_id: str, kind: str, message: str) -> None:
    connection.execute(
        text("INSERT INTO asset_history (asset_id, kind, text) VALUES (:asset_id, :kind, :text)"),
        {"asset_id": asset_id, "kind": kind, "text": message},
    )


def _write_job(connection: Connection, job_id: str, fields: dict[str, Any]) -> None:
    assignments = ", ".join(f"{field} = :{field}" for field in fields)
    connection.execute(
        text(f"UPDATE jobs SET {assignments} WHERE id = :id"), {"id": job_id, **fields}
    )


def _asset_matches_job(asset: dict[str, Any], job: dict[str, Any]) -> bool:
    return (
        asset["status"] == "in_progress"
        and asset["step_status"] == "running"
        and asset["current_module_id"] == job["module_id"]
    )


def _return_assets(
    connection: Connection,
    asset_ids: Iterable[str],
    graph: Any,
    assets: dict[str, dict[str, Any]],
) -> None:
    for asset_id in asset_ids:
        if asset_id in assets:
            return_asset(connection, asset_id, graph)


def _cancel_locked_job(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    asset_ids: list[str],
    assets: dict[str, dict[str, Any]],
    reason: str,
    now: datetime,
) -> None:
    if job["status"] == "assigned":
        _write_job(connection, job["id"], {"status": "cancelled", "error": reason, "finished_at": now})
        _return_assets(connection, asset_ids, graph, assets)
    elif job["status"] == "running":
        _write_job(connection, job["id"], {"status": "cancelling", "error": reason})


def _interrupt_locked_job(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    assets: dict[str, dict[str, Any]],
    reason: str,
    now: datetime,
    max_attempts: int,
) -> None:
    if job["status"] not in active_job_statuses:
        return
    _write_job(
        connection,
        job["id"],
        {"status": "interrupted", "error": reason, "finished_at": now},
    )
    module_name = _module_name(graph, job["module_id"])
    for asset_id in _job_asset_ids(connection, job["id"]):
        asset = assets.get(asset_id)
        if asset is None or not _asset_matches_job(asset, job):
            continue
        attempt = asset["attempts"] + 1
        message = f"Versuch {attempt} an „{module_name}“ unterbrochen: {reason}"
        connection.execute(
            text("UPDATE assets SET attempts = :attempt, updated_at = :now WHERE id = :id"),
            {"attempt": attempt, "now": now, "id": asset_id},
        )
        _history(connection, asset_id, "error", message)
        _log(connection, job["workflow_id"], job["module_id"], job["id"], "warn", message)
        if attempt >= max_attempts:
            fail_asset(connection, asset_id, f"Nach {attempt} unterbrochenen Versuchen fehlgeschlagen")
        else:
            return_asset(connection, asset_id, graph)


def _apply_error(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    asset_id: str,
    error: str,
) -> None:
    module = graph.modules.get(job["module_id"]) if graph is not None else None
    _log(connection, job["workflow_id"], job["module_id"], job["id"], "error", error)
    if module is not None and "error" in module.outputs:
        _history(connection, asset_id, "error", error)
        route_asset(connection, asset_id, graph, job["module_id"], "error")
    else:
        fail_asset(connection, asset_id, error)


def _set_asset_path(connection: Connection, asset: dict[str, Any], path: str) -> None:
    metadata = asset["metadata"] or {}
    current_path = asset_file_path(metadata)
    if metadata.get("path") == path and current_path == path:
        return
    statement = text("UPDATE assets SET metadata = metadata || jsonb_build_object('path', :path) WHERE id = :id")
    connection.execute(statement.bindparams(bindparam("path", type_=Text)), {"id": asset["id"], "path": path})


def _spawn_asset(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    spawned: Mapping[str, Any],
    now: datetime,
) -> None:
    path = str(spawned["path"])
    name = PurePosixPath(path.replace("\\", "/")).name
    list_position = connection.execute(
        text("SELECT COALESCE(MAX(list_position), 0) + 1 FROM assets WHERE workflow_id = :workflow_id"),
        {"workflow_id": job["workflow_id"]},
    ).scalar_one()
    asset_id = f"as-{uuid4().hex[:12]}"
    statement = text(
        "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, "
        "metadata, list_position, created_at, updated_at) "
        "VALUES (:id, :workflow_id, :name, 'in_progress', :module_id, 'waiting', :metadata, "
        ":list_position, :now, :now)"
    ).bindparams(bindparam("metadata", type_=JSONB))
    connection.execute(
        statement,
        {
            "id": asset_id,
            "workflow_id": job["workflow_id"],
            "name": name,
            "module_id": job["module_id"],
            "metadata": {"path": path},
            "list_position": list_position,
            "now": now,
        },
    )
    for message in spawned.get("log", []):
        _history(connection, asset_id, "info", str(message))
    route_asset(connection, asset_id, graph, job["module_id"], spawned["output"])


def _apply_result_to_asset(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    asset: dict[str, Any],
    result: Mapping[str, Any],
    now: datetime,
) -> None:
    module = graph.modules.get(job["module_id"]) if graph is not None else None
    if module is None:
        fail_asset(connection, asset["id"], "Modul nicht mehr im Workflow")
        return

    output = result.get("output")
    if output is not None and (not isinstance(output, str) or output not in module.outputs):
        _apply_error(connection, job, graph, asset["id"], f"Ungültiger Ausgang: {output}")
        return

    level = "error" if output == "error" else "info"
    for message in result.get("log", []):
        message = str(message)
        _log(connection, job["workflow_id"], job["module_id"], job["id"], level, message)
        _history(connection, asset["id"], level, message)

    path = result.get("asset_path")
    if path is not None:
        _set_asset_path(connection, asset, str(path))

    for spawned in result.get("spawned", []):
        _spawn_asset(connection, job, graph, spawned, now)

    if output is None:
        connection.execute(
            text(
                "UPDATE assets SET status = 'done', step_status = NULL, wait_until = NULL, "
                "updated_at = :now WHERE id = :id"
            ),
            {"id": asset["id"], "now": now},
        )
        _history(connection, asset["id"], "success", f"Erledigt an „{module.name}“")
    else:
        route_asset(connection, asset["id"], graph, job["module_id"], output)


def _write_job_asset_output(connection: Connection, job_id: str, output: str | None) -> None:
    connection.execute(
        text("UPDATE job_assets SET output_id = :output WHERE job_id = :job_id"),
        {"job_id": job_id, "output": output},
    )


# JOB OPERATIONS

def create_job(
    connection: Connection,
    asset_id: str,
    graph: Graph,
    worker_id: str,
    now: datetime,
    pickup_timeout_s: float,
) -> str:
    """Assign one waiting asset to a worker and return the new job ID."""
    workflow_id = graph.workflow_id
    if _lock_workflow(connection, workflow_id) is None:
        raise LookupError(f"Workflow nicht gefunden: {workflow_id}")
    active_job = connection.execute(
        text(
            "SELECT j.id FROM jobs AS j JOIN job_assets AS ja ON ja.job_id = j.id "
            "WHERE ja.asset_id = :asset_id AND j.status IN ('assigned', 'running', 'cancelling') "
            "ORDER BY j.id FOR UPDATE OF j"
        ),
        {"asset_id": asset_id},
    ).scalar_one_or_none()
    if active_job is not None:
        raise ValueError(f"Asset hat bereits einen aktiven Job: {asset_id}")
    asset_row = connection.execute(
        text(
            "SELECT id, workflow_id, current_module_id, status, step_status, attempts, metadata "
            "FROM assets WHERE id = :id FOR UPDATE"
        ),
        {"id": asset_id},
    ).mappings().one_or_none()
    if asset_row is None or asset_row["workflow_id"] != workflow_id:
        raise LookupError(f"Asset nicht gefunden: {asset_id}")
    asset = dict(asset_row)
    module_id = asset["current_module_id"]
    module = graph.modules.get(module_id)
    if module is None:
        raise ValueError("Modul nicht mehr im Workflow")
    if asset["status"] != "in_progress" or asset["step_status"] != "waiting":
        raise ValueError("Asset wartet nicht auf einen Job")
    worker = connection.execute(
        text("SELECT name FROM workers WHERE id = :id FOR UPDATE"), {"id": worker_id}
    ).mappings().one_or_none()
    if worker is None:
        raise LookupError(f"Worker nicht gefunden: {worker_id}")

    job_id = f"job-{uuid4().hex[:12]}"
    statement = text(
        "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, type_id, version, "
        "settings, pickup_deadline, created_at) VALUES (:id, :workflow_id, :module_id, 'assigned', "
        ":worker_id, :attempt, :type_id, :version, :settings, :pickup_deadline, :now)"
    ).bindparams(bindparam("settings", type_=JSONB))
    connection.execute(
        statement,
        {
            "id": job_id,
            "workflow_id": workflow_id,
            "module_id": module_id,
            "worker_id": worker_id,
            "attempt": asset["attempts"] + 1,
            "type_id": module.type_id,
            "version": graph.version,
            "settings": module.settings,
            "pickup_deadline": now + timedelta(seconds=pickup_timeout_s),
            "now": now,
        },
    )
    connection.execute(
        text("INSERT INTO job_assets (job_id, asset_id) VALUES (:job_id, :asset_id)"),
        {"job_id": job_id, "asset_id": asset_id},
    )
    connection.execute(
        text("UPDATE assets SET step_status = 'running', updated_at = :now WHERE id = :id"),
        {"id": asset_id, "now": now},
    )
    _log(connection, workflow_id, module_id, job_id, "info", f"Zugeteilt an {worker['name']}")
    return job_id


def start_job(connection: Connection, job_id: str, worker_id: str, now: datetime) -> bool:
    """Mark an assigned job as running when the reporting worker matches."""
    job, _graph = _prepare_job(connection, job_id)
    if job["status"] != "assigned" or job["worker_id"] != worker_id:
        return False
    _write_job(connection, job_id, {"status": "running", "started_at": now})
    return True


def apply_job_result(
    connection: Connection,
    job_id: str,
    worker_id: str,
    result: Mapping[str, Any],
    now: datetime,
    max_attempts: int,
) -> bool:
    """Apply a worker result, discard stale results, and return whether it was accepted."""
    job, graph = _prepare_job(connection, job_id)
    if job["status"] not in active_job_statuses:
        _log(connection, job["workflow_id"], job["module_id"], job_id, "warn", "Spätes Ergebnis verworfen")
        return False
    if job["worker_id"] != worker_id:
        raise JobWorkerMismatch(f"Job gehört einem anderen Worker: {job_id}")

    asset_ids = _job_asset_ids(connection, job_id)
    assets = _lock_assets(connection, asset_ids)
    matching = [assets[asset_id] for asset_id in asset_ids if asset_id in assets and _asset_matches_job(assets[asset_id], job)]
    if not matching:
        _log(connection, job["workflow_id"], job["module_id"], job_id, "warn", "Asset passt nicht zum Job")
        outcome = result.get("outcome")
        if outcome == "error":
            _write_job(connection, job_id, {
                "status": "failed", "error": str(result.get("error") or "Unbekannter Fehler"), "finished_at": now
            })
        elif outcome == "cancelled":
            status = "cancelled" if job["status"] == "cancelling" else "interrupted"
            _write_job(connection, job_id, {
                "status": status,
                "error": job["error"] if status == "cancelled" else "Worker beendet",
                "finished_at": now,
            })
        else:
            _write_job(connection, job_id, {
                "status": "succeeded", "output_id": result.get("output"), "progress": 100, "finished_at": now
            })
        _write_job_asset_output(connection, job_id, result.get("output"))
        return True

    outcome = result.get("outcome")
    if outcome == "cancelled":
        if job["status"] == "cancelling":
            _write_job(connection, job_id, {"status": "cancelled", "finished_at": now})
            _return_assets(connection, asset_ids, graph, assets)
        else:
            _interrupt_locked_job(connection, job, graph, assets, "Worker beendet", now, max_attempts)
        return True

    if outcome == "error":
        error = str(result.get("error") or "Unbekannter Fehler")
        _write_job(connection, job_id, {"status": "failed", "error": error, "finished_at": now})
        for asset in matching:
            if graph is None or job["module_id"] not in graph.modules:
                fail_asset(connection, asset["id"], "Modul nicht mehr im Workflow")
            else:
                _apply_error(connection, job, graph, asset["id"], error)
        return True

    output = result.get("output")
    module = graph.modules.get(job["module_id"]) if graph is not None else None
    if module is not None and output is not None and (not isinstance(output, str) or output not in module.outputs):
        error = f"Ungültiger Ausgang: {output}"
        _write_job(connection, job_id, {"status": "failed", "error": error, "finished_at": now})
        for asset in matching:
            _apply_error(connection, job, graph, asset["id"], error)
        return True

    _write_job(
        connection,
        job_id,
        {"status": "succeeded", "progress": 100, "output_id": output, "finished_at": now},
    )
    _write_job_asset_output(connection, job_id, output)
    for asset in matching:
        _apply_result_to_asset(connection, job, graph, asset, result, now)
    return True


def interrupt_job(
    connection: Connection,
    job_id: str,
    reason: str,
    now: datetime,
    max_attempts: int,
) -> None:
    """Mark an active job interrupted and return or fail its assets by attempt count."""
    job, graph = _prepare_job(connection, job_id)
    assets = _lock_assets(connection, _job_asset_ids(connection, job_id))
    _interrupt_locked_job(connection, job, graph, assets, reason, now, max_attempts)


def cancel_job(connection: Connection, job_id: str, reason: str, now: datetime) -> None:
    """Cancel an assigned job or request cancellation of a running job."""
    job, graph = _prepare_job(connection, job_id)
    asset_ids = _job_asset_ids(connection, job_id)
    assets = _lock_assets(connection, asset_ids)
    _cancel_locked_job(connection, job, graph, asset_ids, assets, reason, now)


def drop_unstarted_job(connection: Connection, job_id: str, now: datetime) -> None:
    """Drop an assigned job that was not picked up and restore its waiting asset."""
    job, _graph = _prepare_job(connection, job_id)
    if job["status"] != "assigned":
        return
    _write_job(
        connection,
        job_id,
        {"status": "cancelled", "error": "Nicht abgeholt", "finished_at": now},
    )
    for asset_id in _job_asset_ids(connection, job_id):
        asset = _lock_assets(connection, [asset_id]).get(asset_id)
        if asset is not None and asset["current_module_id"] == job["module_id"]:
            connection.execute(
                text(
                    "UPDATE assets SET status = 'in_progress', step_status = 'waiting', wait_until = NULL, "
                    "updated_at = :now WHERE id = :id"
                ),
                {"id": asset_id, "now": now},
            )


def request_pause(connection: Connection, workflow_id: str, now: datetime) -> bool:
    """Pause a workflow, cancel assignments, and ask running workers to stop."""
    workflow = _lock_workflow(connection, workflow_id)
    if workflow is None:
        return False
    graph = load_graph(connection, workflow_id)
    jobs = [dict(row) for row in connection.execute(
        text(
            "SELECT id, workflow_id, module_id, status, worker_id, attempt, output_id, error "
            "FROM jobs WHERE workflow_id = :id AND status IN ('assigned', 'running', 'cancelling') "
            "ORDER BY id FOR UPDATE"
        ),
        {"id": workflow_id},
    ).mappings()]
    asset_ids = [asset_id for job in jobs for asset_id in _job_asset_ids(connection, job["id"])]
    waiting_ids = list(connection.execute(
        text("SELECT id FROM assets WHERE workflow_id = :id AND wait_until IS NOT NULL ORDER BY id"),
        {"id": workflow_id},
    ).scalars())
    assets = _lock_assets(connection, [*asset_ids, *waiting_ids])
    if not workflow["active"] and not workflow["pausing"] and not jobs:
        return True

    connection.execute(
        text("UPDATE workflows SET active = false, scheduled_active = false, pausing = true, updated_at = :now WHERE id = :id"),
        {"id": workflow_id, "now": now},
    )
    for job in jobs:
        _cancel_locked_job(
            connection,
            job,
            graph,
            _job_asset_ids(connection, job["id"]),
            assets,
            "Workflow pausiert",
            now,
        )
    for asset_id in waiting_ids:
        if asset_id in assets:
            return_asset(connection, asset_id, graph)
    active = connection.execute(
        text("SELECT 1 FROM jobs WHERE workflow_id = :id AND status IN ('assigned', 'running', 'cancelling') LIMIT 1"),
        {"id": workflow_id},
    ).scalar_one_or_none()
    if active is None:
        connection.execute(text("UPDATE workflows SET pausing = false WHERE id = :id"), {"id": workflow_id})
    return True


def remove_missing_modules(connection: Connection, workflow_id: str, now: datetime) -> None:
    """Return assets and cancel jobs whose current modules left the saved graph."""
    if _lock_workflow(connection, workflow_id) is None:
        return
    graph = load_graph(connection, workflow_id)
    current_module_ids = set(graph.modules) if graph is not None else set()
    active_jobs = [dict(row) for row in connection.execute(
        text(
            "SELECT id, workflow_id, module_id, status, worker_id, attempt, output_id, error "
            "FROM jobs WHERE workflow_id = :id AND status IN ('assigned', 'running', 'cancelling') "
            "ORDER BY id FOR UPDATE"
        ),
        {"id": workflow_id},
    ).mappings()]
    missing_jobs = {job["id"]: job for job in active_jobs if job["module_id"] not in current_module_ids}
    candidates = [dict(row) for row in connection.execute(
        text(
            "SELECT id FROM assets WHERE workflow_id = :id AND status = 'in_progress' "
            "AND current_module_id IS NOT NULL ORDER BY id"
        ),
        {"id": workflow_id},
    ).mappings()]
    candidate_ids = [row["id"] for row in candidates]
    assets = _lock_assets(connection, candidate_ids)
    by_asset: dict[str, dict[str, Any]] = {}
    for job in missing_jobs.values():
        for asset_id in _job_asset_ids(connection, job["id"]):
            by_asset[asset_id] = job
    for asset_id, asset in assets.items():
        if asset["current_module_id"] in current_module_ids:
            continue
        job = by_asset.get(asset_id)
        if job is not None:
            _cancel_locked_job(connection, job, graph, [asset_id], assets, "Modul aus dem Workflow entfernt", now)
        else:
            return_asset(connection, asset_id, graph)


def graph_impact(connection: Connection, workflow_id: str, module_ids: Iterable[str]) -> dict[str, Any]:
    """Count in-progress assets and active jobs on modules absent from a proposed graph."""
    _lock_workflow(connection, workflow_id)
    graph = load_graph(connection, workflow_id)
    remaining = set(module_ids)
    removed = []
    for module_id, module in sorted((graph.modules if graph is not None else {}).items()):
        if module_id in remaining:
            continue
        params = {"workflow_id": workflow_id, "module_id": module_id}
        asset_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM assets WHERE workflow_id = :workflow_id "
                "AND current_module_id = :module_id AND status = 'in_progress'"
            ),
            params,
        ).scalar_one()
        job_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM jobs WHERE workflow_id = :workflow_id AND module_id = :module_id "
                "AND status IN ('assigned', 'running', 'cancelling')"
            ),
            params,
        ).scalar_one()
        asset_count = int(asset_count)
        job_count = int(job_count)
        if asset_count:
            removed.append({"id": module_id, "name": module.name, "assets": asset_count, "jobs": job_count})
    return {
        "removed_modules": removed,
        "assets": sum(item["assets"] for item in removed),
        "jobs": sum(item["jobs"] for item in removed),
    }
