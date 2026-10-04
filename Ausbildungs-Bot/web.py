"""Schnittstelle und Einstiegspunkt des Ausbildungs-Bots.

Ein Prozess: uvicorn bedient die JSON-Schnittstelle auf 127.0.0.1:8083, ein
Hintergrund-Thread haelt die Truppen auf Soll. Die Oberflaeche liefert der
Seekampf-Hub aus; er reicht /api/ausbildung/... hierher weiter.

Start von Hand:   .venv/bin/python web.py
Als Dienst:       systemctl start seekampf-ausbildungs-bot
"""
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager

import requests
import uvicorn
from fastapi import Body, FastAPI, HTTPException

import config
from api_client import ApiError, SeekampfClient
from ausbildung import Ausbilder
from logger_setup import get_logger
from notify import TelegramNotifier
from state import State

log = get_logger()
state = State()
client = SeekampfClient()
bot = Ausbilder(client, state, TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID))
_stop = threading.Event()
_jetzt = threading.Event()   # Soll geaendert: sofort einen Durchlauf


def _schleife() -> None:
    while not _stop.is_set():
        _jetzt.clear()
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
        _jetzt.wait(config.TICK_S)
        if _stop.is_set():
            break


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Ausbildungs-Bot startet (Schnittstelle auf %s:%s)", config.WEB_HOST, config.WEB_PORT)
    if not config.API_KEY:
        log.error("SEEKAMPF_API_KEY fehlt - bitte in die .env eintragen")
    thread = threading.Thread(target=_schleife, name="ausbildung", daemon=True)
    thread.start()
    try:
        yield
    finally:
        _stop.set()
        _jetzt.set()
        thread.join(timeout=5)
        state.save()
        log.info("Ausbildungs-Bot beendet")


app = FastAPI(title="Seekampf Ausbildungs-Bot", lifespan=lifespan)


@app.get("/api/status")
def status():
    daten = state.snapshot()
    inseln = []
    for iid, ov in sorted(bot.ov.items()):
        inseln.append({
            "id": iid, "name": ov.get("name"), "koordinaten": ov.get("koordinaten"),
            "kaserne": int((ov.get("gebaeude") or {}).get("kaserne") or 0),
            "truppen": ov.get("truppen") or {},
            "bedrohung": bool(ov.get("bedrohung_im_anflug")),
            "soll": state.soll(iid),
            "bericht": bot.bericht.get(str(iid)),
        })
    return {
        "laeuft": daten["laeuft"], "fehler": bot.letzter_fehler, "letzter_tick": bot.letzter_tick,
        "serverzeit": time.time(), "einheiten": list(config.EINHEITEN), "inseln": inseln,
        "verlauf": daten["verlauf"], "reserve": bot.reserve,
    }


@app.post("/api/control")
def steuern(payload: dict = Body(...)):
    aktion = str(payload.get("aktion", "")).lower()
    if aktion not in ("start", "stop"):
        raise HTTPException(400, "aktion muss 'start' oder 'stop' sein")
    with state.lock:
        state.data["laeuft"] = aktion == "start"
    state.save()
    _jetzt.set()
    log.info("Seekampf-Hub: Ausbildungs-Bot %s", "gestartet" if aktion == "start" else "angehalten")
    return {"laeuft": state.data["laeuft"]}


@app.post("/api/inseln/{insel_id}/soll")
def soll_setzen(insel_id: int, payload: dict = Body(...)):
    try:
        soll = state.soll_setzen(insel_id, payload)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Ungueltiger Wert: {e}") from e
    log.info("Seekampf-Hub: Soll %s: %s", bot._name(insel_id),
             ", ".join(f"{k}={'-' if v is None else v}" for k, v in soll.items()))
    _jetzt.set()
    return {"id": insel_id, "soll": soll}


if __name__ == "__main__":
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")
