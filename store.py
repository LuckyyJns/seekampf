"""Lernt echte Kosten-/Bauzeit-/Produktions-/Lager-Wachstumsraten aus
Beobachtungen statt sie zu raten.

Die Live-API liefert pro Tick nur die Kosten der NAECHSTEN Stufe jedes
Gebaeudes. Weil der Bot ueber Tage/Wochen tatsaechlich Stufe fuer Stufe
ausbaut, laeuft dabei von selbst eine Zeitreihe realer (Stufe -> Kosten)
Punkte auf. Aus zwei aufeinanderfolgenden echten Punkten laesst sich eine
echte Wachstumsrate berechnen, die die anfangs generische Annahme ersetzt.

Speicherformat: eine JSON-Datei (kein SQLite noetig fuer diese Groessenordnung).
"""
from __future__ import annotations

import json
import os
from statistics import geometric_mean

from models import Res


class Store:
    def __init__(self, path: str):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (ValueError, OSError):
                pass
        return {"cost_points": {}, "time_points": {}, "storage_points": {}, "prod_deltas": {}, "last_snapshot": None}

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # Beobachtung: bei jedem Tick aufrufen, unabhaengig davon, ob gebaut wurde
    # ------------------------------------------------------------------
    def observe(self, buildings: list[dict], resources: dict, storage_key: str | None) -> None:
        for b in buildings:
            key = b.get("typ")
            kosten = b.get("kosten")
            if key and kosten and b.get("naechste_stufe") is not None:
                next_level = int(b.get("naechste_stufe"))
                self._add_point(self.data["cost_points"], key, next_level, kosten)
                bauzeit = b.get("bauzeit_s")
                if bauzeit:
                    self._add_point(self.data["time_points"], key, next_level, {"s": bauzeit})

        prev = self.data.get("last_snapshot")
        if prev is not None:
            self._learn_from_transition(prev, buildings, resources, storage_key)

        self.data["last_snapshot"] = {"buildings": buildings, "resources": resources}
        self.save()

    @staticmethod
    def _add_point(bucket: dict, key: str, level: int, values: dict) -> None:
        series = bucket.setdefault(key, {})
        series[str(level)] = values  # ueberschreiben ist ok, jeder Tick bestaetigt den Wert erneut

    def _learn_from_transition(self, prev: dict, buildings: list[dict], resources: dict,
                                storage_key: str | None) -> None:
        prev_levels = {b["typ"]: int(b.get("stufe", 0)) for b in prev.get("buildings", [])}
        cur_levels = {b["typ"]: int(b.get("stufe", 0)) for b in buildings}
        changed = [k for k in cur_levels if cur_levels.get(k) != prev_levels.get(k)]

        # Nur zuordenbar, wenn sich seit dem letzten Snapshot GENAU ein
        # Gebaeude um GENAU eine Stufe veraendert hat - sonst laesst sich der
        # Produktions-/Lagersprung nicht sauber diesem einen Gebaeude anlasten.
        if len(changed) != 1:
            return
        key = changed[0]
        old_lvl, new_lvl = prev_levels.get(key, 0), cur_levels[key]
        if new_lvl != old_lvl + 1:
            return

        prev_res, cur_res = prev.get("resources") or {}, resources or {}
        prev_prod = prev_res.get("produktion_pro_h", {})
        cur_prod = cur_res.get("produktion_pro_h", {})
        if prev_prod and cur_prod:
            delta = Res.from_dict(cur_prod) - Res.from_dict(prev_prod)
            bucket = self.data["prod_deltas"].setdefault(key, {})
            bucket[str(new_lvl)] = {"gold": delta.gold, "stein": delta.stein, "holz": delta.holz}

        if key == storage_key and "kapazitaet" in prev_res and "kapazitaet" in cur_res:
            bucket = self.data["storage_points"].setdefault("kapazitaet", {})
            bucket[str(new_lvl)] = cur_res["kapazitaet"]

    # ------------------------------------------------------------------
    # Abfrage: von gamedata.py genutzt
    # ------------------------------------------------------------------
    def cost_growth_rate(self, key: str, default: float) -> float:
        return self._growth_rate(self.data["cost_points"].get(key, {}), default,
                                   value_fn=lambda v: sum(v.get(r, 0) for r in ("gold", "stein", "holz")))

    def time_growth_rate(self, key: str, default: float) -> float:
        return self._growth_rate(self.data["time_points"].get(key, {}), default,
                                   value_fn=lambda v: v.get("s", 0))

    def storage_growth_rate(self, default: float) -> float:
        points = self.data["storage_points"].get("kapazitaet", {})
        return self._growth_rate({k: {"v": v} for k, v in points.items()}, default,
                                   value_fn=lambda v: v.get("v", 0))

    def production_delta(self, key: str, level: int) -> Res | None:
        entry = self.data["prod_deltas"].get(key, {}).get(str(level))
        if entry is None:
            return None
        return Res.from_dict(entry)

    @staticmethod
    def _growth_rate(points: dict, default: float, value_fn) -> float:
        """Geometrisches Mittel der Verhaeltnisse aufeinanderfolgender
        Beobachtungspunkte (nach Stufe sortiert). Mit <2 Punkten: default."""
        pairs = sorted(((int(lvl), value_fn(v)) for lvl, v in points.items()), key=lambda x: x[0])
        ratios = []
        for (l1, v1), (l2, v2) in zip(pairs, pairs[1:]):
            if v1 > 0 and v2 > 0 and l2 > l1:
                ratios.append((v2 / v1) ** (1.0 / (l2 - l1)))
        if not ratios:
            return default
        return geometric_mean(ratios)
