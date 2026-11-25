"""Asset write endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from marathon.server.api.deps import current_user, get_engine
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.workflow_access import require_workflow_access
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
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, list[str]]:
    """Reprocess assets or move them to a different workflow atomically."""
    try:
        with engine.begin() as connection:
            unique_ids = list(dict.fromkeys(payload.asset_ids))
            rows = connection.execute(
                text("SELECT id, workflow_id FROM assets WHERE id = ANY(:asset_ids)").bindparams(
                    bindparam("asset_ids", type_=ARRAY(Text()))
                ),
                {"asset_ids": unique_ids},
            ).mappings()
            source_workflow_ids = dict.fromkeys(row["workflow_id"] for row in rows)
            if unique_ids:
                for workflow_id in source_workflow_ids:
                    require_workflow_access(connection, user, workflow_id, "run")
                if payload.target_workflow_id is not None:
                    target_exists = connection.execute(
                        text("SELECT 1 FROM workflows WHERE id = :workflow_id"),
                        {"workflow_id": payload.target_workflow_id},
                    ).scalar_one_or_none()
                    if target_exists is not None:
                        require_workflow_access(connection, user, payload.target_workflow_id, "run")
            asset_ids = reprocess_assets(connection, payload.asset_ids, payload.target_workflow_id)
    except AssetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AssetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"asset_ids": asset_ids}
