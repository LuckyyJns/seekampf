"""Zustand des Ausbildungs-Bots in data/state.json.

  laeuft    Hauptschalter (Seekampf-Hub)
  standard  Mindestbestand je Posten (Truppen und Schiffe) fuer alle Inseln;
            null = keine Vorgabe
  inseln    Insel-ID -> {"soll": {posten: Anzahl oder null}, "aus": [posten],
            "aktiv": true/false}
            soll   eigener Wert; null = der Standard gilt
            aus    Posten, die der Bot auf dieser Insel in Ruhe laesst
            aktiv  false = die Insel macht gar nicht mit
  verlauf   die letzten Ausbildungen und Verlegungen
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time

import config


def leeres_soll() -> dict:
    return {e: None for e in config.ITEMS}


def _zahl_oder_none(posten: str, wert):
    if wert is None or (isinstance(wert, str) and not wert.strip()):
        return None
    n = int(wert)
    if n < 0:
        raise ValueError(f"{posten}: Soll darf nicht negativ sein")
    return n


def _neuer_eintrag() -> dict:
    return {"soll": leeres_soll(), "aus": [], "aktiv": True}


class State:
    def __init__(self, path: str = config.STATE_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict:
        roh = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                roh = json.load(f)
        except (OSError, ValueError):
            roh = {}
        daten = {"laeuft": True, "standard": leeres_soll(), "inseln": {}, "verlauf": []}
        if isinstance(roh, dict):
            daten.update({k: v for k, v in roh.items() if k in daten})
        standard = leeres_soll()
        if isinstance(daten["standard"], dict):
            standard.update({k: v for k, v in daten["standard"].items() if k in standard})
        daten["standard"] = standard
        for iid, alt in list(daten["inseln"].items()):
            eintrag = _neuer_eintrag()
            if isinstance(alt, dict):
                eintrag["soll"].update({k: v for k, v in (alt.get("soll") or {}).items() if k in eintrag["soll"]})
                eintrag["aus"] = [p for p in (alt.get("aus") or []) if p in config.ITEMS]
                eintrag["aktiv"] = bool(alt.get("aktiv", True))
            daten["inseln"][iid] = eintrag
        return daten

    def save(self) -> None:
        with self.lock:
            schnappschuss = copy.deepcopy(self.data)
        ordner = os.path.dirname(self.path)
        os.makedirs(ordner, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=ordner, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(schnappschuss, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.data)

    # ------------------------------------------------------------ Lesen
    def standard(self) -> dict:
        with self.lock:
            return dict(self.data["standard"])

    def insel(self, insel_id) -> dict:
        """Eigene Einstellungen der Insel (Kopie); unbekannte Inseln: alles leer."""
        with self.lock:
            return copy.deepcopy(self.data["inseln"].get(str(insel_id)) or _neuer_eintrag())

    def soll(self, insel_id) -> dict:
        """Nur die eigenen Werte der Insel (null = Standard)."""
        return self.insel(insel_id)["soll"]

    def soll_eff(self, insel_id) -> dict:
        """Wirksamer Mindestbestand: eigener Wert, sonst der Standard; null =
        keine Vorgabe. Ausgenommene Posten und inaktive Inseln haben keinen."""
        with self.lock:
            eintrag = self.data["inseln"].get(str(insel_id)) or _neuer_eintrag()
            if not eintrag["aktiv"]:
                return leeres_soll()
            return {p: (None if p in eintrag["aus"]
                        else eintrag["soll"][p] if eintrag["soll"][p] is not None
                        else self.data["standard"][p]) for p in config.ITEMS}

    # ---------------------------------------------------------- Schreiben
    def standard_setzen(self, werte: dict) -> dict:
        """Standard setzen; leer/None = keine Vorgabe. ValueError bei
        negativen oder nicht ganzzahligen Werten."""
        neu = {p: _zahl_oder_none(p, werte[p]) for p in config.ITEMS if p in werte}
        with self.lock:
            self.data["standard"].update(neu)
            ergebnis = dict(self.data["standard"])
        self.save()
        return ergebnis

    def insel_setzen(self, insel_id, werte: dict) -> dict:
        """Einstellungen einer Insel setzen: Posten (Anzahl oder leer = Standard),
        "aus" (Liste ausgenommener Posten) und "aktiv" (true/false)."""
        neu = {p: _zahl_oder_none(p, werte[p]) for p in config.ITEMS if p in werte}
        aus = None
        if "aus" in werte:
            if not isinstance(werte["aus"], list) or any(p not in config.ITEMS for p in werte["aus"]):
                raise ValueError("aus: erwartet eine Liste bekannter Posten")
            aus = [p for p in config.ITEMS if p in werte["aus"]]
        aktiv = None
        if "aktiv" in werte:
            if not isinstance(werte["aktiv"], bool):
                raise ValueError("aktiv: erwartet true oder false")
            aktiv = werte["aktiv"]
        with self.lock:
            eintrag = self.data["inseln"].setdefault(str(insel_id), _neuer_eintrag())
            eintrag["soll"].update(neu)
            if aus is not None:
                eintrag["aus"] = aus
            if aktiv is not None:
                eintrag["aktiv"] = aktiv
            ergebnis = copy.deepcopy(eintrag)
        self.save()
        return ergebnis

    def soll_setzen(self, insel_id, werte: dict) -> dict:
        """Wie insel_setzen, liefert aber nur die eigenen Soll-Werte."""
        return self.insel_setzen(insel_id, werte)["soll"]

    def merken(self, text: str) -> None:
        with self.lock:
            self.data["verlauf"].insert(0, {"zeit": time.time(), "text": text})
            del self.data["verlauf"][config.VERLAUF_MERKEN:]
