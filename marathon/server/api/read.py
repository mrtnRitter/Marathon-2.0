"""Read-only dashboard and entity endpoints."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from marathon.server.api.deps import current_user, get_engine
from marathon.server.auth.sessions import AuthUser
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
def get_dashboard(
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return accessible workflows, visible asset totals, and workers."""
    with engine.connect() as connection:
        return {
            "workflows": list_workflows(connection, user),
            "totals": asset_totals(connection, user),
            "workers": list_workers(connection),
        }


@router.get("/workflows")
def get_workflows(
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> list[dict[str, Any]]:
    """Return visible workflows and their asset counts."""
    with engine.connect() as connection:
        return list_workflows(connection, user)


@router.get("/workflows/{workflow_id}")
def get_workflow_by_id(
    workflow_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return a visible workflow or a not-found response."""
    with engine.connect() as connection:
        workflow = get_workflow(connection, workflow_id, user)
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
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> list[dict[str, Any]]:
    """Return assets from accessible workflows with optional filters."""
    with engine.connect() as connection:
        return list_assets(connection, workflow_id=workflow_id, status=status, user=user)


@router.get("/workers")
def get_workers(engine: Engine = Depends(get_engine)) -> list[dict[str, Any]]:
    """Return workers with their currently assigned job counts."""
    with engine.connect() as connection:
        return list_workers(connection)
