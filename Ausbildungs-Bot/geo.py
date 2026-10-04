"""Entfernungen und Fahrzeiten auf der Seekampf-Karte.

Die Karte ist doppelt gerastert: die Weltkoordinate (x, y) bezeichnet einen
Sektor, die Inselnummer z (1..25) den Platz in dessen 5x5-Raster. Fuer die
Entfernung zaehlt nicht der Sektor-Abstand, sondern der Abstand in FELDERN -
z gehoert also mit in die Rechnung.

Nachgerechnet an einer echten Flotte (18.09.2026): 53:49:8 -> 53:48:6 meldete
die API distanz_felder = 5.385164807134504 = sqrt(29); mit der Umrechnung
unten ergibt sich dX = 2, dY = 5, also genau sqrt(4 + 25). Die gemeldete
Fahrzeit betrug 323,1 s = 5.385 / 10 Knoten * 10 Minuten.
"""
from __future__ import annotations

import math

import config


def feld(x: int, y: int, z: int) -> tuple[int, int]:
    """Weltkoordinate -> absolute Feldkoordinate."""
    kante = config.SEKTOR_KANTE
    return (x * kante + (z - 1) % kante, y * kante + (z - 1) // kante)


def distanz_felder(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    ax, ay = feld(*a)
    bx, by = feld(*b)
    return math.dist((ax, ay), (bx, by))


def flotten_knoten(ships: dict) -> float:
    """Eine Flotte faehrt so schnell wie ihr langsamstes Schiff."""
    werte = [config.SCHIFF_KNOTEN[typ] for typ, n in ships.items()
             if n and typ in config.SCHIFF_KNOTEN]
    return float(min(werte)) if werte else 0.0


def ladevolumen(ships: dict) -> int:
    """Wie viel Beute die Flotte hoechstens mitnehmen kann."""
    return sum(config.SCHIFF_LADEVOLUMEN.get(typ, 0) * n for typ, n in ships.items())


def fahrzeit_s(a: tuple[int, int, int], b: tuple[int, int, int], knoten: float) -> float:
    """Einfache Fahrzeit in Sekunden (Hinweg; die Rueckfahrt dauert genauso)."""
    if knoten <= 0:
        return 0.0
    dauer = distanz_felder(a, b) / knoten * 600.0
    untergrenze = (config.MIN_FAHRZEIT_SELBER_SEKTOR_S
                   if (a[0], a[1]) == (b[0], b[1]) else config.MIN_FAHRZEIT_S)
    return max(untergrenze, dauer)


def koord_str(x: int, y: int, z: int) -> str:
    return f"{x}:{y}:{z}"


def koord_parse(text: str) -> tuple[int, int, int]:
    x, y, z = (int(p) for p in text.split(":"))
    return x, y, z
