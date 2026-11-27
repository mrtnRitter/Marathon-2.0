"""Expand macro instances into runtime graph modules and connections."""

# IMPORTS
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


# FUNC

def runtime_id(instance_id: str, block_id: str) -> str:
    """Return the runtime identifier for one block in a module instance."""
    return f"{instance_id}.{block_id}"


def block_settings(
    definition: Mapping[str, Any],
    block_id: str,
    instance_settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy fixed block settings and apply the instance's exposed values."""
    block = next(block for block in definition["blocks"] if block["id"] == block_id)
    settings = deepcopy(block["settings"])
    for setting in definition.get("settings", []):
        if setting["block_id"] == block_id and setting["key"] in instance_settings:
            settings[setting["setting_key"]] = deepcopy(instance_settings[setting["key"]])
    return settings


def _block_exits(definition: Mapping[str, Any], block_id: str) -> dict[str, str]:
    exits = {}
    for output in definition.get("outputs", []):
        for source in output.get("sources", []):
            if source["block_id"] == block_id:
                exits[source["output_id"]] = output["id"]
    return exits


def expand_graph(
    instances: Sequence[Mapping[str, Any]],
    connections: Sequence[Mapping[str, Any]],
    definitions: Mapping[tuple[str, int], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Expand module instances and their connections into the runtime graph."""
    modules = []
    runtime_connections = []
    instances_by_id = {}

    for instance in instances:
        definition = definitions[(instance["macro_id"], instance["macro_version"])]
        instances_by_id[instance["id"]] = (instance, definition)
        for block in definition["blocks"]:
            modules.append({
                "id": runtime_id(instance["id"], block["id"]),
                "instance_id": instance["id"],
                "block_id": block["id"],
                "type_id": block["type_id"],
                "name": instance["name"],
                "description": "",
                "position": deepcopy(instance["position"]),
                "settings": block_settings(definition, block["id"], instance.get("settings", {})),
                "locked": instance.get("locked", False),
                "exits": _block_exits(definition, block["id"]),
            })
        for connection in definition.get("connections", []):
            runtime_connections.append({
                "source_module_id": runtime_id(instance["id"], connection["source_block_id"]),
                "source_output_id": connection["source_output_id"],
                "target_module_id": runtime_id(instance["id"], connection["target_block_id"]),
            })

    for connection in connections:
        source_id = connection["source_module_id"]
        target_id = connection["target_module_id"]
        if source_id not in instances_by_id:
            raise ValueError(f"Verbindung zu unbekanntem Modul: {source_id}")
        if target_id not in instances_by_id:
            raise ValueError(f"Verbindung zu unbekanntem Modul: {target_id}")

        source_instance, source_definition = instances_by_id[source_id]
        target_instance, target_definition = instances_by_id[target_id]
        output_id = connection["source_output_id"]
        output = next((item for item in source_definition.get("outputs", []) if item["id"] == output_id), None)
        if output is None:
            raise ValueError(f"Unbekannter Ausgang {output_id} an Modul {source_id}")
        entry_block_id = target_definition.get("entry_block_id")
        if entry_block_id is None:
            raise ValueError(f"Modul {target_id} hat keinen Eingang")
        for source in output.get("sources", []):
            runtime_connections.append({
                "source_module_id": runtime_id(source_id, source["block_id"]),
                "source_output_id": source["output_id"],
                "target_module_id": runtime_id(target_id, entry_block_id),
            })

    return modules, runtime_connections


def remap_settings(
    settings: Mapping[str, Any],
    hull: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Keep settings exposed by a hull and list removed keys in sorted order."""
    allowed = {item["key"] for item in hull.get("settings", [])}
    retained = {key: deepcopy(value) for key, value in settings.items() if key in allowed}
    removed = sorted(key for key in settings if key not in allowed)
    return retained, removed
