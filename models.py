"""Rohstoff-Tripel (gold, stein, holz) als kleiner Rechentyp.

Genutzt von store.py (Produktions-Differenz zweier Beobachtungen) und
planner.py (gelernter Ertrag einer Stufe).
"""
from __future__ import annotations


class Res:
    __slots__ = ("gold", "stein", "holz")

    def __init__(self, gold: float = 0.0, stein: float = 0.0, holz: float = 0.0):
        self.gold = gold
        self.stein = stein
        self.holz = holz

    def __sub__(self, other: "Res") -> "Res":
        return Res(self.gold - other.gold, self.stein - other.stein, self.holz - other.holz)

    def __repr__(self) -> str:
        return f"Res(gold={self.gold:.0f}, stein={self.stein:.0f}, holz={self.holz:.0f})"

    @classmethod
    def from_dict(cls, d: dict | None) -> "Res":
        d = d or {}
        return cls(float(d.get("gold", 0) or 0), float(d.get("stein", 0) or 0), float(d.get("holz", 0) or 0))
