"""Zustand des Kolonisations-Bots in data/state.json.

  laeuft          Hauptschalter (Seekampf-Hub)
  warteschlange   die Ziele in Reihenfolge, je Eintrag Status, Bau-Insel,
                  Ausbildungsauftrag und Flotte
  verlauf         abgeschlossene Eintraege (kolonisiert, entfernt, gescheitert)
  naechste_id     laufende Nummer fuer neue Eintraege
  umbenennen      neue Inseln automatisch umbenennen: an/aus und Muster
  bekannte_inseln Inseln, die schon da waren bzw. schon umbenannt sind
                  (None = noch nie erfasst, dann wird nichts umbenannt)

Weboberflaeche und Bot-Schleife laufen in einem Prozess, aber in
verschiedenen Threads - Zugriffe gehen deshalb ueber das Lock. Geschrieben
wird atomar ueber eine Temp-Datei.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time

import config


class State:
    def __init__(self, path: str = config.STATE_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.defekt: str | None = None
        self.data = self._load()

    def _load(self) -> dict:
        roh = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    roh = json.load(f)
                if not isinstance(roh, dict):
                    raise ValueError("kein JSON-Objekt")
            except (ValueError, OSError):
                # Nie still mit leerer Warteschlange weitermachen - die kaputte
                # Datei bleibt zur Rettung liegen, der Bot startet angehalten.
                self.defekt = f"{self.path}.defekt-{time.strftime('%Y%m%d-%H%M%S')}"
                os.replace(self.path, self.defekt)
                roh = {"laeuft": False}
        daten = {"laeuft": True, "warteschlange": [], "verlauf": [], "naechste_id": 1,
                 "umbenennen": {"aktiv": True, "muster": "GiG {n}"}, "bekannte_inseln": None}
        daten.update({k: v for k, v in roh.items() if k in daten})
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
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.data)

    # ---------------------------------------------------------- Warteschlange
    @property
    def warteschlange(self) -> list[dict]:
        return self.data["warteschlange"]

    def eintrag(self, eintrag_id: int) -> dict | None:
        return next((e for e in self.warteschlange if e["id"] == int(eintrag_id)), None)

    def neuer_eintrag(self, x: int, y: int, z: int, name: str, bewohnt: bool = False) -> dict:
        with self.lock:
            e = {
                "id": self.data["naechste_id"],
                "koordinaten": f"{x}:{y}:{z}", "x": x, "y": y, "z": z, "name": name,
                "hinzugefuegt": time.time(),
                # wartet -> spart -> baut -> unterwegs; danach in den Verlauf
                "status": "wartet",
                "bau_insel": None,       # Insel, die das Schiff baut bzw. schickt
                "auftrag_id": None,      # Ausbildungsauftrag des Schiffs
                "flotte_id": None,
                "abfahrt": None, "ankunft": None, "rueckruf_bis": None,
                "zurueckgerufen": False,
                "letzte_pruefung": 0.0,
                "hinweis": None,
                # Insel eines Spielers: bleibt in der Liste, auch wenn sie einen Besitzer hat
                "bewohnt": bewohnt,
            }
            self.data["naechste_id"] += 1
            self.warteschlange.append(e)
        self.save()
        return e

    def abschliessen(self, e: dict, ergebnis: str, text: str) -> None:
        """Eintrag aus der Warteschlange nehmen und in den Verlauf schreiben."""
        with self.lock:
            if e in self.warteschlange:
                self.warteschlange.remove(e)
            self.data["verlauf"].insert(0, {
                "id": e["id"], "koordinaten": e["koordinaten"], "name": e.get("name"),
                "ergebnis": ergebnis, "text": text, "zeit": time.time(),
                "bau_insel": e.get("bau_insel"),
            })
            del self.data["verlauf"][config.VERLAUF_MERKEN:]
        self.save()

    def verschieben(self, eintrag_id: int, richtung: int) -> bool:
        with self.lock:
            liste = self.warteschlange
            e = self.eintrag(eintrag_id)
            if e is None:
                return False
            i = liste.index(e)
            j = max(0, min(len(liste) - 1, i + (1 if richtung > 0 else -1)))
            liste[i], liste[j] = liste[j], liste[i]
        self.save()
        return True
