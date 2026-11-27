"""Read module setting issues from workflow graph versions."""

# IMPORTS
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.modules import catalog_module


# FUNC
def invalid_modules(modules: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return modules whose registered settings validator reports errors."""
    invalid = []
    for module in modules:
        module_type = catalog_module(module["type_id"])
        validator = getattr(module_type, "validate_settings", None) if module_type is not None else None
        if validator is not None and validator(module["settings"]):
            invalid.append(module)
    return invalid


def workflow_invalid_modules(connection: Connection, workflow_id: str) -> list[dict[str, str]]:
    """Return one sorted invalid-module record per workflow instance."""
    rows = connection.execute(
        text(
            "SELECT COALESCE(module.instance_id, module.id) AS instance_id, module.name, "
            "module.type_id, module.settings "
            "FROM workflows AS workflow "
            "JOIN workflow_modules AS module ON module.workflow_id = workflow.id "
            "AND module.version = workflow.current_version "
            "WHERE workflow.id = :workflow_id "
            "ORDER BY module.name, COALESCE(module.instance_id, module.id), module.id"
        ),
        {"workflow_id": workflow_id},
    ).mappings()
    invalid = []
    seen_instances = set()
    for module in invalid_modules(rows):
        instance_id = module["instance_id"]
        if instance_id in seen_instances:
            continue
        seen_instances.add(instance_id)
        invalid.append({"id": instance_id, "name": module["name"]})
    return invalid


def workflow_issues(connection: Connection, workflow_id: str) -> list[str]:
    """Return the names of invalid modules in the current workflow version."""
    return list(dict.fromkeys(module["name"] for module in workflow_invalid_modules(connection, workflow_id)))
