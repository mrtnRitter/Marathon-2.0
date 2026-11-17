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

---
---

## Geplant

### Release: Engine (Detailkonzept, Entscheidungen offen) – nach dem Feinschliff

Umsetzungsvertrag: `docs/engine_spec.md`

#### Vorgaben von Martin

- [ ] Engine: Jobs veröffentlichen, zuteilen, Ergebnisse auswerten
- [ ] Zuteilung durch den Server: Marathon entscheidet, welcher Worker welchen Job bekommt (z. B. Transcode gezielt an einen Worker, Dateioperationen an einen anderen)
- [ ] Workflow pausieren bricht alle Bearbeitungen sofort ab
  - Teilweise fertige Dateien werden verworfen
  - Das Asset rutscht zum vorherigen Modul zurück, an dem es als fertig stand
  - Dauert das Abbrechen länger als 1 s: Meldung, dass der Workflow gerade pausiert wird; alle Play/Pause-Buttons zeigen „Wird pausiert“ und sind gesperrt
- [ ] Jedes Modul hat immer die Ausgänge Erfolg und Fehler; ohne Modul am Fehlerausgang ist das Asset fehlgeschlagen und beendet
- [ ] Jeder Workflow hat seine eigene Asset-Liste; die Assets werden je Workflow verwaltet
- [ ] Warten ist kein Job für Worker
  - Warten entspricht `time.sleep(n)` und wird von der Engine gesteuert; das Asset bleibt während der Wartezeit unverändert und läuft danach weiter
  - Warten hat keinen Ausgang Fehler (Ausnahme von der Pflicht zu Erfolg und Fehler)
- [ ] `config/server.toml` wird in `config/marathon.toml` umbenannt (mit allen Verweisen in Code und Doku)
- [ ] Jobsteuerung als endlicher Zustandsautomat: Jedes Asset steht jederzeit an genau einem Modul in genau einem definierten Zustand; es gibt nur festgelegte Übergänge

#### Entschieden

- [ ] **Server-Worker:** Das Server-Paket enthält die Worker-Laufzeit; sie läuft als eigener Prozess und spricht wie jeder andere Worker nur über die Worker-Schnittstelle (`localhost`)
  - Kein Sonderweg in der Engine; Absturz oder Ende des Server-Workers = Worker offline, Jobs werden neu vergeben, der Server läuft weiter
  - Der Server startet und überwacht ihn selbst (Neustart nach Absturz mit wachsender Wartezeit, geordneter Stopp beim Server-Ende); gleich unter Windows und Linux
  - Kein Auto-Update; er wird mit dem Server aktualisiert
  - Worker-Client = Worker-Laufzeit (ohne Oberfläche, plattformunabhängig) + Client-Hülle für Windows-PCs (Starter, Einstellungen, Auto-Update, später Zeitpläne)
- [ ] **Jobverteilung:** Der Server teilt zu, der Worker holt ab
  - Der Server verteilt Jobs auf freie Plätze von Workern, die online sind; die Oberfläche zeigt sofort „zugeteilt an Worker X“
  - Der Worker hält eine Anfrage beim Server offen (ca. 25 s); der Server antwortet sofort mit Job oder Abbruch, sonst mit „nichts“, danach fragt der Worker neu
  - Verbindungen gehen nur vom Worker zum Server; keine offenen Ports auf Worker-PCs
  - Keine Warteschlange im Worker: nie mehr zugeteilte Jobs als freie Plätze
  - Holt der Worker einen zugeteilten Job nicht innerhalb der Abhol-Frist ab, geht der Job zurück und wird neu vergeben
- [ ] **Unterbrochene Jobs** (Absturz, Worker offline, Neustart): Das Asset geht zurück auf den Ausgang des vorherigen Moduls (Erfolg oder Fehler, je nachdem, worüber es kam) und wird von dort nach dem Workflow neu verteilt
  - Nach 3 erfolglosen Versuchen am selben Modul ist das Asset *Fehlgeschlagen*; der Log zeigt die Versuche an diesem Modul
  - Meldet das Modul selbst einen Fehler, ist das keine Unterbrechung: Ausgang Fehler, kein neuer Versuch
- [ ] **Worker-Auswahl:** Ist der bevorzugte Worker belegt, geht der Job an einen anderen Worker mit gleichen Fähigkeiten
  - Reihenfolge nach Vorrang je Worker und Modultyp, z. B. für Transcode: GPU-Rechner → CPU-Rechner → Server-Worker
  - Keine Sonderregel für den Server-Worker in der Engine; sein Verhalten ergibt sich aus seinen Standardwerten in `config/marathon.toml`
  - Standard Server-Worker: Dateimodule (Kopieren, Verschieben, Umbenennen) mit normalem Vorrang; rechenintensive Module und Fachmodule (Transcode, QC, alles mit FFmpeg o. Ä.) nur als letzte Wahl
- [ ] **Verteilung zwischen Workflows:** reihum, keine Workflow-Priorität; einen Workflow bevorzugt man, indem man die anderen pausiert
- [ ] **Workflow ändern während des Laufs:** jederzeit speichern
  1. Jedes Speichern erzeugt auf einen Schlag eine neue Version; die Engine sieht nie einen halben Stand
  2. Module behalten über alle Versionen ihre feste Kennung; Assets merken sich die Kennung
  3. Laufende Jobs behalten die Einstellungen, mit denen sie gestartet sind
  4. Wohin ein Asset nach einem Modul geht, bestimmt die Version, die beim Eintreffen des Ergebnisses gilt
  5. Wird ein Modul gelöscht, an dem Assets stehen: Laufende Jobs werden wie beim Pausieren abgebrochen; die Assets gehen zurück auf den Ausgang des vorherigen Moduls und laufen nach der neuen Version weiter. Fehlt auch das vorherige Modul: *Fehlgeschlagen* mit „Modul nicht mehr im Workflow“
  6. Vor dem Speichern zeigt der Server die Auswirkungen (z. B. „Transcode gelöscht: 4 Assets warten, 2 Jobs werden abgebrochen“); Speichern erst nach Bestätigung. Ohne Auswirkungen keine Rückfrage
- [ ] **Erneut verarbeiten:** Das Asset beginnt immer am Anfang des Workflows, als wäre es gerade neu hereingekommen
  - Hinter dem Quellmodul (Watchfolder, Prioliste) über dessen Ausgang Erfolg
  - Ohne Quellmodul am Modul ohne eingehende Verbindung; gibt es mehrere solche Module: *Fehlgeschlagen* mit „Startpunkt nicht eindeutig“

#### Offen zur Jobsteuerung

- [] Zustandstabelle für Asset, Job, Worker und Workflow: `docs/engine_spec.md`, Abschnitt 4
- [ ] Transcode: Wie GPU- und CPU-Verarbeitung je Worker gewählt wird, klärt das Transcode-Modul
- [ ] Erreichbarkeit der Pfade: Ein Worker darf nur Jobs bekommen, deren Pfade er erreicht (z. B. nur Netzlaufwerk). Vorschlag: je Worker erreichbare Speicherorte, zusammen mit der Pfadzuordnung – spätestens mit dem Worker-Client; solange nur der Server-Worker arbeitet, nicht nötig

#### Konzeptvorschlag (CodeZilla)

1. **Zuteilung:** entschieden (siehe Entschieden: Jobverteilung)
2. **Zwei Modularten:**
   - Server-Module (Warten, später Bedingung, Sammeln) erledigt die Engine selbst. Warten ist nur eine Zeitmarke am Asset und belegt keinen Worker.
   - Worker-Module (Dateien, Transcode, QC …) gehen als Job an einen Worker. Der Modulkatalog kennzeichnet, wo ein Modul läuft.
3. **Worker-Einstellungen:** erlaubte Modultypen (Fähigkeiten), Anzahl paralleler Jobs, optional Vorrang je Modultyp (z. B. „bevorzugt für Transcode“).
4. **Reihenfolge:** Wird bei einem Worker ein Platz frei, kommen die gestarteten Workflows reihum dran. Innerhalb eines Workflows ist das Asset zuerst dran, das am weitesten oben in der Liste steht.
5. **Weg eines Assets:** Ein Asset steht immer an einem Modul und ist dort *wartet*, *läuft* oder *fertig*. Ist es fertig, bestimmt der Ausgang das nächste Modul. Hängt am Ausgang nichts mehr: nach Erfolg *Erledigt*, nach Fehler *Fehlgeschlagen*.
6. **Pausieren:** Zustände *Läuft* → *Wird pausiert* → *Pausiert*.
   - Sofort keine neuen Jobs, Abbruchsignal an alle laufenden Jobs
   - Module verwerfen halb fertige Dateien (Kopieren und Verschieben tun das schon)
   - Asset geht zurück auf *fertig* am vorherigen Modul; beim nächsten Start läuft das abgebrochene Modul neu
   - *Pausiert* ist erreicht, wenn jeder Job den Abbruch bestätigt hat oder sein Worker 30 s nichts gemeldet hat
   - Bis der Live-Kanal kommt, fragt die Seite den Zustand im Sekundentakt ab
7. **Ausfälle:** entschieden (siehe Entschieden: Unterbrochene Jobs)
8. **Workflow ändern während des Laufs:** entschieden (siehe Entschieden)
9. **Erneut verarbeitete Assets:** entschieden (siehe Entschieden: Erneut verarbeiten)
10. **Neue Assets aus Modulen** (z. B. die Kopie aus „Datei kopieren“) werden am erzeugenden Modul angelegt und laufen über dessen Ausgang weiter; das Original ist dort erledigt.
11. **Datenbank:** Jobs speichern die Einstellungen ihres Moduls, die Ablaufzeit der Zuteilung, den Fehlertext und das Modul, von dem das Asset kam; je Asset im Job der gemeldete Ausgang.
12. **Schnittstelle für Worker** (versioniert, später auch für den Worker-Client): anmelden, Lebenszeichen, Job holen, Fortschritt und Log melden, Ergebnis melden, Abbruch erfahren.

#### Fragen an Martin

- [x] **Wo laufen Worker-Module, solange es noch keinen Worker-Client gibt?** → Server-Worker (siehe Entschieden)
- [x] **Jobverteilung** → Server teilt zu, Worker holt ab (siehe Entschieden)
- [x] **Unterbrochene Jobs** → siehe Entschieden
- [x] **Bevorzugter Worker belegt** → siehe Entschieden: Worker-Auswahl
- [x] **Verteilung zwischen Workflows** → siehe Entschieden
- [x] **Workflow ändern während des Laufs** → siehe Entschieden
- [x] **Startmodul beim erneuten Verarbeiten** → siehe Entschieden

#### Von CodeZilla entschieden (zur Prüfung)

- Ein Ergebnis, das vor dem Abbruch schon fertig war, wird übernommen (sonst stünde nach einem erledigten Verschieben der alte Pfad am Asset)
- Ein spätes Ergebnis eines abgebrochenen Jobs wird verworfen
- Watchfolder und Prioliste haben keinen Eingang; für sie gilt die Pflicht zu Erfolg und Fehler nicht
- Pausieren während Warten: Das Asset geht wie bei jedem Modul zurück auf den Ausgang des vorherigen Moduls; die Wartezeit beginnt beim nächsten Start neu
- Ein Modul, das mit einem Fehler abbricht, gibt das Asset über den Ausgang Fehler weiter; die Meldung kommt in den Log
- Unterbrochene Jobs: Als Versuch zählt nur ein Job, der schon lief; Pausieren und eine abgelaufene Abhol-Frist zählen nicht
- Der Zähler der Versuche beginnt neu, sobald das Asset das Modul verlässt oder erneut verarbeitet wird
- Server-Neustart: Jobs auf anderen Workern laufen weiter; meldet sich der Worker innerhalb von 30 s, wird sein Ergebnis übernommen, sonst gilt der Job als unterbrochen
- Ein Worker gilt nach 30 s ohne Meldung als ausgefallen
- Die Prioliste wird von der Engine vorerst übergangen (eigener Backlog-Punkt „Verarbeitung nach Prio“)
- Erlaubte Aufgaben stellt der Rechner am Client ein (Server-Worker: `config/marathon.toml`); Vorrang, Parallelität und Sperren in der Worker-Ansicht; es gilt die Schnittmenge
- Der Server-Worker beendet sich, wenn der Server-Prozess nicht mehr läuft (nie zwei Server-Worker nach einem Neustart)
- Mit dem Umstieg auf Linux wird die Pfadzuordnung je Worker nötig (Server-Worker sieht `/mnt/...` statt `\\server\freigabe`)
- Parallelität und Ablaufzeit der Zuteilung stehen im neuen Abschnitt `[engine]` in `config/marathon.toml`
- Nicht im Release Engine: Live-Kanal, Worker-Client mit Auto-Update, Asset-Aktionen (Abbrechen, Anhalten, nach oben), Verarbeitung nach Prioliste, Sammeljobs
- Ersetzt durch Martins Vorgaben: „Warten läuft als normaler Job“, „Pausieren lässt laufende Jobs fertig laufen“, „Jobs über alle Workflows hinweg nach Alter“

---
---

## Backlog (Reihenfolge offen)

### Engine und Worker

- [ ] Live-Kanal zum Browser
- [ ] Worker-Client: Client-Hülle für Windows (Starter, Einstellungen, Auto-Update mit Rückfall) um die Worker-Laufzeit aus dem Release Engine
- [ ] Rollout von Client-Versionen

### Module

- [ ] Module über Engine und Worker ausführen (Vertrag `run(job)` steht)
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
  - [ ] QC
  - [ ] Searcher
  - [ ] Restore
  - [ ] Mailversand
  - [ ] Report erstellen
  - [ ] API (EditShare, Veritone, ARK, awork) → später
  - [ ] Umbenennen: weitere Methoden
  - [ ] Prioliste: Metadaten je Eintrag bearbeiten, Verarbeitung nach Prio

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
