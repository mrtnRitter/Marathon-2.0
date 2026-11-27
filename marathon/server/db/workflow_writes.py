"""Transactional write operations for workflows and their graph versions."""

# IMPORTS
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from marathon.server.db.macro_writes import load_definitions, load_macros, module_type_map
from marathon.server.db.workflow_checks import invalid_modules, workflow_issues
from marathon.server.macros.definition import macro_hull
from marathon.server.macros.expand import expand_graph
from marathon.server.engine.jobs import remove_missing_modules, request_pause
from marathon.server.schedule import ScheduleError, next_occurrence, normalize_interval


# EXCEPTIONS
class WorkflowValidationError(ValueError):
    """Raised when workflow or graph input fails domain validation."""


class WorkflowConflict(Exception):
    """Raised when a graph was edited from a stale workflow version."""


class WorkflowNotStartable(Exception):
    """Raised when settings issues prevent workflow activation."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__(f"Einstellungen prüfen: {', '.join(issues)}")


class WorkflowPausing(Exception):
    """Raised when a workflow is still stopping active jobs."""


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


def _validate_module_ids(modules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Validate instance identifiers and return the graph indexed by ID."""
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
    return modules_by_id


def _validate_graph(
    modules: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    hulls: dict[str, dict[str, Any]],
) -> None:
    """Validate names and connections against the resolved instance hulls."""
    modules_by_id = _validate_module_ids(modules)
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
        if not any(isinstance(output, dict) and output.get("id") == output_id for output in hulls[source_id]["outputs"]):
            raise WorkflowValidationError(f"Unbekannter Ausgang {output_id} an Modul {source_id}")

    for graph_connection in connections:
        target_id = graph_connection["target_module_id"]
        if not hulls[target_id]["has_input"]:
            raise WorkflowValidationError(f"Modul {target_id} hat keinen Eingang")

    connected_outputs: set[tuple[str, str]] = set()
    for graph_connection in connections:
        source_id = graph_connection["source_module_id"]
        output_id = graph_connection["source_output_id"]
        output_key = (source_id, output_id)
        if output_key in connected_outputs:
            raise WorkflowValidationError(f"Ausgang {output_id} an Modul {source_id} ist mehrfach verbunden")
        connected_outputs.add(output_key)


def resolve_graph(
    connection: Connection,
    workflow_id: str,
    modules: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate and resolve proposed instances without changing caller-owned input."""
    _validate_module_ids(modules)
    macro_ids = list(dict.fromkeys(module["type_id"] for module in modules))
    macros = load_macros(connection, macro_ids)
    module_types = module_type_map(connection)

    versions: dict[str, int] = {}
    definition_keys = []
    for module in modules:
        macro = macros.get(module["type_id"])
        if macro is None:
            continue
        version = module.get("macro_version")
        if version is None:
            version = macro["current_version"]
        versions[module["id"]] = version
        definition_keys.append((module["type_id"], version))
    definitions = load_definitions(connection, definition_keys)

    current_version = connection.execute(
        text("SELECT current_version FROM workflows WHERE id = :id"), {"id": workflow_id}
    ).scalar_one_or_none()
    existing_versions: set[tuple[str, int]] = set()
    if current_version is not None:
        existing_versions = set(connection.execute(
            text(
                "SELECT macro_id, macro_version FROM workflow_instances "
                "WHERE workflow_id = :id AND version = :version"
            ),
            {"id": workflow_id, "version": current_version},
        ).all())

    hulls: dict[str, dict[str, Any]] = {}
    instances: list[dict[str, Any]] = []
    for module in modules:
        type_id = module["type_id"]
        macro = macros.get(type_id)
        if macro is None:
            raise WorkflowValidationError(f"Unbekanntes Modul: {type_id}")
        version = versions[module["id"]]
        key = (type_id, version)
        definition = definitions.get(key)
        if definition is None:
            raise WorkflowValidationError(f"Unbekannte Modulversion: {type_id} v{version}")
        if macro["archived_at"] is not None and key not in existing_versions:
            raise WorkflowValidationError(f"Modul ist gelöscht: {type_id}")
        hulls[module["id"]] = macro_hull(type_id, version, definition, module_types)
        instances.append({**module, "macro_id": type_id, "macro_version": version})

    _validate_graph(modules, connections, hulls)
    instance_connections = [dict(item) for item in connections]
    runtime_modules, runtime_connections = expand_graph(instances, instance_connections, definitions)
    return instances, instance_connections, runtime_modules, runtime_connections


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
    owner_user_id: str | None = None,
) -> str:
    """Create a workflow and its initial empty version, returning its ID."""
    workflow_id = f"wf-{uuid4().hex[:12]}"
    normalized_name = _validate_workflow_name(connection, name, workflow_id)
    schedule_fields = _schedule_values(schedule, now)
    connection.execute(
        text(
            "INSERT INTO workflows "
            "(id, name, description, active, current_version, schedule_every, schedule_unit, "
            "schedule_start, next_run_at, owner_user_id) "
            "VALUES (:id, :name, :description, false, 1, :schedule_every, :schedule_unit, "
            ":schedule_start, :next_run_at, :owner_user_id)"
        ),
        {
            "id": workflow_id,
            "name": normalized_name,
            "description": description.strip(),
            "owner_user_id": owner_user_id,
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
    owner_user_id: str | None = None,
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
            "schedule_start, next_run_at, owner_user_id) "
            "VALUES (:id, :name, :description, false, 1, :schedule_every, :schedule_unit, "
            ":schedule_start, :next_run_at, :owner_user_id)"
        ),
        {
            "id": duplicate_id,
            "name": candidate,
            "description": source["description"],
            "owner_user_id": owner_user_id,
            **schedule_fields,
        },
    )
    connection.execute(
        text("INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, 1)"),
        {"id": duplicate_id},
    )
    copy_parameters = {"duplicate_id": duplicate_id, "workflow_id": workflow_id, "version": source["current_version"]}
    connection.execute(
        text(
            "INSERT INTO workflow_instances "
            "(workflow_id, version, id, macro_id, macro_version, name, description, position_x, position_y, settings, locked) "
            "SELECT :duplicate_id, 1, id, macro_id, macro_version, name, description, position_x, position_y, settings, locked "
            "FROM workflow_instances WHERE workflow_id = :workflow_id AND version = :version"
        ),
        copy_parameters,
    )
    connection.execute(
        text(
            "INSERT INTO workflow_instance_connections "
            "(workflow_id, version, source_id, source_output_id, target_id) "
            "SELECT :duplicate_id, 1, source_id, source_output_id, target_id "
            "FROM workflow_instance_connections WHERE workflow_id = :workflow_id AND version = :version"
        ),
        copy_parameters,
    )
    connection.execute(
        text(
            "INSERT INTO workflow_modules "
            "(workflow_id, version, id, type_id, name, description, position_x, position_y, settings, locked, instance_id, block_id, exits) "
            "SELECT :duplicate_id, 1, id, type_id, name, description, position_x, position_y, settings, locked, instance_id, block_id, exits "
            "FROM workflow_modules WHERE workflow_id = :workflow_id AND version = :version"
        ),
        copy_parameters,
    )
    connection.execute(
        text(
            "INSERT INTO workflow_connections "
            "(workflow_id, version, source_module_id, source_output_id, target_module_id) "
            "SELECT :duplicate_id, 1, source_module_id, source_output_id, target_module_id "
            "FROM workflow_connections WHERE workflow_id = :workflow_id AND version = :version"
        ),
        copy_parameters,
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
            "SELECT schedule_every, schedule_unit, schedule_start, next_run_at, schedule_waiting, run_started_at "
            "FROM workflows WHERE id = :id FOR UPDATE"
        ),
        {"id": workflow_id},
    ).mappings().one_or_none()
    if current_row is None:
        return False

    normalized_name = _validate_workflow_name(connection, name, workflow_id)
    current = dict(current_row)
    schedule_fields = _schedule_values(schedule, now, current)
    schedule_waiting = current["schedule_waiting"] if schedule is not None else False
    run_started_at = current["run_started_at"] if schedule is not None else None
    updated = connection.execute(
        text(
            "UPDATE workflows SET name = :name, description = :description, "
            "schedule_every = :schedule_every, schedule_unit = :schedule_unit, "
            "schedule_start = :schedule_start, next_run_at = :next_run_at, "
            "schedule_waiting = :schedule_waiting, run_started_at = :run_started_at, "
            "updated_at = now() WHERE id = :id RETURNING id"
        ),
        {
            "id": workflow_id,
            "name": normalized_name,
            "description": description.strip(),
            **schedule_fields,
            "schedule_waiting": schedule_waiting,
            "run_started_at": run_started_at,
        },
    ).scalar_one_or_none()
    return updated is not None


def set_workflow_active(connection: Connection, workflow_id: str, active: bool) -> bool:
    """Set a workflow's active state and clear schedule activation."""
    if not active:
        return request_pause(connection, workflow_id, datetime.now(timezone.utc))

    workflow = connection.execute(
        text("SELECT id, pausing, schedule_every FROM workflows WHERE id = :id FOR UPDATE"), {"id": workflow_id}
    ).mappings().one_or_none()
    if workflow is None:
        return False
    if workflow["pausing"]:
        raise WorkflowPausing
    issues = workflow_issues(connection, workflow_id)
    if issues:
        raise WorkflowNotStartable(issues)
    updated = connection.execute(
        text(
            "UPDATE workflows SET active = true, schedule_waiting = (schedule_every IS NOT NULL), "
            "run_started_at = NULL, updated_at = now() WHERE id = :id RETURNING id"
        ),
        {"id": workflow_id},
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
    """Validate and save a new immutable instance and runtime graph version."""
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    current_row = connection.execute(
        text("SELECT current_version, active FROM workflows WHERE id = :id FOR UPDATE"),
        {"id": workflow_id},
    ).mappings().one_or_none()
    if current_row is None:
        return None
    current_version = current_row["current_version"]
    workflow_active = current_row["active"]
    if current_version != base_version:
        raise WorkflowConflict

    instances, instance_connections, runtime_modules, runtime_connections = resolve_graph(
        connection, workflow_id, modules, connections
    )
    new_version = current_version + 1
    connection.execute(
        text("INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, :version)"),
        {"id": workflow_id, "version": new_version},
    )

    instance_insert = text(
        "INSERT INTO workflow_instances "
        "(workflow_id, version, id, macro_id, macro_version, name, description, position_x, position_y, settings, locked) "
        "VALUES (:workflow_id, :version, :id, :macro_id, :macro_version, :name, :description, :position_x, :position_y, :settings, :locked)"
    ).bindparams(bindparam("settings", type_=JSONB()))
    for instance in instances:
        connection.execute(instance_insert, {
            "workflow_id": workflow_id,
            "version": new_version,
            "id": instance["id"],
            "macro_id": instance["macro_id"],
            "macro_version": instance["macro_version"],
            "name": instance["name"],
            "description": instance.get("description", ""),
            "position_x": instance["position"]["x"],
            "position_y": instance["position"]["y"],
            "settings": instance.get("settings", {}),
            "locked": instance.get("locked", False),
        })
    instance_connection_insert = text(
        "INSERT INTO workflow_instance_connections "
        "(workflow_id, version, source_id, source_output_id, target_id) "
        "VALUES (:workflow_id, :version, :source_module_id, :source_output_id, :target_module_id)"
    )
    for item in instance_connections:
        connection.execute(instance_connection_insert, {
            "workflow_id": workflow_id, "version": new_version, **item,
        })

    runtime_insert = text(
        "INSERT INTO workflow_modules "
        "(workflow_id, version, id, type_id, name, description, position_x, position_y, settings, locked, instance_id, block_id, exits) "
        "VALUES (:workflow_id, :version, :id, :type_id, :name, :description, :position_x, :position_y, :settings, :locked, :instance_id, :block_id, :exits)"
    ).bindparams(bindparam("settings", type_=JSONB()), bindparam("exits", type_=JSONB()))
    for module in runtime_modules:
        connection.execute(runtime_insert, {
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
            "instance_id": module["instance_id"],
            "block_id": module["block_id"],
            "exits": module["exits"],
        })
    runtime_connection_insert = text(
        "INSERT INTO workflow_connections "
        "(workflow_id, version, source_module_id, source_output_id, target_module_id) "
        "VALUES (:workflow_id, :version, :source_module_id, :source_output_id, :target_module_id)"
    )
    for item in runtime_connections:
        connection.execute(runtime_connection_insert, {
            "workflow_id": workflow_id, "version": new_version, **item,
        })

    connection.execute(
        text("UPDATE workflows SET current_version = :version, updated_at = now() WHERE id = :id"),
        {"id": workflow_id, "version": new_version},
    )
    now = datetime.now(timezone.utc)
    remove_missing_modules(connection, workflow_id, now)
    if workflow_active:
        invalid = invalid_modules(runtime_modules)
        invalid_instances = {}
        for module in invalid:
            invalid_instances.setdefault(module["instance_id"], module)
        if invalid_instances:
            request_pause(connection, workflow_id, now)
            for module in invalid_instances.values():
                connection.execute(
                    text(
                        "INSERT INTO log_lines (workflow_id, module_id, job_id, level, text) "
                        "VALUES (:workflow_id, :module_id, NULL, 'warn', 'Workflow gestoppt: Einstellungen prüfen')"
                    ),
                    {"workflow_id": workflow_id, "module_id": module["id"]},
                )
    return new_version
