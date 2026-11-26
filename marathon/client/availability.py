# IMPORTS
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from marathon.common.availability import (
    blocked_process,
    normalize_availability,
    schedule_allows,
)


# CONFIG
@dataclass(frozen=True)
class Sample:
    """One local measurement used to evaluate client availability."""

    now: datetime
    idle_seconds: float | None
    cpu_percent: float
    ram_percent: float
    gpu_percent: float | None
    processes: tuple[str, ...]


@dataclass(frozen=True)
class AvailabilityState:
    """The availability decision and its user-facing explanation."""

    available: bool
    reason: str


class AvailabilityEvaluator:
    """Evaluate availability from supplied measurements without reading sensors or clocks."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Create an evaluator using normalized settings or unrestricted defaults."""
        self._config = normalize_availability(config if config is not None else {})
        self._history: list[tuple[float, float, float, float | None]] = []
        self._activated = False
        self._last_idle_seconds: float | None = None

    @property
    def activated(self) -> bool:
        """Whether low-load checks are currently bypassed for an assigned job."""
        return self._activated

    def set_config(self, config: Mapping[str, Any]) -> None:
        """Replace normalized settings, reset activation, and retain load history."""
        self._config = normalize_availability(config)
        self._activated = False

    def evaluate(self, sample: Sample, monotonic_s: float) -> AvailabilityState:
        """Evaluate a measurement and update the rolling load and idle history."""
        idle = self._config["idle"]
        window_s = idle["minutes"] * 60
        self._history.append(
            (monotonic_s, sample.cpu_percent, sample.ram_percent, sample.gpu_percent)
        )
        cutoff = monotonic_s - window_s
        first_after_cutoff = next(
            (index for index, point in enumerate(self._history) if point[0] > cutoff),
            len(self._history),
        )
        self._history = self._history[max(first_after_cutoff - 1, 0) :]

        previous_idle = self._last_idle_seconds
        self._last_idle_seconds = sample.idle_seconds
        user_input = (
            sample.idle_seconds is not None
            and previous_idle is not None
            and sample.idle_seconds < previous_idle
        )

        if not schedule_allows(self._config, sample.now):
            self._activated = False
            return AvailabilityState(False, "Außerhalb des Zeitplans")

        process_name = blocked_process(self._config, sample.processes)
        if process_name is not None:
            self._activated = False
            return AvailabilityState(False, f"Programm {process_name} aktiv")

        if not idle["enabled"]:
            return AvailabilityState(True, "")

        if self._activated:
            if user_input:
                self._activated = False
                return AvailabilityState(False, "Rechner wird benutzt")
            return AvailabilityState(True, "")

        if sample.idle_seconds is None:
            return AvailabilityState(False, "Leerlauf nicht messbar")
        if sample.idle_seconds < window_s:
            return AvailabilityState(False, "Rechner wird benutzt")
        if not self._history or monotonic_s - self._history[0][0] < window_s:
            return AvailabilityState(False, "Warte auf niedrige Last")

        cpu_average = sum(point[1] for point in self._history) / len(self._history)
        if cpu_average >= idle["max_cpu_percent"]:
            return AvailabilityState(False, f"CPU-Last zu hoch ({cpu_average:.0f} %)")

        ram_average = sum(point[2] for point in self._history) / len(self._history)
        if ram_average >= idle["max_ram_percent"]:
            return AvailabilityState(False, f"RAM-Auslastung zu hoch ({ram_average:.0f} %)")

        gpu_values = [point[3] for point in self._history if point[3] is not None]
        if gpu_values:
            gpu_average = sum(gpu_values) / len(gpu_values)
            if gpu_average >= idle["max_gpu_percent"]:
                return AvailabilityState(False, f"GPU-Last zu hoch ({gpu_average:.0f} %)")

        self._activated = True
        return AvailabilityState(True, "")
