"""Wait module: pauses an asset for a configured duration."""

# IMPORTS
from collections.abc import Mapping
from typing import Any

from marathon.modules._job import JobStopped, ModuleJob, ModuleResult, check_stop


# CONFIG
defaults = {"wait_s": 60}
MODULE = {
    "id": "wait",
    "name": "Warten",
    "category": "Steuerung",
    "icon": "timer",
    "description": "Hält jedes Asset eine einstellbare Zeit an und gibt es dann weiter. Für kurze Zeitspannen gedacht.",
    "has_input": True,
    "outputs": [{"id": "success", "label": "Erfolg", "description": "Wartezeit ist abgelaufen"}],
    "settings": [{
        "key": "wait_s",
        "label": "Dauer",
        "type": "number",
        "default": 60,
        "min": 1,
        "max": 86400,
        "unit": "s",
        "help": "Angabe in Sekunden, z. B. 3600 für eine Stunde",
    }],
    "sort_order": 20,
}


# FUNC
def validate_settings(settings: Mapping[str, Any]) -> dict[str, str]:
    """Validate wait settings and return field-specific error messages."""
    wait_s = settings.get("wait_s", defaults["wait_s"])
    if isinstance(wait_s, bool) or not isinstance(wait_s, (int, float)):
        return {"wait_s": "Zahl erwartet"}
    if wait_s < 1:
        return {"wait_s": "Mindestens 1 s"}
    if wait_s > 86400:
        return {"wait_s": "Höchstens 86400 s"}
    return {}


def run(job: ModuleJob) -> ModuleResult:
    """Wait interruptibly, then pass the asset through the success output."""
    errors = validate_settings(job.settings)
    if errors:
        raise ValueError(f"Dauer: {errors['wait_s']}")

    wait_s = job.settings.get("wait_s", defaults["wait_s"])
    check_stop(job.stop)
    if job.stop.wait(wait_s):
        raise JobStopped
    return ModuleResult(output="success", log=(f"{wait_s:g} s gewartet",))
