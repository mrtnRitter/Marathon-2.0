# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[ ]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`

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
- [ ] Ausführungsvertrag für Module: Jedes ausführbare Modul hat `run(job)` (Eingang: Asset-Pfad, Einstellungen, Stopp-Signal; Ausgang: Ergebnis-Ausgang, Log, neuer Pfad, neue Assets); der Worker ruft es auf und bewegt damit die Dateien. Nichts mehr zu tun
- [ ] Der Server meldet jede Änderung an Assets, Jobs, Workern, Workflows und Modulzählern sofort an alle offenen Browser; alle Ansichten zu Assets, Fortschritt, Workern und Workflows aktualisieren sich in Echtzeit und synchron
- [ ] Ersetzt das bisherige Abfragen im Takt (Überwachen alle 2 s, Pausieren jede Sekunde) und das Neuladen per Hand; nach einem Verbindungsabbruch verbindet sich der Browser selbst neu und lädt den aktuellen Stand
- [ ] Platzhalter „Live-Kanal“ (Server-Sent Events / WebSocket) entfällt aus den Einstellungen; der Live-Kanal nutzt fest Server-Sent Events

---
---

## Geplant

### Nächster Release: Worker-Steuerung, Watchfolder auf Workern, Live-Kanal

#### Einstellungen

- [ ] Die Einstellungsseite speichert in die Datenbank; erste echte Einstellung ist die Server-Adresse für den Ordner-Dialog
  - Standardwert im neuen Eintrag `[folder_dialog] server_address` in `config/marathon.toml`; `host` bleibt die Adresse, an der Marathon selbst erreichbar ist, und ist nicht in der Oberfläche änderbar
- [ ] Standardwerte stehen in `config/marathon.toml` und gehen beim Build in die Datenbank, sofern dort noch kein Wert steht; ab dann gilt die Datenbank, geändert wird in der Oberfläche
- [ ] Ordner-Dialog: Button „Laufwerke“ wird Umschalter Laufwerke / Server; „Server“ zeigt die Freigaben von `\\<Server-Adresse>`

#### Worker

- [ ] Die Vorrang-Stufen (Bevorzugt, Normal, Letzte Wahl, Aus) entfallen überall: Oberfläche, Datenbank, Zuteilung
- [ ] Fähigkeiten je Worker als sortierbare Liste im Stil der Prioliste; weiter oben = höhere Prio; nicht in der Liste = übernimmt diesen Modultyp nicht; wählbar sind nur Modultypen, die der Rechner meldet
- [ ] Zuteilung: Ein Job geht an den Worker mit freiem Platz, bei dem der Modultyp am weitesten oben steht. Beispiel: Worker A [Transcode, Kopieren], Worker B [Kopieren, Transcode] → Transcode-Jobs vorrangig an A, Kopieren an B; ist A voll, transcodiert B
- [ ] Gleicher Rang: zuerst der Worker, dem noch kein Job zugeteilt ist; sind beide frei, einer von beiden
- [ ] Server-Worker ab Werk mit den Fähigkeiten Watchfolder, Kopieren, Verschieben, Umbenennen; einstellbar unter `[server_worker]` in `config/marathon.toml` (Fähigkeiten, parallele Jobs); diese Standardwerte gehen beim Build in die Datenbank, danach gilt die Datenbank
- [ ] Alle Worker, auch der Server-Worker, werden in der Oberfläche eingestellt
- [ ] „Freigeben“ und „Sperren“ werden ein Button „Starten“ / „Stoppen“, der je nach Zustand wechselt (wie bei Workflows); auch direkt in der Worker-Liste
- [ ] Status „bereit“ oder „gestoppt“ (rot); beim Stoppen „Wird gestoppt“, bis alle Jobs abgebrochen sind; „online“ / „offline“ bleibt eine eigene Anzeige, die Auslastung zeigt die Spalte Jobs; „neu“, „beschäftigt“ und „gesperrt“ entfallen
- [ ] Ein neuer Worker meldet sich „gestoppt“ an und bekommt erst nach „Starten“ Jobs (wie bisher nach „Freigeben“); der Server-Worker startet automatisch
- [ ] Stoppen bricht alle laufenden Jobs des Workers ab, unfertige Ergebnisse werden verworfen, das Asset geht zurück auf den Ausgang des letzten Moduls, an dem es fertig war, und wird neu verteilt; der Abbruch zählt nicht als Versuch

#### Workflows

- [ ] Überall „Starten“ / „Stoppen“ statt „Starten“ / „Pausieren“; Status „aktiv“ oder „gestoppt“ (rot); „Wird gestoppt“ statt „Wird pausiert“
- [ ] Zeitgesteuerte Workflows: Der Workflow bleibt „aktiv“, auch während die Zeitsteuerung eine Pause vorsieht; nur aktive Workflows werden verarbeitet, auch ihre Pausen
  - In der Pause startet der Workflow keine neuen Jobs und der Watchfolder scannt nicht; zum nächsten Termin läuft er, bis kein Asset mehr aussteht oder in Arbeit ist, dann beginnt die nächste Pause
  - Das Selbst-Pausieren entfällt; „Stoppen“ beendet auch die Zeitsteuerung, bis der Workflow wieder gestartet wird
- [ ] Verarbeitung bleibt parallel: Jedes Asset läuft weiter, sobald es fertig ist, soweit Worker frei sind; die Idee der Asset-fokussierten Verarbeitung entfällt
- [ ] Überwachen: Doppelklick auf ein Modul wechselt zu Bearbeiten, das Modul ist markiert und das rechte Panel offen

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

#### Module

- [ ] Modul-Log: neben Fehlern auch jede Aktivität des Moduls (Zeit, Asset, Worker, Ergebnis); der Reiter „Jobs“ bleibt die Tabelle mit Status, Worker, Versuch und Fortschritt je Job
- [ ] Parallele Verarbeitung (statt „Threads“): mehrere Assets gleichzeitig, jedes Asset in einem Strom; einstellbar in Kopieren und Verschieben, später ggf. Transcode und API-Abrufe
  - Offen: Wie zählt z. B. der Wert 2 beim Worker?
    - a) Ein Job mit 2 Assets gleichzeitig, belegt 1 Platz beim Worker (wie das frühere Beispiel: Kopieren + Umbenennen = 2 Jobs)
    - b) Ein Job je Asset; der Wert begrenzt, wie viele Jobs dieses Moduls gleichzeitig laufen (belegt 2 Plätze)
- [ ] Prioliste: „Metadaten bearbeiten“ heißt „Bearbeiten“
- [ ] Verbindung zwischen Nodes: Anfang und Ende der Linie liegen am Rand des Kreises – heute beginnt die Linie am Ausgang „Erfolg“ etwas innerhalb des Kreises, beim Ziehen liegen Anfang und Ende im Modul
- [ ] Asset erneut verarbeiten: keine Beschränkung auf 1000 Assets

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


### unsortiert Martin

- [ ] 
