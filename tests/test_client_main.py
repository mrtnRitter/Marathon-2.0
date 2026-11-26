import logging
import subprocess
import sys
from pathlib import Path

import pytest

from marathon.client import __main__ as client_main
from marathon.client.local_state import Bootstrap, VersionState, save_version_state, version_exe


class FakeOps:
    def __init__(self):
        self.messages = []

    def message(self, title, text, error=False):
        self.messages.append((title, text, error))


def test_development_requires_run_or_uninstall(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(client_main, "client_dir", lambda: tmp_path)
    assert client_main.main([]) == 2
    assert "usage:" in capsys.readouterr().out


def test_run_and_uninstall_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(client_main, "client_dir", lambda: tmp_path)
    monkeypatch.setattr(client_main, "read_bootstrap", lambda path: Bootstrap("https://server.example", "ca", "1.2.3"))
    called = []
    monkeypatch.setattr(client_main, "run_client", lambda base, bootstrap: called.append((base, bootstrap)) or 7)
    assert client_main.main(["--run", "--bootstrap", str(tmp_path / "bootstrap.json")]) == 7
    assert called and called[0][0] == tmp_path

    ops = FakeOps()
    monkeypatch.setattr(client_main, "windows_ops", lambda: ops)
    monkeypatch.setattr(client_main, "uninstall", lambda base, actual_ops, running: 9)
    assert client_main.main(["--uninstall"]) == 9


def test_frozen_installs_when_not_running_from_installed_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(client_main, "client_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "download" / "client.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    bootstrap = Bootstrap("https://server.example", "ca", "1.2.3")
    monkeypatch.setattr(client_main, "read_bootstrap", lambda path: bootstrap)
    monkeypatch.setattr(client_main, "is_installed_copy", lambda executable, base: False)
    monkeypatch.setattr(client_main, "is_client_running", lambda: False)
    ops = FakeOps()
    monkeypatch.setattr(client_main, "windows_ops", lambda: ops)
    calls = []
    monkeypatch.setattr(client_main, "install", lambda exe, data, base, actual_ops, running: calls.append((exe, data, base, actual_ops, running)) or 0)
    assert client_main.main([]) == 0
    assert calls and calls[0][1] == bootstrap


def test_frozen_installed_starts_starter_with_hidden_process(tmp_path, monkeypatch):
    monkeypatch.setattr(client_main, "client_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "MarathonWorker.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(client_main, "read_bootstrap", lambda path: Bootstrap("https://server.example", "ca", "1.2.3"))
    monkeypatch.setattr(client_main, "is_installed_copy", lambda executable, base: True)
    spawned = []

    class Process:
        def wait(self):
            return 0

    executable = version_exe(tmp_path, "1.2.3")
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fake executable")
    save_version_state(tmp_path, VersionState("1.2.3"))
    monkeypatch.setattr(client_main.subprocess, "Popen", lambda args, creationflags=0: spawned.append((args, creationflags)) or Process())
    result = client_main.main([])
    assert result == 0
    assert spawned[0][0] == [str(executable), "--run"]
    assert spawned[0][1] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_bootstrap_error_shows_frozen_message_and_returns_two(tmp_path, monkeypatch):
    monkeypatch.setattr(client_main, "client_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(client_main, "read_bootstrap", lambda path: (_ for _ in ()).throw(ValueError("bootstrap fehlt")))
    ops = FakeOps()
    monkeypatch.setattr(client_main, "windows_ops", lambda: ops)
    assert client_main.main([]) == 2
    assert ops.messages[0][1] == "bootstrap fehlt"


def test_unfrozen_bootstrap_failure_returns_two(tmp_path, monkeypatch):
    monkeypatch.setattr(client_main, "client_dir", lambda: tmp_path)
    monkeypatch.setattr(client_main, "read_bootstrap", lambda path: (_ for _ in ()).throw(ValueError("ungültig")))
    assert client_main.main(["--run", "--bootstrap", str(tmp_path / "bootstrap.json")]) == 2


def test_main_import_does_not_import_pystray():
    assert "pystray" not in sys.modules
