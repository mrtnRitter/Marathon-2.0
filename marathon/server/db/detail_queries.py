"""Read-only queries for workflow, module, and asset detail views."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from marathon.server.auth.sessions import AuthUser
from marathon.server.db.macro_writes import can_edit_macro, load_definitions, load_macros, module_type_map
from marathon.server.db.queries import list_assets, list_workers
from marathon.server.macros.definition import macro_hull
from marathon.server.db.workflow_access import workflow_access_filter


def get_graph(
    connection: Connection,
    workflow_id: str,
    user: AuthUser | None = None,
) -> dict[str, Any] | None:
    """Return the current workflow graph at module-instance level, or None."""
    workflow = connection.execute(text(
        "SELECT id, current_version AS version FROM workflows WHERE id = :workflow_id"
    ), {"workflow_id": workflow_id}).mappings().one_or_none()
    if workflow is None:
        return None

    version = workflow["version"]
    instance_rows = connection.execute(text(
        "SELECT id, macro_id, macro_version, name, description, position_x, position_y, settings, locked "
        "FROM workflow_instances WHERE workflow_id = :workflow_id AND version = :version ORDER BY id"
    ), {"workflow_id": workflow_id, "version": version}).mappings().all()
    instances = [dict(row) for row in instance_rows]
    macro_rows = load_macros(connection, (instance["macro_id"] for instance in instances))
    definitions = load_definitions(
        connection, ((instance["macro_id"], instance["macro_version"]) for instance in instances)
    )
    module_types = module_type_map(connection)

    counter_rows = connection.execute(text("""
        SELECT runtime.instance_id,
               COALESCE(SUM((SELECT COUNT(*) FROM assets AS asset
                    WHERE asset.workflow_id = runtime.workflow_id
                      AND asset.current_module_id = runtime.id AND asset.step_status = 'waiting')), 0)::integer AS waiting,
               COALESCE(SUM((SELECT COUNT(*) FROM assets AS asset
                    WHERE asset.workflow_id = runtime.workflow_id
                      AND asset.current_module_id = runtime.id AND asset.step_status = 'running')), 0)::integer AS running,
               COALESCE(SUM((SELECT COUNT(*) FROM assets AS asset
                    WHERE asset.workflow_id = runtime.workflow_id
                      AND asset.current_module_id = runtime.id AND asset.status = 'failed')), 0)::integer AS failed,
               COALESCE(SUM((SELECT COUNT(*) FROM watch_files AS watch_file
                    WHERE watch_file.workflow_id = runtime.workflow_id
                      AND watch_file.module_id = runtime.id AND watch_file.asset_id IS NOT NULL)), 0)::integer AS discovered
        FROM workflow_modules AS runtime
        WHERE runtime.workflow_id = :workflow_id AND runtime.version = :version
        GROUP BY runtime.instance_id
    """), {"workflow_id": workflow_id, "version": version}).mappings()
    counters_by_instance = {
        row["instance_id"]: {key: row[key] or 0 for key in ("waiting", "running", "failed", "discovered")}
        for row in counter_rows
    }
    done_rows = connection.execute(text("""
        SELECT runtime.instance_id, COUNT(*) AS done
        FROM workflow_modules AS runtime
        JOIN jobs AS job ON job.workflow_id = runtime.workflow_id AND job.module_id = runtime.id
        JOIN job_assets AS job_asset ON job_asset.job_id = job.id
        WHERE runtime.workflow_id = :workflow_id AND runtime.version = :version
          AND job_asset.outcome = 'result'
          AND (
              job_asset.output_id IS NULL
              OR (jsonb_exists(runtime.exits, job_asset.output_id)
                  AND runtime.exits ->> job_asset.output_id <> 'error')
              OR (NOT jsonb_exists(runtime.exits, job_asset.output_id)
                  AND job_asset.output_id <> 'error'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM workflow_connections AS internal_connection
                      JOIN workflow_modules AS target_module
                        ON target_module.workflow_id = internal_connection.workflow_id
                       AND target_module.version = internal_connection.version
                       AND target_module.id = internal_connection.target_module_id
                      WHERE internal_connection.workflow_id = runtime.workflow_id
                        AND internal_connection.version = runtime.version
                        AND internal_connection.source_module_id = runtime.id
                        AND internal_connection.source_output_id = job_asset.output_id
                        AND target_module.instance_id = runtime.instance_id
                  ))
          )
        GROUP BY runtime.instance_id
    """), {"workflow_id": workflow_id, "version": version}).mappings()
    for row in done_rows:
        counters_by_instance.setdefault(row["instance_id"], {})["done"] = row["done"]

    module_list = []
    for instance in instances:
        macro_id = instance["macro_id"]
        macro = macro_rows[macro_id]
        macro_version = instance["macro_version"]
        counters = counters_by_instance.get(instance["id"], {})
        module_list.append({
            "id": instance["id"],
            "type_id": macro_id,
            "macro_version": macro_version,
            "latest_version": macro["current_version"],
            "update_available": (
                macro["current_version"] > macro_version and macro["archived_at"] is None
            ),
            "can_edit": user is not None and can_edit_macro(macro["owner_user_id"], user),
            "hull": macro_hull(
                macro_id,
                macro_version,
                definitions[(macro_id, macro_version)],
                module_types,
            ),
            "name": instance["name"],
            "description": instance["description"],
            "position": {"x": instance["position_x"], "y": instance["position_y"]},
            "settings": instance["settings"],
            "locked": instance["locked"],
            "counters": {
                "waiting": counters.get("waiting", 0),
                "running": counters.get("running", 0),
                "done": counters.get("done", 0),
                "failed": counters.get("failed", 0),
                "discovered": counters.get("discovered", 0),
            },
        })

    connection_rows = connection.execute(text(
        "SELECT source_id, source_output_id, target_id FROM workflow_instance_connections "
        "WHERE workflow_id = :workflow_id AND version = :version "
        "ORDER BY source_id, source_output_id, target_id"
    ), {"workflow_id": workflow_id, "version": version}).mappings()
    connection_list = [{
        "id": f"{workflow_id}-{row['source_id']}-{row['source_output_id']}",
        "source_module_id": row["source_id"],
        "source_output_id": row["source_output_id"],
        "target_module_id": row["target_id"],
    } for row in connection_rows]
    return {
        "workflow_id": workflow["id"],
        "version": version,
        "modules": module_list,
        "connections": connection_list,
    }


def get_module_activity(
    connection: Connection,
    workflow_id: str,
    module_id: str,
    user: AuthUser | None = None,
) -> dict[str, Any] | None:
    """Return activity for a current module instance, or None when missing."""
    instance = connection.execute(text(
        "SELECT workflow.current_version AS version FROM workflows AS workflow "
        "JOIN workflow_instances AS instance ON instance.workflow_id = workflow.id "
        "AND instance.version = workflow.current_version "
        "WHERE workflow.id = :workflow_id AND instance.id = :module_id"
    ), {"workflow_id": workflow_id, "module_id": module_id}).mappings().one_or_none()
    if instance is None:
        return None

    runtime_ids = list(connection.execute(text(
        "SELECT id FROM workflow_modules WHERE workflow_id = :workflow_id "
        "AND version = :version AND instance_id = :module_id ORDER BY id"
    ), {"workflow_id": workflow_id, "version": instance["version"], "module_id": module_id}).scalars())
    runtime_id_set = set(runtime_ids)
    assets = [
        asset for asset in list_assets(connection, workflow_id=workflow_id, user=user)
        if asset["current_module_id"] in runtime_id_set
    ]
    jobs = []
    if assets and runtime_ids:
        rows = connection.execute(text(
            "SELECT job.id, job.module_id, job.status, worker.name AS worker, worker.id AS worker_id, "
            "job.attempt, job.progress, array_agg(job_asset.asset_id ORDER BY job_asset.asset_id) AS asset_ids "
            "FROM jobs AS job JOIN job_assets AS job_asset ON job_asset.job_id = job.id "
            "JOIN assets AS asset ON asset.id = job_asset.asset_id "
            "LEFT JOIN workers AS worker ON worker.id = job.worker_id "
            "WHERE job.workflow_id = :workflow_id AND job.module_id IN :runtime_ids "
            "AND asset.workflow_id = :workflow_id AND asset.current_module_id = job.module_id "
            "GROUP BY job.id, job.module_id, job.status, worker.name, worker.id, job.attempt, job.progress, job.created_at "
            "ORDER BY job.created_at, job.id"
        ).bindparams(bindparam("runtime_ids", expanding=True)), {
            "workflow_id": workflow_id, "runtime_ids": runtime_ids,
        }).mappings()
        jobs = [dict(row) for row in rows]

    worker_ids = set()
    for job in jobs:
        worker_id = job.pop("worker_id")
        if worker_id is not None:
            worker_ids.add(worker_id)
    workers = [worker for worker in list_workers(connection) if worker["id"] in worker_ids]
    logs = connection.execute(text(
        "SELECT ts, level, text, asset, worker FROM ("
        "SELECT log.id, log.ts, log.level, log.text, asset.name AS asset, worker.name AS worker "
        "FROM log_lines AS log LEFT JOIN assets AS asset ON asset.id = log.asset_id "
        "LEFT JOIN jobs AS job ON job.id = log.job_id "
        "LEFT JOIN workers AS worker ON worker.id = job.worker_id "
        "WHERE log.workflow_id = :workflow_id AND (log.module_id = :module_id OR log.module_id IN :runtime_ids) "
        "AND log.level IN ('warn', 'error') "
        "ORDER BY log.ts DESC, log.id DESC LIMIT 500"
        ") AS recent_logs ORDER BY ts, id"
    ).bindparams(bindparam("runtime_ids", expanding=True)), {
        "workflow_id": workflow_id, "module_id": module_id, "runtime_ids": runtime_ids,
    }).mappings()
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


def get_asset_detail(
    connection: Connection,
    asset_id: str,
    now: datetime | None = None,
    user: AuthUser | None = None,
) -> dict[str, Any] | None:
    """Return an accessible asset with its timeline, messages, and logs, or None."""
    access_filter, parameters = workflow_access_filter(user)
    parameters["asset_id"] = asset_id
    asset_row = connection.execute(text(
        "SELECT asset.id, asset.workflow_id, asset.name, asset.status, asset.current_module_id, "
        "module.name AS current_module, asset.step_status, asset.updated_at, asset.created_at, asset.metadata "
        "FROM assets AS asset JOIN workflows AS workflow ON workflow.id = asset.workflow_id "
        "LEFT JOIN workflow_modules AS module ON module.workflow_id = asset.workflow_id "
        "AND module.version = workflow.current_version AND module.id = asset.current_module_id "
        f"WHERE asset.id = :asset_id AND {access_filter}"
    ), parameters).mappings().one_or_none()
    if asset_row is None:
        return None
    detail = dict(asset_row)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    timeline_rows = connection.execute(text(
        "SELECT job.module_id, COALESCE(module.name, job.module_id) AS module_name, job.id AS job_id, "
        "job.status, job.attempt, job.worker_id, worker.name AS worker, job_asset.output_id, module_type.outputs, "
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
