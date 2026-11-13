"""Move an asset file to a configured destination folder."""

# IMPORTS
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from marathon.modules._files import NameConflict, move_file
from marathon.modules._job import ModuleJob, ModuleResult, check_stop
from marathon.modules.watchfolder.rules import validate_path


# CONFIG
defaults = {"target": ""}
MODULE = {
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
    "settings": [{
        "key": "target",
        "label": "Zielordner",
        "type": "path",
        "default": "",
        "help": "Echtes Verschieben nur auf demselben Laufwerk bzw. derselben Freigabe. Liegt das Ziel woanders, wird die Datei kopiert und die Quelle gelöscht – ohne Checksumme.",
    }],
    "sort_order": 40,
}


# FUNC
def validate_settings(settings: Mapping[str, Any]) -> dict[str, str]:
    """Validate move-file settings and return field-specific error messages."""
    error = validate_path(str(settings.get("target", defaults["target"])))
    return {"target": error} if error else {}


def run(job: ModuleJob) -> ModuleResult:
    """Move the input file to the configured folder and report the result."""
    errors = validate_settings(job.settings)
    if errors:
        raise ValueError(f"Zielordner: {errors['target']}")

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
