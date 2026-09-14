"""Priorisierungs-Kaskade, uebernommen aus dem vom Nutzer bereitgestellten
"bot test"-Skript (choose_upgrade), angepasst auf unsere verifizierten
Live-Feldnamen (kein Alias-Raten noetig, kosten/naechste_stufe/verfuegbar
kommen bereits exakt aus GET /islands/{id}/buildings - kein separater
building_detail-Call).

Kombiniert mit store.py: die Ressourcen-Gebaeude (Goldmine/Steingrube/
Saegewerk) werden nicht mehr nur nach fester Gewichtung bewertet, sondern
nach dem ECHTEN gelernten Produktionsertrag der naechsten Stufe, sobald
store.py genug Beobachtungen hat. Bis dahin dienen die Gewichte aus
config.PRIORITY_WEIGHTS als Startwert.
"""
from __future__ import annotations

import config


def affordable(cost: dict, current: dict) -> bool:
    return all(current.get(r, 0.0) >= cost.get(r, 0.0) for r in config.RESOURCE_KEYS)


def normalized_cost(cost: dict, capacity: float) -> float:
    return sum(cost.get(r, 0.0) / max(capacity, 1.0) for r in config.RESOURCE_KEYS)


def total_cost(cost: dict) -> float:
    return sum(cost.get(r, 0.0) for r in config.RESOURCE_KEYS)


def projected_levels(buildings: list, active_orders: list) -> dict:
    """Aktuelle Stufe + bereits in der Warteschlange steckende Ausbauten."""
    levels = {b["typ"]: int(b.get("stufe", 0)) for b in buildings}
    for order in active_orders:
        name = order.get("building_typ")
        if name:
            levels[name] = levels.get(name, 0) + 1
    return levels


def collect_candidates(buildings: list, levels: dict) -> dict:
    result = {}
    for b in buildings:
        name = b.get("typ")
        if name not in config.TRACKED_BUILDINGS or not b.get("verfuegbar", True):
            continue
        if b.get("naechste_stufe") is None or not b.get("kosten"):
            continue
        level = levels.get(name, int(b.get("stufe", 0)))
        result[name] = {"building": name, "level": level, "target_level": level + 1, "cost": b["kosten"]}
    return result


def _resource_score(name: str, candidate: dict, capacity: float, store) -> float:
    cost_norm = max(normalized_cost(candidate["cost"], capacity), 1e-6)
    learned = store.production_delta(name, candidate["target_level"])
    if learned is not None:
        gain = learned.gold + learned.stein + learned.holz
        if gain > 0:
            return gain / cost_norm
    weight = config.PRIORITY_WEIGHTS.get(name, 100.0)
    return weight / cost_norm


def choose_upgrade(candidates: dict, current: dict, capacity: float, store, allow_low: bool):
    payable = {n: c for n, c in candidates.items() if affordable(c["cost"], current)}

    # 1. Ressourcen-Gebaeude zuerst - bewertet nach gelerntem Ertrag pro Kosten,
    #    sonst nach statischer Gewichtung.
    resource_choices = [
        (_resource_score(n, payable[n], capacity, store), payable[n])
        for n in config.RESOURCE_BUILDINGS if n in payable
    ]
    if resource_choices:
        return max(resource_choices, key=lambda x: x[0])[1]

    resource_candidates = [c for n, c in candidates.items() if n in config.RESOURCE_BUILDINGS]
    average_resource_cost = (
        sum(total_cost(c["cost"]) for c in resource_candidates) / len(resource_candidates)
        if resource_candidates else None
    )

    # 2a. Lagerhaus, sobald irgendein anderer anstehender Ausbau schon nah an
    #     die Kapazitaetsgrenze geht.
    trigger_pool = [c for n, c in candidates.items() if n != "lagerhaus" and n not in config.LOW_PRIORITY_BUILDINGS]
    storage_needed = any(
        c["cost"].get(r, 0.0) >= config.STORAGE_TRIGGER_RATIO * max(capacity, 1.0)
        for c in trigger_pool for r in config.RESOURCE_KEYS
    )
    if storage_needed and "lagerhaus" in payable:
        return payable["lagerhaus"]

    # 2b. Hauptgebaeude nur, wenn deutlich guenstiger als der Ressourcen-Durchschnitt.
    if "haupthaus" in payable and average_resource_cost is not None:
        if total_cost(payable["haupthaus"]["cost"]) <= config.HAUPTHAUS_MAX_COST_RATIO * average_resource_cost:
            return payable["haupthaus"]

    # 2c. Hafen.
    if "hafen" in payable:
        return payable["hafen"]

    # 3. Kaserne.
    if "kaserne" in payable:
        return payable["kaserne"]

    # 4. Steinmauer/Wachturm erst, wenn die Warteschlange schon eine Weile leer
    #    war und nichts Wichtigeres bezahlbar ist (siehe bot.py: LOW_PRIORITY_SINCE).
    if allow_low:
        low_choices = []
        for name in config.LOW_PRIORITY_BUILDINGS:
            if name in payable:
                weight = config.PRIORITY_WEIGHTS.get(name, 5.0)
                score = weight / max(normalized_cost(payable[name]["cost"], capacity), 1e-6)
                low_choices.append((score, payable[name]))
        if low_choices:
            return max(low_choices, key=lambda x: x[0])[1]
    return None
