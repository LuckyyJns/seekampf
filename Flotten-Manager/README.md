# Flotten-Manager

Schickt vollautomatisch Raid-Flotten auf die freien Inseln rund um die eigene
Insel und steuert sich ueber eine Weboberflaeche im Heimnetz.

```
http://<ip-des-pi>:8080
```

## Was er tut

1. **Scannen** – sucht im eingestellten Radius (in Sektoren) alle freien Inseln,
   also solche ohne Besitzer: frisch angelegte unbewohnte Inseln und Ruinen.
   Inseln mit Besitzernamen bleiben aussen vor, auch die "Unbemannt NNN", die
   trotz ihres Namens dem Spieler *Niemend* gehoeren.
2. **Losschicken** – so viele Flotten gleichzeitig, wie Schiffe und Steinewerfer
   hergeben (Standard je Flotte: 1 kleines Kriegsschiff, 1 kleines
   Handelsschiff, 1 Steinewerfer). Die Ziele kommen stur reihum aus der
   Rotation.
3. **Gegenpruefen** – unmittelbar vor der Abfahrt wird ueber
   `GET /map/island/{x}/{y}/{z}` noch einmal geprueft, ob die Insel wirklich
   frei ist. Hat sie inzwischen einen Besitzer, fliegt sie aus der Rotation und
   die Flotte nimmt das naechste Ziel.
4. **Nachhalten** – Ankunft und Rueckkehr jeder Flotte stehen im Log; sobald
   eine Flotte daheim ist, faehrt sie im selben Takt zum naechsten Ziel weiter.
5. **Auswerten** – aus dem Kampfbericht kommen Sieg/Niederlage und die echte
   Beute in die Statistik.

## Bedienung

Die Seite zeigt oben die Kennzahlen (Raids gesamt, Inseln besucht, Gold, Stein,
Holz, Laufzeit, Loot/Stunde), darunter die fahrenden Flotten mit Countdown, die
Zielrotation, alle Einstellungen und das Log. Start/Stop sitzt oben rechts.

Alle Einstellungen greifen sofort, ohne Neustart. Die wichtigsten:

| Einstellung | Bedeutung |
| --- | --- |
| `scan_radius_sektoren` | 0 = eigener Sektor, 1 = 3×3 Sektoren, 2 = 5×5 … |
| `scan_intervall_stunden` | wie oft neu nach freien Inseln gesucht wird (24 = taeglich) |
| `flotte_*` | Zusammensetzung je Flotte; mehr Handelsschiffe = mehr Beute (75 je Schiff) |
| `max_flotten` | 0 = so viele gleichzeitig, wie Schiffe da sind |
| `rohstoff_modus` | `knappster` pluendert gezielt den Rohstoff, von dem am wenigsten da ist |
| `lager_voll_schwelle` | ab diesem Fuellstand aller drei Rohstoffe wird pausiert |
| `niederlagen_bis_blacklist` / `blacklist_tage` | wann ein Ziel gesperrt wird und wie lange |
| `rueckruf_bei_bedrohung` | holt fahrende Flotten zurueck, wenn ein Angriff im Anflug ist |

### Beute-Priorisierung

`rohstoff_modus = knappster` waehlt den Rohstoff mit dem kleinsten **erwarteten**
Bestand – was die schon fahrenden Flotten mitbringen, ist eingerechnet. Sind
vier Flotten mit Stein unterwegs, hebt das Stein rechnerisch so weit an, dass
die fuenfte von selbst etwas anderes holt. Liegen alle drei Bestaende naeher als
`ausgleich_schwelle` (Anteil der Lagerkapazitaet) beieinander, wird gleichmaessig
gepluendert – dann kommt die Ladung wenigstens voll zurueck.

## Warum nur ein Steinewerfer reicht – und wann nicht

Eine freie Insel ohne jede Restgarnison verliert automatisch, sobald der
Angreifer mindestens eine Einheit landet. Steht dort aber auch nur **eine**
Einheit, zaehlt zusaetzlich die Basis-Inselverteidigung von 50 – dagegen
verliert ein einzelner Steinewerfer (Angriff 5) sicher und geht verloren. Frisch
angelegte freie Inseln sind leer, Ruinen verlassener Spieler koennen eine
Restgarnison haben. Deshalb die Blacklist: nach `niederlagen_bis_blacklist`
Niederlagen in Folge ist das Ziel fuer `blacklist_tage` gesperrt.

## Einrichtung

```bash
cd ~/Seekampf/Flotten-Manager
cp .env.example .env          # SEEKAMPF_API_KEY eintragen
uv sync                       # .venv anlegen
.venv/bin/python web.py       # von Hand starten
```

Als Dauerdienst:

```bash
sudo cp seekampf-flotten-manager.service seekampf-flotten-manager-alert.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seekampf-flotten-manager
journalctl -u seekampf-flotten-manager -f
```

Der Dienst startet nach einem Neustart von selbst und raidet sofort weiter;
fahrende Flotten werden aus `data/state.json` wieder uebernommen.

### Ausfall-Alarm

Gibt systemd den Dienst auf (fuenf Fehlstarts in zehn Minuten), zieht
`OnFailure=` die Unit `seekampf-flotten-manager-alert.service` hoch, die per
Telegram meldet, dass keine Flotte mehr faehrt - samt der letzten Logzeilen.
Das ist noetig, weil der Manager in genau diesem Moment selbst nichts mehr
senden kann. Von Hand pruefen:

```bash
sudo systemctl start seekampf-flotten-manager-alert
```

Der Flotten-Manager benutzt einen **eigenen API-Schluessel**, nicht den des
Ressourcen-Bots - so laesst sich einer von beiden zurueckziehen, ohne den
anderen lahmzulegen.

## Dateien

| Datei | Aufgabe |
| --- | --- |
| `web.py` | Einstiegspunkt: uvicorn + Bot-Thread, alle HTTP-Endpunkte |
| `manager.py` | der Takt: Flotten abgleichen, Berichte auswerten, losschicken |
| `scanner.py` | Bereich abklappern, Zielliste pflegen |
| `geo.py` | Feldkoordinaten, Entfernung, Fahrzeit |
| `state.py` | `data/state.json`: Einstellungen, Ziele, Flotten, Statistik |
| `api_client.py` | die genutzten Seekampf-Endpunkte |
| `report.py` / `notify.py` | Telegram-Morgenreport |
| `alert.py` | Telegram-Alarm, den systemd bei Ausfall des Dienstes startet |
| `static/index.html` | die Weboberflaeche (ein File, kein Build) |
| `logs/flotte-JJJJ-MM-TT.log` | eine Logdatei pro Tag, 60 Tage Aufbewahrung |
