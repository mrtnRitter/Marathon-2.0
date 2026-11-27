"""Transactional operations for updating workflows to a macro version."""

# IMPORTS
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from marathon.server.auth.sessions import AuthUser
from marathon.server.db.edit_locks import active_foreign_lock, break_lock, lock_held_prefix
from marathon.server.db.object_access import log_event, log_intervention
from marathon.server.db.macro_writes import (
    load_definitions,
    load_macros,
    module_type_map,
    outdated_workflows,
)
from marathon.server.db.workflow_writes import (
    WorkflowConflict,
    WorkflowValidationError,
    resolve_graph,
    save_graph,
)
from marathon.server.engine.jobs import graph_impact
from marathon.server.macros.definition import macro_hull
from marathon.server.macros.expand import remap_settings


# CONFIG
app_name = "marathon-macro-updates"
app_version = "1.0"


# EXCEPTIONS
class _SkipWorkflow(Exception):
    """Abort a workflow transaction with a user-facing skip reason."""


# FUNC
def _load_update_context(
    connection: Connection,
    macro_id: str,
    user: AuthUser,
) -> tuple[list[dict[str, str]], int, dict[str, Any], str] | None:
    """Load the user's outdated workflows and the current macro hull."""
    macro = load_macros(connection, [macro_id]).get(macro_id)
    if macro is None or macro["archived_at"] is not None:
        return None
    workflows = outdated_workflows(connection, macro_id, user)
    definition = load_definitions(connection, [(macro_id, macro["current_version"])]).get(
        (macro_id, macro["current_version"])
    )
    if definition is None:
        return [], macro["current_version"], {}, ""
    hull = macro_hull(macro_id, macro["current_version"], definition, module_type_map(connection))
    return workflows, macro["current_version"], hull, definition["name"]


def _workflow_graph(connection: Connection, workflow_id: str, version: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read editable instances and edges for the locked current workflow version."""
    modules = connection.execute(text("""
        SELECT id, macro_id, macro_version, name, description, position_x, position_y, settings, locked
        FROM workflow_instances
        WHERE workflow_id = :workflow_id AND version = :version
        ORDER BY id
    """), {"workflow_id": workflow_id, "version": version}).mappings()
    connections = connection.execute(text("""
        SELECT source_id AS source_module_id, source_output_id, target_id AS target_module_id
        FROM workflow_instance_connections
        WHERE workflow_id = :workflow_id AND version = :version
        ORDER BY source_id, source_output_id
    """), {"workflow_id": workflow_id, "version": version}).mappings()
    return [
        {
            "id": row["id"],
            "type_id": row["macro_id"],
            "macro_version": row["macro_version"],
            "name": row["name"],
            "description": row["description"],
            "position": {"x": row["position_x"], "y": row["position_y"]},
            "settings": row["settings"],
            "locked": row["locked"],
        }
        for row in modules
    ], [dict(row) for row in connections]


def _update_one_workflow(
    engine: Engine,
    workflow: dict[str, str],
    macro_id: str,
    macro_version: int,
    hull: dict[str, Any],
    module_name: str,
    user: AuthUser,
    force: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Update one outdated graph, returning either its update or skip result."""
    dropped_settings: set[str] = set()
    try:
        with engine.begin() as connection:
            current_version = connection.execute(text(
                "SELECT current_version FROM workflows WHERE id = :id FOR UPDATE"
            ), {"id": workflow["id"]}).scalar_one_or_none()
            if current_version is None:
                raise _SkipWorkflow("Workflow nicht gefunden")

            lock = active_foreign_lock(connection, "workflow", workflow["id"])
            foreign_lock = lock is not None and lock["user_id"] != user.id
            if foreign_lock and not force:
                raise _SkipWorkflow(lock_held_prefix + lock["name"])

            modules, connections = _workflow_graph(connection, workflow["id"], current_version)
            has_update = False
            for module in modules:
                if module["type_id"] != macro_id or module["macro_version"] >= macro_version:
                    continue
                module["macro_version"] = macro_version
                module["settings"], removed = remap_settings(module["settings"], hull)
                dropped_settings.update(removed)
                has_update = True
            if not has_update:
                raise _SkipWorkflow("Workflow wurde zwischenzeitlich geändert. Bitte neu laden.")

            _, _, runtime_modules, runtime_connections = resolve_graph(
                connection, workflow["id"], modules, connections
            )
            impact = graph_impact(
                connection,
                workflow["id"],
                [module["id"] for module in runtime_modules],
                [
                    (item["source_module_id"], item["source_output_id"], item["target_module_id"])
                    for item in runtime_connections
                ],
            )
            if any(item["assets"] > 0 or item["jobs"] > 0 for item in impact["removed_modules"]):
                raise _SkipWorkflow("Laufende Assets betroffen")

            if foreign_lock and force:
                broken = break_lock(connection, "workflow", workflow["id"])
                if broken is not None and not broken["expired"] and broken["user_id"] != user.id:
                    log_event(
                        connection,
                        user,
                        "workflow",
                        workflow["id"],
                        f"Bearbeitungssperre von {broken['name']} aufgehoben",
                    )

            saved_version = save_graph(connection, workflow["id"], current_version, modules, connections)
            if saved_version is None:
                raise _SkipWorkflow("Workflow nicht gefunden")
            log_intervention(
                connection,
                user,
                "workflow",
                workflow["id"],
                f"Modul „{module_name}“ auf v{macro_version} aktualisiert",
            )
        return {
            "id": workflow["id"],
            "name": workflow["name"],
            "dropped_settings": sorted(dropped_settings),
        }, None
    except _SkipWorkflow as exc:
        return None, {"id": workflow["id"], "name": workflow["name"], "reason": str(exc)}
    except WorkflowValidationError as exc:
        return None, {"id": workflow["id"], "name": workflow["name"], "reason": str(exc)}
    except WorkflowConflict:
        return None, {
            "id": workflow["id"],
            "name": workflow["name"],
            "reason": "Workflow wurde zwischenzeitlich geändert. Bitte neu laden.",
        }


def update_outdated_workflows(
    engine: Engine,
    macro_id: str,
    user: AuthUser,
    force: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Upgrade outdated workflows visible to the user to the current macro version."""
    with engine.begin() as connection:
        context = _load_update_context(connection, macro_id, user)
    if context is None:
        return {"updated": [], "skipped": []}
    workflows, macro_version, hull, module_name = context
    if not hull:
        return {"updated": [], "skipped": []}

    updated = []
    skipped = []
    for workflow in workflows:
        updated_workflow, skipped_workflow = _update_one_workflow(
            engine, workflow, macro_id, macro_version, hull, module_name, user, force
        )
        if updated_workflow is not None:
            updated.append(updated_workflow)
        if skipped_workflow is not None:
            skipped.append(skipped_workflow)
    return {"updated": updated, "skipped": skipped}
