# Seekampf-Hub

Eine Weboberfläche für alle Seekampf-Bots, erreichbar im Heimnetz:

```
http://<ip-des-pi>:8080
```

Links eine Seitenleiste (Hamburger-Menü) mit den Bereichen **Übersicht**,
**Flotten-Manager**, **Upgrade-Bot** und **Allianz-Bot**. Einzelne Inseln
lassen sich direkt verlinken, z. B. `#upgrade/447` oder `#flotte/20`.

## Gesundheit

Menüpunkt **Gesundheit** (der Punkt im Menü ist grün/gelb/rot): je Bot der
letzte Durchlauf und ob er fehlerfrei war, Fehler und Warnungen der letzten
24 Stunden (die häufigsten zusammengefasst), wie lange jede Bau-Warteschlange
leer stand und wie viel Produktion durch volle Lager verloren ging (gestern /
heute, „läuft über“ = Lagerhaus wird vorgezogen), Temperatur/Drosselung/Speicher des Pi, Cloudflare-Tunnel und die
nächtliche Sicherung (`~/Seekampf/sicherung.sh`). Code: `gesundheit.py`.

## Upgrade-Bot: Verlauf

Im Bereich **Upgrade-Bot** unten: Linien je Insel über die letzten 60 Tage –
Produktion pro Stunde, Summe der Ausbaustufen, Punkte, und wie viel Produktion
durch volle Lager verpufft ist (mit Tabellenansicht). Daten aus
`Upgrade-Bot/data/verlauf.json` (`GET /api/upgrade/verlauf`); die Tage vor dem
01.10.2026 wurden aus den Logs nachgetragen, Punkte gibt es erst ab da.

## Rohstoff-Ausgleich

Menüpunkt **Rohstoff-Ausgleich**: Hauptschalter und Standardwerte; je Insel
die Schalter *gibt ab* / *bekommt* (beides möglich – alle Inseln gleichen sich
gegenseitig aus), eigene Grenzen (auffüllen bis, behält mindestens, max. je
Lieferung), Bestand, Unterwegs, Bedarf bzw. Überschuss; die letzten Lieferungen. Die Logik
läuft im Flotten-Manager (`Flotten-Manager/ausgleich.py`).

## Kolonisation und Ausbildung

Menüpunkte **Kolonisation** (Warteschlange freier Inseln, Bau-Inseln,
Verlauf; Code `Kolonisations-Bot/`) und **Ausbildung** (Soll-Truppen je Insel
und Einheit; Code `Ausbildungs-Bot/`). Beide Bots haben wie der
Flotten-Manager eine eigene Schnittstelle (8082 bzw. 8083), der Hub reicht
`/api/kolonie/...` und `/api/ausbildung/...` durch. Auf der Karte hat jede
freie Insel den Knopf „Zur Kolonisation“. Unter Kolonisation steht auch das
Muster, nach dem neue Inseln automatisch umbenannt werden.

Im **Upgrade-Bot** hat jede Insel eine Liste **Ausbauziele** (Gebäude +
Stufe, der Reihe nach): der Bot baut nur das erste noch nicht erreichte Ziel
und wartet, bis es bezahlbar ist; nicht verfügbare oder ausgebaute werden
übersprungen, gesperrte trotzdem gebaut; danach baut er wieder automatisch.
Eine Priorisierung geht vor.

## Karte

Menüpunkt **Karte**: die ganze Welt (100 × 100 Sektoren) statt der 3 × 3
Sektoren im Spiel.

- Hervorheben nach **Spieler** (Standard: du) oder **Allianz**: im
  Allianz-Modus leuchtet jede Insel in ihrer Allianzfarbe (12 kräftige Farben,
  angelehnt an map.seekampf.brckr.de), gildenlose grau. Zugeordnet wird nach
  Allianz-ID, damit Farben stabil bleiben; nah herangezoomt steht das Kürzel an
  jeder Insel.
- **Meine Flotten** mit Strecke und einem Punkt, der nach Abfahrt/Ankunft/
  Rückkehr mitwandert (Hinweg durchgezogen, Rückweg gestrichelt; Form = Mission).
- **Angriffe auf mich** in Rot, mit Absender und Ankunft.
- Ebenen: freie Inseln & Ruinen, Raid-Ziele & Scan-Radius des Flotten-Managers,
  Änderungen seit dem letzten Scan, Sektorgitter.
- Insel anklicken: Details, Fahrzeiten ab deinen Inseln; bei eigenen Inseln
  Fahrzeit-Ringe (10/30/60 min) je Schiffstyp.
- Suche nach Koordinate (`52:48:21`, `52:48`) oder Spieler.

Gescannt wird täglich um 1:00 Uhr und per Knopf „Karte jetzt scannen“
(≈ 1.150 Abfragen, gut eine Minute). Dafür hat der Hub einen eigenen
API-Schlüssel in `.env` (`SEEKAMPF_API_KEY=…`). Ergebnis in `data/karte.json`,
der vorige Stand in `data/karte_vorher.json` (für die Änderungen).

## Zugriff von unterwegs: https://seekampf-hub.com

Über einen **Cloudflare Tunnel** mit **Cloudflare Access** davor – ohne
Portfreigabe im Router, im normalen Browser (auch am Firmenrechner).

- Login: Cloudflare Access (Team `seekampf-hub`), nur die freigegebene
  E-Mail-Adresse, Code per Mail. Einstellungen im Cloudflare-Dashboard unter
  Zero Trust → Access → Applications → Seekampf-Hub.
- Tunnel `seekampf-hub` (ID: `cloudflared tunnel list`), Dienst
  `cloudflared.service`, Konfiguration `/etc/cloudflared/config.yml`.
- Doppelte Absicherung: cloudflared prüft selbst das Access-Token
  (`originRequest.access`, AUD-Tag der Anwendung). Ohne gültigen Login kommt
  nichts beim Hub an, auch wenn im Dashboard einmal die Regel fehlt.
- Wird die Access-Anwendung neu angelegt, ändert sich der AUD-Tag – dann in
  `/etc/cloudflared/config.yml` eintragen und `sudo systemctl restart cloudflared`.
- Im Heimnetz bleibt `http://<pi>:8080` ohne Login erreichbar.

```bash
systemctl status cloudflared          # Tunnel läuft?
cloudflared tunnel info seekampf-hub  # Verbindungen zu Cloudflare
```

## Wie der Seekampf-Hub mit den Bots redet

Die Bots bleiben eigenständige Dienste. Stürzt einer ab, bleibt die Seite
erreichbar und zeigt das an.

| Bot | Weg |
| --- | --- |
| Flotten-Manager | eigene JSON-Schnittstelle auf `127.0.0.1:8081`, der Seekampf-Hub reicht `/api/flotte/...` durch |
| Kolonisations-Bot, Ausbildungs-Bot | ebenso auf `127.0.0.1:8082` / `8083`, durchgereicht unter `/api/kolonie/...` / `/api/ausbildung/...` |
| Upgrade-Bot, Allianz-Bot | Dateien in `data/` des Bots: `steuerung.json` (schreibt der Seekampf-Hub), `befehle/*.json` (führt der Bot aus und löscht sie), `status.json` (schreibt der Bot) |
| systemd | Status per `systemctl show`; Starten/Stoppen/Neustarten per `sudo`, erlaubt nur über `seekampf-hub.sudoers` |

Ungespeicherte Formulare werden beim Wechsel des Bereichs und beim Schließen
der Seite abgefragt.

Schutz gegen Cross-Site-Requests: Ändernde Anfragen (POST/PUT) brauchen den
Kopf `X-Seekampf-Hub: 1`, den nur die eigene Seite setzt. Sonst könnte jede
Webseite, die man im Heimnetz öffnet, unbemerkt Dienste stoppen. Von Hand:
`curl -H 'X-Seekampf-Hub: 1' -X POST …`.

`steuerung.json` des Upgrade-Bots schreiben Hub und Bot (neue Inseln) unter
derselben Dateisperre `data/steuerung.lock`.

## Einrichtung

```bash
cd ~/Seekampf/Seekampf-Hub
uv sync
sudo install -m 0440 seekampf-hub.sudoers /etc/sudoers.d/seekampf-hub
sudo visudo -c
sudo cp seekampf-hub.service seekampf-hub-alert.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seekampf-hub
```

Die sudo-Regel erlaubt genau Start/Stopp/Neustart der fünf Bots und den
Neustart des Seekampf-Hubs – ohne Platzhalter und ohne gespeichertes Passwort.

## Dateien

| Datei | Aufgabe |
| --- | --- |
| `karte.py` | Welt-Scan, täglicher Planer, Vergleich, Angriffe |
| `gesundheit.py` | Auswertung für die Gesundheitsseite |
| `alert.py` / `seekampf-hub-alert.service` | Telegram-Alarm, wenn systemd den Hub aufgibt (Token in `.env`) |
| `hub.py` | FastAPI-App: Seite, Dienste, Logs, Weiterleitung, Steuerdateien |
| `static/index.html` | die ganze Oberfläche (ein File, kein Build) |
| `seekampf-hub.service` | systemd-Unit |
| `seekampf-hub.sudoers` | sudo-Regel für die Dienst-Knöpfe |
