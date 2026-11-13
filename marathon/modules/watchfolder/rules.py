"""Validation and matching rules for watchfolder settings."""

# IMPORTS
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

# CONFIG
defaults = {"path": "", "include": "", "exclude": "", "wait_s": 60, "recursive": False}
min_wait_s = 5
max_wait_s = 86400
_INVALID_EXTENSION_CHARS = frozenset('/\\:*?"<>|')
_NETWORK_PATH_PATTERN = re.compile(r"^\\\\[^\\/]+[\\/][^\\/]+")
_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_ERROR_LABELS = {
    "path": "Ordner",
    "include": "Include-Endungen",
    "exclude": "Exclude-Endungen",
    "wait_s": "Wartezeit",
    "recursive": "Unterordner einbeziehen",
}

# FUNC
@dataclass(frozen=True)
class WatchSettings:
    path: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    wait_s: float
    recursive: bool


def parse_extensions(raw: str) -> list[str]:
    """Parse and stably deduplicate comma-separated extensions."""
    extensions = []
    seen = set()
    for value in raw.split(","):
        entry = value.strip()
        if not entry:
            continue
        extension = entry.casefold()
        if not extension.startswith("."):
            extension = f".{extension}"
        if extension == "." or any(char.isspace() or char in _INVALID_EXTENSION_CHARS for char in extension):
            raise ValueError(f"Ungültige Endung: {entry}")
        if extension not in seen:
            extensions.append(extension)
            seen.add(extension)
    return extensions


def validate_path(raw: str) -> str | None:
    """Return a validation message for a non-absolute path, or None."""
    path = raw.strip()
    if not path:
        return "Ordner fehlt"
    if path.startswith("\\\\"):
        if _NETWORK_PATH_PATTERN.match(path):
            return None
        return "Netzpfad braucht Server und Freigabe: \\\\server\\freigabe"
    if _DRIVE_PATH_PATTERN.match(path) or path.startswith("/"):
        return None
    return "Pfad muss absolut sein, z. B. D:\\Ingest oder \\\\server\\freigabe"


def validate_settings(settings: Mapping[str, Any]) -> dict[str, str]:
    """Validate watchfolder values and return field-specific error messages."""
    errors = {}
    path_error = validate_path(str(settings.get("path", defaults["path"])))
    if path_error:
        errors["path"] = path_error

    parsed_extensions = {}
    for key in ("include", "exclude"):
        value = settings.get(key, defaults[key])
        if not isinstance(value, str):
            errors[key] = "Text erwartet"
            continue
        try:
            parsed_extensions[key] = parse_extensions(value)
        except ValueError as error:
            errors[key] = str(error)

    if all(key in parsed_extensions for key in ("include", "exclude")):
        include = set(parsed_extensions["include"])
        overlaps = [extension for extension in parsed_extensions["exclude"] if extension in include]
        if overlaps:
            if len(overlaps) == 1:
                errors["exclude"] = f"{overlaps[0]} steht bereits in der Include-Liste"
            else:
                errors["exclude"] = f"{', '.join(overlaps)} stehen bereits in der Include-Liste"

    wait_s = settings.get("wait_s", defaults["wait_s"])
    if isinstance(wait_s, bool) or not isinstance(wait_s, (int, float)):
        errors["wait_s"] = "Zahl erwartet"
    elif wait_s < min_wait_s:
        errors["wait_s"] = f"Mindestens {min_wait_s} s"
    elif wait_s > max_wait_s:
        errors["wait_s"] = f"Höchstens {max_wait_s} s"

    if not isinstance(settings.get("recursive", defaults["recursive"]), bool):
        errors["recursive"] = "Ja oder Nein erwartet"
    return errors


def read_settings(settings: Mapping[str, Any]) -> WatchSettings:
    """Validate settings and return their normalized immutable form."""
    errors = validate_settings(settings)
    for key, label in _ERROR_LABELS.items():
        if key in errors:
            raise ValueError(f"{label}: {errors[key]}")
    return WatchSettings(
        path=str(settings.get("path", defaults["path"])).strip(),
        include=tuple(parse_extensions(settings.get("include", defaults["include"]))),
        exclude=tuple(parse_extensions(settings.get("exclude", defaults["exclude"]))),
        wait_s=float(settings.get("wait_s", defaults["wait_s"])),
        recursive=settings.get("recursive", defaults["recursive"]),
    )


def matches_extensions(file_name: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    """Return whether a filename passes include and exclude suffix rules."""
    name = file_name.casefold()
    return (not include or any(name.endswith(extension) for extension in include)) and not any(
        name.endswith(extension) for extension in exclude
    )
