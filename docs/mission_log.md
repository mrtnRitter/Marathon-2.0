# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`

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
- [ ] Module für den ersten Release: Watchfolder, Transcoder, Mover, Renamer, QCer
- [ ] Module für den nächsten Release: API-Abfrage ES und VT, Restorer
- [ ] Teilworkflows als Modul neben den Basis-Modulen; dafür einzelne Einstellungen als „public“ markieren, damit sie gleich oben erscheinen

### Workflows, Assets und Daten

- [ ] Workflows speichern (Versionen, Module, Verbindungen)
- [ ] Assets hinzufügen: manuell und einfacher Listenimport
- [ ] Dummy-Daten überall löschen
- [ ] Aufbewahrung der Daten
  - Offen: Daten pro Workflow oder global aufheben?

### Reports

- [ ] Reports ausbauen: weitere Filter und Kennzahlen
- [ ] Export von Reports als CSV (nächster Release)

### Zugang und Sicherheit

- [ ] Andockstelle Login
- [ ] HTTPS-Verbindung
  - Offen: Braucht m2 das Zertifikat selbst, oder übernimmt das der übergeordnete Server/Reverse-Proxy?
- [ ] Workflows freigeben/sperren
  - Offen: Wie werden Workflows freigegeben und gesperrt?
  - Idee Ökosystem: Login auf einer Landingpage mit Übersicht der Tools, u. a. m2 → in m2 sind bestimmte Workflows gesperrt; ungern ein zweiter Login in m2

### UI
- [] Markierung soll schon Node beinhalten, wenn dieser zu über 50% vom Auswahlrahmen bedeckt ist
- [ ] Sind mehrer Nodes gewählt und wird bei einem Node das Schlossymbol gedrückt, sind alle gewählten Nodes gelockt. Zum Entlocken muss jeder Node einzeln gewählt werden
- [ ] Wenn von meinem Node ein Linie gezogen wird, soll das schon funktioniern, wenn die Maus auf dem Feld "Erfolg" oder "Fehler" ist, nicht nur auf dem Punkt. 
- [ ] Das Beschreibungsfeld soll sich automatisch nach unten erweitern, wenn der Inhalt größer wird. Umbrüche sind erlaubt (enter)
- [ ] In der Ansicht "Überwachen", sollen die Nodes nicht verschoben werden können, auch wenn sie in Edit nicht gelockt sind