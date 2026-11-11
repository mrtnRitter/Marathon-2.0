"""Workflow graph, module activity, and asset detail endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.detail_queries import get_asset_detail, get_graph, get_module_activity


router = APIRouter(tags=["detail"])


@router.get("/workflows/{workflow_id}/graph")
def get_workflow_graph(workflow_id: str, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    """Return the current workflow graph or a not-found response."""
    with engine.connect() as connection:
        graph = get_graph(connection, workflow_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return graph


@router.get("/workflows/{workflow_id}/modules/{module_id}/activity")
def get_workflow_module_activity(
    workflow_id: str,
    module_id: str,
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return activity for a current workflow module or a not-found response."""
    with engine.connect() as connection:
        activity = get_module_activity(connection, workflow_id, module_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Modul nicht gefunden")
    return activity


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    """Return one asset with its history and job timeline."""
    with engine.connect() as connection:
        detail = get_asset_detail(connection, asset_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Asset nicht gefunden")
    return detail
