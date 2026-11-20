<!-- Überarbeiteter Entwurf (v2: User und Worker strikt getrennt) zum Einfügen in docs/mission_log.md unter "## Geplant".
     Ersetzt den bisherigen Abschnitt "### Nächster Release: Watchfolder auf Workern, Live-Kanal"
     und übernimmt die passenden Punkte aus Backlog (Worker-Client, Login, Workflows freigeben). -->

### Nächster Release: Infrastruktur – Nutzer, Worker-Client, lokale Watchfolder & Datei-Dialog

Vertrag: `docs/infrastructure_spec.md`

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
