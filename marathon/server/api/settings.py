"""Application settings endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool
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


class ProxyInput(BaseModel):
    """Proxy settings supplied by the client."""

    public_url: str
    builtin: StrictBool
    trusted_proxies: list[str]


class AuthInput(BaseModel):
    """Authentication settings supplied by the client."""

    allowed_domains: list[str]


class SettingsInput(BaseModel):
    """Settings supplied by the client."""

    folder_dialog: FolderDialogInput
    proxy: ProxyInput
    auth: AuthInput


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
            return save_settings(
                connection,
                payload.folder_dialog.server_address,
                public_url=payload.proxy.public_url,
                builtin=payload.proxy.builtin,
                trusted_proxies=payload.proxy.trusted_proxies,
                allowed_domains=payload.auth.allowed_domains,
            )
    except SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
