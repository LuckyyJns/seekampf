"""Datenmodelle fuer den Planer (optimizer.py).

Urspruenglich Teil des uebernommenen Skripts, aber nie mitgeliefert - hier
neu gebaut, exakt auf das Interface zugeschnitten, das optimizer.py erwartet,
und auf unsere echten Ressourcen-Keys (gold, stein, holz - siehe
ResourcesOut in api_client.py) statt der urspruenglich angenommenen
(holz, stein, gold) aus der leeren Konfigurationsvorlage.
"""
from __future__ import annotations

from dataclasses import dataclass, field

RESOURCES = ("gold", "stein", "holz")


class Res:
    __slots__ = ("gold", "stein", "holz")

    def __init__(self, gold: float = 0.0, stein: float = 0.0, holz: float = 0.0):
        self.gold = gold
        self.stein = stein
        self.holz = holz

    def as_tuple(self):
        return (self.gold, self.stein, self.holz)

    def dot(self, other: "Res") -> float:
        return sum(a * b for a, b in zip(self.as_tuple(), other.as_tuple()))

    def covers(self, cost: "Res") -> bool:
        return all(s >= c for s, c in zip(self.as_tuple(), cost.as_tuple()))

    def __add__(self, other: "Res") -> "Res":
        return Res(*(a + b for a, b in zip(self.as_tuple(), other.as_tuple())))

    def __sub__(self, other: "Res") -> "Res":
        return Res(*(a - b for a, b in zip(self.as_tuple(), other.as_tuple())))

    def __mul__(self, k: float) -> "Res":
        return Res(*(a * k for a in self.as_tuple()))

    __rmul__ = __mul__

    def __repr__(self) -> str:
        return f"Res(gold={self.gold:.0f}, stein={self.stein:.0f}, holz={self.holz:.0f})"

    @classmethod
    def from_dict(cls, d: dict | None) -> "Res":
        d = d or {}
        return cls(float(d.get("gold", 0) or 0), float(d.get("stein", 0) or 0), float(d.get("holz", 0) or 0))


@dataclass
class Candidate:
    island_id: str
    building: str
    from_level: int
    to_level: int
    cost: Res
    build_time_s: float
    score: float = float("-inf")
    wait_s: float = 0.0
    reason: str = ""

    @property
    def label(self) -> str:
        return f"{self.building} {self.from_level}→{self.to_level}"


@dataclass
class BuildJob:
    building: str
    to_level: int
    finishes_in_s: float = 0.0


@dataclass
class Island:
    id: str
    name: str
    resources: Res
    levels: dict
    storage_cap: float = 0.0
    population_free: int = 9999
    population_total: int = 9999
    queue: list = field(default_factory=list)
    queue_slots: int = 3

    def level(self, key: str) -> int:
        return self.levels.get(key, 0)

    def copy(self) -> "Island":
        return Island(
            self.id, self.name, Res(*self.resources.as_tuple()), dict(self.levels),
            self.storage_cap, self.population_free, self.population_total,
            list(self.queue), self.queue_slots,
        )
