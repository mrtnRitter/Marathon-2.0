# Marathon 2.0

Workflow-Engine als Webanwendung (interner Name „m2“, im Code `marathon`, Version 2.0.0).

**Aktueller Stand:** UI-Prototyp zum Festlegen von Look & Feel. Alle Inhalte sind Beispieldaten, Schaltflächen ohne echte Funktion zeigen den Hinweis „Noch nicht verfügbar“. Eine Datenbank wird in diesem Stand noch nicht benötigt.

## Voraussetzungen

- **Python 3.11 oder neuer**
  - Windows: Installer von https://www.python.org, dabei „Add python.exe to PATH“ aktivieren.
  - Linux: Pakete `python3` und `python3-venv`.
- Internetzugang **nur beim Einrichten**. Zur Laufzeit wird nichts nachgeladen.

Alles Weitere (Python-Pakete, Node.js, UI-Bibliotheken, Schrift) installiert `build.py` lokal in den Projektordner. Es wird nichts global installiert.

## Einrichten

Im Projektordner ausführen:

```bash
python build.py      # Windows (alternativ: py build.py)
python3 build.py     # Linux
```

`build.py` erledigt:

1. Lokale Python-Umgebung `.venv` anlegen und `requirements/server.txt` installieren.
2. Node.js (feste Version, Prüfsumme wird kontrolliert) nach `.tools/node` laden.
3. UI-Bibliotheken aus `ui/package-lock.json` nach `ui/node_modules` installieren.
4. Die Oberfläche bauen; das Ergebnis liegt in `marathon/server/static`.

Bereits erledigte Schritte werden übersprungen. `build.py` erneut ausführen nach jedem `git pull` und nach Änderungen am UI-Code, damit `python run.py` den aktuellen Stand zeigt.

## Starten

```bash
python run.py        # Linux: python3 run.py
```

Oberfläche im Browser öffnen: http://127.0.0.1:8080 (Adresse und Port siehe Konfiguration).

### Entwicklungsmodus (Live-Vorschau)

Für die Arbeit am Look & Feel: Änderungen unter `ui/src` erscheinen sofort im Browser, ohne neu zu bauen.

```bash
python run.py --dev
```

Oberfläche öffnen: http://127.0.0.1:5173

Beenden jeweils mit `Strg+C`.

### Konsolenbefehle

| Befehl | Wirkung |
|---|---|
| `python build.py` | Alles installieren und die Oberfläche bauen |
| `python run.py` | Server starten, liefert die gebaute Oberfläche aus |
| `python run.py --dev` | Live-Vorschau der Oberfläche starten |
| `python build.py --help` / `python run.py --help` | Hilfe anzeigen |

## Konfiguration

Datei `config/server.toml`:

| Eintrag | Bedeutung | Standard |
|---|---|---|
| `host` | Adresse, an der der Server erreichbar ist. `0.0.0.0` macht ihn im lokalen Netz erreichbar. | `127.0.0.1` |
| `port` | Port des Servers | `8080` |

## Tests (optional)

```bash
.venv\Scripts\python -m pip install pytest httpx    # Windows
.venv\Scripts\python -m pytest -q
.venv/bin/python -m pip install pytest httpx         # Linux
.venv/bin/python -m pytest -q
```

## Projektstruktur

```
build.py               Einrichten und Bauen
run.py                 Starten
config/server.toml     Server-Einstellungen
requirements/          Python-Abhängigkeiten (server.txt)
marathon/common/       Gemeinsame Grundlage (Version)
marathon/server/       Server (api/ = API-Bereiche); static/ enthält die gebaute Oberfläche
ui/                    Quellcode der Oberfläche
  src/app/             Rahmen, Navigation, Routen
  src/theme/           Farben, Schrift, Status-Farben (zentral)
  src/components/      Wiederverwendbare Bausteine
  src/data/            Datenschicht (heute Beispieldaten, später API)
  src/pages/           Seiten
  src/features/        Asset-Liste, Node-Editor
tests/                 Automatische Tests
docs/mission_log.md    Planung und Fortschritt der Releases
```

Lokal erzeugte Ordner (nicht im Repo): `.venv`, `.tools`, `.build`, `ui/node_modules`, `marathon/server/static`.
