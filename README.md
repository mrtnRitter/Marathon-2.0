# Marathon 2.0

Workflow-Engine als Webanwendung (interner Name „m2“, im Code `marathon`, Version 2.0.0).

**Aktueller Stand:** Die Oberfläche liest und speichert alle Inhalte in einer lokalen PostgreSQL-Datenbank. Workflows lassen sich anlegen, ändern, duplizieren, im Node-Editor bearbeiten, speichern und löschen. Workflows lassen sich in der Workflow-Übersicht, auf dem Dashboard und auf ihrer Detailseite starten und pausieren und zeitgesteuert starten (alle X Minuten, Stunden, Tage, Wochen oder Monate); ein zeitgesteuert gestarteter Workflow pausiert sich selbst, sobald kein Asset mehr aussteht oder in Arbeit ist. Assets lassen sich erneut verarbeiten, auch in einem anderen Workflow. Reports lassen sich als CSV herunterladen. Über den Katzenkopf unten links lässt sich ein Bug-Report erfassen; er wird noch nicht versendet. Module liegen im Repo unter `marathon/modules/` und erscheinen beim Serverstart in der Modul-Palette: Watchfolder, Warten, Datei kopieren, Datei verschieben, Umbenennen und Prioliste (siehe [Module](#module)). Die weitere Verarbeitung der Assets folgt mit der Engine. Schaltflächen ohne echte Funktion zeigen den Hinweis „Noch nicht verfügbar“.

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
python run.py
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
| `python run.py --dev` | Datenbank, Server und Live-Vorschau starten |
| `python build.py --help` / `python run.py --help` | Hilfe anzeigen |

### Datenbank zurücksetzen

Löscht alle Daten: Server beenden, Ordner `.data` löschen, `python build.py` ausführen, dann `python run.py`.

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

## Module

### Watchfolder (Kategorie „Quelle“)

Überwacht einen Ordner und legt für jede neue Datei ein Asset im Workflow an (Status „Ausstehend“, aktuelles Modul = Watchfolder), sobald sich Größe und Zeitstempel eine Wartezeit lang nicht geändert haben. Gescannt wird nur, solange der Workflow gestartet ist. Kein Eingang, ein Ausgang „Erfolg“.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Ordner | Absoluter Pfad, z. B. `D:\Ingest`, verbundenes Laufwerk `Z:\Ingest` oder Netzpfad `\\server\freigabe\ordner`. Direkt eingeben oder über den Ordner-Button wählen. | – |
| Include-Endungen | Nur diese Endungen übernehmen, z. B. `.mxf, .mov`. Leer = alle Dateien. | leer |
| Exclude-Endungen | Diese Endungen nie übernehmen. Eine Endung darf nicht in beiden Listen stehen. | leer |
| Wartezeit | So lange müssen Größe und Zeitstempel unverändert sein; zugleich Abstand der Ordnerprüfung (5–86400 s). | 60 s |
| Unterordner einbeziehen | Auch Dateien in Unterordnern übernehmen. | aus |

- Pfade gelten aus Sicht des Servers. Das Konto, unter dem der Server läuft, braucht Lesezugriff auf den Ordner bzw. die Netzfreigabe.
- Das Fenster „Ordner wählen“ zeigt nur Ordnernamen des Servers, keine Dateien. Netzfreigaben lassen sich ab `\\server\freigabe` durchblättern.
- Jede Datei wird je Pfad einmal übernommen. Eine unter gleichem Namen ersetzte Datei wird derzeit nicht erneut übernommen. Wird ein Asset gelöscht und liegt die Datei noch im Ordner, wird sie erneut übernommen.
- Ist der Ordner nicht erreichbar (oder antwortet er 60 s nicht), steht das einmal im Modul-Log; danach folgt „Ordner wieder erreichbar“.
- Zeitgesteuert gestartete Workflows pausieren sich, sobald kein Asset aussteht. Den Watchfolder deshalb in einem dauerhaft gestarteten Workflow verwenden.

Warten, Datei kopieren, Datei verschieben und Umbenennen lassen sich im Workflow platzieren und einstellen. Ausgeführt werden sie, sobald Engine und Worker folgen; Pfade gelten dann aus Sicht des Workers.

### Warten (Kategorie „Steuerung“)

Hält jedes Asset eine einstellbare Zeit an und gibt es dann über „Erfolg“ weiter. Für kurze Zeitspannen gedacht.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Dauer | Wartezeit in Sekunden (1–86400), z. B. `3600` für eine Stunde | 60 s |

### Datei kopieren (Kategorie „Dateien“)

Kopiert die Datei des Assets unter ihrem Originalnamen in den Zielordner. Ausgänge „Erfolg“ und „Fehler“.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Zielordner | Absoluter Pfad, Laufwerksbuchstabe oder Netzpfad | – |
| Checksumme | `Keine`, `MD5` oder `SHA-256` | Keine |
| Namenskonflikt mit _n lösen | Ist der Name im Ziel belegt, wird `_1`, `_2` … vor der Endung angehängt | aus |

- Ohne Checksumme gilt die Kopie als erfolgreich, sobald das Betriebssystem den Schreibvorgang bestätigt. Mit Checksumme wird die Kopie nachgelesen; stimmen die Prüfsummen nicht, wird die Kopie gelöscht und das Asset geht auf „Fehler“.
- Erfolg: Das Original ist im Durchlauf beendet (Log „Kopiert nach …“). Die Kopie wird ein neues Asset (Log „Kopie von …“) und läuft über „Erfolg“ weiter.
- Ohne „_n“ führt ein belegter Name zu „Fehler“. Vorhandene Dateien werden nie überschrieben.
- Bei Abbruch oder Fehler wird eine unvollständige Kopie entfernt.

### Datei verschieben (Kategorie „Dateien“)

Verschiebt die Datei des Assets unter ihrem Originalnamen in den Zielordner. Ausgänge „Erfolg“ und „Fehler“; danach zeigt das Asset auf den neuen Pfad.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Zielordner | Absoluter Pfad, Laufwerksbuchstabe oder Netzpfad | – |

- Auf demselben Laufwerk bzw. derselben Freigabe verschiebt das Betriebssystem die Datei direkt.
- Liegt das Ziel auf einem anderen Laufwerk, wird die Datei kopiert und die Quelle gelöscht – ohne Checksumme. Das Log weist darauf hin.
- Ist der Name im Ziel belegt, geht das Asset auf „Fehler“.

### Umbenennen (Kategorie „Dateien“)

Setzt ein Präfix vor den Dateinamen und ein Suffix vor die Dateiendung, z. B. `clip.mxf` mit Präfix `P_` und Suffix `_v2` → `P_clip_v2.mxf`. Ausgänge „Erfolg“ und „Fehler“; danach zeigt das Asset auf den neuen Namen.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Präfix | Text vor dem Dateinamen | leer |
| Suffix | Text vor der Dateiendung | leer |
| Namenskonflikt mit _n lösen | Ist der neue Name belegt, wird `_1`, `_2` … angehängt; sonst „Fehler“ | aus |

Präfix oder Suffix muss gesetzt sein. Nicht erlaubt sind `/ \ : * ? " < > |` und Steuerzeichen.

### Prioliste (Kategorie „Daten“)

Liste von Einträgen mit Namen, z. B. „Kollektion Defa“, „Kollektion Cintec“. Die Position in der Liste ist die Priorität (Spalte „Prio“). Kein Eingang; der Ausgang „Prioliste“ wird zusätzlich zum Asset-Fluss mit dem Eingang des Moduls verbunden, das die Assets danach verarbeiten soll.

- In den Moduleinstellungen: „Eintrag hinzufügen“, Name direkt bearbeiten, Pfeile zum Hoch- und Runtersetzen.
- Drei-Punkte-Menü: „Metadaten bearbeiten“ (noch nicht verfügbar) und „Entfernen“.
- Namen sind Pflicht und eindeutig (Groß-/Kleinschreibung zählt nicht).

## Tests (optional)

```bash
.venv\Scripts\python -m pip install pytest httpx    # Windows
.venv\Scripts\python -m pytest -q
.venv/bin/python -m pip install pytest httpx         # Linux
.venv/bin/python -m pytest -q
```

Prüfregeln der Oberfläche:

```bash
.tools\node\node.exe --test ui/tests/module_settings.test.mjs ui/tests/selection.test.mjs   # Windows
.tools/node/bin/node --test ui/tests/module_settings.test.mjs ui/tests/selection.test.mjs     # Linux
```

Die Datenbank-Tests starten eine eigene, temporäre PostgreSQL-Instanz mit den Programmen aus `.tools/postgres` und räumen sie danach wieder ab.

## Projektstruktur

```
build.py               Einrichten und Bauen
run.py                 Starten
config/server.toml     Server- und Datenbank-Einstellungen
requirements/          Python-Abhängigkeiten (server.txt)
marathon/common/       Gemeinsame Grundlage (Version)
marathon/modules/      Module (je Ordner ein Modultyp, z. B. watchfolder/; _job.py Ausführungsvertrag, _files.py Datei-Helfer)
marathon/server/       Server; static/ enthält die gebaute Oberfläche
  api/                 API-Bereiche (/api/v1)
  db/                  Datenbank: Verbindung, Migrationen, Abfragen
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
