"""Weboberflaeche und Einstiegspunkt des Flotten-Managers.

Ein einziger Prozess: uvicorn bedient die Seite, ein Hintergrund-Thread laesst
die Flotten fahren. Beide teilen sich denselben State und schuetzen ihn ueber
dessen Lock. Die Seite selbst ist reines HTML/JS ohne Build-Schritt und liegt
in static/index.html.

Start von Hand:   .venv/bin/python web.py
Als Dienst:       systemctl start seekampf-flotten-manager
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import geo
import logger_setup
import report as report_modul
from api_client import ApiError, SeekampfClient
from logger_setup import get_logger
from manager import FlottenManager
from notify import TelegramNotifier
from state import State

log = get_logger()
state = State()
client = SeekampfClient()
notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
manager = FlottenManager(client, state, notifier)

_stop = threading.Event()
_thread: threading.Thread | None = None


def _schleife() -> None:
    """Der Bot-Takt. Faellt ein Tick aus, laeuft der naechste trotzdem - der
    Dienst darf an einem Netzwerkaussetzer nicht sterben."""
    while not _stop.is_set():
        try:
            if manager.heimat is None:
                manager.start()
            manager.tick()
            manager.letzter_fehler = None
        except ApiError as e:
            manager.letzter_fehler = str(e)
            log.error("Tick fehlgeschlagen: %s", e)
        except Exception as e:  # noqa: BLE001 - der Thread darf nie enden
            manager.letzter_fehler = f"{type(e).__name__}: {e}"
            log.exception("Unerwarteter Fehler im Tick")
        _stop.wait(max(1.0, float(state.settings["tick_sekunden"])))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _thread
    log.info("Flotten-Manager startet (Web auf %s:%s)", config.WEB_HOST, config.WEB_PORT)
    if not config.API_KEY:
        log.error("SEEKAMPF_API_KEY fehlt - bitte in die .env eintragen")
    _thread = threading.Thread(target=_schleife, name="flotten-manager", daemon=True)
    _thread.start()
    try:
        yield
    finally:
        _stop.set()
        if _thread is not None:
            _thread.join(timeout=5)
        state.save()
        log.info("Flotten-Manager beendet")


app = FastAPI(title="Seekampf Flotten-Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(config.BASE_DIR, "static")), name="static")


@app.get("/")
def startseite():
    return FileResponse(os.path.join(config.BASE_DIR, "static", "index.html"))


@app.get("/api/status")
def status():
    daten = state.snapshot()
    stats = daten["stats"]
    stunden = stats["laufzeit_s"] / 3600.0
    beute_gesamt = stats["gold"] + stats["stein"] + stats["holz"]

    ov = manager._overview or {}
    flotten = sorted(daten["flotten"].values(),
                     key=lambda f: (f.get("return_at") or f.get("arrive_at") or 0))
    ziele = sorted(daten["ziele"].values(), key=lambda z: z.get("distanz", 0))
    rotation = daten["rotation"]
    naechstes = rotation[daten["rotation_index"] % len(rotation)] if rotation else None

    return {
        "laeuft": daten["laeuft"],
        "pause_grund": daten["pause_grund"],
        "fehler": manager.letzter_fehler,
        "serverzeit": time.time(),
        "letzter_tick": manager.letzter_tick,
        "letzter_scan": daten["letzter_scan"],
        "naechstes_ziel": naechstes,
        "insel": {
            "id": manager.insel_id,
            "name": manager.insel_name,
            "koordinaten": geo.koord_str(*manager.heimat) if manager.heimat else "",
            "rohstoffe": ov.get("rohstoffe") or {},
            "schiffe": ov.get("schiffe") or {},
            "truppen": ov.get("truppen") or {},
            "bedrohung": bool(ov.get("bedrohung_im_anflug")),
        },
        "stats": {
            **{k: stats[k] for k in ("raids", "gold", "stein", "holz", "laufzeit_s", "niederlagen")},
            "inseln_besucht": len(stats["inseln_besucht"]),
            "beute_gesamt": beute_gesamt,
            "loot_pro_stunde": (beute_gesamt / stunden) if stunden > 0.01 else 0.0,
            "seit": stats["seit"],
        },
        "flotten": flotten,
        "ziele": ziele,
        "settings": daten["settings"],
    }


@app.post("/api/control")
def steuern(payload: dict = Body(...)):
    aktion = str(payload.get("aktion", "")).lower()
    if aktion not in ("start", "stop"):
        raise HTTPException(400, "aktion muss 'start' oder 'stop' sein")
    with state.lock:
        state.data["laeuft"] = aktion == "start"
        if aktion == "start":
            state.data["pause_grund"] = None
            if state.data["stats"]["seit"] is None:
                state.data["stats"]["seit"] = time.time()
    state.save()
    log.info("Weboberflaeche: Manager %s", "gestartet" if aktion == "start" else "gestoppt")
    return {"laeuft": state.data["laeuft"]}


@app.post("/api/settings")
def einstellungen(payload: dict = Body(...)):
    geaendert = state.update_settings(payload)
    if geaendert:
        log.info("Einstellungen geaendert: %s",
                 ", ".join(f"{k}={v}" for k, v in geaendert.items()))
    return {"geaendert": geaendert, "settings": state.settings}


@app.post("/api/scan")
def scannen():
    try:
        return manager.scan()
    except ApiError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/stats/reset")
def stats_reset():
    state.stats_zuruecksetzen()
    log.info("Weboberflaeche: Statistik zurueckgesetzt")
    return {"ok": True}


@app.post("/api/blacklist/clear")
def blacklist_leeren():
    with state.lock:
        for ziel in state.data["ziele"].values():
            ziel["blacklist_bis"] = None
            ziel["blacklist_grund"] = None
            ziel["niederlagen"] = 0
    state.save()
    log.info("Weboberflaeche: Blacklist geleert")
    return {"ok": True}


@app.post("/api/ziele/{koord}/sperre")
def sperre_umschalten(koord: str):
    """Ein Ziel von Hand sperren oder wieder freigeben."""
    with state.lock:
        ziel = state.data["ziele"].get(koord)
        if ziel is None:
            raise HTTPException(404, f"{koord} ist kein bekanntes Ziel")
        if ziel.get("blacklist_bis"):
            ziel["blacklist_bis"] = None
            ziel["blacklist_grund"] = None
            ziel["niederlagen"] = 0
            aktion = "freigegeben"
        else:
            ziel["blacklist_bis"] = "dauerhaft"
            ziel["blacklist_grund"] = "von Hand gesperrt"
            aktion = "gesperrt"
    state.save()
    log.info("Weboberflaeche: Ziel %s %s", koord, aktion)
    return {"koordinaten": koord, "gesperrt": aktion == "gesperrt"}


@app.post("/api/report/test")
def report_test():
    if not notifier.aktiv:
        raise HTTPException(400, "Telegram ist nicht eingerichtet (.env)")
    ok = notifier.send("Seekampf Flotten-Manager (Test)", report_modul.text(state))
    return {"gesendet": ok}


@app.get("/api/log")
def logdatei(zeilen: int = 200):
    from datetime import datetime
    pfad = logger_setup.log_path_for(datetime.now(logger_setup.TZ).date())
    if not os.path.exists(pfad):
        return JSONResponse({"zeilen": []})
    with open(pfad, "r", encoding="utf-8", errors="replace") as f:
        inhalt = f.readlines()
    return {"datei": os.path.basename(pfad), "zeilen": [z.rstrip() for z in inhalt[-zeilen:]]}


if __name__ == "__main__":
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")
