from test_server_core import make_workflow, register, request


def test_workflow_summary_and_node_tasks(setup_server):
    app, _ = setup_server
    workflow_id = make_workflow(app)
    request(app, "POST", f"/api/workflows/{workflow_id}/clips", json={"name": "one", "job": "job"})
    summary = request(app, "GET", f"/api/workflows/{workflow_id}/summary").json()
    assert summary["nodes"]["wait-node"]["queued"] == 1
    assert summary["clips"] == {"active": 1, "finished": 0, "failed": 0}
    tasks = request(app, "GET", f"/api/workflows/{workflow_id}/nodes/wait-node/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["state"] == "queued"


def test_worker_capabilities_and_module_matching(setup_server):
    app, _ = setup_server
    workflow_id = make_workflow(app)
    request(app, "POST", f"/api/workflows/{workflow_id}/clips", json={"name": "one", "job": "job"})
    wrong = register(app, ["wait"], ["io"])
    lease_response = request(app, "POST", f"/api/agents/{wrong}/lease")
    assert lease_response.status_code == 204


def test_contract_routes_and_module_listing(setup_server):
    app, _ = setup_server
    routes = {(method, route.path) for route in app.routes for method in getattr(route, "methods", set())}
    required = {
        ("GET", "/api/health"), ("GET", "/api/modules"),
        ("GET", "/api/workflows"), ("POST", "/api/workflows"),
        ("GET", "/api/workflows/{workflow_id}"),
        ("POST", "/api/workflows/{workflow_id}/versions"),
        ("POST", "/api/workflows/{workflow_id}/clips"),
        ("GET", "/api/workflows/{workflow_id}/summary"),
        ("GET", "/api/workflows/{workflow_id}/nodes/{node_id}/tasks"),
        ("GET", "/api/clips"), ("GET", "/api/clips/{clip_id}"),
        ("GET", "/api/workers"), ("POST", "/api/tasks/{task_id}/retry"),
        ("POST", "/api/agents/register"),
        ("POST", "/api/agents/{worker_id}/heartbeat"),
        ("POST", "/api/agents/{worker_id}/lease"),
        ("POST", "/api/tasks/{task_id}/heartbeat"),
        ("POST", "/api/tasks/{task_id}/result"),
        ("GET", "/api/events/stream"),
    }
    assert required.issubset(routes)
    assert any(route.path in ("", "/") and route.name == "static" for route in app.routes)
    modules = request(app, "GET", "/api/modules").json()
    assert {module["name"] for module in modules} == {"manual_source", "wait", "copy_file"}
