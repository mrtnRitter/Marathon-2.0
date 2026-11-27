"""Read-only queries for workflow, module, and asset detail views."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from marathon.server.auth.sessions import AuthUser
from marathon.server.db.macro_writes import load_definitions, load_macros, module_type_map
from marathon.server.db.object_access import access_levels, access_filter_sql, object_access_map
from marathon.server.db.queries import blind_message_text, blind_module_name, list_assets, list_workers
from marathon.server.macros.definition import macro_hull


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

    module_access = object_access_map(
        connection, user, "macro", (instance["macro_id"] for instance in instances)
    )
    module_list = []
    for instance in instances:
        macro_id = instance["macro_id"]
        macro = macro_rows[macro_id]
        macro_version = instance["macro_version"]
        counters = counters_by_instance.get(instance["id"], {})
        access = module_access.get(macro_id)
        hull = macro_hull(
            macro_id,
            macro_version,
            definitions[(macro_id, macro_version)],
            module_types,
        )
        blind = access is None
        if blind:
            hull = {
                "id": "",
                "version": macro_version,
                "name": "",
                "category": "",
                "icon": "",
                "description": "",
                "sort_order": 0,
                "has_input": hull["has_input"],
                "outputs": [
                    {
                        "id": output["id"],
                        "label": output["label"],
                        "description": "",
                        **({"data": True} if output.get("data") is True else {}),
                    }
                    for output in hull["outputs"]
                ],
                "settings": [],
            }
        module_list.append({
            "id": instance["id"],
            "type_id": "" if blind else macro_id,
            "macro_version": macro_version,
            "latest_version": macro["current_version"],
            "update_available": (
                macro["current_version"] > macro_version and macro["archived_at"] is None
            ),
            "access": access,
            "blind": blind,
            "hull": hull,
            "name": "" if blind else instance["name"],
            "description": "" if blind else instance["description"],
            "position": {"x": instance["position_x"], "y": instance["position_y"]},
            "settings": {} if blind else instance["settings"],
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
        "SELECT workflow.current_version AS version, instance.macro_id "
        "FROM workflows AS workflow "
        "JOIN workflow_instances AS instance ON instance.workflow_id = workflow.id "
        "AND instance.version = workflow.current_version "
        "WHERE workflow.id = :workflow_id AND instance.id = :module_id"
    ), {"workflow_id": workflow_id, "module_id": module_id}).mappings().one_or_none()
    if instance is None:
        return None

    module_access = object_access_map(connection, user, "macro", [instance["macro_id"]])
    access = module_access.get(instance["macro_id"])
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
        if access not in ("edit", "owner"):
            job["module_id"] = module_id
    workers = [worker for worker in list_workers(connection) if worker["id"] in worker_ids]
    logs = []
    if access is not None:
        rows = connection.execute(text(
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
        logs = [dict(row) for row in rows]
    return {
        "jobs": jobs,
        "assets": assets,
        "workers": workers,
        "logs": logs,
        "logs_hidden": access is None,
    }


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



def _macro_output_label(exits: Any, output_id: str | None, definition: Any) -> str | None:
    """Resolve a runtime output through its instance definition."""
    if output_id is None or not isinstance(exits, dict):
        return None
    macro_output_id = exits.get(output_id)
    if macro_output_id is None:
        return None
    for output in definition.get("outputs", []):
        if output.get("id") == macro_output_id:
            return output.get("label")
    return None


def get_asset_detail(
    connection: Connection,
    asset_id: str,
    now: datetime | None = None,
    user: AuthUser | None = None,
) -> dict[str, Any] | None:
    """Return an accessible asset with its timeline, messages, and logs, or None."""
    access_filter, parameters = access_filter_sql(user, "workflow", "workflow")
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

    timeline_rows = list(connection.execute(text(
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
    ), {"asset_id": asset_id}).mappings())
    messages = list(connection.execute(text(
        "SELECT ts, kind, text, module_id FROM asset_history "
        "WHERE asset_id = :asset_id ORDER BY ts DESC, id DESC"
    ), {"asset_id": asset_id}).mappings())
    log_rows = list(connection.execute(text(
        "SELECT log.ts, log.level, log.text, job.module_id "
        "FROM log_lines AS log "
        "JOIN job_assets AS job_asset ON job_asset.job_id = log.job_id "
        "LEFT JOIN jobs AS job ON job.id = log.job_id "
        "WHERE job_asset.asset_id = :asset_id ORDER BY log.ts, log.id"
    ), {"asset_id": asset_id}).mappings())

    runtime_ids = list(dict.fromkeys(
        module_id for module_id in (
            detail["current_module_id"],
            *(row["module_id"] for row in timeline_rows),
            *(row["module_id"] for row in messages),
        ) if module_id is not None
    ))
    if runtime_ids:
        module_rows = list(connection.execute(text(
            "SELECT runtime.id AS runtime_id, runtime.version, runtime.exits, "
            "instance.macro_id, instance.macro_version "
            "FROM workflow_modules AS runtime "
            "JOIN workflow_instances AS instance ON instance.workflow_id = runtime.workflow_id "
            "AND instance.version = runtime.version AND instance.id = runtime.instance_id "
            "WHERE runtime.workflow_id = :workflow_id AND runtime.instance_id IS NOT NULL "
            "AND runtime.id IN :runtime_ids ORDER BY runtime.version DESC"
        ).bindparams(bindparam("runtime_ids", expanding=True)), {
            "workflow_id": detail["workflow_id"], "runtime_ids": runtime_ids,
        }).mappings())
    else:
        module_rows = []

    macro_ids = list(dict.fromkeys(row["macro_id"] for row in module_rows))
    module_access = object_access_map(connection, user, "macro", macro_ids)
    rows_by_runtime: dict[str, list[Any]] = {}
    latest_by_runtime: dict[str, Any] = {}
    for row in module_rows:
        rows_by_runtime.setdefault(row["runtime_id"], []).append(row)
        latest_by_runtime.setdefault(row["runtime_id"], row)

    runtime_access: dict[str, str | None] = {}
    for runtime_id, rows in rows_by_runtime.items():
        levels = [module_access.get(row["macro_id"]) for row in rows]
        runtime_access[runtime_id] = min(
            levels, key=lambda level: access_levels.index(level) if level in access_levels else -1
        )
    definitions_needed = [
        (row["macro_id"], row["macro_version"])
        for runtime_id, row in latest_by_runtime.items()
        if runtime_access[runtime_id] not in ("edit", "owner")
    ]
    definitions = load_definitions(connection, definitions_needed)

    if detail["current_module_id"] in runtime_access and runtime_access[detail["current_module_id"]] is None:
        detail["current_module"] = blind_module_name

    timeline = []
    for row in timeline_rows:
        module_id = row["module_id"]
        access = runtime_access.get(module_id)
        started_at = row["started_at"]
        duration = _duration_seconds(row["status"], started_at, row["finished_at"], current_time)
        module_name = blind_module_name if module_id in runtime_access and access is None else row["module_name"]
        if module_id in runtime_access and access not in ("edit", "owner"):
            macro_instance = latest_by_runtime[module_id]
            output_label = _macro_output_label(
                macro_instance["exits"], row["output_id"],
                definitions[(macro_instance["macro_id"], macro_instance["macro_version"])],
            )
        else:
            output_label = _output_label(row["outputs"], row["output_id"])
        timeline.append({
            "module_id": module_id,
            "module_name": module_name,
            "job_id": row["job_id"],
            "status": row["status"],
            "attempts": row["attempt"],
            "worker": row["worker"] or "–",
            "started_at": started_at or row["created_at"],
            "duration_s": duration,
            "output_label": output_label,
        })

    visible_logs = [
        {key: row[key] for key in ("ts", "level", "text")}
        for row in log_rows
        if row["module_id"] not in runtime_access or runtime_access[row["module_id"]] is not None
    ]
    visible_messages = [
        {
            "ts": row["ts"],
            "kind": row["kind"],
            "text": (
                blind_message_text
                if row["module_id"] in runtime_access and runtime_access[row["module_id"]] is None
                else row["text"]
            ),
        }
        for row in messages
    ]
    detail["timeline"] = timeline
    detail["messages"] = visible_messages
    detail["log"] = visible_logs
    return detail
