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
   - [ ] Wo tragen wir die Fähigkeiten des Serverworkers ein? Wo tragen wir die Fähigkeiten anderer Worker ein? - für beides: gerne im UI Webapp
   - [ ] Wozu ist Worker -> Freigeben und Sperren?
   - [ ] Server-Worker soll standardmäßig "Server-Worker" heißen, nur einen Job parallel machen und oberste Prio für kopieren, verschieben und umbenennen bekommen - wo ändere ich das außerhalb des UIs?
   - [ ] Ordner-Dialog: wenn ich oben im Adressfeld den Pfad weitertippe, soll die Liste unten gefiltert werden
   - [ ] Die Angaben in den Nodes aktualisieren sich nicht in Echtzeit
   - [ ] Watchfolder zeigt nicht an, wieviele Dateien erfasst wurden - außerdem braucht das Modul keine Anzeige für läuft, erledigt und fehler
   - [ ] Watchfolder hat nie einen Job im Seitenmenü. Hier wäre ein Discover-Job sinnvoll
   - [ ] Watchfolder hat nie einen Worker - von wem wird der Watchfolder verarbeitet, die Engine oder der Server-Worker?
   - [ ]  Im Modul-Log - stehen dort nur Fehler oder sollten dort auch normale Statusmeldungen stehen? Im Moment steht dort nichts (geprüft an Modul "kopieren"
   - [ ] Detailinfos Asset - Size bitte besser lesbar mit Einheit (KB, MB, GB, TB - je nachdem, was sinnvoll ist)
   - [ ] Detailinfos Asset Metadaten: hier ist alles kleingeschrieben, bitte an Kopfdaten anpassen - mtime bitte umbenennen in was auch immer das bedeutet, Source_path umschreiben in Quellpfad
   - [ ] Wenn die Anzeige großer Listen länger als 0.5 sekunden dauert, bitte einen Spinner einfügen: drei Punkte, die als overlay nacheinander einblenden - der Spinner kommt auch, wenn die Sortierung eine Tabelle länger als 0.5 Sekunden dauert
   - [ ] Fähigkeiten Worker: last_choice überalle entfernen - die Fähigkeiten werden im UI hinterlegt und im Stile der Prioliste priorisiert. Wenn der Server-Worker nur als letzte Instanz transcodieren soll, steht bei ihm transcode als Letztes.
   - [ ] Meldung: Änderung betrifft laufende Assets: 1. Diese Überschrift in den Textrumpf und Warnung als neue Überschrift mit Ausrufezeichen-Symbol (das selbe wie bei den Workflows) - 2. klar benennen, was mit den betroffenen Assets und Jobs passiert: x Assets werden (abgebrochen, deaktiviert, fehlerhaft ...) - neue Zeile: x Jobs werden abgebrochen
   - [ ] in Workflow - Überwachen als weiteren Tab: einstellungen pro Modul - gleiches Menü wie bei Bearbeiten, aber ohne Edit-Funktion - dann sieht man gleich, wie das Modul eingestellt ist und kann es in einem sicheren Raum überprüfen, ohne etwas unbeabsichtig zu verändern. 
   - [ ] Grafik-Glitch: wenn ein Modul in Ansicht Überwachen aktiv ist, hat es einen blauen Balken zur Linken - der Balken liegt aber nicht über der Tabelle mit den Status - die horizontalen Trennlinen liegen über den Balken
   - [ ] der Workflow braucht zwei verarbeitungsmodi - offene Idee zur Diskussion: Asset-Fokusiert (Asset pro Asset wird durch den Worflow geschickt) oder Node-Fokusiert (jeder Node arbeitet seinen Queue ab, erstr dann kommt der nächste Node dran)
   - [ ] kopieren, verschieben brauchen einstellungen für parallel ausführung - aktuell geht das über jobs vom worker - besser: der worker mag 2 jobs machen können, kopieren mit 2 threads gilt dann als ein job, anschließendes umbenennen als ein 2. job
   - [ ] ansicht assets braucht neben ausstehend, in arbeit, ... noch ansicht "alle", das zeigt alle assets unabhängig vom Status an
   - [ ] zum thema kopie - offen zum vorschlag: wenn wir bereits zu assets abstrahiert haben, könnte ein asset auch mehrere Dateien besitzen (das ist zb. tatsächlich bei opatom dateien der fall) - somit entsteht bei einer kopie kein neues Asset, sondern das asset hat eine weitere, kopierte datei - das würde mit gut gefallen, da wir so besser komplizierte dateikonstrukte darstellen können (weiters beispiel: asset interview besteht aus vielen einzelnen dateien), allerdings brauchen wir ein praktikable lösung, wenn ich dann zb. nur die neu entstandene kopie weiterverarbeiten möchte - diese ist dann nicht asset_x, sondern asset_x.copy_a
   - [ ] Wenn module verbunden werden und ich vom erfolg zum eingangskreis vom modul ziehe, soll die linie am kreis snappen, auch wenn ich mit dem maus über dem modul bin - ausgenommen ist der erfolg oder fehler-ausgang
   - [ ] verbindungen können nur von erfolg zu modul-eingang gezogen werden - sind module verbunden und ich ziehe vom verbundenen moduleingang weg, löst sich die bestehende verbindung. gibt es mehrer eingangsverbindung, löst sich die zuletzt angelegte verbindung.
   - [ ] assets, die vom watchfolder entdeckt wurden, gelten als wartend, wenn nach dem Watchfolder kein weiteres modul kommt. Verstehen wir den Watchfolder als ein Indexer, der Assets sammeln kann, diese aber selbst nicht erledigt
   - [ ] in den Asset-Meldungen sollen meldungen vom typ fehler ebenfalls die rote schrift bekommen
