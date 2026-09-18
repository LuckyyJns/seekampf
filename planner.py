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
import gamedata


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


def resource_gain(name: str, candidate: dict, store) -> tuple[float | None, str]:
    """Produktionsplus pro Stunde, das dieser Ausbau bringt - und woher der Wert
    stammt. Erste Wahl sind die offiziellen Regeltabellen (gamedata.py), die
    jede Stufe kennen; die aus Beobachtungen gelernten Werte greifen nur, wenn
    die Tabelle fehlt (Datei nicht geholt, Stufe ausserhalb 1-20)."""
    gain = gamedata.production_gain(name, candidate["level"], candidate["target_level"])
    if gain is not None:
        return gain, "Tabelle"
    learned = store.production_delta(name, candidate["target_level"])
    if learned is not None:
        return learned.gold + learned.stein + learned.holz, "gelernt"
    return None, "Gewichtung"


def _resource_score(name: str, candidate: dict, capacity: float, store) -> float:
    cost_norm = max(normalized_cost(candidate["cost"], capacity), 1e-6)
    gain, _ = resource_gain(name, candidate, store)
    if gain is not None and gain > 0:
        return gain / cost_norm
    weight = config.PRIORITY_WEIGHTS.get(name, 100.0)
    return weight / cost_norm


def average_resource_cost(candidates: dict) -> float | None:
    """Durchschnittliche Gesamtkosten der Ressourcen-Gebaeude-Ausbauten."""
    resource_candidates = [c for n, c in candidates.items() if n in config.RESOURCE_BUILDINGS]
    if not resource_candidates:
        return None
    return sum(total_cost(c["cost"]) for c in resource_candidates) / len(resource_candidates)


def storage_needed(candidates: dict, capacity: float) -> bool:
    """True, sobald irgendein anderer anstehender Ausbau schon nah an die
    Kapazitaetsgrenze geht - dann bekommt das Lagerhaus Vorrang."""
    trigger_pool = [
        c for n, c in candidates.items()
        if n != "lagerhaus" and n not in config.LOW_PRIORITY_BUILDINGS
    ]
    return any(
        c["cost"].get(r, 0.0) >= config.STORAGE_TRIGGER_RATIO * max(capacity, 1.0)
        for c in trigger_pool for r in config.RESOURCE_KEYS
    )


def _best_by_weight(names, payable: dict, capacity: float, default_weight: float):
    """Bestes Gebaeude einer Prioritaetsstufe: Gewichtung pro (normierten) Kosten."""
    choices = []
    for name in names:
        if name in payable:
            weight = config.PRIORITY_WEIGHTS.get(name, default_weight)
            score = weight / max(normalized_cost(payable[name]["cost"], capacity), 1e-6)
            choices.append((score, payable[name]))
    if not choices:
        return None
    return max(choices, key=lambda x: x[0])[1]


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

    average_cost = average_resource_cost(candidates)

    # 2a. Lagerhaus, sobald irgendein anderer anstehender Ausbau schon nah an
    #     die Kapazitaetsgrenze geht.
    if storage_needed(candidates, capacity) and "lagerhaus" in payable:
        return payable["lagerhaus"]

    # 2b. Hauptgebaeude nur, wenn deutlich guenstiger als der Ressourcen-Durchschnitt.
    if "haupthaus" in payable and average_cost is not None:
        if total_cost(payable["haupthaus"]["cost"]) <= config.HAUPTHAUS_MAX_COST_RATIO * average_cost:
            return payable["haupthaus"]

    # 3. Hafen und Kaserne auf einer Stufe, innerhalb nach Gewichtung pro Kosten.
    mid = _best_by_weight(config.MID_PRIORITY_BUILDINGS, payable, capacity, 30.0)
    if mid is not None:
        return mid

    # 4. Steinmauer/Wachturm erst, wenn die Warteschlange schon eine Weile leer
    #    war und die Kaskade in dieser Zeit nichts Wichtigeres gebaut hat
    #    (siehe bot.py: LOW_PRIORITY_SINCE).
    if allow_low:
        return _best_by_weight(config.LOW_PRIORITY_BUILDINGS, payable, capacity, 5.0)
    return None


def rejection_reason(name: str, candidates: dict, capacity: float, allow_low: bool) -> str:
    """Warum wurde dieser bezahlbare Kandidat trotzdem nicht gebaut?
    Spiegelt exakt die Regeln aus choose_upgrade wider (fuer die Logausgabe)."""
    if name in config.LOW_PRIORITY_BUILDINGS and not allow_low:
        return "niedrige Prioritaet"
    if name == "lagerhaus" and not storage_needed(candidates, capacity):
        return (f"noch nicht noetig, kein Ausbau kostet mehr als "
                f"{config.STORAGE_TRIGGER_RATIO:.0%} der Lagerkapazitaet")
    if name == "haupthaus":
        average_cost = average_resource_cost(candidates)
        if average_cost is not None:
            own = total_cost(candidates[name]["cost"])
            if own > config.HAUPTHAUS_MAX_COST_RATIO * average_cost:
                return (f"zu teuer: {own:.0f} statt hoechstens "
                        f"{config.HAUPTHAUS_MAX_COST_RATIO * average_cost:.0f} "
                        f"({config.HAUPTHAUS_MAX_COST_RATIO:.0%} der Ressourcen-Durchschnittskosten)")
    return "von einem hoeher priorisierten Ausbau verdraengt"


def missing_resources(cost: dict, current: dict) -> dict:
    """Welche Rohstoffe fehlen noch und wie viel (nur die fehlenden)."""
    return {
        r: cost.get(r, 0.0) - current.get(r, 0.0)
        for r in config.RESOURCE_KEYS
        if cost.get(r, 0.0) - current.get(r, 0.0) > 0
    }
