"""Workflow write endpoints."""

# IMPORTS
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.detail_queries import get_graph
from marathon.server.db.queries import get_workflow
from marathon.server.db.workflow_writes import (
    WorkflowConflict,
    WorkflowValidationError,
    create_workflow,
    delete_workflow,
    save_graph,
    update_workflow,
)


# MODELS
class WorkflowInput(BaseModel):
    """Metadata supplied when creating or updating a workflow."""

    name: str
    description: str = ""
    priority: int = 0


class PositionInput(BaseModel):
    """Two-dimensional editor position for a workflow module."""

    x: float
    y: float


class ModuleInput(BaseModel):
    """Editable module data stored in a workflow graph version."""

    id: str
    type_id: str
    name: str
    description: str = ""
    position: PositionInput
    settings: dict[str, str | int | float | bool] = Field(default_factory=dict)
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
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Create a workflow and return its initial metadata in the same transaction."""
    try:
        with engine.begin() as connection:
            workflow_id = create_workflow(
                connection, payload.name, payload.description, payload.priority
            )
            workflow = get_workflow(connection, workflow_id)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return workflow


@router.put("/workflows/{workflow_id}")
def put_workflow(
    workflow_id: str,
    payload: WorkflowInput,
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Update workflow metadata and return it within the write transaction."""
    try:
        with engine.begin() as connection:
            updated = update_workflow(
                connection,
                workflow_id,
                payload.name,
                payload.description,
                payload.priority,
            )
            if not updated:
                raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
            workflow = get_workflow(connection, workflow_id)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return workflow


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow_by_id(workflow_id: str, engine: Engine = Depends(get_engine)) -> Response:
    """Delete a workflow and its dependent data, returning an empty 204 response."""
    with engine.begin() as connection:
        deleted = delete_workflow(connection, workflow_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/workflows/{workflow_id}/graph")
def put_workflow_graph(
    workflow_id: str,
    payload: GraphInput,
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Save a new graph version and return it from the same transaction."""
    try:
        with engine.begin() as connection:
            new_version = save_graph(
                connection,
                workflow_id,
                payload.base_version,
                [module.model_dump() for module in payload.modules],
                [graph_connection.model_dump() for graph_connection in payload.connections],
            )
            if new_version is None:
                raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
            graph = get_graph(connection, workflow_id)
    except WorkflowConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Workflow wurde zwischenzeitlich geändert. Bitte neu laden.",
        ) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return graph
