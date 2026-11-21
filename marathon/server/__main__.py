"""Start the Marathon server: python -m marathon.server"""

# IMPORTS
import sys
from typing import Any, Callable

from fastapi import FastAPI
import uvicorn

from marathon.server import watcher
from marathon.server.app import create_app
from marathon.server.config import ConfigError, load_server_config
from marathon.server.scheduler import tick_interval_s
from marathon.server.ui import default_static_dir


# FUNC
def _begin_shutdown(app: FastAPI) -> None:
    """Signal pending worker polls and close live streams."""
    app.state.shutdown_event.set()
    try:
        app.state.live_hub.close_streams()
    except Exception:
        pass


def serve(app: FastAPI, host: str, port: int) -> None:
    """Run Uvicorn while signaling shutdown as soon as it starts."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=5,
    )
    _LiveServer(config, lambda: _begin_shutdown(app)).run()


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
    app = create_app(
        scheduler_interval_s=tick_interval_s,
        sync_catalog=True,
        watch_interval_s=watcher.tick_interval_s,
        engine_config=config.engine,
        run_engine=True,
        run_live=True,
        server_worker=config.server_worker,
        load_proxy=True,
        listen=(config.host, config.port),
    )
    serve(app, config.host, config.port)
    return 0


# EXEC
if __name__ == "__main__":
    sys.exit(main())
