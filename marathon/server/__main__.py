"""Start the Marathon server: python -m marathon.server"""

# IMPORTS
import sys
from typing import Any, Callable

from fastapi import FastAPI
import uvicorn

from marathon.common.version import app_name, app_version
from marathon.server import watcher
from marathon.server.app import create_app
from marathon.server.config import ConfigError, load_server_config
from marathon.server.scheduler import tick_interval_s
from marathon.server.ui import default_static_dir
from marathon.server.worker_supervisor import server_url


# FUNC
def serve(app: FastAPI, host: str, port: int) -> None:
    """Run Uvicorn while closing live streams as soon as shutdown starts."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=5,
    )
    _LiveServer(config, app.state.live_hub.close_streams).run()


class _LiveServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, on_exit: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_exit = on_exit

    def handle_exit(self, sig: int, frame: Any) -> None:
        try:
            self._on_exit()
        except Exception:
            pass
        super().handle_exit(sig, frame)


# MAIN
def main() -> int:
    """Load configuration and start the database-backed server."""
    try:
        config = load_server_config()
    except ConfigError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    if not (default_static_dir / "index.html").is_file():
        print("Fehler: Oberfläche ist nicht gebaut. Zuerst python build.py ausführen.", file=sys.stderr)
        return 1
    print(f"{app_name} {app_version} läuft auf http://{config.host}:{config.port}  (Beenden mit „stop“)")
    app = create_app(
        scheduler_interval_s=tick_interval_s,
        sync_catalog=True,
        watch_interval_s=watcher.tick_interval_s,
        engine_config=config.engine,
        run_engine=True,
        run_live=True,
        server_worker=config.server_worker,
        server_url=server_url(config.host, config.port),
    )
    serve(app, config.host, config.port)
    return 0


# EXEC
if __name__ == "__main__":
    sys.exit(main())
