# Marathon 2.0

Workflow-Engine als Webanwendung (interner Name „m2“, im Code `marathon`, Version 2.0.0).

**Aktueller Stand:** Die Oberfläche liest alle Inhalte aus einer lokalen PostgreSQL-Datenbank. Zum Testen lassen sich Demo-Daten laden. Der Stand ist nur lesend: Schaltflächen ohne echte Funktion zeigen den Hinweis „Noch nicht verfügbar“.

## Voraussetzungen

- **Python 3.11.4 oder neuer**
  - Windows: Installer von https://www.python.org, dabei „Add python.exe to PATH“ aktivieren.
  - Linux: Pakete `python3` und `python3-venv`.
- Windows oder Linux, jeweils 64 Bit (x64).
- Internetzugang **nur beim Einrichten**. Zur Laufzeit wird nichts nachgeladen.

Alles Weitere (Python-Pakete, Node.js, PostgreSQL, UI-Bibliotheken, Schrift) installiert `build.py` lokal in den Projektordner. Es wird nichts global installiert.

## Einrichten

Im Projektordner ausführen:

```bash
python build.py      # Windows (alternativ: py build.py)
python3 build.py     # Linux
```

`build.py` erledigt:

1. Lokale Python-Umgebung `.venv` anlegen und `requirements/server.txt` installieren.
2. Node.js (feste Version, Prüfsumme wird kontrolliert) nach `.tools/node` laden.
3. PostgreSQL 18.6 (feste Version, Prüfsumme wird kontrolliert) nach `.tools/postgres` laden.
4. Datenbank-Ordner `.data/postgres` anlegen. Das Passwort wird zufällig erzeugt und liegt in `.data/postgres.pass`.
5. UI-Bibliotheken aus `ui/package-lock.json` nach `ui/node_modules` installieren.
6. Die Oberfläche bauen; das Ergebnis liegt in `marathon/server/static`.

Bereits erledigte Schritte werden übersprungen. `build.py` erneut ausführen nach jedem `git pull` und nach Änderungen am UI-Code.

## Starten

```bash
python run.py --demo-data   # beim ersten Start: Demo-Daten laden
python run.py               # danach
```

`run.py` startet die Datenbank, bringt sie auf den neuesten Stand und startet den Server. Beim Beenden (`Strg+C`) wird die Datenbank wieder gestoppt.

Oberfläche im Browser öffnen: http://127.0.0.1:8080 (Adresse und Port siehe Konfiguration).

### Entwicklungsmodus (Live-Vorschau)

Änderungen unter `ui/src` erscheinen sofort im Browser, ohne neu zu bauen. Datenbank und Server laufen mit.

```bash
python run.py --dev
```

Oberfläche öffnen: http://127.0.0.1:5173

### Konsolenbefehle

| Befehl | Wirkung |
|---|---|
| `python build.py` | Alles installieren, Datenbank-Ordner anlegen und die Oberfläche bauen |
| `python run.py` | Datenbank und Server starten |
| `python run.py --demo-data` | Wie oben, vorher Demo-Daten laden. Nur in eine leere Datenbank, sonst wird der Schritt übersprungen |
| `python run.py --dev` | Datenbank, Server und Live-Vorschau starten (mit `--demo-data` kombinierbar) |
| `python build.py --help` / `python run.py --help` | Hilfe anzeigen |

### Datenbank zurücksetzen

Löscht alle Daten: Server beenden, Ordner `.data` löschen, `python build.py` ausführen, dann `python run.py --demo-data`.

## Konfiguration

Datei `config/server.toml`:

| Eintrag | Bedeutung | Standard |
|---|---|---|
| `host` | Adresse, an der der Server erreichbar ist. `0.0.0.0` macht ihn im lokalen Netz erreichbar. | `127.0.0.1` |
| `port` | Port des Servers | `8080` |
| `[database] host` | Adresse, an der die Datenbank lauscht | `127.0.0.1` |
| `[database] port` | Port der Datenbank | `5433` |
| `[database] name` | Name der Datenbank | `marathon` |
| `[database] user` | Datenbank-Benutzer; wird beim ersten `build.py` angelegt | `marathon` |

`name` und `user` erlauben nur Kleinbuchstaben, Ziffern und `_`. Den Benutzer nach dem Einrichten nicht mehr ändern, sonst die Datenbank zurücksetzen.

## Tests (optional)

```bash
.venv\Scripts\python -m pip install pytest httpx    # Windows
.venv\Scripts\python -m pytest -q
.venv/bin/python -m pip install pytest httpx         # Linux
.venv/bin/python -m pytest -q
```

Die Datenbank-Tests starten eine eigene, temporäre PostgreSQL-Instanz mit den Programmen aus `.tools/postgres` und räumen sie danach wieder ab.

## Projektstruktur

```
build.py               Einrichten und Bauen
run.py                 Starten
config/server.toml     Server- und Datenbank-Einstellungen
requirements/          Python-Abhängigkeiten (server.txt)
marathon/common/       Gemeinsame Grundlage (Version)
marathon/server/       Server; static/ enthält die gebaute Oberfläche
  api/                 API-Bereiche (/api/v1)
  db/                  Datenbank: Verbindung, Migrationen, Abfragen, Demo-Daten
ui/                    Quellcode der Oberfläche
  src/app/             Rahmen, Navigation, Routen
  src/theme/           Farben, Schrift, Status-Farben (zentral)
  src/components/      Wiederverwendbare Bausteine
  src/data/            Datenschicht (Zugriff auf die API)
  src/pages/           Seiten
  src/features/        Asset-Liste, Node-Editor
tests/                 Automatische Tests
docs/mission_log.md    Planung und Fortschritt der Releases
```

Lokal erzeugte Ordner (nicht im Repo): `.venv`, `.tools`, `.build`, `.data`, `ui/node_modules`, `marathon/server/static`.
