"""Schnittstelle und Einstiegspunkt des Flotten-Managers.

Ein einziger Prozess: uvicorn bedient die JSON-Schnittstelle, ein
Hintergrund-Thread laesst die Flotten fahren. Beide teilen sich denselben
State und schuetzen ihn ueber dessen Lock. Die Weboberflaeche liefert der
Seekampf-Hub (~/Seekampf/Seekampf-Hub) aus; er reicht /api/flotte/... hierher weiter.

Start von Hand:   .venv/bin/python web.py
Als Dienst:       systemctl start seekampf-flotten-manager
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import requests
import uvicorn
from fastapi import Body, FastAPI, HTTPException

import config
import report as report_modul
from api_client import ApiError, SeekampfClient
from logger_setup import get_logger
from manager import FlottenManager
from notify import TelegramNotifier
from state import TZ, State

log = get_logger()
state = State()
client = SeekampfClient()
notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
manager = FlottenManager(client, state, notifier)

if state.defekt:
    log.error("data/state.json war unlesbar und liegt jetzt unter %s - Manager startet PAUSIERT",
              state.defekt)
    notifier.send("Flotten-Manager: state.json defekt",
                  f"data/state.json liess sich nicht lesen und wurde als {os.path.basename(state.defekt)} "
                  "beiseitegelegt. Der Manager laeuft pausiert. Stand aus der naechtlichen Sicherung "
                  "(~/Seekampf-Sicherungen) zurueckspielen oder im Seekampf-Hub neu starten.")

_stop = threading.Event()


def _kurz(e: Exception) -> str:
    """Die eigentliche Ursache eines requests-Fehlers ohne die verschachtelte Kette."""
    text = str(e)
    for marke in ("Caused by ", "Errno"):
        if marke in text:
            return text[text.index(marke):][:160].rstrip(")'\"")
    return text[:160]
_thread: threading.Thread | None = None


def _schleife() -> None:
    """Der Bot-Takt. Faellt ein Tick aus, laeuft der naechste trotzdem - der
    Dienst darf an einem Netzwerkaussetzer nicht sterben."""
    while not _stop.is_set():
        try:
            if not manager.gestartet:
                manager.start()
            manager.tick()
            manager.letzter_fehler = None
        except ApiError as e:
            manager.letzter_fehler = str(e)
            log.error("Tick fehlgeschlagen: %s", e)
        except requests.RequestException as e:
            # Netzaussetzer: eine Zeile genuegt, der naechste Tick versucht es wieder.
            manager.letzter_fehler = f"Netzwerk: {type(e).__name__}"
            log.error("Tick fehlgeschlagen (Netzwerk): %s", _kurz(e))
        except Exception as e:  # noqa: BLE001 - der Thread darf nie enden
            manager.letzter_fehler = f"{type(e).__name__}: {e}"
            log.exception("Unerwarteter Fehler im Tick")
        _stop.wait(max(1.0, float(state.settings["tick_sekunden"])))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _thread
    log.info("Flotten-Manager startet (Schnittstelle auf %s:%s)", config.WEB_HOST, config.WEB_PORT)
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


def _stats_ausgabe(stats: dict) -> dict:
    stunden = stats["laufzeit_s"] / 3600.0
    beute_gesamt = stats["gold"] + stats["stein"] + stats["holz"]
    return {
        **{k: stats[k] for k in ("raids", "gold", "stein", "holz", "laufzeit_s", "niederlagen")},
        "inseln_besucht": len(stats["inseln_besucht"]),
        "beute_gesamt": beute_gesamt,
        "loot_pro_stunde": (beute_gesamt / stunden) if stunden > 0.01 else 0.0,
        "seit": stats["seit"],
    }


@app.get("/api/status")
def status():
    daten = state.snapshot()
    ov_alle = manager._overview or {}
    flotten = sorted(daten["flotten"].values(),
                     key=lambda f: (f.get("return_at") or f.get("arrive_at") or 0))

    inseln = []
    for iid_text, insel in daten["inseln"].items():
        iid = int(iid_text)
        ov = ov_alle.get(iid) or {}
        inseln.append({
            "id": iid,
            "name": insel.get("name") or ov.get("name") or f"Insel {iid}",
            "koordinaten": insel.get("koordinaten") or ov.get("koordinaten") or "",
            "aktiv": bool(insel.get("aktiv")),
            "gehoert_uns": iid in ov_alle,
            "pause_grund": insel.get("pause_grund"),
            "rohstoffe": ov.get("rohstoffe") or {},
            "schiffe": ov.get("schiffe") or {},
            "truppen": ov.get("truppen") or {},
            "bedrohung": bool(ov.get("bedrohung_im_anflug")),
            "freie_flotten": manager._freie_flotten(iid, ov) if ov else 0,
            "ausgleich_rolle": insel.get("ausgleich_rolle") or "aus",
            "handel_reserviert": iid in manager.ausgleich.reserviert,
            "stats": _stats_ausgabe(insel["stats"]),
            "letzter_scan": insel.get("letzter_scan"),
            "naechstes_ziel": manager.naechstes_ziel_vorschau(insel),
            "ziele": sorted(insel["ziele"].values(), key=lambda z: z.get("distanz", 0)),
        })

    return {
        "laeuft": daten["laeuft"],
        "pause_grund": daten["pause_grund"],
        "fehler": manager.letzter_fehler,
        "serverzeit": time.time(),
        "letzter_tick": manager.letzter_tick,
        "stats": _stats_ausgabe(daten["stats"]),
        "flotten": flotten,
        "inseln": inseln,
        "settings": daten["settings"],
    }


@app.get("/api/verlauf")
def verlauf(tage: int = 30):
    """Beute und Raids je Tag, je Insel und zusammen - fuer die Diagramme.
    Tage ohne Raid kommen als Nullen mit, damit die Zeitachse lueckenlos ist."""
    tage = max(1, min(int(tage), 120))
    heute = datetime.now(TZ).date()
    daten_liste = [(heute - timedelta(days=n)).isoformat() for n in range(tage - 1, -1, -1)]
    with state.lock:
        roh = {iid: dict(t) for iid, t in state.data["verlauf"].items()}
    leer = {"gold": 0.0, "stein": 0.0, "holz": 0.0, "raids": 0}
    inseln = {iid: [{"datum": d, **leer, **t.get(d, {})} for d in daten_liste] for iid, t in roh.items()}
    gesamt = []
    for i, d in enumerate(daten_liste):
        summe = dict(leer, datum=d)
        for reihe in inseln.values():
            for k in ("gold", "stein", "holz", "raids"):
                summe[k] += reihe[i][k]
        gesamt.append(summe)
    return {"tage": tage, "gesamt": gesamt, "inseln": inseln}


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
    log.info("Seekampf-Hub: Manager %s", "gestartet" if aktion == "start" else "gestoppt")
    return {"laeuft": state.data["laeuft"]}


@app.post("/api/inseln/{insel_id}/aktiv")
def insel_schalten(insel_id: int, payload: dict = Body(...)):
    """Eine Insel ein- oder ausschalten. Aus heisst: keine neuen Flotten -
    was unterwegs ist, faehrt zu Ende und kommt heim."""
    aktiv = bool(payload.get("aktiv"))
    with state.lock:
        insel = state.insel(insel_id)
        if insel is None:
            raise HTTPException(404, f"Insel {insel_id} ist nicht bekannt")
        insel["aktiv"] = aktiv
        if aktiv and insel["stats"]["seit"] is None:
            insel["stats"]["seit"] = time.time()
    state.save()
    log.info("Seekampf-Hub: %s %s", insel.get("name") or insel_id, "eingeschaltet" if aktiv else "ausgeschaltet")
    return {"id": insel_id, "aktiv": aktiv}


@app.post("/api/settings")
def einstellungen(payload: dict = Body(...)):
    geaendert = state.update_settings(payload)
    if geaendert:
        log.info("Einstellungen geaendert: %s",
                 ", ".join(f"{k}={v}" for k, v in geaendert.items()))
    return {"geaendert": geaendert, "settings": state.settings}


@app.post("/api/inseln/{insel_id}/scan")
def scannen(insel_id: int):
    if state.insel(insel_id) is None:
        raise HTTPException(404, f"Insel {insel_id} ist nicht bekannt")
    try:
        return manager.scan(insel_id)
    except ApiError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/ausgleich")
def ausgleich_status():
    """Rohstoff-Ausgleich fuer den Seekampf-Hub: Rollen, Bestand, Bedarf,
    Ueberschuss, was unterwegs ist und die letzten Lieferungen."""
    daten = state.snapshot()
    ov_alle = manager._overview or {}
    bericht = manager.ausgleich.bericht.get("inseln") or {}
    inseln = []
    for iid_text, insel in daten["inseln"].items():
        ov = ov_alle.get(int(iid_text)) or {}
        b = bericht.get(iid_text) or {}
        inseln.append({
            "id": int(iid_text), "name": insel.get("name"), "koordinaten": insel.get("koordinaten"),
            "rolle": insel.get("ausgleich_rolle") or "aus", "gehoert_uns": int(iid_text) in ov_alle,
            "rohstoffe": ov.get("rohstoffe") or {}, "bedrohung": bool(ov.get("bedrohung_im_anflug")),
            "handelsschiffe": {t: (ov.get("schiffe") or {}).get(t, 0) for t in config.HANDELSSCHIFF_TYPEN},
            "bedarf": b.get("bedarf") or {}, "ueberschuss": b.get("ueberschuss") or {},
            "unterwegs": b.get("unterwegs") or {}, "upgrade": b.get("upgrade"), "hinweis": b.get("hinweis"),
            "reserviert": int(iid_text) in manager.ausgleich.reserviert,
        })
    s = daten["settings"]
    return {
        "aktiv": s["ausgleich_aktiv"], "laeuft": daten["laeuft"],
        "hinweis": manager.ausgleich.bericht.get("hinweis"),
        "settings": {k: v for k, v in s.items() if k.startswith("ausgleich_")},
        "inseln": inseln, "stand": daten.get("ausgleich") or {}, "serverzeit": time.time(),
    }


@app.post("/api/inseln/{insel_id}/rolle")
def rolle_setzen(insel_id: int, payload: dict = Body(...)):
    """Rolle einer Insel im Rohstoff-Ausgleich: aus, spender oder empfaenger."""
    rolle = str(payload.get("rolle") or "")
    if rolle not in ("aus", "spender", "empfaenger"):
        raise HTTPException(400, "rolle muss aus, spender oder empfaenger sein")
    with state.lock:
        insel = state.insel(insel_id)
        if insel is None:
            raise HTTPException(404, f"Insel {insel_id} ist nicht bekannt")
        insel["ausgleich_rolle"] = rolle
    state.save()
    log.info("Seekampf-Hub: %s im Rohstoff-Ausgleich: %s", insel.get("name") or insel_id, rolle)
    return {"id": insel_id, "rolle": rolle}


@app.post("/api/stats/reset")
def stats_reset():
    state.stats_zuruecksetzen()
    log.info("Seekampf-Hub: Statistik zurueckgesetzt")
    return {"ok": True}


@app.post("/api/blacklist/clear")
def blacklist_leeren():
    with state.lock:
        for insel in state.data["inseln"].values():
            for ziel in insel["ziele"].values():
                ziel["blacklist_bis"] = None
                ziel["blacklist_grund"] = None
                ziel["niederlagen"] = 0
    state.save()
    log.info("Seekampf-Hub: Blacklist geleert")
    return {"ok": True}


@app.post("/api/inseln/{insel_id}/ziele/{koord}/sperre")
def sperre_umschalten(insel_id: int, koord: str):
    """Ein Ziel einer Insel von Hand sperren oder wieder freigeben."""
    with state.lock:
        ziel = state.ziel(insel_id, koord)
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
    log.info("Seekampf-Hub: Ziel %s (Insel %s) %s", koord, insel_id, aktion)
    return {"koordinaten": koord, "gesperrt": aktion == "gesperrt"}


@app.post("/api/report/test")
def report_test():
    if not notifier.aktiv:
        raise HTTPException(400, "Telegram ist nicht eingerichtet (.env)")
    ok = notifier.send("Seekampf Flotten-Manager (Test)", report_modul.text(state))
    return {"gesendet": ok}


if __name__ == "__main__":
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")
