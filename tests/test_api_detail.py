"""Detail API integration tests backed by the local PostgreSQL fixture."""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from auth_helpers import sign_in
from sqlalchemy import text

from marathon.server.app import create_app
from records import insert_records
from marathon.server.db.queries import list_assets


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_graph_returns_current_workflow_modules_and_counters(db_engine, tmp_path):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO module_types (id, name, category, icon, description, has_input, outputs, settings, sort_order) "
            "VALUES ('watchfolder', 'Watchfolder', 'Input', 'folder', '', false, '[]'::jsonb, '[]'::jsonb, 2)"
        ))
        watchfolder_definition = {
            "name": "Watchfolder", "category": "Input", "icon": "folder", "description": "", "sort_order": 2,
            "blocks": [{"id": "main", "type_id": "watchfolder", "name": "Watchfolder", "position": {"x": 0, "y": 0}, "settings": {}}],
            "connections": [], "entry_block_id": None, "outputs": [], "settings": [],
        }
        connection.execute(text(
            "INSERT INTO macros (id, owner_user_id, current_version) VALUES ('watchfolder', NULL, 1)"
        ))
        connection.execute(text(
            "INSERT INTO macro_versions (macro_id, version, definition) VALUES ('watchfolder', 1, CAST(:definition AS jsonb))"
        ), {"definition": json.dumps(watchfolder_definition)})
        connection.execute(text(
            "UPDATE workflow_instances SET macro_id = 'watchfolder' "
            "WHERE workflow_id = 'wf-a' AND version = 2 AND id = 'm1'"
        ))
        connection.execute(text(
            "UPDATE workflow_modules SET type_id = 'watchfolder', exits = '{}'::jsonb "
            "WHERE workflow_id = 'wf-a' AND version = 2 AND id = 'm1'"
        ))
        connection.execute(text(
            "INSERT INTO watch_modules (workflow_id, module_id, next_scan_at) VALUES ('wf-a', 'm1', :next_scan_at)"
        ), {"next_scan_at": fixed_now})
        connection.execute(text(
            "INSERT INTO watch_files (workflow_id, module_id, path, size, mtime_ns, stable_since, asset_id) "
            "VALUES ('wf-a', 'm1', '/one.mov', 10, 1, :stable_since, 'a-running'), "
            "('wf-a', 'm1', '/two.mov', 20, 2, :stable_since, NULL)"
        ), {"stable_since": fixed_now})
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _test_app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(_test_app)
    with TestClient(_test_app) as client:
        response = client.get("/api/v1/workflows/wf-a/graph")
        assert response.status_code == 200
        graph = response.json()
        assert graph["workflow_id"] == "wf-a"
        assert len(graph["modules"]) == 3
        assert len(graph["connections"]) == 2
        assert all(module["description"] == "" for module in graph["modules"])
        assert {module["id"]: module["counters"]["discovered"] for module in graph["modules"]} == {
            "m1": 1, "m2": 0, "m3": 0
        }
        assert {module["id"]: module["locked"] for module in graph["modules"]} == {
            "m1": True, "m2": False, "m3": False
        }
        with db_engine.connect() as connection:
            running = connection.execute(text(
                "SELECT COUNT(*) FROM assets WHERE workflow_id = :workflow_id AND step_status = 'running'"
            ), {"workflow_id": "wf-a"}).scalar_one()
            assert sum(module["counters"]["running"] for module in graph["modules"]) == running
            for module in graph["modules"]:
                done = connection.execute(text(
                    "SELECT COUNT(*) FROM job_assets AS job_asset JOIN jobs AS job ON job.id = job_asset.job_id "
                    "WHERE job.workflow_id = :workflow_id AND job.module_id = :module_id "
                    "AND job_asset.outcome = 'result' AND job_asset.output_id IS DISTINCT FROM 'error'"
                ), {"workflow_id": "wf-a", "module_id": module["id"]}).scalar_one()
                assert module["counters"]["done"] == done

        missing = client.get("/api/v1/workflows/nope/graph")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Workflow nicht gefunden"


def test_module_activity_contains_related_assets_workers_and_logs(db_engine, tmp_path):
    insert_records(db_engine, fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _test_app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(_test_app)
    with TestClient(_test_app) as client:
        response = client.get("/api/v1/workflows/wf-a/modules/m2/activity")
        assert response.status_code == 200
        activity = response.json()
        asset_ids = {asset["id"] for asset in activity["assets"]}
        assert asset_ids == {"a-running", "a-waiting"}
        assert all(set(job["asset_ids"]) <= asset_ids for job in activity["jobs"])
        assert {worker["name"] for worker in activity["workers"]} == {
            job["worker"] for job in activity["jobs"] if job["worker"] is not None
        }
        assert activity["logs"]
        with db_engine.connect() as connection:
            assert [asset["id"] for asset in activity["assets"]] == [
                asset["id"] for asset in list_assets(connection, workflow_id="wf-a")
                if asset["current_module_id"] == "m2"
            ]

        missing = client.get("/api/v1/workflows/wf-a/modules/nope/activity")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Modul nicht gefunden"


def test_asset_detail_timeline_messages_logs_and_legacy_asset_list(db_engine, tmp_path):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        connection.execute(text(
            "UPDATE job_assets SET output_id = CASE WHEN job_id IN ('j1', 'j2', 'j3') "
            "THEN 'success' WHEN job_id = 'j4' THEN 'failure' END "
            "WHERE asset_id IN ('a-done', 'a-failed')"
        ))
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _test_app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(_test_app)
    with TestClient(_test_app) as client:
        done_assets = client.get("/api/v1/assets", params={"status": "done"}).json()
        done = client.get(f"/api/v1/assets/{done_assets[0]['id']}")
        assert done.status_code == 200
        done_detail = done.json()
        assert done_detail["timeline"]
        assert all(entry["status"] == "succeeded" for entry in done_detail["timeline"])
        assert done_detail["timeline"][-1]["output_label"] is not None
        assert done_detail["log"]
        assert all(set(line) == {"ts", "level", "text"} for line in done_detail["log"])

        pending_assets = client.get("/api/v1/assets", params={"status": "pending"}).json()
        pending = client.get(f"/api/v1/assets/{pending_assets[0]['id']}")
        assert pending.status_code == 200
        assert pending.json()["timeline"] == []
        assert pending.json()["log"] == []

        failed_assets = client.get("/api/v1/assets", params={"status": "failed"}).json()
        failed = client.get(f"/api/v1/assets/{failed_assets[0]['id']}")
        assert failed.status_code == 200
        last = failed.json()["timeline"][-1]
        assert last["status"] == "failed"
        assert last["attempts"] == 3
        assert last["output_label"] == "Fehler"

        for detail in (done_detail, pending.json(), failed.json()):
            assert "created_at" in detail and "metadata" in detail
            assert all(isinstance(item["duration_s"], int) and item["duration_s"] >= 0
                       for item in detail["timeline"])

        assert client.get("/api/v1/assets/nope").status_code == 404
        assert client.get("/api/v1/assets").status_code == 200



def test_graph_done_counter_counts_asset_results_not_jobs(db_engine, tmp_path):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO workflow_instances (workflow_id, version, id, macro_id, macro_version, name, description, position_x, position_y, settings, locked) "
            "VALUES ('wf-a', 2, 'm-batch', 'step', 1, 'Batch', '', 600, 0, '{}'::jsonb, false)"
        ))
        connection.execute(text(
            "INSERT INTO workflow_modules (workflow_id, version, id, type_id, name, description, position_x, position_y, settings, locked, instance_id, block_id, exits) "
            "VALUES ('wf-a', 2, 'm-batch', 'step', 'Batch', '', 600, 0, '{}'::jsonb, false, 'm-batch', 'main', CAST(:exits AS jsonb))"
        ), {"exits": json.dumps({"success": "success", "failure": "failure"})})
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, list_position, metadata, created_at, updated_at) "
            "VALUES ('a-batch-1', 'wf-a', 'batch-1', 'done', 'm-batch', NULL, 8, '{}'::jsonb, :now, :now), "
            "('a-batch-2', 'wf-a', 'batch-2', 'done', 'm-batch', NULL, 9, '{}'::jsonb, :now, :now), "
            "('a-batch-3', 'wf-a', 'batch-3', 'done', 'm-batch', NULL, 10, '{}'::jsonb, :now, :now)"
        ), {"now": fixed_now})
        connection.execute(text(
            "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, output_id, created_at, started_at, finished_at) "
            "VALUES ('j-batch', 'wf-a', 'm-batch', 'succeeded', 'w1', 1, 'success', :now, :now, :now)"
        ), {"now": fixed_now})
        connection.execute(text(
            "INSERT INTO job_assets (job_id, asset_id, outcome, output_id) "
            "VALUES ('j-batch', 'a-batch-1', 'result', 'success'), "
            "('j-batch', 'a-batch-2', 'result', 'success'), "
            "('j-batch', 'a-batch-3', 'result', 'error')"
        ))
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _test_app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(_test_app)
    with TestClient(_test_app) as client:
        graph = client.get("/api/v1/workflows/wf-a/graph").json()
        batch = next(module for module in graph["modules"] if module["id"] == "m-batch")
        assert batch["counters"]["done"] == 2


def test_module_activity_logs_include_job_context_and_nulls(db_engine, tmp_path):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        connection.execute(text(
            "DELETE FROM log_lines WHERE workflow_id = 'wf-a' AND module_id = 'm2'"
        ))
        connection.execute(text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, asset_id, ts, level, text) "
            "VALUES ('wf-a', 'm2', 'j1', 'a-running', :job_ts, 'info', 'job log'), "
            "('wf-a', 'm2', NULL, NULL, :module_ts, 'warn', 'module log'), "
            "('wf-a', 'm2', 'j6', 'a-waiting', :null_worker_ts, 'debug', 'null worker log')"
        ), {
            "job_ts": fixed_now + timedelta(seconds=1),
            "module_ts": fixed_now + timedelta(seconds=2),
            "null_worker_ts": fixed_now + timedelta(seconds=3),
        })
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _test_app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(_test_app)
    with TestClient(_test_app) as client:
        response = client.get("/api/v1/workflows/wf-a/modules/m2/activity")
        assert response.status_code == 200
        logs = response.json()["logs"]
        assert [(line["text"], line["asset"], line["worker"]) for line in logs] == [
            ("module log", None, None),
        ]


def test_module_activity_returns_latest_500_logs_in_ascending_order(db_engine, tmp_path):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, ts, level, text) "
            "VALUES (:workflow_id, :module_id, NULL, :ts, 'warn', :text)"
        ), [
            {
                "workflow_id": "wf-a", "module_id": "m2",
                "ts": fixed_now + timedelta(seconds=index), "text": f"log-{index}",
            }
            for index in range(505)
        ])
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _test_app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(_test_app)
    with TestClient(_test_app) as client:
        response = client.get("/api/v1/workflows/wf-a/modules/m2/activity")
        assert response.status_code == 200
        logs = response.json()["logs"]
        assert len(logs) == 500
        assert [line["text"] for line in logs] == [f"log-{index}" for index in range(5, 505)]


def test_asset_timeline_uses_each_job_asset_output(db_engine, tmp_path):
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, output_id, created_at, started_at, finished_at) "
            "VALUES ('j-multi-output', 'wf-a', 'm2', 'succeeded', 'w1', 1, 'success', :now, :now, :now)"
        ), {"now": fixed_now + timedelta(seconds=1)})
        connection.execute(text(
            "INSERT INTO job_assets (job_id, asset_id, outcome, output_id) "
            "VALUES ('j-multi-output', 'a-running', 'result', 'success'), "
            "('j-multi-output', 'a-waiting', 'result', 'failure')"
        ))
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _test_app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(_test_app)
    with TestClient(_test_app) as client:
        outputs_by_asset = {}
        for asset_id in ("a-running", "a-waiting"):
            response = client.get(f"/api/v1/assets/{asset_id}")
            assert response.status_code == 200
            entry = next(row for row in response.json()["timeline"] if row["job_id"] == "j-multi-output")
            outputs_by_asset[asset_id] = entry["output_label"]
        assert outputs_by_asset == {"a-running": "Erfolg", "a-waiting": "Fehler"}
