"""Manage the server worker process and its restart lifecycle."""

# IMPORTS
from collections.abc import Mapping, Sequence
import logging
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock, Thread, current_thread
import time

from marathon.server.config import ServerWorkerConfig


# STATIC
_logger = logging.getLogger("marathon.supervisor")


# FUNC
def server_url(host: str, port: int) -> str:
    """Return the worker-facing URL for the server bind address."""
    if host in {"", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def server_worker_command(url: str, config: ServerWorkerConfig) -> list[str]:
    """Build the command that starts the configured server worker."""
    command = [
        sys.executable,
        "-m",
        "marathon.worker",
        "--server",
        url,
        "--worker-id",
        "server-worker",
        "--name",
        "Server-Worker",
        "--max-parallel",
        str(config.max_parallel),
        "--exit-on-stdin-eof",
    ]
    return command


class WorkerSupervisor:
    """Run one worker subprocess with bounded restart and shutdown waits."""

    def __init__(
        self,
        command: list[str],
        env: Mapping[str, str],
        cwd: Path,
        log_file: Path,
        backoff_s: Sequence[float] = (1, 2, 5, 10, 30),
        stable_s: float = 60.0,
        graceful_timeout_s: float = 10.0,
        kill_timeout_s: float = 5.0,
    ) -> None:
        self.command = list(command)
        self.env = dict(env)
        self.cwd = Path(cwd)
        self.log_file = Path(log_file)
        self.backoff_s = tuple(backoff_s)
        self.stable_s = stable_s
        self.graceful_timeout_s = graceful_timeout_s
        self.kill_timeout_s = kill_timeout_s
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._process: subprocess.Popen | None = None
        self._started = False

    def start(self) -> None:
        """Start the single daemon supervisor thread, if not already started."""
        with self._lock:
            if self._started or self._stop_event.is_set():
                return
            self._started = True
            self._thread = Thread(target=self._run, name="marathon-supervisor", daemon=True)
            self._thread.start()

    def stop(self) -> bool:
        """Stop the current child and supervisor within the configured deadlines."""
        self._stop_event.set()
        with self._lock:
            process = self._process
            thread = self._thread
        if process is None:
            if thread is not None and thread is not current_thread():
                thread.join(min(0.5, self.graceful_timeout_s + self.kill_timeout_s + 1.5))
            return True

        if process.stdin is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

        if process.poll() is None and not self._wait_for_process(process, self.graceful_timeout_s):
            try:
                process.terminate()
            except OSError:
                pass
            if not self._wait_for_process(process, self.kill_timeout_s):
                try:
                    process.kill()
                except OSError:
                    pass
                self._wait_for_process(process, 1.0)

        if thread is not None and thread is not current_thread():
            thread.join(0.5)
        return process.poll() is not None

    @staticmethod
    def _wait_for_process(process: subprocess.Popen, timeout_s: float) -> bool:
        try:
            process.wait(timeout=max(0.0, timeout_s))
            return True
        except subprocess.TimeoutExpired:
            return False

    def _wait_or_stop(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self._stop_event.wait(min(0.5, remaining)):
                return True
        return True

    def _run_process(self) -> tuple[int | None, float]:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._stop_event.is_set():
                return None, time.monotonic()
            log_handle = self.log_file.open("a", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    self.command,
                    stdin=subprocess.PIPE,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    cwd=self.cwd,
                    env=self.env,
                    **self._process_options(),
                )
            except OSError:
                log_handle.close()
                raise
            self._process = process

        started_at = time.monotonic()
        try:
            while True:
                try:
                    return process.wait(timeout=0.5), started_at
                except subprocess.TimeoutExpired:
                    continue
        finally:
            log_handle.close()
            with self._lock:
                if self._process is process:
                    self._process = None

    @staticmethod
    def _process_options() -> dict[str, int | bool]:
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    def _run(self) -> None:
        backoff_index = 0
        while not self._stop_event.is_set():
            started_at = time.monotonic()
            try:
                return_code, started_at = self._run_process()
            except OSError as error:
                return_code = None
                _logger.warning("Server worker failed to start: %s", error)
            if self._stop_event.is_set():
                return
            if return_code is not None:
                if time.monotonic() - started_at >= self.stable_s:
                    backoff_index = 0
                _logger.warning("Server worker exited with code %s; restarting", return_code)
            delay = self.backoff_s[min(backoff_index, len(self.backoff_s) - 1)] if self.backoff_s else 0.0
            backoff_index += 1
            if self._wait_or_stop(delay):
                return
