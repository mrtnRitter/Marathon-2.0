"""Priolist module: validates an ordered list of metadata entries."""

# IMPORTS
from collections.abc import Mapping
from typing import Any


# CONFIG
MODULE = {
    "id": "priolist",
    "name": "Prioliste",
    "category": "Daten",
    "icon": "list-ordered",
    "description": "Liste von Metadaten-Einträgen; die Position in der Liste ist die Priorität. Mit dem Eingang eines Moduls verbinden, das die Assets danach verarbeitet.",
    "has_input": False,
    "outputs": [{
        "id": "list",
        "label": "Prioliste",
        "description": "Zusätzlich zum Asset-Fluss mit dem Eingang eines Moduls verbinden",
    }],
    "settings": [{"key": "entries", "label": "Einträge", "type": "priolist", "default": []}],
    "sort_order": 60,
}


# FUNC
def validate_settings(settings: Mapping[str, Any]) -> dict[str, str]:
    """Validate ordered priolist entries and return the first field error."""
    entries = settings.get("entries", [])
    if not isinstance(entries, list):
        return {"entries": "Liste erwartet"}

    seen_names = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            return {"entries": f"Eintrag {index}: Name fehlt"}
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            return {"entries": f"Eintrag {index}: Name fehlt"}
        metadata = entry.get("metadata", {})
        if not isinstance(metadata, dict):
            return {"entries": f"Eintrag {index}: Metadaten ungültig"}
        normalized_name = name.strip().casefold()
        if normalized_name in seen_names:
            return {"entries": f"Eintrag {index}: Name doppelt: {name.strip()}"}
        seen_names.add(normalized_name)
    return {}
