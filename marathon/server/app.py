"""FastAPI application factory."""

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException

from marathon.common.version import app_name, app_version
from marathon.server.api import api_prefix, api_routers
from marathon.server.ui import create_ui_router, default_static_dir


def create_app(static_dir: Path | None = None) -> FastAPI:
    """Build the app: versioned API first, then the UI catch-all."""
    app = FastAPI(title=app_name, version=app_version, docs_url=None, redoc_url=None, openapi_url=None)
    for router in api_routers:
        app.include_router(router, prefix=api_prefix)
    app.include_router(_create_api_fallback())
    app.include_router(create_ui_router(static_dir or default_static_dir))
    return app


def _create_api_fallback() -> APIRouter:
    router = APIRouter()

    @router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(path: str) -> None:
        raise HTTPException(404, f"Unbekannter API-Pfad: /api/{path}")

    return router
