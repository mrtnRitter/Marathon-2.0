# IMPORTS
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Protocol

from marathon.client.local_state import (
    Bootstrap,
    VersionState,
    delete_credentials,
    exe_name,
    load_version_state,
    save_version_state,
    version_exe,
)


# CONFIG
app_key = "MarathonWorker"
display_name = "Marathon-Worker"
_running_message = "Marathon-Worker läuft bereits. Bitte im Tray-Menü „Beenden“ wählen und den Vorgang erneut starten."
_certificate_warning = "Stammzertifikat konnte nicht importiert werden. Browser und Worker vertrauen dem Server dann nicht."


# FUNC
class InstallerOps(Protocol):
    """Platform operations used by the installer and uninstall flow."""

    def confirm(self, title: str, text: str) -> bool: ...

    def message(self, title: str, text: str, error: bool = False) -> None: ...

    def set_run_entry(self, name: str, command: str | None) -> None: ...

    def set_uninstall_entry(self, key: str, values: Mapping[str, str | int] | None) -> None: ...

    def import_root_cert(self, pem_path: Path) -> bool: ...

    def launch(self, args: list[str]) -> None: ...

    def schedule_delete(self, path: Path) -> None: ...


def is_installed_copy(exe_path: Path, base: Path) -> bool:
    """Return whether ``exe_path`` resolves to the installed starter or a versioned client."""
    resolved_exe = exe_path.resolve()
    resolved_base = base.resolve()
    if resolved_exe == (resolved_base / exe_name).resolve():
        return True
    try:
        relative_path = resolved_exe.relative_to((resolved_base / "versions").resolve())
    except ValueError:
        return False
    if len(relative_path.parts) != 2 or relative_path.name != exe_name:
        return False
    try:
        return version_exe(resolved_base, relative_path.parts[0]).resolve() == resolved_exe
    except ValueError:
        return False


def install(
    exe_path: Path,
    bootstrap: Bootstrap,
    base: Path,
    ops: InstallerOps,
    is_running: Callable[[], bool],
) -> int:
    """Install the client for the current user and start its installed copy."""
    if is_running():
        ops.message(display_name, _running_message)
        return 1
    if not ops.confirm(
        "Marathon-Worker installieren",
        "Marathon-Worker auf diesem Rechner installieren? Der Worker startet danach automatisch mit Windows.",
    ):
        return 1

    starter = base / exe_name
    try:
        versioned_exe = version_exe(base, bootstrap.client_version)
        base.mkdir(parents=True, exist_ok=True)
        versioned_exe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe_path, starter)
        shutil.copy2(exe_path, versioned_exe)

        old_state = load_version_state(base)
        previous = old_state.current if old_state and old_state.current != bootstrap.client_version else (
            old_state.previous if old_state else None
        )
        failed = old_state.failed if old_state else []
        save_version_state(base, VersionState(bootstrap.client_version, previous, False, failed))

        if bootstrap.root_ca_pem:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".crt", dir=base, delete=False
            ) as certificate_file:
                certificate_file.write(bootstrap.root_ca_pem)
                certificate_path = Path(certificate_file.name)
            try:
                imported = ops.import_root_cert(certificate_path)
            finally:
                certificate_path.unlink(missing_ok=True)
            if not imported:
                ops.message(display_name, _certificate_warning)

        ops.set_run_entry(app_key, f'"{starter}"')
        ops.set_uninstall_entry(app_key, {
            "DisplayName": display_name,
            "DisplayVersion": bootstrap.client_version,
            "Publisher": "Marathon",
            "InstallLocation": str(base),
            "DisplayIcon": str(starter),
            "UninstallString": f'"{starter}" --uninstall',
            "NoModify": 1,
            "NoRepair": 1,
        })
        ops.launch([str(starter)])
    except OSError as error:
        ops.message(display_name, f"Installation fehlgeschlagen: {error}", error=True)
        return 1
    return 0


def uninstall(base: Path, ops: InstallerOps, is_running: Callable[[], bool]) -> int:
    """Remove the current user's client registration and schedule its files for deletion."""
    if is_running():
        ops.message(display_name, _running_message)
        return 1
    if not ops.confirm("Marathon-Worker entfernen", "Marathon-Worker von diesem Rechner entfernen?"):
        return 1

    ops.set_run_entry(app_key, None)
    ops.set_uninstall_entry(app_key, None)
    delete_credentials(base)
    ops.schedule_delete(base)
    ops.message(display_name, "Marathon-Worker wurde entfernt.")
    return 0


def windows_ops() -> InstallerOps:
    """Build the Windows registry, dialog, certificate, process, and cleanup operations."""
    import subprocess
    import winreg
    import ctypes

    run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    uninstall_key_prefix = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"

    class WindowsInstallerOps:
        def confirm(self, title: str, text: str) -> bool:
            result = ctypes.windll.user32.MessageBoxW(None, text, title, 0x00000004 | 0x00000020)
            return result == 6

        def message(self, title: str, text: str, error: bool = False) -> None:
            icon = 0x00000010 if error else 0x00000040
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x00000000 | icon)

        def set_run_entry(self, name: str, command: str | None) -> None:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_SET_VALUE) as key:
                if command is None:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)

        def set_uninstall_entry(self, key: str, values: Mapping[str, str | int] | None) -> None:
            if values is None:
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{uninstall_key_prefix}\{key}")
                except FileNotFoundError:
                    pass
                return
            key_path = rf"{uninstall_key_prefix}\{key}"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as registry_key:
                for name, value in values.items():
                    value_type = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                    winreg.SetValueEx(registry_key, name, 0, value_type, value)

        def import_root_cert(self, pem_path: Path) -> bool:
            try:
                result = subprocess.run(
                    ["certutil", "-user", "-addstore", "Root", str(pem_path)],
                    timeout=120,
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired):
                return False
            return result.returncode == 0

        def launch(self, args: list[str]) -> None:
            subprocess.Popen(
                args,
                close_fds=True,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )

        def schedule_delete(self, path: Path) -> None:
            if not path.is_absolute() or path.name != app_key:
                raise ValueError("Löschpfad muss absolut sein und auf einen MarathonWorker-Ordner zeigen")
            command = 'ping -n 3 127.0.0.1 >nul & rmdir /s /q "%MARATHON_DELETE_PATH%"'
            environment = os.environ.copy()
            environment["MARATHON_DELETE_PATH"] = str(path)
            subprocess.Popen(
                ["cmd", "/d", "/c", command],
                env=environment,
                close_fds=True,
                creationflags=(
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                ),
            )

    return WindowsInstallerOps()
