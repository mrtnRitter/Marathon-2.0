# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[ ]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Architektur & Vertrag: `docs/spec.md`

---

## Abnahme offen

### Release Worker-Steuerung

Vertrag: `docs/spec.md` (Teil 2)

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

### Release Live-Kanal & Zeitplan-Wartemodus

- [ ] Alle Ansichten aktualisieren sich über einen Live-Kanal (SSE) in Echtzeit; bei Verbindungsverlust sperrt der Hinweis „Keine Verbindung zum Marathon-Server“ die Oberfläche, bis die Verbindung zurück ist
- [ ] Zeitgesteuerte Workflows bleiben zwischen Läufen „aktiv“, warten auf den nächsten Termin und scannen/vergeben während der Wartezeit keine Jobs

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
- [ ] `last_choice` überall entfernt (Konfiguration, Worker-Anmeldung, Worker-Laufzeit)

---

## Geplant

### Nächster Release: Infrastruktur – Nutzer, Worker-Client, lokale Watchfolder & Datei-Dialog

Vertrag: `docs/spec.md` (Teil 3)

#### Zugang, Nutzer & Freigaben

- [ ] Anmeldung per E-Mail (Magic-Link mit Bestätigungsklick gegen Mail-Scanner + 6-stelliger Einmalcode); ein Feld für Login und Selbstregistrierung (Konto wird erst bei Bestätigung angelegt)
- [ ] Langlebige serverseitige Session (`HttpOnly`-Cookie mit Sliding Expiration, z. B. 180 Tage, einzeln widerrufbar); Andockstelle (`AuthProvider`, `external_subject`) für späteres Ökosystem bleibt erhalten
- [ ] Erlaubte E-Mail-Domains für die Selbstregistrierung in den Serversettings pflegbar (Startwert aus `config/marathon.toml` analog zu `folder_dialog.server_address`)
- [ ] Zentraler `[smtp]`-Konfigurationsblock in `config/marathon.toml` (Passwort nur per Umgebungsvariable oder `.data/smtp.pass`), nutzbar für Magic-Link und späteren Bug-Report
- [ ] Rollen `user` und `admin`; Break-Glass über `[auth] admin_emails` in `config/marathon.toml` (immer Admin, in UI nicht herabstufbar/löschbar, Schutz letzter Admin) plus lokaler Server-CLI-Notfallbefehl (`python run.py admin-link <mail>`) ohne SMTP
- [ ] Workflow-Freigaben je Nutzer in drei Stufen: nicht teilen, teilen zur Ansicht (kein Bearbeiten, kein Starten/Stoppen/erneutes Verarbeiten), teilen zur Bearbeitung und Ausführung
- [ ] Modultypen je Nutzer einzeln freigebbar/sperrbar (Admin sieht/darf alles; neue Nutzer ab Werk mit allen Modultypen freigegeben)
- [ ] Konsistenzschutz Modul-/Workflow-Freigabe: Workflow mit für den Nutzer gesperrtem Modul lässt sich nicht zur Bearbeitung freigeben (eindeutige Fehlermeldung, nur Ansicht möglich); nachträgliche Modulsperre stuft den Workflow effektiv auf Ansicht zurück; laufende Bearbeitung kann bei zwischenzeitlich entzogener Modulfreigabe nicht gespeichert werden (`403` + klare Meldung)
- [ ] Normale Nutzer sehen keine Servereinstellungen, keine Nutzerverwaltung und keine fremden Worker; alle API-Routen und der Live-Kanal (SSE) prüfen Rechte serverseitig

#### Worker-Client (Windows-Tray), Entkopplung & Verfügbarkeit

- [ ] Strikte Trennung von Nutzerkonto und Worker: Ein Worker ist ein eigenständiger Rechner mit individuellem Worker-Token (Erstregistrierung über `/api/v1/worker/enroll`, optional mit Admin-Freigabe über `[worker_client] require_admin_approval`), ohne dauerhafte Bindung an einen Nutzer-Account
- [ ] Windows-Tray-Client im Benutzerkontext (Autostart, Starter + austauschbare Laufzeit, kein eigenes Einstellungs-UI); Doppelklick / Rechtsklick → „Worker-Einstellungen öffnen“ holt einen kurzlebigen Einmal-Steuerungslink vom Server und öffnet genau diesen Worker im Browser – auch ohne Marathon-User-Login
- [ ] Lokale Rechner-Erkennung im Browser über einen Loopback-only-Endpunkt (`127.0.0.1`) mit strenger `Origin`-Prüfung und kurzlebigem Anwesenheits-Ticket (`presence_token`, plus Tray-Fallback-Link), damit Marathon weiß, an welchem Rechner der Nutzer gerade sitzt – ohne User↔Worker-Bindung und ohne Zugriff auf fremde Rechner übers Netz
- [ ] Drei Steuerbefehle am Worker (vor Ort über Tray-Link & für Admins): „Starten“, „Stoppen“ (sanftes Auslaufen / Drain: keine neuen Jobs, laufende Jobs sauber beenden), „Sofort stoppen“ (harter Abbruch, Assets zurück ans letzte Modul, zählt nicht als Versuch)
- [ ] Verfügbarkeitsregeln am Worker durchgängig als Positivliste („Wann ist der Worker verfügbar?“): Zeitplan Mo–So 0–24 Uhr, Inaktivitäts-Start nach X Minuten (Tastatur/Maus + CPU, RAM, GPU unter Schwellenwert; Eigenlast laufender Worker-Jobs deaktiviert die Prüfung nicht), Ausschlussliste „Nur verfügbar, wenn keines der Programme X, Y, Z läuft“
- [ ] Einstellungen liegen zentral im Marathon-Server; Worker-Client wertet lokale Sensoren aus und meldet Verfügbarkeit samt Grund zurück; bei Verlust der Verfügbarkeit laufen aktive Jobs sauber zu Ende (Drain); Fähigkeiten-Rangliste bleibt Admin-Sache

#### Watchfolder auf Workern, lokale Pfade & Rechnerwechsel

- [ ] Watchfolder wird eine Fähigkeit der Worker (ab Werk oberste Prio) und ersetzt den Server-Hintergrunddienst (`watcher.py`); ist kein passender Worker verfügbar, wartet der Scan mit Eintrag im Modul-Log
- [ ] Strukturiertes Pfadmodell `{ scope: "network" | "local", worker_id, path }` statt Worker-Name im Pfad-String; in der UI lesbar als Chip `[Worker-Name] D:\Ingest`; Netzwerkpfade scannt der Server-Worker, lokale Pfade ausschließlich die gewählte `worker_id`
- [ ] Nahtloser Rechnerwechsel (Rechner 1 → Rechner 2): Beim Anlegen lokaler Pfade wird der aktuell erkannte lokale Rechner vorgeschlagen; öffnet der Nutzer den Workflow an einem anderen Rechner, bietet Marathon per 1-Klick-Umschaltung (und beim Starten eines gestoppten Workflows) an, den Workflow auf den aktuellen Rechner umzustellen – inkl. Live-Prüfung, ob der lokale Pfad dort existiert
- [ ] Sticky Locality: lokal erfasste Assets bleiben an ihre `worker_id` gebunden, bis ein Modul die Datei auf einen Netzwerkpfad schreibt; danach normale Verteilung nach Fähigkeiten-Rang
- [ ] Kontextsensitive Fehlermeldungen direkt am Watchfolder-Modul: kein Worker-Client auf dem aktuellen Rechner erkannt → Link zum Client-Download; zugewiesener Worker ist ein anderer Rechner → 1-Klick-Umstellung auf diesen Rechner; zugewiesener Worker ist offline, gestoppt oder nicht verfügbar → Grund + Link zu den Worker-Einstellungen
- [ ] Watchfolder-Spalten auf `neu` (links, Funde des laufenden Scans; danach → `erfasst`) und `erfasst` (rechts) umgestellt, `wartet` entfällt; gefundene Assets gehen direkt weiter
- [ ] Warten-Modul zeigt Assets während der Wartezeit in der Spalte `wartet`, danach `erledigt` über Ausgang `Erfolg`

#### Datei- und Ordner-Dialog

- [ ] Ausbau von `FolderDialog` zum vollwertigen Datei-/Ordner-Dialog (`mode: "folder" | "file"`) ohne Favoritenspalte links; Dateien im Ordner-Modus zur Orientierung sichtbar
- [ ] Quellen-Umschalter `Server` (Netzlaufwerke/Freigaben) vs. `Dieser Rechner` (Live-Listing über den ausgehenden Long-Poll des lokal erkannten Workers, abgesichert über `presence_token`; Admins dürfen zusätzlich beliebige Online-Worker wählen; Hinweis + Download-Link falls lokal kein Client läuft)
- [ ] Kopfzeile wie Windows Explorer: Zurück, Vor, Nach oben, Aktualisieren, anklickbare Breadcrumb-Pfadleiste mit Editiermodus und Autocomplete-Dropdown (`↓`/`↑` wählen, `Tab` ergänzen, `Enter` betreten), clientseitiges Filterfeld (Teilstring, Endungen wie `.mp4`, Wildcards)
- [ ] Ansichten `Liste` (mehrspaltig vertikal fließend, Icon + Name) und `Details` (sortierbare Tabelle: Name, Änderungsdatum, Typ, Größe; Ordner zuerst) mit Dateigruppen-Icons (`lucide-react`)
- [ ] Performance für große Ordner (5.000+ Dateien): Backend-Limit auf 10.000 angehoben (Hinweis bei Kappung) und virtualisiertes Rendering im Frontend

---

## Backlog

### Engine und Worker

- [ ] Auto-Update mit Rückfall & Rollout von Client-Versionen und Modulen (Server gibt Versionen für alle oder ausgewählte Worker frei; Client prüft Prüfsumme, lässt laufende Jobs auslaufen, tauscht die Laufzeit über den Starter und fällt bei Startfehler auf die vorherige Version zurück)
  - Offen: Rollout-Strategie und Versionsverträglichkeit zwischen Server und Client
- [ ] Umstieg auf Linux & Pfadzuordnung je Worker (Server-Worker sieht `/mnt/...` statt `\\server\freigabe`)
- [ ] Erreichbare Speicherorte je Worker: Jobs nur an Worker, die die Pfade erreichen – zusammen mit der Pfadzuordnung
- [ ] Sammeljobs: Ein Modul sammelt mehrere Assets nach einer Regel (z. B. gleicher Schlüssel) und veröffentlicht einen Job; Teilergebnisse je Asset

### Module

- [ ] Teilworkflows als Modul neben den Basis-Modulen, genannt Sub – zurückgestellt bis Infrastruktur läuft
  - Anlegen: Module im Workflow markieren → „Als Sub speichern“; Eingang, Erfolg, Fehler ergeben sich aus der Auswahl
  - Freigegebene Optionen (z. B. QC-Preset) je Verwendung im Seitenpanel einstellbar
  - Doppelklick betritt den Sub; Änderungen gelten nur für diese Instanz
  - Vorlage ändern: Sub in leeren Workflow ziehen, auflösen, ändern, beim Speichern bestehenden Sub ersetzen
  - Verwendende Workflows bekommen einen Update-Hinweis zum Übernehmen; Instanz-Einstellungen bleiben, sonst Warnung
  - Offen: Was gilt bei einem Update, wenn die Instanz innen geändert wurde?
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

- [ ] Bug-Report: echter Versand per Mail (nutzt den zentralen `[smtp]`-Dienst aus dem Infrastruktur-Release)
  - Empfängeradresse in Konfiguration/Einstellungen (keine Zugangsdaten im Code)
  - Server-Endpunkt zum Versenden; „Senden“ ruft ihn auf
  - Meldung bei fehlgeschlagenem Versand statt Erfolgsmeldung, Eingaben bleiben erhalten
  - Zusatzdaten anhängen: App-Version, aktuelle Seite, Browser, Zeitpunkt
  - Offen: Screenshot oder Logauszug mitschicken?
- [ ] Dashboard-Kennzahlen und Asset-Ansicht ausbauen
  - Offen: Welche weiteren Kennzahlen zeigt das Dashboard (z. B. Fehler, Durchsatz) und welche zusätzlichen Spalten/Filter braucht die Asset-Ansicht?

### Reports

- [ ] Reports ausbauen: weitere Filter und Kennzahlen

### Zugang und Sicherheit

- [ ] Anbindung an übergeordnetes Ökosystem (SSO / Landingpage mit Tool-Übersicht ohne zweiten Login in Marathon; nutzt `AuthProvider` und `users.external_subject` aus dem Infrastruktur-Release)
  - Offen: Übernimmt der übergeordnete Server nur den Login oder auch mehrere Instanzen/Mandanten?
- [ ] HTTPS-Verbindung / öffentlicher Zugang
  - Offen: Braucht Marathon das Zertifikat selbst, oder übernimmt das ein übergeordneter Server / Reverse-Proxy?
