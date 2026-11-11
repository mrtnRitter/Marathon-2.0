"""Tests for the local Marathon runner."""

# IMPORTS
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import build
import run


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


def test_main_orders_database_migration_demo_server_and_stop(ready_runner, monkeypatch):
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
        if command[1:4] == ["-m", "marathon.server.db", "demo-data"]:
            calls.append("demo-data")
            return completed()
        if command[1:2] == ["stop"]:
            calls.append("stop")
            return completed()
        raise AssertionError(command)

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run.subprocess, "call", lambda *args, **kwargs: calls.append("server") or 0)

    assert run.main(["--demo-data"]) == 0
    assert calls == ["status", "start", "upgrade", "demo-data", "server", "stop"]


def test_main_stops_database_after_server_keyboard_interrupt(ready_runner, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[1])
        return completed(1 if command[1] == "status" else 0)

    def interrupt_server(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run.subprocess, "call", interrupt_server)

    assert run.main([]) == 0
    assert calls == ["status", "start", "-m", "stop"]


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
        def terminate(self):
            events.append("terminate")

        def wait(self, timeout):
            events.append(f"wait-{timeout}")
            if timeout == 10:
                raise subprocess.TimeoutExpired("server", timeout)
            return -9

        def kill(self):
            events.append("kill")

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

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run.subprocess, "Popen", lambda *args, **kwargs: events.append("server-start") or Server())

    def fake_vite(command, **kwargs):
        events.append("vite")
        assert kwargs["env"]["MARATHON_API_URL"] == "http://127.0.0.1:8123"
        return 0

    monkeypatch.setattr(run.subprocess, "call", fake_vite)

    assert run.main(["--dev"]) == 0
    assert events == ["server-start", "vite", "terminate", "wait-10", "kill", "wait-5", "db-stop"]


def test_api_url_maps_wildcard_host_to_loopback():
    assert run._api_url("0.0.0.0", 8123) == "http://127.0.0.1:8123"


@pytest.mark.parametrize("host", ["::", ""])
def test_api_url_maps_other_wildcard_hosts_to_loopback(host):
    assert run._api_url(host, 8123) == "http://127.0.0.1:8123"


def test_help_includes_both_options(capsys):
    with pytest.raises(SystemExit) as error:
        run.main(["--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--dev" in output
    assert "--demo-data" in output


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
