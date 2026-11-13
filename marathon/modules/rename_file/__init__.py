"""Rename module: adds a prefix and suffix to an asset filename."""

# IMPORTS
from collections.abc import Mapping
import os
from pathlib import Path
from typing import Any

from marathon.modules._files import NameConflict, free_name
from marathon.modules._job import ModuleJob, ModuleResult, check_stop


# CONFIG
defaults = {"prefix": "", "suffix": "", "numbered": False}
MODULE = {
    "id": "rename_file",
    "name": "Umbenennen",
    "category": "Dateien",
    "icon": "rename",
    "description": "Benennt die Datei des Assets um: Präfix vor den Namen, Suffix vor die Dateiendung.",
    "has_input": True,
    "outputs": [
        {"id": "success", "label": "Erfolg", "description": "Datei ist umbenannt"},
        {"id": "error", "label": "Fehler", "description": "Umbenennen fehlgeschlagen oder Name belegt"},
    ],
    "settings": [
        {"key": "prefix", "label": "Präfix", "type": "text", "default": "", "help": "Wird vor den Dateinamen gesetzt"},
        {"key": "suffix", "label": "Suffix", "type": "text", "default": "", "help": "Wird vor der Dateiendung eingefügt"},
        {"key": "numbered", "label": "Namenskonflikt mit _n lösen", "type": "boolean", "default": False, "help": "Ist der neue Name belegt, wird _1, _2 … angehängt; sonst Fehler"},
    ],
    "sort_order": 50,
}


# FUNC
def validate_settings(settings: Mapping[str, Any]) -> dict[str, str]:
    """Return all field-specific validation errors in setting order."""
    errors = {}
    values = {key: settings.get(key, defaults[key]) for key in ("prefix", "suffix")}
    for key, value in values.items():
        if not isinstance(value, str):
            errors[key] = "Text erwartet"
            continue
        invalid_character = next((character for character in value if character in '/\\:*?"<>|'), None)
        if invalid_character is not None:
            errors[key] = f"Ungültiges Zeichen: {invalid_character}"
        elif any(ord(character) < 32 for character in value):
            errors[key] = "Steuerzeichen nicht erlaubt"

    if not errors and not values["prefix"] and not values["suffix"]:
        errors["prefix"] = "Präfix oder Suffix angeben"

    numbered = settings.get("numbered", defaults["numbered"])
    if not isinstance(numbered, bool):
        errors["numbered"] = "Ja oder Nein erwartet"
    return errors


def run(job: ModuleJob) -> ModuleResult:
    """Rename the asset file and return success or a user-facing error."""
    errors = validate_settings(job.settings)
    if errors:
        labels = {"prefix": "Präfix", "suffix": "Suffix", "numbered": "Namenskonflikt mit _n lösen"}
        key = next(key for key in ("prefix", "suffix", "numbered") if key in errors)
        raise ValueError(f"{labels[key]}: {errors[key]}")

    check_stop(job.stop)
    source = Path(job.asset_path)
    if not source.is_file():
        return ModuleResult(output="error", log=(f"Quelldatei nicht gefunden: {source}",))

    prefix = job.settings.get("prefix", defaults["prefix"])
    suffix = job.settings.get("suffix", defaults["suffix"])
    numbered = job.settings.get("numbered", defaults["numbered"])
    new_name = f"{prefix}{source.stem}{suffix}{source.suffix}"
    try:
        target = free_name(source.parent, new_name, numbered)
        os.rename(source, target)
    except NameConflict as error:
        return ModuleResult(output="error", log=(str(error),))
    except FileExistsError:
        return ModuleResult(output="error", log=(f"Datei existiert bereits: {target}",))
    except OSError as error:
        return ModuleResult(output="error", log=(f"Umbenennen fehlgeschlagen: {error}",))

    return ModuleResult(output="success", log=(f"Umbenannt in {target.name}",), asset_path=str(target))
