"""System health and restart endpoints."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from marathon.common.version import app_name, app_version
from marathon.server.api.deps import require_admin
from marathon.server.auth.sessions import AuthUser


router = APIRouter(tags=["system"])


@router.get("/health")
def get_health() -> dict[str, str]:
    """Return the server health and version."""
    return {"status": "ok", "app": app_name, "version": app_version}


@router.post("/server/restart", status_code=202)
def post_server_restart(
    request: Request,
    background_tasks: BackgroundTasks,
    _admin: AuthUser = Depends(require_admin),
) -> dict[str, str]:
    """Schedule a clean restart after the response has been sent."""
    restart = request.app.state.request_restart
    if restart is None:
        raise HTTPException(status_code=503, detail="Neustart nicht verfügbar")
    background_tasks.add_task(restart)
    return {"detail": "Server wird neu gestartet"}
