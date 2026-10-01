"""Seekampf-Hub: eine Weboberflaeche fuer alle Seekampf-Bots.

Die Bots bleiben eigenstaendige Dienste; der Seekampf-Hub redet mit ihnen so:

  Flotten-Manager  hat eine eigene JSON-Schnittstelle auf 127.0.0.1:8081,
                   /api/flotte/... wird dorthin durchgereicht.
  Upgrade-Bot      ueber Dateien in dessen data/: steuerung.json (schreibt der
  Allianz-Bot      Seekampf-Hub), befehle/*.json (legt der Seekampf-Hub ab, der Bot
                   fuehrt sie aus) und status.json (schreibt der Bot).
  systemd          Status per `systemctl show`, Start/Stopp/Neustart per sudo
                   (nur die in seekampf-hub.sudoers erlaubten Befehle).

Stuerzt ein Bot ab, bleibt die Seite erreichbar und zeigt das an.

Start von Hand:   .venv/bin/python hub.py
Als Dienst:       systemctl start seekampf-hub
"""
from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import uuid

from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from karte import Karte

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEEKAMPF_DIR = os.path.dirname(BASE_DIR)

# Nur im Heimnetz, ohne Login (so gewuenscht, bis alles laeuft).
HOST = os.environ.get("HUB_HOST", "0.0.0.0")
PORT = int(os.environ.get("HUB_PORT", "8080"))
FLOTTE_URL = os.environ.get("FLOTTEN_MANAGER_URL", "http://127.0.0.1:8081")

BOTS = {
    "flotte": {"titel": "Flotten-Manager", "dienst": "seekampf-flotten-manager.service",
               "ordner": os.path.join(SEEKAMPF_DIR, "Flotten-Manager"), "log": "flotte"},
    "upgrade": {"titel": "Upgrade-Bot", "dienst": "seekampf-upgrade-bot.service",
                "ordner": os.path.join(SEEKAMPF_DIR, "Upgrade-Bot"), "log": "bot"},
    "allianz": {"titel": "Allianz-Bot", "dienst": "seekampf-allianz-bot.service",
                "ordner": os.path.join(SEEKAMPF_DIR, "Allianz-Bot"), "log": "allianz"},
    "hub": {"titel": "Seekampf-Hub", "dienst": "seekampf-hub.service",
                  "ordner": BASE_DIR, "log": None},
}
# Was sudoers erlaubt - der Seekampf-Hub selbst laesst sich nur neu starten, sonst
# saegte man den Ast ab, auf dem die Seite sitzt.
AKTIONEN = {"flotte": ("start", "stop", "restart"), "upgrade": ("start", "stop", "restart"),
            "allianz": ("start", "stop", "restart"), "hub": ("restart",)}

# Gebaeude, die der Upgrade-Bot bauen kann (Labor und Marktplatz sind im Spiel abgeschaltet).
GEBAEUDE = ("haupthaus", "goldmine", "steingrube", "saegewerk", "lagerhaus",
            "hafen", "kaserne", "steinmauer", "wachturm")
ROHSTOFFE = ("gold", "stein", "holz")
FAEHIGKEITEN = ("notruf", "beistand", "leihe_rueckgabe", "anfrage")
STRATEGIE = {  # Typ, Minimum, Maximum - wie in Allianz-Bot/steuerung.py
    "MIN_SPEERKAEMPFER": (int, 0, 100000),
    "HILFE_MAX_ANTEIL": (float, 0.0, 1.0),
    "ANFRAGE_SCHWELLE": (float, 0.0, 1.0),
    "ANFRAGE_ZIEL": (float, 0.0, 1.0),
    "RUECKRUF_ERLAUBT": (bool, None, None),
}

_schreib_lock = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # sonst ~1100 Zeilen je Kartenscan
karte = Karte()
_stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=karte.planer, args=(_stop,), name="karten-planer", daemon=True).start()
    try:
        yield
    finally:
        _stop.set()


app = FastAPI(title="Seekampf-Hub", lifespan=lifespan)


# ------------------------------------------------------------------ Dateien
def _pfad(bot: str, *teile: str) -> str:
    return os.path.join(BOTS[bot]["ordner"], "data", *teile)


def _json_lesen(pfad: str) -> dict | None:
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _json_schreiben(pfad: str, daten: dict) -> None:
    """Atomar ueber eine Temp-Datei: der Bot liest nie eine halbe Datei."""
    ordner = os.path.dirname(pfad)
    os.makedirs(ordner, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ordner, prefix=".hub-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=1)
        os.replace(tmp, pfad)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _befehl_ablegen(bot: str, befehl: dict) -> str:
    befehl = {**befehl, "id": uuid.uuid4().hex[:12], "erstellt": time.time()}
    _json_schreiben(_pfad(bot, "befehle", f"{time.time_ns()}.json"), befehl)
    return befehl["id"]


def _steuerung_aendern(bot: str, aendern) -> dict:
    """steuerung.json lesen, per Funktion aendern, zurueckschreiben."""
    with _schreib_lock:
        pfad = _pfad(bot, "steuerung.json")
        daten = _json_lesen(pfad) or {}
        aendern(daten)
        daten["geaendert"] = time.time()
        _json_schreiben(pfad, daten)
        return daten


# ------------------------------------------------------------------- Seite
@app.get("/")
def startseite():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"),
                        headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ Dienste
def _dienst_status(unit: str) -> dict:
    try:
        aus = subprocess.run(
            ["systemctl", "show", unit, "--timestamp=unix",
             "-p", "ActiveState,SubState,ActiveEnterTimestamp,NRestarts,Result,LoadState"],
            capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"zustand": "unbekannt", "fehler": str(e)}
    werte = dict(z.split("=", 1) for z in aus.splitlines() if "=" in z)
    seit = werte.get("ActiveEnterTimestamp", "").lstrip("@")
    return {
        "zustand": werte.get("ActiveState", "unbekannt"),
        "detail": werte.get("SubState"),
        "seit": float(seit) if seit.isdigit() else None,
        "neustarts": int(werte.get("NRestarts") or 0),
        "ergebnis": werte.get("Result"),
        "installiert": werte.get("LoadState") == "loaded",
    }


@app.get("/api/dienste")
def dienste():
    return {name: {"titel": b["titel"], "dienst": b["dienst"], "aktionen": AKTIONEN[name],
                   **_dienst_status(b["dienst"])}
            for name, b in BOTS.items()}


@app.post("/api/dienste/{name}/{aktion}")
def dienst_steuern(name: str, aktion: str):
    if name not in BOTS or aktion not in AKTIONEN[name]:
        raise HTTPException(400, "Unbekannter Dienst oder nicht erlaubte Aktion")
    unit = BOTS[name]["dienst"]
    if name == "hub":
        # Der Neustart beendet diesen Prozess - erst antworten, dann ausfuehren.
        subprocess.Popen(["sh", "-c", f"sleep 1; sudo -n /usr/bin/systemctl restart {unit}"])
        return {"ok": True}
    erg = subprocess.run(["sudo", "-n", "/usr/bin/systemctl", aktion, unit],
                         capture_output=True, text=True, timeout=60)
    if erg.returncode != 0:
        raise HTTPException(500, (erg.stderr or erg.stdout or "systemctl fehlgeschlagen").strip())
    return {"ok": True, **_dienst_status(unit)}


# --------------------------------------------------------------------- Logs
@app.get("/api/log/{bot}")
def log_lesen(bot: str, zeilen: int = 200):
    if bot not in BOTS or not BOTS[bot]["log"]:
        raise HTTPException(404, "Kein Log fuer diesen Bot")
    muster = os.path.join(BOTS[bot]["ordner"], "logs", f"{BOTS[bot]['log']}-*.log")
    dateien = sorted(glob.glob(muster))
    if not dateien:
        return {"datei": None, "zeilen": []}
    zeilen = max(10, min(int(zeilen), 2000))
    with open(dateien[-1], encoding="utf-8", errors="replace") as f:
        inhalt = f.readlines()
    return {"datei": os.path.basename(dateien[-1]), "zeilen": [z.rstrip() for z in inhalt[-zeilen:]]}


# ------------------------------------------------------------ Flotten-Manager
_http = httpx.AsyncClient(base_url=FLOTTE_URL, timeout=60.0)


@app.api_route("/api/flotte/{pfad:path}", methods=["GET", "POST"])
async def flotte(pfad: str, request: Request):
    try:
        antwort = await _http.request(
            request.method, f"/api/{pfad}", params=request.query_params,
            content=await request.body(), headers={"Content-Type": "application/json"})
    except httpx.HTTPError as e:
        raise HTTPException(503, f"Flotten-Manager nicht erreichbar ({type(e).__name__})") from e
    return Response(antwort.content, status_code=antwort.status_code,
                    media_type=antwort.headers.get("content-type", "application/json"))


# ------------------------------------------------------------------ Upgrade
@app.get("/api/upgrade")
def upgrade():
    status = _json_lesen(_pfad("upgrade", "status.json"))
    return {"status": status, "steuerung": _json_lesen(_pfad("upgrade", "steuerung.json")) or {},
            "gebaeude": GEBAEUDE, "serverzeit": time.time()}


@app.put("/api/upgrade/inseln/{insel_id}")
def upgrade_insel(insel_id: int, payload: dict = Body(...)):
    neu = {}
    if "aktiv" in payload:
        neu["aktiv"] = bool(payload["aktiv"])
    if "modus" in payload:
        if payload["modus"] != "auto" and payload["modus"] not in GEBAEUDE:
            raise HTTPException(400, "Unbekannte Priorisierung")
        neu["modus"] = payload["modus"]
    if "bis_stufe" in payload:
        bis = payload["bis_stufe"]
        if bis in (None, "", 0):
            neu["bis_stufe"] = None
        else:
            try:
                neu["bis_stufe"] = max(1, min(int(bis), 20))
            except (TypeError, ValueError):
                raise HTTPException(400, "Zielstufe muss eine Zahl sein") from None
    if "gesperrt" in payload:
        gesperrt = payload["gesperrt"] or []
        if not isinstance(gesperrt, list) or any(g not in GEBAEUDE for g in gesperrt):
            raise HTTPException(400, "Unbekanntes Gebaeude in der Sperrliste")
        neu["gesperrt"] = sorted(set(gesperrt))

    def aendern(daten):
        daten.setdefault("inseln", {}).setdefault(str(insel_id), {}).update(neu)

    daten = _steuerung_aendern("upgrade", aendern)
    return {"ok": True, "insel": daten["inseln"][str(insel_id)]}


@app.post("/api/upgrade/befehl")
def upgrade_befehl(payload: dict = Body(...)):
    typ = payload.get("typ")
    if typ == "pruefen":
        return {"id": _befehl_ablegen("upgrade", {"typ": "pruefen"})}
    if typ == "auftrag":
        if payload.get("gebaeude") not in GEBAEUDE:
            raise HTTPException(400, "Unbekanntes Gebaeude")
        try:
            insel_id = int(payload.get("insel_id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "insel_id fehlt") from None
        return {"id": _befehl_ablegen("upgrade", {"typ": "auftrag", "insel_id": insel_id,
                                                  "gebaeude": payload["gebaeude"]})}
    raise HTTPException(400, "Unbekannter Befehl")


# ------------------------------------------------------------------ Allianz
@app.get("/api/allianz")
def allianz():
    return {"status": _json_lesen(_pfad("allianz", "status.json")),
            "steuerung": _json_lesen(_pfad("allianz", "steuerung.json")) or {},
            "serverzeit": time.time()}


@app.put("/api/allianz/faehigkeiten")
def allianz_faehigkeiten(payload: dict = Body(...)):
    neu = {f: bool(v) for f, v in payload.items() if f in FAEHIGKEITEN}
    if not neu:
        raise HTTPException(400, "Keine bekannte Faehigkeit")
    daten = _steuerung_aendern("allianz", lambda d: d.setdefault("faehigkeiten", {}).update(neu))
    return {"ok": True, "faehigkeiten": daten["faehigkeiten"]}


@app.put("/api/allianz/strategie")
def allianz_strategie(payload: dict = Body(...)):
    neu = {}
    for name, wert in payload.items():
        if name not in STRATEGIE:
            continue
        typ, lo, hi = STRATEGIE[name]
        if typ is bool:
            neu[name] = bool(wert)
            continue
        try:
            wert = typ(wert)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{name} muss eine Zahl sein") from None
        if not lo <= wert <= hi:
            raise HTTPException(400, f"{name} muss zwischen {lo} und {hi} liegen")
        neu[name] = wert
    def aendern(daten):
        strategie = {**daten.get("strategie", {}), **neu}
        schwelle = strategie.get("ANFRAGE_SCHWELLE", 0.10)
        if strategie.get("ANFRAGE_ZIEL", 0.25) < schwelle:
            # Vor dem Schreiben pruefen - die Exception verhindert das Speichern.
            raise HTTPException(400, "Auffüllen bis muss mindestens so hoch sein wie die Schwelle")
        daten["strategie"] = strategie

    daten = _steuerung_aendern("allianz", aendern)
    return {"ok": True, "strategie": daten["strategie"]}


@app.post("/api/allianz/befehl")
def allianz_befehl(payload: dict = Body(...)):
    typ = payload.get("typ")
    if typ == "anfrage":
        try:
            rohstoffe = {r: int(payload.get("rohstoffe", {}).get(r) or 0) for r in ROHSTOFFE}
        except (TypeError, ValueError):
            raise HTTPException(400, "Mengen muessen ganze Zahlen sein") from None
        rohstoffe = {r: n for r, n in rohstoffe.items() if n > 0}
        if not rohstoffe:
            raise HTTPException(400, "Mindestens eine Menge angeben")
        insel = str(payload.get("insel") or "").strip()
        if not insel or len(insel.split(":")) != 3:
            raise HTTPException(400, "Insel fehlt")
        return {"id": _befehl_ablegen("allianz", {"typ": "anfrage", "rohstoffe": rohstoffe, "insel": insel})}
    if typ == "anfrage_beenden":
        return {"id": _befehl_ablegen("allianz", {"typ": "anfrage_beenden",
                                                  "insel_id": payload.get("insel_id")})}
    raise HTTPException(400, "Unbekannter Befehl")


# -------------------------------------------------------------------- Karte
@app.get("/api/karte")
def karte_daten():
    return {**karte.daten(), "serverzeit": time.time()}


@app.get("/api/karte/status")
def karte_status():
    return karte.scan_status()


@app.post("/api/karte/scan")
def karte_scan():
    if not karte.scan_starten("von Hand"):
        raise HTTPException(409, "Es läuft schon ein Scan")
    return {"ok": True}


@app.get("/api/karte/angriffe")
def karte_angriffe():
    return {"angriffe": karte.angriffe(), "serverzeit": time.time()}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
