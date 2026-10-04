# Ausbildungs-Bot

Hält die Truppen je Insel auf Soll. Eingestellt wird im Seekampf-Hub unter
**Ausbildung**: je Insel und Einheit (Steinewerfer, Speerkämpfer,
Bogenschützen) eine Zahl. Leer heißt keine Vorgabe - die Insel wird für diese
Einheit weder aufgefüllt noch gibt sie welche ab.

**Vorhanden** zählt: daheim + mit eigenen Raids unterwegs + in Ausbildung +
per Handel im Anflug. An Mitspieler Verliehenes zählt nicht und wird
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
`GET /api/status`, `POST /api/inseln/{id}/soll {einheit: Zahl|null}`,
`POST /api/control {aktion}`.
