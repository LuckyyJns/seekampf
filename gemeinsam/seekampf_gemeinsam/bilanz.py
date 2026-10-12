"""Rohstoff-Bilanz: woher die Rohstoffe jeder Insel kommen und wohin sie gehen.

Jeder Bot bucht dort, wo er Rohstoffe bewegt, in seine eigene Datei
`~/Seekampf/data/bilanz/<bot>.json` (nur ein Schreiber je Datei):

    {"tage": {"2026-10-12": {"447": {"Raids": {"gold": 1200, "stein": 800, "holz": 950},
                                     "Ausbau": {"gold": -9000, ...}}}},
     "bestand": {"2026-10-12": {"447": {"erst": {...}, "letzt": {...}}}}}

Zugaenge sind positiv, Abgaenge negativ. Der Upgrade-Bot traegt zusaetzlich den
ersten und letzten beobachteten Lagerstand des Tages ein (`bestand`); was
zwischen Bestandsaenderung und Buchungen fehlt, zeigt der Hub als
"nicht zugeordnet" (Quests, Pluenderung, Handarbeit im Spiel ...).

Gebucht wird im Speicher und hoechstens alle SPEICHERN_S auf die Karte
geschrieben (schont die SD-Karte); `speichern()` erzwingt es (beim Beenden).
Tage aelter als BEHALTEN_TAGE fallen weg. Tage zaehlen in Europe/Berlin.
"""
from __future__ import annotations

import atexit
import json
import os
import tempfile
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .reservierungen import SEEKAMPF_DIR

ORDNER = os.path.join(SEEKAMPF_DIR, "data", "bilanz")
TZ = ZoneInfo("Europe/Berlin")
ROHSTOFFE = ("gold", "stein", "holz")
SPEICHERN_S = 60
BEHALTEN_TAGE = 60

# Kategorien in der Reihenfolge, in der der Hub sie zeigt.
ZUGAENGE = ("Produktion", "Raids", "Ausgleich erhalten", "Allianz erhalten")
ABGAENGE = ("Ausbau", "Ausbildung", "Kolonisation", "Allianzkasse", "Ausgleich abgegeben")


def tag(zeitpunkt: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if zeitpunkt is None else zeitpunkt, TZ).date().isoformat()


class Bilanz:
    """Buchungen eines Bots. Ein Objekt je Bot und Prozess."""

    def __init__(self, bot: str, ordner: str | None = None):
        self.bot = bot
        self.pfad = os.path.join(ordner or ORDNER, f"{bot}.json")
        self._lock = threading.Lock()
        self._gespeichert = 0.0
        self._geaendert = False
        self.daten = self._laden()
        atexit.register(self.speichern)

    def _laden(self) -> dict:
        try:
            with open(self.pfad, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("tage", {})
                d.setdefault("bestand", {})
                return d
        except (OSError, ValueError):
            pass
        return {"tage": {}, "bestand": {}}

    def buchen(self, insel_id, kategorie: str, rohstoffe: dict, vorzeichen: int = 1,
               zeitpunkt: float | None = None) -> None:
        """`rohstoffe` ({gold, stein, holz}) auf Insel und Kategorie buchen;
        vorzeichen=-1 fuer Abgaenge. Nullbuchungen werden ignoriert."""
        werte = {}
        for r in ROHSTOFFE:
            try:
                n = float((rohstoffe or {}).get(r, 0) or 0)
            except (TypeError, ValueError):
                continue
            if n:
                werte[r] = vorzeichen * n
        if not werte or insel_id is None:
            return
        with self._lock:
            ziel = (self.daten["tage"].setdefault(tag(zeitpunkt), {})
                    .setdefault(str(insel_id), {}).setdefault(kategorie, {}))
            for r, n in werte.items():
                ziel[r] = round(ziel.get(r, 0.0) + n, 2)
            self._geaendert = True
        self._vielleicht_speichern()

    def bestand(self, insel_id, rohstoffe: dict, zeitpunkt: float | None = None) -> None:
        """Beobachteten Lagerstand festhalten (erster und letzter des Tages)."""
        stand = {r: float((rohstoffe or {}).get(r, 0) or 0) for r in ROHSTOFFE}
        with self._lock:
            eintrag = self.daten["bestand"].setdefault(tag(zeitpunkt), {}).setdefault(str(insel_id), {})
            eintrag.setdefault("erst", stand)
            eintrag["letzt"] = stand
            self._geaendert = True
        self._vielleicht_speichern()

    def _vielleicht_speichern(self) -> None:
        if time.time() - self._gespeichert >= SPEICHERN_S:
            self.speichern()

    def speichern(self) -> None:
        with self._lock:
            if not self._geaendert:
                return
            grenze = tag(time.time() - BEHALTEN_TAGE * 86400)
            for teil in ("tage", "bestand"):
                for t in [t for t in self.daten[teil] if t < grenze]:
                    del self.daten[teil][t]
            ordner = os.path.dirname(self.pfad)
            try:
                os.makedirs(ordner, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=ordner, prefix=f".{self.bot}-", suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.daten, f, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp, self.pfad)
            except OSError:
                return   # die Bilanz darf einen Bot nie stoppen; naechster Versuch beim naechsten Buchen
            self._gespeichert = time.time()
            self._geaendert = False


def zusammenfassen(tage: int = 14, ordner: str | None = None) -> dict:
    """Alle Bot-Dateien zusammengefuehrt: {tag: {insel: {kategorie: {r: n}}}} und
    die Bestaende {tag: {insel: {erst, letzt}}} der letzten `tage` Tage."""
    ordner = ordner or ORDNER
    grenze = tag(time.time() - (tage - 1) * 86400)
    buchungen: dict = {}
    bestand: dict = {}
    try:
        namen = sorted(n for n in os.listdir(ordner) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        namen = []
    for name in namen:
        try:
            with open(os.path.join(ordner, name), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        for t, inseln in (d.get("tage") or {}).items():
            if t < grenze:
                continue
            for iid, kategorien in (inseln or {}).items():
                for kat, werte in (kategorien or {}).items():
                    ziel = buchungen.setdefault(t, {}).setdefault(iid, {}).setdefault(kat, {})
                    for r, n in (werte or {}).items():
                        ziel[r] = round(ziel.get(r, 0.0) + float(n or 0), 2)
        for t, inseln in (d.get("bestand") or {}).items():
            if t >= grenze:
                for iid, b in (inseln or {}).items():
                    bestand.setdefault(t, {})[iid] = b
    return {"buchungen": buchungen, "bestand": bestand}
