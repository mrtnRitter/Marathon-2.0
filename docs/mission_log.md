# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` offen · `[x]` eingebaut und abgenommen; ob ein Punkt schon eingebaut ist, zeigt der Abschnitt (*Abnahme offen* = eingebaut, *Geplant*/*Backlog* = noch nicht eingebaut)
- Architektur & Vertrag: `docs/spec.md`
- Abgleich mit dem Code: Stand Commit `c72820f` (add module-builder), 27.11.2026

---

## Abnahme offen

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

- [ ] Zusatzprogramme (z. B. ffmpeg) vom Server: Ablage `.data/client-tools/<name>/`, im Client `%LOCALAPPDATA%\MarathonWorker\tools\<name>\<version>\`; Module finden sie über `job.tool("<name>")`
  - Entschieden: Abgleich beim Start und alle 10 Minuten, jede Datei per SHA-256 geprüft; Version = Inhalts-Hash
- [ ] Auto-Update im Client: Angebot laden, Prüfsumme prüfen, Jobs auslaufen lassen, Neustart über den Starter, Rückfall auf die vorherige Version, wenn die neue nicht sauber startet
  - Entschieden: Der Server bietet noch keine Versionen an (Rollout im Backlog); bis dahin neue Version herunterladen und erneut installieren

### Release Modul-Builder – erster Wurf

Vertrag: `docs/spec.md` Teil 4 (Migration `0012_macros`, Worker-Protokoll unverändert). Leitidee: die umfassende Abstraktion – aus atomaren Bausteinen entstehen Makro-Systeme. Ersetzt die Teilworkflows (Sub) aus dem Backlog.

- [ ] Modul-Builder als eigene Seite „Module“: links Bausteine, Mitte Flow, rechts Einstellungen; Module nur im Builder erzeugen
  - Befund: Das Seitenpanel liegt unter dem Editor statt rechts → Fix im Release Modul-Builder – Fixes & Verfeinerung
- [ ] Module ohne Code: Bausteine (Modultypen aus `marathon/modules/`) mit Eingang, Ausgängen, einstellbaren Einstellungen und Zählern
- [ ] Code-Module bleiben Python im Repo; ein freier Code-Block ist verworfen (Ausführung beliebigen Codes auf allen Workern)
- [ ] Nutzer sehen nur Modul und Einstellungen; Innenleben nur Ersteller und Admin im Builder; Sprung „Im Builder öffnen“ aus dem Workflow
- [ ] Bestehende Modultypen werden Standardmodule (gehören dem Admin), bestehende Workflows werden migriert
- [ ] Versionen: Workflows behalten ihre Version; Update-Hinweis im Workflow; Ersteller oder Admin aktualisiert auf Wunsch alle eigenen Workflows

---

## Geplant

Reihenfolge = Umsetzungsreihenfolge. `Offen:` klären wir je Release Punkt für Punkt, bevor der Punkt umgesetzt wird; `Vorschlag:` ist eine Empfehlung, keine Entscheidung. Wo `docs/spec.md` (Teil 3) und ein offener Punkt sich widersprechen, gilt bis zur Klärung der offene Punkt.

### Nächster Release: Freigaben & Besitz (Grundlage)

Ein Freigabesystem für alle teilbaren Objekte; ersetzt alle bisherigen Teillösungen (`workflow_shares`, `can_edit_macro`, „alle Module für alle“, Standardmodul = ohne Eigentümer). Vertrag: `docs/spec.md` Teil 5 (Migration `0013_access`, Worker-Protokoll unverändert). In Umsetzung.

- [ ] Formalisierung der freigabe und berechtigunegen (besitzer und admin dürfen ändern, andere nur nutzen) (Martin)
- [ ] Ein Modell für alle Objekte: dieselbe Prüfung im Server, dieselben Fehlertexte, derselbe Teilen-Dialog
- [ ] Freigabe beim Speichern: privat oder öffentlich; öffentliche Module können andere nur benutzen
- [ ] Bearbeitungssperre für Workflows und Module
- [ ] Logiklöcher aus dem Code-Abgleich:
  - [ ] Nutzer löschen macht seine Module zu Standardmodulen: `owner_user_id NULL` bedeutet heute „Standard“ – danach nur noch für Admins änderbar und nicht mehr löschbar
  - [ ] „Modul wird in Workflows verwendet: …“ und der Zähler „Verwendet“ nennen auch Workflows, die der Nutzer nicht sehen darf
  - [ ] Ein fremder Workflow, der mein Modul verwendet, blockiert das Löschen meines Moduls dauerhaft
  - [ ] „Alle aktualisieren“ erfasst nur eigene Workflows; eine neue Version eines Standardmoduls erreicht fremde Workflows nur über deren Editor
  - [ ] Workflow-Graph, Aktivität, Asset-Detail und Reports liefern Name, Icon, Einstellungen und Logs jedes Moduls an jeden, der den Workflow sieht

Entschieden:
- Objekte mit Besitz und Freigaben: Workflows, Module, künftig Presets; Worker, Nutzer und Einstellungen bleiben reine Admin-Sache (Rolle, keine Freigabe)
- Stufen: keine (privat) < `view` (Ansicht) < `use` (Verwendung) < `edit` (Bearbeitung) < Besitzer; „privat“ ist der Teilen-Hauptschalter, die übrigen bauen aufeinander auf; `run` wird `use`
- Empfänger: einzelne Nutzer und „Alle“; keine Gruppen; wirksam ist die höhere von Nutzer- und Alle-Freigabe
- Besitzer: alle Admins (inkl. Notfallzugang) immer, dazu höchstens ein Nutzer-Besitzer (`owner_user_id`); Anlage durch einen Nutzer → er ist Nutzer-Besitzer, Anlage durch einen Admin → nur „Admin“; Anzeige „Besitzer“ = Nutzer-Besitzer, sonst „Admin“
- Besitzer dürfen alles (teilen, bearbeiten, löschen); nur Besitzer teilen; nur Admins setzen oder entfernen den Nutzer-Besitzer; Admins können nie entzogen werden
- Nutzer gelöscht → `owner_user_id NULL` (nur Admin), seine Freigaben entfallen; neuen Nutzer-Besitzer setzt nur ein Admin
- Standardmodul (Systemmodul) = Modul ohne Nutzer-Besitzer, für „Alle“ mindestens zur Verwendung freigegeben; abgeleitet, kein eigenes Merkmal
- Module sind versioniert: Workflows behalten ihre Version, auch wenn das Modul geändert, gelöscht (archiviert) oder die Freigabe entzogen wird
- Modul aktualisieren: im Workflow per Knopf an der Instanz (Workflow-Stufe `edit`); im Builder „in allen meinen Workflows“ (Besitzer; Admin = alle Workflows); im Bearbeiten gesperrte Workflows werden übersprungen und gemeldet
- Bearbeitungssperre: Ein Objekt, das jemand bearbeitet, ist für alle anderen nur lesbar; Admins heben die Sperre auf, der bisherige Bearbeiter kann danach nicht mehr speichern
- Unsichtbare Workflows erscheinen nirgends und fließen in keine Zahl ein; Worker zeigen nur die Anzahl ihrer Jobs, nicht woran sie arbeiten
- Unsichtbare Module fehlen in Palette, Modulliste und Builder; im Workflow erscheinen sie als Blindmodul
- Duplizieren verlangt, das Innenleben sehen zu dürfen: Workflow ab `view` (Blindmodule bleiben blind), Modul ab `edit`
- Modulsperren je Nutzer entfallen; Lücken werden im Freigabesystem gelöst, keine Sonderregeln
- Admins sehen und dürfen alles (auch Privates), damit nichts verloren geht und das System reparierbar bleibt
- Modul verwenden = in Workflows einfügen und Name und Einstellungen der Instanz ändern; Modul bearbeiten = Innenleben im Builder ändern
- Blindmodul (keine Modul-Freigabe): kein Name, kein Icon, Name nicht änderbar; Zähler, Ein- und Ausgänge mit Beschriftung sichtbar (Fehlerausgang erkennbar); Logs und Meldungen verborgen
- Logs eines Moduls sieht, wer das Modul mindestens sehen darf – damit immer Admins und Modulbesitzer
- Version eines Moduls aktualisieren reicht die Bearbeitung des Workflows; entfernte Blindmodule lassen sich nicht wieder einfügen (Rückfrage beim Entfernen)
- Eingriffe der Admins in fremde Objekte stehen im Verlauf des Objekts
- Matrix und Regeln: `docs/spec.md` §5.2–§5.3

### Release Modul-Builder – Fixes & Verfeinerung

Vertrag: `docs/spec.md` Teil 4; setzt Release Freigaben & Besitz voraus

- [ ] Modul-Builder settings liegen nichts rechts, sondern unter editor - UI kaputt (Martin)
  - Ursache: Das Seitenpanel steht in `ModuleBuilderPage.tsx` neben `BuilderCanvas` im spaltenweise angeordneten `.workflow-editor` statt in der Zeile `.module-builder-body`
- [ ] Befunde aus Martins Test des ersten Wurfs
  - Offen: Liste der Befunde (Bedienung, Darstellung, fehlende Funktionen)

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

### Nacharbeit Worker-Client & Steuerung (später)

Aus der Abnahme des Releases Worker-Client & Steuerung.

- [ ] Downloadseite `https://<Marathon-Adresse>/download` ohne Anmeldung, mit der der Client heruntergeladen wird (nutzt `GET /api/v1/client/download`)
  - Entschieden: Die Zertifikatswarnung des Browsers auf einem Rechner ohne Stammzertifikat wird hier vorerst hingenommen; der installierte Client importiert das Stammzertifikat danach selbst
  - Offen: Inhalt der Seite (nur Schaltfläche oder zusätzlich kurze Installationsschritte) und Verhalten ohne gebautes Client-Paket
- [ ] Client: installation zertificat: braucht es dass auch für den firmen reverse-proxy? Wenn ja, wird cert immer mit installiert, wenn nein, soll es optional im Installer sein (Marathon arbeitet mit einem Proxy - eigenen Proxy verwenden oder Proxy von Marathon (installaliert Zertifikat)). (Martin)
- [ ] Workersteuerung UI: Play und Stop werden zu einem Button mit wechselnden Symbol - Sofort bleibt als extra Button - bei Sofort Aus ist das Stop-Symbol zu weit oben und damit nicht mittig auf dem Button. Vergleiche mit dem Stop-Button. (Martin)
- [ ] Etwas unklar, was jetz die Staus bereit, online und verfügbar darstellen - lässt sich das reduzieren? Verfügbar stellen war als grünen Punkt dar statt "ja" (Martin)
- [ ] feld "wird sofort gestopp" in worker übersicht tabelle oben braucht es nicht - sofort stoppen geht ja schnell (Martin)
- [ ] Client Installer mit Marathon-M als Icon - beachte alle Icon-Größen (16x16 bis 256x256 oder was auch immer der Standard ist) (Martin)
- [ ] Workereinstellungen UI überarbeiten und Rangfolge der Bedingungen klären (Martin)
  - [ ] Tray-Icon updated sich zu langsam
  - [ ] Starten, Stoppen, Sofort Aus direkt über Tray-Menü

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

## Backlog

### Engine und Worker

- [ ] Rollout von Client-Versionen und Modulen über den Server (Server gibt Versionen für alle oder ausgewählte Worker frei, Schaltfläche „Version ausrollen“); die Client-Seite (Prüfsumme, Auslaufen, Starter, Rückfall) ist seit Release Worker-Client & Steuerung eingebaut
  - Offen: Rollout-Strategie und Versionsverträglichkeit zwischen Server und Client
- [ ] Umstieg auf Linux & Pfadzuordnung je Worker (Server-Worker sieht `/mnt/...` statt `\\server\freigabe`)
- [ ] Erreichbare Speicherorte je Worker: Jobs nur an Worker, die die Pfade erreichen – zusammen mit der Pfadzuordnung
- [ ] Sammeljobs: Ein Modul sammelt mehrere Assets nach einer Regel (z. B. gleicher Schlüssel) und veröffentlicht einen Job; Teilergebnisse je Asset
- [ ] Syntethisches Testprogramm für Server - ich kann parallel manuell den Worker killen, um dieses Verhalten zu prüfen (Martin)
- [ ] Ecosystem für Serverupdates: es wird prod und dev umgebung geben. Beide Server dürfen sich nicht beeinflussen und worker müssen immer wissen, welchen server sie verwenden (prod!) - ich brauche ein system, um update für den server von dev auf prod auszurollen. Updates zu modulen (veränderungen, weitere module) sollten ohne server-downzeit erfolgen (Martin)

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
- [ ] Menüpunkt "Presets" - kommt dann mit Transcode: eine gesammelte stelle über alle Presets mit Option, dort neue zu erstellen, bestehende zu verändern - ein modul, dass mit preset arbeitet, gibt in den Modulsettings gleich die option, aktuelle einstellungen als preset zu speichern (neues Preset, bestehendes Überschreiben). (Martin)
- [ ] Modul-Builder: Verschachtelte Module (Modul aus Modulen)
- [ ] Modul-Builder: Begrenzte Ausdruckssprache für kleine Logik (Bedingungen, Namensmuster, Metadaten) ohne Datei-, Netz- oder Importzugriff
- [ ] Modul-Builder: Rollout von Code-Modulen über den Server (siehe „Engine und Worker“)

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
- [ ] Reihenfolge linkes Seitenmenü -> System: Nutzer, Worker, Einstellungen -- Einstellungen immer als letztes, auch wenn noch weitere Optionen dazukommen (Martin; heute: Worker, Einstellungen, Nutzer)
- [ ] Tooltips sollen überall keine Internas preisgeben (IP Adressen, Domain, Server) - immer neutrale Tooltips verwenden (Martin)
- [ ] UI cleanup - Details kommen noch, ich muss mir erstmal alles ansehen - offen für empfehlung zu klaren rollen der UI Elementei (Seitenleisetn, obere Leiste, Tabellen - beispiel: starten ist einmal im oberen Menüband und einmal in der Tabelle - ist oberes Menüband immer global über Mehrfachauswahl? Machen wir das nur in der Tabelle, weil in der echten Welt nicht ständig x Worker ausgeschaltet werden, sondern ab und zu mal einer) (Martin)

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

Eingang für neue Punkte; einsortierte Punkte tragen den Vermerk „(Martin)“.

- [ ] Datenmodell Assets (ein Asset ist die Hülle für Dateien und Metadaten) -> großer Punkt: die greifen Module auf Dateien und Metadaten zu, insbesonder wie wird das im UI nachvollziehbar und verständlich? 
- [ ] 
