"""Worker management API integration tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from auth_helpers import sign_in
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.server.app import create_app
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Insert baseline records and provide an API client."""
    insert_records(db_engine, fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _test_app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(_test_app)
    with TestClient(_test_app) as client:
        yield client


def _insert_worker(connection, worker_id="worker-test", status="stopped"):
    connection.execute(
        text(
            "INSERT INTO workers (id, name, status, capabilities, skills, online) "
            "VALUES (:id, :name, :status, :capabilities, :skills, true)"
        ).bindparams(bindparam("capabilities", type_=JSONB()), bindparam("skills", type_=JSONB())),
        {
            "id": worker_id, "name": "Test Worker", "status": status,
            "capabilities": ["copy_file", "move_file"], "skills": ["copy_file"],
        },
    )


def test_list_workers_returns_release_record(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, "worker-jobs", "ready")
        for index, status in enumerate(("assigned", "running", "cancelling", "succeeded")):
            connection.execute(
                text(
                    "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id) "
                    "VALUES (:id, 'wf-a', 'm2', :status, 'worker-jobs')"
                ),
                {"id": f"worker-job-{index}", "status": status},
            )

    response = api_client.get("/api/v1/workers")

    assert response.status_code == 200
    worker = next(item for item in response.json() if item["id"] == "worker-jobs")
    assert set(worker) == {
        "id", "name", "status", "online", "capabilities", "skills", "running_jobs",
        "client_version", "last_seen", "ip_address",
    }
    assert worker["online"] is True
    assert worker["skills"] == ["copy_file"]
    assert worker["running_jobs"] == 3


def test_update_worker_preserves_skill_order(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection)

    response = api_client.put(
        "/api/v1/workers/worker-test",
        json={"name": " GPU 1 ", "skills": ["move_file", "copy_file"]},
    )

    assert response.status_code == 200
    worker = response.json()
    assert worker["name"] == "GPU 1"
    assert worker["skills"] == ["move_file", "copy_file"]
    with db_engine.connect() as connection:
        row = connection.execute(
            text("SELECT name, skills FROM workers WHERE id = 'worker-test'")
        ).mappings().one()
    assert row["name"] == "GPU 1"
    assert row["skills"] == ["move_file", "copy_file"]


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"name": " ", "skills": []}, "Name fehlt"),
        ({"name": "x" * 101, "skills": []}, "Name ist zu lang (max. 100 Zeichen)"),
        ({"name": "Worker", "skills": ["Copy-File"]}, "Ungültiger Modultyp: Copy-File"),
        ({"name": "Worker", "skills": ["copy_file", "copy_file"]}, "Fähigkeit doppelt: copy_file"),
        ({"name": "Worker", "skills": ["rename_file"]}, "Fähigkeit wird vom Rechner nicht gemeldet: rename_file"),
    ],
)
def test_update_worker_validation(api_client, db_engine, payload, detail):
    with db_engine.begin() as connection:
        _insert_worker(connection)

    response = api_client.put("/api/v1/workers/worker-test", json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": detail}


def test_start_stopped_worker(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection)

    response = api_client.post("/api/v1/workers/worker-test/start")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    listed = next(item for item in api_client.get("/api/v1/workers").json() if item["id"] == "worker-test")
    assert response.json() == listed


def test_start_stopping_worker_returns_conflict(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="stopping")

    response = api_client.post("/api/v1/workers/worker-test/start")

    assert response.status_code == 409
    assert response.json() == {"detail": "Worker wird gerade gestoppt"}


def test_stop_worker_without_jobs_and_repeat_stop(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="ready")

    first = api_client.post("/api/v1/workers/worker-test/stop")
    second = api_client.post("/api/v1/workers/worker-test/stop")

    assert first.status_code == 200
    assert first.json()["status"] == "stopped"
    assert second.status_code == 200
    assert second.json()["status"] == "stopped"
    listed = next(item for item in api_client.get("/api/v1/workers").json() if item["id"] == "worker-test")
    assert first.json() == second.json() == listed


def test_stop_worker_cancels_running_job(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="ready")
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt) "
                "VALUES ('running-stop', 'wf-a', 'm2', 'running', 'worker-test', 1)"
            )
        )
        connection.execute(
            text("INSERT INTO job_assets (job_id, asset_id) VALUES ('running-stop', 'a-running')")
        )

    response = api_client.post("/api/v1/workers/worker-test/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stopping"
    listed = next(item for item in api_client.get("/api/v1/workers").json() if item["id"] == "worker-test")
    assert response.json() == listed
    with db_engine.connect() as connection:
        job = connection.execute(
            text("SELECT status, error FROM jobs WHERE id = 'running-stop'")
        ).mappings().one()
    assert job["status"] == "cancelling"
    assert job["error"] == "Worker gestoppt"


def test_stop_worker_cancels_assigned_job_and_returns_asset(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="ready")
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt) "
                "VALUES ('assigned-stop', 'wf-a', 'm2', 'assigned', 'worker-test', 1)"
            )
        )
        connection.execute(
            text("INSERT INTO job_assets (job_id, asset_id) VALUES ('assigned-stop', 'a-waiting')")
        )

    response = api_client.post("/api/v1/workers/worker-test/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    listed = next(item for item in api_client.get("/api/v1/workers").json() if item["id"] == "worker-test")
    assert response.json() == listed
    with db_engine.connect() as connection:
        job = connection.execute(
            text("SELECT status, error FROM jobs WHERE id = 'assigned-stop'")
        ).mappings().one()
        asset = connection.execute(
            text("SELECT status, step_status, wait_until FROM assets WHERE id = 'a-waiting'")
        ).mappings().one()
    assert job["status"] == "cancelled"
    assert job["error"] == "Worker gestoppt"
    assert asset["status"] == "in_progress"
    assert asset["step_status"] == "waiting"
    assert asset["wait_until"] is None


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("put", "/api/v1/workers/missing", {"name": "Worker", "skills": []}),
        ("post", "/api/v1/workers/missing/start", None),
        ("post", "/api/v1/workers/missing/stop", None),
    ],
)
def test_unknown_worker_returns_404(api_client, method, path, payload):
    response = getattr(api_client, method)(path, json=payload) if payload is not None else getattr(api_client, method)(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Worker nicht gefunden"}
