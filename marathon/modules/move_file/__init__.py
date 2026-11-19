"""Move an asset file to a configured destination folder."""

# IMPORTS
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from marathon.modules._files import NameConflict, move_file
from marathon.modules._job import ModuleJob, ModuleResult, check_stop
from marathon.modules.watchfolder.rules import validate_path


# CONFIG
defaults = {"target": "", "parallel": 1}
MODULE = {
    "execution": "worker",
    "id": "move_file",
    "name": "Datei verschieben",
    "category": "Dateien",
    "icon": "move",
    "description": "Verschiebt die Datei des Assets unter ihrem Originalnamen in den Zielordner.",
    "has_input": True,
    "outputs": [
        {"id": "success", "label": "Erfolg", "description": "Datei liegt im Zielordner"},
        {"id": "error", "label": "Fehler", "description": "Verschieben fehlgeschlagen oder Name im Ziel belegt"},
    ],
    "settings": [
        {
            "key": "target",
            "label": "Zielordner",
            "type": "path",
            "default": "",
            "help": "Echtes Verschieben nur auf demselben Laufwerk bzw. derselben Freigabe. Liegt das Ziel woanders, wird die Datei kopiert und die Quelle gelöscht – ohne Checksumme.",
        },
        {
            "key": "parallel",
            "label": "Parallele Verarbeitung",
            "type": "number",
            "default": 1,
            "min": 1,
            "max": 16,
            "help": "So viele Assets bearbeitet ein Job gleichzeitig, jedes in einem eigenen Strom",
        },
    ],
    "sort_order": 40,
}


# FUNC
def validate_settings(settings: Mapping[str, Any]) -> dict[str, str]:
    """Validate move-file settings and return field-specific error messages."""
    errors = {}
    target_error = validate_path(str(settings.get("target", defaults["target"])))
    if target_error:
        errors["target"] = target_error
    parallel = settings.get("parallel", defaults["parallel"])
    if type(parallel) is not int or not 1 <= parallel <= 16:
        errors["parallel"] = "Ganze Zahl von 1 bis 16"
    return errors


def run(job: ModuleJob) -> ModuleResult:
    """Move the input file to the configured folder and report the result."""
    errors = validate_settings(job.settings)
    labels = {"target": "Zielordner", "parallel": "Parallele Verarbeitung"}
    for key, label in labels.items():
        if key in errors:
            raise ValueError(f"{label}: {errors[key]}")

    check_stop(job.stop)
    source = Path(job.asset_path)
    if not source.is_file():
        return ModuleResult(output="error", log=(f"Quelldatei nicht gefunden: {source}",))

    target_dir = Path(str(job.settings.get("target", defaults["target"])).strip())
    if not target_dir.is_dir():
        return ModuleResult(output="error", log=(f"Zielordner nicht gefunden: {target_dir}",))

    target = target_dir / source.name
    try:
        moved = move_file(source, target, job.stop)
    except NameConflict as error:
        return ModuleResult(output="error", log=(str(error),))
    except OSError as error:
        return ModuleResult(output="error", log=(f"Verschieben fehlgeschlagen: {error}",))

    log = (f"Verschoben nach {target}",) + (() if moved else (
        "Quelle und Ziel liegen nicht auf demselben Laufwerk: Datei wurde kopiert und die Quelle gelöscht (ohne Checksumme)",
    ))
    return ModuleResult(output="success", log=log, asset_path=str(target))
