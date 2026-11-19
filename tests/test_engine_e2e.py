"""End-to-end tests for the engine, HTTP server, and real worker runtime."""

# IMPORTS
from contextlib import contextmanager
from pathlib import Path
import socket
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx
import uvicorn
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.modules._job import JobStopped
from marathon.server.app import create_app
from marathon.server.config import EngineConfig
from marathon.worker.runtime import WorkerRuntime, worker_capabilities


# TEST HELPERS
def _wait_until(predicate, timeout_s: float, message: str) -> None:
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.05)
    assert predicate(), message


def _asset_rows(db_engine, workflow_id: str) -> list[dict]:
    with db_engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            text("SELECT id, status, step_status, current_module_id, metadata FROM assets "
                 "WHERE workflow_id = :workflow_id ORDER BY id"),
            {"workflow_id": workflow_id},
        ).mappings()]


def _job_statuses(db_engine, workflow_id: str) -> list[str]:
    with db_engine.connect() as connection:
        return list(connection.execute(
            text("SELECT status FROM jobs WHERE workflow_id = :workflow_id ORDER BY id"),
            {"workflow_id": workflow_id},
        ).scalars())


def _workflow(client: httpx.Client, name: str, modules: list[dict], connections: list[dict]) -> str:
    created = client.post("/api/v1/workflows", json={"name": name})
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    saved = client.put(
        f"/api/v1/workflows/{workflow_id}/graph",
        json={"base_version": 1, "modules": modules, "connections": connections},
    )
    assert saved.status_code == 200, saved.text
    activated = client.put(f"/api/v1/workflows/{workflow_id}/active", json={"active": True})
    assert activated.status_code == 200, activated.text
    return workflow_id


def _module(module_id: str, type_id: str, settings: dict | None = None) -> dict:
    return {
        "id": module_id,
        "type_id": type_id,
        "name": module_id,
        "position": {"x": 0, "y": 0},
        "settings": settings or {},
    }


def _connection(source: str, output: str, target: str) -> dict:
    return {
        "source_module_id": source,
        "source_output_id": output,
        "target_module_id": target,
    }


def _insert_asset(db_engine, workflow_id: str, source_path: str, asset_id: str | None = None) -> str:
    asset_id = asset_id or f"e2e-{uuid4().hex[:12]}"
    statement = text(
        "INSERT INTO assets (id, workflow_id, name, status, current_module_id, metadata, list_position) "
        "VALUES (:id, :workflow_id, :name, 'pending', NULL, :metadata, 1)"
    ).bindparams(bindparam("metadata", type_=JSONB))
    with db_engine.begin() as connection:
        connection.execute(statement, {
            "id": asset_id,
            "workflow_id": workflow_id,
            "name": Path(source_path).name,
            "metadata": {"source_path": source_path},
        })
    asset = next(row for row in _asset_rows(db_engine, workflow_id) if row["id"] == asset_id)
    assert asset["status"] == "pending"
    assert asset["current_module_id"] is None
    assert asset["metadata"] == {"source_path": source_path}
    return asset_id


@contextmanager
def _running_server_and_worker(db_engine, tmp_path, resolve_module=None):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    app = create_app(
        static_dir=tmp_path,
        engine=db_engine,
        worker_token="e2e",
        engine_config=EngineConfig(tick_interval_s=0.1, poll_timeout_s=1, pickup_timeout_s=5),
        run_engine=True,
        sync_catalog=True,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    server_thread = Thread(target=server.run, name="test-uvicorn-server", daemon=True)
    worker_stop = Event()
    worker_result = []
    runtime_options = {
        "request_timeout_s": 3,
        "retry_delays_s": (0.1,),
        "shutdown_timeout_s": 2,
    }
    if resolve_module is not None:
        runtime_options["resolve_module"] = resolve_module
    runtime = WorkerRuntime(
        url, "e2e", "server-worker", "Server", worker_capabilities(), **runtime_options
    )

    def run_worker() -> None:
        try:
            worker_result.append(runtime.run(worker_stop))
        except BaseException as error:
            worker_result.append(error)

    worker_thread = Thread(target=run_worker, name="test-worker-runtime", daemon=True)
    server_thread.start()
    try:
        _wait_until(lambda: server.started or not server_thread.is_alive(), 15, "Serverstart dauerte zu lange")
        assert server.started
        worker_thread.start()
        _wait_until(
            lambda: _worker_registered(db_engine),
            10,
            "Worker hat sich nicht am Server angemeldet",
        )
        started = httpx.post(f"{url}/api/v1/workers/server-worker/start", timeout=3)
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "ready"
        yield url
    finally:
        worker_stop.set()
        server.should_exit = True
        server_thread.join(15)
        if worker_thread.ident is not None:
            worker_thread.join(15)
        assert not server_thread.is_alive(), "Server-Thread wurde nicht beendet"
        assert worker_thread.ident is None or not worker_thread.is_alive(), "Worker-Thread wurde nicht beendet"
        assert not worker_result or isinstance(worker_result[0], int), worker_result


def _worker_registered(db_engine) -> bool:
    with db_engine.connect() as connection:
        return connection.execute(
            text("SELECT 1 FROM workers WHERE id = 'server-worker'")
        ).scalar_one_or_none() is not None


def _watch_settings(source_dir) -> dict:
    return {"path": str(source_dir), "include": "", "exclude": "", "wait_s": 5, "recursive": False}


# TESTS
def test_copy_then_move_end_to_end(db_engine, tmp_path):
    source_dir = tmp_path / "src"
    copy_dir = tmp_path / "a"
    move_dir = tmp_path / "b"
    source_dir.mkdir()
    copy_dir.mkdir()
    move_dir.mkdir()
    source = source_dir / "clip.mov"
    contents = b"end-to-end worker file"
    source.write_bytes(contents)

    with _running_server_and_worker(db_engine, tmp_path) as url, httpx.Client(base_url=url, timeout=5) as client:
        workflow_id = _workflow(
            client,
            "Copy then move",
            [
                _module("watch", "watchfolder", _watch_settings(source_dir)),
                _module("copy", "copy_file", {"target": str(copy_dir)}),
                _module("move", "move_file", {"target": str(move_dir)}),
            ],
            [_connection("watch", "success", "copy"), _connection("copy", "success", "move")],
        )
        original_id = _insert_asset(db_engine, workflow_id, str(source))

        _wait_until(
            lambda: len(_asset_rows(db_engine, workflow_id)) == 2
            and all(row["status"] == "done" for row in _asset_rows(db_engine, workflow_id))
            and len(_job_statuses(db_engine, workflow_id)) == 2
            and all(status == "succeeded" for status in _job_statuses(db_engine, workflow_id)),
            15,
            "Kopier- und Verschiebe-Workflow wurde nicht rechtzeitig abgeschlossen",
        )

        assets = _asset_rows(db_engine, workflow_id)
        original = next(asset for asset in assets if asset["id"] == original_id)
        copied = next(asset for asset in assets if asset["id"] != original_id)
        assert original["status"] == "done"
        assert copied["status"] == "done"
        assert copied["metadata"]["path"] == str(move_dir / "clip.mov")
        assert (move_dir / "clip.mov").read_bytes() == contents
        assert list(copy_dir.iterdir()) == []
        assert source.read_bytes() == contents
        assert _job_statuses(db_engine, workflow_id) == ["succeeded", "succeeded"]


def test_pause_cancels_running_job_end_to_end(db_engine, tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = source_dir / "clip.mov"
    source.write_bytes(b"pause test")

    class BlockingModule:
        def run(self, job):
            if not job.stop.wait(10):
                raise TimeoutError("Abbruchsignal blieb aus")
            raise JobStopped

    with _running_server_and_worker(
        db_engine, tmp_path, resolve_module=lambda type_id: BlockingModule()
    ) as url, httpx.Client(base_url=url, timeout=5) as client:
        workflow_id = _workflow(
            client,
            "Pause running job",
            [
                _module("watch", "watchfolder", _watch_settings(source_dir)),
                _module("copy", "copy_file", {"target": str(source_dir)}),
            ],
            [_connection("watch", "success", "copy")],
        )
        asset_id = _insert_asset(db_engine, workflow_id, str(source))

        _wait_until(
            lambda: _job_status_for_asset(db_engine, asset_id) == "running",
            10,
            "Job erreichte den Zustand running nicht",
        )
        response = client.put(f"/api/v1/workflows/{workflow_id}/active", json={"active": False})
        assert response.status_code == 200, response.text
        assert response.json()["pausing"] is True

        _wait_until(
            lambda: _pause_finished(db_engine, workflow_id, asset_id),
            5,
            "Job oder Workflow wurde nicht rechtzeitig pausiert",
        )
        assert _job_status_for_asset(db_engine, asset_id) == "cancelled"
        asset = next(row for row in _asset_rows(db_engine, workflow_id) if row["id"] == asset_id)
        assert (asset["status"], asset["step_status"], asset["current_module_id"]) == (
            "in_progress", "waiting", "copy"
        )


def test_wait_module_end_to_end(db_engine, tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = source_dir / "clip.mov"
    source.write_bytes(b"wait test")

    with _running_server_and_worker(db_engine, tmp_path) as url, httpx.Client(base_url=url, timeout=5) as client:
        workflow_id = _workflow(
            client,
            "Wait module",
            [
                _module("watch", "watchfolder", _watch_settings(source_dir)),
                _module("wait", "wait", {"wait_s": 1}),
            ],
            [_connection("watch", "success", "wait")],
        )
        asset_id = _insert_asset(db_engine, workflow_id, str(source))

        _wait_until(
            lambda: _asset_status(db_engine, asset_id) == "done",
            5,
            "Warte-Workflow wurde nicht rechtzeitig abgeschlossen",
        )
        assert _job_statuses(db_engine, workflow_id) == ["succeeded"]


def _job_status_for_asset(db_engine, asset_id: str) -> str | None:
    with db_engine.connect() as connection:
        return connection.execute(
            text("SELECT jobs.status FROM jobs JOIN job_assets ON job_assets.job_id = jobs.id "
                 "WHERE job_assets.asset_id = :asset_id ORDER BY jobs.created_at DESC LIMIT 1"),
            {"asset_id": asset_id},
        ).scalar_one_or_none()


def _asset_status(db_engine, asset_id: str) -> str | None:
    with db_engine.connect() as connection:
        return connection.execute(text("SELECT status FROM assets WHERE id = :id"), {"id": asset_id}).scalar_one_or_none()


def _pause_finished(db_engine, workflow_id: str, asset_id: str) -> bool:
    with db_engine.connect() as connection:
        workflow = connection.execute(
            text("SELECT pausing FROM workflows WHERE id = :id"), {"id": workflow_id}
        ).scalar_one()
        asset = connection.execute(
            text("SELECT status, step_status, current_module_id FROM assets WHERE id = :id"),
            {"id": asset_id},
        ).mappings().one()
    return (
        not workflow
        and asset["status"] == "in_progress"
        and asset["step_status"] == "waiting"
        and asset["current_module_id"] == "copy"
    )
