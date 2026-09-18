"""Zugriff auf die Gebaeudetabellen aus den offiziellen Spielregeln.

Die Live-API liefert pro Gebaeude immer nur die naechste Stufe. Damit kennt der
Planer zwar die Kosten eines Ausbaus, nicht aber seinen Ertrag - und konnte
Goldmine, Steingrube und Saegewerk deshalb nur nach Kosten vergleichen.

https://seekampf.de/legal/regeln veroeffentlicht alle 20 Stufen samt
Produktion pro Stunde; fetch_gamedata.py holt sie nach data/gamedata.json.
Fehlt die Datei, liefern alle Funktionen None und der Planer faellt auf sein
altes Verhalten zurueck.
"""
from __future__ import annotations

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gamedata.json")

# Das Haupthaus verkuerzt die Bauzeit aller anderen Gebaeude linear von 0 % bei
# Stufe 1 bis 40 % bei Stufe 20 (Regeln, §3). Die Tabellen listen die Basiszeit
# ohne diesen Bonus - ungerechnet weichen sie von der API ab.
BUILD_TIME_MAX_DISCOUNT = 0.40
BUILD_TIME_DISCOUNT_MAX_LEVEL = 20


def _load() -> dict:
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("gebaeude", {})
    except (OSError, ValueError):
        return {}


_BUILDINGS = _load()


def available() -> bool:
    return bool(_BUILDINGS)


def entry(typ: str, level: int) -> dict | None:
    """Tabellenzeile eines Gebaeudes fuer eine Stufe."""
    return _BUILDINGS.get(typ, {}).get(str(int(level)))


def cost(typ: str, level: int) -> dict | None:
    row = entry(typ, level)
    if row is None:
        return None
    return {r: row[r] for r in ("gold", "stein", "holz") if r in row}


def production_per_h(typ: str, level: int) -> float | None:
    """Stundenproduktion eines Rohstoffgebaeudes auf dieser Stufe."""
    if int(level) <= 0:
        return 0.0
    row = entry(typ, level)
    return None if row is None else row.get("produktion_pro_h")


def production_gain(typ: str, from_level: int, to_level: int) -> float | None:
    """Wie viel Produktion pro Stunde der Ausbau tatsaechlich bringt."""
    before, after = production_per_h(typ, from_level), production_per_h(typ, to_level)
    if before is None or after is None:
        return None
    return after - before


def build_time_s(typ: str, level: int, haupthaus_level: int = 1) -> float | None:
    """Bauzeit inklusive Haupthaus-Bonus - so, wie die API sie meldet."""
    row = entry(typ, level)
    if row is None or row.get("bauzeit_s") is None:
        return None
    base = float(row["bauzeit_s"])
    if typ == "haupthaus":
        return base  # profitiert nicht von der eigenen Stufe
    steps = max(0, min(int(haupthaus_level), BUILD_TIME_DISCOUNT_MAX_LEVEL) - 1)
    discount = BUILD_TIME_MAX_DISCOUNT * steps / (BUILD_TIME_DISCOUNT_MAX_LEVEL - 1)
    return base * (1.0 - discount)


def storage(level: int) -> dict | None:
    """Lagerkapazitaet und Pluenderschutz je Rohstoff beim Lagerhaus."""
    row = entry("lagerhaus", level)
    if row is None:
        return None
    return {"kapazitaet": row.get("kapazitaet"), "pluenderschutz": row.get("pluenderschutz")}
