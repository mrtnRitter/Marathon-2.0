"""Worker management API integration tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Insert baseline records and provide an API client."""
    insert_records(db_engine, fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


def _insert_worker(connection, worker_id="worker-test", status="new"):
    connection.execute(
        text(
            "INSERT INTO workers (id, name, status, capabilities, max_parallel) "
            "VALUES (:id, :name, :status, '[\"copy_file\", \"move_file\"]'::jsonb, 1)"
        ),
        {"id": worker_id, "name": "Test Worker", "status": status},
    )


def test_list_workers_has_priorities_and_slots(api_client, db_engine):
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
    assert isinstance(worker["priorities"], dict)
    assert isinstance(worker["client_slots"], int)
    assert worker["running_jobs"] == 3


def test_update_worker(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection)

    response = api_client.put(
        "/api/v1/workers/worker-test",
        json={
            "name": " GPU 1 ",
            "max_parallel": 4,
            "priorities": {"copy_file": "preferred", "move_file": "off"},
        },
    )

    assert response.status_code == 200
    worker = response.json()
    assert worker["name"] == "GPU 1"
    assert worker["max_parallel"] == 4
    assert worker["priorities"] == {"copy_file": "preferred", "move_file": "off"}
    with db_engine.connect() as connection:
        row = connection.execute(
            text("SELECT name, max_parallel, priorities FROM workers WHERE id = 'worker-test'")
        ).mappings().one()
    assert row["name"] == "GPU 1"
    assert row["max_parallel"] == 4
    assert row["priorities"] == {"copy_file": "preferred", "move_file": "off"}


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"name": " ", "max_parallel": 1, "priorities": {}}, "Name fehlt"),
        ({"name": "x" * 101, "max_parallel": 1, "priorities": {}}, "Name ist zu lang (max. 100 Zeichen)"),
        ({"name": "Worker", "max_parallel": 0, "priorities": {}}, "Parallele Jobs: 1 bis 64"),
        ({"name": "Worker", "max_parallel": 65, "priorities": {}}, "Parallele Jobs: 1 bis 64"),
        ({"name": "Worker", "max_parallel": 1, "priorities": {"Copy-File": "normal"}}, "Ungültiger Modultyp: Copy-File"),
        ({"name": "Worker", "max_parallel": 1, "priorities": {"copy_file": "high"}}, "Ungültiger Vorrang für copy_file: high"),
    ],
)
def test_update_worker_validation(api_client, db_engine, payload, detail):
    with db_engine.begin() as connection:
        _insert_worker(connection)

    response = api_client.put("/api/v1/workers/worker-test", json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": detail}


def test_approve_and_lock(api_client, db_engine):
    with db_engine.begin() as connection:
        _insert_worker(connection)

    approved = api_client.post("/api/v1/workers/worker-test/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "ready"

    locked = api_client.post("/api/v1/workers/worker-test/lock")
    assert locked.status_code == 200
    assert locked.json()["status"] == "locked"

    approved_again = api_client.post("/api/v1/workers/worker-test/approve")
    assert approved_again.status_code == 200
    assert approved_again.json()["status"] == "ready"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("put", "/api/v1/workers/missing", {"name": "Worker", "max_parallel": 1, "priorities": {}}),
        ("post", "/api/v1/workers/missing/approve", None),
        ("post", "/api/v1/workers/missing/lock", None),
    ],
)
def test_unknown_worker_returns_404(api_client, method, path, payload):
    response = getattr(api_client, method)(path, json=payload) if payload is not None else getattr(api_client, method)(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Worker nicht gefunden"}
