from marathon.server.api.report import job_status_csv


def test_job_status_csv_format():
    report = {
        "rows": [
            {
                "workflow_id": "a",
                "workflow_name": "Ingest; Tag 1",
                "counts": {"open": 1, "assigned": 0, "succeeded": 2, "failed": 1, "cancelled": 0},
                "total": 4,
            },
            {
                "workflow_id": "b",
                "workflow_name": "=SUM(A1)",
                "counts": {"open": 0, "assigned": 0, "succeeded": 0, "failed": 0, "cancelled": 0},
                "total": 0,
            },
        ],
        "totals": {
            "counts": {"open": 1, "assigned": 0, "succeeded": 2, "failed": 1, "cancelled": 0},
            "total": 4,
        },
    }

    assert job_status_csv(report) == (
        "\ufeffWorkflow;Gesamt;offen;läuft;erfolgreich;fehlgeschlagen;abgebrochen;% erfolgreich\r\n"
        '"Ingest; Tag 1";4;1;0;2;1;0;50,0\r\n'
        "'=SUM(A1);0;0;0;0;0;0;\r\n"
        "Summe;4;1;0;2;1;0;50,0\r\n"
    )


def test_job_status_csv_endpoint(db_engine, tmp_path):
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from marathon.server.app import create_app

    fixed_time = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO workflows (id, name) VALUES ('wf-x', 'Ingest')")
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, created_at) "
                "VALUES ('job-success', 'wf-x', 'm1', 'succeeded', :created_at), "
                "('job-failed', 'wf-x', 'm1', 'failed', :created_at)"
            ),
            {"created_at": fixed_time},
        )

    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        params = {
            "start": (fixed_time - timedelta(hours=1)).isoformat(),
            "end": (fixed_time + timedelta(hours=1)).isoformat(),
        }
        response = client.get("/api/v1/reports/job-status.csv", params=params)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"] == "attachment"
        assert response.content.decode("utf-8-sig").split("\r\n")[1] == "Ingest;2;0;0;1;1;0;50,0"

        same_range = client.get(
            "/api/v1/reports/job-status.csv",
            params={**params, "end": params["start"]},
        )
        assert same_range.status_code == 422
        assert same_range.json()["detail"] == "Ende muss nach dem Start liegen"

        missing_workflow = client.get(
            "/api/v1/reports/job-status.csv",
            params={**params, "workflow_id": "nope"},
        )
        assert missing_workflow.status_code == 404
        assert missing_workflow.json()["detail"] == "Workflow nicht gefunden"
