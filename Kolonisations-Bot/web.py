"""Schnittstelle und Einstiegspunkt des Kolonisations-Bots.

Ein Prozess: uvicorn bedient die JSON-Schnittstelle auf 127.0.0.1:8082, ein
Hintergrund-Thread arbeitet die Warteschlange ab. Die Oberflaeche liefert der
Seekampf-Hub aus; er reicht /api/kolonie/... hierher weiter.

Start von Hand:   .venv/bin/python web.py
Als Dienst:       systemctl start seekampf-kolonisations-bot
"""
from __future__ import annotations

import os
import re
import threading
import time
from contextlib import asynccontextmanager

import requests
import uvicorn
from fastapi import Body, FastAPI, HTTPException

import config
from api_client import ApiError, SeekampfClient
from kolonisation import Kolonisierer, begleitung_bereinigen, ziel_status
from logger_setup import get_logger
from notify import TelegramNotifier
from state import State

log = get_logger()
state = State()
client = SeekampfClient()
notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
bot = Kolonisierer(client, state, notifier)
_stop = threading.Event()

if state.defekt:
    log.error("data/state.json war unlesbar und liegt jetzt unter %s - Bot startet ANGEHALTEN", state.defekt)
    notifier.send("Kolonisations-Bot: state.json defekt",
                  f"data/state.json liess sich nicht lesen und wurde als {os.path.basename(state.defekt)} "
                  "beiseitegelegt. Der Bot ist angehalten; Warteschlange aus der Sicherung zurueckspielen.")


def _schleife() -> None:
    while not _stop.is_set():
        try:
            bot.tick()
            bot.letzter_fehler = None
        except ApiError as e:
            bot.letzter_fehler = str(e)
            log.error("Tick fehlgeschlagen: %s", e)
        except requests.RequestException as e:
            bot.letzter_fehler = f"Netzwerk: {type(e).__name__}"
            log.error("Tick fehlgeschlagen (Netzwerk): %s", str(e)[:160])
        except Exception as e:  # noqa: BLE001 - der Thread darf nie enden
            bot.letzter_fehler = f"{type(e).__name__}: {e}"
            log.exception("Unerwarteter Fehler im Tick")
        _stop.wait(config.TICK_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Kolonisations-Bot startet (Schnittstelle auf %s:%s)", config.WEB_HOST, config.WEB_PORT)
    if not config.API_KEY:
        log.error("SEEKAMPF_API_KEY fehlt - bitte in die .env eintragen")
    thread = threading.Thread(target=_schleife, name="kolonisation", daemon=True)
    thread.start()
    try:
        yield
    finally:
        _stop.set()
        thread.join(timeout=5)
        state.save()
        log.info("Kolonisations-Bot beendet")


app = FastAPI(title="Seekampf Kolonisations-Bot", lifespan=lifespan)


def _eintrag_ausgabe(e: dict) -> dict:
    aus = dict(e)
    iid = e.get("bau_insel")
    if iid is not None and int(iid) in bot.ov:
        aus["bau_insel_name"] = bot._name(iid)
        aus["entfernung"] = round(bot.entfernung(iid, e), 1)
        aus["fahrzeit_s"] = round(bot.fahrzeit(iid, e))
    return aus


@app.get("/api/status")
def status():
    daten = state.snapshot()
    return {
        "laeuft": daten["laeuft"],
        "fehler": bot.letzter_fehler,
        "letzter_tick": bot.letzter_tick,
        "serverzeit": time.time(),
        "warteschlange": [_eintrag_ausgabe(e) for e in daten["warteschlange"]],
        "verlauf": daten["verlauf"],
        "inseln": sorted(bot.inseln.values(), key=lambda i: i["id"]),
        "schiff": {"ab_hafen": bot.ab_hafen, "kosten": bot.kosten},
        "umbenennen": daten["umbenennen"],
    }


@app.post("/api/umbenennen")
def umbenennen(payload: dict = Body(...)):
    """Neue Inseln automatisch umbenennen: {aktiv, muster}; {n} = naechste Nummer."""
    with state.lock:
        einst = state.data["umbenennen"]
        if "aktiv" in payload:
            einst["aktiv"] = bool(payload["aktiv"])
        if "muster" in payload:
            muster = str(payload["muster"] or "").strip()
            if not muster or len(muster.replace("{n}", "999")) > 40:
                raise HTTPException(400, "Muster: 1 bis 40 Zeichen, {n} steht fuer die Nummer")
            einst["muster"] = muster
        ergebnis = dict(einst)
    state.save()
    log.info("Seekampf-Hub: Umbenennen %s, Muster '%s'", "an" if ergebnis["aktiv"] else "aus", ergebnis["muster"])
    return ergebnis


@app.post("/api/control")
def steuern(payload: dict = Body(...)):
    aktion = str(payload.get("aktion", "")).lower()
    if aktion not in ("start", "stop"):
        raise HTTPException(400, "aktion muss 'start' oder 'stop' sein")
    with state.lock:
        state.data["laeuft"] = aktion == "start"
    state.save()
    log.info("Seekampf-Hub: Kolonisations-Bot %s", "gestartet" if aktion == "start" else "angehalten")
    return {"laeuft": state.data["laeuft"]}


_KOORD = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*$")


@app.post("/api/warteschlange")
def hinzufuegen(payload: dict = Body(...)):
    """Ziel hinten anhaengen. Freie Inseln (auch Ruinen) immer; eine bewohnte
    Insel nur mit {"bewohnt": true} - sonst Antwort 409 "BEWOHNT|<Besitzer>",
    damit der Hub nachfragen kann."""
    m = _KOORD.match(str(payload.get("koordinaten", "")))
    if not m:
        raise HTTPException(400, "Koordinate als x:y:z angeben, z. B. 48:53:21")
    x, y, z = (int(g) for g in m.groups())
    if not (1 <= z <= 25):
        raise HTTPException(400, "Die Inselnummer z liegt zwischen 1 und 25")
    koord = f"{x}:{y}:{z}"
    if any(e["koordinaten"] == koord for e in state.warteschlange):
        raise HTTPException(409, f"{koord} steht schon in der Warteschlange")
    try:
        info = client.get_island_info(x, y, z) or {}
    except ApiError as e:
        raise HTTPException(502, f"Insel nicht pruefbar: {e.message}") from e
    ist_insel, besitzer = ziel_status(info)
    if not ist_insel:
        raise HTTPException(400, f"Auf {koord} liegt keine Insel")
    if besitzer == bot.spieler:
        raise HTTPException(409, f"{koord} gehoert schon dir")
    if besitzer and not payload.get("bewohnt"):
        raise HTTPException(409, f"BEWOHNT|{besitzer}")
    e = state.neuer_eintrag(x, y, z, info.get("name") or "Unbewohnte Insel", bewohnt=bool(besitzer),
                            begleitung=begleitung_bereinigen(payload.get("begleitung")) if besitzer else None)
    log.info("Seekampf-Hub: %s (%s) in die Warteschlange (Platz %d)", koord, e["name"], len(state.warteschlange))
    return e


@app.post("/api/warteschlange/{eintrag_id}/entfernen")
def entfernen(eintrag_id: int):
    """Aus der Warteschlange nehmen. Faehrt das Schiff schon, wird es
    zurueckgerufen, solange das noch geht; ein Schiff in Ausbildung nimmt
    danach das naechste Ziel."""
    e = state.eintrag(eintrag_id)
    if e is None:
        raise HTTPException(404, "Eintrag nicht gefunden")
    text = "von Hand entfernt"
    if e["status"] == "unterwegs" and not e.get("zurueckgerufen"):
        if time.time() < float(e.get("rueckruf_bis") or 0):
            try:
                client.recall_fleet(e["flotte_id"])
                text += ", Schiff zurueckgerufen"
            except ApiError as err:
                raise HTTPException(502, f"Rueckruf fehlgeschlagen: {err.message}") from err
        else:
            text += " (Schiff war nicht mehr zurueckrufbar)"
    state.abschliessen(e, "entfernt", text)
    log.info("Seekampf-Hub: %s %s", e["koordinaten"], text)
    return {"ok": True, "text": text}


@app.post("/api/warteschlange/{eintrag_id}/begleitung")
def begleitung(eintrag_id: int, payload: dict = Body(...)):
    """Weitere Schiffe/Truppen fuer ein bewohntes Ziel: {schiffe: {typ: n}, truppen: {typ: n}}.
    Sie muessen beim Start im Hafen der Bau-Insel liegen; der Bot bildet sie nicht aus."""
    e = state.eintrag(eintrag_id)
    if e is None:
        raise HTTPException(404, "Eintrag nicht gefunden")
    if not e.get("bewohnt"):
        raise HTTPException(400, "Begleitung gibt es nur bei bewohnten Inseln")
    if e["status"] == "unterwegs":
        raise HTTPException(409, "Das Schiff ist schon unterwegs")
    with state.lock:
        e["begleitung"] = begleitung_bereinigen(payload)
    state.save()
    return {"ok": True, "begleitung": e["begleitung"]}


@app.post("/api/warteschlange/{eintrag_id}/verschieben")
def verschieben(eintrag_id: int, payload: dict = Body(...)):
    if not state.verschieben(eintrag_id, int(payload.get("richtung") or 0)):
        raise HTTPException(404, "Eintrag nicht gefunden")
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")
