"""Application lifespan integration tests for the workflow engine and worker."""

# IMPORTS
import logging
import os
from pathlib import Path

from fastapi.testclient import TestClient

from marathon.server import app as server_app
from marathon.server.config import EngineConfig, ServerWorkerConfig


# TEST HELPERS
def _static_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    return tmp_path


# TESTS
def test_lifespan_starts_and_stops_engine_and_server_worker(monkeypatch, db_engine, tmp_path):
    events = []
    engine_instances = []
    worker_instances = []

    class FakeEngineRunner:
        def __init__(self, engine_provider, config):
            self.engine_provider = engine_provider
            self.config = config
            engine_instances.append(self)

        def start(self):
            events.append("engine.start")

        def stop(self):
            events.append("engine.stop")

    class FakeWorkerSupervisor:
        def __init__(self, command, env, cwd, log_file):
            self.command = command
            self.env = env
            self.cwd = cwd
            self.log_file = log_file
            worker_instances.append(self)

        def start(self):
            events.append("worker.start")

        def stop(self):
            events.append("worker.stop")

    monkeypatch.setattr(server_app, "EngineRunner", FakeEngineRunner, raising=False)
    monkeypatch.setattr(server_app, "WorkerSupervisor", FakeWorkerSupervisor, raising=False)
    config = EngineConfig(tick_interval_s=0.1)
    app = server_app.create_app(
        static_dir=_static_dir(tmp_path),
        engine=db_engine,
        engine_config=config,
        worker_token="e2e-token",
        run_engine=True,
        server_worker=ServerWorkerConfig(enabled=True),
        server_url="http://127.0.0.1:8080",
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200

    assert len(engine_instances) == 1
    assert len(worker_instances) == 1
    assert engine_instances[0].config is config
    assert worker_instances[0].env["MARATHON_WORKER_TOKEN"] == "e2e-token"
    assert worker_instances[0].env.get("PATH") == os.environ.get("PATH")
    assert events == ["engine.start", "worker.start", "worker.stop", "engine.stop"]


def test_server_worker_start_failure_keeps_server_running(monkeypatch, db_engine, tmp_path, caplog):
    class FailingWorkerSupervisor:
        def __init__(self, command, env, cwd, log_file):
            pass

        def start(self):
            raise RuntimeError("worker startup failed e2e-token")

        def stop(self):
            return True

    monkeypatch.setattr(server_app, "WorkerSupervisor", FailingWorkerSupervisor, raising=False)
    app = server_app.create_app(
        static_dir=_static_dir(tmp_path),
        engine=db_engine,
        worker_token="e2e-token",
        server_worker=ServerWorkerConfig(enabled=True),
        server_url="http://127.0.0.1:8080",
    )

    with caplog.at_level(logging.WARNING, logger="marathon.server"), TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert "e2e-token" not in caplog.text
    assert any(
        record.name == "marathon.server"
        and record.getMessage() == "Server-Worker konnte nicht gestartet werden (RuntimeError)"
        for record in caplog.records
    )


def test_disabled_server_worker_not_started(monkeypatch, db_engine, tmp_path):
    starts = []

    class FakeWorkerSupervisor:
        def __init__(self, command, env, cwd, log_file):
            pass

        def start(self):
            starts.append(True)

    monkeypatch.setattr(server_app, "WorkerSupervisor", FakeWorkerSupervisor, raising=False)
    app = server_app.create_app(
        static_dir=_static_dir(tmp_path),
        engine=db_engine,
        worker_token="e2e-token",
        server_worker=ServerWorkerConfig(enabled=False),
        server_url="http://127.0.0.1:8080",
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200

    assert starts == []

def test_lifespan_stops_components_in_contract_order(monkeypatch, db_engine, tmp_path):
    events = []

    def fake_task(name):
        class FakeTask:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                events.append(f"{name}.start")

            def stop(self):
                events.append(f"{name}.stop")

        return FakeTask

    monkeypatch.setattr(server_app, "ScheduleRunner", fake_task("scheduler"))
    monkeypatch.setattr(server_app, "WatchRunner", fake_task("watcher"))
    monkeypatch.setattr(server_app, "EngineRunner", fake_task("engine"))
    monkeypatch.setattr(server_app, "WorkerSupervisor", fake_task("worker"))
    app = server_app.create_app(
        static_dir=_static_dir(tmp_path),
        engine=db_engine,
        scheduler_interval_s=1,
        watch_interval_s=1,
        engine_config=EngineConfig(),
        worker_token="e2e-token",
        run_engine=True,
        server_worker=ServerWorkerConfig(enabled=True),
        server_url="http://127.0.0.1:8080",
    )

    with TestClient(app):
        pass

    assert events == [
        "scheduler.start",
        "watcher.start",
        "engine.start",
        "worker.start",
        "worker.stop",
        "engine.stop",
        "watcher.stop",
        "scheduler.stop",
    ]

