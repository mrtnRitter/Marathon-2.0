# Marathon 2.0.0 – Pflichtenheft Startumfang

**Stand:** 05.11.2026 · **Status:** Entwurf, wartet auf Freigabe
Grundlage: `docs/m2_architektur_vorueberlegungen.md`, Abschnitt 11.

## Ziel und Umfang

- Erste lauffähige Version: Der Kreislauf läuft komplett durch (Asset auf die Liste → Job → Worker → Status), inklusive Auto-Update des Worker-Clients.
- Läuft auf Windows 11; Code plattformunabhängig.
- Name im Code `marathon`, Version `2.0.0` an genau einer Stelle.

## Ein- und Ausgaben

- **Eingaben:** Workflows im Editor; Assets manuell (Name) oder als CSV-Import (Spalte `name`, alle weiteren Spalten werden Metadaten).
- **Ausgaben:** Oberfläche mit Live-Status, Historie und Logs; Client-Pakete für den Rollout.

## Funktionen

**Build** – `python build.py`
- Legt alles im Projektordner ab: Python-Umgebung mit gepinnten Requirements, PostgreSQL (offizielle Windows-Programmdateien) mit Datenordner, Node.js nur für den Bau der Oberfläche, Schrift JetBrains Mono.
- Baut die Oberfläche und das Client-Paket.
- Voraussetzung: installiertes Python 3.12+. Internet nur beim Bauen.

**Server** – `python -m marathon.server`, Einstellungen in `server.toml`
- Startet und stoppt die lokale PostgreSQL mit.
- Datenbank mit Migrationen: Workflows, Versionen, Assets, Jobs, Historie, Worker, Client-Versionen.
- Engine: veröffentlicht je Asset und Modul einen Job; teilt ihn einem freigegebenen Worker mit passenden Fähigkeiten zu (höchstens seine maximale Zahl paralleler Jobs); wertet den Ausgang aus und setzt Status, Position und Historie.
- Jeder Workflow beginnt an einem festen Element **Start** (läuft im Server, kein Job). Pausierte Workflows veröffentlichen keine neuen Jobs.
- Änderungen am Workflow gelten sofort; Assets an entfernten Modulen werden fehlgeschlagen.
- Worker ohne Lebenszeichen werden als `offline` angezeigt.
- Live-Status per Server-Sent Events; HTTP, HTTPS per Zertifikat in `server.toml` zuschaltbar; Andockstelle Login ohne Funktion.

**Oberfläche** – React Flow, nur Dark Theme, JetBrains Mono
- Seitenleiste: Dashboard, Workflows, Assets, Worker.
- Dashboard: je Workflow Name, aktiv/pausiert, Anzahl Assets je Gesamtstatus; Sprung in Module und Assets.
- Workflow: Modus Bearbeiten (Modultypen ziehen, verbinden, Einstellungsformular aus Modultyp-Beschreibung) und Überwachen (Zähler je Modul); Klick auf Modul zeigt Jobs, Assets, Worker, Logs.
- Assets: Reiter je Gesamtstatus; Aktionen ansehen, erneut verarbeiten, anhalten/freigeben, entfernen; Asset hinzufügen und CSV-Import.
- Asset-Detail: Metadaten, Zeitstrahl (Module, Jobs, Worker, Dauer), Meldungen, Log (live).
- Worker: Liste mit Status, Fähigkeiten, Parallelität, Version; freigeben, sperren, benennen; Client-Version für alle oder ausgewählte Worker ausrollen.

**Worker-Client** – Ordner aus dem Client-Paket, Start per Befehl, Einstellung `server_url` in `client.toml`
- Paket enthält alles inklusive Python; auf dem Worker ist nichts vorinstalliert.
- Starter startet die Client-Laufzeit, tauscht sie bei Update und fällt bei Startfehler auf die vorherige Version zurück.
- Laufzeit: anmelden, Lebenszeichen mit Status und Version, Job holen, Modul ausführen, Fortschritt, Log und Ergebnis melden.
- Update: Paket laden, Prüfsumme prüfen, keine neuen Jobs annehmen, laufende fertig, tauschen, neu starten, neue Version melden.

**Module**
- Modul-Schnittstelle mit Modultyp-Beschreibung (Ein- und Ausgänge, Einstellungen, Fähigkeiten).
- Testmodul **Warten**: Einstellungen Dauer und Ausgang (*Erfolg*/*Fehler*); meldet Fortschritt und Log-Zeilen.
- Module werden im Client-Paket mitgeliefert.

## Erfolgskriterien

1. `python build.py` auf einem frischen Windows 11 installiert alles im Projektordner, nichts global.
2. Server startet; Oberfläche im Browser erreichbar, Dark Theme, Monospace-Schrift.
3. Workflow „Start → Warten → Warten (Fehler)“ lässt sich bauen und speichern.
4. 10 Assets per CSV erscheinen als *Ausstehend*; nach Aktivieren laufen sie durch, Status ändert sich live in allen drei Ansichten.
5. Zwei Worker-Clients melden sich als *neu*, werden freigegeben und teilen sich die Jobs.
6. Ein fehlgeschlagenes Asset lässt sich erneut verarbeiten.
7. Version 2.0.1 ausgerollt auf einen Worker: Er aktualisiert sich nach dem laufenden Job und meldet 2.0.1. Eine defekte Version führt zum Rückfall auf die vorherige.
8. „m2“ kommt im Code nicht vor.

## Grenzen

- Ein Job enthält genau ein Asset; das Datenmodell erlaubt später mehrere.
- Kein automatisches Wiederholen und kein automatisches Zurückholen von Jobs bei Worker-Ausfall.
- Aufräumregel, Login, Quell-Module, Fachmodule, Dateiablage: nicht enthalten.
- Worker-Client wird manuell gestartet (kein Dienst, kein Autostart).
- Getestet nur unter Windows 11.
