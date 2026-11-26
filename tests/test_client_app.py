import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from marathon.client import app as client_app
from marathon.client.api import ApiError
from marathon.client.availability import Sample
from marathon.client.local_state import WorkerCredentials, load_credentials, save_credentials


class FakeTray:
    def __init__(self, on_open_settings, on_quit):
        self.on_open_settings = on_open_settings
        self.on_quit = on_quit
        self.states = []
        self.started = threading.Event()
        self.stopped = threading.Event()

    def set_status(self, state):
        self.states.append(dict(state))

    def run(self):
        self.started.set()
        self.stopped.wait(4)

    def stop(self):
        self.stopped.set()


class FakeApi:
    def __init__(self, token=None):
        self.token = token


class FakeRuntime:
    def __init__(self, *, behavior=None, **callbacks):
        self.callbacks = callbacks
        self.behavior = behavior
        self.started = threading.Event()
        self.stop_received = None

    def running_jobs(self):
        return 0

    def run(self, stop):
        self.stop_received = stop
        self.started.set()
        if self.behavior is not None:
            return self.behavior(self, stop)
        stop.wait(4)
        return 0


class FakeTools:
    def __init__(self, sync_started):
        self.sync_started = sync_started

    def sync(self, stop):
        self.sync_started.set()
        stop.wait(4)

    def tool_path(self, name):
        return Path("/fake") / name


class FakeUpdate:
    def __init__(self, is_blocking=False):
        self.is_blocking = is_blocking
        self.offers = []
        self.stopped = threading.Event()
        self.confirm_count = 0

    def blocks_jobs(self):
        return self.is_blocking

    def confirm_healthy(self):
        self.confirm_count += 1

    def offer(self, offer):
        self.offers.append(offer)

    def stop(self, timeout_s=5.0):
        self.stopped.set()


class Harness:
    def __init__(self, tmp_path, *, enroll_fn=None, runtime_behavior=None, sample=None, update_blocking=False):
        self.tmp_path = tmp_path
        self.enroll_calls = []
        self.delays = []
        self.sync_started = threading.Event()
        self.runtime_behavior = runtime_behavior
        self.sample_value = sample or Sample(datetime(2026, 11, 25, 12), None, 1, 1, None, ())
        self.tray = None
        self.runtime = None
        self.update = None
        self.api_tokens = []

        def api_factory(server_url, ssl_context, token=None):
            self.api_tokens.append(token)
            return FakeApi(token)

        def default_enroll(api, hostname, platform_name, client_version):
            self.enroll_calls.append((hostname, platform_name, client_version))
            return "worker_1", "token_1", True

        def runtime_factory(*args, **callbacks):
            self.runtime = FakeRuntime(behavior=self.runtime_behavior, **callbacks)
            return self.runtime

        def update_factory(base, version, download, is_idle, request_restart):
            self.update = FakeUpdate(update_blocking)
            self.request_restart = request_restart
            return self.update

        def tool_factory(base, fetch_manifest, download):
            return FakeTools(self.sync_started)

        def tray_factory(on_open_settings, on_quit):
            self.tray = FakeTray(on_open_settings, on_quit)
            return self.tray

        def wait(stop, seconds):
            self.delays.append(seconds)
            if seconds >= 600:
                return stop.wait(4)
            return stop.wait(0.005)

        self.deps = client_app.ClientDeps(
            api_factory=api_factory,
            enroll_fn=enroll_fn or default_enroll,
            ssl_context_factory=lambda pem: object(),
            runtime_factory=runtime_factory,
            tray_factory=tray_factory,
            sensor_factory=lambda: type("Sensors", (), {"sample": lambda _self, _now: self.sample_value})(),
            tool_store_factory=tool_factory,
            update_manager_factory=update_factory,
            control_link_fn=lambda api: "https://settings.example/one-time",
            open_browser=lambda url: self.browser_urls.append(url),
            hostname=lambda: "host",
            platform_name=lambda: "Windows-test",
            now=lambda: datetime(2026, 11, 25, 12),
            monotonic=time.monotonic,
            wait=wait,
        )
        self.browser_urls = []


    def stop(self, code=0):
        assert self.tray is not None
        self.tray.on_quit()
        self.thread.join(3)
        assert not self.thread.is_alive()
        assert self.result == code


def start_harness(harness):
    thread = threading.Thread(
        target=lambda: setattr(
            harness,
            "result",
            client_app.run_client(
                harness.tmp_path,
                client_app.Bootstrap("https://server.example", "ca", "1.2.3"),
                harness.deps,
            ),
        ),
        daemon=True,
    )
    harness.thread = thread
    thread.start()
    deadline = time.monotonic() + 1
    while harness.tray is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert harness.tray is not None
    assert harness.tray.started.wait(1)
    return thread



def wait_runtime(harness):
    deadline = time.monotonic() + 2
    while harness.runtime is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert harness.runtime is not None
    assert harness.runtime.started.wait(1)
    return harness.runtime


def quit_harness(harness, expected=0):
    harness.tray.on_quit()
    harness.thread.join(3)
    assert not harness.thread.is_alive()
    assert harness.result == expected


def test_registration_backoff_and_client_error_notice(tmp_path):
    outcomes = iter([RuntimeError("offline"), ApiError(403, "not approved"), ("worker_1", "token_1", True)])
    calls = []

    def enroll(*args):
        calls.append(args)
        result = next(outcomes)
        if isinstance(result, BaseException):
            raise result
        return result

    harness = Harness(tmp_path, enroll_fn=enroll)
    start_harness(harness)
    wait_runtime(harness)
    assert harness.delays[:2] == [1, 60]
    assert len(calls) == 3
    assert any(
        state.get("message") == "Anmeldung beim Server fehlgeschlagen: not approved"
        for state in harness.tray.states
    )
    assert load_credentials(tmp_path) == WorkerCredentials("https://server.example", "worker_1", "token_1")
    quit_harness(harness)


def test_registration_uses_full_backoff_schedule(tmp_path):
    calls = 0

    def enroll(*args):
        nonlocal calls
        calls += 1
        if calls <= 6:
            raise RuntimeError("temporary")
        return "worker_1", "token_1", True

    harness = Harness(tmp_path, enroll_fn=enroll)
    start_harness(harness)
    wait_runtime(harness)
    assert harness.delays[:6] == [1, 2, 5, 10, 30, 60]
    quit_harness(harness)


def test_saved_credentials_are_reused_and_server_mismatch_reenrolls(tmp_path):
    save_credentials(tmp_path, WorkerCredentials("https://server.example", "worker_1", "saved"))
    harness = Harness(tmp_path)
    start_harness(harness)
    wait_runtime(harness)
    assert harness.enroll_calls == []
    assert "saved" in harness.api_tokens
    quit_harness(harness)

    save_credentials(tmp_path, WorkerCredentials("https://other.example", "worker_old", "old"))
    second = Harness(tmp_path)
    start_harness(second)
    wait_runtime(second)
    assert len(second.enroll_calls) == 1
    assert load_credentials(tmp_path).server_url == "https://server.example"
    quit_harness(second)


def test_server_availability_rules_reach_evaluator_and_update_blocks_jobs(tmp_path):
    sample = Sample(datetime(2026, 11, 25, 12), None, 1, 1, None, ("editor.exe",))
    harness = Harness(tmp_path, sample=sample)
    start_harness(harness)
    wait_runtime(harness)
    runtime = harness.runtime
    deadline = time.monotonic() + 1
    while runtime.callbacks["availability"]() != (True, "") and time.monotonic() < deadline:
        time.sleep(0.005)
    assert runtime.callbacks["availability"]() == (True, "")
    runtime.callbacks["on_availability_config"]({"blocked_processes": ["editor.exe"]}, 1)
    deadline = time.monotonic() + 1
    while runtime.callbacks["availability"]()[0] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert runtime.callbacks["availability"]() == (False, "Programm editor.exe aktiv")
    harness.update.is_blocking = True
    assert runtime.callbacks["availability"]() == (False, client_app.block_reason)
    quit_harness(harness)


def test_first_connected_status_confirms_healthy_once(tmp_path):
    def behavior(runtime, stop):
        runtime.callbacks["on_status"]({"connected": True, "status": "ready"})
        runtime.callbacks["on_status"]({"connected": True, "status": "ready"})
        stop.wait(4)
        return 0

    harness = Harness(tmp_path, runtime_behavior=behavior)
    start_harness(harness)
    wait_runtime(harness)
    deadline = time.monotonic() + 1
    while harness.update.confirm_count == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert harness.update.confirm_count == 1
    quit_harness(harness)


def test_exit_three_deletes_credentials_and_keeps_permanent_notice(tmp_path):
    callback_box = {}

    def behavior(runtime, stop):
        callback_box.update(runtime.callbacks)
        return 3

    save_credentials(tmp_path, WorkerCredentials("https://server.example", "worker_1", "token"))
    harness = Harness(tmp_path, runtime_behavior=behavior)
    start_harness(harness)
    wait_runtime(harness)
    deadline = time.monotonic() + 1
    while load_credentials(tmp_path) is not None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert load_credentials(tmp_path) is None
    assert not harness.tray.stopped.is_set()
    assert any(state.get("message", "").startswith("Worker wurde entfernt.") for state in harness.tray.states)
    callback_box["on_status"]({"connected": True, "status": "ready"})
    assert harness.tray.states[-1]["message"].startswith("Worker wurde entfernt.")
    quit_harness(harness)


def test_settings_success_error_and_missing_credentials(tmp_path):
    harness = Harness(tmp_path)
    start_harness(harness)
    wait_runtime(harness)
    save_credentials(tmp_path, WorkerCredentials("https://server.example", "worker_1", "token"))
    harness.tray.on_open_settings()
    deadline = time.monotonic() + 1
    while not harness.browser_urls and time.monotonic() < deadline:
        time.sleep(0.005)
    assert harness.browser_urls == ["https://settings.example/one-time"]
    quit_harness(harness)

    error_harness = Harness(tmp_path)
    error_harness.deps.control_link_fn = lambda api: (_ for _ in ()).throw(ApiError(503, "unavailable"))
    start_harness(error_harness)
    wait_runtime(error_harness)
    error_harness.tray.on_open_settings()
    deadline = time.monotonic() + 1
    while not any("Worker-Einstellungen konnten" in state.get("message", "") for state in error_harness.tray.states) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert any("Worker-Einstellungen konnten nicht geöffnet werden: unavailable" == state.get("message") for state in error_harness.tray.states)
    quit_harness(error_harness)

    missing_harness = Harness(tmp_path, runtime_behavior=lambda runtime, stop: 3)
    start_harness(missing_harness)
    wait_runtime(missing_harness)
    deadline = time.monotonic() + 1
    while load_credentials(tmp_path) is not None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert load_credentials(tmp_path) is None
    missing_harness.tray.on_open_settings()
    deadline = time.monotonic() + 1
    while not any(state.get("message") == "Noch nicht beim Server angemeldet" for state in missing_harness.tray.states) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert any(state.get("message") == "Noch nicht beim Server angemeldet" for state in missing_harness.tray.states)
    quit_harness(missing_harness)


def test_quit_stops_runtime_and_worker_threads_within_three_seconds(tmp_path):
    harness = Harness(tmp_path)
    start_harness(harness)
    wait_runtime(harness)
    start = time.monotonic()
    quit_harness(harness)
    assert time.monotonic() - start <= 3
    assert harness.runtime.stop_received.is_set()
    assert harness.update.stopped.is_set()
    assert not any(thread.is_alive() for thread in threading.enumerate() if thread.name in {
        "marathon-client-main", "marathon-client-availability", "marathon-client-tools", "marathon-client-runtime"
    })


def test_update_restart_exits_with_restart_code(tmp_path):
    harness = Harness(tmp_path)
    start_harness(harness)
    wait_runtime(harness)
    harness.request_restart()
    harness.thread.join(3)
    assert not harness.thread.is_alive()
    assert harness.result == 10


def test_single_instance_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(client_app, "client_dir", lambda: tmp_path)
    first = client_app.SingleInstance(tmp_path)
    second = client_app.SingleInstance(tmp_path)
    assert first.acquire()
    assert client_app.is_client_running()
    assert not second.acquire()
    first.release()
    assert not client_app.is_client_running()
    assert second.acquire()
    second.release()



def test_temporary_notice_survives_status_expires_and_can_be_replaced(tmp_path, monkeypatch):
    clock = [100.0]
    timers = []

    class FakeTimer:
        def __init__(self, interval, function, args=()):
            self.interval = interval
            self.function = function
            self.args = args
            self.daemon = False
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def fire(self):
            self.function(*self.args)

    monkeypatch.setattr(client_app.threading, "Timer", FakeTimer)
    enroll_calls = 0

    def enroll(*args):
        nonlocal enroll_calls
        enroll_calls += 1
        if enroll_calls == 1:
            raise ApiError(403, "not approved")
        return "worker_1", "token_1", True

    harness = Harness(tmp_path, enroll_fn=enroll)
    harness.deps.monotonic = lambda: clock[0]
    start_harness(harness)
    runtime = wait_runtime(harness)
    expected = "Anmeldung beim Server fehlgeschlagen: not approved"
    runtime.callbacks["on_status"]({"connected": True, "status": "ready"})
    assert harness.tray.states[-1]["message"] == expected

    clock[0] = 131.0
    timers[-1].fire()
    assert "message" not in harness.tray.states[-1]

    harness.deps.control_link_fn = lambda api: (_ for _ in ()).throw(ApiError(503, "unavailable"))
    harness.tray.on_open_settings()
    replacement = "Worker-Einstellungen konnten nicht geöffnet werden: unavailable"
    assert harness.tray.states[-1]["message"] == replacement
    runtime.callbacks["on_status"]({"connected": True, "status": "ready"})
    assert harness.tray.states[-1]["message"] == replacement
    quit_harness(harness)


def test_shutdown_during_registration_wait_is_cancellable(tmp_path):
    def enroll(*args):
        raise RuntimeError("offline")

    harness = Harness(tmp_path, enroll_fn=enroll)
    start_harness(harness)
    deadline = time.monotonic() + 1
    while not harness.delays and time.monotonic() < deadline:
        time.sleep(0.005)
    assert harness.delays
    start = time.monotonic()
    quit_harness(harness)
    assert time.monotonic() - start <= 3
    assert load_credentials(tmp_path) is None
