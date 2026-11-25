"""Administrator user-management endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from marathon.server.api.deps import current_user, get_engine, require_admin
from marathon.server.auth.sessions import AuthUser
from marathon.server.db import user_writes


router = APIRouter(tags=["users"])


class UserRoleInput(BaseModel):
    role: Literal["user", "admin"]


def _raise_user_error(error: Exception) -> None:
    if isinstance(error, user_writes.UserNotFound):
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden") from error
    if isinstance(error, user_writes.SessionNotFound):
        raise HTTPException(status_code=404, detail="Sitzung nicht gefunden") from error
    if isinstance(error, user_writes.UserProtected):
        raise HTTPException(status_code=409, detail="Notfallzugang kann nicht geändert oder gelöscht werden") from error
    if isinstance(error, user_writes.LastAdmin):
        raise HTTPException(status_code=409, detail="Der letzte Admin kann nicht herabgestuft oder gelöscht werden") from error
    raise error


@router.get("/users")
def get_users(
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> list[dict]:
    """Return all users with administrative metadata."""
    with engine.connect() as connection:
        return user_writes.list_users(connection)


@router.get("/users/directory")
def get_user_directory(
    _user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> list[dict]:
    """Return the minimal directory for the signed-in user."""
    with engine.connect() as connection:
        return user_writes.list_user_directory(connection)


@router.put("/users/{user_id}")
def put_user(
    user_id: str,
    payload: UserRoleInput,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Change a user role and return the updated record."""
    try:
        with engine.begin() as connection:
            return user_writes.set_user_role(connection, user_id, payload.role)
    except (user_writes.UserNotFound, user_writes.UserProtected, user_writes.LastAdmin) as error:
        _raise_user_error(error)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> Response:
    """Delete a user, rejecting self-deletion and protected administrators."""
    if _admin.id == user_id:
        raise HTTPException(status_code=409, detail="Du kannst Dein eigenes Konto nicht löschen")
    try:
        with engine.begin() as connection:
            user_writes.delete_user(connection, user_id)
    except (user_writes.UserNotFound, user_writes.UserProtected, user_writes.LastAdmin) as error:
        _raise_user_error(error)
    return Response(status_code=204)


@router.delete("/users/{user_id}/lock", status_code=204)
def delete_user_lock(
    user_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> Response:
    """Clear the user login lock and failed-code history."""
    with engine.begin() as connection:
        if not user_writes.unlock_user(connection, user_id):
            raise HTTPException(status_code=404, detail="Nutzer nicht gefunden")
    return Response(status_code=204)


@router.get("/users/{user_id}/sessions")
def get_user_sessions(
    user_id: str,
    admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> list[dict]:
    """List active sessions and mark the caller session."""
    try:
        with engine.connect() as connection:
            return user_writes.list_user_sessions(connection, user_id, admin.session_id)
    except user_writes.UserNotFound as error:
        _raise_user_error(error)


@router.delete("/users/{user_id}/sessions/{session_id}", status_code=204)
def delete_user_session(
    user_id: str,
    session_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> Response:
    """Revoke one of the user sessions."""
    try:
        with engine.begin() as connection:
            user_writes.revoke_user_session(connection, user_id, session_id)
    except (user_writes.UserNotFound, user_writes.SessionNotFound) as error:
        _raise_user_error(error)
    return Response(status_code=204)


@router.delete("/users/{user_id}/sessions", status_code=204)
def delete_user_sessions(
    user_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> Response:
    """Revoke all sessions belonging to the user."""
    try:
        with engine.begin() as connection:
            user_writes.revoke_user_sessions(connection, user_id)
    except user_writes.UserNotFound as error:
        _raise_user_error(error)
    return Response(status_code=204)
