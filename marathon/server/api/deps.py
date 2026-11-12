"""Dependencies shared by API routes."""

from threading import Lock

from fastapi import FastAPI, Request
from sqlalchemy.engine import Engine

from marathon.server.config import load_server_config
from marathon.server.db.engine import create_db_engine


_engine_lock = Lock()


def app_engine(app: FastAPI) -> Engine:
    """Return the app's database engine, creating it lazily when needed."""
    engine = app.state.engine
    if engine is not None:
        return engine
    with _engine_lock:
        engine = app.state.engine
        if engine is None:
            config = load_server_config()
            engine = create_db_engine(config.database)
            app.state.engine = engine
            app.state.engine_owned = True
    return engine


def get_engine(request: Request) -> Engine:
    """Return the engine for the request's application."""
    return app_engine(request.app)
