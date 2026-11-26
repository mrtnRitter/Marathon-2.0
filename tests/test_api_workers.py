"""Worker management API integration tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from auth_helpers import sign_in
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.server.app import create_app
from marathon.common.availability import default_availability
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
        "client_version", "last_seen", "ip_address", "approved", "available",
        "availability_reason", "availability_config", "availability_revision",
    }
    assert worker["approved"] is True
    assert worker["available"] is True
    assert worker["availability_reason"] == ""
    assert worker["availability_config"] == default_availability()
    assert worker["availability_revision"] == 0
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


def test_start_ready_worker_keeps_ready(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="ready")

    response = api_client.post("/api/v1/workers/worker-test/start")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_start_stopping_worker_returns_conflict(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="stopping")

    response = api_client.post("/api/v1/workers/worker-test/start")

    assert response.status_code == 409
    assert response.json() == {"detail": "Worker wird gerade gestoppt"}


def test_stop_now_worker_without_jobs_and_repeat_stop(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="ready")

    first = api_client.post("/api/v1/workers/worker-test/stop-now")
    second = api_client.post("/api/v1/workers/worker-test/stop-now")

    assert first.status_code == 200
    assert first.json()["status"] == "stopped"
    assert second.status_code == 200
    assert second.json()["status"] == "stopped"
    listed = next(item for item in api_client.get("/api/v1/workers").json() if item["id"] == "worker-test")
    assert first.json() == second.json() == listed


def test_stop_now_worker_cancels_running_job(api_client, db_engine):
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

    response = api_client.post("/api/v1/workers/worker-test/stop-now")

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


def test_stop_now_worker_cancels_assigned_job_and_returns_asset(api_client, db_engine):
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

    response = api_client.post("/api/v1/workers/worker-test/stop-now")

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
        ("post", "/api/v1/workers/missing/stop-drain", None),
        ("post", "/api/v1/workers/missing/stop-now", None),
        ("post", "/api/v1/workers/missing/approve", None),
        ("put", "/api/v1/workers/missing/availability", {}),
        ("delete", "/api/v1/workers/missing", None),
    ],
)
def test_unknown_worker_returns_404(api_client, method, path, payload):
    response = getattr(api_client, method)(path, json=payload) if payload is not None else getattr(api_client, method)(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Worker nicht gefunden"}



def test_removed_stop_route_returns_404(api_client):
    response = api_client.post("/api/v1/workers/worker-test/" + "stop")

    assert response.status_code == 404


def test_start_requires_approval_and_starting_drain_keeps_running_job(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="draining")
        connection.execute(text("UPDATE workers SET approved = false WHERE id = 'worker-test'"))
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt) "
                "VALUES ('active-drain', 'wf-a', 'm2', 'running', 'worker-test', 1)"
            )
        )
        connection.execute(text("INSERT INTO job_assets (job_id, asset_id) VALUES ('active-drain', 'a-running')"))

    denied = api_client.post("/api/v1/workers/worker-test/start")
    assert denied.status_code == 409
    assert denied.json() == {"detail": "Worker wartet auf Freigabe durch einen Administrator"}

    with db_engine.begin() as connection:
        connection.execute(text("UPDATE workers SET approved = true WHERE id = 'worker-test'"))
    started = api_client.post("/api/v1/workers/worker-test/start")

    assert started.status_code == 200
    assert started.json()["status"] == "ready"
    assert _worker_job_status(db_engine, "active-drain") == "running"


def _worker_job_status(db_engine, job_id):
    with db_engine.connect() as connection:
        return connection.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}).scalar_one()


def test_drain_returns_assigned_job_without_counting_attempt(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="ready")
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt) "
                "VALUES ('assigned-drain', 'wf-a', 'm2', 'assigned', 'worker-test', 1)"
            )
        )
        connection.execute(text("INSERT INTO job_assets (job_id, asset_id) VALUES ('assigned-drain', 'a-waiting')"))

    response = api_client.post("/api/v1/workers/worker-test/stop-drain")

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    with db_engine.connect() as connection:
        job = connection.execute(text("SELECT status FROM jobs WHERE id = 'assigned-drain'")).scalar_one()
        asset = connection.execute(
            text("SELECT status, step_status, attempts FROM assets WHERE id = 'a-waiting'")
        ).mappings().one()
    assert job == "cancelled"
    assert asset == {"status": "in_progress", "step_status": "waiting", "attempts": 0}

@pytest.mark.parametrize(("status", "expected"), [("stopping", "stopping"), ("stopped", "stopped")])
def test_drain_preserves_stopping_and_stopped_workers(api_client, db_engine, status, expected):
    with db_engine.begin() as connection:
        _insert_worker(connection, status=status)

    response = api_client.post("/api/v1/workers/worker-test/stop-drain")

    assert response.status_code == 200
    assert response.json()["status"] == expected


def test_drain_leaves_running_job_and_engine_finishes_worker(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="ready")
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt) "
                "VALUES ('running-drain', 'wf-a', 'm2', 'running', 'worker-test', 1)"
            )
        )
        connection.execute(text("INSERT INTO job_assets (job_id, asset_id) VALUES ('running-drain', 'a-running')"))

    response = api_client.post("/api/v1/workers/worker-test/stop-drain")

    assert response.status_code == 200
    assert response.json()["status"] == "draining"
    assert _worker_job_status(db_engine, "running-drain") == "running"
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET status = 'succeeded' WHERE id = 'running-drain'"))
        from marathon.server.engine.tick import finish_worker_stops
        assert finish_worker_stops(connection) == 1
    assert next(item for item in api_client.get("/api/v1/workers").json() if item["id"] == "worker-test")["status"] == "stopped"


def test_stop_now_cancels_running_job_from_draining(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, status="draining")
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt) "
                "VALUES ('running-now', 'wf-a', 'm2', 'running', 'worker-test', 1)"
            )
        )
        connection.execute(text("INSERT INTO job_assets (job_id, asset_id) VALUES ('running-now', 'a-running')"))

    response = api_client.post("/api/v1/workers/worker-test/stop-now")

    assert response.status_code == 200
    assert response.json()["status"] == "stopping"
    with db_engine.connect() as connection:
        job = connection.execute(text("SELECT status, error FROM jobs WHERE id = 'running-now'")).mappings().one()
    assert job == {"status": "cancelling", "error": "Worker gestoppt"}


def test_approve_worker(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection)
        connection.execute(text("UPDATE workers SET approved = false WHERE id = 'worker-test'"))

    response = api_client.post("/api/v1/workers/worker-test/approve")

    assert response.status_code == 200
    assert response.json()["approved"] is True
    assert response.json()["status"] == "stopped"


def test_delete_worker_conflicts_and_success(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection, "server-worker")
        _insert_worker(connection, "worker-active")
        connection.execute(
            text("INSERT INTO jobs (id, workflow_id, module_id, status, worker_id) VALUES ('delete-active', 'wf-a', 'm2', 'running', 'worker-active')")
        )
        _insert_worker(connection, "worker-delete")

    server = api_client.delete("/api/v1/workers/server-worker")
    active = api_client.delete("/api/v1/workers/worker-active")
    deleted = api_client.delete("/api/v1/workers/worker-delete")
    missing = api_client.delete("/api/v1/workers/worker-delete")

    assert server.status_code == 409
    assert server.json() == {"detail": "Der Server-Worker kann nicht entfernt werden"}
    assert active.status_code == 409
    assert active.json() == {"detail": "Worker hat noch laufende Jobs. Zuerst stoppen."}
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Worker nicht gefunden"}


def test_update_availability_normalizes_and_increments_revision(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection)

    response = api_client.put(
        "/api/v1/workers/worker-test/availability",
        json={"blocked_processes": [" app.exe ", "APP"]},
    )

    assert response.status_code == 200
    assert response.json()["availability_revision"] == 1
    assert response.json()["availability_config"]["blocked_processes"] == ["app.exe"]
    assert response.json()["available"] is True


def test_update_availability_invalid_rules_return_422(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection)

    response = api_client.put("/api/v1/workers/worker-test/availability", json={"invalid": True})

    assert response.status_code == 422
    assert response.json() == {"detail": "Unbekannte Einstellung: invalid"}



def test_list_worker_uses_default_for_invalid_saved_availability(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection)
        connection.execute(
            text("UPDATE workers SET availability_config = '{\"unknown\": true}'::jsonb WHERE id = 'worker-test'")
        )

    worker = next(item for item in api_client.get("/api/v1/workers").json() if item["id"] == "worker-test")

    assert worker["availability_config"] == default_availability()
