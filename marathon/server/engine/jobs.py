"""Create, coordinate, and finish worker jobs for workflow assets."""

# IMPORTS
from collections.abc import Iterable, Mapping, Sequence
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
        text("SELECT id, active, pausing FROM workflows WHERE id = :id FOR UPDATE"),
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
            "SELECT id, workflow_id, module_id, status, worker_id, attempt, output_id, error, type_id "
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
    job_id: str | None,
    level: str,
    message: str,
    asset_id: str | None = None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, asset_id, level, text) "
            "VALUES (:workflow_id, :module_id, :job_id, :asset_id, :level, :text)"
        ),
        {
            "workflow_id": workflow_id,
            "module_id": module_id,
            "job_id": job_id,
            "asset_id": asset_id,
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


def _cancel_locked_job(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    asset_ids: list[str],
    assets: dict[str, dict[str, Any]],
    reason: str,
    now: datetime,
) -> None:
    if job["status"] not in active_job_statuses:
        return
    if job["status"] == "assigned" or job["worker_id"] is None:
        job_assets = {row["asset_id"]: row for row in _job_asset_rows(connection, job["id"])}
        for asset_id in asset_ids:
            job_asset = job_assets.get(asset_id)
            if job_asset is None or job_asset["outcome"] is not None:
                continue
            _finish_job_asset(connection, job["id"], asset_id, "cancelled", now, error=None)
            asset = assets.get(asset_id)
            if asset is not None and _asset_matches_job(asset, job):
                return_asset(connection, asset_id, graph)
        _finish_job(connection, {**job, "error": reason}, now, force_cancel=True)
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
    _write_job(connection, job["id"], {"error": reason})
    for row in _job_asset_rows(connection, job["id"]):
        asset_id = row["asset_id"]
        if row["outcome"] is not None:
            continue
        asset = assets.get(asset_id)
        _finish_job_asset(connection, job["id"], asset_id, "interrupted", now, error=reason)
        if asset is None or not _asset_matches_job(asset, job):
            _log(connection, job["workflow_id"], job["module_id"], job["id"], "warn", "Asset passt nicht zum Job", asset_id)
            continue
        attempt = asset["attempts"] + 1
        message = f"Versuch {attempt} an „{_module_name(graph, job['module_id'])}“ unterbrochen: {reason}"
        connection.execute(
            text("UPDATE assets SET attempts = :attempt, updated_at = :now WHERE id = :id"),
            {"attempt": attempt, "now": now, "id": asset_id},
        )
        _history(connection, asset_id, "error", message)
        _log(connection, job["workflow_id"], job["module_id"], job["id"], "warn", message, asset_id)
        if attempt >= max_attempts:
            fail_asset(connection, asset_id, f"Nach {attempt} unterbrochenen Versuchen fehlgeschlagen")
        else:
            return_asset(connection, asset_id, graph)
    _finish_job(connection, {**job, "error": reason}, now)


def _apply_error(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    asset_id: str,
    error: str,
) -> None:
    module = graph.modules.get(job["module_id"]) if graph is not None else None
    _log(connection, job["workflow_id"], job["module_id"], job["id"], "error", error, asset_id)
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
        _log(connection, job["workflow_id"], job["module_id"], job["id"], level, message, asset["id"])
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


def _job_asset_rows(connection: Connection, job_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            "SELECT job_id, asset_id, outcome, output_id, error, finished_at FROM job_assets "
            "WHERE job_id = :job_id ORDER BY asset_id"
        ),
        {"job_id": job_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def _get_job_asset(connection: Connection, job_id: str, asset_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        text(
            "SELECT job_id, asset_id, outcome, output_id, error, finished_at FROM job_assets "
            "WHERE job_id = :job_id AND asset_id = :asset_id"
        ),
        {"job_id": job_id, "asset_id": asset_id},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _finish_job_asset(
    connection: Connection,
    job_id: str,
    asset_id: str,
    outcome: str,
    now: datetime,
    *,
    output: str | None = None,
    error: str | None = None,
) -> None:
    connection.execute(
        text(
            "UPDATE job_assets SET outcome = :outcome, output_id = :output, error = :error, finished_at = :now "
            "WHERE job_id = :job_id AND asset_id = :asset_id AND outcome IS NULL"
        ),
        {"job_id": job_id, "asset_id": asset_id, "outcome": outcome, "output": output, "error": error, "now": now},
    )


def _finish_job(
    connection: Connection,
    job: dict[str, Any],
    now: datetime,
    *,
    force_cancel: bool = False,
) -> bool:
    rows = _job_asset_rows(connection, job["id"])
    if not rows:
        return False
    finished = [row for row in rows if row["outcome"] is not None]
    progress = round(100 * len(finished) / len(rows))
    if len(finished) < len(rows):
        _write_job(connection, job["id"], {"progress": progress})
        return False

    outcomes = {row["outcome"] for row in rows}
    if (force_cancel or job["status"] == "cancelling") and "cancelled" in outcomes:
        status = "cancelled"
    elif "interrupted" in outcomes:
        status = "interrupted"
    elif "error" in outcomes:
        status = "failed"
    else:
        status = "succeeded"
    output_ids = [row["output_id"] for row in rows]
    output_id = output_ids[0] if all(value == output_ids[0] for value in output_ids) else None
    error_rows = sorted(
        (row for row in rows if row["outcome"] == "error" and row["error"] is not None),
        key=lambda row: (row["finished_at"] is None, row["finished_at"], row["asset_id"]),
    )
    interruption_rows = sorted(
        (row for row in rows if row["outcome"] == "interrupted" and row["error"] is not None),
        key=lambda row: (row["finished_at"] is None, row["finished_at"], row["asset_id"]),
    )
    fields: dict[str, Any] = {
        "status": status,
        "progress": 100,
        "output_id": output_id,
        "finished_at": now,
    }
    if status == "failed":
        fields["error"] = error_rows[0]["error"] if error_rows else "Unbekannter Fehler"
    elif status == "interrupted":
        fields["error"] = interruption_rows[0]["error"] if interruption_rows else job.get("error")
    elif status == "cancelled":
        fields["error"] = job.get("error")
    else:
        fields["error"] = None
    _write_job(connection, job["id"], fields)
    return True


def _output_label(connection: Connection, type_id: str, output_id: str | None) -> str:
    if output_id is None:
        return "Erledigt"
    row = connection.execute(
        text("SELECT outputs FROM module_types WHERE id = :id"), {"id": type_id}
    ).mappings().one_or_none()
    for output in (row["outputs"] or []) if row is not None else []:
        if output.get("id") == output_id:
            return str(output.get("label") or output_id)
    return output_id


def _result_label(connection: Connection, job: dict[str, Any], outcome: str, output: str | None) -> str:
    if outcome == "error":
        return "Fehler"
    if outcome in {"cancelled", "interrupted"}:
        return "Abgebrochen" if outcome == "cancelled" else "Worker beendet"
    return _output_label(connection, job["type_id"], output)


def _record_asset_result(
    connection: Connection,
    job: dict[str, Any],
    asset_id: str,
    outcome: str,
    now: datetime,
    *,
    output: str | None = None,
    error: str | None = None,
    display_outcome: str | None = None,
) -> None:
    _finish_job_asset(connection, job["id"], asset_id, outcome, now, output=output, error=error)
    label = _result_label(connection, job, display_outcome or outcome, output)
    level = "error" if outcome == "error" or output == "error" else "info"
    _log(
        connection,
        job["workflow_id"],
        job["module_id"],
        job["id"],
        level,
        f"Ergebnis: {label}",
        asset_id,
    )


def create_batch_job(
    connection: Connection,
    asset_ids: Sequence[str],
    graph: Graph,
    worker_id: str,
    now: datetime,
    pickup_timeout_s: float,
) -> str:
    """Assign waiting assets from one module to a worker as a single job."""
    if not asset_ids or len(set(asset_ids)) != len(asset_ids):
        raise ValueError("Mindestens ein unterschiedliches Asset erforderlich")
    workflow_id = graph.workflow_id
    if _lock_workflow(connection, workflow_id) is None:
        raise LookupError(f"Workflow nicht gefunden: {workflow_id}")

    for asset_id in sorted(asset_ids):
        active_jobs = connection.execute(
            text(
                "SELECT j.id FROM jobs AS j JOIN job_assets AS ja ON ja.job_id = j.id "
                "WHERE ja.asset_id = :asset_id AND j.status IN ('assigned', 'running', 'cancelling') "
                "ORDER BY j.id FOR UPDATE OF j"
            ),
            {"asset_id": asset_id},
        ).scalars().all()
        if active_jobs:
            raise ValueError(f"Asset hat bereits einen aktiven Job: {asset_id}")

    assets = _lock_assets(connection, asset_ids)
    if len(assets) != len(asset_ids) or any(asset["workflow_id"] != workflow_id for asset in assets.values()):
        raise LookupError("Asset nicht gefunden")
    module_ids = {asset["current_module_id"] for asset in assets.values()}
    if len(module_ids) != 1:
        raise ValueError("Alle Assets müssen am selben Modul warten")
    module_id = next(iter(module_ids))
    module = graph.modules.get(module_id)
    if module is None:
        raise ValueError("Modul nicht mehr im Workflow")
    if any(asset["status"] != "in_progress" or asset["step_status"] != "waiting" for asset in assets.values()):
        raise ValueError("Alle Assets müssen auf einen Job warten")

    worker = connection.execute(
        text("SELECT name FROM workers WHERE id = :id FOR UPDATE"), {"id": worker_id}
    ).mappings().one_or_none()
    if worker is None:
        raise LookupError(f"Worker nicht gefunden: {worker_id}")

    job_id = f"job-{uuid4().hex[:12]}"
    attempt = max(asset["attempts"] for asset in assets.values()) + 1
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
            "attempt": attempt,
            "type_id": module.type_id,
            "version": graph.version,
            "settings": module.settings,
            "pickup_deadline": now + timedelta(seconds=pickup_timeout_s),
            "now": now,
        },
    )
    for asset_id in asset_ids:
        connection.execute(
            text("INSERT INTO job_assets (job_id, asset_id) VALUES (:job_id, :asset_id)"),
            {"job_id": job_id, "asset_id": asset_id},
        )
        connection.execute(
            text("UPDATE assets SET step_status = 'running', updated_at = :now WHERE id = :id"),
            {"id": asset_id, "now": now},
        )
        _log(connection, workflow_id, module_id, job_id, "info", f"Zugeteilt an {worker['name']}", asset_id)
    return job_id


def create_job(
    connection: Connection,
    asset_id: str,
    graph: Graph,
    worker_id: str,
    now: datetime,
    pickup_timeout_s: float,
) -> str:
    """Assign one waiting asset through the batch-job implementation."""
    return create_batch_job(connection, [asset_id], graph, worker_id, now, pickup_timeout_s)


def create_engine_job(connection: Connection, asset_id: str, graph: Graph, now: datetime) -> str:
    """Start a wait-module job owned and completed by the engine."""
    workflow_id = graph.workflow_id
    if _lock_workflow(connection, workflow_id) is None:
        raise LookupError(f"Workflow nicht gefunden: {workflow_id}")
    active_jobs = connection.execute(
        text(
            "SELECT j.id FROM jobs AS j JOIN job_assets AS ja ON ja.job_id = j.id "
            "WHERE ja.asset_id = :asset_id AND j.status IN ('assigned', 'running', 'cancelling') "
            "ORDER BY j.id FOR UPDATE OF j"
        ),
        {"asset_id": asset_id},
    ).scalars().all()
    if active_jobs:
        raise ValueError(f"Asset hat bereits einen aktiven Job: {asset_id}")
    asset = _lock_assets(connection, [asset_id]).get(asset_id)
    if asset is None or asset["workflow_id"] != workflow_id:
        raise LookupError(f"Asset nicht gefunden: {asset_id}")
    if asset["status"] != "in_progress" or asset["step_status"] != "waiting":
        raise ValueError("Asset wartet nicht auf einen Engine-Job")
    module = graph.modules.get(asset["current_module_id"])
    if module is None or module.type_id != "wait":
        raise ValueError("Asset wartet nicht an einem Warten-Modul")

    wait_s = module.settings.get("wait_s", 60)
    wait_until = now + timedelta(seconds=wait_s)
    job_id = f"job-{uuid4().hex[:12]}"
    statement = text(
        "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, type_id, version, "
        "settings, started_at, created_at) VALUES (:id, :workflow_id, :module_id, 'running', NULL, "
        ":attempt, :type_id, :version, :settings, :now, :now)"
    ).bindparams(bindparam("settings", type_=JSONB))
    connection.execute(
        statement,
        {
            "id": job_id,
            "workflow_id": workflow_id,
            "module_id": module.id,
            "attempt": asset["attempts"] + 1,
            "type_id": module.type_id,
            "version": graph.version,
            "settings": module.settings,
            "now": now,
        },
    )
    connection.execute(
        text("INSERT INTO job_assets (job_id, asset_id) VALUES (:job_id, :asset_id)"),
        {"job_id": job_id, "asset_id": asset_id},
    )
    connection.execute(
        text("UPDATE assets SET step_status = 'running', wait_until = :wait_until, updated_at = :now WHERE id = :id"),
        {"id": asset_id, "wait_until": wait_until, "now": now},
    )
    _log(
        connection,
        workflow_id,
        module.id,
        job_id,
        "info",
        f"Wartezeit gestartet: {wait_s:g} s",
        asset_id,
    )
    return job_id


def start_job(connection: Connection, job_id: str, worker_id: str, now: datetime) -> bool:
    """Mark an assigned job as running when the reporting worker matches."""
    job, _graph = _prepare_job(connection, job_id)
    if job["status"] != "assigned" or job["worker_id"] != worker_id:
        return False
    _write_job(connection, job_id, {"status": "running", "started_at": now})
    return True


def _interrupt_result_asset(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    asset: dict[str, Any],
    reason: str,
    now: datetime,
    max_attempts: int,
) -> None:
    attempt = asset["attempts"] + 1
    message = f"Versuch {attempt} an „{_module_name(graph, job['module_id'])}“ unterbrochen: {reason}"
    connection.execute(
        text("UPDATE assets SET attempts = :attempt, updated_at = :now WHERE id = :id"),
        {"attempt": attempt, "now": now, "id": asset["id"]},
    )
    _history(connection, asset["id"], "error", message)
    _log(connection, job["workflow_id"], job["module_id"], job["id"], "warn", message, asset["id"])
    if attempt >= max_attempts:
        fail_asset(connection, asset["id"], f"Nach {attempt} unterbrochenen Versuchen fehlgeschlagen")
    else:
        return_asset(connection, asset["id"], graph)


def _apply_asset_result_locked(
    connection: Connection,
    job: dict[str, Any],
    graph: Any,
    job_asset: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    result: Mapping[str, Any],
    now: datetime,
    max_attempts: int,
) -> bool:
    asset_id = job_asset["asset_id"]
    if job_asset["outcome"] is not None:
        _log(connection, job["workflow_id"], job["module_id"], job["id"], "warn", "Spätes Ergebnis verworfen", asset_id)
        return False

    asset = assets.get(asset_id)
    if asset is None or not _asset_matches_job(asset, job):
        _log(connection, job["workflow_id"], job["module_id"], job["id"], "warn", "Asset passt nicht zum Job", asset_id)
        outcome = result.get("outcome")
        if outcome == "cancelled" and job["status"] != "cancelling":
            outcome, error = "interrupted", "Worker beendet"
        elif outcome == "error":
            outcome, error = "error", str(result.get("error") or "Unbekannter Fehler")
        elif outcome == "cancelled":
            outcome, error = "cancelled", None
        else:
            outcome, error = "result", None
        output = result.get("output") if outcome == "result" else None
        display_outcome = result.get("outcome") if result.get("outcome") == "cancelled" else outcome
        _record_asset_result(
            connection, job, asset_id, outcome, now, output=output, error=error, display_outcome=display_outcome
        )
        _finish_job(connection, job, now, force_cancel=job["status"] == "cancelling")
        return True

    raw_outcome = result.get("outcome")
    output = result.get("output")
    error: str | None = None
    if raw_outcome == "error":
        outcome = "error"
        error = str(result.get("error") or "Unbekannter Fehler")
    elif raw_outcome == "cancelled":
        if job["status"] == "cancelling":
            outcome = "cancelled"
        else:
            outcome, error = "interrupted", "Worker beendet"
    else:
        module = graph.modules.get(job["module_id"]) if graph is not None else None
        if module is not None and output is not None and (not isinstance(output, str) or output not in module.outputs):
            outcome, error = "error", f"Ungültiger Ausgang: {output}"
        else:
            outcome = "result"

    if outcome == "error":
        _apply_error(connection, job, graph, asset_id, error or "Unbekannter Fehler")
        output = None
    elif outcome == "cancelled":
        return_asset(connection, asset_id, graph)
        output = None
    elif outcome == "interrupted":
        _interrupt_result_asset(connection, job, graph, asset, error or "Worker beendet", now, max_attempts)
        output = None
    else:
        _apply_result_to_asset(connection, job, graph, asset, result, now)

    display_outcome = "cancelled" if raw_outcome == "cancelled" else outcome
    _record_asset_result(
        connection,
        job,
        asset_id,
        outcome,
        now,
        output=output if outcome == "result" else None,
        error=error,
        display_outcome=display_outcome,
    )
    _finish_job(connection, job, now, force_cancel=job["status"] == "cancelling" and outcome == "cancelled")
    return True


def apply_asset_result(
    connection: Connection,
    job_id: str,
    asset_id: str,
    worker_id: str | None,
    result: Mapping[str, Any],
    now: datetime,
    max_attempts: int,
) -> bool:
    """Apply one asset result and finalize the job when every asset is complete."""
    job, graph = _prepare_job(connection, job_id)
    job_asset = _get_job_asset(connection, job_id, asset_id)
    if job_asset is None:
        raise JobNotFound(f"Asset gehört nicht zum Job: {asset_id}")
    if job["worker_id"] != worker_id:
        raise JobWorkerMismatch(f"Job gehört einem anderen Worker: {job_id}")
    if job["status"] not in active_job_statuses:
        _log(connection, job["workflow_id"], job["module_id"], job_id, "warn", "Spätes Ergebnis verworfen", asset_id)
        return False
    if job_asset["outcome"] is not None:
        _log(connection, job["workflow_id"], job["module_id"], job_id, "warn", "Spätes Ergebnis verworfen", asset_id)
        return False
    assets = _lock_assets(connection, [asset_id])
    return _apply_asset_result_locked(connection, job, graph, job_asset, assets, result, now, max_attempts)


def apply_job_result(
    connection: Connection,
    job_id: str,
    worker_id: str | None,
    result: Mapping[str, Any],
    now: datetime,
    max_attempts: int,
) -> bool:
    """Apply one job-level result to each asset that has not yet completed."""
    job, graph = _prepare_job(connection, job_id)
    if job["status"] not in active_job_statuses:
        asset_ids = _job_asset_ids(connection, job_id)
        asset_id = asset_ids[0] if len(asset_ids) == 1 else None
        _log(connection, job["workflow_id"], job["module_id"], job_id, "warn", "Spätes Ergebnis verworfen", asset_id)
        return False
    if job["worker_id"] != worker_id:
        raise JobWorkerMismatch(f"Job gehört einem anderen Worker: {job_id}")
    job_assets = _job_asset_rows(connection, job_id)
    assets = _lock_assets(connection, [row["asset_id"] for row in job_assets])
    for job_asset in job_assets:
        if job_asset["outcome"] is None:
            _apply_asset_result_locked(connection, job, graph, job_asset, assets, result, now, max_attempts)
    _finish_job(connection, job, now, force_cancel=job["status"] == "cancelling")
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
    """Cancel an assigned job and restore every open asset to its waiting state."""
    job, _graph = _prepare_job(connection, job_id)
    if job["status"] != "assigned":
        return
    asset_ids = _job_asset_ids(connection, job_id)
    assets = _lock_assets(connection, asset_ids)
    for row in _job_asset_rows(connection, job_id):
        asset_id = row["asset_id"]
        if row["outcome"] is not None:
            continue
        _finish_job_asset(connection, job_id, asset_id, "cancelled", now, error="Nicht abgeholt")
        asset = assets.get(asset_id)
        if asset is not None and asset["current_module_id"] == job["module_id"]:
            connection.execute(
                text(
                    "UPDATE assets SET status = 'in_progress', step_status = 'waiting', wait_until = NULL, "
                    "updated_at = :now WHERE id = :id"
                ),
                {"id": asset_id, "now": now},
            )
    _finish_job(connection, {**job, "error": "Nicht abgeholt"}, now, force_cancel=True)


def request_pause(connection: Connection, workflow_id: str, now: datetime) -> bool:
    """Stop a workflow, cancel assignments, and ask running workers to stop."""
    workflow = _lock_workflow(connection, workflow_id)
    if workflow is None:
        return False
    graph = load_graph(connection, workflow_id)
    jobs = [dict(row) for row in connection.execute(
        text(
            "SELECT id, workflow_id, module_id, status, worker_id, attempt, output_id, error, type_id "
            "FROM jobs WHERE workflow_id = :id AND status IN ('assigned', 'running', 'cancelling') "
            "ORDER BY id FOR UPDATE"
        ),
        {"id": workflow_id},
    ).mappings()]
    job_asset_ids = {asset_id for job in jobs for asset_id in _job_asset_ids(connection, job["id"])}
    waiting_ids = list(connection.execute(
        text("SELECT id FROM assets WHERE workflow_id = :id AND wait_until IS NOT NULL ORDER BY id"),
        {"id": workflow_id},
    ).scalars())
    assets = _lock_assets(connection, [*job_asset_ids, *waiting_ids])
    if not workflow["active"] and not workflow["pausing"] and not jobs:
        return True

    connection.execute(
        text("UPDATE workflows SET active = false, schedule_waiting = false, run_started_at = NULL, pausing = true, updated_at = :now WHERE id = :id"),
        {"id": workflow_id, "now": now},
    )
    for job in jobs:
        _cancel_locked_job(
            connection,
            job,
            graph,
            _job_asset_ids(connection, job["id"]),
            assets,
            "Workflow gestoppt",
            now,
        )
    for asset_id in waiting_ids:
        if asset_id in assets and asset_id not in job_asset_ids:
            return_asset(connection, asset_id, graph)
    active = connection.execute(
        text("SELECT 1 FROM jobs WHERE workflow_id = :id AND status IN ('assigned', 'running', 'cancelling') LIMIT 1"),
        {"id": workflow_id},
    ).scalar_one_or_none()
    if active is None:
        connection.execute(text("UPDATE workflows SET pausing = false WHERE id = :id"), {"id": workflow_id})
    return True

def remove_missing_modules(connection: Connection, workflow_id: str, now: datetime) -> None:
    """Return waiting assets and cancel jobs whose modules left the saved graph."""
    if _lock_workflow(connection, workflow_id) is None:
        return
    graph = load_graph(connection, workflow_id)
    current_module_ids = set(graph.modules) if graph is not None else set()
    active_jobs = [dict(row) for row in connection.execute(
        text(
            "SELECT id, workflow_id, module_id, status, worker_id, attempt, output_id, error, type_id "
            "FROM jobs WHERE workflow_id = :id AND status IN ('assigned', 'running', 'cancelling') "
            "ORDER BY id FOR UPDATE"
        ),
        {"id": workflow_id},
    ).mappings()]
    missing_jobs = [job for job in active_jobs if job["module_id"] not in current_module_ids]
    asset_ids_by_job = {job["id"]: _job_asset_ids(connection, job["id"]) for job in missing_jobs}
    active_job_asset_ids = {
        asset_id for job in active_jobs for asset_id in _job_asset_ids(connection, job["id"])
    }
    candidates = list(connection.execute(
        text(
            "SELECT id FROM assets WHERE workflow_id = :id AND status = 'in_progress' "
            "AND current_module_id IS NOT NULL ORDER BY id"
        ),
        {"id": workflow_id},
    ).scalars())
    assets = _lock_assets(connection, candidates)
    for job in missing_jobs:
        _cancel_locked_job(
            connection,
            job,
            graph,
            asset_ids_by_job[job["id"]],
            assets,
            "Modul aus dem Workflow entfernt",
            now,
        )
    for asset_id, asset in assets.items():
        if asset["current_module_id"] not in current_module_ids and asset_id not in active_job_asset_ids:
            return_asset(connection, asset_id, graph)

def graph_impact(
    connection: Connection,
    workflow_id: str,
    module_ids: Iterable[str],
    connections: Iterable[tuple[str, str, str]] = (),
) -> dict[str, Any]:
    """Count removed-module work and predict each affected asset's outcome."""
    _lock_workflow(connection, workflow_id)
    graph = load_graph(connection, workflow_id)
    remaining = set(module_ids)
    routes = {(source_id, output_id) for source_id, output_id, target_id in connections if target_id in remaining}
    removed = []
    outcomes = {"rerouted": 0, "done": 0, "failed": 0}
    for module_id, module in sorted((graph.modules if graph is not None else {}).items()):
        if module_id in remaining:
            continue
        params = {"workflow_id": workflow_id, "module_id": module_id}
        assets = connection.execute(
            text(
                "SELECT from_module_id, from_output_id FROM assets WHERE workflow_id = :workflow_id "
                "AND current_module_id = :module_id AND status = 'in_progress'"
            ),
            params,
        ).mappings().all()
        job_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM jobs WHERE workflow_id = :workflow_id AND module_id = :module_id "
                "AND status IN ('assigned', 'running', 'cancelling')"
            ),
            params,
        ).scalar_one()
        asset_count = len(assets)
        for asset in assets:
            source_id = asset["from_module_id"]
            output_id = asset["from_output_id"]
            if source_id is None or source_id not in remaining:
                outcome = "failed"
            elif (source_id, output_id) in routes:
                outcome = "rerouted"
            elif output_id == "error":
                outcome = "failed"
            else:
                outcome = "done"
            outcomes[outcome] += 1
        job_count = int(job_count)
        if asset_count:
            removed.append({"id": module_id, "name": module.name, "assets": asset_count, "jobs": job_count})
    return {
        "removed_modules": removed,
        "assets": sum(item["assets"] for item in removed),
        "jobs": sum(item["jobs"] for item in removed),
        "outcomes": outcomes,
    }
