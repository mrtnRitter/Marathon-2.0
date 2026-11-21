import json
import logging
import os
import subprocess
import socket
from pathlib import Path
import sys
import time
from threading import Event, Thread


from marathon.server.config import ServerWorkerConfig
from marathon.server.worker_supervisor import WorkerSupervisor, server_url, server_worker_command


def wait_for(predicate, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_supervisor(tmp_path, code, **kwargs):
    log_file = tmp_path / "logs" / "worker.log"
    return WorkerSupervisor(
        [sys.executable, "-c", code],
        env=os.environ.copy(),
        cwd=tmp_path,
        log_file=log_file,
        **kwargs,
    ), log_file


def test_server_url():
    assert server_url("0.0.0.0", 8080) == "http://127.0.0.1:8080"
    assert server_url("::", 1) == "http://127.0.0.1:1"
    assert server_url("", 5) == "http://127.0.0.1:5"
    assert server_url("10.0.0.5", 9000) == "http://10.0.0.5:9000"


def test_server_worker_command():
    command = server_worker_command("http://127.0.0.1:8080", ServerWorkerConfig())
    assert command == [
        sys.executable, "-m", "marathon.worker", "--server", "http://127.0.0.1:8080",
        "--worker-id", "server-worker", "--name", "Server-Worker", "--exit-on-stdin-eof",
    ]


def test_restarts_after_crash(tmp_path):
    starts = tmp_path / "starts.txt"
    code = f"from pathlib import Path; p=Path({str(starts)!r}); p.open('a').write('start\\n'); raise SystemExit(1)"
    supervisor, _ = make_supervisor(tmp_path, code, backoff_s=(0.05,))
    supervisor.start()
    try:
        assert wait_for(lambda: starts.exists() and len(starts.read_text().splitlines()) >= 3, 1.5)
    finally:
        supervisor.stop()


def test_restarts_after_port_is_occupied(tmp_path):
    starts = tmp_path / "timestamps.txt"
    with socket.socket() as occupied_socket:
        occupied_socket.bind(("127.0.0.1", 0))
        occupied_socket.listen()
        port = occupied_socket.getsockname()[1]
        code = (
            "import socket, time; "
            f"open({str(starts)!r}, 'a').write(str(time.monotonic()) + '\\n'); "
            f"sock=socket.socket(); sock.bind(('127.0.0.1', {port}))"
        )
        supervisor, _ = make_supervisor(tmp_path, code, backoff_s=(0.15,))
        supervisor.start()
        try:
            assert wait_for(lambda: starts.exists() and len(starts.read_text().splitlines()) >= 3, 2.0)
            times = [float(line) for line in starts.read_text().splitlines()]
        finally:
            supervisor.stop()
    assert all(later - earlier >= 0.1 for earlier, later in zip(times, times[1:]))


def test_zero_graceful_timeout_skips_wait_before_termination(tmp_path, monkeypatch):
    code = "import time; exec('while True: time.sleep(1)')"
    supervisor, _ = make_supervisor(
        tmp_path, code, graceful_timeout_s=0.0, kill_timeout_s=0.5, backoff_s=(0.05,)
    )
    supervisor.start()
    timeouts = []
    original_wait = supervisor._wait_for_process

    def record_wait(process, timeout_s):
        timeouts.append(timeout_s)
        return original_wait(process, timeout_s)

    monkeypatch.setattr(supervisor, "_wait_for_process", record_wait)
    try:
        assert wait_for(lambda: supervisor._process is not None)
        assert supervisor.stop() is True
        assert timeouts[0] == 0.0
    finally:
        supervisor.stop()


def test_start_failure_does_not_raise_or_log_environment(tmp_path, caplog):
    secret = "worker-secret-test-value"
    caplog.set_level(logging.WARNING, logger="marathon.supervisor")
    log_file = tmp_path / "worker.log"
    supervisor = WorkerSupervisor(
        [str(tmp_path / "fehlt")],
        env={**os.environ, "MARATHON_TEST_SECRET": secret},
        cwd=tmp_path,
        log_file=log_file,
        backoff_s=(5,),
    )
    supervisor.start()
    try:
        assert wait_for(lambda: "failed to start" in caplog.text)
        assert secret not in caplog.text
        assert secret not in log_file.read_text(encoding="utf-8")
    finally:
        supervisor.stop()


def test_stop_closes_stdin_gracefully(tmp_path):
    marker = tmp_path / "graceful.txt"
    starts = tmp_path / "starts.txt"
    code = (
        "import sys; "
        f"open({str(starts)!r}, 'a').write('started\\n'); "
        "sys.stdin.read(); "
        f"open({str(marker)!r}, 'w').write('graceful')"
    )
    supervisor, _ = make_supervisor(tmp_path, code, graceful_timeout_s=0.5, backoff_s=(0.05,))
    supervisor.start()
    try:
        assert wait_for(starts.exists)
        started = time.monotonic()
        assert supervisor.stop() is True
        assert time.monotonic() - started < 2
        assert marker.read_text() == "graceful"
        time.sleep(0.1)
        assert len(starts.read_text().splitlines()) == 1
    finally:
        supervisor.stop()


def test_stop_escalates_to_kill(tmp_path):
    code = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN) if hasattr(signal, 'SIG_IGN') and __import__('os').name != 'nt' else None; "
        "exec('while True: time.sleep(1)')"
    )
    graceful_timeout_s = 0.2
    kill_timeout_s = 0.5
    supervisor, _ = make_supervisor(
        tmp_path,
        code,
        graceful_timeout_s=graceful_timeout_s,
        kill_timeout_s=kill_timeout_s,
        backoff_s=(0.05,),
    )
    supervisor.start()
    try:
        assert wait_for(lambda: supervisor._process is not None)
        started = time.monotonic()
        assert supervisor.stop() is True
        assert time.monotonic() - started < graceful_timeout_s + kill_timeout_s + 2
    finally:
        supervisor.stop()


def test_child_output_goes_to_log_file(tmp_path):
    supervisor, log_file = make_supervisor(tmp_path, "print('hello', flush=True)", backoff_s=(0.05,))
    supervisor.start()
    try:
        assert wait_for(lambda: log_file.exists() and "hello" in log_file.read_text())
        assert "hello" in log_file.read_text()
    finally:
        supervisor.stop()


def test_env_is_passed(tmp_path):
    target = tmp_path / "token.txt"
    code = f"import os; open({str(target)!r}, 'w').write(os.environ['MARATHON_WORKER_TOKEN'])"
    env = os.environ.copy()
    env["MARATHON_WORKER_TOKEN"] = "test-token"
    supervisor = WorkerSupervisor(
        [sys.executable, "-c", code], env=env, cwd=tmp_path, log_file=tmp_path / "worker.log",
        backoff_s=(0.05,),
    )
    supervisor.start()
    try:
        assert wait_for(target.exists)
        assert target.read_text() == "test-token"
    finally:
        supervisor.stop()


def test_stable_run_resets_backoff(tmp_path):
    starts = tmp_path / "timestamps.jsonl"
    code = (
        "import json, time; "
        f"open({str(starts)!r}, 'a').write(json.dumps(time.monotonic()) + '\\n'); "
        "time.sleep(0.3)"
    )
    supervisor, _ = make_supervisor(
        tmp_path, code, backoff_s=(0.05, 5), stable_s=0.2
    )
    supervisor.start()
    try:
        assert wait_for(lambda: starts.exists() and len(starts.read_text().splitlines()) >= 2, 2.0)
        times = [json.loads(line) for line in starts.read_text().splitlines()]
        assert times[1] - times[0] < 1.0
    finally:
        supervisor.stop()


def test_start_is_idempotent_and_stop_is_final(tmp_path):
    starts = tmp_path / "starts.txt"
    code = f"open({str(starts)!r}, 'a').write('started\\n'); import sys; sys.stdin.read()"
    supervisor = make_supervisor(tmp_path, code, backoff_s=(0.05,))[0]
    assert supervisor.stop() is True
    supervisor.start()
    assert not starts.exists()

    supervisor = make_supervisor(tmp_path, code, backoff_s=(0.05,))[0]
    supervisor.start()
    try:
        assert wait_for(starts.exists)
        supervisor.start()
        supervisor.start()
        time.sleep(0.1)
        assert len(starts.read_text().splitlines()) == 1
        assert supervisor.stop() is True
        supervisor.start()
        time.sleep(0.1)
        assert len(starts.read_text().splitlines()) == 1
    finally:
        supervisor.stop()


def test_stop_signals_during_child_start(tmp_path, monkeypatch):
    starts = tmp_path / "starts.txt"
    code = f"open({str(starts)!r}, 'a').write('started\\n'); import sys; sys.stdin.read()"
    supervisor, _ = make_supervisor(tmp_path, code, backoff_s=(0.05,))
    original_popen = subprocess.Popen
    popen_entered = Event()
    allow_popen = Event()
    results = []
    stop_thread = None

    def delayed_popen(*args, **kwargs):
        popen_entered.set()
        allow_popen.wait(2)
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", delayed_popen)
    supervisor.start()
    try:
        assert popen_entered.wait(1)
        stop_thread = Thread(target=lambda: results.append(supervisor.stop()))
        stop_thread.start()
        stop_signaled = wait_for(supervisor._stop_event.is_set, 0.5)
        allow_popen.set()
        stop_thread.join(3)
        assert stop_signaled
        assert not stop_thread.is_alive()
        assert results == [True]
        assert wait_for(starts.exists)
        assert len(starts.read_text().splitlines()) == 1
    finally:
        allow_popen.set()
        if stop_thread is not None and stop_thread.is_alive():
            stop_thread.join(3)
        supervisor.stop()


def test_stop_interrupts_restart_wait(tmp_path):
    starts = tmp_path / "starts.txt"
    code = f"open({str(starts)!r}, 'a').write('started\\n'); raise SystemExit(1)"
    supervisor, _ = make_supervisor(tmp_path, code, backoff_s=(5,))
    supervisor.start()
    try:
        assert wait_for(starts.exists)
        started = time.monotonic()
        assert supervisor.stop() is True
        assert time.monotonic() - started < 1
        assert len(starts.read_text().splitlines()) == 1
    finally:
        supervisor.stop()


def test_supervisor_closes_log_handles(tmp_path, monkeypatch):
    log_file = tmp_path / "logs" / "worker.log"
    original_open = Path.open
    handles = []

    def tracked_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if path == log_file and mode == "a":
            handles.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", tracked_open)
    code = "print('ready', flush=True); import sys; sys.stdin.read()"
    supervisor, _ = make_supervisor(tmp_path, code, backoff_s=(0.05,))
    supervisor.start()
    try:
        assert wait_for(lambda: log_file.exists() and "ready" in log_file.read_text())
        assert all(handle.closed for handle in handles) is False
    finally:
        supervisor.stop()
    assert handles and all(handle.closed for handle in handles)


def test_server_worker_command_with_ca_file(tmp_path):
    ca_file = tmp_path / "root.crt"
    command = server_worker_command("https://laptop-martin-r", ServerWorkerConfig(), ca_file=ca_file)

    assert command[command.index("--server") + 1] == "https://laptop-martin-r"
    assert command[-2:] == ["--ca-file", str(ca_file)]
