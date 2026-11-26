from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from marathon.modules._job import JobStopped, ModuleJob, ModuleResult, SpawnedAsset, ToolMissing
from marathon.worker.__main__ import main
from marathon.worker import runtime as worker_runtime
from marathon.worker.runtime import WorkerRuntime


class WorkerTestServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.routes: dict[str, Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]] = {}
        self.empty_poll_delay = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                request = {
                    "path": self.path,
                    "payload": payload,
                    "headers": dict(self.headers.items()),
                }
                with owner.lock:
                    owner.requests.append(request)
                route = owner.route_name(self.path)
                handler = owner.routes.get(route)
                if handler is None:
                    if route == "register":
                        status, body = 200, {"worker_id": "w", "status": "ready", "approved": True, "poll_timeout_s": 1}
                    elif route == "poll":
                        owner.empty_poll_delay.wait(0.01)
                        status, body = 200, {"jobs": [], "cancel": [], "status": "ready", "approved": True}
                    elif route == "start":
                        status, body = 200, {"ok": True}
                    else:
                        status, body = 200, {"accepted": True}
                else:
                    status, body = handler(payload)
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    @staticmethod
    def route_name(path: str) -> str:
        if path.startswith("/api/v1/"):
            path = path[len("/api/v1"):]
        if path == "/worker/register":
            return "register"
        if path == "/worker/poll":
            return "poll"
        if path.endswith("/start"):
            return "start"
        if path.endswith("/result"):
            return "result"
        return "other"

    def matching(self, suffix: str) -> list[dict[str, Any]]:
        with self.lock:
            return [request for request in self.requests if request["path"].endswith(suffix)]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)


@pytest.fixture
def server() -> Any:
    instance = WorkerTestServer()
    try:
        yield instance
    finally:
        instance.close()


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.01)
    return predicate()


def start_runtime(
    server: WorkerTestServer,
    run_module: Callable[..., Any] | None = None,
    *,
    resolve_module: Callable[[str], Any | None] | None = None,
    **runtime_options: Any,
) -> tuple[threading.Event, threading.Thread, list[int]]:
    if resolve_module is None:
        resolve_module = lambda type_id: SimpleNamespace(run=run_module) if run_module else None
    runtime = WorkerRuntime(
        server.url,
        "token",
        "w",
        "W",
        (),
        resolve_module=resolve_module,
        request_timeout_s=0.2,
        retry_delays_s=(0.05,),
        result_retry_s=0.05,
        result_deadline_s=0.6,
        shutdown_timeout_s=0.3,
        **runtime_options,
    )
    stop = threading.Event()
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(runtime.run(stop)), daemon=True)
    thread.start()
    return stop, thread, result


def stop_runtime(stop: threading.Event, thread: threading.Thread, timeout: float = 2.0) -> None:
    stop.set()
    thread.join(timeout)
    assert not thread.is_alive(), "WorkerRuntime.run() blieb nach Stopp hängen"


def job(job_id: str = "j1", type_id: str = "x", path: str = "/x") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "type_id": type_id,
        "settings": {},
        "assets": [{"id": "a1", "name": "asset", "path": path}],
    }


def test_requests_use_api_prefix(server: WorkerTestServer) -> None:
    stop, thread, _ = start_runtime(server)
    try:
        assert wait_until(lambda: bool(server.matching("/register")))
        assert server.requests[0]["path"] == "/api/v1/worker/register"
    finally:
        stop_runtime(stop, thread)


def test_worker_capabilities_filters_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        worker_runtime,
        "load_catalog",
        lambda: [{"id": "local", "execution": "local"}, {"id": "worker", "execution": "worker"}],
    )
    assert worker_runtime.worker_capabilities() == ["worker"]


def test_register_retries_until_server_answers(server: WorkerTestServer) -> None:
    attempts = 0

    def register(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal attempts
        attempts += 1
        return (503, {}) if attempts <= 2 else (200, {"poll_timeout_s": 1})

    server.routes["register"] = register
    stop, thread, _ = start_runtime(server)
    try:
        assert wait_until(lambda: bool(server.matching("/poll")))
        assert len(server.matching("/register")) == 3
    finally:
        stop_runtime(stop, thread)


def test_runs_job_and_reports_result(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})
    stop, thread, _ = start_runtime(server, lambda _: ModuleResult("success", ("ok",), "/renamed"))
    try:
        assert wait_until(lambda: bool(server.matching("/result")))
        requests = server.requests
        start_index = next(i for i, item in enumerate(requests) if item["path"].endswith("/start"))
        result_index = next(i for i, item in enumerate(requests) if item["path"].endswith("/result"))
        assert start_index < result_index
        result = requests[result_index]
        assert result["payload"] == {
            "worker_id": "w", "outcome": "result", "output": "success", "log": ["ok"],
            "asset_path": "/renamed", "spawned": [],
        }
        assert result["headers"].get("Authorization") == "Bearer token"
    finally:
        stop_runtime(stop, thread)


def test_unchanged_path_sends_none(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})
    stop, thread, _ = start_runtime(server, lambda _: ModuleResult("success"))
    try:
        assert wait_until(lambda: bool(server.matching("/result")))
        assert server.matching("/result")[0]["payload"]["asset_path"] is None
    finally:
        stop_runtime(stop, thread)


def test_spawned_assets_are_reported(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})
    stop, thread, _ = start_runtime(server, lambda _: ModuleResult("success", (), "/x", (SpawnedAsset("/k", "success", ("Kopie",)),)))
    try:
        assert wait_until(lambda: bool(server.matching("/result")))
        assert server.matching("/result")[0]["payload"]["spawned"] == [
            {"path": "/k", "output": "success", "log": ["Kopie"]}
        ]
    finally:
        stop_runtime(stop, thread)


def test_start_conflict_skips_job(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []})
    server.routes["start"] = lambda _: (409, {})
    ran = threading.Event()
    stop, thread, _ = start_runtime(server, lambda _: ran.set())
    try:
        assert wait_until(lambda: len(server.matching("/start")) >= 1)
        threading.Event().wait(0.1)
        assert not ran.is_set()
        assert not server.matching("/result")
    finally:
        stop_runtime(stop, thread)


def test_cancel_sets_stop_event(server: WorkerTestServer) -> None:
    module_started = threading.Event()
    polls = 0

    def poll(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal polls
        polls += 1
        if polls == 1:
            return 200, {"jobs": [job()], "cancel": []}
        if module_started.is_set():
            return 200, {"jobs": [], "cancel": ["j1"]}
        return 200, {"jobs": [], "cancel": []}

    def module(module_job: Any) -> None:
        module_started.set()
        module_job.stop.wait(1)
        raise JobStopped

    server.routes["poll"] = poll
    stop, thread, _ = start_runtime(server, module)
    try:
        assert wait_until(lambda: bool(server.matching("/result")), 1.8)
        payload = server.matching("/result")[0]["payload"]
        assert payload["outcome"] == "cancelled"
        assert "asset_path" not in payload
    finally:
        stop_runtime(stop, thread)


def test_repeated_cancel_does_not_hot_loop(server: WorkerTestServer) -> None:
    module_started = threading.Event()
    polls_at_start = 0

    def poll(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        polls = len(server.matching("/poll"))
        if polls == 1:
            return 200, {"jobs": [job()], "cancel": ["j1"]}
        return 200, {"jobs": [], "cancel": ["j1"]}

    def module(_: Any) -> ModuleResult:
        module_started.set()
        threading.Event().wait(1.5)
        return ModuleResult("success")

    server.routes["poll"] = poll
    stop, thread, _ = start_runtime(server, module)
    try:
        assert module_started.wait(1)
        polls_at_start = len(server.matching("/poll"))
        threading.Event().wait(1.5)
        polls = len(server.matching("/poll"))
        assert polls - polls_at_start <= 5
    finally:
        stop_runtime(stop, thread)


def test_module_exception_reports_error(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})

    def module(_: Any) -> None:
        raise ValueError("kaputt")

    stop, thread, _ = start_runtime(server, module)
    try:
        assert wait_until(lambda: bool(server.matching("/result")))
        payload = server.matching("/result")[0]["payload"]
        assert payload["outcome"] == "error"
        assert payload["error"] == "kaputt"
        assert "asset_path" not in payload
    finally:
        stop_runtime(stop, thread)


def test_unknown_type_reports_error(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [job(type_id="x")], "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})
    stop, thread, _ = start_runtime(server, resolve_module=lambda _: None)
    try:
        assert wait_until(lambda: bool(server.matching("/result")))
        payload = server.matching("/result")[0]["payload"]
        assert payload["outcome"] == "error"
        assert payload["error"] == "Modultyp unbekannt: x"
        assert "asset_path" not in payload
    finally:
        stop_runtime(stop, thread)


def test_only_one_job_runs_at_a_time(server: WorkerTestServer) -> None:
    released = threading.Event()
    module_started = threading.Event()
    served = False

    def poll(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal served
        if not served:
            served = True
            return 200, {"jobs": [job("j1"), job("j2", path="/y")], "cancel": []}
        return 200, {"jobs": [], "cancel": []}

    def module(_: Any) -> ModuleResult:
        module_started.set()
        released.wait(1)
        return ModuleResult("success")

    server.routes["poll"] = poll
    stop, thread, _ = start_runtime(server, module)
    try:
        assert module_started.wait(1)
        threading.Event().wait(0.1)
        assert len(server.matching("/start")) == 1
        released.set()
        assert wait_until(lambda: bool(server.matching("/result")))
        assert [item["path"].split("/")[-2] for item in server.matching("/start")] == ["j1"]
    finally:
        released.set()
        stop_runtime(stop, thread)


def test_poll_never_answers_stop_is_prompt(server: WorkerTestServer) -> None:
    entered = threading.Event()

    def poll(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        entered.set()
        threading.Event().wait(30)
        return 200, {"jobs": [], "cancel": []}

    server.routes["poll"] = poll
    stop, thread, result = start_runtime(server)
    assert entered.wait(1)
    started = time.monotonic()
    stop.set()
    thread.join(1.5)
    assert not thread.is_alive()
    assert time.monotonic() - started < 2
    assert result == [0]


def test_stop_during_registration_is_prompt(server: WorkerTestServer) -> None:
    entered = threading.Event()
    release = threading.Event()

    def register(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        entered.set()
        release.wait(5)
        return 200, {"poll_timeout_s": 1}

    server.routes["register"] = register
    stop, thread, result = start_runtime(server)
    try:
        assert entered.wait(1)
        stop.set()
        thread.join(1.5)
        assert not thread.is_alive()
        assert result == [0]
    finally:
        release.set()
        stop_runtime(stop, thread)

def test_poll_server_error_retries_without_register(server: WorkerTestServer) -> None:
    polls = 0

    def poll(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal polls
        polls += 1
        return (500, {}) if polls <= 2 else (200, {"jobs": [], "cancel": []})

    server.routes["poll"] = poll
    stop, thread, _ = start_runtime(server)
    try:
        assert wait_until(lambda: len(server.matching("/poll")) >= 3)
        assert len(server.matching("/register")) == 1
    finally:
        stop_runtime(stop, thread)


def test_poll_404_reregisters(server: WorkerTestServer) -> None:
    polls = 0

    def poll(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal polls
        polls += 1
        return (404, {"detail": "Worker unbekannt"}) if polls == 1 else (200, {"jobs": [], "cancel": []})

    server.routes["poll"] = poll
    stop, thread, _ = start_runtime(server)
    try:
        assert wait_until(lambda: len(server.matching("/register")) >= 2)
        assert len(server.matching("/register")) == 2
    finally:
        stop_runtime(stop, thread)


def test_unauthorized_returns_3(server: WorkerTestServer) -> None:
    server.routes["register"] = lambda _: (401, {"detail": "Worker-Token ungültig"})
    runtime = WorkerRuntime(server.url, "token", "w", "W", (), request_timeout_s=0.2)
    assert runtime.run(threading.Event()) == 3


def test_poll_unauthorized_returns_3(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (401, {"detail": "Worker-Token ungültig"})
    stop, thread, result = start_runtime(server)
    thread.join(1)
    assert not thread.is_alive()
    assert result == [3]
    assert len(server.matching("/register")) == 1


def test_stop_during_result_retry_is_prompt(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})
    first_result = threading.Event()
    def reject_result(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        first_result.set()
        return 500, {}

    server.routes["result"] = reject_result
    stop, thread, result = start_runtime(server, lambda _: ModuleResult("success"))
    try:
        assert first_result.wait(1)
        stop.set()
        thread.join(1.5)
        assert not thread.is_alive()
        assert result == [0]
        assert len(server.matching("/result")) <= 2
    finally:
        stop_runtime(stop, thread)

def test_result_retried_until_accepted(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})
    results = 0

    def result(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal results
        results += 1
        return (500, {}) if results <= 2 else (200, {"accepted": True})

    server.routes["result"] = result
    stop, thread, _ = start_runtime(server, lambda _: ModuleResult("success"))
    try:
        assert wait_until(lambda: len(server.matching("/result")) == 3)
        assert len(server.matching("/result")) == 3
    finally:
        stop_runtime(stop, thread)


def test_shutdown_reports_cancel_and_returns(server: WorkerTestServer) -> None:
    module_started = threading.Event()

    def module(module_job: Any) -> None:
        module_started.set()
        module_job.stop.wait(2)
        raise JobStopped

    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})
    stop, thread, result = start_runtime(server, module)
    assert module_started.wait(1)
    stop.set()
    thread.join(1.5)
    assert not thread.is_alive()
    assert result == [0]
    assert wait_until(lambda: bool(server.matching("/result")), 0.5)
    assert server.matching("/result")[0]["payload"]["outcome"] == "cancelled"
    assert len(server.matching("/result")) == 1


def test_main_requires_token(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv("MARATHON_WORKER_TOKEN", raising=False)
    assert main(["--server", "https://127.0.0.1:9", "--worker-id", "w", "--name", "W"]) == 2
    assert "MARATHON_WORKER_TOKEN fehlt" in caplog.text


def test_cli_exits_on_stdin_eof() -> None:
    environment = os.environ.copy()
    environment["MARATHON_WORKER_TOKEN"] = "token"
    process = subprocess.Popen(
        [
            sys.executable, "-m", "marathon.worker", "--server", "https://127.0.0.1:9",
            "--worker-id", "w", "--name", "W", "--exit-on-stdin-eof",
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    process.stdin.close()
    try:
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


# Protocol 3 regression coverage
def protocol3_job(job_id: str, assets: list[dict[str, str]]) -> dict[str, Any]:
    return {"job_id": job_id, "type_id": "x", "settings": {}, "assets": assets}


def asset_result_requests(server: WorkerTestServer) -> list[dict[str, Any]]:
    return [request for request in server.requests if "/assets/" in request["path"] and request["path"].endswith("/result")]


def offer_jobs(server: WorkerTestServer, jobs: list[dict[str, Any]]) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": jobs, "cancel": []}) if not server.matching("/start") else (200, {"jobs": [], "cancel": []})


def test_register_uses_protocol_3_without_max_parallel(server: WorkerTestServer) -> None:
    stop, thread, _ = start_runtime(server)
    try:
        assert wait_until(lambda: bool(server.matching("/register")))
        payload = server.matching("/register")[0]["payload"]
        assert payload["protocol"] == 3
        assert "max_parallel" not in payload
    finally:
        stop_runtime(stop, thread)


def test_job_runs_assets_in_parallel_and_reports_each_result(server: WorkerTestServer) -> None:
    assets = [{"id": "a1", "name": "one", "path": "/one"}, {"id": "a2", "name": "two", "path": "/two"}]
    offer_jobs(server, [protocol3_job("j1", assets)])
    both_running = threading.Barrier(2)
    paths: list[str] = []
    paths_lock = threading.Lock()

    def module(module_job: Any) -> ModuleResult:
        with paths_lock:
            paths.append(module_job.asset_path)
        both_running.wait(timeout=2)
        return ModuleResult(module_job.asset_path)

    stop, thread, _ = start_runtime(server, module)
    try:
        assert wait_until(lambda: len(asset_result_requests(server)) == 2)
        assert sorted(paths) == ["/one", "/two"]
        assert {request["path"] for request in asset_result_requests(server)} == {
            "/api/v1/worker/jobs/j1/assets/a1/result", "/api/v1/worker/jobs/j1/assets/a2/result",
        }
    finally:
        stop_runtime(stop, thread)


def test_second_job_offer_is_ignored_while_job_runs(server: WorkerTestServer) -> None:
    started = threading.Event()
    release = threading.Event()
    server.routes["poll"] = lambda _: (200, {
        "jobs": [
            protocol3_job("j1", [{"id": "a1", "name": "one", "path": "/one"}]),
            protocol3_job("j2", [{"id": "a2", "name": "two", "path": "/two"}]),
        ], "cancel": [],
    })

    def module(_: Any) -> ModuleResult:
        started.set()
        release.wait(2)
        return ModuleResult("done")

    stop, thread, _ = start_runtime(server, module)
    try:
        assert started.wait(1)
        assert wait_until(lambda: len(server.matching("/start")) == 1)
        assert [request["path"] for request in server.matching("/start")] == ["/api/v1/worker/jobs/j1/start"]
    finally:
        release.set()
        stop_runtime(stop, thread)


def test_cancel_cancels_each_running_asset(server: WorkerTestServer) -> None:
    assets = [{"id": "a1", "name": "one", "path": "/one"}, {"id": "a2", "name": "two", "path": "/two"}]
    both_running = threading.Event()
    started_count = 0
    started_lock = threading.Lock()

    def poll(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not server.matching("/start"):
            return 200, {"jobs": [protocol3_job("j1", assets)], "cancel": []}
        return 200, {"jobs": [], "cancel": ["j1"] if both_running.is_set() else []}

    def module(module_job: Any) -> None:
        nonlocal started_count
        with started_lock:
            started_count += 1
            if started_count == 2:
                both_running.set()
        module_job.stop.wait(2)
        raise JobStopped

    server.routes["poll"] = poll
    stop, thread, _ = start_runtime(server, module)
    try:
        assert wait_until(lambda: len(asset_result_requests(server)) == 2)
        assert [request["payload"]["outcome"] for request in asset_result_requests(server)] == ["cancelled", "cancelled"]
    finally:
        stop_runtime(stop, thread)


def test_asset_exception_does_not_prevent_other_result(server: WorkerTestServer) -> None:
    assets = [{"id": "a1", "name": "one", "path": "/bad"}, {"id": "a2", "name": "two", "path": "/good"}]
    offer_jobs(server, [protocol3_job("j1", assets)])

    def module(module_job: Any) -> ModuleResult:
        if module_job.asset_path == "/bad":
            raise ValueError("kaputt")
        return ModuleResult("success")

    stop, thread, _ = start_runtime(server, module)
    try:
        assert wait_until(lambda: len(asset_result_requests(server)) == 2)
        results = {request["path"].split("/assets/")[1].split("/")[0]: request["payload"]["outcome"] for request in asset_result_requests(server)}
        assert results == {"a1": "error", "a2": "result"}
    finally:
        stop_runtime(stop, thread)


def test_failed_asset_result_send_does_not_block_other_asset(server: WorkerTestServer) -> None:
    assets = [{"id": "a1", "name": "one", "path": "/one"}, {"id": "a2", "name": "two", "path": "/two"}]
    send_one_started = threading.Event()
    release_send_one = threading.Event()
    send_two_accepted = threading.Event()
    server.routes["poll"] = lambda _: (200, {"jobs": [protocol3_job("j1", assets)] if not server.matching("/start") else [], "cancel": []})

    def result_route(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if payload.get("output") == "/one":
            send_one_started.set()
            release_send_one.wait(2)
            return 500, {}
        send_two_accepted.set()
        return 200, {}

    server.routes["result"] = result_route
    stop, thread, _ = start_runtime(server, lambda module_job: ModuleResult(module_job.asset_path))
    try:
        assert send_one_started.wait(1)
        assert send_two_accepted.wait(1)
        assert wait_until(lambda: any(request["payload"].get("running") == ["j1"] for request in server.matching("/poll")))
        assert any(request["payload"].get("output") == "/one" for request in asset_result_requests(server))
        assert any(request["payload"].get("output") == "/two" for request in asset_result_requests(server))
    finally:
        release_send_one.set()
        stop_runtime(stop, thread)


def test_job_without_assets_is_not_started(server: WorkerTestServer) -> None:
    offer_jobs(server, [protocol3_job("j1", [])])
    stop, thread, _ = start_runtime(server)
    try:
        assert wait_until(lambda: len(server.matching("/poll")) >= 1)
        assert not server.matching("/start")
    finally:
        stop_runtime(stop, thread)


def test_shutdown_does_not_wait_for_unresponsive_asset(server: WorkerTestServer) -> None:
    started = threading.Event()
    offer_jobs(server, [protocol3_job("j1", [{"id": "a1", "name": "one", "path": "/hang"}])])

    def module(_: Any) -> None:
        started.set()
        threading.Event().wait()

    stop, thread, _ = start_runtime(server, module)
    assert started.wait(1)
    began = time.monotonic()
    stop_runtime(stop, thread, timeout=0.8)
    assert time.monotonic() - began < 0.8



def test_register_and_poll_report_availability_and_revision(server: WorkerTestServer) -> None:
    configs = []
    server.routes["register"] = lambda _: (200, {
        "status": "ready", "approved": True,
        "availability": {"config": {"blocked_processes": ["bad.exe"]}, "revision": 4},
    })
    polls = 0

    def poll(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal polls
        polls += 1
        assert payload["availability_revision"] == (4 if polls == 1 else 5)
        if polls == 1:
            return 200, {
                "jobs": [], "cancel": [], "status": "ready", "approved": True,
                "availability": {"config": {"blocked_processes": ["worse.exe"]}, "revision": 5},
            }
        return 200, {"jobs": [], "cancel": [], "status": "ready", "approved": True}

    server.routes["poll"] = poll
    stop, thread, _ = start_runtime(server, on_availability_config=lambda config, revision: configs.append((config, revision)))
    try:
        assert wait_until(lambda: len(server.matching("/poll")) >= 2)
        registration_payload = server.matching("/register")[0]["payload"]
        assert registration_payload == {
            "worker_id": "w", "name": "W", "client_version": worker_runtime.app_version,
            "capabilities": [], "protocol": 3, "available": True, "availability_reason": "",
        }
        poll_payloads = [request["payload"] for request in server.matching("/poll")]
        assert poll_payloads[0] == {
            "worker_id": "w", "running": [], "available": True,
            "availability_reason": "", "availability_revision": 4,
        }
        assert poll_payloads[1]["availability_revision"] == 5
        assert configs == [
            ({"blocked_processes": ["bad.exe"]}, 4),
            ({"blocked_processes": ["worse.exe"]}, 5),
        ]
    finally:
        stop_runtime(stop, thread)


def test_poll_uses_minus_one_before_any_availability_revision(server: WorkerTestServer) -> None:
    seen = threading.Event()
    server.routes["poll"] = lambda payload: (seen.set() or 200, {"jobs": [], "cancel": []})
    stop, thread, _ = start_runtime(server)
    try:
        assert seen.wait(1)
        assert server.matching("/poll")[0]["payload"]["availability_revision"] == -1
    finally:
        stop_runtime(stop, thread)


def test_status_callbacks_cover_responses_and_server_failures(server: WorkerTestServer) -> None:
    statuses: list[dict[str, Any]] = []
    server.routes["register"] = lambda _: (200, {"status": "paused", "approved": False})
    polls = 0

    def poll(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        nonlocal polls
        polls += 1
        return (500, {}) if polls == 1 else (200, {"jobs": [], "cancel": [], "status": "ready", "approved": True})

    server.routes["poll"] = poll
    stop, thread, _ = start_runtime(server, on_status=statuses.append)
    try:
        assert wait_until(lambda: any(item["connected"] and item["status"] == "ready" for item in statuses))
        assert any(item["connected"] and item["status"] == "paused" and item["approved"] is False for item in statuses)
        assert any(item["connected"] is False for item in statuses)
        assert statuses[-1] == {
            "connected": True, "status": "ready", "approved": True,
            "available": True, "reason": "", "running": 0,
        }
    finally:
        stop_runtime(stop, thread)


def test_status_callback_reports_connection_failure(server: WorkerTestServer) -> None:
    statuses: list[dict[str, Any]] = []
    runtime = WorkerRuntime(
        "http://127.0.0.1:1", "token", "w", "W", (), request_timeout_s=0.1,
        retry_delays_s=(0.01,), on_status=statuses.append,
    )
    stop = threading.Event()
    thread = threading.Thread(target=lambda: runtime.run(stop), daemon=True)
    thread.start()
    try:
        assert wait_until(lambda: any(item["connected"] is False for item in statuses), 1)
        assert statuses[0] == {
            "connected": False, "status": None, "approved": None,
            "available": True, "reason": "", "running": 0,
        }
    finally:
        stop_runtime(stop, thread)


def test_unavailable_worker_does_not_start_offered_job(server: WorkerTestServer) -> None:
    calls = 0

    def availability() -> tuple[bool, str]:
        nonlocal calls
        calls += 1
        return (True, "") if calls == 1 else (False, "Wartung")

    server.routes["poll"] = lambda _: (200, {"jobs": [job()], "cancel": []})
    stop, thread, _ = start_runtime(server, availability=availability)
    try:
        assert wait_until(lambda: len(server.matching("/poll")) >= 1)
        assert not server.matching("/start")
        assert server.matching("/register")[0]["payload"]["available"] is True
        assert server.matching("/poll")[0]["payload"]["available"] is False
        assert server.matching("/poll")[0]["payload"]["availability_reason"] == "Wartung"
    finally:
        stop_runtime(stop, thread)


def test_availability_exception_marks_worker_unavailable(server: WorkerTestServer, caplog: pytest.LogCaptureFixture) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [], "cancel": []})

    def fail() -> tuple[bool, str]:
        raise RuntimeError("unavailable")

    stop, thread, _ = start_runtime(server, availability=fail)
    try:
        assert wait_until(lambda: bool(server.matching("/poll")))
        assert server.matching("/register")[0]["payload"]["available"] is False
        assert server.matching("/register")[0]["payload"]["availability_reason"] == "Verfügbarkeit nicht prüfbar"
        assert server.matching("/poll")[0]["payload"]["availability_reason"] == "Verfügbarkeit nicht prüfbar"
        assert "Verfügbarkeit" in caplog.text
    finally:
        stop_runtime(stop, thread)


def test_callback_exception_does_not_stop_runtime(server: WorkerTestServer) -> None:
    server.routes["poll"] = lambda _: (200, {"jobs": [], "cancel": []})

    def fail(_: dict[str, Any]) -> None:
        raise RuntimeError("callback failed")

    stop, thread, result = start_runtime(server, on_status=fail)
    try:
        assert wait_until(lambda: len(server.matching("/poll")) >= 1)
        assert thread.is_alive()
    finally:
        stop_runtime(stop, thread)
    assert result == [0]


def test_client_update_offer_is_forwarded(server: WorkerTestServer) -> None:
    offered: list[dict[str, Any]] = []
    server.routes["poll"] = lambda _: (200, {
        "jobs": [], "cancel": [], "client_update": {"version": "2.0", "url": "https://example.invalid/update"},
    })
    stop, thread, _ = start_runtime(server, on_update_offer=offered.append)
    try:
        assert wait_until(lambda: bool(offered))
        assert offered == [{"version": "2.0", "url": "https://example.invalid/update"}]
    finally:
        stop_runtime(stop, thread)


def test_module_job_tool_resolver_and_missing() -> None:
    tool = Path("/tools/ffmpeg")
    module_job = ModuleJob("/asset", {}, threading.Event(), lambda name: tool if name == "ffmpeg" else Path(name))
    assert module_job.tool("ffmpeg") == tool
    with pytest.raises(ToolMissing, match="Zusatzprogramm fehlt: ffmpeg"):
        ModuleJob("/asset", {}, threading.Event()).tool("ffmpeg")


def test_main_tools_dir_configures_tool_resolver(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import marathon.worker.__main__ as worker_main

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    captured: dict[str, Any] = {}

    class RuntimeStub:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        def run(self, stop: threading.Event) -> int:
            return 0

    monkeypatch.setenv("MARATHON_WORKER_TOKEN", "token")
    monkeypatch.setattr(worker_main, "WorkerRuntime", RuntimeStub)
    assert main([
        "--server", "https://localhost", "--worker-id", "w", "--name", "W", "--tools-dir", str(tools_dir),
    ]) == 0
    assert captured["tool_path"]("ffmpeg") == tools_dir / "ffmpeg"


def test_main_tools_dir_missing_raises_tool_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import marathon.worker.__main__ as worker_main

    captured: dict[str, Any] = {}

    class RuntimeStub:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        def run(self, stop: threading.Event) -> int:
            return 0

    monkeypatch.setenv("MARATHON_WORKER_TOKEN", "token")
    monkeypatch.setattr(worker_main, "WorkerRuntime", RuntimeStub)
    assert main([
        "--server", "https://localhost", "--worker-id", "w", "--name", "W", "--tools-dir", str(tmp_path / "absent"),
    ]) == 0
    with pytest.raises(ToolMissing, match="Zusatzprogramm fehlt: ffmpeg"):
        captured["tool_path"]("ffmpeg")


def test_runtime_passes_tool_resolver_to_module_job() -> None:
    tool = Path("/tools/ffmpeg")
    observed: list[Path] = []

    def run(module_job: ModuleJob) -> ModuleResult:
        observed.append(module_job.tool("ffmpeg"))
        return ModuleResult("done")

    runtime = WorkerRuntime(
        "https://localhost", "token", "w", "W", (),
        resolve_module=lambda _: SimpleNamespace(run=run),
        tool_path=lambda name: tool if name == "ffmpeg" else Path(name),
    )
    runtime._job_result("x", "/asset", {}, threading.Event())
    assert observed == [tool]


def test_running_jobs_returns_current_job_count() -> None:
    runtime = WorkerRuntime("https://localhost", "token", "w", "W", ())
    assert runtime.running_jobs() == 0
    with runtime._jobs_lock:
        runtime._jobs["j1"] = worker_runtime._RunningJob("j1")
    assert runtime.running_jobs() == 1


@pytest.mark.parametrize("phase", ["register", "poll"])
def test_blocking_status_callback_does_not_block_stop(server: WorkerTestServer, phase: str) -> None:
    callback_started = threading.Event()
    release_callback = threading.Event()
    callback_threads: list[str] = []
    calls = 0

    def on_status(_: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        should_block = calls == (1 if phase == "register" else 2)
        if should_block:
            callback_threads.append(threading.current_thread().name)
            callback_started.set()
            release_callback.wait()

    stop, thread, _ = start_runtime(server, on_status=on_status)
    try:
        assert callback_started.wait(1)
        stop.set()
        thread.join(1.8)
        stopped_promptly = not thread.is_alive()
    finally:
        release_callback.set()
        stop_runtime(stop, thread)
    assert stopped_promptly
    assert callback_threads == ["marathon-worker-callbacks"]


def test_availability_config_callbacks_keep_response_order(server: WorkerTestServer) -> None:
    callback_started = threading.Event()
    release_callback = threading.Event()
    revisions: list[int] = []

    def on_config(_: dict[str, Any], revision: int) -> None:
        if revision == 1:
            callback_started.set()
            release_callback.wait()
        revisions.append(revision)

    server.routes["register"] = lambda _: (200, {
        "status": "ready", "approved": True,
        "availability": {"config": {"blocked_processes": ["one.exe"]}, "revision": 1},
    })
    server.routes["poll"] = lambda _: (200, {
        "jobs": [], "cancel": [], "status": "ready", "approved": True,
        "availability": {"config": {"blocked_processes": ["two.exe"]}, "revision": 2},
    })
    stop, thread, _ = start_runtime(server, on_availability_config=on_config)
    try:
        assert callback_started.wait(1)
        assert wait_until(lambda: bool(server.matching("/poll")))
    finally:
        release_callback.set()
        stop_runtime(stop, thread)
    assert revisions == [1, 2]
