"""Read-only SQL queries for the Marathon API."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.common.availability import AvailabilityError, default_availability, normalize_availability
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.object_access import access_filter_sql, access_level_sql, object_access_map
from marathon.server.db.workflow_checks import workflow_issues


_asset_statuses = ("pending", "in_progress", "done", "failed", "on_hold")
blind_module_name = "Nicht freigegebenes Modul"
blind_message_text = "Meldung eines nicht freigegebenen Moduls"


def _asset_counts(
    connection: Connection,
    workflow_id: str | None = None,
    user: AuthUser | None = None,
) -> dict[str, int]:
    """Return initialized status counts, optionally limited to visible workflows."""
    counts = {status: 0 for status in _asset_statuses}
    access_filter, parameters = access_filter_sql(user, "workflow", "workflow")
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
    access_sql, parameters = access_level_sql(user, "workflow", "workflow")
    return (
        "SELECT workflow.id, workflow.name, workflow.description, workflow.active, workflow.pausing, "
        "workflow.current_version AS version, workflow.schedule_every, workflow.schedule_unit, "
        "workflow.schedule_start, workflow.next_run_at, workflow.owner_user_id, "
        "owner.email AS owner_email, owner.display_name AS owner_display_name, "
        f"{access_sql} AS access "
        "FROM workflows AS workflow "
        "LEFT JOIN users AS owner ON owner.id = workflow.owner_user_id ",
        parameters,
    )

def list_workflows(connection: Connection, user: AuthUser | None = None) -> list[dict[str, Any]]:
    """List visible workflows with asset counts and current issues."""
    select, parameters = _workflow_select(user)
    access_filter, access_parameters = access_filter_sql(user, "workflow", "workflow")
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
    access_filter, access_parameters = access_filter_sql(user, "workflow", "workflow")
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
    "COALESCE(worker.last_seen, worker.created_at) AS last_seen, worker.ip_address, worker.approved, "
    "worker.client_available AS available, worker.availability_reason, worker.availability_config, "
    "worker.availability_revision "
    "FROM workers AS worker "
)


def _worker_record(row: Any) -> dict[str, Any]:
    """Map availability settings to their canonical public representation."""
    worker = dict(row)
    try:
        worker["availability_config"] = normalize_availability(worker["availability_config"])
    except AvailabilityError:
        worker["availability_config"] = default_availability()
    return worker


def list_workers(connection: Connection) -> list[dict[str, Any]]:
    """List workers alphabetically with active-job counts and a non-null last-seen time."""
    rows = connection.execute(text(_worker_select + "ORDER BY worker.name")).mappings()
    return [_worker_record(row) for row in rows]


def get_worker(connection: Connection, worker_id: str) -> dict[str, Any] | None:
    """Return one worker in the list format, or None when it is missing."""
    row = connection.execute(
        text(_worker_select + "WHERE worker.id = :worker_id"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    return _worker_record(row) if row is not None else None


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
    """List visible assets, masking current modules without macro access."""
    filters, parameters = [], {}
    access_filter, access_parameters = access_filter_sql(user, "workflow", "workflow")
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
        "module.name AS current_module, instance.macro_id AS module_macro_id, "
        "asset.step_status, asset.updated_at "
        "FROM assets AS asset "
        "JOIN workflows AS workflow ON workflow.id = asset.workflow_id "
        "LEFT JOIN workflow_modules AS module ON module.workflow_id = asset.workflow_id "
        "AND module.version = workflow.current_version AND module.id = asset.current_module_id "
        "LEFT JOIN workflow_instances AS instance ON instance.workflow_id = module.workflow_id "
        "AND instance.version = module.version AND instance.id = module.instance_id "
        f"WHERE {' AND '.join(filters)} ORDER BY asset.list_position, asset.id"
    ), parameters).mappings()
    assets = [dict(row) for row in rows]
    module_access = object_access_map(
        connection, user, "macro", (asset["module_macro_id"] for asset in assets if asset["module_macro_id"] is not None)
    )
    for asset in assets:
        macro_id = asset.pop("module_macro_id")
        if macro_id is not None and module_access.get(macro_id) is None:
            asset["current_module"] = blind_module_name
    return assets
