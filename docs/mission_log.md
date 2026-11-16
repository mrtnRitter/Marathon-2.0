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
7. **Ausfälle** (Worker stürzt ab, ist offline, Server startet neu): wie Pausieren – Asset zurück, Job wird neu vergeben.
8. **Workflow ändern während des Laufs:** Laufende Jobs behalten ihre Einstellungen; danach gilt die neue Version.
9. **Erneut verarbeitete Assets** (ohne aktuelles Modul) starten hinter dem Quellmodul.
10. **Neue Assets aus Modulen** (z. B. die Kopie aus „Datei kopieren“) werden am erzeugenden Modul angelegt und laufen über dessen Ausgang weiter; das Original ist dort erledigt.
11. **Datenbank:** Jobs speichern die Einstellungen ihres Moduls, die Ablaufzeit der Zuteilung, den Fehlertext und das Modul, von dem das Asset kam; je Asset im Job der gemeldete Ausgang.
12. **Schnittstelle für Worker** (versioniert, später auch für den Worker-Client): anmelden, Lebenszeichen, Job holen, Fortschritt und Log melden, Ergebnis melden, Abbruch erfahren.

#### Fragen an Martin

- [x] **Wo laufen Worker-Module, solange es noch keinen Worker-Client gibt?** → Server-Worker (siehe Entschieden)
- [x] **Jobverteilung** → Server teilt zu, Worker holt ab (siehe Entschieden)
- [ ] **Unterbrochene Jobs** (Absturz, Neustart; bei Dateien z. B. eine halbe Kopie)
  - a) Wie Pausieren neu einreihen, höchstens 3 Versuche am selben Modul, danach Ausgang Fehler (Empfehlung)
  - b) Sofort Fehlgeschlagen mit Hinweis „unterbrochen“, erneut verarbeiten von Hand
  - c) Nur „Warten“ neu einreihen, bei Dateimodulen sofort Fehlgeschlagen
- [ ] **Bevorzugter Worker belegt:** Job geht an einen anderen passenden Worker (Empfehlung) oder wartet auf den bevorzugten?
- [ ] **Verteilung zwischen Workflows:** reihum (Empfehlung) oder eine Workflow-Priorität wieder einführen?
- [ ] **Workflow ändern während des Laufs:** speichern erlaubt, neue Version gilt ab dem nächsten Modul (Empfehlung) – oder nur im pausierten Zustand speichern?
- [ ] **Startmodul erneut verarbeiteter Assets:** hinter dem Quellmodul; gibt es keins, das einzige Modul ohne eingehende Verbindung; nicht eindeutig → Fehlgeschlagen mit Meldung. Passt das?

#### Von CodeZilla entschieden (zur Prüfung)

- Ein Ergebnis, das vor dem Abbruch schon fertig war, wird übernommen (sonst stünde nach einem erledigten Verschieben der alte Pfad am Asset)
- Ein spätes Ergebnis eines abgebrochenen Jobs wird verworfen
- Watchfolder und Prioliste haben keinen Eingang; für sie gilt die Pflicht zu Erfolg und Fehler nicht. „Warten“ bekommt einen Ausgang Fehler
- Ein Modul, das mit einem Fehler abbricht, gibt das Asset über den Ausgang Fehler weiter; die Meldung kommt in den Log
- Fehlt das Modul eines Assets in der neuen Workflow-Version: Fehlgeschlagen mit „Modul nicht mehr im Workflow“
- Ein Worker gilt nach 30 s ohne Meldung als ausgefallen
- Die Prioliste wird von der Engine vorerst übergangen (eigener Backlog-Punkt „Verarbeitung nach Prio“)
- Erlaubte Aufgaben stellt der Rechner am Client ein (Server-Worker: `config/server.toml`); Vorrang, Parallelität und Sperren in der Worker-Ansicht; es gilt die Schnittmenge
- Der Server-Worker beendet sich, wenn der Server-Prozess nicht mehr läuft (nie zwei Server-Worker nach einem Neustart)
- Mit dem Umstieg auf Linux wird die Pfadzuordnung je Worker nötig (Server-Worker sieht `/mnt/...` statt `\\server\freigabe`)
- Parallelität und Ablaufzeit der Zuteilung stehen im neuen Abschnitt `[engine]` in `config/server.toml`
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
