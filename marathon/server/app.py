"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import AsyncIterator

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError

from marathon.common.version import app_name, app_version
from marathon.server.api import api_prefix, api_routers
from marathon.server.api.deps import app_engine
from marathon.server.config import ConfigError
from marathon.server.scheduler import ScheduleRunner
from marathon.server.ui import create_ui_router, default_static_dir


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run configured background scheduling and dispose of owned resources."""
    runner = None
    try:
        if app.state.scheduler_interval_s is not None:
            runner = ScheduleRunner(
                lambda: app_engine(app),
                app.state.scheduler_interval_s,
            )
            runner.start()
        yield
    finally:
        if runner is not None:
            runner.stop()
        if app.state.engine_owned and app.state.engine is not None:
            app.state.engine.dispose()


def create_app(
    static_dir: Path | None = None,
    engine: Engine | None = None,
    scheduler_interval_s: float | None = None,
) -> FastAPI:
    """Build the versioned API and UI without opening a database connection."""
    app = FastAPI(
        title=app_name,
        version=app_version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.engine = engine
    app.state.engine_owned = engine is None
    app.state.scheduler_interval_s = scheduler_interval_s

    @app.exception_handler(OperationalError)
    @app.exception_handler(SQLAlchemyTimeoutError)
    @app.exception_handler(ConfigError)
    async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "Datenbank nicht erreichbar"})

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
