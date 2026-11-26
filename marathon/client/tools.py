# IMPORTS
import hashlib
import ntpath
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


# CONFIG
_name_pattern = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_version_pattern = re.compile(r"^[0-9a-f]{16}$")
_sha256_pattern = re.compile(r"^[0-9a-f]{64}$")


# FUNC
class ToolError(RuntimeError):
    """Raised when a local supplementary tool is unavailable or invalid."""


@dataclass(frozen=True)
class SyncResult:
    """Outcome of synchronizing supplementary tools."""

    updated: list[str]
    failed: dict[str, str]


class ToolStore:
    """Store verified supplementary tools beneath a client directory."""

    def __init__(
        self,
        base: Path,
        fetch_manifest: Callable[[], list[dict[str, Any]]],
        download: Callable[[str, str, Path, threading.Event], None],
    ) -> None:
        """Create a store using injected manifest and download functions."""
        self.base = Path(base)
        self.fetch_manifest = fetch_manifest
        self.download = download
        self._lock = threading.Lock()

    @staticmethod
    def _validate_entry(entry: Any) -> tuple[str, str, list[tuple[str, int, str]]]:
        if not isinstance(entry, dict):
            raise ToolError("Ungültiger Manifest-Eintrag")
        name = entry.get("name")
        if not isinstance(name, str) or not _name_pattern.fullmatch(name):
            raise ToolError(f"Ungültiger Zusatzprogrammname: {name}")
        version = entry.get("version")
        if not isinstance(version, str) or not _version_pattern.fullmatch(version):
            raise ToolError(f"Ungültige Version für Zusatzprogramm {name}")
        raw_files = entry.get("files")
        if not isinstance(raw_files, list):
            raise ToolError(f"Ungültige Dateiliste für Zusatzprogramm {name}")

        files = []
        for item in raw_files:
            if not isinstance(item, dict):
                raise ToolError(f"Ungültige Datei für Zusatzprogramm {name}")
            path = item.get("path")
            if (
                not isinstance(path, str)
                or not path
                or "\\" in path
                or path.startswith("/")
                or bool(ntpath.splitdrive(path)[0])
                or any(part in {"", ".."} for part in path.split("/"))
            ):
                raise ToolError(f"Ungültiger Pfad für Zusatzprogramm {name}: {path}")
            size = item.get("size")
            digest = item.get("sha256")
            if type(size) is not int or size < 0 or not isinstance(digest, str) or not _sha256_pattern.fullmatch(digest):
                raise ToolError(f"Ungültige Dateiprüfung für Zusatzprogramm {name}: {path}")
            files.append((path, size, digest))
        return name, version, files

    @staticmethod
    def _safe_target(root: Path, relative_path: str) -> Path:
        if root.is_symlink() or root.parent.is_symlink() or root.parent.parent.is_symlink():
            raise ToolError(f"Unsicherer Dateipfad: {relative_path}")
        target = root.joinpath(*relative_path.split("/"))
        current = root
        for part in relative_path.split("/")[:-1]:
            current = current / part
            if current.is_symlink():
                raise ToolError(f"Unsicherer Dateipfad: {relative_path}")
        try:
            target.resolve(strict=False).relative_to(root.resolve(strict=False))
        except (OSError, RuntimeError, ValueError) as error:
            raise ToolError(f"Unsicherer Dateipfad: {relative_path}") from error
        return target

    @staticmethod
    def _has_expected_files(root: Path, files: list[tuple[str, int, str]]) -> bool:
        if root.is_symlink() or not root.is_dir():
            return False
        for relative_path, size, expected_hash in files:
            path = ToolStore._safe_target(root, relative_path)
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
                    return False
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != expected_hash:
                    return False
            except OSError:
                return False
        return True

    @staticmethod
    def _remove_path(path: Path) -> None:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def _sync_entry(self, entry: Any, stop: threading.Event) -> tuple[str, bool]:
        name, version, files = self._validate_entry(entry)
        tools_root = self.base / "tools"
        tool_dir = tools_root / name
        current_file = tool_dir / "current.txt"
        if tools_root.is_symlink() or tool_dir.is_symlink():
            raise ToolError(f"Unsicherer Ablagepfad für Zusatzprogramm {name}")
        with self._lock:
            try:
                current_version = current_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                current_version = ""
            if current_version == version and self._has_expected_files(tool_dir / version, files):
                return name, False

        version_dir = tool_dir / version
        if version_dir.is_symlink():
            raise ToolError(f"Unsicherer Ablagepfad für Zusatzprogramm {name}")
        for relative_path, size, expected_hash in files:
            if stop.is_set():
                return name, False
            target = self._safe_target(version_dir, relative_path)
            part_path = target.with_name(target.name + ".part")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.parent.is_symlink() or part_path.is_symlink():
                    raise ToolError(f"Unsicherer Dateipfad: {relative_path}")
                part_path.unlink(missing_ok=True)
                self.download(name, relative_path, part_path, stop)
                if stop.is_set():
                    part_path.unlink(missing_ok=True)
                    return name, False
                digest = hashlib.sha256()
                actual_size = 0
                with part_path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        actual_size += len(block)
                        digest.update(block)
                if actual_size != size or digest.hexdigest() != expected_hash:
                    part_path.unlink(missing_ok=True)
                    raise ToolError(f"Zusatzprogramm {name}: Prüfsumme stimmt nicht ({relative_path})")
                os.replace(part_path, target)
            except (KeyboardInterrupt, SystemExit):
                try:
                    part_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            except Exception:
                try:
                    part_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

        if stop.is_set():
            return name, False
        with self._lock:
            if stop.is_set():
                return name, False
            tool_dir.mkdir(parents=True, exist_ok=True)
            temporary_file = tool_dir / "current.txt.tmp"
            try:
                if temporary_file.is_symlink():
                    raise ToolError(f"Unsicherer Ablagepfad für Zusatzprogramm {name}")
                temporary_file.write_text(version, encoding="utf-8", newline="\n")
                os.replace(temporary_file, current_file)
            finally:
                try:
                    temporary_file.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                for old_version in tool_dir.iterdir():
                    if old_version.name not in {version, "current.txt", "current.txt.tmp"}:
                        self._remove_path(old_version)
            except OSError:
                pass
        return name, True

    def sync(self, stop: threading.Event) -> SyncResult:
        """Download, verify, and activate all tools in the current manifest."""
        try:
            manifest = self.fetch_manifest()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            return SyncResult([], {"*": f"Liste der Zusatzprogramme nicht abrufbar: {error}"})
        if not isinstance(manifest, list):
            return SyncResult([], {"*": "Ungültige Liste der Zusatzprogramme"})

        updated: list[str] = []
        failed: dict[str, str] = {}
        active_names: set[str] = set()
        for entry in manifest:
            if stop.is_set():
                return SyncResult(updated, failed)
            name = entry.get("name") if isinstance(entry, dict) else None
            failure_key = name if isinstance(name, str) and name else "*"
            if isinstance(name, str) and _name_pattern.fullmatch(name):
                active_names.add(name)
            try:
                synced_name, changed = self._sync_entry(entry, stop)
                if changed:
                    updated.append(synced_name)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as error:
                failed[failure_key] = str(error) or error.__class__.__name__

        if stop.is_set():
            return SyncResult(updated, failed)
        tools_root = self.base / "tools"
        try:
            if tools_root.is_dir() and not tools_root.is_symlink():
                for tool_dir in tools_root.iterdir():
                    if _name_pattern.fullmatch(tool_dir.name) and tool_dir.name not in active_names:
                        self._remove_path(tool_dir)
        except OSError:
            pass
        return SyncResult(updated, failed)

    def tool_path(self, name: str) -> Path:
        """Return the active version directory for ``name``."""
        missing = ToolError(f"Zusatzprogramm fehlt: {name}")
        if not isinstance(name, str) or not _name_pattern.fullmatch(name):
            raise missing
        with self._lock:
            tools_root = self.base / "tools"
            tool_dir = tools_root / name
            if tools_root.is_symlink() or tool_dir.is_symlink():
                raise missing
            try:
                version = (tool_dir / "current.txt").read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                raise missing
            if not _version_pattern.fullmatch(version):
                raise missing
            version_dir = tool_dir / version
            if version_dir.is_dir() and not version_dir.is_symlink():
                return version_dir
        raise missing
