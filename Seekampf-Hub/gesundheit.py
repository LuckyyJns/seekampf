"""Gesundheitsseite des Seekampf-Hubs: laeuft alles - und tut es auch etwas?

"Dienst laeuft" allein sagt wenig: ein Bot kann laufen und trotzdem seit
Stunden an jedem Durchlauf scheitern. Deshalb je Bot:

  - letzter Durchlauf und ob er fehlerfrei war (Statusdateien bzw. die
    Schnittstelle des Flotten-Managers),
  - Fehler und Warnungen der letzten 24 Stunden aus den Tageslogs,
  - beim Upgrade-Bot: wie lange jede Bau-Warteschlange leer stand,
dazu der Raspberry Pi selbst (Temperatur, Drosselung, Speicher, Laufzeit)
und die naechtliche Sicherung.

Das Auswerten der Logs kostet ein paar MB Lesen - das Ergebnis wird deshalb
kurz zwischengespeichert.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime

ZEILE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (.*)$")
WARTESCHLANGE = re.compile(r"^Insel (\S+) \| Warteschlange (\d+)/(\d+)")
SICHERUNG_DIR = os.path.expanduser("~/Seekampf-Sicherungen")
CACHE_S = 30
FENSTER_S = 24 * 3600

_cache: dict = {"zeit": 0.0, "daten": None}


def _log_zeilen(ordner: str, praefix: str, seit: float):
    """(Zeitpunkt, Level, Text) aller Logzeilen seit `seit`, aus heute und gestern."""
    tage = {datetime.fromtimestamp(seit).date(), datetime.now().date()}
    for tag in sorted(tage):
        pfad = os.path.join(ordner, "logs", f"{praefix}-{tag.isoformat()}.log")
        try:
            with open(pfad, encoding="utf-8", errors="replace") as f:
                for zeile in f:
                    m = ZEILE.match(zeile)
                    if not m:
                        continue
                    try:
                        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                    except ValueError:
                        continue
                    if t >= seit:
                        yield t, m.group(2), m.group(3).rstrip()
        except OSError:
            continue


def _log_auswerten(ordner: str, praefix: str, seit: float, leerlauf: bool) -> dict:
    fehler = warnungen = 0
    letzte: list[dict] = []
    gruppen: dict[str, int] = {}
    # Leerlauf je Insel: Zeitgewichtet, denn die Durchlaeufe kommen unregelmaessig.
    insel_proben: dict[str, list[tuple[float, bool]]] = {}
    for t, level, text in _log_zeilen(ordner, praefix, seit):
        if level in ("ERROR", "CRITICAL"):
            fehler += 1
        elif level == "WARNING":
            warnungen += 1
        if level in ("ERROR", "CRITICAL", "WARNING"):
            letzte.append({"zeit": t, "level": level, "text": text[:240]})
            schluessel = re.sub(r"\d+", "#", text)[:80]
            gruppen[schluessel] = gruppen.get(schluessel, 0) + 1
        if leerlauf and (m := WARTESCHLANGE.match(text)):
            insel_proben.setdefault(m.group(1), []).append((t, m.group(2) == "0"))
    jetzt = time.time()
    leer = {}
    for insel, proben in insel_proben.items():
        gesamt = leer_s = 0.0
        for i, (t, ist_leer) in enumerate(proben):
            bis = proben[i + 1][0] if i + 1 < len(proben) else jetzt
            dauer = min(bis - t, 1800)  # Luecken (Dienst aus) nicht hochrechnen
            gesamt += dauer
            leer_s += dauer if ist_leer else 0
        if gesamt > 0:
            leer[insel] = {"anteil": leer_s / gesamt, "leer_s": leer_s, "erfasst_s": gesamt}
    haeufig = sorted(gruppen.items(), key=lambda x: x[1], reverse=True)[:5]
    return {"fehler": fehler, "warnungen": warnungen, "letzte": letzte[-8:][::-1],
            "haeufig": [{"anzahl": n, "text": t} for t, n in haeufig], "leerlauf": leer}


def _json(pfad: str) -> dict | None:
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _befehl(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def system() -> dict:
    temp = None
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = int(f.read().strip()) / 1000
    except (OSError, ValueError):
        pass
    gedrosselt = _befehl(["vcgencmd", "get_throttled"]).partition("=")[2] or None
    platte = shutil.disk_usage("/")
    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for zeile in f:
                k, _, v = zeile.partition(":")
                mem[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    try:
        with open("/proc/uptime") as f:
            laufzeit = float(f.read().split()[0])
    except (OSError, ValueError):
        laufzeit = None
    return {
        "temperatur": temp,
        # 0x0 = alles gut; Bit 0/16 Unterspannung, 1/17 Takt gedrosselt, 2/18 Drosselung, 3/19 Temperaturgrenze
        "gedrosselt": gedrosselt,
        "platte": {"gesamt": platte.total, "frei": platte.free},
        "speicher": {"gesamt": mem.get("MemTotal"), "frei": mem.get("MemAvailable")},
        "laufzeit_s": laufzeit,
        "last": os.getloadavg(),
    }


def sicherung() -> dict:
    dateien = sorted(glob.glob(os.path.join(SICHERUNG_DIR, "seekampf-*.tar.gz")), key=os.path.getmtime)
    status = _json(os.path.join(SICHERUNG_DIR, "letzter-lauf.json")) or {}
    if not dateien:
        return {"anzahl": 0, "letzte": None, "lauf": status}
    letzte = dateien[-1]
    return {"anzahl": len(dateien), "lauf": status,
            "letzte": {"datei": os.path.basename(letzte), "zeit": os.path.getmtime(letzte),
                       "groesse": os.path.getsize(letzte)},
            "gesamt_groesse": sum(os.path.getsize(d) for d in dateien)}


def berechnen(bots: dict, flotte_status: dict | None, dienst_status) -> dict:
    """bots: BOTS aus hub.py; flotte_status: /api/status des Flotten-Managers
    (None = nicht erreichbar); dienst_status: Funktion unit -> dict."""
    jetzt = time.time()
    if _cache["daten"] is not None and jetzt - _cache["zeit"] < CACHE_S:
        daten = dict(_cache["daten"])
    else:
        seit = jetzt - FENSTER_S
        daten = {"logs": {name: _log_auswerten(b["ordner"], b["log"], seit, leerlauf=(name == "upgrade"))
                          for name, b in bots.items() if b.get("log")},
                 "system": system(), "sicherung": sicherung()}
        _cache.update(zeit=jetzt, daten=dict(daten))

    up = _json(os.path.join(bots["upgrade"]["ordner"], "data", "status.json")) or {}
    verlauf = (_json(os.path.join(bots["upgrade"]["ordner"], "data", "verlauf.json")) or {}).get("inseln") or {}
    heute = datetime.now().date()
    gestern = heute.fromordinal(heute.toordinal() - 1)
    daten["lager_verlust"] = {
        iid: {"heute": (tage.get(heute.isoformat()) or {}).get("verlust"),
              "gestern": (tage.get(gestern.isoformat()) or {}).get("verlust"),
              "ueberlauf": ((up.get("inseln") or {}).get(iid) or {}).get("ueberlauf")}
        for iid, tage in verlauf.items()}
    al = _json(os.path.join(bots["allianz"]["ordner"], "data", "status.json")) or {}
    tick_fm = None
    if flotte_status:
        tick_fm = {"letzter": flotte_status.get("letzter_tick"), "fehler": flotte_status.get("fehler"),
                   "laeuft": flotte_status.get("laeuft"), "pause": flotte_status.get("pause_grund"),
                   "takt_s": (flotte_status.get("settings") or {}).get("tick_sekunden"),
                   "flotten": len(flotte_status.get("flotten") or [])}
    daten["durchlauf"] = {
        "flotte": tick_fm or {"fehler": "Schnittstelle nicht erreichbar"},
        "upgrade": {"letzter": up.get("letzter_tick"), "fehler": up.get("fehler"),
                    "naechster": up.get("naechster_tick"), "takt_s": up.get("poll_s"),
                    "inseln_fehler": {iid: i.get("fehler") for iid, i in (up.get("inseln") or {}).items()
                                      if isinstance(i, dict) and i.get("fehler")},
                    "namen": {iid: i.get("name") for iid, i in (up.get("inseln") or {}).items()
                              if isinstance(i, dict)}},
        "allianz": {"letzter": al.get("zeit"), "fehler": al.get("fehler"), "takt_s": 15},
    }
    daten["dienste"] = {name: dienst_status(b["dienst"]) for name, b in bots.items()}
    daten["dienste"]["cloudflared"] = dienst_status("cloudflared.service")
    daten["serverzeit"] = jetzt
    return daten
