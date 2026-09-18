"""Der gesamte Zustand des Flotten-Managers in einer JSON-Datei.

Enthaelt Einstellungen, die Zielliste, die gerade fahrenden Flotten und die
Statistik. Die Weboberflaeche und die Bot-Schleife laufen in einem Prozess,
aber in verschiedenen Threads - jeder Zugriff geht deshalb ueber das Lock.

Geschrieben wird ueber eine temporaere Datei mit os.replace: ein Stromausfall
mitten im Speichern hinterlaesst so entweder den alten oder den neuen Stand,
nie eine halbe Datei.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time

import config


def _leere_stats() -> dict:
    return {
        # Wie viele Flotten haben ein Ziel tatsaechlich erreicht (Mehrfach-
        # angriffe auf dieselbe Insel zaehlen jedes Mal).
        "raids": 0,
        # Verschiedene Inseln, die mindestens einmal angegriffen wurden.
        "inseln_besucht": [],
        "gold": 0.0,
        "stein": 0.0,
        "holz": 0.0,
        "niederlagen": 0,
        # Kumulierte Laufzeit ueber alle Laeufe hinweg, in Sekunden. Es zaehlt
        # nur Zeit, in der der Manager wirklich lief (gestoppt = Uhr steht).
        "laufzeit_s": 0.0,
        "seit": None,
    }


class State:
    def __init__(self, path: str = config.STATE_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()

    # ------------------------------------------------------------ Laden/Speichern
    def _load(self) -> dict:
        roh = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    roh = json.load(f)
            except (ValueError, OSError):
                roh = {}

        daten = {
            "laeuft": True,          # nach einem Neustart sofort weiterraiden
            "settings": {},
            "ziele": {},             # "x:y:z" -> Ziel-Datensatz
            "rotation": [],          # Reihenfolge der Koordinaten (stur reihum)
            "rotation_index": 0,
            "flotten": {},           # fleet_id (str) -> laufende Flotte
            "stats": _leere_stats(),
            "letzter_scan": None,
            "letzter_report": None,  # ISO-Datum des letzten Telegram-Reports
            "report_basis": None,    # Statistik-Stand beim letzten Report
            "verbuchte_berichte": [],  # message_ids, damit nichts doppelt zaehlt
            "pause_grund": None,
        }
        daten.update({k: v for k, v in roh.items() if k in daten})

        # Neue Einstellungen aus config ergaenzen, bestehende nicht ueberschreiben.
        settings = dict(config.DEFAULT_SETTINGS)
        settings.update(daten.get("settings") or {})
        daten["settings"] = settings

        stats = _leere_stats()
        stats.update(daten.get("stats") or {})
        daten["stats"] = stats
        return daten

    def save(self) -> None:
        with self.lock:
            schnappschuss = copy.deepcopy(self.data)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(schnappschuss, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.data)

    # ----------------------------------------------------------- Einstellungen
    @property
    def settings(self) -> dict:
        return self.data["settings"]

    def update_settings(self, neu: dict) -> dict:
        """Uebernimmt nur bekannte Schluessel und erzwingt den Typ des
        Standardwerts - die Weboberflaeche schickt alles als Text."""
        geaendert = {}
        with self.lock:
            for key, wert in (neu or {}).items():
                if key not in config.DEFAULT_SETTINGS:
                    continue
                standard = config.DEFAULT_SETTINGS[key]
                try:
                    if isinstance(standard, bool):
                        wert = wert if isinstance(wert, bool) else str(wert).lower() in ("1", "true", "ja", "on")
                    elif isinstance(standard, int):
                        wert = int(wert)
                    elif isinstance(standard, float):
                        wert = float(wert)
                    else:
                        wert = str(wert)
                except (TypeError, ValueError):
                    continue
                if self.settings.get(key) != wert:
                    self.settings[key] = wert
                    geaendert[key] = wert
        if geaendert:
            self.save()
        return geaendert

    # ------------------------------------------------------------------ Ziele
    def ziel(self, koord: str) -> dict | None:
        return self.data["ziele"].get(koord)

    def neues_ziel(self, koord: str, x: int, y: int, z: int, name: str,
                   distanz: float, fahrzeit_s: float) -> dict:
        return {
            "koordinaten": koord, "x": x, "y": y, "z": z, "name": name,
            "distanz": distanz, "fahrzeit_s": fahrzeit_s,
            "raids": 0, "niederlagen": 0, "niederlagen_gesamt": 0,
            "letzter_raid": None, "letzte_beute": None,
            "beute_gesamt": {"gold": 0.0, "stein": 0.0, "holz": 0.0},
            "blacklist_bis": None, "blacklist_grund": None,
        }

    def ist_gesperrt(self, ziel: dict, jetzt: float | None = None) -> bool:
        bis = ziel.get("blacklist_bis")
        if not bis:
            return False
        if bis == "dauerhaft":
            return True
        return (jetzt or time.time()) < float(bis)

    # -------------------------------------------------------------- Statistik
    def stats_zuruecksetzen(self) -> None:
        with self.lock:
            self.data["stats"] = _leere_stats()
            self.data["report_basis"] = None
        self.save()
