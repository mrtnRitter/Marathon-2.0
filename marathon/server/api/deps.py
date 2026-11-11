"""Dependencies shared by API routes."""

from threading import Lock

from fastapi import Request
from sqlalchemy.engine import Engine

from marathon.server.config import load_server_config
from marathon.server.db.engine import create_db_engine


_engine_lock = Lock()


def get_engine(request: Request) -> Engine:
    """Return the app's database engine, creating it lazily when needed."""
    engine = request.app.state.engine
    if engine is not None:
        return engine
    with _engine_lock:
        engine = request.app.state.engine
        if engine is None:
            config = load_server_config()
            engine = create_db_engine(config.database)
            request.app.state.engine = engine
            request.app.state.engine_owned = True
    return engine
