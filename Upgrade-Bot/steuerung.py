"""Verbindung zum Seekampf-Hub (Weboberflaeche unter ~/Seekampf/Seekampf-Hub).

Der Seekampf-Hub und der Bot reden nur ueber Dateien in data/:

  steuerung.json  schreibt der Seekampf-Hub, der Bot liest sie nur. Je Insel:
                  an/aus, Priorisierung, Zielstufe, gesperrte Gebaeude.
  befehle/*.json  legt der Seekampf-Hub ab, der Bot fuehrt sie aus und loescht
                  sie ("jetzt pruefen", einzelner Ausbau von Hand).
  status.json     schreibt der Bot nach jedem Durchlauf: Inseln, Warteschlange,
                  was als Naechstes geplant ist, Ergebnisse der Befehle.

Faellt der Seekampf-Hub aus, laeuft der Bot mit der zuletzt gespeicherten
Steuerung weiter; fehlt die Datei ganz, gilt fuer jede Insel der Standard.

Einzige Ausnahme vom "Bot liest nur": taucht eine neue Insel auf, uebernimmt
sie die Steuerung der bis dahin neuesten Insel (neue_inseln_uebernehmen) -
das schreibt der Bot selbst, damit die Sperren greifen, bevor jemand den
Seekampf-Hub oeffnet. Beide Seiten schreiben unter steuerung.lock.
"""
from __future__ import annotations

import contextlib
import fcntl
import glob
import json
import os
import tempfile

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STEUERUNG_PATH = os.path.join(DATA_DIR, "steuerung.json")
SPERR_PATH = os.path.join(DATA_DIR, "steuerung.lock")
STATUS_PATH = os.path.join(DATA_DIR, "status.json")
BEFEHLE_DIR = os.path.join(DATA_DIR, "befehle")

STANDARD = {"aktiv": True, "modus": "auto", "bis_stufe": None, "gesperrt": []}

_NIE = object()  # noch nie nachgesehen - anders als "Datei fehlt" (None)
_mtime_gesehen = _NIE


def _mtime() -> float | None:
    try:
        return os.path.getmtime(STEUERUNG_PATH)
    except OSError:
        return None


def lesen() -> dict:
    try:
        with open(STEUERUNG_PATH, encoding="utf-8") as f:
            daten = json.load(f)
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}


def insel(daten: dict, island_id) -> dict:
    """Einstellung einer Insel, fehlende Felder mit dem Standard aufgefuellt."""
    eintrag = dict(STANDARD)
    eintrag.update((daten.get("inseln") or {}).get(str(island_id)) or {})
    return eintrag


@contextlib.contextmanager
def _gesperrt():
    """Dateisperre gegen gleichzeitiges Schreiben mit dem Seekampf-Hub."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SPERR_PATH, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _atomar_schreiben(pfad: str, daten: dict) -> None:
    ordner = os.path.dirname(pfad)
    fd, tmp = tempfile.mkstemp(dir=ordner, prefix=".bot-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=1)
        os.replace(tmp, pfad)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def neue_inseln_uebernehmen(islands: list) -> list[tuple]:
    """Inseln ohne eigenen Eintrag bekommen eine Kopie des Eintrags der
    neuesten Insel, die schon einen hat. "Neueste" heisst: am weitesten hinten
    in der Inselliste der API (die Reihenfolge, in der die Inseln dazukamen).
    Gibt [(neue_id, vorlage_id), ...] zurueck."""
    ids = [str(i["id"]) for i in islands]
    with _gesperrt():
        daten = lesen()
        eintraege = daten.setdefault("inseln", {})
        uebernommen = []
        for pos, iid in enumerate(ids):
            if iid in eintraege:
                continue
            vorlage = next((v for v in reversed(ids[:pos]) if v in eintraege), None)
            if vorlage is None:
                vorlage = next((v for v in reversed(ids) if v in eintraege), None)
            if vorlage is None:
                continue  # noch gar nichts eingestellt: Standard fuer alle
            eintraege[iid] = json.loads(json.dumps(eintraege[vorlage]))
            uebernommen.append((iid, vorlage))
        if uebernommen:
            _atomar_schreiben(STEUERUNG_PATH, daten)
    return uebernommen


def geaendert() -> bool:
    """True, wenn der Seekampf-Hub seit dem letzten Aufruf etwas gespeichert hat."""
    global _mtime_gesehen
    jetzt = _mtime()
    if jetzt == _mtime_gesehen:
        return False
    erster_aufruf = _mtime_gesehen is _NIE
    _mtime_gesehen = jetzt
    return not erster_aufruf


def befehle_holen() -> list[dict]:
    """Alle wartenden Befehle, aeltester zuerst. Jede Datei wird sofort
    geloescht, damit ein Befehl nie zweimal laeuft - auch wenn er scheitert."""
    befehle = []
    for pfad in sorted(glob.glob(os.path.join(BEFEHLE_DIR, "*.json"))):
        try:
            with open(pfad, encoding="utf-8") as f:
                befehl = json.load(f)
        except (OSError, ValueError):
            befehl = None
        finally:
            try:
                os.remove(pfad)
            except OSError:
                pass
        if isinstance(befehl, dict):
            befehle.append(befehl)
    return befehle


def status_schreiben(daten: dict) -> None:
    ordner = os.path.dirname(STATUS_PATH)
    os.makedirs(ordner, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ordner, prefix=".status-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        os.replace(tmp, STATUS_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
