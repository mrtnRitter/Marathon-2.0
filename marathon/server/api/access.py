"""Unified sharing, ownership, history, and edit-lock API."""

# IMPORTS
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from marathon.server.api.deps import current_user, get_engine
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.edit_locks import LockHeld, acquire_lock, break_lock, lock_status, release_lock
from marathon.server.db.object_access import (
    ShareValidationError,
    list_events,
    list_shares,
    log_event,
    log_intervention,
    owner_display,
    owner_record,
    object_types,
    replace_shares,
    require_access,
    set_owner,
)


# CONFIG
app_name = "marathon-access-api"
app_version = "1.0"
router = APIRouter(tags=["access"])


# TYPES
class ShareInput(BaseModel):
    """A single user or global access grant."""

    user_id: str | None
    access: str


class SharesInput(BaseModel):
    """Replacement list of object access grants."""

    shares: list[ShareInput]


class OwnerInput(BaseModel):
    """New user owner, or null for admin-only ownership."""

    user_id: str | None


# FUNC
def _prepare_object(
    connection: Connection,
    user: AuthUser,
    object_type: str,
    object_id: str,
    minimum: str,
    admin_only: bool = False,
) -> None:
    """Validate object type and visibility in the specified contract order."""
    if object_type not in object_types:
        raise HTTPException(status_code=404, detail="Unbekannter Objekttyp")
    if admin_only and not user.is_admin:
        raise HTTPException(status_code=403, detail="Nur für Admins")
    if object_type == "macro":
        exists = connection.execute(
            text("SELECT 1 FROM macros WHERE id = :object_id AND archived_at IS NULL"),
            {"object_id": object_id},
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail="Modul nicht gefunden")
    require_access(connection, user, object_type, object_id, minimum)


def _shares_response(connection: Connection, object_type: str, object_id: str) -> dict[str, Any]:
    return {
        "owner": owner_record(connection, object_type, object_id),
        "shares": list_shares(connection, object_type, object_id),
    }


def _raise_share_validation(error: ShareValidationError) -> None:
    raise HTTPException(status_code=422, detail=str(error)) from error


# ROUTES
@router.get("/access/{object_type}/{object_id}/shares")
def get_shares(
    object_type: str,
    object_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    with engine.begin() as connection:
        _prepare_object(connection, user, object_type, object_id, "owner")
        return _shares_response(connection, object_type, object_id)


@router.put("/access/{object_type}/{object_id}/shares")
def put_shares(
    object_type: str,
    object_id: str,
    body: SharesInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    with engine.begin() as connection:
        _prepare_object(connection, user, object_type, object_id, "owner")
        try:
            replace_shares(
                connection,
                object_type,
                object_id,
                [share.model_dump() for share in body.shares],
            )
        except ShareValidationError as error:
            _raise_share_validation(error)
        log_intervention(connection, user, object_type, object_id, "Freigaben geändert")
        return _shares_response(connection, object_type, object_id)


@router.put("/access/{object_type}/{object_id}/owner")
def put_owner(
    object_type: str,
    object_id: str,
    body: OwnerInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    with engine.begin() as connection:
        _prepare_object(connection, user, object_type, object_id, "owner", admin_only=True)
        old_owner = owner_record(connection, object_type, object_id)
        try:
            set_owner(connection, object_type, object_id, body.user_id)
        except ShareValidationError as error:
            _raise_share_validation(error)
        new_owner = owner_record(connection, object_type, object_id)
        log_event(
            connection,
            user,
            object_type,
            object_id,
            f"Besitzer geändert: {owner_display(old_owner)} → {owner_display(new_owner)}",
        )
        return _shares_response(connection, object_type, object_id)


@router.get("/access/{object_type}/{object_id}/events")
def get_events(
    object_type: str,
    object_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    with engine.begin() as connection:
        _prepare_object(connection, user, object_type, object_id, "owner")
        return {"events": list_events(connection, object_type, object_id)}


@router.post("/access/{object_type}/{object_id}/lock")
def post_lock(
    object_type: str,
    object_id: str,
    request: Request,
    token: str | None = Header(default=None, alias="X-Edit-Lock"),
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    with engine.begin() as connection:
        _prepare_object(connection, user, object_type, object_id, "edit")
        try:
            return acquire_lock(
                connection,
                user,
                object_type,
                object_id,
                token,
                request.app.state.edit_lock.ttl_s,
            )
        except LockHeld as error:
            raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/access/{object_type}/{object_id}/lock")
def get_lock(
    object_type: str,
    object_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    with engine.begin() as connection:
        _prepare_object(connection, user, object_type, object_id, "view")
        return lock_status(connection, object_type, object_id)


@router.delete("/access/{object_type}/{object_id}/lock", status_code=status.HTTP_204_NO_CONTENT)
def delete_lock(
    object_type: str,
    object_id: str,
    force: bool = False,
    token: str | None = Header(default=None, alias="X-Edit-Lock"),
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> Response:
    with engine.begin() as connection:
        _prepare_object(connection, user, object_type, object_id, "view", admin_only=force)
        if not force:
            release_lock(connection, object_type, object_id, token)
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        broken = break_lock(connection, object_type, object_id)
        if broken is not None and not broken["expired"] and broken["user_id"] != user.id:
            log_event(
                connection,
                user,
                object_type,
                object_id,
                f"Bearbeitungssperre von {broken['name']} aufgehoben",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
