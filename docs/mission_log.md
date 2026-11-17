# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`

### Abnahme offen

- [] Assets neu verarbeiten: Option: in einem anderen Workflow
- [] Watchfolder
  - Pfad per Ordner-Dialog oder direkt; Netzpfade `\\server\freigabe`, Laufwerksbuchstaben erlaubt
  - Ordnerfehler einmal im Modul-Log, danach „Ordner wieder erreichbar“
- [] Ausführungsvertrag für Module (`run(job)`); Dateien bewegt später der Worker, angeschlossen wird mit der Engine

#### Release Engine

Umsetzungsvertrag: `docs/engine_spec.md`

**Konfiguration**

- [] `config/server.toml` heißt `config/marathon.toml` (alle Verweise in Code und Doku)
- [] Abschnitt `[engine]`: Takt, Anfragedauer der Worker (25 s), Ausfallzeit (30 s), Abhol-Frist (10 s), Versuche (3)
- [] Abschnitt `[server_worker]`: an/aus, parallele Jobs (2), Modultypen nur als letzte Wahl (`last_choice`)

**Jobsteuerung**

- [] Engine veröffentlicht Jobs, teilt sie zu und wertet die Ergebnisse aus
- [] Endlicher Zustandsautomat: Jedes Asset steht jederzeit an genau einem Modul in genau einem Zustand (*wartet*, *läuft*, *fertig*); nur festgelegte Übergänge
- [] Zustandstabellen für Asset, Job, Worker und Workflow: `docs/engine_spec.md`, Abschnitt 4
- [] Jedes Modul hat Erfolg und Fehler; hängt am Ausgang nichts: nach Erfolg *Erledigt*, nach Fehler *Fehlgeschlagen*
- [] Watchfolder und Prioliste haben keinen Eingang; für sie gilt die Pflicht zu Erfolg und Fehler nicht
- [] Jeder Workflow hat seine eigene Asset-Liste
- [] Der Modulkatalog kennzeichnet, wo ein Modul läuft: Worker (Kopieren, Verschieben, Umbenennen), Engine (Warten), nicht ausgeführt (Watchfolder, Prioliste)
- [] Kopieren, Verschieben und Umbenennen laufen als Job auf einem Worker (`run(job)`)
- [] Warten steuert die Engine: Zeitmarke am Asset, belegt keinen Worker, kein Ausgang Fehler
- [] Meldet ein Modul einen Fehler: Ausgang Fehler, Meldung im Log, kein neuer Versuch
- [] Neue Assets aus Modulen (z. B. die Kopie) entstehen am erzeugenden Modul und laufen über dessen Ausgang weiter; das Original ist dort erledigt
- [] Die Prioliste wird von der Engine vorerst übergangen

**Zuteilung**

- [] Der Server teilt zu, der Worker holt ab: Seine Anfrage bleibt ca. 25 s offen; Verbindungen nur vom Worker zum Server
- [] Nie mehr zugeteilte Jobs als freie Plätze, keine Warteschlange im Worker
- [] Nicht innerhalb der Abhol-Frist abgeholt: Der Job geht zurück und wird neu vergeben
- [] Job-Status in der Oberfläche: zugeteilt, läuft, wird abgebrochen, unterbrochen; Log „Zugeteilt an {Worker}“
- [] Worker-Auswahl nach Vorrang je Worker und Modultyp (Bevorzugt, Normal, Letzte Wahl, Aus); ist der bevorzugte belegt, übernimmt ein anderer mit gleichen Fähigkeiten
- [] Es gilt die Schnittmenge: Modultypen, die der Rechner meldet, ohne Vorrang „Aus“; parallele Jobs = kleinerer Wert aus Rechner und Worker-Ansicht
- [] Zwischen Workflows reihum, keine Workflow-Priorität; im Workflow zuerst das Asset, das am weitesten oben steht

**Worker**

- [] Worker-Schnittstelle, versioniert (`/api/v1/worker/…`): anmelden, Job holen (mit Lebenszeichen und Abbruch), Start, Fortschritt und Log, Ergebnis; Zugang per Token
- [] Worker-Laufzeit ohne Oberfläche, plattformunabhängig: `python -m marathon.worker --server <Basis-URL>`
- [] Neue Worker müssen freigegeben werden; Sperren: laufende Jobs laufen fertig, keine neuen
- [] Worker-Ansicht: Freigeben, Sperren, Name, parallele Jobs und Vorrang je Modultyp bearbeiten
- [] Ein Worker gilt nach 30 s ohne Meldung als ausgefallen

**Server-Worker**

- [] Eigener Prozess im Server-Paket; spricht wie jeder Worker nur über die Worker-Schnittstelle (`localhost`), kein Sonderweg in der Engine; sofort freigegeben
- [] Absturz oder Ende = Worker offline, Jobs werden neu vergeben, der Server läuft weiter
- [] Der Server startet und überwacht ihn (Neustart mit wachsender Wartezeit, geordneter Stopp), gleich unter Windows und Linux; Log `.data/server-worker.log`
- [] Er beendet sich, wenn der Server-Prozess nicht mehr läuft (nie zwei Server-Worker)
- [] Kein Auto-Update; er wird mit dem Server aktualisiert
- [] Dateimodule mit normalem Vorrang; rechenintensive Module nur als letzte Wahl über `last_choice` (noch leer, solche Module gibt es noch nicht)

**Unterbrechungen**

- [] Unterbrochener Job (Absturz, Worker offline, Neustart): Das Asset geht zurück auf den Ausgang des vorherigen Moduls und wird von dort neu verteilt
- [] Nach 3 unterbrochenen Versuchen am selben Modul *Fehlgeschlagen*; der Log zeigt die Versuche
- [] Als Versuch zählt nur ein Job, der schon lief; Pausieren und abgelaufene Abhol-Frist zählen nicht; der Zähler beginnt neu, wenn das Asset das Modul verlässt oder erneut verarbeitet wird
- [] Server-Neustart: Jobs auf anderen Workern laufen weiter; meldet sich der Worker innerhalb von 30 s, wird sein Ergebnis übernommen
- [] Ein vor dem Abbruch fertiges Ergebnis wird übernommen; ein spätes Ergebnis eines abgebrochenen Jobs wird verworfen

**Pausieren**

- [] Pausieren bricht alle Bearbeitungen sofort ab, keine neuen Jobs; halb fertige Dateien werden verworfen
- [] Das Asset geht zurück auf *fertig* am vorherigen Modul; beim nächsten Start läuft das abgebrochene Modul neu
- [] *Läuft* → *Wird pausiert* → *Pausiert*, sobald jeder Job den Abbruch bestätigt hat oder sein Worker 30 s nichts gemeldet hat
- [] Dauert es länger als 1 s: Hinweis „Workflow wird gerade pausiert“; alle Play/Pause-Buttons zeigen „Wird pausiert“ und sind gesperrt; die Seite lädt im Sekundentakt neu
- [] Pausieren während Warten: Die Wartezeit beginnt beim nächsten Start neu

**Workflow ändern während des Laufs**

- [] Jederzeit speichern; jedes Speichern erzeugt auf einen Schlag eine neue Version
- [] Module behalten ihre feste Kennung; laufende Jobs behalten die Einstellungen, mit denen sie gestartet sind
- [] Wohin ein Asset nach einem Modul geht, bestimmt die Version beim Eintreffen des Ergebnisses
- [] Gelöschtes Modul mit Assets: Laufende Jobs werden wie beim Pausieren abgebrochen, die Assets laufen vom vorherigen Modul nach der neuen Version weiter; fehlt auch dieses: *Fehlgeschlagen* mit „Modul nicht mehr im Workflow“
- [] Vor dem Speichern zeigt das Seitenpanel die Auswirkungen (je Modul Assets und abgebrochene Jobs); Speichern erst nach Bestätigung, ohne Auswirkungen keine Rückfrage

**Erneut verarbeiten**

- [] Das Asset beginnt immer am Anfang des Workflows: hinter dem Quellmodul über Erfolg, sonst am Modul ohne eingehende Verbindung; mehrere solche Module: *Fehlgeschlagen* mit „Startpunkt nicht eindeutig“

---
---

## Geplant

Nächster Release: noch offen

---
---

## Backlog (Reihenfolge offen)

### Engine und Worker

- [ ] Live-Kanal zum Browser (ersetzt das Neuladen im Sekundentakt beim Pausieren)
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
- [ ] Watchfolder in zeitgesteuert gestarteten Workflows (pausieren sich ohne ausstehende Assets sofort)
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
  - [ ] Rechenintensive Module (Transcode, QC …) beim Einbau in `[server_worker] last_choice` aufnehmen; `last_choice` muss dann auch beim schon angemeldeten Server-Worker greifen (gilt bisher nur bei der ersten Anmeldung)

### Assets und Daten

- [ ] Assets hinzufügen: manuell und einfacher Listenimport
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


### unsortiert von Martin
   - [] Prioliste: Icon für Metadaten bearbeiten
   - [ ] Ordner-öffnen Dialog: Button Laufwerke als Umschalter zwischen Laufwerke / Server - in den Settings lässt sich Serveradresse fest hinterlegen und wird dann als \\Server-IP benutzt - Anzeigen der Shares funktioniert prima!
