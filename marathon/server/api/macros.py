"""Authenticated endpoints for listing and editing macros."""

# IMPORTS
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from marathon.server.api.deps import current_user, get_engine
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.macro_updates import update_outdated_workflows
from marathon.server.db.macro_writes import (
    MacroConflict,
    MacroForbidden,
    MacroInUse,
    StandardMacroDelete,
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
    """Return all non-archived macro hulls visible in the module palette."""
    with engine.begin() as connection:
        return list_macros(connection, user)


@router.get("/macros/{macro_id}")
def get_macro_by_id(
    macro_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return the editable macro detail, hiding missing and archived modules."""
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
    """Create a validated user macro and return its detail."""
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
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Append a macro version and report the user's outdated workflows."""
    try:
        with engine.begin() as connection:
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
    """Archive an unused user macro and return an empty 204 response."""
    try:
        with engine.begin() as connection:
            archived = archive_macro(connection, macro_id, user)
            if not archived:
                raise HTTPException(status_code=404, detail="Modul nicht gefunden")
    except MacroForbidden as exc:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
    except StandardMacroDelete as exc:
        raise HTTPException(status_code=409, detail="Standardmodule können nicht gelöscht werden") from exc
    except MacroInUse as exc:
        raise HTTPException(
            status_code=409,
            detail="Modul wird in Workflows verwendet: " + ", ".join(exc.names),
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/macros/{macro_id}/update-workflows")
def post_update_macro_workflows(
    macro_id: str,
    user: AuthUser = Depends(current_user),
    engine: Engine = Depends(get_engine),
) -> dict[str, list[dict[str, Any]]]:
    """Upgrade the current user's outdated workflows to this macro version."""
    try:
        with engine.begin() as connection:
            macro = get_macro(connection, macro_id, user)
    except MacroForbidden as exc:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
    if macro is None:
        raise HTTPException(status_code=404, detail="Modul nicht gefunden")
    try:
        return update_outdated_workflows(engine, macro_id, user.id)
    except MacroForbidden as exc:
        raise HTTPException(status_code=403, detail="Keine Berechtigung für dieses Modul") from exc
