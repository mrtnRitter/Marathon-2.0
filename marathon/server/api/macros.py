"""Authenticated endpoints for listing and editing macros."""

# IMPORTS
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from marathon.server.api.deps import current_user, get_engine
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.edit_locks import LockMissing, require_lock
from marathon.server.db.macro_updates import update_outdated_workflows
from marathon.server.db.object_access import require_access
from marathon.server.db.macro_writes import (
    MacroConflict,
    MacroForbidden,
    archive_macro,
    create_macro,
    get_macro,
    list_macros,
    outdated_workflows,
    update_macro,
)
from marathon.server.macros.definition import MacroValidationError


# MODELS
class MacroInput(BaseModel):
    """Definition supplied when creating a macro."""

    definition: dict[str, Any]


class MacroUpdateInput(BaseModel):
    """Versioned definition supplied when updating a macro."""

    base_version: int
    definition: dict[str, Any]


# ROUTES
router = APIRouter(tags=["macros"])


@router.get("/macros")
def get_macros(
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> list[dict[str, Any]]:
    """Return all non-archived macro hulls visible at view level."""
    with engine.begin() as connection:
        return list_macros(connection, user)


@router.get("/macros/{macro_id}")
def get_macro_by_id(
    macro_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return the editable macro detail, hiding missing and inaccessible modules."""
    try:
        with engine.begin() as connection:
            macro = get_macro(connection, macro_id, user)
    except MacroForbidden as exc:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
    if macro is None:
        raise HTTPException(status_code=404, detail="Modul nicht gefunden")
    return macro


@router.post("/macros", status_code=status.HTTP_201_CREATED)
def post_macro(
    payload: MacroInput,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Create a private macro and return its detail."""
    try:
        with engine.begin() as connection:
            macro_id = create_macro(connection, payload.definition, user)
            macro = get_macro(connection, macro_id, user)
    except MacroValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if macro is None:
        raise HTTPException(status_code=404, detail="Modul nicht gefunden")
    return macro


@router.put("/macros/{macro_id}")
def put_macro(
    macro_id: str,
    payload: MacroUpdateInput,
    edit_lock: str | None = Header(default=None, alias="X-Edit-Lock"),
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Append a macro version after access, lock, and base-version checks."""
    try:
        with engine.begin() as connection:
            macro = get_macro(connection, macro_id, user)
            if macro is None:
                raise HTTPException(status_code=404, detail="Modul nicht gefunden")
            require_lock(connection, "macro", macro_id, edit_lock)
            version = update_macro(
                connection, macro_id, payload.base_version, payload.definition, user
            )
            if version is None:
                raise HTTPException(status_code=404, detail="Modul nicht gefunden")
            macro = get_macro(connection, macro_id, user)
            if macro is None:
                raise HTTPException(status_code=404, detail="Modul nicht gefunden")
            macro["outdated_workflows"] = outdated_workflows(connection, macro_id, user)
    except MacroForbidden as exc:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
    except LockMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MacroConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Modul wurde zwischenzeitlich geändert. Bitte neu laden.",
        ) from exc
    except MacroValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return macro


@router.delete("/macros/{macro_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_macro(
    macro_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> Response:
    """Archive an owned macro and return an empty 204 response."""
    try:
        with engine.begin() as connection:
            archived = archive_macro(connection, macro_id, user)
            if not archived:
                raise HTTPException(status_code=404, detail="Modul nicht gefunden")
    except MacroForbidden as exc:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/macros/{macro_id}/update-workflows")
def post_update_macro_workflows(
    macro_id: str,
    force: bool = False,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, list[dict[str, Any]]]:
    """Upgrade outdated workflows to this macro version."""
    with engine.begin() as connection:
        require_access(connection, user, "macro", macro_id, "edit")
    if force and not user.is_admin:
        raise HTTPException(status_code=403, detail="Nur für Admins")
    with engine.begin() as connection:
        macro = get_macro(connection, macro_id, user)
    if macro is None:
        raise HTTPException(status_code=404, detail="Modul nicht gefunden")
    try:
        return update_outdated_workflows(engine, macro_id, user, force)
    except MacroForbidden as exc:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
