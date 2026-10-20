# Seekampf-Hub

Eine Weboberfläche für alle Seekampf-Bots, erreichbar nur über
**https://seekampf-hub.com** (Cloudflare Tunnel + Access, siehe unten). Der
Hub selbst lauscht nur auf `127.0.0.1:8080` (`HUB_HOST`), im Heimnetz ist er
nicht direkt erreichbar.

Links eine Seitenleiste (Hamburger-Menü) mit den Bereichen **Übersicht**,
**Flotten-Manager**, **Upgrade-Bot** und **Allianz-Bot**. Einzelne Inseln
lassen sich direkt verlinken, z. B. `#upgrade/447` oder `#flotte/20`.

## Design

Pixel-Optik nach `docs/design/dashboard-ui-referenz` (Repo SeekampfBot): Pergament-Logbuch
auf Holzdielen, Pixelschrift (Pixelify Sans) und die freigestellten Pixel-Icons. Das Aussehen
steckt in `static/pixel.css`, die Icons liegen unter `static/icons/` und werden über `/static`
ausgeliefert. „Thema" wechselt zwischen Pergament (hell) und einer dunklen Holz-Variante.

## Gesundheit

Menüpunkt **Gesundheit** (der Punkt im Menü ist grün/gelb/rot): je Bot der
letzte Durchlauf und ob er fehlerfrei war, Fehler und Warnungen der letzten
24 Stunden (die häufigsten zusammengefasst), wie lange jede Bau-Warteschlange
leer stand und wie viel Produktion durch volle Lager verloren ging (gestern /
heute, „läuft über“ = Lagerhaus wird vorgezogen), Temperatur/Drosselung/Speicher des Pi, Cloudflare-Tunnel und die
nächtliche Sicherung (`~/Seekampf/sicherung.sh`). Code: `gesundheit.py`.

Jeder Bot schreibt nach jedem Takt selbst ein **Lebenszeichen**
(`seekampf_gemeinsam/lebenszeichen.py`, Datei im Arbeitsspeicher unter
`/dev/shm/seekampf-lebenszeichen/<bot>.json`): letzter Takt, Fehler in Folge
und die Warnungen/Fehler der letzten 24 h. Daraus zählt die Seite die Fehler
(nicht mehr aus den Logdateien) und meldet **„hängt“**, wenn der letzte Takt
länger als das 5-Fache des Takts zurückliegt, obwohl der Dienst läuft
(Flotten-Manager 25 s, Allianz-Bot 75 s, Kolonisation 2,5 min, Ausbildung
5 min, Upgrade-Bot 25 min). Nur im Hub, keine Telegram-Meldung.

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
Verlauf; Code `Kolonisations-Bot/`) und **Ausbildung** (Mindestbestand an
Truppen und Schiffen: Standard für alle Inseln, eigene Werte je Insel, Kachel
anklicken = Anzahl wählen und von Hand ausbilden; Code `Ausbildungs-Bot/`). Beide Bots haben wie der
Flotten-Manager eine eigene Schnittstelle (8082 bzw. 8083), der Hub reicht
`/api/kolonie/...` und `/api/ausbildung/...` durch. Auf der Karte hat jede
freie Insel den Knopf „Zur Kolonisation“. Unter Kolonisation steht auch das
Muster, nach dem neue Inseln automatisch umbenannt werden.

Im **Upgrade-Bot** zeigt jede aufgeklappte Insel alle Gebäude als Kacheln (Icon,
Stufe, Kosten, Bauzeit): ein Klick reiht den Ausbau ein (mit Rückfrage).
Außerdem hat jede Insel eine Liste **Ausbauziele** (Gebäude +
Stufe, der Reihe nach): der Bot baut nur das erste noch nicht erreichte Ziel
und wartet, bis es bezahlbar ist; nicht verfügbare oder ausgebaute werden
übersprungen, gesperrte trotzdem gebaut; danach baut er wieder automatisch.
Eine Priorisierung geht vor. **Nebenher bauen** (je Insel, Standard an):
während auf das Ziel gespart wird, startet der Bot andere Ausbauten nur, wenn
das Ziel danach trotzdem genauso früh bezahlbar ist und die Warteschlange bis
dahin wieder frei ist (`Upgrade-Bot/bot.py`, `_nebenher_waehlen`).

## Rohstoff-Bilanz und Reservierungen

Menüpunkt **Rohstoff-Bilanz**: je Tag und Insel, woher die Rohstoffe kamen
(Produktion, Raids, Ausgleich, Allianz) und wohin sie gingen (Ausbau,
Ausbildung, Kolonisation, Allianzkasse, Ausgleich); Zeitraum heute / gestern /
7 / 14 Tage, eine oder alle Inseln, dazu eine Übersicht je Insel. Die Bots
buchen selbst (`seekampf_gemeinsam.bilanz`, Dateien in
`~/Seekampf/data/bilanz/`); „nicht zugeordnet“ ist der Rest zur Lagerveränderung
laut Upgrade-Bot (Quests, Plünderung, Handarbeit). `GET /api/bilanz`.

Darunter die **Reservierungen**: was welcher Bot gerade auf welcher Insel
zurückhält (`~/Seekampf/data/reservierungen/`, `GET /api/reservierungen`).

## Flotten-Manager: Unterbereiche einklappen

In jeder aufgeklappten Insel lassen sich „Flotten unterwegs“ und
„Zielrotation“ per Klick auf die Überschrift einklappen (Anzahl steht daneben).
Der Zustand bleibt im Browser gemerkt (`zu-unterteile` im localStorage).

## Allianzkasse

Im Bereich **Allianz-Bot**: Abschnitt „Überlauf → Allianzkasse“ mit
eigenem Schalter, Schwellen und den letzten Einzahlungen
(`POST /api/allianz/kasse` schreibt den Abschnitt `kasse` in die
`steuerung.json` des Allianz-Bots).

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

## Spieler

Menüpunkt **Spieler**: die anderen Spieler Tag für Tag (`spieler.py`).

- Ganz oben: wie viele **Schiffe und Truppen** weltweit dazugekommen sind, je
  Typ und Tag (Häfen/Garnisonen + auf See, wie im Spiel unter „Statistiken“),
  dazu Spieler und bewohnte Inseln. Je Spieler gibt das Spiel Schiffe und
  Truppen nicht heraus.
- Allianzen: Rang, Mitglieder, Punkte, Veränderung am letzten Tag.
- Alle Spieler in der Reihenfolge der Rangliste, aufklappbar. Je Tag: Rang,
  Punkte, Inseln; welche Inseln dazugekommen sind und verloren gingen (bei
  einem Wechsel unter Spielern: von wem / an wen), Allianzwechsel, neu
  erfüllte Quests, welche Inseln ausgebaut wurden (Inselpunkte).

Stand täglich um 1:00 Uhr (beim allerersten Start sofort als Ausgangsstand;
war der Hub um 1:00 aus, wird nachgeholt): Rangliste, Profil jedes Spielers,
Allianz-Rangliste und `GET /stats/overview` - gut 60 Abfragen, rund 10 s, mit
dem API-Schlüssel des Hubs. Gespeichert in `data/spieler.json` (365 Tage,
gut 50 KB je Stand); ein „Tag“ ist der Vergleich zweier Stände. `GET /api/spieler`.

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
- Der Hub lauscht nur auf `127.0.0.1:8080`; ohne Tunnel kommt niemand dran,
  auch nicht aus dem Heimnetz (auf dem Pi selbst: `curl http://localhost:8080`).

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
Webseite, die man im eingeloggten Browser öffnet, unbemerkt Dienste stoppen. Von Hand:
`curl -H 'X-Seekampf-Hub: 1' -X POST …`.

`steuerung.json` des Upgrade-Bots schreiben Hub und Bot (neue Inseln) unter
derselben Dateisperre `data/steuerung.lock`.

Befehle (`befehle/*.json`) löscht der Upgrade-Bot **vor** der Ausführung
(`steuerung.befehle_holen`). Das ist gewollt: ein Befehl läuft so nie zweimal,
auch wenn er scheitert. Stürzt der Bot genau zwischen Löschen und Ausführen
ab, ist der Befehl verloren – dann im Hub einfach noch einmal auslösen. Das
Ergebnis jedes Befehls steht in `status.json` unter `befehle`.

Gleichzeitige Zugriffe: Kolonisations- und Ausbildungs-Bot halten während
ihres Takts die Sperre ihres Zustands; Änderungen aus dem Hub warten so lange
(Sekunden). Der Flotten-Manager hält sie bewusst nicht über seinen Takt (die
Seite hinge sonst bei jedem Takt); er kopiert seinen Zustand für die
Schnittstelle und wiederholt die Kopie, falls sie mitten in eine Änderung fällt.

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
| `spieler.py` | Spieler-Tracker: täglicher Stand um 1:00, Vergleich je Tag |
| `gesundheit.py` | Auswertung für die Gesundheitsseite |
| `alert.py` / `seekampf-hub-alert.service` | Telegram-Alarm, wenn systemd den Hub aufgibt (Token in `.env`) |
| `hub.py` | FastAPI-App: Seite, Dienste, Logs, Weiterleitung, Steuerdateien |
| `static/index.html` | Gerüst der Oberfläche (HTML, kein Build) |
| `static/hub.css`, `static/pixel.css` | Aussehen (Grundlage, Pixel-Optik) |
| `static/js/*.js` | Skripte je Bereich; laufen der Reihe nach im selben globalen Bereich - was beim Laden gebraucht wird, muss in einer früheren Datei stehen (`hilfen.js`, `akkordeon.js` zuerst, `start.js` zuletzt) |
| `seekampf-hub.service` | systemd-Unit |
| `seekampf-hub.sudoers` | sudo-Regel für die Dienst-Knöpfe |
