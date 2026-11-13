"""List watchfolder files and plan changes to the known-file state."""

# IMPORTS
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import os
import stat as stat_module
import threading
import time

from marathon.modules.watchfolder.rules import WatchSettings, matches_extensions

# CONFIG
_active_listings: set[tuple[str, bool]] = set()
_active_listings_lock = threading.Lock()

# FUNC
@dataclass(frozen=True)
class FileStat:
    """File size and modification time from one listing."""

    size: int
    mtime_ns: int


@dataclass(frozen=True)
class KnownFile:
    """Persisted observation of a watchfolder file."""

    size: int
    mtime_ns: int
    stable_since: datetime
    asset_id: str | None
    missing_since: datetime | None


@dataclass(frozen=True)
class ScanPlan:
    """Database changes and files ready for ingestion."""

    upserts: dict[str, KnownFile]
    deletes: tuple[str, ...]
    ready: tuple[str, ...]


class FolderUnavailable(Exception):
    """Raised when a watchfolder cannot be read or does not respond."""


class ScanStopped(Exception):
    """Raised when a caller stops waiting for a folder listing."""


def _folder_error(path: str, error: OSError, is_root: bool) -> FolderUnavailable:
    if is_root and isinstance(error, (FileNotFoundError, NotADirectoryError)):
        return FolderUnavailable(f"Ordner nicht gefunden: {path}")
    if is_root and isinstance(error, PermissionError):
        return FolderUnavailable(f"Kein Zugriff auf {path}")
    return FolderUnavailable(f"Ordner nicht lesbar: {path} ({error})")


def _list_folder_sync(settings: WatchSettings) -> dict[str, FileStat]:
    listing: dict[str, FileStat] = {}

    def visit(directory: str, is_root: bool = False) -> None:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if is_directory:
                        if settings.recursive:
                            visit(os.path.join(directory, entry.name))
                        continue
                    if not matches_extensions(entry.name, settings.include, settings.exclude):
                        continue
                    try:
                        file_stat = entry.stat()
                    except FileNotFoundError:
                        continue
                    if stat_module.S_ISREG(file_stat.st_mode):
                        listing[os.path.join(directory, entry.name)] = FileStat(file_stat.st_size, file_stat.st_mtime_ns)
        except FolderUnavailable:
            raise
        except OSError as error:
            raise _folder_error(settings.path, error, is_root) from error

    visit(settings.path, is_root=True)
    return listing


def list_folder(settings: WatchSettings, timeout_s: float, stop: threading.Event | None = None) -> dict[str, FileStat]:
    """List matching files, stopping promptly on cancellation or timeout."""
    path = settings.path
    key = (path, settings.recursive)
    if stop is not None and stop.is_set():
        raise ScanStopped()
    with _active_listings_lock:
        if stop is not None and stop.is_set():
            raise ScanStopped()
        if key in _active_listings:
            raise FolderUnavailable(f"Ordner antwortet nicht: {path} (vorherige Prüfung läuft noch)")
        _active_listings.add(key)

    completed = threading.Event()
    result: list[dict[str, FileStat] | BaseException] = []

    def run_listing() -> None:
        try:
            result.append(_list_folder_sync(settings))
        except BaseException as error:
            result.append(error)
        finally:
            with _active_listings_lock:
                _active_listings.discard(key)
            completed.set()

    worker = threading.Thread(target=run_listing, name="watchfolder-listing", daemon=True)
    try:
        worker.start()
    except BaseException as error:
        with _active_listings_lock:
            _active_listings.discard(key)
        raise FolderUnavailable(f"Ordner nicht lesbar: {path} ({error})") from error

    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if stop is not None and stop.is_set():
            raise ScanStopped()
        if completed.is_set():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FolderUnavailable(f"Ordner antwortet nicht: {path}")
        completed.wait(min(0.2, remaining))

    outcome = result[0]
    if isinstance(outcome, BaseException):
        if isinstance(outcome, FolderUnavailable):
            raise outcome
        raise FolderUnavailable(f"Ordner nicht lesbar: {path} ({outcome})") from outcome
    return outcome


def plan_scan(
    known: Mapping[str, KnownFile],
    listing: Mapping[str, FileStat],
    now: datetime,
    wait_s: float,
) -> ScanPlan:
    """Return the upserts, deletions, and newly stable files for a scan."""
    upserts: dict[str, KnownFile] = {}
    deletes = []
    ready = []

    for path, stat in listing.items():
        previous = known.get(path)
        if previous is None:
            upserts[path] = KnownFile(stat.size, stat.mtime_ns, now, None, None)
        elif previous.asset_id is None:
            if (previous.size, previous.mtime_ns) != (stat.size, stat.mtime_ns):
                upserts[path] = KnownFile(stat.size, stat.mtime_ns, now, None, None)
            elif (now - previous.stable_since).total_seconds() >= wait_s:
                ready.append(path)
        elif (previous.size, previous.mtime_ns) == (stat.size, stat.mtime_ns) and previous.missing_since is not None:
            upserts[path] = KnownFile(
                previous.size,
                previous.mtime_ns,
                previous.stable_since,
                previous.asset_id,
                None,
            )

    for path, previous in known.items():
        if path in listing:
            continue
        if previous.asset_id is None:
            deletes.append(path)
        elif previous.missing_since is None:
            upserts[path] = KnownFile(
                previous.size,
                previous.mtime_ns,
                previous.stable_since,
                previous.asset_id,
                now,
            )

    return ScanPlan(upserts, tuple(sorted(deletes)), tuple(sorted(ready)))
