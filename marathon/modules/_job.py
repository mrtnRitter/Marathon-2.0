"""Shared job contracts for executable Marathon modules."""

# IMPORTS
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any


# FUNC
class JobStopped(Exception):
    """Raised when the job's stop event is set."""


class ToolMissing(Exception):
    """Raised when a module requests an unavailable auxiliary program."""


@dataclass(frozen=True)
class ModuleJob:
    """Input passed to a module for one asset."""

    asset_path: str
    settings: Mapping[str, Any]
    stop: threading.Event
    tool_path: Callable[[str], Path] | None = None

    def tool(self, name: str) -> Path:
        if self.tool_path is None:
            raise ToolMissing(f"Zusatzprogramm fehlt: {name}")
        return self.tool_path(name)


@dataclass(frozen=True)
class SpawnedAsset:
    """A new asset created by a module and routed to an output."""

    path: str
    output: str
    log: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleResult:
    """Result of running a module for one asset.

    output None = asset ends here as done without routing; asset_path None = file path unchanged
    """

    output: str | None
    log: tuple[str, ...] = ()
    asset_path: str | None = None
    spawned: tuple[SpawnedAsset, ...] = ()


def check_stop(stop: threading.Event) -> None:
    """Raise JobStopped when the stop event is set."""
    if stop.is_set():
        raise JobStopped
