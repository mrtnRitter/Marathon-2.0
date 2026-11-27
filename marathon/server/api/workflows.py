"""Workflow write endpoints."""

# IMPORTS
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.engine import Engine

from marathon.server.api.deps import current_user, get_engine
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.detail_queries import get_graph
from marathon.server.engine.jobs import graph_impact
from marathon.server.db.queries import get_workflow
from marathon.server.db.workflow_access import (
    list_workflow_shares,
    replace_workflow_shares,
    require_workflow_access,
    set_workflow_owner,
    user_exists,
    workflow_owner_id,
)
from marathon.server.db.workflow_writes import (
    resolve_graph,
    WorkflowConflict,
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


class ShareInput(BaseModel):
    """One user and access level in a workflow share replacement."""

    user_id: str
    access: str


class SharesInput(BaseModel):
    """Complete workflow share list replacement."""

    shares: list[ShareInput]


class OwnerInput(BaseModel):
    """New workflow owner identifier."""

    user_id: str


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
                owner_user_id=user.id,
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
            require_workflow_access(connection, user, workflow_id, "view")
            duplicate_id = duplicate_workflow(
                connection, workflow_id, datetime.now(timezone.utc), owner_user_id=user.id
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
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Update workflow metadata and return it within the write transaction."""
    try:
        with engine.begin() as connection:
            require_workflow_access(connection, user, workflow_id, "edit")
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
            require_workflow_access(connection, user, workflow_id, "run")
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
        require_workflow_access(connection, user, workflow_id, "owner")
        deleted = delete_workflow(connection, workflow_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/workflows/{workflow_id}/graph")
def put_workflow_graph(
    workflow_id: str,
    payload: GraphInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Save a new graph version and return it from the same transaction."""
    try:
        with engine.begin() as connection:
            require_workflow_access(connection, user, workflow_id, "edit")
            new_version = save_graph(
                connection,
                workflow_id,
                payload.base_version,
                [module.model_dump() for module in payload.modules],
                [graph_connection.model_dump() for graph_connection in payload.connections],
            )
            if new_version is None:
                raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
            graph = get_graph(connection, workflow_id, user=user)
    except WorkflowConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Workflow wurde zwischenzeitlich geändert. Bitte neu laden.",
        ) from exc
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
        require_workflow_access(connection, user, workflow_id, "edit")
        exists = connection.execute(
            text("SELECT id FROM workflows WHERE id = :id FOR SHARE"), {"id": workflow_id}
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
        try:
            _, _, runtime_modules, runtime_connections = resolve_graph(
                connection,
                workflow_id,
                [module.model_dump() for module in payload.modules],
                [item.model_dump() for item in payload.connections],
            )
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
        current_modules = connection.execute(text(
            "SELECT runtime.id, COALESCE(runtime.instance_id, runtime.id) AS instance_id, "
            "COALESCE(instance.name, runtime.name) AS instance_name "
            "FROM workflow_modules AS runtime "
            "LEFT JOIN workflow_instances AS instance ON instance.workflow_id = runtime.workflow_id "
            "AND instance.version = runtime.version AND instance.id = runtime.instance_id "
            "WHERE runtime.workflow_id = :id AND runtime.version = "
            "(SELECT current_version FROM workflows WHERE id = :id)"
        ), {"id": workflow_id}).mappings()
        instance_by_runtime_id = {
            row["id"]: (row["instance_id"], row["instance_name"])
            for row in current_modules
        }
        grouped = {}
        for removed in impact["removed_modules"]:
            instance_id, instance_name = instance_by_runtime_id.get(
                removed["id"], (removed["id"], removed["name"])
            )
            item = grouped.setdefault(instance_id, {
                "id": instance_id, "name": instance_name, "assets": 0, "jobs": 0,
            })
            item["assets"] += removed["assets"]
            item["jobs"] += removed["jobs"]
        impact["removed_modules"] = list(grouped.values())
        return impact


@router.get("/workflows/{workflow_id}/shares")
def get_workflow_shares(
    workflow_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return shares for a workflow owned by the current user or an admin."""
    with engine.connect() as connection:
        require_workflow_access(connection, user, workflow_id, "owner")
        return {"shares": list_workflow_shares(connection, workflow_id)}


@router.put("/workflows/{workflow_id}/shares")
def put_workflow_shares(
    workflow_id: str,
    payload: SharesInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Replace all workflow shares and return the resulting list."""
    with engine.begin() as connection:
        require_workflow_access(connection, user, workflow_id, "owner")
        owner_id = workflow_owner_id(connection, workflow_id)
        seen: set[str] = set()
        shares = []
        for share in payload.shares:
            if share.user_id in seen:
                raise HTTPException(status_code=422, detail=f"Nutzer doppelt: {share.user_id}")
            seen.add(share.user_id)
            if share.user_id == owner_id:
                raise HTTPException(status_code=422, detail="Der Eigentümer kann nicht freigegeben werden")
            if not user_exists(connection, share.user_id):
                raise HTTPException(status_code=422, detail=f"Unbekannter Nutzer: {share.user_id}")
            if share.access not in ("view", "run", "edit"):
                raise HTTPException(status_code=422, detail=f"Ungültige Freigabestufe: {share.access}")
            shares.append({"user_id": share.user_id, "access": share.access})
        replace_workflow_shares(connection, workflow_id, shares)
        return {"shares": list_workflow_shares(connection, workflow_id)}


@router.put("/workflows/{workflow_id}/owner")
def put_workflow_owner(
    workflow_id: str,
    payload: OwnerInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Set a workflow owner; this route is restricted to administrators."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Nur für Admins")
    with engine.begin() as connection:
        if not user_exists(connection, payload.user_id):
            raise HTTPException(status_code=422, detail=f"Unbekannter Nutzer: {payload.user_id}")
        require_workflow_access(connection, user, workflow_id, "owner")
        set_workflow_owner(connection, workflow_id, payload.user_id)
        workflow = get_workflow(connection, workflow_id, user)
    return workflow
