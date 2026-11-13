# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[]` eingebaut, Abnahme offen · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`


### Abnahme offen

- [] Markierung soll schon Node beinhalten, wenn dieser zu über 50 % vom Auswahlrahmen bedeckt ist
- [] Sind mehrere Nodes gewählt und wird bei einem Node das Schlosssymbol gedrückt, sind alle gewählten Nodes gelockt. Zum Entlocken muss jeder Node einzeln gewählt werden
- [] Wenn von einem Node eine Linie gezogen wird, soll das schon funktionieren, wenn die Maus auf dem Feld „Erfolg“ oder „Fehler“ ist, nicht nur auf dem Punkt
- [] Das Beschreibungsfeld soll sich automatisch nach unten erweitern, wenn der Inhalt größer wird. Umbrüche sind erlaubt (Enter)
- [] In der Ansicht „Überwachen“ sollen die Nodes nicht verschoben werden können, auch wenn sie in „Bearbeiten“ nicht gelockt sind
- [] Assets neu verarbeiten: Option: in einem anderen Workflow

---
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
- [ ] Teilworkflows als Modul neben den Basis-Modulen, genannt Sub
- [ ] Modulpanel mit ausklappbaren Kategorien zur Sortierung 
- Geplante Module:
  - [ ] Watchfolder
  - [ ] Warten.
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
- [] wenn man auf Bug melden klickt, soll an der Stelle des Buttons (etwas darüber), eine kleine Sprechblase mit einem Katzengeräusch kurz erscheinen (Murr, Mautz, Miau, Mau, Greep, Brrr und so ähnlich) - die Meldung ist immer zufällig gewählt
