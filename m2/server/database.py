from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._uri = self.path == ":memory:"
        if self._uri:
            self.path = f"file:m2-{id(self)}?mode=memory&cache=shared"
            self._anchor = self._connect()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, uri=self._uri, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_versions (
                    workflow_id TEXT NOT NULL REFERENCES workflows(id),
                    version INTEGER NOT NULL,
                    graph TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (workflow_id, version)
                );
                CREATE TABLE IF NOT EXISTS clips (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(id),
                    workflow_version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    job TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    files TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workflow_version INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    module TEXT NOT NULL,
                    params TEXT NOT NULL,
                    clip_ids TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    worker_id TEXT,
                    lease_expires_at TEXT,
                    progress REAL,
                    logs TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_ready ON tasks(state, created_at);
                CREATE TABLE IF NOT EXISTS workers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    modules TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_heartbeat TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    type TEXT NOT NULL,
                    workflow_id TEXT,
                    clip_id TEXT,
                    node_id TEXT,
                    task_id TEXT,
                    worker_id TEXT,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_clip ON events(clip_id, id);
                """
            )


def json_value(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def unpack(value: str | None, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    return json.loads(value)


def as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
