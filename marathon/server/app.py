"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import AsyncIterator
import logging
import os

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError

from marathon.common.version import app_name, app_version
from marathon.server.api import api_prefix, api_routers
from marathon.server.api.deps import app_engine
from marathon.server.config import ConfigError, EngineConfig, ServerWorkerConfig, data_dir, root_dir
from marathon.server.db.catalog_writes import sync_module_types
from marathon.modules import load_catalog
from marathon.server.engine.tick import EngineRunner
from marathon.server.live import LiveHub, NotifyListener, open_listen_connection
from marathon.server.scheduler import ScheduleRunner
from marathon.server.watcher import WatchRunner
from marathon.server.ui import create_ui_router, default_static_dir
from marathon.server.worker_auth import load_or_create_token
from marathon.server.worker_supervisor import WorkerSupervisor, server_worker_command


_logger = logging.getLogger("marathon.server")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run configured background tasks and dispose of owned resources."""
    runner = None
    watcher = None
    engine_runner = None
    supervisor = None
    listener = None
    try:
        if app.state.sync_catalog:
            with app_engine(app).begin() as connection:
                sync_module_types(connection, load_catalog())
        if app.state.run_live:
            listener = NotifyListener(
                lambda: open_listen_connection(app_engine(app)),
                app.state.live_hub.publish,
            )
            try:
                listener.start()
            except Exception as error:
                _logger.warning("Live-Listener konnte nicht gestartet werden (%s)", type(error).__name__)
        if app.state.scheduler_interval_s is not None:
            runner = ScheduleRunner(
                lambda: app_engine(app),
                app.state.scheduler_interval_s,
            )
            runner.start()
        if app.state.watch_interval_s is not None:
            watcher = WatchRunner(lambda: app_engine(app), app.state.watch_interval_s)
            watcher.start()
        if app.state.run_engine:
            engine_runner = EngineRunner(lambda: app_engine(app), app.state.engine_config)
            engine_runner.start()
        server_worker = app.state.server_worker
        if server_worker is not None and server_worker.enabled and app.state.server_url:
            try:
                token = app.state.worker_token or load_or_create_token()
                app.state.worker_token = token
                supervisor = WorkerSupervisor(
                    server_worker_command(app.state.server_url, server_worker),
                    env={**os.environ, "MARATHON_WORKER_TOKEN": token},
                    cwd=root_dir,
                    log_file=data_dir / "server-worker.log",
                )
                supervisor.start()
            except Exception as error:
                _logger.warning("Server-Worker konnte nicht gestartet werden (%s)", type(error).__name__)
        yield
    finally:
        try:
            app.state.live_hub.close_streams()
        except Exception:
            _logger.exception("Live-Kanal konnte nicht geschlossen werden")
        for name, task in (
            ("Live-Kanal", listener),
            ("Server-Worker", supervisor),
            ("Engine", engine_runner),
            ("Watcher", watcher),
            ("Scheduler", runner),
        ):
            if task is None:
                continue
            try:
                stopped = task.stop()
                if name == "Live-Kanal" and stopped is False:
                    _logger.warning("%s konnte nicht gestoppt werden", name)
            except Exception:
                _logger.exception("%s konnte nicht gestoppt werden", name)
        if app.state.engine_owned and app.state.engine is not None:
            try:
                app.state.engine.dispose()
            except Exception:
                _logger.exception("Datenbank-Engine konnte nicht freigegeben werden")


def create_app(
    static_dir: Path | None = None,
    engine: Engine | None = None,
    scheduler_interval_s: float | None = None,
    sync_catalog: bool = False,
    watch_interval_s: float | None = None,
    engine_config: EngineConfig | None = None,
    worker_token: str | None = None,
    run_engine: bool = False,
    run_live: bool = False,
    server_worker: ServerWorkerConfig | None = None,
    server_url: str | None = None,
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
    app.state.sync_catalog = sync_catalog
    app.state.watch_interval_s = watch_interval_s
    app.state.engine_config = engine_config or EngineConfig()
    app.state.worker_token = worker_token
    app.state.run_engine = run_engine
    app.state.live_hub = LiveHub()
    app.state.run_live = run_live
    app.state.server_worker = server_worker
    app.state.server_url = server_url

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
