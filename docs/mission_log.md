# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`

### Abnahme offen

- [] Assets neu verarbeiten: Option: in einem anderen Workflow
- [] Ausführungsvertrag für Module (`run(job)`); Dateien bewegt später der Worker, angeschlossen wird mit der Engine - ist das noch aktuell?
- [] Der Server teilt zu, der Worker holt ab: Seine Anfrage bleibt ca. 25 s offen; Verbindungen nur vom Worker zum Server
- [] Nicht innerhalb der Abhol-Frist abgeholt: Der Job geht zurück und wird neu vergeben
- [] Worker-Auswahl nach Vorrang je Worker und Modultyp (Bevorzugt, Normal, Letzte Wahl, Aus); ist der bevorzugte belegt, übernimmt ein anderer mit gleichen Fähigkeiten
- [] Es gilt die Schnittmenge: Modultypen, die der Rechner meldet, ohne Vorrang „Aus“; parallele Jobs = kleinerer Wert aus Rechner und Worker-Ansicht
- [] Zwischen Workflows reihum, keine Workflow-Priorität; im Workflow zuerst das Asset, das am weitesten oben steht
- [] Worker-Schnittstelle, versioniert (`/api/v1/worker/…`): anmelden, Job holen (mit Lebenszeichen und Abbruch), Start, Fortschritt und Log, Ergebnis; Zugang per Token
- [] Ein Worker gilt nach 30 s ohne Meldung als ausgefallen
- [] Absturz oder Ende = Worker offline, Jobs werden neu vergeben, der Server läuft weiter`
- [] Unterbrochener Job (Absturz, Worker offline, Neustart): Das Asset geht zurück auf den Ausgang des vorherigen Moduls und wird von dort neu verteilt
- [] Nach 3 unterbrochenen Versuchen am selben Modul *Fehlgeschlagen*; der Log zeigt die Versuche
- [] Als Versuch zählt nur ein Job, der schon lief; Pausieren und abgelaufene Abhol-Frist zählen nicht; der Zähler beginnt neu, wenn das Asset das Modul verlässt oder erneut verarbeitet wird
- [] Server-Neustart: Jobs auf anderen Workern laufen weiter; meldet sich der Worker innerhalb von 30 s, wird sein Ergebnis übernommen
- [] Ein vor dem Abbruch fertiges Ergebnis wird übernommen; ein spätes Ergebnis eines abgebrochenen Jobs wird verworfen
- [] Prioliste: Menüpunkt „Metadaten bearbeiten“ mit Stift-Icon
- [] Ordner-Dialog: Weitertippen im Adressfeld filtert die Liste (Pfad beginnt mit der Eingabe, ohne Groß-/Kleinschreibung); „Kein passender Ordner“, wenn nichts passt
- [] Überwachen: Zähler in den Nodes aktualisieren sich alle 2 s (bis zum Live-Kanal)
- [] Watchfolder-Node zeigt „erfasst“ (Dateien mit noch vorhandenem Asset) und „wartet“; kein „läuft“, „erledigt“, „Fehler“
- [] Server-Worker: Standardname „Server-Worker“ (gilt bei der ersten Anmeldung), Standard 1 paralleler Job (`[server_worker] max_parallel = 1`)
- [] `last_choice` überall entfernt (Konfiguration, Worker-Anmeldung, Worker-Laufzeit)
- [] Asset-Detail: Größe mit Einheit (B, KB, MB, GB, TB; Basis 1024); Metadaten „Quellpfad“, „Größe“, „Datei geändert“ (statt mtime), übrige mit großem Anfangsbuchstaben
- [] Asset-Meldungen vom Typ Fehler in roter Schrift
- [] Spinner (drei Punkte, nacheinander eingeblendet) über Tabellen, wenn Laden oder Sortieren länger als 0,5 s dauert
- [] Assets: Reiter „Alle“ vor den Status-Reitern
- [] Warnung beim Speichern: Überschrift „Warnung“ mit gelbem Warndreieck; Text nennt, wie viele Assets neu verteilt, erledigt oder fehlgeschlagen werden und wie viele Jobs abgebrochen werden, dazu die betroffenen Module
- [] Überwachen: Reiter „Einstellungen“ im Modul-Seitenmenü, nur lesen
- [] Grafik-Glitch: blauer Balken aktiver Module liegt über den Trennlinien
- [] Verbinden: Linie rastet am Eingang ein, sobald die Maus über dem Ziel-Modul ist (nicht über dessen Ausgangsleiste)
- [] Verbindungen nur vom Ausgang (Erfolg und Fehler) zum Eingang; Ziehen am verbundenen Eingang löst die zuletzt angelegte Verbindung (nach dem Neuladen gilt die gespeicherte Reihenfolge)

---
---

## Geplant

Nächster Release: offene Punkte aus „unsortiert von Martin“ klären

- [ ] Ordner-Dialog: Button „Laufwerke“ als Umschalter Laufwerke / Server; die Server-Adresse wird in den Einstellungen fest hinterlegt und als `\\<Server-IP>` benutzt (Anzeige der Freigaben funktioniert schon)
  - Offen: Die Einstellungsseite speichert noch nichts. Server-Adresse in `config/marathon.toml` (gilt nach Server-Neustart) oder als erste echte Einstellung in der Datenbank, änderbar in der Oberfläche?
- [ ] Fähigkeiten der Worker (Server-Worker und alle anderen) in der Oberfläche pflegen und im Stil der Prioliste priorisieren; steht Transcode beim Server-Worker zuletzt, transcodiert er nur als letzte Instanz
  - Heute: Die Fähigkeiten meldet der Worker selbst (alle Module, die ein Worker ausführt). Auf der Worker-Seite unter „Bearbeiten“ gibt es je Modultyp einen Vorrang: Bevorzugt, Normal, Letzte Wahl, Aus.
  - Offen: Wie vergleicht die Zuteilung die Ranglisten verschiedener Worker? Beispiel: Worker A [Kopieren, Transcode], Server-Worker [Kopieren, Umbenennen, Transcode] – wer bekommt einen Transcode-Job?
  - Offen: Ersetzt die Reihenfolge die vier Vorrang-Stufen ganz, und heißt „nicht in der Liste“ = Aus?
  - Offen: Server-Worker ab Werk mit oberster Prio für Kopieren, Verschieben und Umbenennen – ergibt sich aus der Antwort oben.
- [ ] Server-Worker außerhalb der Oberfläche einstellen
  - Heute: `config/marathon.toml` → `[server_worker] max_parallel` begrenzt die parallelen Jobs, auch beim schon angemeldeten Server-Worker. Name und Vorrang lassen sich nur in der Oberfläche ändern; der Name aus dem Code gilt nur bei der ersten Anmeldung.
  - Offen: Sollen Name und Prio auch in die Konfiguration? Was gilt, wenn Konfiguration und Oberfläche voneinander abweichen?
- [ ] Worker „Freigeben“ und „Sperren“
  - Antwort: Ein neuer Worker (Status „neu“) bekommt erst nach „Freigeben“ Jobs; so holt sich kein Rechner Jobs, nur weil er das Token kennt. „Sperren“ nimmt einen Worker aus der Zuteilung (z. B. Wartung, Fehlverhalten); laufende Jobs laufen weiter. Der Server-Worker ist automatisch freigegeben.
  - Offen: So lassen oder ändern?
- [ ] Watchfolder: Wer verarbeitet ihn – Engine oder Server-Worker? Discover-Job im Seitenmenü
  - Heute: Den Ordner scannt der Server selbst in einem eigenen Hintergrunddienst neben der Engine, ohne Worker und ohne Job. Deshalb zeigt das Seitenmenü nie einen Job und nie einen Worker.
  - Offen: Soll das Scannen ein Discover-Job auf einem Worker werden? Dann gelten die Pfade aus Sicht des Workers (passt zu Pfadzuordnung und erreichbaren Speicherorten im Backlog). Was passiert, wenn kein Worker frei ist?
- [ ] Watchfolder als Indexer: Assets ohne Folgemodul gelten als wartend statt erledigt
  - Offen: Welcher Status genau – „Ausstehend“ oder „In Arbeit“ mit Schrittstatus „wartet“ am Watchfolder?
  - Offen: Zeitgesteuert gestartete Workflows pausieren sich, sobald kein Asset mehr aussteht oder in Arbeit ist. Mit wartenden Watchfolder-Assets würden sie nie pausieren – soll der Watchfolder dabei nicht mitzählen?
- [ ] Modul-Log: nur Fehler oder auch normale Statusmeldungen?
  - Heute: Im Modul-Log stehen nur Meldungen des Moduls, die zu keinem Job gehören (z. B. Watchfolder „Ordner nicht erreichbar“, „Workflow pausiert: Einstellungen prüfen“). Job-Meldungen wie „Kopiert nach …“ stehen im Log des Assets; deshalb ist das Log von „Datei kopieren“ leer.
  - Offen: Sollen auch Job-Meldungen im Modul-Log erscheinen – alle oder nur Warnungen und Fehler, mit Asset-Namen?
- [ ] Zwei Verarbeitungsmodi je Workflow: Asset-fokussiert (Asset für Asset durch den Workflow) oder Node-fokussiert (jeder Node arbeitet seine Warteschlange ab, erst dann kommt der nächste Node dran)
  - Heute: Jedes Asset läuft für sich weiter, sobald es fertig ist; alle Module arbeiten gleichzeitig, soweit Worker frei sind.
  - Offen: Startet Node-fokussiert das nächste Modul erst, wenn am vorherigen nichts mehr wartet oder läuft? Was gilt für Assets, die währenddessen neu vom Watchfolder kommen? Wird der Modus je Workflow eingestellt?
- [ ] Kopieren und Verschieben parallel: Einstellung im Modul, z. B. Kopieren mit 2 Threads gilt als ein Job, anschließendes Umbenennen als 2. Job; der Worker macht 2 Jobs
  - Heute: Ein Job verarbeitet ein Asset; parallel wird über die parallelen Jobs des Workers.
  - Offen: Heißt „2 Threads“, dass ein Job mehrere Assets gleichzeitig kopiert (wie Sammeljobs im Backlog), oder dass eine Datei schneller in mehreren Strömen kopiert wird?
- [ ] Asset mit mehreren Dateien (z. B. OP-Atom, Interview aus vielen Einzeldateien): Eine Kopie wird eine weitere Datei des Assets statt eines neuen Assets
  - Heute: Die Kopie wird ein neues Asset (Log „Kopie von …“).
  - Offen: Welche Datei verarbeiten Folgemodule standardmäßig? Wie wird nur die neue Kopie weiterverarbeitet (`asset_x.copy_a`)?

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
  - [ ] Rechenintensive Module (Transcode, QC …) beim Einbau für den Server-Worker als letzte Wahl vorsehen – über die neue Fähigkeiten-Rangliste (siehe Nächster Release); `last_choice` entfällt

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
