# Seekampf-Hub

Eine Weboberfläche für alle Seekampf-Bots, erreichbar im Heimnetz:

```
http://<ip-des-pi>:8080
```

Links eine Seitenleiste (Hamburger-Menü) mit den Bereichen **Übersicht**,
**Flotten-Manager**, **Upgrade-Bot** und **Allianz-Bot**. Einzelne Inseln
lassen sich direkt verlinken, z. B. `#upgrade/447` oder `#flotte/20`.

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
- Tunnel `seekampf-hub` (ID `0db7b5ce-50fb-4ff8-b5bc-730687622ff7`), Dienst
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
| Upgrade-Bot, Allianz-Bot | Dateien in `data/` des Bots: `steuerung.json` (schreibt der Seekampf-Hub), `befehle/*.json` (führt der Bot aus und löscht sie), `status.json` (schreibt der Bot) |
| systemd | Status per `systemctl show`; Starten/Stoppen/Neustarten per `sudo`, erlaubt nur über `seekampf-hub.sudoers` |

Ungespeicherte Formulare werden beim Wechsel des Bereichs und beim Schließen
der Seite abgefragt.

## Einrichtung

```bash
cd ~/Seekampf/Seekampf-Hub
uv sync
sudo install -m 0440 seekampf-hub.sudoers /etc/sudoers.d/seekampf-hub
sudo visudo -c
sudo cp seekampf-hub.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seekampf-hub
```

Die sudo-Regel erlaubt genau Start/Stopp/Neustart der drei Bots und den
Neustart des Seekampf-Hubs – ohne Platzhalter und ohne gespeichertes Passwort.

## Dateien

| Datei | Aufgabe |
| --- | --- |
| `karte.py` | Welt-Scan, täglicher Planer, Vergleich, Angriffe |
| `hub.py` | FastAPI-App: Seite, Dienste, Logs, Weiterleitung, Steuerdateien |
| `static/index.html` | die ganze Oberfläche (ein File, kein Build) |
| `seekampf-hub.service` | systemd-Unit |
| `seekampf-hub.sudoers` | sudo-Regel für die Dienst-Knöpfe |
