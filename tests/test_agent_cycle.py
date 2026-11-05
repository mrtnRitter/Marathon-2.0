import json
from pathlib import Path

from m2.agent import Agent


class FakeApi:
    def __init__(self, task):
        self.task = task
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/agents/register":
            return 200, {"worker_id": "worker-1"}
        if path == "/agents/worker-1/heartbeat":
            return 200, {"ok": True}
        if path == "/agents/worker-1/lease":
            task, self.task = self.task, None
            return (200, task) if task else (204, None)
        if path == "/tasks/task-1/heartbeat":
            return 200, {"ok": True}
        if path == "/tasks/task-1/result":
            return 200, {"ok": True}
        raise AssertionError(f"Unexpected request: {method} {path}")


def make_task(module="wait", params=None):
    return {
        "task_id": "task-1",
        "module": module,
        "module_version": "1.0.0",
        "params": params or {},
        "clips": [{"id": "clip-1", "name": "Clip", "job": "job-1", "metadata": {}, "files": []}],
    }


def test_lease_run_result_cycle(tmp_path):
    modules_dir = Path(__file__).parents[1] / "m2" / "modules"
    api = FakeApi(make_task(params={"seconds": 0, "fail_rate": 0}))
    agent = Agent("http://unused", "demo", [], {"storages": {"media": str(tmp_path)}}, client=api, modules_dir=modules_dir)

    assert agent.run_once() is True
    assert api.calls[0][2]["modules"] == ["cleanup", "copy_file", "wait"]
    assert [path for _, path, _ in api.calls] == [
        "/agents/register",
        "/agents/worker-1/heartbeat",
        "/agents/worker-1/lease",
        "/tasks/task-1/heartbeat",
        "/tasks/task-1/result",
    ]
    result = api.calls[-1][2]["results"][0]
    assert result["clip_id"] == "clip-1"
    assert result["status"] == "success"
    assert result["output_port"] == "success"
    heartbeat = api.calls[-2][2]
    assert heartbeat["progress"] == 100
    assert heartbeat["log"] == ["Waiting for 0 seconds"]


def test_module_exception_reports_error_result(tmp_path):
    modules_dir = tmp_path / "modules"
    folder = modules_dir / "explode"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"name": "explode", "version": "1.0.0", "kind": "process"}))
    (folder / "module.py").write_text("def run(ctx, clips, params):\n    raise RuntimeError('broken')\n")
    api = FakeApi(make_task("explode"))
    agent = Agent("http://unused", "demo", [], {"storages": {}}, client=api, modules_dir=modules_dir)

    assert agent.run_once() is True
    result = api.calls[-1][2]["results"][0]
    assert result == {
        "clip_id": "clip-1",
        "status": "error",
        "output_port": "error",
        "message": "RuntimeError: broken",
        "metadata": {},
        "files_add": [],
    }
