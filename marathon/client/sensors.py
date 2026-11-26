# IMPORTS
import ctypes
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from marathon.client.availability import Sample


# CONFIG


# FUNC
def windows_idle_seconds() -> float | None:
    """Return seconds since the last Windows input event, or None if unavailable."""
    if os.name != "nt":
        return None

    class LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    try:
        last_input = LastInputInfo()
        last_input.cbSize = ctypes.sizeof(last_input)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):
            return None
        tick_count = ctypes.windll.kernel32.GetTickCount()
        elapsed_ms = (int(tick_count) - int(last_input.dwTime)) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except Exception:
        return None


class SystemSensors:
    """Read local CPU, memory, process, idle-time, and optional GPU measurements."""

    def __init__(
        self,
        ps: Any | None = None,
        run: Callable[..., Any] = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
        gpu_command: Sequence[str] = (
            "nvidia-smi",
            "--query-gpu=utilization.gpu",
            "--format=csv,noheader,nounits",
        ),
        gpu_timeout_s: float = 2.0,
        gpu_retry_s: float = 600.0,
        idle_reader: Callable[[], float | None] | None = None,
    ) -> None:
        """Create sensors with injectable local readers for deterministic testing."""
        if ps is None:
            import psutil

            ps = psutil
        self._ps = ps
        self._run = run
        self._monotonic = monotonic
        self._gpu_command = tuple(gpu_command)
        self._gpu_timeout_s = gpu_timeout_s
        self._gpu_retry_s = gpu_retry_s
        self._idle_reader = idle_reader if idle_reader is not None else windows_idle_seconds
        self._gpu_retry_at: float | None = None
        self._process_errors = tuple(
            error
            for error in (getattr(ps, "NoSuchProcess", None), getattr(ps, "AccessDenied", None))
            if isinstance(error, type) and issubclass(error, BaseException)
        )

    def _gpu_percent(self) -> float | None:
        current_time = self._monotonic()
        if self._gpu_retry_at is not None and current_time < self._gpu_retry_at:
            return None

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            result = self._run(
                self._gpu_command,
                capture_output=True,
                text=True,
                timeout=self._gpu_timeout_s,
                creationflags=creationflags,
            )
            if result.returncode != 0 or not isinstance(result.stdout, str):
                raise ValueError("GPU command returned no readable output")
            values = [float(line.strip()) for line in result.stdout.splitlines() if line.strip()]
            if not values:
                raise ValueError("GPU command returned no measurements")
            self._gpu_retry_at = None
            return max(values)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
            self._gpu_retry_at = self._monotonic() + self._gpu_retry_s
            return None

    def _process_names(self) -> tuple[str, ...]:
        names: list[str] = []
        try:
            processes = iter(self._ps.process_iter(["name"]))
        except self._process_errors:
            return ()

        while True:
            try:
                process = next(processes)
            except StopIteration:
                break
            except self._process_errors:
                continue
            try:
                info = process.info
                name = info.get("name") if isinstance(info, Mapping) else None
            except self._process_errors:
                continue
            if isinstance(name, str) and name:
                names.append(name)
        return tuple(names)

    def sample(self, now: datetime) -> Sample:
        """Read one availability sample without waiting for an unbounded GPU call."""
        return Sample(
            now=now,
            idle_seconds=self._idle_reader(),
            cpu_percent=self._ps.cpu_percent(interval=None),
            ram_percent=self._ps.virtual_memory().percent,
            gpu_percent=self._gpu_percent(),
            processes=self._process_names(),
        )
