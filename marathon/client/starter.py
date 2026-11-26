# IMPORTS
import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .local_state import VersionState, load_version_state, save_version_state, version_exe


# CONFIG
exit_restart = 10
main_log = logging.getLogger(__name__)


# FUNC
def _roll_back(base: Path, state: VersionState) -> None:
    failed_version = state.current
    previous_version = state.previous
    if failed_version not in state.failed:
        state.failed.append(failed_version)
    state.current = previous_version
    state.previous = None
    state.pending = False
    save_version_state(base, state)
    main_log.error("Update auf %s fehlgeschlagen, zurück auf %s", failed_version, previous_version)


def run_starter(
    base: Path,
    spawn: Callable[[list[str]], Any],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    healthy_after_s: float = 60.0,
    backoff_s: Sequence[float] = (1, 2, 5, 10, 30),
) -> int:
    """Start client versions, restart them, and roll back failed updates."""
    backoff_index = 0
    while True:
        state = load_version_state(base)
        if state is None:
            main_log.error("Worker-Client ist nicht installiert")
            return 2

        executable = version_exe(base, state.current)
        if not executable.is_file():
            if state.previous is None or not version_exe(base, state.previous).is_file():
                main_log.error("Worker-Client ist nicht installiert")
                return 2
            _roll_back(base, state)
            backoff_index = 0
            continue

        started_at = monotonic()
        try:
            process = spawn([str(executable), "--run"])
            exit_code = process.wait()
        except OSError:
            exit_code = 1
        runtime_s = max(0.0, monotonic() - started_at)

        if exit_code == 0:
            return 0
        if exit_code == exit_restart:
            backoff_index = 0
            continue
        if state.pending and runtime_s < healthy_after_s and state.previous is not None:
            _roll_back(base, state)
            backoff_index = 0
            continue
        if runtime_s >= healthy_after_s:
            backoff_index = 0
        delay = backoff_s[min(backoff_index, len(backoff_s) - 1)]
        sleep(delay)
        backoff_index = min(backoff_index + 1, len(backoff_s) - 1)
