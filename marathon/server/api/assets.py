"""Asset write endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.asset_writes import (
    AssetConflict,
    AssetNotFound,
    AssetValidationError,
    reprocess_assets,
)


class ReprocessInput(BaseModel):
    """Asset IDs and optional destination workflow for reprocessing."""

    asset_ids: list[str]
    target_workflow_id: str | None = None


router = APIRouter(tags=["assets"])


@router.post("/assets/reprocess")
def post_assets_reprocess(
    payload: ReprocessInput,
    engine: Engine = Depends(get_engine),
) -> dict[str, list[str]]:
    """Reprocess assets or move them to a different workflow atomically."""
    try:
        with engine.begin() as connection:
            asset_ids = reprocess_assets(connection, payload.asset_ids, payload.target_workflow_id)
    except AssetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AssetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"asset_ids": asset_ids}
