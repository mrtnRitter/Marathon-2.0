"""Read-only SQL queries for the Marathon API."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


_asset_statuses = ("pending", "in_progress", "done", "failed", "on_hold")


def _asset_counts(connection: Connection, workflow_id: str | None = None) -> dict[str, int]:
    """Return initialized status counts, optionally limited to one workflow."""
    counts = {status: 0 for status in _asset_statuses}
    parameters: dict[str, Any] = {}
    where = ""
    if workflow_id is not None:
        where = " WHERE workflow_id = :workflow_id"
        parameters["workflow_id"] = workflow_id
    rows = connection.execute(
        text(f"SELECT status, COUNT(*) AS count FROM assets{where} GROUP BY status"), parameters
    ).mappings()
    counts.update({row["status"]: row["count"] for row in rows})
    return counts


def list_workflows(connection: Connection) -> list[dict[str, Any]]:
    """List workflows by descending priority and name with counts for every asset status."""
    rows = connection.execute(text(
        "SELECT id, name, description, active, current_version AS version, priority "
        "FROM workflows ORDER BY priority DESC, name"
    )).mappings()
    workflows = [dict(row) for row in rows]
    counts_by_workflow = {
        workflow["id"]: _asset_counts(connection, workflow["id"]) for workflow in workflows
    }
    for workflow in workflows:
        workflow["asset_counts"] = counts_by_workflow[workflow["id"]]
    return workflows


def get_workflow(connection: Connection, workflow_id: str) -> dict[str, Any] | None:
    """Return one workflow and its asset counts, or None when it does not exist."""
    row = connection.execute(text(
        "SELECT id, name, description, active, current_version AS version, priority "
        "FROM workflows WHERE id = :workflow_id"
    ), {"workflow_id": workflow_id}).mappings().one_or_none()
    if row is None:
        return None
    workflow = dict(row)
    workflow["asset_counts"] = _asset_counts(connection, workflow_id)
    return workflow


def asset_totals(connection: Connection) -> dict[str, int]:
    """Return total asset counts for all five statuses, including zero counts."""
    return _asset_counts(connection)


def list_workers(connection: Connection) -> list[dict[str, Any]]:
    """List workers alphabetically with assigned-job counts and a non-null last-seen time."""
    rows = connection.execute(text(
        "SELECT worker.id, worker.name, worker.status, worker.capabilities, worker.max_parallel, "
        "(SELECT COUNT(*) FROM jobs WHERE jobs.status = 'assigned' "
        "AND jobs.worker_id = worker.id) AS running_jobs, worker.client_version, "
        "COALESCE(worker.last_seen, worker.created_at) AS last_seen, worker.ip_address "
        "FROM workers AS worker ORDER BY worker.name"
    )).mappings()
    return [dict(row) for row in rows]


def list_module_types(connection: Connection) -> list[dict[str, Any]]:
    """List module types by configured sort order and id."""
    rows = connection.execute(text(
        "SELECT id, name, category, icon, description, has_input, outputs, settings "
        "FROM module_types ORDER BY sort_order, id"
    )).mappings()
    return [dict(row) for row in rows]


def list_assets(
    connection: Connection,
    workflow_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List assets with current-version module names, optionally filtered by workflow and status."""
    filters = []
    parameters: dict[str, str] = {}
    if workflow_id is not None:
        filters.append("asset.workflow_id = :workflow_id")
        parameters["workflow_id"] = workflow_id
    if status is not None:
        filters.append("asset.status = :status")
        parameters["status"] = status
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = connection.execute(text(
        "SELECT asset.id, asset.workflow_id, asset.name, asset.status, asset.current_module_id, "
        "module.name AS current_module, asset.step_status, asset.updated_at "
        "FROM assets AS asset "
        "JOIN workflows AS workflow ON workflow.id = asset.workflow_id "
        "LEFT JOIN workflow_modules AS module ON module.workflow_id = asset.workflow_id "
        "AND module.version = workflow.current_version AND module.id = asset.current_module_id "
        f"{where} ORDER BY asset.list_position, asset.id"
    ), parameters).mappings()
    return [dict(row) for row in rows]
