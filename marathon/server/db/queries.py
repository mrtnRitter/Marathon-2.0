"""Read-only SQL queries for the Marathon API."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.auth.sessions import AuthUser
from marathon.server.db.workflow_access import workflow_access_filter
from marathon.server.db.workflow_checks import workflow_issues


_asset_statuses = ("pending", "in_progress", "done", "failed", "on_hold")


def _asset_counts(
    connection: Connection,
    workflow_id: str | None = None,
    user: AuthUser | None = None,
) -> dict[str, int]:
    """Return initialized status counts, optionally limited to visible workflows."""
    counts = {status: 0 for status in _asset_statuses}
    access_filter, parameters = workflow_access_filter(user)
    filters = [access_filter]
    if workflow_id is not None:
        filters.append("workflow.id = :workflow_id")
        parameters["workflow_id"] = workflow_id
    rows = connection.execute(
        text(
            "SELECT asset.status, COUNT(*) AS count FROM assets AS asset "
            "JOIN workflows AS workflow ON workflow.id = asset.workflow_id "
            f"WHERE {' AND '.join(filters)} GROUP BY asset.status"
        ),
        parameters,
    ).mappings()
    counts.update({row["status"]: row["count"] for row in rows})
    return counts


def _workflow_record(row: Any) -> dict[str, Any]:
    """Map a workflow row and its nullable database fields to the API shape."""
    workflow = dict(row)
    every = workflow.pop("schedule_every")
    unit = workflow.pop("schedule_unit")
    start = workflow.pop("schedule_start")
    next_run_at = workflow.pop("next_run_at")
    owner_id = workflow.pop("owner_user_id")
    owner_email = workflow.pop("owner_email")
    owner_display_name = workflow.pop("owner_display_name")
    workflow["schedule"] = None if every is None else {
        "every": every,
        "unit": unit,
        "start": start,
        "next_run_at": next_run_at,
    }
    workflow["owner"] = None if owner_id is None else {
        "id": owner_id,
        "email": owner_email,
        "display_name": owner_display_name,
    }
    return workflow


def _workflow_select(user: AuthUser | None) -> tuple[str, dict[str, Any]]:
    """Return workflow columns, the effective access level, and user parameters."""
    _, parameters = workflow_access_filter(user)
    parameters = {
        **parameters,
        "workflow_access_is_admin": user is None or user.is_admin,
        "workflow_access_user_id": user.id if user is not None else "",
    }
    return (
        "SELECT workflow.id, workflow.name, workflow.description, workflow.active, workflow.pausing, "
        "workflow.current_version AS version, workflow.schedule_every, workflow.schedule_unit, "
        "workflow.schedule_start, workflow.next_run_at, workflow.owner_user_id, "
        "owner.email AS owner_email, owner.display_name AS owner_display_name, "
        "CASE WHEN :workflow_access_is_admin THEN 'admin' "
        "WHEN workflow.owner_user_id = :workflow_access_user_id THEN 'owner' "
        "ELSE share.access END AS access "
        "FROM workflows AS workflow "
        "LEFT JOIN users AS owner ON owner.id = workflow.owner_user_id "
        "LEFT JOIN workflow_shares AS share ON share.workflow_id = workflow.id "
        "AND share.user_id = :workflow_access_user_id ",
        parameters,
    )


def list_workflows(connection: Connection, user: AuthUser | None = None) -> list[dict[str, Any]]:
    """List visible workflows with asset counts and current issues."""
    select, parameters = _workflow_select(user)
    access_filter, access_parameters = workflow_access_filter(user)
    parameters.update(access_parameters)
    rows = connection.execute(
        text(select + f"WHERE {access_filter} ORDER BY lower(workflow.name), workflow.id"),
        parameters,
    ).mappings()
    workflows = [_workflow_record(row) for row in rows]
    counts_by_workflow = {
        workflow["id"]: _asset_counts(connection, workflow["id"], user) for workflow in workflows
    }
    for workflow in workflows:
        workflow["asset_counts"] = counts_by_workflow[workflow["id"]]
        workflow["issues"] = workflow_issues(connection, workflow["id"])
    return workflows


def get_workflow(
    connection: Connection,
    workflow_id: str,
    user: AuthUser | None = None,
) -> dict[str, Any] | None:
    """Return one visible workflow and its asset counts, or None."""
    select, parameters = _workflow_select(user)
    access_filter, access_parameters = workflow_access_filter(user)
    parameters.update(access_parameters)
    parameters["workflow_id"] = workflow_id
    row = connection.execute(
        text(select + f"WHERE workflow.id = :workflow_id AND {access_filter}"),
        parameters,
    ).mappings().one_or_none()
    if row is None:
        return None
    workflow = _workflow_record(row)
    workflow["asset_counts"] = _asset_counts(connection, workflow_id, user)
    workflow["issues"] = workflow_issues(connection, workflow_id)
    return workflow


def asset_totals(connection: Connection, user: AuthUser | None = None) -> dict[str, int]:
    """Return total asset counts for visible workflows, including zero counts."""
    return _asset_counts(connection, user=user)


_worker_select = (
    "SELECT worker.id, worker.name, worker.status, worker.online, worker.capabilities, worker.skills, "
    "(SELECT COUNT(*) FROM jobs WHERE jobs.status IN ('assigned', 'running', 'cancelling') "
    "AND jobs.worker_id = worker.id) AS running_jobs, worker.client_version, "
    "COALESCE(worker.last_seen, worker.created_at) AS last_seen, worker.ip_address "
    "FROM workers AS worker "
)


def list_workers(connection: Connection) -> list[dict[str, Any]]:
    """List workers alphabetically with active-job counts and a non-null last-seen time."""
    rows = connection.execute(text(_worker_select + "ORDER BY worker.name")).mappings()
    return [dict(row) for row in rows]


def get_worker(connection: Connection, worker_id: str) -> dict[str, Any] | None:
    """Return one worker in the list format, or None when it is missing."""
    row = connection.execute(
        text(_worker_select + "WHERE worker.id = :worker_id"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def list_module_types(connection: Connection) -> list[dict[str, Any]]:
    """List module types by configured sort order and ID."""
    rows = connection.execute(text(
        "SELECT id, name, category, icon, description, has_input, outputs, settings "
        "FROM module_types ORDER BY sort_order, id"
    )).mappings()
    return [dict(row) for row in rows]


def list_assets(
    connection: Connection,
    workflow_id: str | None = None,
    status: str | None = None,
    user: AuthUser | None = None,
) -> list[dict[str, Any]]:
    """List assets from visible workflows, optionally filtered by workflow and status."""
    filters, parameters = [], {}
    access_filter, access_parameters = workflow_access_filter(user)
    filters.append(access_filter)
    parameters.update(access_parameters)
    if workflow_id is not None:
        filters.append("asset.workflow_id = :workflow_id")
        parameters["workflow_id"] = workflow_id
    if status is not None:
        filters.append("asset.status = :status")
        parameters["status"] = status
    rows = connection.execute(text(
        "SELECT asset.id, asset.workflow_id, asset.name, asset.status, asset.current_module_id, "
        "module.name AS current_module, asset.step_status, asset.updated_at "
        "FROM assets AS asset "
        "JOIN workflows AS workflow ON workflow.id = asset.workflow_id "
        "LEFT JOIN workflow_modules AS module ON module.workflow_id = asset.workflow_id "
        "AND module.version = workflow.current_version AND module.id = asset.current_module_id "
        f"WHERE {' AND '.join(filters)} ORDER BY asset.list_position, asset.id"
    ), parameters).mappings()
    return [dict(row) for row in rows]
