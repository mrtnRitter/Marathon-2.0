from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Mapping

import pytest

from marathon.client.local_state import (
    Bootstrap,
    VersionState,
    load_version_state,
    save_version_state,
    save_credentials,
    WorkerCredentials,
    exe_name,
    version_exe,
)


class FakeInstallerOps:
    def __init__(self, *, import_result: bool = True) -> None:
        self.import_result = import_result
        self.confirm_result = True
        self.confirmations: list[tuple[str, str]] = []
        self.messages: list[tuple[str, str, bool]] = []
        self.run_entries: list[tuple[str, str | None]] = []
        self.uninstall_entries: list[tuple[str, Mapping[str, str | int] | None]] = []
        self.imported_paths: list[Path] = []
        self.launches: list[list[str]] = []
        self.scheduled_deletions: list[Path] = []

    def confirm(self, title: str, text: str) -> bool:
        self.confirmations.append((title, text))
        return self.confirm_result

    def message(self, title: str, text: str, error: bool = False) -> None:
        self.messages.append((title, text, error))

    def set_run_entry(self, name: str, command: str | None) -> None:
        self.run_entries.append((name, command))

    def set_uninstall_entry(self, key: str, values: Mapping[str, str | int] | None) -> None:
        self.uninstall_entries.append((key, values))

    def import_root_cert(self, pem_path: Path) -> bool:
        assert pem_path.exists()
        self.imported_paths.append(pem_path)
        return self.import_result

    def launch(self, args: list[str]) -> None:
        self.launches.append(args)

    def schedule_delete(self, path: Path) -> None:
        self.scheduled_deletions.append(path)


def test_install_creates_files_state_entries_imports_certificate_and_launches(tmp_path: Path) -> None:
    from marathon.client.installer import app_key, display_name, install

    source = tmp_path / "source.exe"
    source.write_bytes(b"client-exe")
    base = tmp_path / "profile" / "MarathonWorker"
    bootstrap = Bootstrap("https://server.example", "-----BEGIN CERTIFICATE-----\nabc\n", "1.2.3")
    ops = FakeInstallerOps()

    assert install(source, bootstrap, base, ops, lambda: False) == 0

    starter = base / exe_name
    versioned = version_exe(base, "1.2.3")
    assert starter.read_bytes() == versioned.read_bytes() == b"client-exe"
    assert load_version_state(base) == VersionState("1.2.3")
    assert ops.confirmations == [(
        "Marathon-Worker installieren",
        "Marathon-Worker auf diesem Rechner installieren? Der Worker startet danach automatisch mit Windows.",
    )]
    assert ops.run_entries == [(app_key, f'"{starter}"')]
    assert ops.uninstall_entries == [(app_key, {
        "DisplayName": display_name,
        "DisplayVersion": "1.2.3",
        "Publisher": "Marathon",
        "InstallLocation": str(base),
        "DisplayIcon": str(starter),
        "UninstallString": f'"{starter}" --uninstall',
        "NoModify": 1,
        "NoRepair": 1,
    })]
    assert len(ops.imported_paths) == 1
    assert not ops.imported_paths[0].exists()
    assert ops.launches == [[str(starter)]]


def test_install_without_certificate_skips_import(tmp_path: Path) -> None:
    from marathon.client.installer import install

    source = tmp_path / "source.exe"
    source.write_bytes(b"exe")
    ops = FakeInstallerOps()

    assert install(source, Bootstrap("https://server.example", "", "1.0.0"), tmp_path / "base", ops, lambda: False) == 0
    assert ops.imported_paths == []


def test_install_certificate_import_failure_warns_but_continues(tmp_path: Path) -> None:
    from marathon.client.installer import install

    source = tmp_path / "source.exe"
    source.write_bytes(b"exe")
    ops = FakeInstallerOps(import_result=False)

    assert install(source, Bootstrap("https://server.example", "pem", "1.0.0"), tmp_path / "base", ops, lambda: False) == 0
    assert any("Stammzertifikat konnte nicht importiert werden. Browser und Worker vertrauen dem Server dann nicht." == text for _, text, _ in ops.messages)
    assert ops.launches


def test_install_declined_makes_no_changes(tmp_path: Path) -> None:
    from marathon.client.installer import install

    source = tmp_path / "source.exe"
    source.write_bytes(b"exe")
    base = tmp_path / "base"
    ops = FakeInstallerOps()
    ops.confirm_result = False

    assert install(source, Bootstrap("https://server.example", "pem", "1.0.0"), base, ops, lambda: False) == 1
    assert not base.exists()
    assert ops.run_entries == ops.uninstall_entries == ops.imported_paths == ops.launches == []


def test_install_when_client_running_stops_before_confirmation(tmp_path: Path) -> None:
    from marathon.client.installer import install

    ops = FakeInstallerOps()
    assert install(tmp_path / "missing.exe", Bootstrap("https://server.example", "", "1.0.0"), tmp_path / "base", ops, lambda: True) == 1
    assert ops.messages == [(
        "Marathon-Worker",
        "Marathon-Worker läuft bereits. Bitte im Tray-Menü „Beenden“ wählen und den Vorgang erneut starten.",
        False,
    )]
    assert ops.confirmations == []


def test_reinstall_records_old_current_as_previous_and_preserves_failed(tmp_path: Path) -> None:
    from marathon.client.installer import install

    source = tmp_path / "source.exe"
    source.write_bytes(b"exe")
    base = tmp_path / "base"
    save_version_state(base, VersionState("1.0.0", "0.9.0", True, ["0.8.0"]))
    ops = FakeInstallerOps()

    assert install(source, Bootstrap("https://server.example", "", "2.0.0"), base, ops, lambda: False) == 0
    assert load_version_state(base) == VersionState("2.0.0", "1.0.0", False, ["0.8.0"])


def test_install_copy_error_reports_failure_and_returns_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from marathon.client import installer

    source = tmp_path / "source.exe"
    source.write_bytes(b"exe")
    ops = FakeInstallerOps()
    original_copy = installer.shutil.copy2
    calls = 0

    def fail_second_copy(src: str | Path, dst: str | Path) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return original_copy(src, dst)

    monkeypatch.setattr(installer.shutil, "copy2", fail_second_copy)
    assert installer.install(source, Bootstrap("https://server.example", "", "1.0.0"), tmp_path / "base", ops, lambda: False) == 1
    assert ops.messages[-1][1] == "Installation fehlgeschlagen: disk full"
    assert ops.messages[-1][2] is True


def test_is_installed_copy_accepts_starter_or_version_only(tmp_path: Path) -> None:
    from marathon.client.installer import is_installed_copy

    base = tmp_path / "base"
    starter = base / exe_name
    versioned = version_exe(base, "1.2.3")
    external = tmp_path / "other.exe"
    assert is_installed_copy(starter, base)
    assert is_installed_copy(versioned, base)
    assert not is_installed_copy(external, base)
    assert not is_installed_copy(base / "versions" / "not-version" / exe_name, base)


def test_uninstall_removes_entries_credentials_and_schedules_directory_delete(tmp_path: Path) -> None:
    from marathon.client.installer import app_key, uninstall

    base = tmp_path / "base"
    save_credentials(base, WorkerCredentials("https://server.example", "worker-1", "secret"))
    ops = FakeInstallerOps()

    assert uninstall(base, ops, lambda: False) == 0
    assert ops.confirmations == [(
        "Marathon-Worker entfernen",
        "Marathon-Worker von diesem Rechner entfernen?",
    )]
    assert ops.run_entries == [(app_key, None)]
    assert ops.uninstall_entries == [(app_key, None)]
    assert not (base / "worker.json").exists()
    assert ops.scheduled_deletions == [base]
    assert ops.messages[-1] == ("Marathon-Worker", "Marathon-Worker wurde entfernt.", False)


def test_uninstall_declined_makes_no_changes(tmp_path: Path) -> None:
    from marathon.client.installer import uninstall

    base = tmp_path / "base"
    save_credentials(base, WorkerCredentials("https://server.example", "worker-1", "secret"))
    ops = FakeInstallerOps()
    ops.confirm_result = False

    assert uninstall(base, ops, lambda: False) == 1
    assert (base / "worker.json").exists()
    assert ops.run_entries == ops.uninstall_entries == ops.scheduled_deletions == []


def test_uninstall_when_client_running_stops_before_confirmation(tmp_path: Path) -> None:
    from marathon.client.installer import uninstall

    ops = FakeInstallerOps()
    assert uninstall(tmp_path / "base", ops, lambda: True) == 1
    assert ops.messages[0][1] == "Marathon-Worker läuft bereits. Bitte im Tray-Menü „Beenden“ wählen und den Vorgang erneut starten."
    assert ops.confirmations == []


def test_module_imports_on_linux_without_calling_windows_ops() -> None:
    module = importlib.import_module("marathon.client.installer")
    assert callable(module.windows_ops)
    if sys.platform != "win32":
        assert module.__name__ == "marathon.client.installer"


def test_windows_schedule_delete_uses_validated_environment_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import types

    from marathon.client import installer

    calls: list[tuple[list[str], dict[str, object]]] = []
    fake_subprocess = types.ModuleType("subprocess")
    fake_subprocess.CREATE_NO_WINDOW = 0x08000000
    fake_subprocess.DETACHED_PROCESS = 0x00000008
    fake_subprocess.CREATE_NEW_PROCESS_GROUP = 0x00000200
    fake_subprocess.TimeoutExpired = type("TimeoutExpired", (Exception,), {})
    fake_subprocess.list2cmdline = lambda args: '"' + args[0] + '"'
    fake_subprocess.Popen = lambda args, **kwargs: calls.append((args, kwargs))

    fake_winreg = types.ModuleType("winreg")
    fake_winreg.HKEY_CURRENT_USER = object()
    fake_winreg.KEY_SET_VALUE = 2
    fake_winreg.REG_DWORD = 4
    fake_winreg.REG_SZ = 1

    fake_ctypes = types.ModuleType("ctypes")
    fake_ctypes.windll = types.SimpleNamespace(user32=object())
    monkeypatch.setitem(sys.modules, "subprocess", fake_subprocess)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    monkeypatch.setenv("MARATHON_TEST_KEEP", "preserved")
    ops = installer.windows_ops()
    install_dir = tmp_path / "profile&%name" / "MarathonWorker"
    install_dir.mkdir(parents=True)
    ops.schedule_delete(install_dir)

    args, kwargs = calls[-1]
    assert args == [
        "cmd",
        "/d",
        "/c",
        'ping -n 3 127.0.0.1 >nul & rmdir /s /q "%MARATHON_DELETE_PATH%"',
    ]
    assert str(install_dir) not in args[-1]
    assert kwargs["env"]["MARATHON_DELETE_PATH"] == str(install_dir)
    assert kwargs["env"]["MARATHON_TEST_KEEP"] == "preserved"
    assert kwargs["close_fds"] is True
    assert kwargs["creationflags"] == (
        fake_subprocess.DETACHED_PROCESS
        | fake_subprocess.CREATE_NEW_PROCESS_GROUP
        | fake_subprocess.CREATE_NO_WINDOW
    )

    with pytest.raises(ValueError):
        ops.schedule_delete(tmp_path / "NotMarathonWorker")
    with pytest.raises(ValueError):
        ops.schedule_delete(Path("MarathonWorker"))
    assert len(calls) == 1
