"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import AsyncIterator
import logging
import os
from threading import Event

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError

from marathon.common.version import app_name, app_version
from marathon.server.api import api_prefix, api_routers
from marathon.server.api.deps import app_engine, current_user
from marathon.server.config import (
    AuthConfig,
    ConfigError,
    EngineConfig,
    ServerWorkerConfig,
    SmtpConfig,
    caddy_exe,
    caddy_log_file,
    caddy_root_cert,
    caddy_storage_dir,
    caddyfile_path,
    cert_dir,
    data_dir,
    root_dir,
)
from marathon.server.db.settings_writes import get_proxy_config
from marathon.server.proxy import ProxyMiddleware, caddyfile, proxy_settings, upstream_address
from marathon.server.db.catalog_writes import sync_module_types
from marathon.modules import load_catalog
from marathon.server.engine.tick import EngineRunner
from marathon.server.live import LiveHub, NotifyListener, open_listen_connection
from marathon.server.scheduler import ScheduleRunner
from marathon.server.watcher import WatchRunner
from marathon.server.ui import create_ui_router, default_static_dir
from marathon.server.mail import MailSender
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
    proxy_supervisor = None
    listener = None
    try:
        if app.state.load_proxy:
            try:
                with app_engine(app).connect() as connection:
                    stored_proxy = get_proxy_config(connection)
            except ValueError as error:
                _logger.error("Proxy-Einstellungen ungültig: %s", error)
                raise RuntimeError("Proxy-Einstellungen ungültig") from error
            app.state.proxy_settings = proxy_settings(
                stored_proxy.public_url, stored_proxy.builtin, stored_proxy.trusted_proxies
            )
            app.state.proxy_builtin = stored_proxy.builtin
            if stored_proxy.builtin:
                if not caddy_exe.is_file():
                    message = "Mitgelieferter Proxy fehlt (.tools/caddy). Zuerst python build.py ausführen."
                    _logger.error(message)
                    raise RuntimeError(message)
                if app.state.listen is None:
                    raise RuntimeError("Listen-Adresse für den mitgelieferten Proxy fehlt")
                try:
                    cert_dir.mkdir(parents=True, exist_ok=True)
                    rendered_caddyfile = caddyfile(
                        stored_proxy.public_url,
                        upstream_address(*app.state.listen),
                        caddy_storage_dir,
                    )
                    with caddyfile_path.open("w", encoding="utf-8", newline="\n") as file:
                        file.write(rendered_caddyfile)
                except OSError as error:
                    _logger.error("Caddy-Konfiguration konnte nicht geschrieben werden: %s", error)
                    raise RuntimeError("Caddy-Konfiguration konnte nicht geschrieben werden") from error
                proxy_supervisor = WorkerSupervisor(
                    [str(caddy_exe), "run", "--config", str(caddyfile_path), "--adapter", "caddyfile"],
                    env=dict(os.environ),
                    cwd=root_dir,
                    log_file=caddy_log_file,
                    graceful_timeout_s=0.0,
                    name="Proxy",
                )
                proxy_supervisor.start()
            print(
                f"{app_name} {app_version} läuft auf {stored_proxy.public_url}  (Beenden mit „stop“)",
                flush=True,
            )
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
        worker_url = (
            app.state.proxy_settings.public_url if app.state.load_proxy else app.state.server_url
        )
        if server_worker is not None and server_worker.enabled and worker_url:
            try:
                token = app.state.worker_token or load_or_create_token()
                app.state.worker_token = token
                supervisor = WorkerSupervisor(
                    server_worker_command(
                        worker_url,
                        server_worker,
                        ca_file=caddy_root_cert
                        if app.state.load_proxy and app.state.proxy_builtin
                        else None,
                    ),
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
            app.state.mail_sender.close()
        except Exception:
            _logger.exception("Mailversand konnte nicht geschlossen werden")
        app.state.shutdown_event.set()
        try:
            app.state.live_hub.close_streams()
        except Exception:
            _logger.exception("Live-Kanal konnte nicht geschlossen werden")
        for name, task in (
            ("Live-Kanal", listener),
            ("Server-Worker", supervisor),
            ("Proxy", proxy_supervisor),
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
    load_proxy: bool = False,
    listen: tuple[str, int] | None = None,
    auth_config: AuthConfig | None = None,
    smtp_config: SmtpConfig | None = None,
    mail_sender: MailSender | None = None,
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
    app.state.shutdown_event = Event()
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
    app.state.load_proxy = load_proxy
    app.state.listen = listen
    app.state.proxy_settings = None
    app.state.proxy_builtin = False
    app.state.auth_config = auth_config if auth_config is not None else AuthConfig()
    app.state.smtp_config = smtp_config if smtp_config is not None else SmtpConfig()
    app.state.mail_sender = mail_sender if mail_sender is not None else MailSender()
    if not hasattr(app.state, "request_restart"):
        app.state.request_restart = None

    @app.exception_handler(OperationalError)
    @app.exception_handler(SQLAlchemyTimeoutError)
    @app.exception_handler(ConfigError)
    async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "Datenbank nicht erreichbar"})

    app.add_middleware(ProxyMiddleware, settings=lambda: app.state.proxy_settings)
    for router in api_routers:
        app.include_router(router, prefix=api_prefix)
    app.include_router(_create_api_fallback())
    app.include_router(create_ui_router(static_dir or default_static_dir))
    return app


def _create_api_fallback() -> APIRouter:
    router = APIRouter(dependencies=[Depends(current_user)])

    @router.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    def api_not_found(path: str) -> None:
        raise HTTPException(404, f"Unbekannter API-Pfad: /api/{path}")

    return router
