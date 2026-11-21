# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[ ]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Architektur & Vertrag: `docs/spec.md`

---

## Abnahme offen

### Release HTTPS

Vertrag: `docs/spec.md` (Teil 2a)

- [ ] Marathon ist von anderen Rechnern nur über `https://` erreichbar (auf dem Server-Rechner bleibt der Rettungszugang http://127.0.0.1:8080) und liegt immer hinter einem Proxy: ab Werk der mitgelieferte Caddy (eigene interne CA, Zertifikate erneuern sich automatisch), wahlweise ein externer Reverse-Proxy; Grundlage für Session-Cookies (`Secure`), Magic-Links, Worker-Tokens und die Rechner-Erkennung im Browser
  - Entschieden: Rechnername `laptop-martin-r` (`public_url = "https://laptop-martin-r"`); Zertifikats- und Proxy-Daten unter `.cert/` (nicht im Repo)
  - Entschieden: Vertrauen – auf dem Server-Rechner `python run.py --trust`, auf weiteren Rechnern das Stammzertifikat einmal von Hand importieren; ab Release Worker-Client & Steuerung übernimmt das der Installer
- [ ] Nur der Port aus `public_url` (ab Werk 443); Port 80 bleibt zu. Anfragen über HTTP oder abweichende Rechnernamen, die Marathon erreichen, werden auf `public_url` umgeleitet
- [ ] Proxy-Einstellungen in den Serversettings (Öffentliche Adresse, mitgelieferter Proxy an/aus, Adressen externer Proxys); Startwerte aus `[proxy]` in `config/marathon.toml`, danach gilt die Datenbank; Änderungen gelten nach einem Neustart des Servers
- [ ] `[auth] cookie_secure` entfällt; Session-Cookies sind immer `Secure`
- [ ] Worker-Laufzeit prüft das Server-Zertifikat (Zertifikatsspeicher des Systems oder einstellbare CA-Datei, Prüfung nie abschaltbar); Server-Worker ohne Sonderbehandlung über `public_url`
- [ ] Mitgelieferter Proxy kommt mit `python build.py` (Caddy 2.11.9, Prüfsumme geprüft); `python run.py` startet und stoppt ihn mit dem Server


### Release Worker-Steuerung

Vertrag: `docs/spec.md` (Teil 2)

- [ ] Neuer Worker meldet sich „gestoppt“ an; Stoppen bricht alle Jobs ab, Asset zurück an den letzten fertigen Ausgang, zählt nicht als Versuch
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

---

## Geplant

Reihenfolge = Umsetzungsreihenfolge. `Offen:` klären wir je Release Punkt für Punkt, bevor der Punkt umgesetzt wird; `Vorschlag:` ist eine Empfehlung, keine Entscheidung. Wo `docs/spec.md` (Teil 3) und ein offener Punkt sich widersprechen, gilt bis zur Klärung der offene Punkt.

### Nächster Release: Zugang & Nutzer

Vertrag: `docs/spec.md` §3.1–3.3 (ohne `[worker_client]`); offene Punkte unten gehen vor

- [ ] Anmeldung per E-Mail (Magic-Link mit Bestätigungsklick gegen Mail-Scanner + 6-stelliger Einmalcode); ein Feld für Login und Selbstregistrierung (Konto wird erst bei Bestätigung angelegt)
  - Offen: Grenze für falsche Codes. Vorschlag: nach 5 Fehlversuchen wird der Code ungültig
  - Offen: Verhalten, wenn kein Mailversand eingerichtet ist. Vorschlag: klare Meldung „Mailversand ist nicht eingerichtet“, Admins nutzen den CLI-Notfallbefehl
  - Offen: Dürfen die Meldungen „Domain nicht freigegeben“ / „Konto deaktiviert“ verraten, ob ein Konto existiert (internes Werkzeug), oder immer neutrale Antwort?
- [ ] Langlebige serverseitige Session (`HttpOnly`-Cookie mit Sliding Expiration, z. B. 180 Tage, einzeln widerrufbar); Andockstelle (`AuthProvider`, `external_subject`) für späteres Ökosystem bleibt erhalten
  - Offen: Wer widerruft Sessions – der Nutzer selbst (Liste seiner Geräte), der Admin für alle oder beide? Schnittstelle und Ansicht dafür fehlen in der Spec
- [ ] Erlaubte E-Mail-Domains für die Selbstregistrierung in den Serversettings pflegbar (Startwert aus `config/marathon.toml` analog zu `folder_dialog.server_address`)
  - Offen: Form in `GET/PUT /api/v1/settings` und Prüfregel für Domains (z. B. Subdomains erlaubt oder nur exakt)
- [ ] Zentraler `[smtp]`-Konfigurationsblock in `config/marathon.toml` (Passwort nur per Umgebungsvariable oder `.data/smtp.pass`), nutzbar für Magic-Link und späteren Bug-Report
- [ ] Rollen `user` und `admin`; Break-Glass über `[auth] admin_emails` in `config/marathon.toml` (immer Admin, in UI nicht herabstufbar/löschbar, Schutz letzter Admin) plus lokaler Server-CLI-Notfallbefehl (`python run.py admin-link <mail>`) ohne SMTP
  - Offen: Nutzerverwaltung – Schnittstelle und Ansicht für Liste, Rolle, Deaktivieren, Löschen fehlen in der Spec. Löschen erlaubt oder nur Deaktivieren?
  - Offen: Was passiert mit den Workflows eines gelöschten Nutzers? Vorschlag: ohne Eigentümer, nur für Admins sichtbar
- [ ] Workflow-Freigaben je Nutzer in drei Stufen: nicht teilen, teilen zur Ansicht (kein Bearbeiten, kein Starten/Stoppen/erneutes Verarbeiten), teilen zur Bearbeitung und Ausführung
  - Offen: Wer darf Workflows anlegen? Vorschlag: jeder Nutzer, Ersteller wird Eigentümer
  - Offen: Darf der Eigentümer selbst teilen oder nur ein Admin?
  - Offen: Bestehende Workflows haben nach dem Update keinen Eigentümer. Vorschlag: nur für Admins sichtbar, bis ein Admin sie zuordnet
- [ ] Modultypen je Nutzer einzeln freigebbar/sperrbar (Admin sieht/darf alles; neue Nutzer ab Werk mit allen Modultypen freigegeben)
  - Offen: Neue Modultypen nach einem Update sind für alle bestehenden Nutzer sofort frei (folgt aus „gesperrt nur per Eintrag“) – so gewollt?
- [ ] Konsistenzschutz Modul-/Workflow-Freigabe: Workflow mit für den Nutzer gesperrtem Modul lässt sich nicht zur Bearbeitung freigeben (eindeutige Fehlermeldung, nur Ansicht möglich); nachträgliche Modulsperre stuft den Workflow effektiv auf Ansicht zurück; laufende Bearbeitung kann bei zwischenzeitlich entzogener Modulfreigabe nicht gespeichert werden (`403` + klare Meldung)
- [ ] Normale Nutzer sehen keine Servereinstellungen, keine Nutzerverwaltung und keine fremden Worker; alle API-Routen und der Live-Kanal (SSE) prüfen Rechte serverseitig
  - Offen: Normale Nutzer brauchen Name und Zustand der Worker, die ihre Workflows nutzen (Worker-Chip, Hinweise am Watchfolder). Vorschlag: eingeschränkte Leseansicht nur dieser Worker (Name, online, Status, Grund), ohne Steuerung
  - Offen: Der Live-Kanal meldet heute nur geänderte Tabellen an alle. Vorschlag: Ereignisse tragen `workflow_id` und werden je Verbindung nach Rechten gefiltert

### Release Worker-Client & Steuerung

Vertrag: `docs/spec.md` §3.1 (`[worker_client]`), §3.2, §3.4 (ohne Rechner-Erkennung im Browser); offene Punkte unten gehen vor

- [ ] Strikte Trennung von Nutzerkonto und Worker: Ein Worker ist ein eigenständiger Rechner mit individuellem Worker-Token (Erstregistrierung über `/api/v1/worker/enroll`, optional mit Admin-Freigabe über `[worker_client] require_admin_approval`), ohne dauerhafte Bindung an einen Nutzer-Account
  - Offen: Standard für `require_admin_approval`. Vorschlag: `true`, sonst kann jeder Rechner im Netz sich anmelden, über `/worker-control` selbst starten und Jobs mit Dateipfaden erhalten
  - Offen: Wer setzt die erste Fähigkeiten-Liste? Der Datensatz entsteht schon bei `enroll`, die Regel „erste Anmeldung → gemeldete Modultypen“ greift dann nie. Vorschlag: bei der ersten Anmeldung mit leerer Liste
  - Offen: Neuinstallation erzeugt einen neuen Worker. Worker löschen (Admin) einplanen?
  - Offen: Protokoll 2 weiter annehmen? Vorschlag: nein, der Server-Worker wechselt mit auf Protokoll 3
- [ ] Windows-Tray-Client im Benutzerkontext (Autostart, Starter + austauschbare Laufzeit, kein eigenes Einstellungs-UI); Doppelklick / Rechtsklick → „Worker-Einstellungen öffnen“ holt einen kurzlebigen Einmal-Steuerungslink vom Server und öffnet genau diesen Worker im Browser – auch ohne Marathon-User-Login
  - Offen: Paketierung – Python mitgeliefert (eingebettet + `requirements/client.txt`) oder eine einzelne `.exe`?
  - Offen: Installation und Autostart – Installer, ZIP + Startskript, Aufgabenplanung oder Autostart-Ordner? Mit oder ohne Adminrechte?
  - Offen: Braucht der Download (`/api/v1/client/download`) eine Anmeldung?
  - Offen: Client-Updates bis zum Auto-Update (Backlog): manuell neu installieren?
  - Offen: Einmal-Link per `GET` oder per Bestätigungs-`POST` einlösen. Vorschlag: `POST`, damit Vorabrufe ihn nicht verbrauchen
  - Installer importiert das Stammzertifikat des mitgelieferten Proxys in den Zertifikatsspeicher des Benutzers; Browser und Worker vertrauen dem Server dann ohne weiteren Schritt
  - Offen: Wie kommt das Stammzertifikat sicher in den Installer (z. B. im Download enthalten und per Fingerabdruck geprüft)?
- [ ] Drei Steuerbefehle am Worker (vor Ort über Tray-Link & für Admins): „Starten“, „Stoppen“ (sanftes Auslaufen / Drain: keine neuen Jobs, laufende Jobs sauber beenden), „Sofort stoppen“ (harter Abbruch, Assets zurück ans letzte Modul, zählt nicht als Versuch)
  - Offen: Anzeigetext für den neuen Status `draining`. Vorschlag: „Läuft aus“
  - Offen: „Starten“ während `draining`. Vorschlag: sofort zurück auf `ready`, laufende Jobs bleiben
  - Offen: `POST /workers/{id}/stop` ändert seine Bedeutung (bisher Abbruch, künftig Drain; Abbruch über neues `/stop-now`). So gewollt? Betrifft auch den noch nicht abgenommenen Release Worker-Steuerung
- [ ] Verfügbarkeitsregeln am Worker durchgängig als Positivliste („Wann ist der Worker verfügbar?“): Zeitplan Mo–So 0–24 Uhr, Inaktivitäts-Start nach X Minuten (Tastatur/Maus + CPU, RAM, GPU unter Schwellenwert; Eigenlast laufender Worker-Jobs deaktiviert die Prüfung nicht), Ausschlussliste „Nur verfügbar, wenn keines der Programme X, Y, Z läuft“
  - Offen: Welche Uhrzeit gilt – die des Rechners oder `timezone` aus der Regel?
  - Offen: Bedeutung einer leeren Regel (`{}`). Vorschlag: immer verfügbar
  - Offen: Prüfregeln für Zeitfenster (`24:00`, Fenster über Mitternacht, Überschneidungen) und Prozessnamen (mit/ohne `.exe`, Groß/klein)
  - Offen: Nach Jobende stehen CPU/RAM durch die eigene Last im 30-s-Mittel noch hoch; der Worker wäre kurz „nicht verfügbar“. Vorschlag: Lastprüfung erst nach Ablauf des Mittelungsfensters wieder einschalten
  - Offen: Ohne GPU-Sensor gilt die GPU-Bedingung als erfüllt?
- [ ] Einstellungen liegen zentral im Marathon-Server; Worker-Client wertet lokale Sensoren aus und meldet Verfügbarkeit samt Grund zurück; bei Verlust der Verfügbarkeit laufen aktive Jobs sauber zu Ende (Drain); Fähigkeiten-Rangliste bleibt Admin-Sache
  - Offen: „Sofort melden“ geht nicht über die bis zu 25 s offene Abfrage. Vorschlag: eigener Endpunkt `POST /api/v1/worker/availability`
  - Offen: Zugeteilter, noch nicht gestarteter Job bei Verlust der Verfügbarkeit. Vorschlag: zurückgeben wie „Nicht abgeholt“, zählt nicht als Versuch
  - Offen: Kennung für geänderte Regeln in der Poll-Antwort (Hash oder Versionsnummer)

### Release Datei- und Ordner-Dialog

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
  - Offen: hängt an der Leseansicht der Worker für normale Nutzer (Release Zugang & Nutzer)
- [ ] Watchfolder-Spalten auf `neu` (links, Funde des laufenden Scans; danach → `erfasst`) und `erfasst` (rechts) umgestellt, `wartet` entfällt; gefundene Assets gehen direkt weiter
  - Offen: Was steht in `neu` – Dateien, die noch auf Stabilität warten, oder Assets, die der laufende Scan gerade angelegt hat?
- [ ] Warten-Modul zeigt Assets während der Wartezeit in der Spalte `wartet`, danach `erledigt` über Ausgang `Erfolg`
  - Offen: Im Release Worker-Steuerung wurde `wartet` gerade entfernt. Bewusst zurück oder Punkt streichen?

#### Datei-Dialog: Quelle „Dieser Rechner“

- [ ] Quelle `Dieser Rechner` (Live-Listing über den ausgehenden Long-Poll des lokal erkannten Workers, abgesichert über `presence_token`; Admins dürfen zusätzlich beliebige Online-Worker wählen; Hinweis + Download-Link falls lokal kein Client läuft)
  - Offen: Verhalten, wenn der Worker gerade nicht antwortet (Zeitlimit 10 s, Meldungstext)

---

## Backlog

### Engine und Worker

- [ ] Auto-Update mit Rückfall & Rollout von Client-Versionen und Modulen (Server gibt Versionen für alle oder ausgewählte Worker frei; Client prüft Prüfsumme, lässt laufende Jobs auslaufen, tauscht die Laufzeit über den Starter und fällt bei Startfehler auf die vorherige Version zurück)
  - Offen: Rollout-Strategie und Versionsverträglichkeit zwischen Server und Client
- [ ] Umstieg auf Linux & Pfadzuordnung je Worker (Server-Worker sieht `/mnt/...` statt `\\server\freigabe`)
- [ ] Erreichbare Speicherorte je Worker: Jobs nur an Worker, die die Pfade erreichen – zusammen mit der Pfadzuordnung
- [ ] Sammeljobs: Ein Modul sammelt mehrere Assets nach einer Regel (z. B. gleicher Schlüssel) und veröffentlicht einen Job; Teilergebnisse je Asset

### Module

- [ ] Teilworkflows als Modul neben den Basis-Modulen, genannt Sub – zurückgestellt bis die Releases Zugang & Nutzer bis Lokale Watchfolder & Rechnerwechsel laufen
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

### Zugang und Sicherheit

- [ ] Anbindung an übergeordnetes Ökosystem (SSO / Landingpage mit Tool-Übersicht ohne zweiten Login in Marathon; nutzt `AuthProvider` und `users.external_subject` aus dem Release Zugang & Nutzer)
  - Offen: Übernimmt der übergeordnete Server nur den Login oder auch mehrere Instanzen/Mandanten?
- [ ] Öffentlicher Zugang aus dem Internet (HTTPS im LAN kommt mit Release HTTPS)
  - Offen: Übernimmt den Zugang von außen ein übergeordneter Server / Reverse-Proxy?

### Feedback Martin
- [ ] wenn Serververbindung verloren ist, kommt overlay - das Icon soll noch ein rotes blinkendes X unten rechts bekommen
- [ ] Überwachen → Einstellungen: Werte ausgegraut - sollte schon umgesetzt sein, aber die Werte sind noch nicht ausgegraut
- [ ] Modul Warten: wenn man auf Assets klickt, kommt eine komplett leere Seite - getestet mit langer Liste über 5k Assets
- [ ] Spinner scheint noch zu fehlen oder zu spät zu kommen - hab ihn trotz Ladezeiten >0.5s noch nie gesehen
- [ ] Modullog soll so hoch sein, wie Seitenpanel und doch nur warnungen und fehler beinhalten, alles außer Info - liegt nichts dergleichen vor, dann steht dort: keine Fehler, keine Warnungen, oder allgemein Keine Vorkommnisse
- [ ] 