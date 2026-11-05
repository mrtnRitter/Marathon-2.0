import hashlib
import http.server
import io
import json
import threading
import time
import zipfile
from pathlib import Path

from marathon import __version__

import pytest

from marathon.client import runtime, starter


class FakeServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, jobs):
        self.lock = threading.Lock()
        self.jobs = list(jobs)
        self.claimed = []
        self.job_workers = {}
        self.results = []
        self.logs = []
        self.registrations = []
        self.active = 0
        self.max_active = 0
        self.stop_events = []
        self.completion_event = threading.Event()
        super().__init__(("127.0.0.1", 0), self.handler())

    def handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, value, status=200):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/worker/register":
                    with server.lock:
                        server.registrations.append(payload)
                        worker_id = f"worker-{len(server.stop_events) + 1}"
                        server.stop_events.append(threading.Event())
                    return self.respond({"id": worker_id, "update": None}, 201)
                if self.path.endswith("/heartbeat"):
                    return self.respond({"update": None, "active_jobs": server.active})
                if self.path.endswith("/jobs/claim"):
                    with server.lock:
                        if not server.jobs:
                            return self.respond({"items": []})
                        job = server.jobs.pop(0)
                        server.claimed.append(job["id"])
                        server.job_workers[job["id"]] = self.path.split("/")[3]
                        server.active += 1
                        server.max_active = max(server.max_active, server.active)
                    return self.respond({"items": [job]})
                if "/jobs/" in self.path and self.path.endswith("/log"):
                    with server.lock:
                        server.logs.append(payload["message"])
                    return self.respond({"id": "log"}, 201)
                if "/jobs/" in self.path and self.path.endswith("/result"):
                    with server.lock:
                        server.results.append((self.path.split("/")[-2], payload))
                        server.active -= 1
                        if len(server.results) == len(server.claimed) and not server.jobs:
                            server.completion_event.set()
                    return self.respond({"ok": True}, 201)
                return self.respond({"error": "unknown"}, 404)

        return Handler

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server_port}"


def make_job(job_id, seconds=0.02):
    return {"id": job_id, "module_type": "wait", "settings": {"seconds": seconds}}


def run_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def stop_server(server, thread):
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


def test_job_runs_and_reports_result_and_log(tmp_path):
    server = FakeServer([make_job("one")])
    thread = run_server(server)
    state = tmp_path / "state.json"
    runtime_client = runtime.WorkerRuntime(
        {"server_url": server.base_url, "name": "one", "max_parallel": 1, "poll_interval": 0.01},
        state,
    )
    stop_event = server.completion_event
    client_thread = threading.Thread(target=runtime_client.run, args=(stop_event,), daemon=True)
    client_thread.start()
    try:
        deadline = time.monotonic() + 3
        while not server.results and time.monotonic() < deadline:
            time.sleep(0.01)
        stop_event.set()
        client_thread.join(timeout=2)
    finally:
        stop_server(server, thread)
    assert len(server.results) == 1
    assert server.results[0][0] == "one"
    assert server.results[0][1]["status"] == "success"
    assert "Started wait" in server.logs
    assert "Completed wait" in server.logs
    assert any(line.startswith("Progress:") for line in server.logs)
    assert "warten" in server.registrations[0]["capabilities"]
    assert state.read_text(encoding="utf-8")


def test_two_clients_share_jobs_with_bounded_parallelism(tmp_path):
    server = FakeServer([make_job(str(index), 0.08) for index in range(8)])
    thread = run_server(server)
    clients = []
    client_threads = []
    try:
        for index in range(2):
            client = runtime.WorkerRuntime(
                {"server_url": server.base_url, "name": f"worker-{index}", "max_parallel": 1, "poll_interval": 0.01},
                tmp_path / f"worker-{index}.json",
            )
            clients.append(client)
            client_threads.append(threading.Thread(target=client.run, args=(server.completion_event,), daemon=True))
        for client_thread in client_threads:
            client_thread.start()
        deadline = time.monotonic() + 5
        while len(server.results) < 8 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(server.results) == 8
        server.completion_event.set()
        for client_thread in client_threads:
            client_thread.join(timeout=2)
    finally:
        stop_server(server, thread)
    assert sorted(server.claimed) == [str(index) for index in range(8)]
    assert server.max_active <= 2
    assert len(set(server.job_workers.values())) == 2


def build_package(version, runtime_source="pass"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("manifest.json", json.dumps({"version": version}))
        package.writestr("marathon/client/runtime.py", runtime_source)
    return buffer.getvalue()


def test_update_installs_version_2_0_1_and_rejects_bad_package(tmp_path, monkeypatch):
    archive = build_package("2.0.1")
    checksum = hashlib.sha256(archive).hexdigest()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (http.server.BaseHTTPRequestHandler,), {
        "do_GET": lambda self: (self.send_response(200), self.end_headers(), self.wfile.write(archive)),
        "log_message": lambda self, *_: None,
    }))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        installed = starter._install_update(
            f"http://127.0.0.1:{server.server_port}",
            {"version": "2.0.1", "download_url": "/package.zip", "sha256": checksum},
            tmp_path / "versions",
        )
        assert json.loads((installed / "manifest.json").read_text()) == {"version": "2.0.1"}
        with pytest.raises(ValueError, match="version does not match"):
            starter._install_update(
                f"http://127.0.0.1:{server.server_port}",
                {"version": __version__, "download_url": "/package.zip", "sha256": checksum},
                tmp_path / "bad-versions",
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()



def test_starter_completes_update_to_2_0_1(tmp_path):
    (tmp_path / "client.toml").write_text('server_url = "http://localhost"')
    state_path = tmp_path / ".client-state.json"
    update_path = state_path.with_suffix(".update.json")
    starter._write_json(update_path, {"version": "2.0.1", "download_url": "/package.zip", "sha256": "0" * 64})
    updated_root = tmp_path / ".client" / "versions" / "2.0.1"
    updated_root.mkdir(parents=True)

    class Process:
        def __init__(self, code):
            self.code = code

        def poll(self):
            return None

        def wait(self):
            return self.code

    launches = iter([Process(runtime.UPDATE_EXIT_CODE), Process(0)])
    original_launch = starter._launch
    starter._launch = lambda *args, **kwargs: next(launches)
    try:
        result = starter.supervise(
            tmp_path,
            startup_timeout=0,
            popen=lambda *args, **kwargs: None,
            installer=lambda *_: updated_root,
        )
    finally:
        starter._launch = original_launch
    saved = json.loads(state_path.read_text())
    assert result == 0
    assert saved["active_version"] == ".client/versions/2.0.1"
    assert saved["previous_version"] == "."


def test_starter_falls_back_after_broken_updated_runtime(tmp_path, monkeypatch):
    old_root = tmp_path / "marathon"
    updated_root = tmp_path / ".client" / "versions" / "2.0.1"
    (old_root / "marathon" / "client").mkdir(parents=True)
    (updated_root / "marathon" / "client").mkdir(parents=True)
    (old_root / "marathon" / "client" / "runtime.py").write_text("pass")
    (updated_root / "marathon" / "client" / "runtime.py").write_text("pass")
    (tmp_path / "client.toml").write_text('server_url = "http://localhost"')
    state_path = tmp_path / ".client-state.json"
    starter._write_json(state_path, {"active_version": ".client/versions/2.0.1", "previous_version": "."})

    class Process:
        def __init__(self, code, stay_alive=False):
            self.code = code
            self.stay_alive = stay_alive

        def poll(self):
            return None if self.stay_alive else self.code

        def wait(self):
            return self.code

    launches = iter([Process(1), Process(0, stay_alive=True)])
    monkeypatch.setattr(starter, "_launch", lambda *args, **kwargs: next(launches))
    result = starter.supervise(tmp_path, startup_timeout=0, popen=lambda *args, **kwargs: None)
    saved = json.loads(state_path.read_text())
    assert result == 0
    assert saved["active_version"] == "."
    assert saved["previous_version"] is None
