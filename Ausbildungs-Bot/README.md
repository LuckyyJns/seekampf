# Ausbildungs-Bot

Hält Truppen und Schiffe je Insel auf einem Mindestbestand. Eingestellt wird im
Seekampf-Hub unter **Ausbildung**:

- **Standard für alle Inseln**: ein Mindestbestand je Posten (Steinewerfer,
  Speerkämpfer, Bogenschützen, Späh-, Handels- und Kriegsschiffe). Leer heißt
  keine Vorgabe. Kolonisationsschiffe gehören dem Kolonisations-Bot.
- **Eigene Werte je Insel**: ein Wert überschreibt den Standard, leer heißt
  Standard. „Hier nicht verwalten“ nimmt einen Posten auf der Insel heraus,
  der Insel-Schalter die ganze Insel.
- **Von Hand ausbilden**: in der Insel-Ansicht eine Kachel anklicken, Anzahl
  wählen, ausbilden (`POST /api/inseln/{id}/ausbilden`).

Wo weder Standard noch eigener Wert gilt, wird der Posten weder aufgefüllt
noch gibt die Insel welche ab. Schiffe werden nur vor Ort im Hafen gebaut
(kein Transport zwischen Inseln, Mindestauftrag 1 statt 5).

**Vorhanden** zählt: daheim + mit eigenen Raids unterwegs + in Ausbildung +
per Handel im Anflug (Schiffe: daheim + auf Fahrt + in Ausbildung). An Mitspieler Verliehenes zählt nicht und wird
nachgebildet.

Fehlt etwas:

1. Kann die Kaserne der Insel die Einheit ausbilden, bildet sie selbst aus -
   so viel, wie die Rohstoffe hergeben (ohne die Reserve des
   Kolonisations-Bots). Reicht es nur für einen Teil, wird erst ab 5 Einheiten
   bestellt.
2. Sonst bringt die nächstgelegene Insel ihren Überschuss (alles über ihrem
   eigenen Soll) per Handelsfahrt auf Kriegsschiffen (klein 8, groß 30
   Plätze). Sind dort zu wenige Kriegsschiffe daheim, hält der Flotten-Manager
   sie zurück (`data/reserve.json`), bis sie da sind.
3. Hat keine Insel Überschuss, bildet die nächstgelegene Insel mit passender
   Kaserne und eigenem Soll für die Einheit die fehlende Menge zusätzlich aus
   und schickt sie dann.

Bedrohte Inseln geben keine Truppen ab.

## Betrieb

```bash
cd ~/Seekampf/Ausbildungs-Bot
cp .env.example .env            # eigenen SEEKAMPF_API_KEY eintragen
uv sync
sudo cp seekampf-ausbildungs-bot*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seekampf-ausbildungs-bot
tail -f logs/ausbildung-$(date +%F).log
```

Schnittstelle auf `127.0.0.1:8083`, der Hub reicht `/api/ausbildung/...` durch:
`GET /api/status`, `POST /api/standard {posten: Zahl|null}`,
`POST /api/inseln/{id}/soll {posten: Zahl|null, aus: [...], aktiv: bool}`,
`POST /api/inseln/{id}/ausbilden {einheit, anzahl}`, `POST /api/control {aktion}`.
