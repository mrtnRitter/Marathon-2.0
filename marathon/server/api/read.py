"""Read-only dashboard and entity endpoints."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.queries import (
    asset_totals,
    get_workflow,
    list_assets,
    list_module_types,
    list_workflows,
    list_workers,
)


router = APIRouter(tags=["read"])
AssetStatus = Literal["pending", "in_progress", "done", "failed", "on_hold"]


@router.get("/dashboard")
def get_dashboard(engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    """Return workflows, asset totals, and workers for the dashboard."""
    with engine.connect() as connection:
        return {
            "workflows": list_workflows(connection),
            "totals": asset_totals(connection),
            "workers": list_workers(connection),
        }


@router.get("/workflows")
def get_workflows(engine: Engine = Depends(get_engine)) -> list[dict[str, Any]]:
    """Return all workflows and their asset counts."""
    with engine.connect() as connection:
        return list_workflows(connection)


@router.get("/workflows/{workflow_id}")
def get_workflow_by_id(workflow_id: str, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    """Return one workflow or a not-found response."""
    with engine.connect() as connection:
        workflow = get_workflow(connection, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return workflow


@router.get("/module-types")
def get_module_types(engine: Engine = Depends(get_engine)) -> list[dict[str, Any]]:
    """Return all module types."""
    with engine.connect() as connection:
        return list_module_types(connection)


@router.get("/assets")
def get_assets(
    workflow_id: str | None = None,
    status: AssetStatus | None = None,
    engine: Engine = Depends(get_engine),
) -> list[dict[str, Any]]:
    """Return assets with optional workflow and status filters."""
    with engine.connect() as connection:
        return list_assets(connection, workflow_id=workflow_id, status=status)


@router.get("/workers")
def get_workers(engine: Engine = Depends(get_engine)) -> list[dict[str, Any]]:
    """Return workers with their currently assigned job counts."""
    with engine.connect() as connection:
        return list_workers(connection)
