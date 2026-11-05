import asyncio
from datetime import timedelta

import httpx

from m2.server.app import create_app


def request(app, method, path, **kwargs):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(send())


def make_workflow(app, edges=None, max_attempts=2):
    response = request(app, "POST", "/api/workflows", json={"name": "test"})
    workflow_id = response.json()["id"]
    graph = {
        "nodes": [
            {"id": "source", "module": "manual_source", "params": {}},
            {"id": "wait-node", "module": "wait", "params": {"seconds": 0},
             "retry": {"max_attempts": max_attempts}},
            {"id": "copy-node", "module": "copy_file", "params": {},
             "retry": {"max_attempts": 1}},
        ],
        "edges": edges if edges is not None else [
            {"id": "e1", "source": "source", "source_port": "success", "target": "wait-node"},
            {"id": "e2", "source": "wait-node", "source_port": "success", "target": "copy-node"},
        ],
    }
    response = request(app, "POST", f"/api/workflows/{workflow_id}/versions", json={"graph": graph})
    assert response.status_code == 200
    return workflow_id


def register(app, modules, capabilities):
    response = request(app, "POST", "/api/agents/register", json={
        "name": "test-worker", "modules": modules, "capabilities": capabilities, "tags": []
    })
    assert response.status_code == 200
    return response.json()["worker_id"]


def lease(app, worker_id):
    return request(app, "POST", f"/api/agents/{worker_id}/lease")


def submit_result(app, leased, status, output_port=None):
    clip_id = leased.json()["clips"][0]["id"]
    result = {"clip_id": clip_id, "status": status, "message": "test result"}
    if output_port:
        result["output_port"] = output_port
    return request(app, "POST", f"/api/tasks/{leased.json()['task_id']}/result",
                   json={"results": [result]})


def test_manual_source_wait_copy_flow_and_event_log_timeline(setup_server):
    app, _ = setup_server
    workflow_id = make_workflow(app)
    created = request(app, "POST", f"/api/workflows/{workflow_id}/clips", json={
        "name": "clip-1", "job": "job-1", "metadata": {"camera": "A"}, "files": []
    })
    clip_id = created.json()["id"]
    worker_id = register(app, ["wait", "copy_file"], ["cpu", "io"])

    first = lease(app, worker_id)
    assert first.status_code == 200
    assert first.json()["module"] == "wait"
    assert first.json()["attempt"] == 1
    assert submit_result(app, first, "success").status_code == 200

    second = lease(app, worker_id)
    assert second.status_code == 200
    assert second.json()["module"] == "copy_file"
    assert submit_result(app, second, "success").status_code == 200

    detail = request(app, "GET", f"/api/clips/{clip_id}").json()
    assert detail["clip"]["status"] == "finished"
    assert [event["type"] for event in detail["events"]] == [
        "clip.created", "clip.entered_node", "clip.entered_node", "task.queued",
        "task.leased", "task.succeeded", "clip.entered_node", "task.queued",
        "task.leased", "task.succeeded", "clip.finished",
    ]
    assert request(app, "GET", "/api/health").json()["version"] == "0.1.0"


def test_error_retries_then_routes_error_port(setup_server):
    app, _ = setup_server
    workflow_id = make_workflow(app, max_attempts=2, edges=[
        {"id": "e1", "source": "source", "source_port": "success", "target": "wait-node"},
        {"id": "e2", "source": "wait-node", "source_port": "error", "target": "copy-node"},
    ])
    clip_id = request(app, "POST", f"/api/workflows/{workflow_id}/clips", json={"name": "bad", "job": "job"}).json()["id"]
    worker_id = register(app, ["wait", "copy_file"], ["cpu", "io"])

    first = lease(app, worker_id)
    assert submit_result(app, first, "error").status_code == 200
    retry = lease(app, worker_id)
    assert retry.json()["attempt"] == 2
    assert submit_result(app, retry, "error").status_code == 200
    routed = lease(app, worker_id)
    assert routed.json()["module"] == "copy_file"
    assert submit_result(app, routed, "success").status_code == 200

    detail = request(app, "GET", f"/api/clips/{clip_id}").json()
    assert detail["clip"]["status"] == "finished"
    assert any(event["type"] == "task.requeued" for event in detail["events"])
    assert any(event["type"] == "clip.entered_node" and event["node_id"] == "copy-node"
               for event in detail["events"])


def test_terminal_error_and_manual_retry(setup_server):
    app, _ = setup_server
    workflow_id = make_workflow(app, max_attempts=1, edges=[
        {"id": "e1", "source": "source", "source_port": "success", "target": "wait-node"},
    ])
    clip_id = request(app, "POST", f"/api/workflows/{workflow_id}/clips", json={"name": "bad", "job": "job"}).json()["id"]
    worker_id = register(app, ["wait"], ["cpu"])
    task = lease(app, worker_id)
    task_id = task.json()["task_id"]
    submit_result(app, task, "error")
    assert request(app, "GET", f"/api/clips/{clip_id}").json()["clip"]["status"] == "failed"

    retried = request(app, "POST", f"/api/tasks/{task_id}/retry")
    assert retried.json() == {"task_id": task_id, "state": "queued", "attempt": 2}
    reissued = lease(app, worker_id)
    assert reissued.json()["attempt"] == 2
    submit_result(app, reissued, "success")
    assert request(app, "GET", f"/api/clips/{clip_id}").json()["clip"]["status"] == "finished"


def test_expired_lease_requeues_with_incremented_attempt(setup_server):
    app, clock_value = setup_server
    app.state.lease_seconds = 1
    workflow_id = make_workflow(app, max_attempts=3, edges=[
        {"id": "e1", "source": "source", "source_port": "success", "target": "wait-node"},
    ])
    clip_id = request(app, "POST", f"/api/workflows/{workflow_id}/clips", json={"name": "lease", "job": "job"}).json()["id"]
    worker_id = register(app, ["wait"], ["cpu"])
    first = lease(app, worker_id)
    assert first.json()["attempt"] == 1

    clock_value[0] += timedelta(seconds=2)
    renewed_task = lease(app, worker_id)
    assert renewed_task.json()["attempt"] == 2
    detail = request(app, "GET", f"/api/clips/{clip_id}").json()
    assert any(event["type"] == "task.requeued" and event["data"]["message"] == "lease expired"
               for event in detail["events"])
