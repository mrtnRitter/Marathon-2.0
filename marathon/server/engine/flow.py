"""Load workflow graphs and route assets through their modules."""

# IMPORTS
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection




# DATACLASSES
@dataclass(frozen=True)
class GraphModule:
    """A module and its asset-routing capabilities in one graph version."""

    id: str
    name: str
    type_id: str
    settings: dict[str, Any]
    execution: str
    has_input: bool
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class Graph:
    """A workflow graph version with its asset routes."""

    workflow_id: str
    version: int
    modules: dict[str, GraphModule]
    routes: dict[tuple[str, str], str]


# FUNC

def load_graph(connection: Connection, workflow_id: str) -> Graph | None:
    """Load the current workflow graph, excluding data-only outputs."""
    workflow = connection.execute(
        text("SELECT current_version FROM workflows WHERE id = :workflow_id"),
        {"workflow_id": workflow_id},
    ).mappings().one_or_none()
    if workflow is None:
        return None

    version = workflow["current_version"]
    rows = connection.execute(
        text(
            "SELECT wm.id, wm.name, wm.type_id, wm.settings, mt.execution, mt.has_input, mt.outputs "
            "FROM workflow_modules AS wm "
            "JOIN module_types AS mt ON mt.id = wm.type_id "
            "WHERE wm.workflow_id = :workflow_id AND wm.version = :version"
        ),
        {"workflow_id": workflow_id, "version": version},
    ).mappings()
    modules = {}
    for row in rows:
        outputs = tuple(
            output["id"]
            for output in row["outputs"]
            if isinstance(output, dict) and output.get("id") and not output.get("data", False)
        )
        modules[row["id"]] = GraphModule(
            id=row["id"],
            name=row["name"],
            type_id=row["type_id"],
            settings=row["settings"],
            execution=row["execution"],
            has_input=row["has_input"],
            outputs=outputs,
        )

    route_rows = connection.execute(
        text(
            "SELECT source_module_id, source_output_id, target_module_id "
            "FROM workflow_connections "
            "WHERE workflow_id = :workflow_id AND version = :version"
        ),
        {"workflow_id": workflow_id, "version": version},
    ).mappings()
    routes = {
        (row["source_module_id"], row["source_output_id"]): row["target_module_id"]
        for row in route_rows
        if row["source_module_id"] in modules
        and row["source_output_id"] in modules[row["source_module_id"]].outputs
    }
    return Graph(workflow_id=workflow_id, version=version, modules=modules, routes=routes)


def asset_file_path(metadata: Mapping[str, Any]) -> str | None:
    """Return the current asset path, falling back to its original source path."""
    return metadata.get("path") or metadata.get("source_path")


def _load_asset(connection: Connection, asset_id: str) -> dict[str, Any]:
    asset = connection.execute(
        text(
            "SELECT id, workflow_id, status, current_module_id, step_status, from_module_id, "
            "from_output_id, attempts FROM assets WHERE id = :asset_id FOR UPDATE"
        ),
        {"asset_id": asset_id},
    ).mappings().one_or_none()
    if asset is None:
        raise LookupError(f"Asset nicht gefunden: {asset_id}")
    return dict(asset)


def _write_asset(connection: Connection, asset_id: str, fields: dict[str, Any]) -> None:
    assignments = ", ".join(f"{field} = :{field}" for field in fields)
    connection.execute(
        text(f"UPDATE assets SET {assignments}, updated_at = now() WHERE id = :asset_id"),
        {"asset_id": asset_id, **fields},
    )


def _history(
    connection: Connection,
    asset_id: str,
    kind: str,
    message: str,
    module_id: str | None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO asset_history (asset_id, module_id, kind, text) "
            "VALUES (:asset_id, :module_id, :kind, :text)"
        ),
        {"asset_id": asset_id, "module_id": module_id, "kind": kind, "text": message},
    )


def _write_asset_with_history(
    connection: Connection,
    asset_id: str,
    fields: dict[str, Any],
    history: tuple[str, str],
    module_id: str | None,
) -> None:
    _write_asset(connection, asset_id, fields)
    _history(connection, asset_id, history[0], history[1], module_id)


def _fail_locked_asset(connection: Connection, asset: dict[str, Any], message: str) -> None:
    _write_asset_with_history(
        connection,
        asset["id"],
        {"status": "failed", "step_status": None, "wait_until": None},
        ("error", message),
        asset["current_module_id"],
    )
    connection.execute(
        text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, asset_id, level, text) "
            "VALUES (:workflow_id, :module_id, NULL, :asset_id, 'error', :text)"
        ),
        {
            "workflow_id": asset["workflow_id"],
            "module_id": asset["current_module_id"],
            "asset_id": asset["id"],
            "text": message,
        },
    )


def fail_asset(connection: Connection, asset_id: str, message: str) -> None:
    """Fail an asset and write its error history and workflow log entry."""
    _fail_locked_asset(connection, _load_asset(connection, asset_id), message)


def _route_locked_asset(
    connection: Connection,
    asset: dict[str, Any],
    graph: Graph,
    from_module_id: str,
    output_id: str | None,
    keep_attempts: bool = False,
) -> None:
    module = graph.modules.get(from_module_id)
    if module is None:
        _fail_locked_asset(connection, asset, "Modul nicht mehr im Workflow")
        return

    target_module_id = graph.routes.get((from_module_id, output_id))
    if target_module_id is not None:
        fields = {
            "current_module_id": target_module_id,
            "step_status": "waiting",
            "status": "in_progress",
            "from_module_id": from_module_id,
            "from_output_id": output_id,
            "wait_until": None,
        }
        if not keep_attempts:
            fields["attempts"] = 0
        _write_asset(connection, asset["id"], fields)
        return

    fields = {
        "current_module_id": from_module_id,
        "step_status": None,
        "wait_until": None,
        "status": "failed" if output_id == "error" else "done",
    }
    history = (
        ("error", f"Fehlgeschlagen an „{module.name}“")
        if output_id == "error"
        else ("success", "Workflow abgeschlossen")
    )
    _write_asset_with_history(connection, asset["id"], fields, history, from_module_id)


def route_asset(
    connection: Connection,
    asset_id: str,
    graph: Graph,
    from_module_id: str,
    output_id: str,
    keep_attempts: bool = False,
) -> None:
    """Route an asset to the connected module or finish its workflow run."""
    _route_locked_asset(
        connection,
        _load_asset(connection, asset_id),
        graph,
        from_module_id,
        output_id,
        keep_attempts,
    )


def return_asset(connection: Connection, asset_id: str, graph: Graph) -> None:
    """Return an asset to its current module or reroute it from its previous module."""
    asset = _load_asset(connection, asset_id)
    from_module_id = asset["from_module_id"]
    if from_module_id is None:
        current_module_id = asset["current_module_id"]
        if current_module_id in graph.modules:
            _write_asset(
                connection,
                asset_id,
                {"status": "in_progress", "step_status": "waiting", "wait_until": None},
            )
            return
        _fail_locked_asset(connection, asset, "Modul nicht mehr im Workflow")
        return

    target_module_id = graph.routes.get((from_module_id, asset["from_output_id"]))
    _route_locked_asset(
        connection,
        asset,
        graph,
        from_module_id,
        asset["from_output_id"],
        keep_attempts=target_module_id == asset["current_module_id"],
    )


def enter_asset(connection: Connection, asset_id: str, graph: Graph) -> None:
    """Place a pending asset at the unique workflow start or fail if ambiguous."""
    asset = _load_asset(connection, asset_id)
    if asset["status"] != "pending":
        return
    _write_asset(connection, asset_id, {"status": "in_progress"})

    current_module_id = asset["current_module_id"]
    if current_module_id is not None:
        module = graph.modules.get(current_module_id)
        if module is None:
            _fail_locked_asset(connection, asset, "Modul nicht mehr im Workflow")
        elif not module.has_input:
            _route_locked_asset(connection, asset, graph, current_module_id, "success")
        else:
            _write_asset(
                connection,
                asset_id,
                {"status": "in_progress", "step_status": "waiting", "wait_until": None},
            )
        return

    sources = [
        module
        for module in graph.modules.values()
        if not module.has_input and "success" in module.outputs
    ]
    if len(sources) == 1:
        _route_locked_asset(connection, asset, graph, sources[0].id, "success")
        return
    if not sources:
        incoming = set(graph.routes.values())
        starts = [
            module for module in graph.modules.values()
            if module.has_input and module.id not in incoming
        ]
        if len(starts) == 1:
            _write_asset(
                connection,
                asset_id,
                {
                    "current_module_id": starts[0].id,
                    "from_module_id": None,
                    "step_status": "waiting",
                    "wait_until": None,
                },
            )
            return
    _fail_locked_asset(connection, asset, "Startpunkt nicht eindeutig")
