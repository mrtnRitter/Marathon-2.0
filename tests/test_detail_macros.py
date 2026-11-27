"""Instanzbasierte Workflow-Detailtests für Makros."""

# IMPORTS
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.detail_queries import get_graph
from records import insert_records


# CONFIG
fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


# FUNC
def _composite_definition(name: str = "Zwei Bausteine") -> dict:
    """Build a two-block definition with one externally visible success exit."""
    return {
        "name": name,
        "category": "Test",
        "icon": "clock",
        "description": "Verbundmodul",
        "sort_order": 3,
        "blocks": [
            {"id": "first", "type_id": "step", "name": "Erster", "position": {"x": 0, "y": 0}, "settings": {}},
            {"id": "second", "type_id": "step", "name": "Zweiter", "position": {"x": 100, "y": 0}, "settings": {}},
        ],
        "connections": [{"source_block_id": "first", "source_output_id": "success", "target_block_id": "second"}],
        "entry_block_id": "first",
        "outputs": [
            {"id": "success", "label": "Erfolg", "description": "", "sources": [{"block_id": "second", "output_id": "success"}]},
            {"id": "error", "label": "Fehler", "description": "", "sources": [{"block_id": "second", "output_id": "failure"}]},
        ],
        "settings": [],
    }


def _insert_composite(connection, current_version: int = 2) -> None:
    """Insert one macro instance expanded into two runtime modules."""
    definition = _composite_definition()
    newer_definition = _composite_definition("Zwei Bausteine v2")
    connection.execute(text(
        "INSERT INTO macros (id, owner_user_id, current_version) VALUES ('macro-composite', NULL, :version)"
    ), {"version": current_version})
    for version, value in ((1, definition), (2, newer_definition)):
        connection.execute(text(
            "INSERT INTO macro_versions (macro_id, version, definition) VALUES ('macro-composite', :version, CAST(:definition AS jsonb))"
        ), {"version": version, "definition": json.dumps(value)})
    connection.execute(text(
        "INSERT INTO workflow_instances (workflow_id, version, id, macro_id, macro_version, name, description, position_x, position_y, settings, locked) "
        "VALUES ('wf-a', 2, 'multi', 'macro-composite', 1, 'Verbund', '', 600, 0, '{}'::jsonb, false)"
    ))
    connection.execute(text(
        "INSERT INTO workflow_modules (workflow_id, version, id, type_id, name, description, position_x, position_y, settings, locked, instance_id, block_id, exits) "
        "VALUES ('wf-a', 2, 'multi.first', 'step', 'Verbund', '', 600, 0, '{}'::jsonb, false, 'multi', 'first', '{}'::jsonb), "
        "('wf-a', 2, 'multi.second', 'step', 'Verbund', '', 600, 0, '{}'::jsonb, false, 'multi', 'second', '{\"success\":\"success\",\"failure\":\"error\"}'::jsonb)"
    ))
    connection.execute(text(
        "INSERT INTO workflow_connections (workflow_id, version, source_module_id, source_output_id, target_module_id) "
        "VALUES ('wf-a', 2, 'multi.first', 'success', 'multi.second')"
    ))


def _insert_assets_and_jobs(connection, rows: list[tuple[str, str, str, str | None]], now: datetime) -> None:
    """Insert assets and completed jobs for counter assertions."""
    for index, (asset_id, module_id, job_id, output_id) in enumerate(rows):
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, list_position, metadata, created_at, updated_at) "
            "VALUES (:asset_id, 'wf-a', :asset_id, 'done', :module_id, NULL, :position, '{}'::jsonb, :now, :now)"
        ), {"asset_id": asset_id, "module_id": module_id, "position": 20 + index, "now": now})
        connection.execute(text(
            "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, output_id, created_at, started_at, finished_at) "
            "VALUES (:job_id, 'wf-a', :module_id, 'succeeded', NULL, 1, :output_id, :now, :now, :now)"
        ), {"job_id": job_id, "module_id": module_id, "output_id": output_id, "now": now})
        connection.execute(text(
            "INSERT INTO job_assets (job_id, asset_id, outcome, output_id) VALUES (:job_id, :asset_id, 'result', :output_id)"
        ), {"job_id": job_id, "asset_id": asset_id, "output_id": output_id})


# TESTS
def test_standard_instance_has_hull_versions_permissions_and_null_done(db_engine):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        connection.execute(text(
            "UPDATE job_assets SET outcome = 'result', output_id = NULL WHERE job_id = 'j1'"
        ))
        admin = AuthUser("test-admin", "admin@test.local", "Admin", "admin", "session")
        ordinary = AuthUser("test-user", "user@test.local", "User", "user", "session")
        admin_module = next(item for item in get_graph(connection, "wf-a", admin)["modules"] if item["id"] == "m2")
        ordinary_module = next(item for item in get_graph(connection, "wf-a", ordinary)["modules"] if item["id"] == "m2")
        anonymous_module = next(item for item in get_graph(connection, "wf-a")["modules"] if item["id"] == "m2")
    assert admin_module["type_id"] == "step"
    assert admin_module["macro_version"] == admin_module["latest_version"] == 1
    assert admin_module["update_available"] is False
    assert admin_module["can_edit"] is True
    assert ordinary_module["can_edit"] is False
    assert anonymous_module["can_edit"] is False
    assert admin_module["hull"]["id"] == "step"
    assert admin_module["hull"]["version"] == 1
    assert admin_module["counters"]["done"] == 1


def test_composite_instance_sums_counters_and_applies_done_exit_rules(db_engine):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        _insert_composite(connection)
        _insert_assets_and_jobs(connection, [
            ("done-null", "multi.second", "job-null", None),
            ("done-internal", "multi.first", "job-internal", "success"),
            ("done-success", "multi.second", "job-success", "success"),
            ("done-mapped-error", "multi.second", "job-mapped-error", "failure"),
            ("done-error", "multi.second", "job-error", "error"),
            ("done-unknown", "multi.second", "job-unknown", "mystery"),
        ], fixed_now)
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, list_position, metadata, created_at, updated_at) "
            "VALUES ('multi-waiting', 'wf-a', 'waiting', 'in_progress', 'multi.first', 'waiting', 30, '{}'::jsonb, :now, :now), "
            "('multi-failed', 'wf-a', 'failed', 'failed', 'multi.second', NULL, 31, '{}'::jsonb, :now, :now), "
            "('multi-running', 'wf-a', 'running', 'in_progress', 'multi.second', 'running', 32, '{}'::jsonb, :now, :now)"
        ), {"now": fixed_now})
        connection.execute(text(
            "INSERT INTO watch_modules (workflow_id, module_id, next_scan_at) VALUES ('wf-a', 'multi.first', :now)"
        ), {"now": fixed_now})
        connection.execute(text(
            "INSERT INTO watch_files (workflow_id, module_id, path, size, mtime_ns, stable_since, asset_id) "
            "VALUES ('wf-a', 'multi.first', '/watched.mov', 10, 1, :now, 'multi-waiting')"
        ), {"now": fixed_now})
        graph = get_graph(connection, "wf-a", AuthUser("test-admin", "admin@test.local", "Admin", "admin", "session"))
    module = next(item for item in graph["modules"] if item["id"] == "multi")
    assert module["macro_version"] == 1
    assert module["latest_version"] == 2
    assert module["update_available"] is True
    assert module["counters"] == {"waiting": 1, "running": 1, "done": 3, "failed": 1, "discovered": 1}


def test_composite_activity_aggregates_runtime_modules_and_instance_logs(db_engine, tmp_path):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        _insert_composite(connection, current_version=1)
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, list_position, metadata, created_at, updated_at) "
            "VALUES ('activity-first', 'wf-a', 'first', 'in_progress', 'multi.first', 'waiting', 20, '{}'::jsonb, :now, :now), "
            "('activity-second', 'wf-a', 'second', 'in_progress', 'multi.second', 'running', 21, '{}'::jsonb, :now, :now)"
        ), {"now": fixed_now})
        connection.execute(text(
            "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, output_id, created_at, started_at) "
            "VALUES ('activity-job-first', 'wf-a', 'multi.first', 'assigned', NULL, 1, NULL, :now, :now), "
            "('activity-job-second', 'wf-a', 'multi.second', 'running', NULL, 1, NULL, :now, :now)"
        ), {"now": fixed_now})
        connection.execute(text(
            "INSERT INTO job_assets (job_id, asset_id) VALUES ('activity-job-first', 'activity-first'), ('activity-job-second', 'activity-second')"
        ))
        connection.execute(text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, asset_id, ts, level, text) "
            "VALUES ('wf-a', 'multi.first', 'activity-job-first', 'activity-first', :now, 'warn', 'first warning'), "
            "('wf-a', 'multi.second', 'activity-job-second', 'activity-second', :now, 'error', 'second error'), "
            "('wf-a', 'multi', NULL, NULL, :now, 'warn', 'instance warning')"
        ), {"now": fixed_now})
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(app)
    with TestClient(app) as client:
        response = client.get("/api/v1/workflows/wf-a/modules/multi/activity")
        assert response.status_code == 200
        activity = response.json()
        assert {item["id"] for item in activity["assets"]} == {"activity-first", "activity-second"}
        assert {item["id"] for item in activity["jobs"]} == {"activity-job-first", "activity-job-second"}
        assert {item["text"] for item in activity["logs"]} == {"first warning", "second error", "instance warning"}
        assert client.get("/api/v1/workflows/wf-a/modules/unknown/activity").status_code == 404
