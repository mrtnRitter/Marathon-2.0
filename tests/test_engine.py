from datetime import datetime, timedelta, timezone

import pytest

from marathon.server import APIError, Engine, MemoryStore, dispatch


def graph(second_fail=True):
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "wait-1", "type": "wait", "settings": {"seconds": 0, "fail": False}},
            {"id": "wait-2", "type": "wait", "settings": {"seconds": 0, "fail": second_fail}},
        ],
        "edges": [
            {"from": "start", "to": "wait-1"},
            {"from": "wait-1", "to": "wait-2"},
        ],
    }


def ready_worker(engine, **values):
    worker = engine.register_worker({"name": "runner", "capabilities": ["wait"], "max_parallel": 1, **values})
    engine.worker_action(worker["id"], "approve", {})
    return worker["id"]


def run_next(engine, worker_id, result=None):
    jobs = engine.claim_jobs(worker_id)
    assert len(jobs) == 1
    job = jobs[0]
    result = result or ("failed" if job["settings"].get("fail") else "success")
    engine.job_result(worker_id, job["id"], {"status": result})
    return job


def test_asset_job_worker_failure_retry_and_history():
    engine = Engine(MemoryStore())
    workflow = engine.create_workflow({"name": "Pipeline", "graph": graph()})
    asset = engine.create_asset(workflow["id"], "clip", {"camera": "A"})
    worker_id = ready_worker(engine)

    first = run_next(engine, worker_id)
    assert first["module_id"] == "wait-1"
    second = run_next(engine, worker_id)
    assert second["module_id"] == "wait-2"
    assert engine.asset_detail(asset["id"])["status"] == "failed"

    engine.update_workflow(workflow["id"], {"graph": graph(second_fail=False)})
    engine.asset_action(asset["id"], "retry")
    retried_first = run_next(engine, worker_id)
    retried_second = run_next(engine, worker_id)
    detail = engine.asset_detail(asset["id"])
    assert retried_first["id"] != first["id"]
    assert retried_second["module_id"] == "wait-2"
    assert detail["status"] == "completed"
    assert len(detail["history"]) >= 10
    assert all(len(job["asset_ids"]) == 1 for job in detail["jobs"])


def test_paused_workflow_does_not_publish_until_resumed():
    engine = Engine(MemoryStore())
    workflow = engine.create_workflow({"name": "Paused", "graph": graph(), "enabled": False})
    asset = engine.create_asset(workflow["id"], "later")
    worker_id = ready_worker(engine)

    assert engine.list_jobs() == []
    assert engine.claim_jobs(worker_id) == []
    engine.set_workflow_enabled(workflow["id"], True)
    assert len(engine.list_jobs(status="queued")) == 1


def test_offline_worker_cannot_claim_and_running_job_is_requeued():
    engine = Engine(MemoryStore(), heartbeat_timeout_seconds=10)
    workflow = engine.create_workflow({"name": "Offline", "graph": graph()})
    engine.create_asset(workflow["id"], "clip")
    worker_id = ready_worker(engine)
    job = engine.claim_jobs(worker_id)[0]

    old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    engine.state["workers"][worker_id]["last_seen"] = old
    engine.store.save(engine.state)
    assert engine.worker_status(engine.state["workers"][worker_id]) == "offline"
    assert engine.expire_workers() == 1
    assert engine.state["jobs"][job["id"]]["status"] == "queued"
    assert engine.claim_jobs(worker_id) == []

    replacement = ready_worker(engine, name="replacement")
    claimed = engine.claim_jobs(replacement)
    assert len(claimed) == 1
    assert claimed[0]["id"] == job["id"]


def test_module_descriptions_and_csv_import_api():
    engine = Engine(MemoryStore())
    root = __import__("pathlib").Path(".")
    response = dispatch("GET", "/api/module-types", "", {}, engine, root)
    assert [item["type"] for item in response.data["items"]] == ["start", "wait"]
    workflow = engine.create_workflow({"name": "CSV"})
    with pytest.raises(APIError):
        dispatch("POST", "/api/assets/import", "", f"workflow_id={workflow['id']}", engine, root)
    result = dispatch("POST", "/api/assets/import", f"workflow_id={workflow['id']}", "name,source\nshot-1,camera-a\n", engine, root)
    assert result.status == 201
    assert result.data["items"][0]["metadata"] == {"source": "camera-a"}


def test_workflow_rejects_invalid_graph():
    engine = Engine(MemoryStore())
    with pytest.raises(APIError):
        engine.create_workflow({"name": "Bad", "graph": {"nodes": [{"id": "a", "type": "wait"}], "edges": [{"from": "a", "to": "missing"}]}})


def test_parallel_limit_and_capability_filtering():
    engine = Engine(MemoryStore())
    workflow = engine.create_workflow({"name": "Capacity", "graph": graph()})
    engine.create_asset(workflow["id"], "one")
    engine.create_asset(workflow["id"], "two")
    wrong = ready_worker(engine, name="wrong", capabilities=["other"])
    assert engine.claim_jobs(wrong) == []
    worker_id = ready_worker(engine, name="limited", max_parallel=1)
    assert len(engine.claim_jobs(worker_id)) == 1
    assert engine.claim_jobs(worker_id) == []


def test_removing_current_module_fails_asset_immediately():
    engine = Engine(MemoryStore())
    workflow = engine.create_workflow({"name": "Changed", "graph": graph()})
    asset = engine.create_asset(workflow["id"], "clip")
    job = next(iter(engine.state["jobs"].values()))
    engine.update_workflow(workflow["id"], {"graph": {"nodes": [{"id": "start", "type": "start"}], "edges": []}})
    assert engine.asset_detail(asset["id"])["status"] == "failed"
    assert engine.state["jobs"][job["id"]]["status"] == "failed"


def test_package_index_rollout_and_download(tmp_path):
    import json
    import zipfile

    engine = Engine(MemoryStore())
    worker_id = ready_worker(engine)
    package_dir = tmp_path / "dist" / "client"
    package_dir.mkdir(parents=True)
    archive_path = package_dir / "r1.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"version": "r1"}))
        archive.writestr("worker.py", "# bundled client")
    config = {"rollout": {"package_dir": "dist/client"}}

    available = dispatch("GET", "/api/client-packages", "", {}, engine, tmp_path, config)
    assert available.data["items"][0]["version"] == "r1"
    rollout = dispatch("POST", "/api/rollouts", "", {"version": "r1", "worker_ids": [worker_id]}, engine, tmp_path, config)
    assert rollout.status == 202
    assert engine._worker(worker_id)["desired_client_version"] == "r1"
    download = dispatch("GET", "/api/client-packages/r1", "", {}, engine, tmp_path, config)
    assert download.content_type == "application/zip"
    assert download.data.startswith(b"PK")


def test_parallel_graph_waits_for_join():
    engine = Engine(MemoryStore())
    workflow = engine.create_workflow({
        "name": "Branches",
        "graph": {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "left", "type": "wait"},
                {"id": "right", "type": "wait"},
                {"id": "join", "type": "wait"},
            ],
            "edges": [
                {"from": "start", "to": "left"},
                {"from": "start", "to": "right"},
                {"from": "left", "to": "join"},
                {"from": "right", "to": "join"},
            ],
        },
    })
    asset = engine.create_asset(workflow["id"], "branch")
    worker_id = ready_worker(engine, max_parallel=2)
    first_jobs = engine.claim_jobs(worker_id)
    assert {job["module_id"] for job in first_jobs} == {"left", "right"}
    engine.job_result(worker_id, first_jobs[0]["id"], {"status": "success"})
    assert not any(job["module_id"] == "join" for job in engine.list_jobs(status="queued"))
    engine.job_result(worker_id, first_jobs[1]["id"], {"status": "success"})
    join_job = engine.claim_jobs(worker_id)
    assert len(join_job) == 1 and join_job[0]["module_id"] == "join"
    engine.job_result(worker_id, join_job[0]["id"], {"status": "success"})
    assert engine.asset_detail(asset["id"])["status"] == "completed"
