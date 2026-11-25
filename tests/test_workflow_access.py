"""Workflow access-control API tests using isolated PostgreSQL."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import create_test_user, sign_in
from marathon.server.app import create_app
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
report_params = {
    "start": "2024-12-01T00:00:00+00:00",
    "end": "2026-01-01T00:00:00+00:00",
}
graph_payload = {"base_version": 2, "modules": [], "connections": []}


@pytest.fixture
def access_context(db_engine, tmp_path):
    """Create shared workflows, users, and one authenticated test application."""
    insert_records(db_engine, fixed_now)
    names = {"a": "A Owner", "b": "B Viewer", "c": "C Runner", "d": "D Editor", "e": "E Private", "admin": "Admin"}
    emails = {key: f"{key}@access.test" for key in names}
    emails["admin"] = "admin@access.test"
    with db_engine.begin() as connection:
        users = {
            key: create_test_user(connection, email, "admin" if key == "admin" else "user")
            for key, email in emails.items()
        }
        for key, user in users.items():
            connection.execute(
                text("UPDATE users SET display_name = :name WHERE id = :id"),
                {"name": names[key], "id": user.id},
            )
        connection.execute(
            text("UPDATE workflows SET owner_user_id = :owner_id WHERE id = 'wf-a'"),
            {"owner_id": users["a"].id},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_shares (workflow_id, user_id, access) "
                "VALUES ('wf-a', :user_id, :access)"
            ),
            [
                {"user_id": users["b"].id, "access": "view"},
                {"user_id": users["c"].id, "access": "run"},
                {"user_id": users["d"].id, "access": "edit"},
            ],
        )
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(static_dir=tmp_path, engine=db_engine)
    return db_engine, app, users


@contextmanager
def _as_user(app, user):
    sign_in(app, user)
    with TestClient(app) as client:
        yield client


def _assert_workflow_not_found(response):
    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow nicht gefunden"


def test_filtered_lists_dashboard_assets_and_reports(access_context):
    _, app, users = access_context
    with _as_user(app, users["b"]) as client:
        workflows = client.get("/api/v1/workflows").json()
        assert [workflow["id"] for workflow in workflows] == ["wf-a"]
        assert workflows[0]["owner"] == {
            "id": users["a"].id,
            "email": "a@access.test",
            "display_name": "A Owner",
        }
        assert workflows[0]["access"] == "view"

        dashboard = client.get("/api/v1/dashboard").json()
        assert [workflow["id"] for workflow in dashboard["workflows"]] == ["wf-a"]
        assert dashboard["totals"] == {
            "pending": 1, "in_progress": 2, "done": 1, "failed": 1, "on_hold": 0,
        }
        assets = client.get("/api/v1/assets").json()
        assert assets and {asset["workflow_id"] for asset in assets} == {"wf-a"}
        assert client.get("/api/v1/assets", params={"workflow_id": "wf-b"}).json() == []

        report = client.get("/api/v1/reports/job-status", params=report_params).json()
        assert [row["workflow_id"] for row in report["rows"]] == ["wf-a"]
        csv_report = client.get("/api/v1/reports/job-status.csv", params=report_params)
        assert csv_report.status_code == 200
        assert "Alpha" in csv_report.text and "Beta" not in csv_report.text
        _assert_workflow_not_found(client.get(
            "/api/v1/reports/job-status",
            params={**report_params, "workflow_id": "wf-b"},
        ))
        _assert_workflow_not_found(client.get(
            "/api/v1/reports/job-status.csv",
            params={**report_params, "workflow_id": "wf-b"},
        ))

    with _as_user(app, users["e"]) as client:
        assert client.get("/api/v1/workflows").json() == []
        dashboard = client.get("/api/v1/dashboard").json()
        assert dashboard["workflows"] == []
        assert dashboard["totals"] == {
            "pending": 0, "in_progress": 0, "done": 0, "failed": 0, "on_hold": 0,
        }
        assert client.get("/api/v1/assets").json() == []
        assert client.get("/api/v1/reports/job-status", params=report_params).json()["rows"] == []
        csv_report = client.get("/api/v1/reports/job-status.csv", params=report_params)
        assert "Alpha" not in csv_report.text and "Beta" not in csv_report.text and "Gamma" not in csv_report.text

    with _as_user(app, users["admin"]) as client:
        workflows = client.get("/api/v1/workflows").json()
        assert {workflow["id"] for workflow in workflows} == {"wf-a", "wf-b", "wf-c"}
        orphan = client.get("/api/v1/workflows/wf-b").json()
        assert orphan["owner"] is None
        assert orphan["access"] == "admin"


def test_read_routes_require_view_and_asset_detail_hides_workflow(access_context):
    _, app, users = access_context
    with _as_user(app, users["b"]) as client:
        assert client.get("/api/v1/workflows/wf-a").status_code == 200
        assert client.get("/api/v1/workflows/wf-a/graph").status_code == 200
        assert client.get("/api/v1/workflows/wf-a/modules/m2/activity").status_code == 200
        assert client.get("/api/v1/assets/a-done").status_code == 200

    with _as_user(app, users["e"]) as client:
        _assert_workflow_not_found(client.get("/api/v1/workflows/wf-a"))
        _assert_workflow_not_found(client.get("/api/v1/workflows/wf-a/graph"))
        _assert_workflow_not_found(client.get("/api/v1/workflows/wf-a/modules/m2/activity"))
        missing_asset = client.get("/api/v1/assets/does-not-exist")
        hidden_asset = client.get("/api/v1/assets/a-done")
        assert hidden_asset.status_code == missing_asset.status_code == 404
        assert hidden_asset.json() == missing_asset.json() == {"detail": "Asset nicht gefunden"}


@pytest.mark.parametrize(
    ("method", "path", "payload", "expected_detail"),
    [
        ("post", "/api/v1/workflows/wf-a/duplicate", None, "Workflow nicht gefunden"),
        ("put", "/api/v1/workflows/wf-a", {"name": "Hidden", "description": ""}, "Workflow nicht gefunden"),
        ("put", "/api/v1/workflows/wf-a/active", {"active": False}, "Workflow nicht gefunden"),
        ("delete", "/api/v1/workflows/wf-a", None, "Workflow nicht gefunden"),
        ("put", "/api/v1/workflows/wf-a/graph", graph_payload, "Workflow nicht gefunden"),
        ("post", "/api/v1/workflows/wf-a/graph/impact", graph_payload, "Workflow nicht gefunden"),
        ("get", "/api/v1/workflows/wf-a/shares", None, "Workflow nicht gefunden"),
        ("put", "/api/v1/workflows/wf-a/shares", {"shares": []}, "Workflow nicht gefunden"),
    ],
)
def test_workflow_routes_return_404_without_access(access_context, method, path, payload, expected_detail):
    _, app, users = access_context
    with _as_user(app, users["e"]) as client:
        response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
    assert response.status_code == 404
    assert response.json()["detail"] == expected_detail


def test_insufficient_access_levels_return_403(access_context):
    _, app, users = access_context
    run_request = ("post", "/api/v1/assets/reprocess", {"asset_ids": ["a-failed"], "target_workflow_id": "wf-a"})
    edit_requests = [
        ("put", "/api/v1/workflows/wf-a", {"name": "Changed", "description": ""}),
        ("put", "/api/v1/workflows/wf-a/graph", graph_payload),
        ("post", "/api/v1/workflows/wf-a/graph/impact", graph_payload),
    ]
    owner_requests = [
        ("delete", "/api/v1/workflows/wf-a", None),
        ("get", "/api/v1/workflows/wf-a/shares", None),
    ]
    with _as_user(app, users["b"]) as client:
        for method, path, payload in [
            ("put", "/api/v1/workflows/wf-a/active", {"active": False}),
            run_request,
            *edit_requests,
            *owner_requests,
        ]:
            response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
            assert response.status_code == 403
            assert response.json()["detail"] == "Keine Berechtigung für diesen Workflow"

    with _as_user(app, users["c"]) as client:
        for method, path, payload in [*edit_requests, *owner_requests]:
            response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
            assert response.status_code == 403
            assert response.json()["detail"] == "Keine Berechtigung für diesen Workflow"

    with _as_user(app, users["d"]) as client:
        for method, path, payload in owner_requests:
            response = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
            assert response.status_code == 403
            assert response.json()["detail"] == "Keine Berechtigung für diesen Workflow"


def test_create_duplicate_and_run_access_assign_owner(access_context):
    db_engine, app, users = access_context
    with _as_user(app, users["e"]) as client:
        created = client.post("/api/v1/workflows", json={"name": "Created by E"})
        assert created.status_code == 201
        assert created.json()["owner"] == {
            "id": users["e"].id,
            "email": "e@access.test",
            "display_name": "E Private",
        }
        assert created.json()["access"] == "owner"

    with _as_user(app, users["b"]) as client:
        duplicate = client.post("/api/v1/workflows/wf-a/duplicate")
        assert duplicate.status_code == 201
        copied = duplicate.json()
        assert copied["owner"]["id"] == users["b"].id
        assert copied["access"] == "owner"
        with db_engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM workflow_shares WHERE workflow_id = :id"),
                {"id": copied["id"]},
            ).scalar_one() == 0

    with _as_user(app, users["c"]) as client:
        assert client.put("/api/v1/workflows/wf-a/active", json={"active": True}).status_code == 200
        reprocessed = client.post(
            "/api/v1/assets/reprocess",
            json={"asset_ids": ["a-failed"], "target_workflow_id": "wf-a"},
        )
        assert reprocessed.status_code == 200



def test_edit_and_owner_access_levels_allow_their_operations(access_context):
    _, app, users = access_context
    with _as_user(app, users["d"]) as client:
        updated = client.put(
            "/api/v1/workflows/wf-a",
            json={"name": "Alpha edited", "description": "Updated by D"},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Alpha edited"
        assert updated.json()["access"] == "edit"
        graph = client.get("/api/v1/workflows/wf-a/graph").json()
        graph_payload_for_edit = {
            "base_version": graph["version"],
            "modules": graph["modules"],
            "connections": graph["connections"],
        }
        saved = client.put("/api/v1/workflows/wf-a/graph", json=graph_payload_for_edit)
        assert saved.status_code == 200
        assert client.post(
            "/api/v1/workflows/wf-a/graph/impact", json=graph_payload_for_edit
        ).status_code == 200

    with _as_user(app, users["a"]) as client:
        assert client.get("/api/v1/workflows/wf-a").json()["access"] == "owner"
        created = client.post("/api/v1/workflows", json={"name": "Owner delete test"})
        assert created.status_code == 201
        assert client.delete(f"/api/v1/workflows/{created.json()['id']}").status_code == 204


def test_reprocess_requires_run_on_target_workflow(access_context):
    db_engine, app, users = access_context
    with _as_user(app, users["c"]) as client:
        response = client.post(
            "/api/v1/assets/reprocess",
            json={"asset_ids": ["a-failed"], "target_workflow_id": "wf-b"},
        )
        _assert_workflow_not_found(response)
    with db_engine.connect() as connection:
        state = connection.execute(
            text("SELECT workflow_id, status FROM assets WHERE id = 'a-failed'")
        ).one()
    assert tuple(state) == ("wf-a", "failed")


def test_shares_get_put_validation_and_sorting(access_context):
    _, app, users = access_context
    with _as_user(app, users["a"]) as client:
        shares = client.get("/api/v1/workflows/wf-a/shares")
        assert shares.status_code == 200
        assert [share["email"] for share in shares.json()["shares"]] == [
            "b@access.test", "c@access.test", "d@access.test",
        ]
        assert all(share["user_id"] != users["a"].id for share in shares.json()["shares"])

        cases = [
            ([{"user_id": "unknown-user", "access": "view"}], "Unbekannter Nutzer: unknown-user"),
            ([{"user_id": users["a"].id, "access": "view"}], "Der Eigentümer kann nicht freigegeben werden"),
            ([{"user_id": users["b"].id, "access": "view"}, {"user_id": users["b"].id, "access": "run"}], f"Nutzer doppelt: {users['b'].id}"),
        ]
        for payload, detail in cases:
            response = client.put("/api/v1/workflows/wf-a/shares", json={"shares": payload})
            assert response.status_code == 422
            assert response.json()["detail"] == detail

        invalid_access = client.put(
            "/api/v1/workflows/wf-a/shares",
            json={"shares": [{"user_id": users["b"].id, "access": "admin"}]},
        )
        assert invalid_access.status_code == 422

        replaced = client.put(
            "/api/v1/workflows/wf-a/shares",
            json={"shares": [
                {"user_id": users["e"].id, "access": "view"},
                {"user_id": users["b"].id, "access": "run"},
            ]},
        )
        assert replaced.status_code == 200
        assert [(share["email"], share["access"]) for share in replaced.json()["shares"]] == [
            ("b@access.test", "run"), ("e@access.test", "view"),
        ]


def test_owner_assignment_is_admin_only_and_removes_new_owners_share(access_context):
    _, app, users = access_context
    with _as_user(app, users["d"]) as client:
        forbidden = client.put("/api/v1/workflows/wf-a/owner", json={"user_id": users["e"].id})
        assert forbidden.status_code == 403

    with _as_user(app, users["admin"]) as client:
        unknown = client.put("/api/v1/workflows/wf-a/owner", json={"user_id": "unknown-user"})
        assert unknown.status_code == 422
        assert unknown.json()["detail"] == "Unbekannter Nutzer: unknown-user"
        changed = client.put("/api/v1/workflows/wf-a/owner", json={"user_id": users["d"].id})
        assert changed.status_code == 200
        assert changed.json()["owner"]["id"] == users["d"].id

    with _as_user(app, users["d"]) as client:
        shares = client.get("/api/v1/workflows/wf-a/shares").json()["shares"]
        assert users["d"].id not in {share["user_id"] for share in shares}
        assert client.get("/api/v1/workflows/wf-a").json()["access"] == "owner"


def test_module_activity_logs_filter_levels_before_latest_500(access_context):
    db_engine, app, users = access_context
    with db_engine.begin() as connection:
        rows = [
            {
                "workflow_id": "wf-a",
                "module_id": "m2",
                "ts": fixed_now + timedelta(seconds=index),
                "level": "warn" if index % 2 else "error",
                "text": f"warning-{index}",
            }
            for index in range(501)
        ]
        rows.append({
            "workflow_id": "wf-a",
            "module_id": "m2",
            "ts": fixed_now + timedelta(seconds=600),
            "level": "info",
            "text": "newest-info",
        })
        connection.execute(
            text(
                "INSERT INTO log_lines (workflow_id, module_id, ts, level, text) "
                "VALUES (:workflow_id, :module_id, :ts, :level, :text)"
            ),
            rows,
        )

    with _as_user(app, users["b"]) as client:
        response = client.get("/api/v1/workflows/wf-a/modules/m2/activity")
    assert response.status_code == 200
    logs = response.json()["logs"]
    assert len(logs) == 500
    assert {row["level"] for row in logs} <= {"warn", "error"}
    assert [row["text"] for row in logs] == [f"warning-{index}" for index in range(1, 501)]
