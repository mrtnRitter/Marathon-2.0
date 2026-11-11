"""Read API integration tests backed by the local PostgreSQL fixture."""

from datetime import datetime, timezone
import socket
from time import monotonic

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from marathon.server.config import DatabaseConfig
from marathon.server.db.demo import load_demo_data
from marathon.server.db.engine import create_db_engine


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
asset_statuses = ("pending", "in_progress", "done", "failed", "on_hold")


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Load deterministic demo data and provide a client with a UI fallback."""
    load_demo_data(db_engine, now=fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


def _asset_counts(connection, workflow_id=None):
    """Count assets by status independently of the API queries."""
    statement = "SELECT status, COUNT(*) AS count FROM assets"
    parameters = {}
    if workflow_id is not None:
        statement += " WHERE workflow_id = :workflow_id"
        parameters["workflow_id"] = workflow_id
    statement += " GROUP BY status"
    counts = {status: 0 for status in asset_statuses}
    counts.update({row["status"]: row["count"] for row in connection.execute(text(statement), parameters).mappings()})
    return counts


def test_dashboard_returns_totals_workflows_and_five_workers(api_client, db_engine):
    response = api_client.get("/api/v1/dashboard")

    assert response.status_code == 200
    data = response.json()
    assert sum(data["totals"].values()) == 42
    with db_engine.connect() as connection:
        assert data["totals"] == _asset_counts(connection)
    assert len(data["workers"]) == 5


def test_workflows_are_ordered_and_have_complete_asset_counts(api_client, db_engine):
    response = api_client.get("/api/v1/workflows")

    assert response.status_code == 200
    workflows = response.json()
    assert [workflow["id"] for workflow in workflows] == ["wf-ingest", "wf-proxy", "wf-archive"]
    with db_engine.connect() as connection:
        for workflow in workflows:
            assert set(workflow["asset_counts"]) == set(asset_statuses)
            assert sum(workflow["asset_counts"].values()) == sum(
                _asset_counts(connection, workflow["id"]).values()
            )


def test_get_workflow_and_unknown_workflow(api_client):
    response = api_client.get("/api/v1/workflows/wf-proxy")
    assert response.status_code == 200
    assert response.json()["id"] == "wf-proxy"

    missing = api_client.get("/api/v1/workflows/nope")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Workflow nicht gefunden"


def test_workers_are_ordered_and_report_ip_and_running_jobs(api_client, db_engine):
    response = api_client.get("/api/v1/workers")

    assert response.status_code == 200
    workers = response.json()
    assert [worker["name"] for worker in workers] == sorted(worker["name"] for worker in workers)
    render_worker = next(worker for worker in workers if worker["name"] == "RENDER-01")
    assert render_worker["ip_address"] == "192.168.10.21"
    with db_engine.connect() as connection:
        for worker in workers:
            expected = connection.execute(
                text("SELECT COUNT(*) FROM jobs WHERE status = 'assigned' AND worker_id = :worker_id"),
                {"worker_id": worker["id"]},
            ).scalar_one()
            assert worker["running_jobs"] == expected


def test_module_types_include_outputs_and_settings(api_client):
    response = api_client.get("/api/v1/module-types")

    assert response.status_code == 200
    module_types = response.json()
    assert len(module_types) == 5
    assert all(isinstance(item["outputs"], list) for item in module_types)
    assert all(isinstance(item["settings"], list) for item in module_types)


def test_assets_can_be_filtered_and_include_current_module_names(api_client):
    response = api_client.get("/api/v1/assets", params={"workflow_id": "wf-proxy", "status": "done"})

    assert response.status_code == 200
    assets = response.json()
    assert assets
    assert all(asset["workflow_id"] == "wf-proxy" and asset["status"] == "done" for asset in assets)
    assert all(asset["current_module"] for asset in assets if asset["current_module_id"] is not None)
    assert api_client.get("/api/v1/assets", params={"status": "bogus"}).status_code == 422


def test_unavailable_database_returns_fast_503_and_health_stays_available():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    engine = create_db_engine(DatabaseConfig(port=port), password="x")
    try:
        with TestClient(create_app(engine=engine)) as client:
            started = monotonic()
            response = client.get("/api/v1/workflows")
            elapsed = monotonic() - started
            assert response.status_code == 503
            assert response.json() == {"detail": "Datenbank nicht erreichbar"}
            assert elapsed < 15
            assert client.get("/api/v1/health").status_code == 200
    finally:
        engine.dispose()
