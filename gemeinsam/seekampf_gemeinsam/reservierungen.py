"""Gemeinsames Reservierungs-Verzeichnis aller Bots.

Was ein Bot auf einer Insel zurueckhaelt, steht in
`~/Seekampf/data/reservierungen/<bot>.json` - jeder Bot schreibt nur seine
eigene Datei (in jedem Takt komplett neu, atomar), alle anderen lesen:

    {"zeit": 1791796242.5, "bot": "kolonisation",
     "inseln": {"447": {"rohstoffe": {"gold": 19525, "stein": 4260, "holz": 35500},
                        "einheiten": {"kleines_kriegsschiff": 2},
                        "grund": "Kolonisationsschiff fuer 52:48:21"}}}

- `rohstoffe`: geben die anderen Bots auf dieser Insel nicht aus
  (Upgrade-Bot, Rohstoff-Ausgleich, Ausbildungs-Bot, Allianzkasse).
- `einheiten`: Schiffe/Truppen, die der Flotten-Manager nicht zum Raiden nimmt.
- `grund`: Klartext fuer die Uebersicht im Seekampf-Hub.

Ist eine Datei aelter als MAX_ALTER_S, laeuft ihr Bot nicht mehr - dann gilt
seine Reservierung nicht mehr, damit nichts auf Dauer blockiert bleibt.

Bots (Dateinamen): kolonisation, ausbildung, ausgleich.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

SEEKAMPF_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORDNER = os.path.join(SEEKAMPF_DIR, "data", "reservierungen")
MAX_ALTER_S = 10 * 60

ARTEN = ("rohstoffe", "einheiten")


def _ordner(ordner: str | None) -> str:
    return ordner or ORDNER


def schreiben(bot: str, inseln: dict, ordner: str | None = None, jetzt: float | None = None) -> None:
    """Die komplette Reservierung von `bot` ersetzen (leere Inseln fallen weg)."""
    ordner = _ordner(ordner)
    sauber = {}
    for iid, eintrag in (inseln or {}).items():
        e = {}
        for art in ARTEN:
            werte = {k: v for k, v in (eintrag.get(art) or {}).items() if v}
            if werte:
                e[art] = werte
        if e:
            if eintrag.get("grund"):
                e["grund"] = eintrag["grund"]
            sauber[str(iid)] = e
    os.makedirs(ordner, exist_ok=True)
    pfad = os.path.join(ordner, f"{bot}.json")
    fd, tmp = tempfile.mkstemp(dir=ordner, prefix=f".{bot}-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"zeit": time.time() if jetzt is None else jetzt, "bot": bot, "inseln": sauber},
                      f, ensure_ascii=False)
        os.replace(tmp, pfad)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def alle(ordner: str | None = None) -> dict[str, dict]:
    """Alle Reservierungsdateien, auch veraltete: {bot: {"zeit", "inseln", "aktuell"}}."""
    ordner = _ordner(ordner)
    aus = {}
    try:
        namen = sorted(os.listdir(ordner))
    except OSError:
        return aus
    jetzt = time.time()
    for name in namen:
        if not name.endswith(".json") or name.startswith("."):
            continue
        try:
            with open(os.path.join(ordner, name), encoding="utf-8") as f:
                daten = json.load(f)
            zeit = float(daten.get("zeit") or 0)
            inseln = {str(k): v for k, v in (daten.get("inseln") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        aus[name[:-5]] = {"zeit": zeit, "inseln": inseln, "aktuell": jetzt - zeit <= MAX_ALTER_S}
    return aus


def summe(art: str, ausser: str | None = None, ordner: str | None = None) -> dict[str, dict[str, float]]:
    """Insel-ID (Text) -> {Name: Menge}, aufsummiert ueber alle laufenden Bots
    ausser `ausser` (die eigene Reservierung zaehlt fuer einen Bot nicht)."""
    aus: dict[str, dict[str, float]] = {}
    for bot, daten in alle(ordner).items():
        if bot == ausser or not daten["aktuell"]:
            continue
        for iid, eintrag in daten["inseln"].items():
            werte = eintrag.get(art)
            if not isinstance(werte, dict):
                continue
            ziel = aus.setdefault(iid, {})
            for k, v in werte.items():
                try:
                    ziel[k] = ziel.get(k, 0) + float(v or 0)
                except (TypeError, ValueError):
                    continue
    return aus


def von(bot: str, ordner: str | None = None) -> dict[str, dict]:
    """Die Inseln, die `bot` gerade reserviert (leer, wenn veraltet)."""
    daten = alle(ordner).get(bot)
    return daten["inseln"] if daten and daten["aktuell"] else {}
