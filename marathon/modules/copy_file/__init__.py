"""Copy an asset file into a target directory as a new asset."""

# IMPORTS
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from marathon.modules._files import ChecksumMismatch, NameConflict, checksum_algorithms, copy_file, free_name
from marathon.modules._job import ModuleJob, ModuleResult, SpawnedAsset, check_stop
from marathon.modules.watchfolder.rules import validate_path


# CONFIG
defaults = {"target": "", "checksum": "Keine", "numbered": False}
MODULE = {
    "id": "copy_file",
    "name": "Datei kopieren",
    "category": "Dateien",
    "icon": "copy",
    "description": "Kopiert die Datei des Assets unter ihrem Originalnamen in den Zielordner. Die Kopie läuft als neues Asset weiter, das Original ist danach beendet.",
    "has_input": True,
    "outputs": [
        {"id": "success", "label": "Erfolg", "description": "Die Kopie läuft als neues Asset weiter"},
        {"id": "error", "label": "Fehler", "description": "Kopieren fehlgeschlagen oder Checksumme stimmt nicht"},
    ],
    "settings": [
        {
            "key": "target",
            "label": "Zielordner",
            "type": "path",
            "default": "",
            "help": "Lokaler Pfad, Laufwerksbuchstabe oder Netzpfad (\\\\server\\freigabe)",
        },
        {
            "key": "checksum",
            "label": "Checksumme",
            "type": "select",
            "options": ["Keine", "MD5", "SHA-256"],
            "default": "Keine",
            "help": "Mit Checksumme gilt die Kopie erst als erfolgreich, wenn die Prüfsummen von Quelle und Kopie übereinstimmen",
        },
        {
            "key": "numbered",
            "label": "Namenskonflikt mit _n lösen",
            "type": "boolean",
            "default": False,
            "help": "Ist der Name im Ziel belegt, wird _1, _2 … angehängt; sonst Fehler",
        },
    ],
    "sort_order": 30,
}


# FUNC
def validate_settings(settings: Mapping[str, Any]) -> dict[str, str]:
    """Validate copy settings and return field-specific error messages."""
    errors = {}
    target_error = validate_path(str(settings.get("target", defaults["target"])))
    if target_error:
        errors["target"] = target_error

    checksum = settings.get("checksum", defaults["checksum"])
    if checksum not in ("Keine", "MD5", "SHA-256"):
        errors["checksum"] = "Ungültige Auswahl"

    if not isinstance(settings.get("numbered", defaults["numbered"]), bool):
        errors["numbered"] = "Ja oder Nein erwartet"
    return errors


def run(job: ModuleJob) -> ModuleResult:
    """Copy the input file and route the copy to success."""
    errors = validate_settings(job.settings)
    labels = {
        "target": "Zielordner",
        "checksum": "Checksumme",
        "numbered": "Namenskonflikt mit _n lösen",
    }
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

    checksum = job.settings.get("checksum", defaults["checksum"])
    numbered = job.settings.get("numbered", defaults["numbered"])
    try:
        target = free_name(target_dir, source.name, numbered)
        digest = copy_file(source, target, job.stop, checksum_algorithms.get(checksum))
    except (NameConflict, ChecksumMismatch) as error:
        return ModuleResult(output="error", log=(str(error),))
    except OSError as error:
        return ModuleResult(output="error", log=(f"Kopieren fehlgeschlagen: {error}",))

    log = (f"Kopiert nach {target}",) + ((f"{checksum} geprüft: {digest}",) if digest else ())
    spawned = SpawnedAsset(path=str(target), output="success", log=(f"Kopie von {source}",))
    return ModuleResult(output=None, log=log, spawned=(spawned,))
