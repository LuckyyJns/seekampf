"""Verbindung zum Seekampf-Hub (Weboberflaeche unter ~/Seekampf/Seekampf-Hub).

Wie beim Upgrade-Bot laeuft alles ueber Dateien in data/:

  steuerung.json  schreibt der Seekampf-Hub: welche Faehigkeiten gewuenscht sind
                  und die Strategie-Werte (ueberschreiben die aus config.py).
  befehle/*.json  Auftraege von Hand (Rohstoff-Anfrage stellen oder beenden),
                  siehe bot.befehle_lesen.
  status.json     schreibt der Bot: Faehigkeiten, laufende Vorgaenge,
                  Mitglieder, Ergebnisse der Befehle.

Faehigkeit abschalten heisst: keine NEUEN Vorgaenge dieser Art. Was laeuft,
wird zu Ende gefuehrt, und so lange bleibt die Faehigkeit in der Praesenz
gemeldet - das Protokoll verbietet, etwas zu senden, was man nicht als
Faehigkeit meldet (agenten.md, Abschnitt 13).
"""
from __future__ import annotations

import json
import os
import tempfile

import config

DATA_DIR = os.path.join(config.BASE_DIR, "data")
STEUERUNG_PATH = os.path.join(DATA_DIR, "steuerung.json")
STATUS_PATH = os.path.join(DATA_DIR, "status.json")

ALLE_FAEHIGKEITEN = ("notruf", "beistand", "leihe_rueckgabe", "anfrage")

# Strategie-Werte, die der Seekampf-Hub setzen darf: Typ, Minimum, Maximum.
STRATEGIE = {
    "MIN_SPEERKAEMPFER": (int, 0, 100000),
    "HILFE_MAX_ANTEIL": (float, 0.0, 1.0),
    "ANFRAGE_SCHWELLE": (float, 0.0, 1.0),
    "ANFRAGE_ZIEL": (float, 0.0, 1.0),
    "RUECKRUF_ERLAUBT": (bool, None, None),
}
# Ueberlauf in die Allianzkasse: eigener Abschnitt "kasse" in steuerung.json.
KASSE = {"aktiv": ("KASSE_AKTIV", bool, None, None), "ab": ("KASSE_AB", float, 0.0, 1.0),
         "bis": ("KASSE_BIS", float, 0.0, 1.0), "min_menge": ("KASSE_MIN_MENGE", int, 0, 1000000)}
KASSE_STANDARD = {schluessel: getattr(config, name) for schluessel, (name, *_r) in KASSE.items()}
# Werte aus config.py, bevor der Seekampf-Hub etwas ueberschreibt.
STANDARD = {name: getattr(config, name) for name in STRATEGIE}
STANDARD_FAEHIGKEITEN = tuple(config.FAEHIGKEITEN)

_gewuenscht: set[str] = set(config.FAEHIGKEITEN)
_mtime: float | None = None


def _wert(name: str, roh):
    typ, lo, hi = STRATEGIE[name]
    if typ is bool:
        return roh if isinstance(roh, bool) else STANDARD[name]
    try:
        wert = typ(roh)
    except (TypeError, ValueError):
        return STANDARD[name]
    return min(max(wert, lo), hi)


def laden() -> None:
    """Steuerung einlesen, wenn sie sich geaendert hat, und auf config anwenden.

    Die Strategie-Werte werden direkt in config gesetzt: alle Rollen lesen sie
    zur Laufzeit als config.X, so greift eine Aenderung ohne Neustart."""
    global _mtime, _gewuenscht
    try:
        mtime = os.path.getmtime(STEUERUNG_PATH)
    except OSError:
        mtime = None
    if mtime == _mtime:
        return
    _mtime = mtime
    daten = {}
    if mtime is not None:
        try:
            with open(STEUERUNG_PATH, encoding="utf-8") as f:
                daten = json.load(f) or {}
        except (OSError, ValueError):
            daten = {}
    faehigkeiten = daten.get("faehigkeiten") or {}
    _gewuenscht = {f for f in ALLE_FAEHIGKEITEN if faehigkeiten.get(f, f in STANDARD_FAEHIGKEITEN)}
    strategie = daten.get("strategie") or {}
    for name in STRATEGIE:
        setattr(config, name, _wert(name, strategie[name]) if name in strategie else STANDARD[name])
    if config.ANFRAGE_ZIEL < config.ANFRAGE_SCHWELLE:
        config.ANFRAGE_ZIEL = config.ANFRAGE_SCHWELLE
    kasse = daten.get("kasse") or {}
    for schluessel, (name, typ, lo, hi) in KASSE.items():
        wert = KASSE_STANDARD[schluessel]
        roh = kasse.get(schluessel)
        if typ is bool and isinstance(roh, bool):
            wert = roh
        elif typ is not bool and roh is not None:
            try:
                wert = min(max(typ(roh), lo), hi)
            except (TypeError, ValueError):
                pass
        setattr(config, name, wert)
    if config.KASSE_BIS > config.KASSE_AB:
        config.KASSE_BIS = config.KASSE_AB


def kasse_einstellung() -> dict:
    return {schluessel: getattr(config, name) for schluessel, (name, *_r) in KASSE.items()}


def will(faehigkeit: str) -> bool:
    """Darf der Bot fuer diese Faehigkeit NEUE Vorgaenge beginnen?"""
    return faehigkeit in _gewuenscht


def gewuenscht() -> list[str]:
    return [f for f in ALLE_FAEHIGKEITEN if f in _gewuenscht]


def gebunden(state: dict) -> list[str]:
    """Faehigkeiten, die laufende Vorgaenge noch brauchen - die bleiben in der
    Praesenz, auch wenn sie im Seekampf-Hub abgeschaltet sind."""
    offen = set()
    if state["notrufe"]:
        offen.add("notruf")
    leihe_zugesagt = any(
        a.get("status") == "zugesagt" and any(t in (a.get("leihe") or []) for t in (a.get("zusage") or {}))
        for n in state["notrufe"].values() for a in n["angebote"].values())
    if leihe_zugesagt or any(s["status"] == "offen" for s in state["schulden"]):
        offen.add("leihe_rueckgabe")
    if any(h.get("angebot") and h["angebot"]["status"] in ("offen", "wartet") for h in state["hilfe"].values()):
        offen.add("beistand")
    if state["anfragen"]:
        offen.add("anfrage")
    return [f for f in ALLE_FAEHIGKEITEN if f in offen]


def wirksam(state: dict) -> list[str]:
    """Was in der Praesenz steht: gewuenscht plus noch gebunden."""
    noetig = set(_gewuenscht) | set(gebunden(state))
    return [f for f in ALLE_FAEHIGKEITEN if f in noetig]


def status_schreiben(daten: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".status-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        os.replace(tmp, STATUS_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
