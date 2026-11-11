"""Read-only queries for workflow, module, and asset detail views."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.db.queries import list_assets, list_workers


def get_graph(connection: Connection, workflow_id: str) -> dict[str, Any] | None:
    """Return the current workflow graph with per-module counters, or None."""
    workflow = connection.execute(text(
        "SELECT id, current_version AS version FROM workflows WHERE id = :workflow_id"
    ), {"workflow_id": workflow_id}).mappings().one_or_none()
    if workflow is None:
        return None

    modules = connection.execute(text(
        "SELECT module.id, module.type_id, module.name, module.description, module.position_x, module.position_y, "
        "module.settings, module.locked, "
        "(SELECT COUNT(*) FROM assets AS asset WHERE asset.workflow_id = module.workflow_id "
        "AND asset.current_module_id = module.id AND asset.step_status = 'waiting') AS waiting, "
        "(SELECT COUNT(*) FROM assets AS asset WHERE asset.workflow_id = module.workflow_id "
        "AND asset.current_module_id = module.id AND asset.step_status = 'running') AS running, "
        "(SELECT COUNT(*) FROM assets AS asset WHERE asset.workflow_id = module.workflow_id "
        "AND asset.current_module_id = module.id AND asset.status = 'failed') AS failed, "
        "(SELECT COUNT(*) FROM jobs AS job WHERE job.workflow_id = module.workflow_id "
        "AND job.module_id = module.id AND job.status = 'succeeded') AS done "
        "FROM workflow_modules AS module WHERE module.workflow_id = :workflow_id "
        "AND module.version = :version ORDER BY module.id"
    ), {"workflow_id": workflow_id, "version": workflow["version"]}).mappings()
    module_list = []
    for row in modules:
        module = dict(row)
        module["position"] = {"x": module.pop("position_x"), "y": module.pop("position_y")}
        module["counters"] = {key: module.pop(key) for key in ("waiting", "running", "done", "failed")}
        module_list.append(module)

    connections = connection.execute(text(
        "SELECT source_module_id, source_output_id, target_module_id "
        "FROM workflow_connections WHERE workflow_id = :workflow_id AND version = :version "
        "ORDER BY source_module_id, source_output_id, target_module_id"
    ), {"workflow_id": workflow_id, "version": workflow["version"]}).mappings()
    connection_list = [
        {
            "id": f"{workflow_id}-{row['source_module_id']}-{row['source_output_id']}",
            "source_module_id": row["source_module_id"],
            "source_output_id": row["source_output_id"],
            "target_module_id": row["target_module_id"],
        }
        for row in connections
    ]
    return {
        "workflow_id": workflow["id"],
        "version": workflow["version"],
        "modules": module_list,
        "connections": connection_list,
    }


def get_module_activity(
    connection: Connection,
    workflow_id: str,
    module_id: str,
) -> dict[str, Any] | None:
    """Return current module activity, or None if its workflow/module is missing."""
    exists = connection.execute(text(
        "SELECT 1 FROM workflows AS workflow JOIN workflow_modules AS module "
        "ON module.workflow_id = workflow.id AND module.version = workflow.current_version "
        "WHERE workflow.id = :workflow_id AND module.id = :module_id"
    ), {"workflow_id": workflow_id, "module_id": module_id}).scalar_one_or_none()
    if exists is None:
        return None

    assets = [
        asset for asset in list_assets(connection, workflow_id=workflow_id)
        if asset["current_module_id"] == module_id
    ]
    jobs = []
    if assets:
        rows = connection.execute(text(
            "SELECT job.id, job.module_id, job.status, worker.name AS worker, worker.id AS worker_id, "
            "job.attempt, job.progress, array_agg(job_asset.asset_id ORDER BY job_asset.asset_id) AS asset_ids "
            "FROM jobs AS job JOIN job_assets AS job_asset ON job_asset.job_id = job.id "
            "JOIN assets AS asset ON asset.id = job_asset.asset_id "
            "LEFT JOIN workers AS worker ON worker.id = job.worker_id "
            "WHERE job.workflow_id = :workflow_id AND job.module_id = :module_id "
            "AND asset.workflow_id = :workflow_id AND asset.current_module_id = :module_id "
            "GROUP BY job.id, job.module_id, job.status, worker.name, worker.id, job.attempt, job.progress, job.created_at "
            "ORDER BY job.created_at, job.id"
        ), {"workflow_id": workflow_id, "module_id": module_id}).mappings()
        jobs = [dict(row) for row in rows]

    worker_ids = set()
    for job in jobs:
        worker_id = job.pop("worker_id")
        if worker_id is not None:
            worker_ids.add(worker_id)
    workers = [worker for worker in list_workers(connection) if worker["id"] in worker_ids]
    logs = connection.execute(text(
        "SELECT ts, level, text FROM log_lines WHERE workflow_id = :workflow_id "
        "AND module_id = :module_id AND job_id IS NULL ORDER BY ts, id"
    ), {"workflow_id": workflow_id, "module_id": module_id}).mappings()
    return {"jobs": jobs, "assets": assets, "workers": workers, "logs": [dict(row) for row in logs]}


def _duration_seconds(
    status: str, started_at: datetime | None, finished_at: datetime | None, now: datetime
) -> int:
    """Return a non-negative whole-second duration for a started job."""
    if status == "open" or started_at is None:
        return 0
    if finished_at is not None:
        end = finished_at
    elif status == "assigned":
        end = now
    else:
        return 0
    return max(0, int((end - started_at).total_seconds()))


def _output_label(outputs: Any, output_id: str | None) -> str | None:
    """Resolve an output id to its label, retaining unknown ids as fallback."""
    if output_id is None:
        return None
    if isinstance(outputs, list):
        for output in outputs:
            if isinstance(output, dict) and output.get("id") == output_id:
                return output.get("label", output_id)
    return output_id


def get_asset_detail(connection: Connection, asset_id: str, now: datetime | None = None) -> dict[str, Any] | None:
    """Return an asset with its job timeline, messages, and logs, or None."""
    asset_row = connection.execute(text(
        "SELECT asset.id, asset.workflow_id, asset.name, asset.status, asset.current_module_id, "
        "module.name AS current_module, asset.step_status, asset.updated_at, asset.created_at, asset.metadata "
        "FROM assets AS asset JOIN workflows AS workflow ON workflow.id = asset.workflow_id "
        "LEFT JOIN workflow_modules AS module ON module.workflow_id = asset.workflow_id "
        "AND module.version = workflow.current_version AND module.id = asset.current_module_id "
        "WHERE asset.id = :asset_id"
    ), {"asset_id": asset_id}).mappings().one_or_none()
    if asset_row is None:
        return None
    detail = dict(asset_row)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    timeline_rows = connection.execute(text(
        "SELECT job.module_id, COALESCE(module.name, job.module_id) AS module_name, job.id AS job_id, "
        "job.status, job.attempt, job.worker_id, worker.name AS worker, job.output_id, module_type.outputs, "
        "job.created_at, job.started_at, job.finished_at "
        "FROM job_assets AS job_asset JOIN jobs AS job ON job.id = job_asset.job_id "
        "JOIN assets AS asset ON asset.id = job_asset.asset_id "
        "JOIN workflows AS workflow ON workflow.id = asset.workflow_id "
        "LEFT JOIN workers AS worker ON worker.id = job.worker_id "
        "LEFT JOIN workflow_modules AS module ON module.workflow_id = job.workflow_id "
        "AND module.version = workflow.current_version AND module.id = job.module_id "
        "LEFT JOIN module_types AS module_type ON module_type.id = module.type_id "
        "WHERE job_asset.asset_id = :asset_id ORDER BY job.created_at, job.id"
    ), {"asset_id": asset_id}).mappings()
    timeline = []
    for row in timeline_rows:
        started_at = row["started_at"]
        duration = _duration_seconds(row["status"], started_at, row["finished_at"], current_time)
        timeline.append({
            "module_id": row["module_id"],
            "module_name": row["module_name"],
            "job_id": row["job_id"],
            "status": row["status"],
            "attempts": row["attempt"],
            "worker": row["worker"] or "–",
            "started_at": started_at or row["created_at"],
            "duration_s": duration,
            "output_label": _output_label(row["outputs"], row["output_id"]),
        })

    messages = connection.execute(text(
        "SELECT ts, kind, text FROM asset_history WHERE asset_id = :asset_id ORDER BY ts DESC, id DESC"
    ), {"asset_id": asset_id}).mappings()
    logs = connection.execute(text(
        "SELECT log.ts, log.level, log.text FROM log_lines AS log "
        "JOIN job_assets AS job_asset ON job_asset.job_id = log.job_id "
        "WHERE job_asset.asset_id = :asset_id ORDER BY log.ts, log.id"
    ), {"asset_id": asset_id}).mappings()
    detail["timeline"] = timeline
    detail["messages"] = [dict(row) for row in messages]
    detail["log"] = [dict(row) for row in logs]
    return detail
