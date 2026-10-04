# Kolonisations-Bot

Kolonisiert freie Inseln (auch Ruinen) aus einer Warteschlange, die du im
Seekampf-Hub unter **Kolonisation** pflegst. Ziele wählt er nie selbst aus:
Koordinate eintragen (oder auf der Karte Insel anklicken → „Zur
Kolonisation“), Reihenfolge mit ▲/▼, entfernen mit ✕.

## Ablauf je Ziel

| Status | Bedeutung |
| --- | --- |
| wartet | keine Insel kann gerade ein Schiff bauen (oder alle bauen schon eins) |
| spart | die nächstgelegene geeignete Insel spart auf das Kolonisationsschiff |
| Schiff in Ausbildung | das Schiff wird im Hafen gebaut (6 h) |
| Schiff bereit | liegt im Hafen; fährt los, sobald keine Bedrohung mehr anfliegt |
| unterwegs | fährt zum Ziel (3 Knoten) |

**Geeignet** ist eine Insel, deren Hafen das Schiff bauen kann (Stufe 20) und
deren Lager die Kosten fasst (größter Posten 35.500 Holz). Für jedes Ziel baut
die nächstgelegene geeignete Insel das Schiff; erfüllt sie die Bedingungen
nicht, nimmt der Bot die nächste. Jede Insel baut höchstens ein Schiff zur
Zeit - mehrere Ziele laufen so parallel auf verschiedenen Inseln. Liegt
schon irgendwo ein freies Kolonisationsschiff (z. B. nach einem Rückruf),
nimmt das nächste Ziel das nächstgelegene davon.

**Ansparen:** Solange eine Insel spart, stehen die Schiffskosten in
`data/reserve.json`. Upgrade-Bot, Rohstoff-Ausgleich und Ausbildungs-Bot
geben auf dieser Insel nur aus, was darüber liegt. Ist der Bot aus (Datei älter
als 10 min), gilt die Reservierung nicht mehr.

**Besiedelt:** Wartende Ziele werden alle 5 min geprüft, direkt vor der
Abfahrt noch einmal und unterwegs jede Minute, solange das Schiff
zurückgerufen werden kann (und 30 s vor Ende ein letztes Mal). Hat das Ziel
einen Besitzer, fliegt es aus der Warteschlange; ein fahrendes Schiff wird
zurückgerufen und nimmt das nächste Ziel. Telegram meldet Erfolg, entfernte
und gescheiterte Ziele.

Der Flotten-Manager lässt Kolonisationsfahrten in Ruhe.

## Neue Inseln umbenennen

Jede neu dazukommende Insel (egal, wie sie dazukam) wird nach einem Muster
umbenannt, Standard `GiG {n}`: `{n}` ist die höchste schon vergebene Nummer
+ 1, eine Insel namens „GiG“ zählt als 1. Beim ersten Start werden die
vorhandenen Inseln nur erfasst (`bekannte_inseln` in `data/state.json`), nie
umbenannt. An/aus und Muster im Hub unter **Kolonisation**
(`POST /api/umbenennen {aktiv, muster}`).

## Betrieb

```bash
cd ~/Seekampf/Kolonisations-Bot
cp .env.example .env            # eigenen SEEKAMPF_API_KEY eintragen
uv sync
sudo cp seekampf-kolonisations-bot*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seekampf-kolonisations-bot
tail -f logs/kolonie-$(date +%F).log
```

Schnittstelle auf `127.0.0.1:8082`, der Hub reicht `/api/kolonie/...` durch:
`GET /api/status`, `POST /api/warteschlange {koordinaten}`,
`POST /api/warteschlange/{id}/verschieben {richtung: -1|1}`,
`POST /api/warteschlange/{id}/entfernen`, `POST /api/control {aktion}`.

| Datei | Aufgabe |
| --- | --- |
| `web.py` | Einstiegspunkt: Schnittstelle + Bot-Thread |
| `kolonisation.py` | Zuordnung, Ansparen, Ausbildung, Abfahrt, Überwachung |
| `state.py` | `data/state.json`: Warteschlange, Verlauf, an/aus |
