"""Zustand des Ausbildungs-Bots in data/state.json.

  laeuft   Hauptschalter (Seekampf-Hub)
  inseln   Insel-ID -> {"soll": {einheit: Anzahl oder null}}; null = keine
           Vorgabe, der Bot laesst diese Einheit auf der Insel in Ruhe
  verlauf  die letzten Ausbildungen und Verlegungen
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
    return {e: None for e in config.EINHEITEN}


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
        daten = {"laeuft": True, "inseln": {}, "verlauf": []}
        if isinstance(roh, dict):
            daten.update({k: v for k, v in roh.items() if k in daten})
        for eintrag in daten["inseln"].values():
            soll = leeres_soll()
            soll.update({k: v for k, v in (eintrag.get("soll") or {}).items() if k in soll})
            eintrag["soll"] = soll
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

    def soll(self, insel_id) -> dict:
        eintrag = self.data["inseln"].get(str(insel_id))
        return dict(eintrag["soll"]) if eintrag else leeres_soll()

    def soll_setzen(self, insel_id, werte: dict) -> dict:
        """Soll einer Insel setzen; leer/None = keine Vorgabe. ValueError bei
        negativen oder nicht ganzzahligen Werten."""
        neu = {}
        for einheit in config.EINHEITEN:
            if einheit not in werte:
                continue
            wert = werte[einheit]
            if wert is None or (isinstance(wert, str) and not wert.strip()):
                neu[einheit] = None
                continue
            n = int(wert)
            if n < 0:
                raise ValueError(f"{einheit}: Soll darf nicht negativ sein")
            neu[einheit] = n
        with self.lock:
            eintrag = self.data["inseln"].setdefault(str(insel_id), {"soll": leeres_soll()})
            eintrag["soll"].update(neu)
            ergebnis = dict(eintrag["soll"])
        self.save()
        return ergebnis

    def merken(self, text: str) -> None:
        with self.lock:
            self.data["verlauf"].insert(0, {"zeit": time.time(), "text": text})
            del self.data["verlauf"][config.VERLAUF_MERKEN:]
