"""Schnittstelle und Einstiegspunkt des Ausbildungs-Bots.

Ein Prozess: uvicorn bedient die JSON-Schnittstelle auf 127.0.0.1:8083, ein
Hintergrund-Thread haelt Truppen und Schiffe auf Soll. Die Oberflaeche liefert der
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
    return {
        "laeuft": daten["laeuft"], "fehler": bot.letzter_fehler, "letzter_tick": bot.letzter_tick,
        "serverzeit": time.time(), **bot.status(), "verlauf": daten["verlauf"], "reserve": bot.reserve,
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


@app.post("/api/standard")
def standard_setzen(payload: dict = Body(...)):
    try:
        standard = state.standard_setzen(payload)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Ungueltiger Wert: {e}") from e
    log.info("Seekampf-Hub: Standard: %s", ", ".join(f"{k}={'-' if v is None else v}" for k, v in standard.items() if v is not None) or "leer")
    _jetzt.set()
    return {"standard": standard}


@app.post("/api/inseln/{insel_id}/soll")
def soll_setzen(insel_id: int, payload: dict = Body(...)):
    try:
        eintrag = state.insel_setzen(insel_id, payload)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Ungueltiger Wert: {e}") from e
    log.info("Seekampf-Hub: %s: aktiv=%s, aus=%s, Soll %s", bot._name(insel_id), eintrag["aktiv"],
             ",".join(eintrag["aus"]) or "-",
             ", ".join(f"{k}={v}" for k, v in eintrag["soll"].items() if v is not None) or "leer")
    _jetzt.set()
    return {"id": insel_id, **eintrag}


@app.post("/api/inseln/{insel_id}/ausbilden")
def ausbilden(insel_id: int, payload: dict = Body(...)):
    try:
        text = bot.ausbilden(insel_id, str(payload.get("einheit", "")), payload.get("anzahl"))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    except ApiError as e:
        raise HTTPException(409, f"Das Spiel lehnt ab: {e.message}") from e
    except requests.RequestException as e:
        raise HTTPException(502, f"Spiel nicht erreichbar: {type(e).__name__}") from e
    _jetzt.set()
    return {"ok": True, "text": text}


if __name__ == "__main__":
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")
