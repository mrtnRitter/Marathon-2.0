"""Shared job contracts for executable Marathon modules."""

# IMPORTS
from collections.abc import Mapping
from dataclasses import dataclass
import threading
from typing import Any


# FUNC
class JobStopped(Exception):
    """Raised when the job's stop event is set."""


@dataclass(frozen=True)
class ModuleJob:
    """Input passed to a module for one asset."""

    asset_path: str
    settings: Mapping[str, Any]
    stop: threading.Event


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
