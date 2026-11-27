"""Workflow write endpoints."""

# IMPORTS
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import text
from sqlalchemy.engine import Engine

from marathon.server.api.deps import current_user, get_engine
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.detail_queries import get_graph
from marathon.server.engine.jobs import graph_impact
from marathon.server.db.edit_locks import LockMissing, lock_missing_text, require_lock
from marathon.server.db.object_access import (
    delete_object_access,
    log_intervention,
    object_access_map,
    require_access,
)
from marathon.server.db.queries import blind_module_name, get_workflow
from marathon.server.db.workflow_writes import (
    apply_module_rights,
    resolve_graph,
    WorkflowConflict,
    WorkflowModuleForbidden,
    WorkflowNotStartable,
    WorkflowPausing,
    WorkflowValidationError,
    create_workflow,
    duplicate_workflow,
    delete_workflow,
    set_workflow_active,
    save_graph,
    update_workflow,
)


# MODELS
class ScheduleInput(BaseModel):
    """Schedule interval and first scheduled timestamp."""

    every: int
    unit: str
    start: datetime


class WorkflowInput(BaseModel):
    """Metadata supplied when creating or updating a workflow."""

    name: str
    description: str = ""
    schedule: ScheduleInput | None = None


class ActiveInput(BaseModel):
    """Strict boolean payload for changing a workflow active state."""

    active: StrictBool


class PositionInput(BaseModel):
    """Two-dimensional editor position for a workflow module."""

    x: float
    y: float


class ModuleInput(BaseModel):
    """Editable module data stored in a workflow graph version."""

    id: str
    type_id: str
    macro_version: int | None = None
    name: str
    description: str = ""
    position: PositionInput
    settings: dict[str, Any] = Field(default_factory=dict)
    locked: bool = False


class ConnectionInput(BaseModel):
    """Directed edge from a module output to a target module."""

    source_module_id: str
    source_output_id: str
    target_module_id: str


class GraphInput(BaseModel):
    """Graph payload based on the version currently shown in the editor."""

    base_version: int
    modules: list[ModuleInput]
    connections: list[ConnectionInput]


# ROUTES
router = APIRouter(tags=["workflows"])


@router.post("/workflows", status_code=status.HTTP_201_CREATED)
def post_workflow(
    payload: WorkflowInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Create a workflow and return its initial metadata in the same transaction."""
    try:
        with engine.begin() as connection:
            workflow_id = create_workflow(
                connection,
                payload.name,
                payload.description,
                payload.schedule.model_dump() if payload.schedule else None,
                datetime.now(timezone.utc),
                owner_user_id=None if user.is_admin else user.id,
            )
            workflow = get_workflow(connection, workflow_id, user)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return workflow


@router.post("/workflows/{workflow_id}/duplicate", status_code=status.HTTP_201_CREATED)
def post_duplicate_workflow(
    workflow_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Duplicate a workflow and return its metadata in the same transaction."""
    try:
        with engine.begin() as connection:
            require_access(connection, user, "workflow", workflow_id, "view")
            duplicate_id = duplicate_workflow(
                connection,
                workflow_id,
                datetime.now(timezone.utc),
                owner_user_id=None if user.is_admin else user.id,
            )
            if duplicate_id is None:
                raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
            workflow = get_workflow(connection, duplicate_id, user)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return workflow


@router.put("/workflows/{workflow_id}")
def put_workflow(
    workflow_id: str,
    payload: WorkflowInput,
    edit_lock: str | None = Header(default=None, alias="X-Edit-Lock"),
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Update workflow metadata and return it within the write transaction."""
    try:
        with engine.begin() as connection:
            require_access(connection, user, "workflow", workflow_id, "edit")
            try:
                require_lock(connection, "workflow", workflow_id, edit_lock)
            except LockMissing as exc:
                raise HTTPException(status_code=409, detail=lock_missing_text) from exc
            updated = update_workflow(
                connection,
                workflow_id,
                payload.name,
                payload.description,
                payload.schedule.model_dump() if payload.schedule else None,
                datetime.now(timezone.utc),
            )
            if not updated:
                raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
            log_intervention(connection, user, "workflow", workflow_id, "Bearbeitet")
            workflow = get_workflow(connection, workflow_id, user)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return workflow


@router.put("/workflows/{workflow_id}/active")
def put_workflow_active(
    workflow_id: str,
    payload: ActiveInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Set a workflow's active state and return its metadata."""
    try:
        with engine.begin() as connection:
            require_access(connection, user, "workflow", workflow_id, "use")
            updated = set_workflow_active(connection, workflow_id, payload.active)
            if not updated:
                raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
            workflow = get_workflow(connection, workflow_id, user)
    except WorkflowNotStartable as exc:
        raise HTTPException(
            status_code=409,
            detail="Workflow kann nicht gestartet werden. " + str(exc),
        ) from exc
    except WorkflowPausing as exc:
        raise HTTPException(status_code=409, detail="Workflow wird gerade gestoppt") from exc
    return workflow


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow_by_id(
    workflow_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> Response:
    """Delete a workflow and its dependent data, returning an empty 204 response."""
    with engine.begin() as connection:
        require_access(connection, user, "workflow", workflow_id, "owner")
        log_intervention(connection, user, "workflow", workflow_id, "Gelöscht")
        delete_object_access(connection, "workflow", workflow_id)
        deleted = delete_workflow(connection, workflow_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/workflows/{workflow_id}/graph")
def put_workflow_graph(
    workflow_id: str,
    payload: GraphInput,
    edit_lock: str | None = Header(default=None, alias="X-Edit-Lock"),
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Save a new graph version and return it from the same transaction."""
    try:
        with engine.begin() as connection:
            require_access(connection, user, "workflow", workflow_id, "edit")
            try:
                require_lock(connection, "workflow", workflow_id, edit_lock)
            except LockMissing as exc:
                raise HTTPException(status_code=409, detail=lock_missing_text) from exc
            new_version = save_graph(
                connection,
                workflow_id,
                payload.base_version,
                [module.model_dump() for module in payload.modules],
                [item.model_dump() for item in payload.connections],
                user=user,
            )
            if new_version is None:
                raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
            log_intervention(connection, user, "workflow", workflow_id, "Bearbeitet")
            graph = get_graph(connection, workflow_id, user=user)
    except WorkflowConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Workflow wurde zwischenzeitlich geändert. Bitte neu laden.",
        ) from exc
    except WorkflowModuleForbidden as exc:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return graph


@router.post("/workflows/{workflow_id}/graph/impact")
def post_workflow_graph_impact(
    workflow_id: str,
    payload: GraphInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return impact for removed runtime modules, grouped by module instance."""
    with engine.begin() as connection:
        require_access(connection, user, "workflow", workflow_id, "edit")
        exists = connection.execute(
            text("SELECT id FROM workflows WHERE id = :id FOR SHARE"), {"id": workflow_id}
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
        try:
            modules = apply_module_rights(
                connection,
                workflow_id,
                [module.model_dump() for module in payload.modules],
                user,
            )
            _, _, runtime_modules, runtime_connections = resolve_graph(
                connection,
                workflow_id,
                modules,
                [item.model_dump() for item in payload.connections],
            )
        except WorkflowModuleForbidden as exc:
            raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
        except WorkflowValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        impact = graph_impact(
            connection,
            workflow_id,
            [module["id"] for module in runtime_modules],
            [
                (item["source_module_id"], item["source_output_id"], item["target_module_id"])
                for item in runtime_connections
            ],
        )
        current_modules = connection.execute(
            text(
                "SELECT runtime.id, COALESCE(runtime.instance_id, runtime.id) AS instance_id, "
                "COALESCE(instance.name, runtime.name) AS instance_name, instance.macro_id "
                "FROM workflow_modules AS runtime "
                "LEFT JOIN workflow_instances AS instance ON instance.workflow_id = runtime.workflow_id "
                "AND instance.version = runtime.version AND instance.id = runtime.instance_id "
                "WHERE runtime.workflow_id = :id AND runtime.version = "
                "(SELECT current_version FROM workflows WHERE id = :id)"
            ),
            {"id": workflow_id},
        ).mappings()
        instance_by_runtime_id = {
            row["id"]: (row["instance_id"], row["instance_name"], row["macro_id"])
            for row in current_modules
        }
        removed_modules = impact["removed_modules"]
        macro_ids = [
            instance_by_runtime_id[item["id"]][2]
            for item in removed_modules
            if item["id"] in instance_by_runtime_id
            and instance_by_runtime_id[item["id"]][2] is not None
        ]
        module_access = object_access_map(connection, user, "macro", macro_ids)
        grouped = {}
        for removed in removed_modules:
            instance_id, instance_name, macro_id = instance_by_runtime_id.get(
                removed["id"], (removed["id"], removed["name"], None)
            )
            if macro_id is not None and module_access.get(macro_id) is None:
                instance_name = blind_module_name
            item = grouped.setdefault(
                instance_id,
                {"id": instance_id, "name": instance_name, "assets": 0, "jobs": 0},
            )
            item["assets"] += removed["assets"]
            item["jobs"] += removed["jobs"]
        impact["removed_modules"] = list(grouped.values())
        return impact
