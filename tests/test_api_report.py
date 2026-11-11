"""Report API integration tests backed by the local PostgreSQL fixture."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
job_statuses = ("open", "assigned", "succeeded", "failed", "cancelled")


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Insert test records and provide a client with a UI fallback."""
    insert_records(db_engine, fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


def _job_counts(connection, start, end, workflow_id=None):
    """Count jobs by current status using SQL independent of the report API."""
    statement = (
        "SELECT status, COUNT(*) AS count FROM jobs "
        "WHERE created_at >= :start AND created_at < :end"
    )
    parameters = {"start": start, "end": end}
    if workflow_id is not None:
        statement += " AND workflow_id = :workflow_id"
        parameters["workflow_id"] = workflow_id
    statement += " GROUP BY status"
    counts = {status: 0 for status in job_statuses}
    counts.update(
        {row["status"]: row["count"] for row in connection.execute(text(statement), parameters).mappings()}
    )
    return counts


def _total_job_count(connection, start, end):
    """Count jobs in the requested range independently of the report API."""
    return connection.execute(
        text("SELECT COUNT(*) FROM jobs WHERE created_at >= :start AND created_at < :end"),
        {"start": start, "end": end},
    ).scalar_one()


def test_report_counts_all_jobs_in_wide_range(api_client, db_engine):
    start = fixed_now - timedelta(days=2)
    end = fixed_now + timedelta(days=2)
    response = api_client.get(
        "/api/v1/reports/job-status", params={"start": start.isoformat(), "end": end.isoformat()}
    )

    assert response.status_code == 200
    data = response.json()
    assert [row["workflow_id"] for row in data["rows"]] == [
        "wf-a", "wf-b", "wf-c"
    ]
    with db_engine.connect() as connection:
        for row in data["rows"]:
            expected = _job_counts(connection, start, end, row["workflow_id"])
            assert row["counts"] == expected
            assert row["total"] == sum(expected.values())
        expected_totals = _job_counts(connection, start, end)
        assert data["totals"]["counts"] == expected_totals
        assert data["totals"]["total"] == _total_job_count(connection, start, end)


def test_report_filters_by_workflow(api_client):
    start = fixed_now - timedelta(days=2)
    end = fixed_now + timedelta(days=2)
    response = api_client.get(
        "/api/v1/reports/job-status",
        params={"start": start.isoformat(), "end": end.isoformat(), "workflow_id": "wf-b"},
    )

    assert response.status_code == 200
    data = response.json()
    assert [row["workflow_id"] for row in data["rows"]] == ["wf-b"]
    assert data["totals"] == {
        "counts": data["rows"][0]["counts"], "total": data["rows"][0]["total"]
    }


def test_report_empty_range_keeps_all_workflows_with_zero(api_client):
    start = fixed_now + timedelta(days=10)
    end = fixed_now + timedelta(days=11)
    response = api_client.get(
        "/api/v1/reports/job-status", params={"start": start.isoformat(), "end": end.isoformat()}
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 3
    assert all(
        row["counts"] == {status: 0 for status in job_statuses} and row["total"] == 0
        for row in data["rows"]
    )
    assert data["totals"] == {"counts": {status: 0 for status in job_statuses}, "total": 0}


def test_report_range_is_half_open(api_client, db_engine):
    with db_engine.connect() as connection:
        minimum = connection.execute(text("SELECT MIN(created_at) FROM jobs")).scalar_one()
        expected_at_minimum = connection.execute(
            text("SELECT COUNT(*) FROM jobs WHERE created_at = :minimum"), {"minimum": minimum}
        ).scalar_one()
    response = api_client.get(
        "/api/v1/reports/job-status",
        params={
            "start": minimum.isoformat(),
            "end": (minimum + timedelta(microseconds=1)).isoformat(),
        },
    )
    assert response.status_code == 200
    assert response.json()["totals"]["total"] == expected_at_minimum

    response = api_client.get(
        "/api/v1/reports/job-status",
        params={"start": (minimum - timedelta(hours=1)).isoformat(), "end": minimum.isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["totals"]["total"] == 0


def test_report_rejects_invalid_input(api_client):
    start = fixed_now.isoformat()
    end = (fixed_now + timedelta(days=1)).isoformat()
    missing_workflow = api_client.get(
        "/api/v1/reports/job-status",
        params={"start": start, "end": end, "workflow_id": "unknown"},
    )
    assert missing_workflow.status_code == 404
    assert missing_workflow.json()["detail"] == "Workflow nicht gefunden"

    same_range = api_client.get("/api/v1/reports/job-status", params={"start": start, "end": start})
    assert same_range.status_code == 422
    assert same_range.json()["detail"] == "Ende muss nach dem Start liegen"

    naive_start = api_client.get(
        "/api/v1/reports/job-status", params={"start": "2025-01-01T00:00:00", "end": end}
    )
    assert naive_start.status_code == 422
    assert naive_start.json()["detail"] == "Zeitangabe ohne Zeitzone"

    missing_start = api_client.get("/api/v1/reports/job-status", params={"end": end})
    assert missing_start.status_code == 422
