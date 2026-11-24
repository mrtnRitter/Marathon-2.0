"""Tests for the local Marathon runner."""

# IMPORTS
import os
import io
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import build
import run
import marathon.server.__main__ as server_main


# FUNC
@pytest.fixture
def ready_runner(tmp_path, monkeypatch):
    """Provide valid local paths and a deterministic server config."""
    venv = tmp_path / ".venv" / "bin" / "python"
    pg_ctl = tmp_path / "postgres" / "bin" / "pg_ctl"
    data_dir = tmp_path / ".data" / "postgres"
    password_file = tmp_path / ".data" / "postgres.pass"
    static_dir = tmp_path / "static"
    ui_dir = tmp_path / "ui"
    modules_dir = ui_dir / "node_modules"
    node = tmp_path / "node"
    for path in (venv, pg_ctl, node):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    data_dir.mkdir(parents=True)
    (data_dir / "PG_VERSION").write_text("18\n", encoding="utf-8")
    password_file.write_text("test-password", encoding="utf-8")
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    ui_dir.mkdir(exist_ok=True)
    modules_dir.mkdir()
    config = SimpleNamespace(
        host="127.0.0.1", port=8080,
        database=SimpleNamespace(host="127.0.0.1", port=5433),
    )
    monkeypatch.setattr(run, "venv_python", lambda: venv)
    monkeypatch.setattr(run, "pg_tool", lambda name: pg_ctl)
    monkeypatch.setattr(run, "pg_data_dir", data_dir)
    monkeypatch.setattr(run, "pg_password_file", password_file)
    monkeypatch.setattr(run, "pg_log_file", tmp_path / ".data" / "postgres.log")
    monkeypatch.setattr(run, "static_dir", static_dir)
    monkeypatch.setattr(run, "node_exe", lambda: node)
    monkeypatch.setattr(run, "ui_dir", ui_dir)
    monkeypatch.setattr(run, "ui_modules_dir", modules_dir)
    monkeypatch.setattr(run, "root_dir", tmp_path)
    monkeypatch.setattr(run, "load_server_config", lambda: config)
    monkeypatch.setattr(run, "node_env", lambda: {**os.environ})
    monkeypatch.setattr(run, "ui_command", lambda *args: ["vite"])
    return config


def completed(returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode)


def test_main_orders_database_migration_server_and_stop(ready_runner, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        if command[1:2] == ["status"]:
            calls.append("status")
            return completed(1)
        if command[1:2] == ["start"]:
            calls.append("start")
            assert command[command.index("-o") + 1] == "-p 5433 -h 127.0.0.1"
            return completed()
        if command[1:4] == ["-m", "marathon.server.db", "upgrade"]:
            calls.append("upgrade")
            return completed()
        if command[1:2] == ["stop"]:
            calls.append("stop")
            return completed()
        raise AssertionError(command)

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    process = SimpleNamespace(args=["python", "-m", "marathon.server"], returncode=0)
    monkeypatch.setattr(run, "_start_child", lambda command, **kwargs: process)
    monkeypatch.setattr(run, "_watch_console", lambda stop_event, restart_event=None: None)
    monkeypatch.setattr(run, "_wait_child", lambda child, stop_event, restart_event=None: calls.append("server") or 0)

    assert run.main([]) == 0
    assert calls == ["status", "start", "upgrade", "server", "stop"]


def test_main_stops_database_after_server_keyboard_interrupt(ready_runner, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[1])
        return completed(1 if command[1] == "status" else 0)

    def interrupt_server(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    process = FakeProcess(args=("python", "-m", "marathon.server"), signal_returncode=0)
    monkeypatch.setattr(run, "_start_child", lambda command, **kwargs: process)
    monkeypatch.setattr(run, "_watch_console", lambda stop_event, restart_event=None: None)
    monkeypatch.setattr(run, "_wait_child", interrupt_server)

    assert run.main([]) == 0
    assert calls == ["status", "start", "-m", "stop"]
    assert process.signals == [signal.SIGINT]


def test_main_cleans_database_if_start_is_interrupted(ready_runner, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        if command[1] == "status":
            calls.append("status")
            return completed(1)
        if command[1] == "start":
            calls.append("start")
            raise KeyboardInterrupt
        if command[1] == "stop":
            calls.append("stop")
            return completed(1)
        raise AssertionError(command)

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run.main([]) == 0
    assert calls == ["status", "start", "stop", "status"]


def test_main_stops_database_after_failed_upgrade(ready_runner, monkeypatch, capsys):
    calls = []

    def fake_run(command, **kwargs):
        if command[1] == "status":
            return completed(1)
        if command[1] == "start":
            return completed()
        if command[1] == "-m":
            calls.append("upgrade")
            return completed(2)
        if command[1] == "stop":
            calls.append("stop")
            return completed()
        raise AssertionError(command)

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    assert run.main([]) == 1
    assert calls == ["upgrade", "stop"]
    assert "Fehler" in capsys.readouterr().err


def test_main_does_not_stop_database_when_start_fails(ready_runner, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[1])
        return completed(1)

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run.subprocess, "call", lambda *args, **kwargs: 0)

    assert run.main([]) == 1
    assert calls == ["status", "start"]


def test_stop_database_falls_back_to_immediate_when_fast_fails(ready_runner, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "stop" and "-m" in command and command[command.index("-m") + 1] == "fast":
            return completed(1)
        if command[1] == "status":
            return completed(0)
        return completed()

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    run.stop_database()

    assert [command[command.index("-m") + 1] for command in calls if "-m" in command] == ["fast", "immediate"]
    assert calls[1][1] == "status"


def test_stop_database_uses_immediate_after_interrupt(ready_runner, monkeypatch):
    events = []

    def fake_run(command, **kwargs):
        if command[1] == "stop":
            mode = command[command.index("-m") + 1]
            events.append(mode)
            if mode == "fast":
                raise KeyboardInterrupt
            return completed(1)
        if command[1] == "status":
            events.append("status")
            return completed(1)
        raise AssertionError(command)

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    run.stop_database()

    assert events == ["fast", "status", "immediate", "status"]


def test_finite_command_timeout_becomes_build_error(monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(run.subprocess, "run", timeout)

    with pytest.raises(build.BuildError, match="named-command"):
        run._run_command(["named-command"], timeout=3)


def test_dev_kills_unresponsive_server_then_stops_database(ready_runner, monkeypatch):
    config = ready_runner
    config.host = "0.0.0.0"
    config.port = 8123
    events = []

    class Server:
        args = ["python", "-m", "marathon.server"]
        returncode = None

        def poll(self):
            return self.returncode

        def send_signal(self, sig):
            events.append(f"signal-{sig}")

        def wait(self, timeout):
            events.append(f"wait-{timeout:g}")
            if timeout == 10:
                raise subprocess.TimeoutExpired("server", timeout)
            self.returncode = -9
            return self.returncode

        def kill(self):
            events.append("kill")

    server = Server()
    vite = FakeProcess(args=("vite",), returncode=0)

    def fake_run(command, **kwargs):
        if command[1] == "status":
            return completed(1)
        if command[1] == "start":
            return completed()
        if command[1] == "-m":
            return completed()
        if command[1] == "stop":
            events.append("db-stop")
            return completed()
        raise AssertionError(command)

    def fake_start_child(command, **kwargs):
        if command[-1:] == ["marathon.server"]:
            events.append("server-start")
            return server
        events.append("vite-start")
        assert kwargs["env"]["MARATHON_API_URL"] == "http://127.0.0.1:8123"
        return vite

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run, "_start_child", fake_start_child)
    monkeypatch.setattr(run, "_watch_console", lambda stop_event, restart_event=None: None)

    assert run.main(["--dev"]) == 0
    assert events == ["server-start", "vite-start", f"signal-{signal.SIGINT}", "wait-10", "kill", "wait-5", "db-stop"]


def test_api_url_maps_wildcard_host_to_loopback():
    assert run._api_url("0.0.0.0", 8123) == "http://127.0.0.1:8123"


@pytest.mark.parametrize("host", ["::", ""])
def test_api_url_maps_other_wildcard_hosts_to_loopback(host):
    assert run._api_url(host, 8123) == "http://127.0.0.1:8123"


def test_help_lists_dev_option(capsys):
    with pytest.raises(SystemExit) as error:
        run.main(["--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--dev" in output
    assert set(re.findall(r"--[a-z][a-z-]*", output)) == {"--help", "--dev", "--trust"}


def test_postgres_start_stop_integration(tmp_path, monkeypatch):
    bin_dir = Path(os.environ.get("MARATHON_TEST_PG_BIN", build.pg_bin_dir))
    tool_suffix = ".exe" if os.name == "nt" else ""
    required_tools = [bin_dir / f"{name}{tool_suffix}" for name in ("initdb", "pg_ctl", "postgres")]
    if not all(path.is_file() for path in required_tools):
        pytest.skip("PostgreSQL-Programme fehlen")
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("initdb kann nicht als root ausgeführt werden")

    data_dir = tmp_path / ".data"
    pg_data_dir = data_dir / "postgres"
    password_file = data_dir / "postgres.pass"
    log_file = data_dir / "postgres.log"
    monkeypatch.setattr(build, "data_dir", data_dir)
    monkeypatch.setattr(build, "pg_data_dir", pg_data_dir)
    monkeypatch.setattr(build, "pg_password_file", password_file)
    monkeypatch.setattr(build, "pg_bin_dir", bin_dir)
    monkeypatch.setattr(run, "pg_data_dir", pg_data_dir)
    monkeypatch.setattr(run, "pg_password_file", password_file)
    monkeypatch.setattr(run, "pg_log_file", log_file)
    monkeypatch.setattr(run, "pg_tool", build.pg_tool)

    build.init_database()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    config = SimpleNamespace(
        host="127.0.0.1", port=8080,
        database=SimpleNamespace(host="127.0.0.1", port=port),
    )
    try:
        run.start_database(config)
        run.stop_database()
    finally:
        status = subprocess.run(
            [str(bin_dir / f"pg_ctl{tool_suffix}"), "status", "-D", str(pg_data_dir)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    assert status.returncode != 0


# CHILD PROCESS SHUTDOWN TESTS
class FakeProcess:
    def __init__(self, args=("python",), returncode=None, signal_returncode=None, interrupt_on_wait=False):
        self.args = list(args)
        self.returncode = returncode
        self.signal_returncode = signal_returncode
        self.interrupt_on_wait = interrupt_on_wait
        self.signals = []
        self.wait_timeouts = []
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.interrupt_on_wait:
            self.interrupt_on_wait = False
            raise KeyboardInterrupt
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        if self.signal_returncode is not None:
            self.returncode = self.signal_returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def watch_stream(stream):
    stop_event = threading.Event()
    thread = run._watch_console(stop_event, stream)
    thread.join(timeout=1)
    assert not thread.is_alive()
    return stop_event


def test_watch_console_restart():
    stop_event = threading.Event()
    restart_event = threading.Event()
    thread = run._watch_console(stop_event, io.StringIO("restart\n"), restart_event)
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert restart_event.is_set()
    assert not stop_event.is_set()


def test_start_server_restarts_on_exit_75(monkeypatch, capsys):
    processes = [FakeProcess(returncode=75), FakeProcess(returncode=0)]
    starts = []
    monkeypatch.setattr(run, "_start_child", lambda *args, **kwargs: starts.append(args[0]) or processes.pop(0))
    monkeypatch.setattr(run, "_watch_console", lambda stop_event, restart_event=None: None)

    assert run.start_server(Path("python")) == 0
    assert len(starts) == 2
    assert "Server wird neu gestartet …" in capsys.readouterr().out


def test_start_server_clears_restart_event_before_new_child(monkeypatch):
    starts = []
    processes = [FakeProcess(returncode=75), FakeProcess(returncode=0)]

    def start_child(*args, **kwargs):
        starts.append(args[0])
        return processes.pop(0)

    def request_restart(stop_event, restart_event=None):
        restart_event.set()

    monkeypatch.setattr(run, "_start_child", start_child)
    monkeypatch.setattr(run, "_watch_console", request_restart)

    assert run.start_server(Path("python")) == 0
    assert len(starts) == 2


def test_start_server_restart_command(monkeypatch, capsys):
    events = {}
    starts = []

    class ExitsWithoutSignal(FakeProcess):
        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            self.returncode = 9
            return self.returncode

    first = ExitsWithoutSignal()
    second = FakeProcess(signal_returncode=0)

    def start_child(*args, **kwargs):
        process = first if not starts else second
        starts.append(args[0])
        if len(starts) == 2:
            events["stop_event"].set()
        return process

    def watch_console(stop_event, restart_event=None):
        events["stop_event"] = stop_event
        restart_event.set()

    monkeypatch.setattr(run, "_start_child", start_child)
    monkeypatch.setattr(run, "_watch_console", watch_console)

    assert run.start_server(Path("python")) == 0
    assert len(starts) == 2
    assert first.signals == [signal.SIGINT]
    assert second.signals == [signal.SIGINT]
    assert "Server wird neu gestartet …" in capsys.readouterr().out


def test_stop_wins_over_restart(monkeypatch):
    starts = []
    monkeypatch.setattr(run, "_start_child", lambda *args, **kwargs: starts.append(args[0]) or FakeProcess(returncode=75))

    def stop_and_restart(stop_event, restart_event=None):
        stop_event.set()
        restart_event.set()

    monkeypatch.setattr(run, "_watch_console", stop_and_restart)

    assert run.start_server(Path("python")) == 75
    assert len(starts) == 1


def test_start_server_restart_child_ignores_stop(monkeypatch, capsys):
    events = {}
    starts = []
    first = FakeProcess()
    second = FakeProcess(signal_returncode=0)
    original_wait_child = run._wait_child

    def start_child(*args, **kwargs):
        process = first if not starts else second
        starts.append(args[0])
        if len(starts) == 2:
            events["stop_event"].set()
        return process

    def watch_console(stop_event, restart_event=None):
        events["stop_event"] = stop_event
        restart_event.set()

    def bounded_wait(process, stop_event, grace_s=10.0, restart_event=None):
        if restart_event is None:
            process.returncode = 75
            return process.returncode
        return original_wait_child(process, stop_event, grace_s=0.01, restart_event=restart_event)

    monkeypatch.setattr(run, "_start_child", start_child)
    monkeypatch.setattr(run, "_watch_console", watch_console)
    monkeypatch.setattr(run, "_wait_child", bounded_wait)

    assert run.start_server(Path("python")) == 0
    assert len(starts) == 2
    assert first.signals == [signal.SIGINT]
    assert first.killed
    assert "Server wird neu gestartet …" in capsys.readouterr().out


def test_dev_restart_keeps_vite(ready_runner, monkeypatch, capsys):
    events = {}
    server_starts = []
    vite_starts = []

    class StopsOnPoll(FakeProcess):
        def poll(self):
            events["stop_event"].set()
            return None

    first_server = FakeProcess(args=("python", "-m", "marathon.server"), returncode=75)
    second_server = StopsOnPoll(args=("python", "-m", "marathon.server"), signal_returncode=0)
    vite = FakeProcess(args=("vite",))

    def start_child(command, **kwargs):
        if command[-1:] == ["marathon.server"]:
            process = first_server if not server_starts else second_server
            server_starts.append(command)
            return process
        vite_starts.append(command)
        return vite

    monkeypatch.setattr(run, "_start_child", start_child)
    def watch_console(stop_event, restart_event=None):
        events["stop_event"] = stop_event

    monkeypatch.setattr(run, "_watch_console", watch_console)
    monkeypatch.setattr(run, "node_env", lambda: {})
    monkeypatch.setattr(run, "ui_command", lambda *args: ["vite"])

    assert run.start_dev(ready_runner, Path("python")) == 0
    assert len(server_starts) == 2
    assert len(vite_starts) == 1
    assert vite.returncode == -15
    assert "Server wird neu gestartet …" in capsys.readouterr().out


def test_main_returns_75_on_restart(tmp_path, monkeypatch):
    app = SimpleNamespace(
        state=SimpleNamespace(
            shutdown_event=threading.Event(),
            live_hub=SimpleNamespace(close_streams=lambda: None),
        )
    )
    config = SimpleNamespace(host="127.0.0.1", port=8080, engine=None, server_worker=None)
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").touch()
    monkeypatch.setattr(server_main, "load_server_config", lambda: config)
    monkeypatch.setattr(server_main, "default_static_dir", static_dir)
    monkeypatch.setattr(server_main, "create_app", lambda **kwargs: app)

    def request_restart(app, host, port):
        app.state.request_restart = lambda: setattr(app.state, "restart_requested", True)
        app.state.request_restart()

    monkeypatch.setattr(server_main, "serve", request_restart)

    assert server_main.main() == 75

    monkeypatch.setattr(server_main, "serve", lambda app, host, port: None)
    assert server_main.main() == 0


def test_stop_command_requests_stop_once_and_returns_child_exit_code():
    stop_event = watch_stream(io.StringIO("stop\n"))
    process = FakeProcess(returncode=None, signal_returncode=23)

    assert stop_event.is_set()
    assert run._wait_child(process, stop_event) == 23
    assert process.signals == [signal.SIGINT]


def test_stop_command_ignores_case_and_surrounding_whitespace():
    stop_event = watch_stream(io.StringIO(" STOP \n"))
    process = FakeProcess(returncode=None, signal_returncode=0)

    assert stop_event.is_set()
    assert run._wait_child(process, stop_event) == 0
    assert process.signals == [signal.SIGINT]


def test_other_console_input_is_ignored():
    stop_event = watch_stream(io.StringIO("exit\nstop now\n"))
    process = FakeProcess(returncode=6)

    assert not stop_event.is_set()
    assert run._wait_child(process, stop_event) == 6
    assert process.signals == []


def test_eof_does_not_stop_child_and_finished_process_ends_waiting():
    stop_event = watch_stream(io.StringIO(""))
    process = FakeProcess(returncode=8)

    assert not stop_event.is_set()
    assert run._wait_child(process, stop_event) == 8
    assert process.signals == []


def test_unresponsive_child_is_killed_after_grace_period():
    stop_event = threading.Event()
    stop_event.set()
    process = FakeProcess()

    assert run._wait_child(process, stop_event, grace_s=0.01) == -9
    assert process.signals == [signal.SIGINT]
    assert process.killed
    assert 0 < process.wait_timeouts[-1] <= 5


def test_keyboard_interrupt_requests_orderly_stop():
    process = FakeProcess(signal_returncode=0, interrupt_on_wait=True)

    assert run._wait_child(process, threading.Event()) == 0
    assert process.signals == [signal.SIGINT]


def test_start_server_stops_child_if_interrupted_before_wait(monkeypatch):
    process = FakeProcess(signal_returncode=0)
    starts = []

    def watch_console(stop_event, restart_event=None):
        restart_event.set()

    monkeypatch.setattr(run, "_watch_console", watch_console)
    monkeypatch.setattr(run, "_start_child", lambda *args, **kwargs: starts.append(args[0]) or process)
    monkeypatch.setattr(run, "_wait_child", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt))

    assert run.start_server(Path("python")) == 0
    assert len(starts) == 1
    assert process.signals == [signal.SIGINT]


@pytest.mark.parametrize("ended", ["server", "vite"])
def test_dev_child_exit_stops_the_other_child(ready_runner, monkeypatch, capsys, ended):
    server = FakeProcess(args=("python", "-m", "marathon.server"), signal_returncode=0)
    vite = FakeProcess(args=("vite",))
    if ended == "server":
        server.returncode = 7
        expected_code = 7
    else:
        vite.returncode = 7
        expected_code = 7

    def fake_start_child(command, **kwargs):
        return server if command[-1:] == ["marathon.server"] else vite

    monkeypatch.setattr(run, "_start_child", fake_start_child)
    monkeypatch.setattr(run, "_watch_console", lambda stop_event, restart_event=None: None)
    monkeypatch.setattr(run, "node_env", lambda: {})
    monkeypatch.setattr(run, "ui_command", lambda *args: ["vite"])

    assert run.start_dev(ready_runner, Path("python")) == expected_code
    assert server.returncode is not None
    assert vite.returncode is not None
    assert "Beenden mit „stop“, Neustart mit „restart“ und Enter" in capsys.readouterr().out
    assert server.signals == ([] if ended == "server" else [signal.SIGINT])


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal handling required")
def test_stop_command_stops_real_signal_aware_child_under_two_seconds():
    import select

    script = "import signal, time; signal.signal(signal.SIGINT, lambda *_: exit(0)); print('ready', flush=True); time.sleep(10)"
    process = run._start_child([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert select.select([process.stdout], [], [], 1)[0]
    assert process.stdout.readline().strip() == "ready"
    stop_event = threading.Event()
    watcher = run._watch_console(stop_event, io.StringIO("stop\n"))
    started = time.monotonic()
    try:
        assert run._wait_child(process, stop_event, grace_s=1) == 0
    finally:
        watcher.join(timeout=0.2)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
    assert time.monotonic() - started < 2


def test_stop_and_keyboard_interrupt_request_stop_at_most_once():
    stop_event = threading.Event()
    stop_event.set()
    process = FakeProcess(interrupt_on_wait=True)

    assert run._wait_child(process, stop_event, grace_s=0.01) == -9
    assert process.signals == [signal.SIGINT]


def test_main_stops_database_after_kill_wait_timeout(ready_runner, monkeypatch, capsys):
    class StuckProcess(FakeProcess):
        def kill(self):
            self.killed = True

    events = []
    process = StuckProcess(args=("python", "-m", "marathon.server"))

    def fake_run(command, **kwargs):
        if command[1] == "status":
            return completed(1)
        if command[1] == "start":
            return completed()
        if command[1] == "-m":
            return completed()
        if command[1] == "stop":
            events.append("db-stop")
            return completed()
        raise AssertionError(command)

    def stop_console(stop_event, stream=None, restart_event=None):
        stop_event.set()
        return None

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run, "_start_child", lambda command, **kwargs: process)
    monkeypatch.setattr(run, "_watch_console", stop_console)

    assert run.main([]) == 1
    assert process.killed
    assert events == ["db-stop"]
    assert "Zeitlimit beim Beenden des Servers überschritten: python" in capsys.readouterr().err



@pytest.mark.skipif(os.name != "posix", reason="POSIX process sessions required")
def test_start_child_creates_posix_session(monkeypatch):
    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return "child"

    monkeypatch.setattr(run.subprocess, "Popen", fake_popen)

    assert run._start_child(["server"]) == "child"
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["start_new_session"] is True


def test_start_child_disables_stdin_and_creates_windows_process_group(monkeypatch):
    captured = {}
    group_flag = 512

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return "child"

    monkeypatch.setattr(run.os, "name", "nt")
    monkeypatch.setattr(run.subprocess, "CREATE_NEW_PROCESS_GROUP", group_flag, raising=False)
    monkeypatch.setattr(run.subprocess, "Popen", fake_popen)

    assert run._start_child(["server"], stdin=subprocess.PIPE) == "child"
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["creationflags"] == group_flag


def test_request_stop_uses_windows_ctrl_break(monkeypatch):
    expected_signal = 21
    monkeypatch.setattr(run.os, "name", "nt")
    monkeypatch.setattr(run.signal, "CTRL_BREAK_EVENT", expected_signal, raising=False)
    process = FakeProcess()

    run._request_stop(process)

    assert process.signals == [expected_signal]


def test_request_stop_ignores_os_error_only_after_child_exit():
    class VanishedProcess(FakeProcess):
        def send_signal(self, sig):
            raise OSError("child already exited")

    run._request_stop(VanishedProcess(returncode=0))
    with pytest.raises(OSError, match="child already exited"):
        run._request_stop(VanishedProcess())


def test_dev_keyboard_interrupt_stops_both_children(ready_runner, monkeypatch):
    server = FakeProcess(args=("python", "-m", "marathon.server"), signal_returncode=0)
    vite = FakeProcess(args=("vite",))
    monkeypatch.setattr(run, "_start_child", lambda command, **kwargs: server if command[-1:] == ["marathon.server"] else vite)
    monkeypatch.setattr(run, "_watch_console", lambda stop_event, restart_event=None: None)
    monkeypatch.setattr(run, "node_env", lambda: {})
    monkeypatch.setattr(run, "ui_command", lambda *args: ["vite"])
    monkeypatch.setattr(run, "_wait_for_dev_end", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt))

    assert run.start_dev(ready_runner, Path("python")) == 0
    assert server.signals == [signal.SIGINT]
    assert vite.returncode == -15


def test_trust_missing_certificate(tmp_path, monkeypatch, capsys):
    cert = tmp_path / "fehlt.crt"
    monkeypatch.setattr(run, "caddy_root_cert", cert)

    assert run.trust_certificate() == 1
    assert "Stammzertifikat fehlt" in capsys.readouterr().err


def test_trust_windows_calls_certutil(tmp_path, monkeypatch):
    cert = tmp_path / "root.crt"
    cert.write_text("certificate", encoding="utf-8")
    monkeypatch.setattr(run, "caddy_root_cert", cert)
    monkeypatch.setattr(run, "is_windows", True)
    calls = []

    def fake_run(command, timeout):
        calls.append((command, timeout))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run, "_run_command", fake_run)

    assert run.trust_certificate() == 0
    assert calls == [(["certutil", "-user", "-addstore", "Root", str(cert)], 120)]


def test_trust_windows_failure(tmp_path, monkeypatch, capsys):
    cert = tmp_path / "root.crt"
    cert.write_text("certificate", encoding="utf-8")
    monkeypatch.setattr(run, "caddy_root_cert", cert)
    monkeypatch.setattr(run, "is_windows", True)
    monkeypatch.setattr(run, "_run_command", lambda command, timeout: subprocess.CompletedProcess(command, 5))

    assert run.trust_certificate() == 1
    assert "Exit-Code 5" in capsys.readouterr().err


def test_trust_linux_prints_path(tmp_path, monkeypatch, capsys):
    cert = tmp_path / "root.crt"
    cert.write_text("certificate", encoding="utf-8")
    monkeypatch.setattr(run, "caddy_root_cert", cert)
    monkeypatch.setattr(run, "is_windows", False)

    assert run.trust_certificate() == 0
    output = capsys.readouterr().out
    assert str(cert) in output
    assert "update-ca-certificates" in output


def test_main_trust_skips_database(monkeypatch):
    monkeypatch.setattr(run, "start_database", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(run, "_validate_prerequisites", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(run, "trust_certificate", lambda: 0)

    assert run.main(["--trust"]) == 0


def test_help_lists_trust_option(monkeypatch, capsys):
    with pytest.raises(SystemExit) as error:
        run.main(["--help"])

    assert error.value.code == 0
    assert "--trust" in capsys.readouterr().out


def test_run_mail_test_calls_module(ready_runner, monkeypatch):
    calls = []

    def fake_run(command, *, timeout):
        calls.append((command, timeout))
        return completed(7)

    monkeypatch.setattr(run, "_run_command", fake_run)
    assert run.main(["mail-test", "max@example.test"]) == 7
    assert calls == [([str(run.venv_python()), "-m", "marathon.server.mail", "test", "max@example.test"], 60)]

def test_run_mail_test_requires_address(ready_runner, monkeypatch):
    monkeypatch.setattr(run, "_run_command", lambda *args, **kwargs: pytest.fail("no subprocess expected"))
    with pytest.raises(SystemExit) as caught:
        run.main(["mail-test"])
    assert caught.value.code == 2


def test_run_admin_code_calls_module(ready_runner, monkeypatch):
    calls = []

    def fake_run(command, *, timeout):
        calls.append((command, timeout))
        return completed(7)

    monkeypatch.setattr(run, "_run_command", fake_run)
    assert run.main(["admin-code", "admin@example.test"]) == 7
    assert calls == [([str(run.venv_python()), "-m", "marathon.server.auth", "admin-code", "admin@example.test"], 60)]


def test_run_admin_code_requires_address(ready_runner, monkeypatch, capsys):
    monkeypatch.setattr(run, "_run_command", lambda *args, **kwargs: pytest.fail("no subprocess expected"))
    with pytest.raises(SystemExit) as caught:
        run.main(["admin-code"])
    assert caught.value.code == 2
    assert "admin-code braucht eine E-Mail-Adresse" in capsys.readouterr().err
