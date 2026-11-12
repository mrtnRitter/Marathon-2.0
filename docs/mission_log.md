# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[~]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`

---

## Aktueller Release (eingebaut, Abnahme offen)

### Editor (Abnahme erst möglich, wenn es Module gibt)

- [~] Markierung soll schon Node beinhalten, wenn dieser zu über 50 % vom Auswahlrahmen bedeckt ist
- [~] Sind mehrere Nodes gewählt und wird bei einem Node das Schlosssymbol gedrückt, sind alle gewählten Nodes gelockt. Zum Entlocken muss jeder Node einzeln gewählt werden
- [~] Wenn von einem Node eine Linie gezogen wird, soll das schon funktionieren, wenn die Maus auf dem Feld „Erfolg“ oder „Fehler“ ist, nicht nur auf dem Punkt
- [~] Das Beschreibungsfeld soll sich automatisch nach unten erweitern, wenn der Inhalt größer wird. Umbrüche sind erlaubt (Enter)
- [~] In der Ansicht „Überwachen“ sollen die Nodes nicht verschoben werden können, auch wenn sie in „Bearbeiten“ nicht gelockt sind

---

## Backlog (Reihenfolge offen)

### Engine und Worker

- [ ] Engine: Jobs veröffentlichen, zuteilen, Ergebnisse auswerten
- [ ] Live-Kanal zum Browser
- [ ] Worker-Client: Starter, Client-Laufzeit, Auto-Update mit Rückfall
- [ ] Rollout von Client-Versionen

### Module

- [ ] Modulkatalog und Modul-Schnittstelle, Testmodul „Warten“
- [ ] Module liegen immer im Repo (anders als bei m1)
- [ ] Teilworkflows als Modul neben den Basis-Modulen; dafür einzelne Einstellungen als „public“ markieren, damit sie gleich oben erscheinen
- Reihenfolge:
  - Erster Modul-Release: Watchfolder, Transcoder, Mover, Renamer, QCer
  - Nächster Modul-Release: API-Abfrage ES und VT, Restorer
- Geplante Module:
  - [ ] Watchfolder
  - [ ] Warten
  - [ ] Verschieben, Kopieren (Mover)
  - [ ] Umbenennen
  - [ ] Transcode
  - [ ] QC
  - [ ] Searcher
  - [ ] Restore
  - [ ] Mailversand
  - [ ] Report erstellen
  - [ ] Prioliste
  - [ ] API (EditShare, Veritone, ARK, awork) → später

### Workflows, Assets und Daten

- [ ] Workflow selbst hat keine Priorität, dafür gibt es das Modul „Prioliste“
- [ ] Workflows zeitgesteuert starten (alle x Tage, täglich usw.)
- [ ] Assets hinzufügen: manuell und einfacher Listenimport
- [ ] Aufbewahrung der Daten
  - Offen: Daten pro Workflow oder global aufheben?

### Oberfläche

- [ ] Nachdem ein neuer Workflow angelegt wurde, gleich in „Bearbeiten“ wechseln
- [ ] Workflow pausieren und starten direkt in der Workflow-Übersicht: Spalte nach der Status-Spalte mit Play/Pause-Button. Läuft der Workflow, zeigt der Button das Pause-Symbol; ist er pausiert, das Play-Symbol
- [ ] Klick auf einen Workflow im Dashboard führt in die Workflow-Übersicht
- [ ] Button für Bug-Report (per Mail) unten links im Seitenmenü über dem Pfeil

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
- [] Assets neu verarbeiten: Option: in einem anderen Workflow
- [ ] 