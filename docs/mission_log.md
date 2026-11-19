# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[ ]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`

### Abnahme offen – Release Worker-Steuerung

Vertrag: `docs/worker_spec.md`

- [ ] Einstellungsseite speichert in die Datenbank; einzige echte Einstellung: Server-Adresse für den Ordner-Dialog (Standard `[folder_dialog] server_address`; `host` bleibt nur in der Konfiguration); die Platzhalter Server, Rollout, Aufräumen entfallen
- [ ] Standardwerte aus `config/marathon.toml` gehen beim Start mit `run.py` in die Datenbank, solange dort kein Wert steht; danach gilt die Datenbank
- [ ] Ordner-Dialog: Umschalter „Laufwerke“ | „Server“; „Server“ zeigt die Freigaben von `\\<Server-Adresse>`
- [ ] Vorrang-Stufen entfallen überall; Fähigkeiten je Worker als sortierbare Liste (oben = höhere Prio, nicht gelistet = übernimmt nicht, wählbar nur gemeldete Modultypen); neue Worker ab Werk mit allen gemeldeten Modultypen
- [ ] Zuteilung nach Rang in der Fähigkeiten-Liste; gleicher Rang → Name
- [ ] Server-Worker ab Werk mit Kopieren, Verschieben, Umbenennen (`[server_worker] skills`), in der Oberfläche einstellbar; startet bei der Ersteinrichtung automatisch
- [ ] „Starten“ / „Stoppen“ statt „Freigeben“ / „Sperren“, auch in der Liste; Status „bereit“, „Wird gestoppt“, „gestoppt“; „Online“ / „Offline“ getrennt; Spalte „Jobs“
- [ ] Neuer Worker meldet sich „gestoppt“ an; Stoppen bricht alle Jobs ab, Asset zurück an den letzten fertigen Ausgang, zählt nicht als Versuch
- [ ] Ein Worker macht nur einen Job; ein Job bündelt bis zu N Assets desselben Moduls, bearbeitet in N Strömen, Ergebnis je Asset; N = „Parallele Verarbeitung“ (1–16) bei Kopieren und Verschieben
- [ ] Warten wie Fachmodule: Wartezeit je Asset ab Übergabe, Anzeige „läuft“ / „erledigt“ / „Fehler“, kein „wartet“; läuft als Job ohne Worker
- [ ] Modul-Log zeigt jede Aktivität (Zeit, Stufe, Asset, Worker, Text; neueste 500)
- [ ] Prioliste: „Bearbeiten“ statt „Metadaten bearbeiten“
- [ ] Verbindungslinien beginnen und enden am Rand der Kreise, auch beim Ziehen
- [ ] Erneut verarbeiten ohne Obergrenze
- [ ] Überwachen → Einstellungen: Werte ausgegraut
- [ ] `stop` beendet den Server ohne Fehlermeldungen (offene Worker-Anfrage endet sofort)

### Abnahme offen

- [ ] Der Server teilt zu, der Worker holt ab: Seine Anfrage bleibt ca. 25 s offen; Verbindungen nur vom Worker zum Server
- [ ] Nicht innerhalb der Abhol-Frist abgeholt: Der Job geht zurück und wird neu vergeben
- [ ] Worker-Auswahl nach Vorrang je Worker und Modultyp (Bevorzugt, Normal, Letzte Wahl, Aus); ist der bevorzugte belegt, übernimmt ein anderer mit gleichen Fähigkeiten – wird im nächsten Release durch die Fähigkeiten-Rangliste ersetzt
- [ ] Es gilt die Schnittmenge: Modultypen, die der Rechner meldet, ohne Vorrang „Aus“; parallele Jobs = kleinerer Wert aus Rechner und Worker-Ansicht – im nächsten Release: Modultypen, die der Rechner meldet und die in seiner Fähigkeiten-Liste stehen
- [ ] Zwischen Workflows reihum, keine Workflow-Priorität; im Workflow zuerst das Asset, das am weitesten oben steht
- [ ] Worker-Schnittstelle, versioniert (`/api/v1/worker/…`): anmelden, Job holen (mit Lebenszeichen und Abbruch), Start, Fortschritt und Log, Ergebnis; Zugang per Token
- [ ] Ein Worker gilt nach 30 s ohne Meldung als ausgefallen
- [ ] Absturz oder Ende = Worker offline, Jobs werden neu vergeben, der Server läuft weiter`
- [ ] Unterbrochener Job (Absturz, Worker offline, Neustart): Das Asset geht zurück auf den Ausgang des vorherigen Moduls und wird von dort neu verteilt
- [ ] Nach 3 unterbrochenen Versuchen am selben Modul *Fehlgeschlagen*; der Log zeigt die Versuche
- [ ] Als Versuch zählt nur ein Job, der schon lief; Pausieren und abgelaufene Abhol-Frist zählen nicht; der Zähler beginnt neu, wenn das Asset das Modul verlässt oder erneut verarbeitet wird
- [ ] Server-Neustart: Jobs auf anderen Workern laufen weiter; meldet sich der Worker innerhalb von 30 s, wird sein Ergebnis übernommen
- [ ] `last_choice` überall entfernt (Konfiguration, Worker-Anmeldung, Worker-Laufzeit)

---
---

## Geplant

### Nächster Release: Watchfolder auf Workern, Live-Kanal

#### Watchfolder

- [ ] Watchfolder wird eine Fähigkeit der Worker, ab Werk mit oberster Prio; den Ordner scannt ein Worker statt des Hintergrunddienstes im Server
- [ ] Ist kein passender Worker frei oder gestartet, wartet der Scan; das Modul-Log meldet „Kein Worker für den Watchfolder verfügbar“
- [ ] Netzwerkpfad: Der Server-Worker scannt
- [ ] Lokaler Pfad: Der Worker auf der Workstation scannt
  - Offen: Ein lokaler Pfad (z. B. `D:\Ingest`) existiert auf jeder Workstation. Welcher Worker scannt ihn?
    - a) Im Watchfolder wird ein bestimmter Worker ausgewählt (Pflicht bei lokalem Pfad)
    - b) Jeder gestartete Worker mit der Fähigkeit Watchfolder scannt seinen eigenen lokalen Ordner
    - c) Der Worker, bei dem Watchfolder am weitesten oben steht
  - Offen: Die Datei eines lokal gefundenen Assets liegt nur auf dieser Workstation. Gehen alle Folgejobs dieses Assets an genau diesen Worker, bis ein Modul die Datei auf einen Netzwerkpfad gebracht hat?
    - a) Ja, gebunden an diesen Worker, bis die Datei auf einem Netzwerkpfad liegt
    - b) Nein, normal nach Fähigkeiten verteilen
- [ ] Gefundene Assets gehen sofort in den nächsten Node und stehen dort auf „wartet“ oder werden direkt verarbeitet; am Watchfolder gibt es nur „erfasst“, keine Spalte „wartet“
- [ ] Warten: Assets vom Watchfolder stehen in der Spalte „wartet“, solange ihre Wartezeit läuft (heute bleibt die Spalte leer); nach Ablauf zählen sie am Warten-Modul als erledigt und gehen über „Erfolg“ weiter
- [ ] modul watchfolder: erfasst und neu als status: neu ist immer, was im lauf neu gefunden wurde, sobald lauf beendet ist, gehen die Assets in erfasst über - reihenfolge umstellen: neu links, erfasst rechts - wartet entfällt

---
---

## Backlog

### Engine und Worker

- [ ] Worker-Client: Client-Hülle für Windows (Starter, Einstellungen inkl. erlaubter Modultypen, Auto-Update mit Rückfall, später Zeitpläne) um die Worker-Laufzeit aus dem Release Engine
- [ ] Rollout von Client-Versionen
- [ ] Pfadzuordnung je Worker – nötig mit dem Umstieg auf Linux (Server-Worker sieht `/mnt/...` statt `\\server\freigabe`)
- [ ] Erreichbare Speicherorte je Worker: Jobs nur an Worker, die die Pfade erreichen – zusammen mit der Pfadzuordnung, spätestens mit dem Worker-Client
- [ ] Sammeljobs: Ein Modul sammelt mehrere Assets nach einer Regel (z. B. gleicher Schlüssel) und veröffentlicht einen Job; Teilergebnisse je Asset

### Module

- [ ] Teilworkflows als Modul neben den Basis-Modulen, genannt Sub – zurückgestellt bis Engine und erste Fachmodule laufen
  - Anlegen: Module im Workflow markieren → „Als Sub speichern“; Eingang, Erfolg, Fehler ergeben sich aus der Auswahl
  - Freigegebene Optionen (z. B. QC-Preset) je Verwendung im Seitenpanel einstellbar
  - Doppelklick betritt den Sub; Änderungen gelten nur für diese Instanz
  - Vorlage ändern: Sub in leeren Workflow ziehen, auflösen, ändern, beim Speichern bestehenden Sub ersetzen
  - Verwendende Workflows bekommen einen Update-Hinweis zum Übernehmen; Instanz-Einstellungen bleiben, sonst Warnung
  - Offen: Was gilt bei einem Update, wenn die Instanz innen geändert wurde?
- [ ] Modulpanel mit ausklappbaren Kategorien zur Sortierung 
- [ ] Watchfolder: ersetzte Datei – altes Asset beenden („Quelldatei kann nicht mehr zugeordnet werden“), neues Asset mit Hinweis auf die verlorene Vorgängerdatei
  - Offen: Welcher Status ist „beendet“ – Erledigt oder Fehlgeschlagen?
- Geplante Module:
  - [ ] Transcode
    - Wahl von GPU oder CPU je Worker regelt das Modul
  - [ ] QC
  - [ ] Searcher
  - [ ] Restore
  - [ ] Mailversand
  - [ ] Report erstellen
  - [ ] API (EditShare, Veritone, ARK, awork) → später
  - [ ] Umbenennen: weitere Methoden
  - [ ] Prioliste: Metadaten je Eintrag bearbeiten, Verarbeitung nach Prio
  - [ ] Rechenintensive Module (Transcode, QC …) beim Einbau für den Server-Worker als letzte Wahl vorsehen – über die neue Fähigkeiten-Rangliste (siehe Geplant); `last_choice` entfällt

### Assets und Daten

- [ ] Assets hinzufügen: manuell und einfacher Listenimport
- [ ] Asset mit mehreren Dateien (z. B. OP-Atom, Interview aus vielen Einzeldateien): Eine Kopie wird eine weitere Datei des Assets statt eines neuen Assets – vertagt
  - Offen: Welche Datei verarbeiten Folgemodule standardmäßig? Wie wird nur die neue Kopie weiterverarbeitet (`asset_x.copy_a`)?
- [ ] Asset-Aktionen im Workflow und in der Asset-Ansicht: Nach oben, Abbrechen, Anhalten/Freigeben, Entfernen
- [ ] Aufbewahrung der Daten
  - Offen: Daten pro Workflow oder global aufheben?

### Oberfläche

- [ ] Bug-Report: echter Versand per Mail
  - Empfängeradresse und Mail-Zugang in der Konfiguration (keine Zugangsdaten im Code)
  - Server-Endpunkt zum Versenden; „Senden“ ruft ihn auf
  - Meldung bei fehlgeschlagenem Versand statt Erfolgsmeldung, Eingaben bleiben erhalten
  - Zusatzdaten anhängen: App-Version, aktuelle Seite, Browser, Zeitpunkt
  - Offen: Screenshot oder Logauszug mitschicken?

### Reports

- [ ] Reports ausbauen: weitere Filter und Kennzahlen

### Zugang und Sicherheit

- [ ] Andockstelle Login
- [ ] HTTPS-Verbindung
  - Offen: Braucht m2 das Zertifikat selbst, oder übernimmt das der übergeordnete Server/Reverse-Proxy?
- [ ] Workflows freigeben/sperren
  - Offen: Wie werden Workflows freigegeben und gesperrt?
  - Idee Ökosystem: Login auf einer Landingpage mit Übersicht der Tools, u. a. m2 → in m2 sind bestimmte Workflows gesperrt; ungern ein zweiter Login in m2
