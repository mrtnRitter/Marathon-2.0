"""Transactional write operations for workflows and their graph versions."""

# IMPORTS
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.engine import Connection

from marathon.server.schedule import ScheduleError, next_occurrence, normalize_interval


# EXCEPTIONS
class WorkflowValidationError(ValueError):
    """Raised when workflow or graph input fails domain validation."""


class WorkflowConflict(Exception):
    """Raised when a graph was edited from a stale workflow version."""


# VALIDATION
def _lock_workflow_names(connection: Connection) -> None:
    """Acquire the transaction lock used to keep workflow names unique."""
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('marathon.workflow_name'))"))


def _validate_workflow_name(connection: Connection, name: str, workflow_id: str) -> str:
    """Return a normalized workflow name or raise its validation message."""
    normalized_name = name.strip()
    if not normalized_name:
        raise WorkflowValidationError("Name fehlt")
    if len(normalized_name) > 200:
        raise WorkflowValidationError("Name ist zu lang (max. 200 Zeichen)")
    _lock_workflow_names(connection)
    duplicate = connection.execute(
        text(
            "SELECT 1 FROM workflows "
            "WHERE lower(name) = lower(:name) AND id <> :id"
        ),
        {"name": normalized_name, "id": workflow_id},
    ).scalar_one_or_none()
    if duplicate is not None:
        raise WorkflowValidationError("Ein Workflow mit diesem Namen existiert bereits")
    return normalized_name


def _validate_graph(
    modules: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    module_types: dict[str, dict[str, Any]],
) -> None:
    """Validate modules and connections in the specified first-error order."""
    for module in modules:
        module_id = module["id"]
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", module_id) is None:
            raise WorkflowValidationError(f"Ungültige Modul-ID: {module_id}")

    modules_by_id: dict[str, dict[str, Any]] = {}
    for module in modules:
        module_id = module["id"]
        if module_id in modules_by_id:
            raise WorkflowValidationError(f"Modul-ID doppelt: {module_id}")
        modules_by_id[module_id] = module

    for module in modules:
        type_id = module["type_id"]
        if type_id not in module_types:
            raise WorkflowValidationError(f"Unbekannter Modultyp: {type_id}")

    for module in modules:
        if not module["name"].strip():
            raise WorkflowValidationError(f"Modulname fehlt: {module['id']}")

    for graph_connection in connections:
        for module_id in (graph_connection["source_module_id"], graph_connection["target_module_id"]):
            if module_id not in modules_by_id:
                raise WorkflowValidationError(f"Verbindung zu unbekanntem Modul: {module_id}")

    for graph_connection in connections:
        source_id = graph_connection["source_module_id"]
        if source_id == graph_connection["target_module_id"]:
            raise WorkflowValidationError(f"Modul {source_id} kann nicht mit sich selbst verbunden werden")

    for graph_connection in connections:
        source_id = graph_connection["source_module_id"]
        output_id = graph_connection["source_output_id"]
        source_type = module_types[modules_by_id[source_id]["type_id"]]
        if not any(isinstance(output, dict) and output.get("id") == output_id for output in source_type["outputs"]):
            raise WorkflowValidationError(f"Unbekannter Ausgang {output_id} an Modul {source_id}")

    for graph_connection in connections:
        target_id = graph_connection["target_module_id"]
        target_type = module_types[modules_by_id[target_id]["type_id"]]
        if not target_type["has_input"]:
            raise WorkflowValidationError(f"Modul {target_id} hat keinen Eingang")

    connected_outputs: set[tuple[str, str]] = set()
    for graph_connection in connections:
        source_id = graph_connection["source_module_id"]
        output_id = graph_connection["source_output_id"]
        output_key = (source_id, output_id)
        if output_key in connected_outputs:
            raise WorkflowValidationError(f"Ausgang {output_id} an Modul {source_id} ist mehrfach verbunden")
        connected_outputs.add(output_key)


def _schedule_values(
    schedule: dict[str, Any] | None,
    now: datetime | None,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize schedule fields and calculate the next scheduled run."""
    keys = ("schedule_every", "schedule_unit", "schedule_start", "next_run_at")
    if schedule is None:
        return dict.fromkeys(keys)

    try:
        every, unit = normalize_interval(schedule["every"], schedule["unit"])
        start = schedule["start"]
        if not isinstance(start, datetime) or start.utcoffset() is None:
            raise ScheduleError("Startzeit braucht eine Zeitzone")
        unchanged = (
            current is not None
            and current.get("schedule_every") == every
            and current.get("schedule_unit") == unit
            and current.get("schedule_start") == start
        )
        next_run_at = (
            current["next_run_at"]
            if unchanged
            else next_occurrence(start, every, unit, after=now or datetime.now(timezone.utc))
        )
    except ScheduleError as exc:
        raise WorkflowValidationError(str(exc)) from exc

    return {
        "schedule_every": every,
        "schedule_unit": unit,
        "schedule_start": start,
        "next_run_at": next_run_at,
    }


# WORKFLOW WRITES
def create_workflow(
    connection: Connection,
    name: str,
    description: str,
    schedule: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    """Create a workflow and its initial empty version, returning its ID."""
    workflow_id = f"wf-{uuid4().hex[:12]}"
    normalized_name = _validate_workflow_name(connection, name, workflow_id)
    schedule_fields = _schedule_values(schedule, now)
    connection.execute(
        text(
            "INSERT INTO workflows "
            "(id, name, description, active, current_version, schedule_every, schedule_unit, "
            "schedule_start, next_run_at, scheduled_active) "
            "VALUES (:id, :name, :description, false, 1, :schedule_every, :schedule_unit, "
            ":schedule_start, :next_run_at, false)"
        ),
        {
            "id": workflow_id,
            "name": normalized_name,
            "description": description.strip(),
            **schedule_fields,
        },
    )
    connection.execute(
        text("INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, 1)"),
        {"id": workflow_id},
    )
    return workflow_id


def duplicate_workflow(
    connection: Connection,
    workflow_id: str,
    now: datetime | None = None,
) -> str | None:
    """Duplicate a workflow's current metadata and graph, or return None if missing."""
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    source = connection.execute(
        text(
            "SELECT name, description, current_version, schedule_every, schedule_unit, schedule_start "
            "FROM workflows WHERE id = :id FOR SHARE"
        ),
        {"id": workflow_id},
    ).mappings().one_or_none()
    if source is None:
        return None

    _lock_workflow_names(connection)
    base_name = source["name"]
    copy_number = 1
    while True:
        suffix = " (Kopie)" if copy_number == 1 else f" (Kopie {copy_number})"
        candidate = f"{base_name[:200 - len(suffix)].rstrip()}{suffix}"
        name_taken = connection.execute(
            text("SELECT 1 FROM workflows WHERE lower(name) = lower(:candidate)"),
            {"candidate": candidate},
        ).scalar_one_or_none()
        if name_taken is None:
            break
        copy_number += 1

    schedule = None
    if source["schedule_every"] is not None:
        schedule = {
            "every": source["schedule_every"],
            "unit": source["schedule_unit"],
            "start": source["schedule_start"],
        }
    schedule_fields = _schedule_values(schedule, now)
    duplicate_id = f"wf-{uuid4().hex[:12]}"
    connection.execute(
        text(
            "INSERT INTO workflows "
            "(id, name, description, active, current_version, schedule_every, schedule_unit, "
            "schedule_start, next_run_at, scheduled_active) "
            "VALUES (:id, :name, :description, false, 1, :schedule_every, :schedule_unit, "
            ":schedule_start, :next_run_at, false)"
        ),
        {
            "id": duplicate_id,
            "name": candidate,
            "description": source["description"],
            **schedule_fields,
        },
    )
    connection.execute(
        text("INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, 1)"),
        {"id": duplicate_id},
    )
    connection.execute(
        text(
            "INSERT INTO workflow_modules "
            "(workflow_id, version, id, type_id, name, description, position_x, position_y, settings, locked) "
            "SELECT :duplicate_id, 1, id, type_id, name, description, position_x, position_y, settings, locked "
            "FROM workflow_modules WHERE workflow_id = :workflow_id AND version = :version"
        ),
        {"duplicate_id": duplicate_id, "workflow_id": workflow_id, "version": source["current_version"]},
    )
    connection.execute(
        text(
            "INSERT INTO workflow_connections "
            "(workflow_id, version, source_module_id, source_output_id, target_module_id) "
            "SELECT :duplicate_id, 1, source_module_id, source_output_id, target_module_id "
            "FROM workflow_connections WHERE workflow_id = :workflow_id AND version = :version"
        ),
        {"duplicate_id": duplicate_id, "workflow_id": workflow_id, "version": source["current_version"]},
    )
    return duplicate_id


def update_workflow(
    connection: Connection,
    workflow_id: str,
    name: str,
    description: str,
    schedule: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Update workflow metadata and schedule, returning False if it is missing."""
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    current_row = connection.execute(
        text(
            "SELECT schedule_every, schedule_unit, schedule_start, next_run_at, scheduled_active "
            "FROM workflows WHERE id = :id FOR UPDATE"
        ),
        {"id": workflow_id},
    ).mappings().one_or_none()
    if current_row is None:
        return False

    normalized_name = _validate_workflow_name(connection, name, workflow_id)
    current = dict(current_row)
    schedule_fields = _schedule_values(schedule, now, current)
    scheduled_active = current["scheduled_active"] if schedule is not None else False
    updated = connection.execute(
        text(
            "UPDATE workflows SET name = :name, description = :description, "
            "schedule_every = :schedule_every, schedule_unit = :schedule_unit, "
            "schedule_start = :schedule_start, next_run_at = :next_run_at, "
            "scheduled_active = :scheduled_active, updated_at = now() WHERE id = :id RETURNING id"
        ),
        {
            "id": workflow_id,
            "name": normalized_name,
            "description": description.strip(),
            **schedule_fields,
            "scheduled_active": scheduled_active,
        },
    ).scalar_one_or_none()
    return updated is not None


def set_workflow_active(connection: Connection, workflow_id: str, active: bool) -> bool:
    """Set a workflow's active state and clear schedule activation."""
    updated = connection.execute(
        text(
            "UPDATE workflows SET active = :active, scheduled_active = false, "
            "updated_at = now() WHERE id = :id RETURNING id"
        ),
        {"id": workflow_id, "active": active},
    ).scalar_one_or_none()
    return updated is not None


def delete_workflow(connection: Connection, workflow_id: str) -> bool:
    """Delete a workflow and its dependent rows, returning whether it existed."""
    deleted = connection.execute(
        text("DELETE FROM workflows WHERE id = :id RETURNING id"), {"id": workflow_id}
    ).scalar_one_or_none()
    return deleted is not None


def save_graph(
    connection: Connection,
    workflow_id: str,
    base_version: int,
    modules: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> int | None:
    """Validate and save a new immutable graph version, or return None if missing."""
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    current_version = connection.execute(
        text("SELECT current_version FROM workflows WHERE id = :id FOR UPDATE"),
        {"id": workflow_id},
    ).scalar_one_or_none()
    if current_version is None:
        return None
    if current_version != base_version:
        raise WorkflowConflict

    module_types: dict[str, dict[str, Any]] = {}
    type_ids = list(dict.fromkeys(module["type_id"] for module in modules))
    if type_ids:
        rows = connection.execute(
            text("SELECT id, has_input, outputs FROM module_types WHERE id = ANY(:ids)").bindparams(
                bindparam("ids", type_=ARRAY(Text()))
            ),
            {"ids": type_ids},
        ).mappings()
        module_types = {row["id"]: dict(row) for row in rows}
    _validate_graph(modules, connections, module_types)

    new_version = current_version + 1
    connection.execute(
        text("INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, :version)"),
        {"id": workflow_id, "version": new_version},
    )
    module_insert = text(
        "INSERT INTO workflow_modules "
        "(workflow_id, version, id, type_id, name, description, position_x, position_y, settings, locked) "
        "VALUES (:workflow_id, :version, :id, :type_id, :name, :description, :position_x, :position_y, "
        ":settings, :locked)"
    ).bindparams(bindparam("settings", type_=JSONB()))
    for module in modules:
        connection.execute(
            module_insert,
            {
                "workflow_id": workflow_id,
                "version": new_version,
                "id": module["id"],
                "type_id": module["type_id"],
                "name": module["name"],
                "description": module["description"],
                "position_x": module["position"]["x"],
                "position_y": module["position"]["y"],
                "settings": module["settings"],
                "locked": module["locked"],
            },
        )
    connection_insert = text(
        "INSERT INTO workflow_connections "
        "(workflow_id, version, source_module_id, source_output_id, target_module_id) "
        "VALUES (:workflow_id, :version, :source_module_id, :source_output_id, :target_module_id)"
    )
    for graph_connection in connections:
        connection.execute(
            connection_insert,
            {
                "workflow_id": workflow_id,
                "version": new_version,
                **graph_connection,
            },
        )
    connection.execute(
        text("UPDATE workflows SET current_version = :version, updated_at = now() WHERE id = :id"),
        {"id": workflow_id, "version": new_version},
    )
    return new_version
