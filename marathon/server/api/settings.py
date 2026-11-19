"""Application settings endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.engine import Connection, Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.settings_writes import (
    SettingsValidationError,
    get_settings,
    save_settings,
)


class FolderDialogInput(BaseModel):
    """Folder-dialog settings supplied by the client."""

    server_address: str


class SettingsInput(BaseModel):
    """Settings supplied by the client."""

    folder_dialog: FolderDialogInput


router = APIRouter(tags=["settings"])


@router.get("/settings")
def read_settings(engine: Engine = Depends(get_engine)) -> dict:
    """Return the saved application settings."""
    with engine.connect() as connection:
        return get_settings(connection)


@router.put("/settings")
def put_settings(payload: SettingsInput, engine: Engine = Depends(get_engine)) -> dict:
    """Validate and save application settings."""
    try:
        with engine.begin() as connection:
            return save_settings(connection, payload.folder_dialog.server_address)
    except SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
