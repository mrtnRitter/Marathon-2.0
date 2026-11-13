"""Watchfolder module: watches a folder and adds stable files as assets."""

# IMPORTS
from marathon.modules.watchfolder.rules import validate_settings

# CONFIG
MODULE = {
    "id": "watchfolder",
    "name": "Watchfolder",
    "category": "Quelle",
    "icon": "folder-input",
    "description": "Überwacht einen Ordner und übernimmt neue Dateien als Assets, sobald sie sich eine Wartezeit lang nicht mehr ändern.",
    "has_input": False,
    "outputs": [{"id": "success", "label": "Erfolg", "description": "Datei ist bereit für die Verarbeitung"}],
    "settings": [
        {"key": "path", "label": "Ordner", "type": "path", "default": "", "help": "Lokaler Pfad, Laufwerksbuchstabe oder Netzpfad (\\\\server\\freigabe)"},
        {"key": "include", "label": "Include-Endungen", "type": "extensions", "default": "", "exclusive_with": "exclude", "list_label": "Include-Liste", "help": "z. B. .mxf, .mov · leer = alle Dateien"},
        {"key": "exclude", "label": "Exclude-Endungen", "type": "extensions", "default": "", "exclusive_with": "include", "list_label": "Exclude-Liste", "help": "Diese Endungen werden nie übernommen"},
        {"key": "wait_s", "label": "Wartezeit", "type": "number", "default": 60, "min": 5, "max": 86400, "unit": "s", "help": "So lange dürfen sich Größe und Zeitstempel nicht ändern; zugleich Abstand der Ordnerprüfung"},
        {"key": "recursive", "label": "Unterordner einbeziehen", "type": "boolean", "default": False},
    ],
    "sort_order": 10,
}
