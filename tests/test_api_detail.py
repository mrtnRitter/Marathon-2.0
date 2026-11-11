"""Detail API integration tests backed by the local PostgreSQL fixture."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from marathon.server.db.demo import load_demo_data
from marathon.server.db.queries import list_assets


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_graph_returns_current_workflow_modules_and_counters(db_engine, tmp_path):
    load_demo_data(db_engine, now=fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        response = client.get("/api/v1/workflows/wf-ingest/graph")
        assert response.status_code == 200
        graph = response.json()
        assert graph["workflow_id"] == "wf-ingest"
        assert len(graph["modules"]) == 6
        assert len(graph["connections"]) == 6
        assert all(module["description"] == "" for module in graph["modules"])
        assert {module["id"]: module["locked"] for module in graph["modules"]} == {
            module_id: module_id == "m1" for module_id in ("m1", "m2", "m3", "m4", "m5", "m6")
        }
        with db_engine.connect() as connection:
            running = connection.execute(text(
                "SELECT COUNT(*) FROM assets WHERE workflow_id = :workflow_id AND step_status = 'running'"
            ), {"workflow_id": "wf-ingest"}).scalar_one()
            assert sum(module["counters"]["running"] for module in graph["modules"]) == running
            for module in graph["modules"]:
                done = connection.execute(text(
                    "SELECT COUNT(*) FROM jobs WHERE workflow_id = :workflow_id AND module_id = :module_id "
                    "AND status = 'succeeded'"
                ), {"workflow_id": "wf-ingest", "module_id": module["id"]}).scalar_one()
                assert module["counters"]["done"] == done

        missing = client.get("/api/v1/workflows/nope/graph")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Workflow nicht gefunden"


def test_module_activity_contains_related_assets_workers_and_logs(db_engine, tmp_path):
    load_demo_data(db_engine, now=fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        response = client.get("/api/v1/workflows/wf-ingest/modules/m5/activity")
        assert response.status_code == 200
        activity = response.json()
        asset_ids = {asset["id"] for asset in activity["assets"]}
        assert asset_ids
        assert all(set(job["asset_ids"]) <= asset_ids for job in activity["jobs"])
        assert {worker["name"] for worker in activity["workers"]} == {
            job["worker"] for job in activity["jobs"] if job["worker"] is not None
        }
        assert activity["logs"]
        with db_engine.connect() as connection:
            assert [asset["id"] for asset in activity["assets"]] == [
                asset["id"] for asset in list_assets(connection, workflow_id="wf-ingest")
                if asset["current_module_id"] == "m5"
            ]

        missing = client.get("/api/v1/workflows/wf-ingest/modules/nope/activity")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Modul nicht gefunden"


def test_asset_detail_timeline_messages_logs_and_legacy_asset_list(db_engine, tmp_path):
    load_demo_data(db_engine, now=fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        done_assets = client.get("/api/v1/assets", params={"status": "done"}).json()
        done = client.get(f"/api/v1/assets/{done_assets[0]['id']}")
        assert done.status_code == 200
        done_detail = done.json()
        assert done_detail["timeline"]
        assert all(entry["status"] == "succeeded" for entry in done_detail["timeline"])
        assert done_detail["timeline"][-1]["output_label"] is not None
        assert done_detail["log"]

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
