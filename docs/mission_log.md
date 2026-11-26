# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[ ]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Architektur & Vertrag: `docs/spec.md`

---

## Abnahme offen

### Release Worker-Steuerung

Vertrag: `docs/spec.md` (Teil 2)

- [ ] Neuer Worker meldet sich „gestoppt“ an; „Sofort-Aus“ (`stop-now`) bricht alle Jobs ab, Asset zurück an den letzten fertigen Ausgang, zählt nicht als Versuch (früheres `/stop`; „Stoppen“ ist seit Release Worker-Client & Steuerung das Auslaufen `stop-drain`)
- [ ] Ein Worker macht nur einen Job; ein Job bündelt bis zu N Assets desselben Moduls, bearbeitet in N Strömen, Ergebnis je Asset; N = „Parallele Verarbeitung“ (1–16) bei Kopieren und Verschieben


### Release Engine

Vertrag: `docs/spec.md` (Teil 2)

- [ ] Der Server teilt zu, der Worker holt ab: Seine Anfrage bleibt ca. 25 s offen; Verbindungen nur vom Worker zum Server
- [ ] Nicht innerhalb der Abhol-Frist abgeholt: Der Job geht zurück und wird neu vergeben
- [ ] Es gilt die Schnittmenge: Modultypen, die der Rechner meldet und die in seiner Fähigkeiten-Liste stehen
- [ ] Zwischen Workflows reihum, keine Workflow-Priorität; im Workflow zuerst das Asset, das am weitesten oben steht
- [ ] Worker-Schnittstelle, versioniert (`/api/v1/worker/…`): anmelden, Job holen (mit Lebenszeichen und Abbruch), Start, Fortschritt und Log, Ergebnis; Zugang per Token
- [ ] Ein Worker gilt nach 30 s ohne Meldung als ausgefallen
- [ ] Absturz oder Ende = Worker offline, Jobs werden neu vergeben, der Server läuft weiter
- [ ] Unterbrochener Job (Absturz, Worker offline, Neustart): Das Asset geht zurück auf den Ausgang des vorherigen Moduls und wird von dort neu verteilt
- [ ] Nach 3 unterbrochenen Versuchen am selben Modul *Fehlgeschlagen*; der Log zeigt die Versuche
- [ ] Als Versuch zählt nur ein Job, der schon lief; Pausieren und abgelaufene Abhol-Frist zählen nicht; der Zähler beginnt neu, wenn das Asset das Modul verlässt oder erneut verarbeitet wird
- [ ] Server-Neustart: Jobs auf anderen Workern laufen weiter; meldet sich der Worker innerhalb von 30 s, wird sein Ergebnis übernommen

### Release Worker-Client & Steuerung

Vertrag: `docs/spec.md` §3.1 (`[worker_client]`, `[client_build]`), §3.2 (Migration `0011_worker_client`), §3.4 (ohne Rechner-Erkennung im Browser); Worker-Protokoll 3. Hinweis: Die Exe lässt sich nur unter Windows bauen und dort real testen (`python build.py --client`); geprüft wurde in automatischen Tests. Ohne Zertifikat in `[client_build] sign_command` bleibt sie unsigniert und Windows warnt beim Start.

- [ ] Strikte Trennung von Nutzerkonto und Worker: eigenes Worker-Token je Rechner über `POST /api/v1/worker/enroll`, ohne Bindung an einen Nutzer
  - Entschieden: `[worker_client] require_admin_approval = true` ab Werk; der Admin wählt „Zulassen“
  - Entschieden: Neue Worker starten mit leerer Fähigkeiten-Liste; der Admin trägt sie ein, vorher keine Jobs
  - Entschieden: Der Admin kann Worker entfernen („Entfernen“ mit Bestätigung), nur ohne aktive Jobs; der Server-Worker ist nicht entfernbar. Ein entfernter Client verwirft seine Anmeldung, zeigt das im Tray und meldet sich beim nächsten Start neu an
  - Entschieden: Protokoll 2 wird abgelehnt („Protokollversion nicht unterstützt“), der Server-Worker spricht Protokoll 3
- [ ] Windows-Tray-Client als eine einzelne Exe, die sich selbst installiert: `%LOCALAPPDATA%\MarathonWorker`, Autostart, Eintrag unter „Apps“ mit Deinstallation, ohne Adminrechte; Stammzertifikat des Proxys in den Zertifikatsspeicher des Benutzers (bleibt bei Deinstallation)
  - Entschieden: Keine zweite Installer-Exe und kein fremdes Installer-Werkzeug; die heruntergeladene Exe fragt beim ersten Start „Marathon-Worker auf diesem Rechner installieren?“
  - Entschieden: Server-Adresse und Stammzertifikat werden beim Bauen eingebettet und von der Signatur abgedeckt; Bau mit `python build.py --client` bei laufendem Server (sonst Startwert aus `config/marathon.toml`). Der Download lehnt eine Exe für eine andere als die aktuelle Adresse ab; nach Adresswechsel neu bauen
  - Entschieden: Signieren über `[client_build] sign_command` (`{file}` = Exe), leer = unsigniert; Empfehlung Azure Artifact Signing (Beispiel in der README)
  - Entschieden: Download `GET /api/v1/client/download` ohne Anmeldung; Schaltfläche „Client herunterladen“ auf der Worker-Seite für alle angemeldeten Nutzer
  - Entschieden: Ohne eingebettetes Stammzertifikat prüft der Client gegen den Zertifikatsspeicher des Systems
  - Entschieden: Tray-Menü „Worker-Einstellungen öffnen“ (auch Doppelklick), „Status: …“, „Beenden“; Tooltip und Statuszeile ohne Server-Adresse
- [ ] Steuerung am Rechner ohne Marathon-Login: Einmal-Link (60 s) per Bestätigungs-`POST`, Seite „Worker-Einstellungen“ (`/worker-control`) mit „Starten“, „Stoppen“, „Sofort-Aus“ und Verfügbarkeit; gilt `control_session_hours` (12 Stunden)
  - Entschieden: Fähigkeiten dort nur lesend; die Seite aktualisiert sich alle 5 s
- [ ] Drei Steuerbefehle „Starten“ (Play), „Stoppen“ (Stop-Symbol weiß, Drain), „Sofort-Aus“ (Stop-Symbol rot); Endpunkte `start`, `stop-drain`, `stop-now`, `/stop` entfällt
  - Entschieden: Status `draining` „Wird gestoppt“, `stopping` „Wird sofort gestoppt“
  - Entschieden: „Starten“ während „Wird gestoppt“ → sofort „bereit“, laufende Jobs bleiben
  - Entschieden: „Stoppen“ gibt zugeteilte, noch nicht gestartete Jobs sofort zurück
- [ ] Verfügbarkeitsregeln zentral auf dem Server, ausgewertet im Client alle 5 s, gemeldet mit der nächsten Anfrage (bis ca. 25 s); Reihenfolge Zeitplan → Programme → Leerlauf
  - Entschieden: Uhrzeit des Rechners; leere Regel = immer verfügbar = Standard; ohne GPU-Sensor gilt die GPU-Bedingung als erfüllt
  - Entschieden: Zeitfenster `HH:MM`, Ende `24:00` erlaubt, über Mitternacht erlaubt, keine Überschneidungen am Tag (auch über Mitternacht), höchstens 10 je Tag; Programme ohne Pfad, Groß/Klein egal, `.exe` optional, höchstens 50
  - Entschieden: Leerlauf = die eingestellten Minuten ohne Tastatur/Maus und Mittelwert der Last über diese Minuten unter den Höchstwerten; danach deaktiviert nur eine Eingabe, dann wieder Lastbeobachtung
  - Entschieden: Verlust der Verfügbarkeit gibt zugeteilte, nicht gestartete Jobs zurück (kein Versuch); laufende Jobs laufen zu Ende
  - Entschieden: Kennung geänderter Regeln `availability_revision` (+1 je Änderung); der Server-Worker ist immer verfügbar
- [ ] Zusatzprogramme (z. B. ffmpeg) vom Server: Ablage `.data/client-tools/<name>/`, im Client `%LOCALAPPDATA%\MarathonWorker\tools\<name>\<version>\`; Module finden sie über `job.tool("<name>")`
  - Entschieden: Abgleich beim Start und alle 10 Minuten, jede Datei per SHA-256 geprüft; Version = Inhalts-Hash
- [ ] Auto-Update im Client: Angebot laden, Prüfsumme prüfen, Jobs auslaufen lassen, Neustart über den Starter, Rückfall auf die vorherige Version, wenn die neue nicht sauber startet
  - Entschieden: Der Server bietet noch keine Versionen an (Rollout im Backlog); bis dahin neue Version herunterladen und erneut installieren

---

## Geplant

Reihenfolge = Umsetzungsreihenfolge. `Offen:` klären wir je Release Punkt für Punkt, bevor der Punkt umgesetzt wird; `Vorschlag:` ist eine Empfehlung, keine Entscheidung. Wo `docs/spec.md` (Teil 3) und ein offener Punkt sich widersprechen, gilt bis zur Klärung der offene Punkt.

### Nächster Release: Datei- und Ordner-Dialog

Vertrag: `docs/spec.md` §3.6 (nur Quelle `Server`); offene Punkte unten gehen vor

- [ ] Ausbau von `FolderDialog` zum vollwertigen Datei-/Ordner-Dialog (`mode: "folder" | "file"`) ohne Favoritenspalte links; Dateien im Ordner-Modus zur Orientierung sichtbar
  - Offen: Rückgabe als `{ scope, worker_id, path }` kommt erst mit dem Pfadmodell (Release Lokale Watchfolder & Rechnerwechsel). Vorschlag: bis dahin Pfad als Text, Schnittstelle so anlegen, dass sie später erweitert wird
- [ ] Quelle `Server` (Laufwerke des Servers und Freigaben von `\\<Server-Adresse>`); Quelle `Dieser Rechner` folgt im Release Lokale Watchfolder & Rechnerwechsel
  - Offen: Dürfen normale Nutzer die Laufwerke des Servers sehen oder nur die Freigaben?
  - Offen: Bleibt der Umschalter „Laufwerke“ | „Server“ aus dem Release Worker-Steuerung, oder wird beides zu `Server` zusammengefasst?
  - Offen: `GET /api/v1/fs/dirs` als Alias behalten oder entfernen?
- [ ] Kopfzeile wie Windows Explorer: Zurück, Vor, Nach oben, Aktualisieren, anklickbare Breadcrumb-Pfadleiste mit Editiermodus und Autocomplete-Dropdown (`↓`/`↑` wählen, `Tab` ergänzen, `Enter` betreten), clientseitiges Filterfeld (Teilstring, Endungen wie `.mp4`, Wildcards)
- [ ] Ansichten `Liste` (mehrspaltig vertikal fließend, Icon + Name) und `Details` (sortierbare Tabelle: Name, Änderungsdatum, Typ, Größe; Ordner zuerst) mit Dateigruppen-Icons (`lucide-react`)
- [ ] Performance für große Ordner (5.000+ Dateien): Backend-Limit von 1.000 auf 10.000 angehoben (Hinweis bei Kappung) und virtualisiertes Rendering im Frontend
  - Offen: Virtualisierung selbst bauen (Spec: ohne externe Bibliothek) oder eine kleine Bibliothek zulassen?

### Nacharbeit Worker-Client & Steuerung (später)

Aus der Abnahme des Releases Worker-Client & Steuerung.

- [ ] Downloadseite `https://<Marathon-Adresse>/download` ohne Anmeldung, mit der der Client heruntergeladen wird (nutzt `GET /api/v1/client/download`)
  - Entschieden: Die Zertifikatswarnung des Browsers auf einem Rechner ohne Stammzertifikat wird hier vorerst hingenommen; der installierte Client importiert das Stammzertifikat danach selbst
  - Offen: Inhalt der Seite (nur Schaltfläche oder zusätzlich kurze Installationsschritte) und Verhalten ohne gebautes Client-Paket

### Release Lokale Watchfolder & Rechnerwechsel

Vertrag: `docs/spec.md` §3.4 (Rechner-Erkennung im Browser), §3.5, §3.6 (Quelle `Dieser Rechner`); offene Punkte unten gehen vor

#### Rechner-Erkennung im Browser

- [ ] Lokale Rechner-Erkennung im Browser über einen Loopback-only-Endpunkt (`127.0.0.1`) mit strenger `Origin`-Prüfung und kurzlebigem Anwesenheits-Ticket (`presence_token`, plus Tray-Fallback-Link), damit Marathon weiß, an welchem Rechner der Nutzer gerade sitzt – ohne User↔Worker-Bindung und ohne Zugriff auf fremde Rechner übers Netz; setzt Release HTTPS voraus
  - Offen: Browser fragen beim ersten Zugriff auf `127.0.0.1` um Erlaubnis („Loopback-Netzwerk“); die Abfrage darf nur nach einem Klick starten. Wo in der Oberfläche löst der Nutzer sie aus, und was zeigt Marathon bei verweigerter Erlaubnis?
  - Offen: Der veraltete Header `Access-Control-Allow-Private-Network` aus der Spec entfällt. Vorschlag: streichen, nur `Access-Control-Allow-Origin` und `targetAddressSpace: "loopback"` im Browser
  - Offen: Tray-Fallback-Link: Wie wird das Cookie `marathon_local_worker` geprüft (eigener Token-Zweck, Tabelle) und wie lange gilt es?
  - Offen: Hauptweg Loopback und Tray-Link als Rückfall – oder beide gleichwertig?
- [ ] Anfragen an Worker (Ordnerauflistung, Pfadprüfung) laufen über den ausgehenden Long-Poll des Workers und werden in der Datenbank gehalten, nicht im Server-Speicher
  - Offen: Tabelle für diese Anfragen fehlt in Migration `0010` (Aufbau, Zeitlimit, Aufräumen)

#### Watchfolder auf Workern

- [ ] Watchfolder wird eine Fähigkeit der Worker (ab Werk oberste Prio) und ersetzt den Server-Hintergrunddienst (`watcher.py`); ist kein passender Worker verfügbar, wartet der Scan mit Eintrag im Modul-Log
  - Offen: Scan als Job oder als kurze Nebenanfrage wie die Ordnerauflistung? Ein Job braucht heute mindestens ein Asset, und ein Worker macht nur einen Job – ein langer Kopierjob würde alle Scans dieses Workers anhalten. Vorschlag: Nebenanfrage; der Worker liefert nur die Dateiliste, der Server wertet sie mit `plan_scan`/`apply_scan` aus
  - Offen: „Ab Werk oberste Prio“ bewirkt nichts, weil der Rang nur zwischen Workern wählt, nicht zwischen Jobs eines Workers. Streichen oder anders fassen?
  - Offen: Wer scannt Netzwerkpfade – nur der Server-Worker (Mission Log) oder der beste Worker nach Rang (Spec)? Bei Rang entscheidet unter gleich hohen Workern der Name
  - Offen: Update bestehender Installationen: `watchfolder` in die Fähigkeiten aller bestehenden Worker (inkl. Server-Worker) und in `[server_worker] skills` aufnehmen, sonst scannt nach dem Update niemand mehr
- [ ] Strukturiertes Pfadmodell `{ scope: "network" | "local", worker_id, path }` statt Worker-Name im Pfad-String; in der UI lesbar als Chip `[Worker-Name] D:\Ingest`; Netzwerkpfade scannt der Server-Worker, lokale Pfade ausschließlich die gewählte `worker_id`
  - Offen: Feldname einheitlich `scope` oder `path_scope` (Mission Log und Dialog vs. Spec §3.5)
  - Offen: Bestehende lokale Pfade (heute Laufwerke des Servers) würden „unvollständig“. Vorschlag: bei der Migration `worker_id = server-worker`
  - Offen: Gilt das Pfadmodell auch für Ziele von Kopieren und Verschieben?
  - Offen: Gemappte Laufwerke (z. B. `Z:\`) gelten als lokal, nur `\\…` als Netzwerk – so gewollt?
- [ ] Nahtloser Rechnerwechsel (Rechner 1 → Rechner 2): Beim Anlegen lokaler Pfade wird der aktuell erkannte lokale Rechner vorgeschlagen; öffnet der Nutzer den Workflow an einem anderen Rechner, bietet Marathon per 1-Klick-Umschaltung (und beim Starten eines gestoppten Workflows) an, den Workflow auf den aktuellen Rechner umzustellen – inkl. Live-Prüfung, ob der lokale Pfad dort existiert
  - Offen: Was passiert mit Assets, die noch an Rechner 1 gebunden sind, wenn der Workflow auf Rechner 2 umgestellt wird?
- [ ] Sticky Locality: lokal erfasste Assets bleiben an ihre `worker_id` gebunden, bis ein Modul die Datei auf einen Netzwerkpfad schreibt; danach normale Verteilung nach Fähigkeiten-Rang
  - Offen: Lokales Ziel auf Worker X bei einem Asset, das an Worker Y gebunden ist – verbieten (Prüfung beim Speichern) oder umbinden?
  - Offen: Gebundener Worker wird gelöscht. Vorschlag: Asset *Fehlgeschlagen* statt freigeben (der lokale Pfad existiert anderswo nicht)
  - Offen: Gebundener Worker lange nicht verfügbar – nur warten oder Hinweis am Asset?
- [ ] Kontextsensitive Fehlermeldungen direkt am Watchfolder-Modul: kein Worker-Client auf dem aktuellen Rechner erkannt → Link zum Client-Download; zugewiesener Worker ist ein anderer Rechner → 1-Klick-Umstellung auf diesen Rechner; zugewiesener Worker ist offline, gestoppt oder nicht verfügbar → Grund + Link zu den Worker-Einstellungen
  - Entschieden (Release Zugang & Nutzer): Normale Nutzer sehen alle Worker lesend; der Client-Download ist für alle angemeldeten Nutzer da
- [ ] Watchfolder-Spalten auf `neu` (links, Funde des laufenden Scans; danach → `erfasst`) und `erfasst` (rechts) umgestellt, `wartet` entfällt; gefundene Assets gehen direkt weiter
  - Offen: Was steht in `neu` – Dateien, die noch auf Stabilität warten, oder Assets, die der laufende Scan gerade angelegt hat?
- [ ] Warten-Modul zeigt Assets während der Wartezeit in der Spalte `wartet`, danach `erledigt` über Ausgang `Erfolg`
  - Offen: Im Release Worker-Steuerung wurde `wartet` gerade entfernt. Bewusst zurück oder Punkt streichen?

#### Datei-Dialog: Quelle „Dieser Rechner“

- [ ] Quelle `Dieser Rechner` (Live-Listing über den ausgehenden Long-Poll des lokal erkannten Workers, abgesichert über `presence_token`; Admins dürfen zusätzlich beliebige Online-Worker wählen; Hinweis + Download-Link falls lokal kein Client läuft)
  - Offen: Verhalten, wenn der Worker gerade nicht antwortet (Zeitlimit 10 s, Meldungstext)

### Release Modul-Builder (grob, Brainstorming folgt)

Vertrag: noch keiner; ersetzt die Teilworkflows (Sub) aus dem Backlog. Leitidee: die umfassende Abstraktion – aus atomaren Bausteinen entstehen Makro-Systeme

- [ ] Module in der Oberfläche bauen: Ein Modul besteht aus Code-Block, Eingang, Ausgängen, Stats und Settings im Seitenpanel
- [ ] Freigabe beim Speichern: Module sind privat oder öffentlich; ändern dürfen nur Ersteller und Admin, öffentliche Module können andere nur benutzen
- [ ] Standardmodule (heute Watchfolder, Warten, Kopieren, Verschieben, Umbenennen, Prioliste) gehören dem Admin und sind ab Werk öffentlich
- [ ] Löst „Module sind immer für alle geteilt“ aus dem Release Zugang & Nutzer ab; Modulsperren je Nutzer und Konsistenzschutz Modul-/Workflow-Freigabe (aus Zugang & Nutzer zurückgestellt; frühere Fassung in der Git-Historie von `docs/spec.md`) werden hier neu gedacht
- Offen (Brainstorming):
  - Wer darf Module bauen – jeder Nutzer oder nur Admins? Der Code-Block läuft auf Workern mit Dateizugriff
  - Sprache, Ausführung und Absicherung des Code-Blocks
  - Wie werden Settings-Felder, Ausgänge und Stats im Builder beschrieben?
  - Verhältnis zu den Modultypen unter `marathon/modules/` und Verteilung an die Worker (Bezug Auto-Update im Backlog)
  - Versionen: Was passiert mit Workflows, wenn ein Modul geändert, privat gestellt oder gelöscht wird?
  - Wird ein Sub (Modul aus einem Teil-Workflow) eine Variante des Builders?
- Aus dem bisherigen Sub-Konzept (zur Prüfung):
  - Anlegen: Module im Workflow markieren → „Als Sub speichern“; Eingang, Erfolg, Fehler ergeben sich aus der Auswahl
  - Freigegebene Optionen (z. B. QC-Preset) je Verwendung im Seitenpanel einstellbar
  - Doppelklick betritt den Sub; Änderungen gelten nur für diese Instanz
  - Vorlage ändern: Sub in leeren Workflow ziehen, auflösen, ändern, beim Speichern bestehenden Sub ersetzen
  - Verwendende Workflows bekommen einen Update-Hinweis zum Übernehmen; Instanz-Einstellungen bleiben, sonst Warnung
  - Offen: Was gilt bei einem Update, wenn die Instanz innen geändert wurde?

---

## Backlog

### Engine und Worker

- [ ] Rollout von Client-Versionen und Modulen über den Server (Server gibt Versionen für alle oder ausgewählte Worker frei, Schaltfläche „Version ausrollen“); die Client-Seite (Prüfsumme, Auslaufen, Starter, Rückfall) ist seit Release Worker-Client & Steuerung eingebaut
  - Offen: Rollout-Strategie und Versionsverträglichkeit zwischen Server und Client
- [ ] Umstieg auf Linux & Pfadzuordnung je Worker (Server-Worker sieht `/mnt/...` statt `\\server\freigabe`)
- [ ] Erreichbare Speicherorte je Worker: Jobs nur an Worker, die die Pfade erreichen – zusammen mit der Pfadzuordnung
- [ ] Sammeljobs: Ein Modul sammelt mehrere Assets nach einer Regel (z. B. gleicher Schlüssel) und veröffentlicht einen Job; Teilergebnisse je Asset

### Module

- [ ] Modulpanel mit ausklappbaren Kategorien zur Sortierung
- [ ] Watchfolder: ersetzte Datei – altes Asset beenden („Quelldatei kann nicht mehr zugeordnet werden“), neues Asset mit Hinweis auf die verlorene Vorgängerdatei
  - Offen: Welcher Status ist „beendet“ – Erledigt oder Fehlgeschlagen?
- [ ] Arbeitsordner je Job & Cleanup-Modul: Marschrichtung ein flacher Arbeitsordner je Job, den ein Cleanup-Modul am Ende löscht
- Geplante Module:
  - [ ] Transcode (`Transcoder`; Wahl von GPU oder CPU je Worker regelt das Modul)
  - [ ] QC (`AQC`)
  - [ ] Searcher (`ES-Search`)
  - [ ] Restore (`ES-ARK-API`)
  - [ ] Mailversand
  - [ ] Report erstellen
  - [ ] API / Fachbibliotheken (`TOOLBOX`, EditShare, Veritone, ARK, awork) → später
  - [ ] Umbenennen: weitere Methoden
  - [ ] Prioliste: Metadaten je Eintrag bearbeiten, Verarbeitung nach Prio
  - [ ] Rechenintensive Module (Transcode, QC …) beim Einbau für den Server-Worker über die Fähigkeiten-Rangliste als letzte Wahl vorsehen

### Assets und Daten

- [ ] Assets hinzufügen: manuell und einfacher Listenimport
- [ ] Asset mit mehreren Dateien (z. B. OP-Atom, Interview aus vielen Einzeldateien): Eine Kopie wird eine weitere Datei des Assets statt eines neuen Assets – vertagt
  - Offen: Welche Datei verarbeiten Folgemodule standardmäßig? Wie wird nur die neue Kopie weiterverarbeitet (`asset_x.copy_a`)?
- [ ] Asset-Aktionen im Workflow und in der Asset-Ansicht: Nach oben, Abbrechen, Anhalten/Freigeben, Entfernen
- [ ] Aufbewahrung und Aufräumregel der Daten (blockweises Entfernen alter Historien/Logs)
  - Offen: Daten pro Workflow oder global aufheben?
  - Offen: Größenordnung (Assets je Workflow, Anzahl Worker), Aufbewahrungsdauer und konkrete Aufräumregel (was wird wann entfernt?)

### Oberfläche

- [ ] Warten-Modul: leere Asset-Seite bei 5.000+ Assets; nicht reproduzierbar – wartet auf Browser-Konsolenfehler und Klickweg
  - Offener Nebenbefund: Workflow-Reiter „Assets“ startet mit Filter `pending`; Assets im Warten-Modul (`in_progress`) erscheinen erst nach Filterwechsel.
- [ ] Bug-Report: echter Versand per Mail (nutzt den zentralen `[smtp]`-Dienst aus dem Release Zugang & Nutzer)
  - Empfängeradresse in Konfiguration/Einstellungen (keine Zugangsdaten im Code)
  - Server-Endpunkt zum Versenden; „Senden“ ruft ihn auf
  - Meldung bei fehlgeschlagenem Versand statt Erfolgsmeldung, Eingaben bleiben erhalten
  - Zusatzdaten anhängen: App-Version, aktuelle Seite, Browser, Zeitpunkt
  - Offen: Screenshot oder Logauszug mitschicken?
- [ ] Dashboard-Kennzahlen und Asset-Ansicht ausbauen
  - Offen: Welche weiteren Kennzahlen zeigt das Dashboard (z. B. Fehler, Durchsatz) und welche zusätzlichen Spalten/Filter braucht die Asset-Ansicht?

### Reports

- [ ] Reports ausbauen: weitere Filter und Kennzahlen

### Mailversand

- [ ] Versand über Microsoft Graph API (Microsoft 365) als weitere Methode neben SMTP; SMTP bleibt die universelle Methode
  - Offen: Details klärt Martin (App-Registrierung im Tenant, Berechtigung zum Senden, Wahl der Methode in Einstellungen → Mailversand); Einrichtung wie bei SMTP nur über die Oberfläche, Daten nur in der Datenbank

### Zugang und Sicherheit

- [ ] Anbindung an übergeordnetes Ökosystem (SSO / Landingpage mit Tool-Übersicht ohne zweiten Login in Marathon; nutzt `AuthProvider` und `users.external_subject` aus dem Release Zugang & Nutzer)
  - Offen: Übernimmt der übergeordnete Server nur den Login oder auch mehrere Instanzen/Mandanten?
- [ ] Geführte Ersteinrichtung: Der erste Admin hinterlegt seine E-Mail-Adresse und erhält darüber den Code; der Notfallzugang per `admin-code` bleibt
  - Erst nach Klärung des Mail-Wegs (SMTP, Graph API) und nach wichtigeren Releases
- [ ] Öffentlicher Zugang aus dem Internet (HTTPS im LAN kommt mit Release HTTPS)
  - Offen: Übernimmt den Zugang von außen ein übergeordneter Server / Reverse-Proxy?

### Änderungen Martin (diesen Bereich immer im Doc belassen!)
- [ ] Reihenfolge linkes Seitenmenü -> System: Nutzer, Worker, Einstellungen -- Einstellungen immer als letztes, auch wenn noch weitere Optionen dazukommen
- [ ] Tooltips sollen überall keine Internas preisgeben (IP Adressen, Domain, Server) - immer neutrale Tooltips verwenden
- [ ] Workersteuerung UI: Play und Stop werden zu einem Button mit wechselnden Symbol - Sofort bleibt als extra Button - bei Sofort Aus ist das Stop-Symbol zu weit oben und damit nicht mittig auf dem Button. Vergleiche mit dem Stop-Button. 
 - [ ] Etwas unklar, was jetz die Staus bereit, online und verfügbar darstellen - lässt sich das reduzieren? Verfügbar stellen war als grünen Punkt dar statt "ja"
 - [ ] feld "wird sofort gestopp" in worker übersicht tabelle oben braucht es nicht - sofort stoppen geht ja schnell
 - [ ] Client Installer mit Marathon-M als Icon - beachte alle Icon-Größen (16x16 bis 256x256 oder was auch immer der Standard ist)

