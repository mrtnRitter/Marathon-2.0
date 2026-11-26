# IMPORTS
import hashlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .local_state import VersionState, load_version_state, save_version_state, version_exe


# CONFIG
block_reason = "Update wird installiert"
main_log = logging.getLogger(__name__)
_version_pattern = re.compile(r"^\d+\.\d+\.\d+$")
_sha256_pattern = re.compile(r"^[0-9a-fA-F]{64}$")


# FUNC
class UpdateManager:
    """Download, verify, and install offered client versions in the background."""

    def __init__(
        self,
        base: Path,
        current_version: str,
        download: Callable[[str, Path, threading.Event], None],
        is_idle: Callable[[], bool],
        request_restart: Callable[[], None],
        monotonic: Callable[[], float] = time.monotonic,
        retry_after_s: float = 600.0,
        idle_poll_s: float = 1.0,
    ) -> None:
        self._base = base
        self._current_version = current_version
        self._download = download
        self._is_idle = is_idle
        self._request_restart = request_restart
        self._monotonic = monotonic
        self._retry_after_s = retry_after_s
        self._idle_poll_s = idle_poll_s
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._part_path: Path | None = None
        self._blocking = False
        self._stopped = False
        self._failed_at: dict[str, float] = {}

    def offer(self, offer: Mapping[str, Any]) -> None:
        """Accept a valid update offer and begin processing it asynchronously."""
        if not isinstance(offer, Mapping):
            return
        version = offer.get("version")
        url = offer.get("url")
        digest = offer.get("sha256")
        if (not isinstance(version, str) or not _version_pattern.fullmatch(version)
                or version == self._current_version
                or not isinstance(url, str) or not url.startswith("/api/v1/")
                or not isinstance(digest, str) or not _sha256_pattern.fullmatch(digest)):
            return
        try:
            state = load_version_state(self._base)
        except ValueError:
            main_log.exception("Update-Angebot wegen beschädigtem Versionsstatus ignoriert")
            return
        if state is not None and version in state.failed:
            return

        with self._lock:
            if self._stopped or (self._thread is not None and self._thread.is_alive()):
                return
            failed_at = self._failed_at.get(version)
            if failed_at is not None and self._monotonic() - failed_at < self._retry_after_s:
                return
            self._stop.clear()
            worker = threading.Thread(
                target=self._install,
                args=(version, url, digest.lower(), state),
                daemon=True,
                name=f"client-update-{version}",
            )
            self._thread = worker
            worker.start()

    def _install(self, version: str, url: str, expected_digest: str, state: VersionState | None) -> None:
        executable = version_exe(self._base, version)
        part_path = executable.with_name(f"{executable.name}.part")
        with self._lock:
            self._part_path = part_path
        try:
            if self._stop.is_set():
                return
            executable.parent.mkdir(parents=True, exist_ok=True)
            part_path.unlink(missing_ok=True)
            self._download(url, part_path, self._stop)
            if self._stop.is_set():
                return
            actual_digest = self._file_sha256(part_path)
            if actual_digest != expected_digest:
                raise ValueError("SHA-256-Prüfsumme stimmt nicht überein")
            if self._stop.is_set():
                return
            os.replace(part_path, executable)
            with self._lock:
                if self._stop.is_set():
                    return
                self._blocking = True

            while not self._stop.is_set():
                if self._is_idle():
                    with self._lock:
                        if self._stop.is_set():
                            return
                        failed = list(state.failed) if state is not None else []
                        save_version_state(
                            self._base,
                            VersionState(version, self._current_version, True, failed),
                        )
                    try:
                        self._request_restart()
                    except Exception:
                        main_log.exception("Neustart des Worker-Clients konnte nicht angefordert werden")
                    return
                if self._stop.wait(self._idle_poll_s):
                    return
        except Exception:
            if not self._stop.is_set():
                with self._lock:
                    self._failed_at[version] = self._monotonic()
                main_log.exception("Update auf %s fehlgeschlagen", version)
        finally:
            part_path.unlink(missing_ok=True)
            with self._lock:
                self._part_path = None
                self._blocking = False

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def blocks_jobs(self) -> bool:
        """Return whether jobs must wait for the update restart."""
        with self._lock:
            return self._blocking

    def confirm_healthy(self) -> None:
        """Clear the pending marker after this version has become healthy."""
        state = load_version_state(self._base)
        if state is not None and state.pending and state.current == self._current_version:
            state.pending = False
            save_version_state(self._base, state)

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop update work and wait at most ``timeout_s`` for its thread."""
        with self._lock:
            self._stopped = True
            self._stop.set()
            worker = self._thread
            part_path = self._part_path
            self._blocking = False
        if worker is not None and worker is not threading.current_thread():
            worker.join(max(0.0, timeout_s))
        if part_path is not None:
            part_path.unlink(missing_ok=True)
