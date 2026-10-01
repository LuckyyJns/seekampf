import os
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config
import gamedata
import report
import steuerung
from api_client import ApiError, SeekampfClient
from logger_setup import get_logger
from notify import build_notifier
from planner import (
    affordable,
    choose_upgrade,
    collect_candidates,
    missing_resources,
    projected_levels,
    rejection_reason,
    resource_gain,
)
from store import Store

TZ = ZoneInfo(config.TIMEZONE_NAME)

# Seit wann ist die Warteschlange einer Insel leer, ohne dass die
# Prioritaets-Kaskade etwas Wichtigeres bauen wollte? Wird in data/learned.json
# mitgespeichert und beim Start wieder geladen, damit ein Neustart die
# Leerlauf-Wartezeit nicht von vorne beginnen laesst.
LOW_PRIORITY_SINCE: dict[str, float] = {}

# Gesundheitszustand fuer die Ausfall-Alarme (siehe _track_health).
HEALTH = {"failures": 0, "alerted": False, "since": None}

# Fruehester Zeitpunkt, zu dem sich auf einer Insel etwas tut (Bauplatz frei,
# naechster Ausbau bezahlbar) - der naechste Durchlauf wird darauf gelegt.
NAECHSTES_EREIGNIS = {"ts": None}

# Ergebnisse der letzten Befehle aus dem Seekampf-Hub, fuer status.json.
BEFEHL_ERGEBNISSE: deque = deque(maxlen=20)

# Zeitpunkt des naechsten planmaessigen Durchlaufs (fuer den Seekampf-Hub).
NAECHSTER_TICK = {"ts": 0.0}


def _load_low_priority(store) -> None:
    """Leerlauf-Uhren aus dem Store in den Speicher holen (einmal beim Start)."""
    LOW_PRIORITY_SINCE.clear()
    for key, value in (store.data.get("low_priority_since") or {}).items():
        try:
            LOW_PRIORITY_SINCE[key] = float(value)
        except (TypeError, ValueError):
            continue


def _start_low_priority(store, key: str, now_ts: float) -> None:
    """Uhr starten, falls sie nicht schon laeuft."""
    if key in LOW_PRIORITY_SINCE:
        return
    LOW_PRIORITY_SINCE[key] = now_ts
    store.data.setdefault("low_priority_since", {})[key] = now_ts
    store.save()


def _stop_low_priority(store, key: str) -> None:
    LOW_PRIORITY_SINCE.pop(key, None)
    if (store.data.get("low_priority_since") or {}).pop(key, None) is not None:
        store.save()


def _usable_resources(resources: dict) -> dict:
    """Fuer Ausbau-Entscheidungen nutzbare Rohstoffe: der komplette Lagerstand."""
    return {r: float(resources.get(r, 0.0) or 0.0) for r in config.RESOURCE_KEYS}


def _lager_key(buildings: list) -> str | None:
    for b in buildings:
        haystack = f"{b.get('typ', '')} {b.get('name', '')}".lower()
        if any(kw in haystack for kw in config.WAREHOUSE_BUILDING_KEYWORDS):
            return b["typ"]
    return None


# ----------------------------------------------------------------------
# Hilfsfunktionen fuer die Log-Ausgabe: warum passiert gerade nichts und
# wann darf wieder gebaut werden?
# ----------------------------------------------------------------------

def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}d {h}h"
    return f"{h}h {m:02d}min" if h else f"{m}min"


def _fmt_res(values: dict, sign: str = "") -> str:
    return "/".join(f"{sign}{float(values.get(r, 0) or 0):.0f}" for r in config.RESOURCE_KEYS)


def _parse_api_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def _next_slot_free(active_orders: list, now: datetime) -> tuple[float | None, dict | None]:
    """Sekunden bis der naechste Warteschlangen-Platz frei wird (+ der Auftrag)."""
    upcoming = []
    for order in active_orders:
        finish = _parse_api_dt(order.get("finish_at"))
        if finish is not None:
            upcoming.append(((finish - now).total_seconds(), order))
    if not upcoming:
        return None, None
    return min(upcoming, key=lambda x: x[0])


def _low_priority_release_in(key: str, queue_empty: bool, now_ts: float) -> float | None:
    """Sekunden, bis Steinmauer/Wachturm freigegeben werden - None, wenn die
    Wartezeit gar nicht laeuft (Warteschlange nicht leer)."""
    if not queue_empty or key not in LOW_PRIORITY_SINCE:
        return None
    return max(0.0, config.LOW_PRIORITY_DELAY_SECONDS - (now_ts - LOW_PRIORITY_SINCE[key]))


def _allow_low_at(key: str, queue_empty: bool, at_ts: float) -> bool:
    if not queue_empty or key not in LOW_PRIORITY_SINCE:
        return False
    return at_ts - LOW_PRIORITY_SINCE[key] >= config.LOW_PRIORITY_DELAY_SECONDS


def _projected_usable(resources: dict, seconds_ahead: float) -> dict:
    """Lagerstand in `seconds_ahead` Sekunden (Produktion hochgerechnet, bei der
    Lagerkapazitaet gedeckelt)."""
    prod = resources.get("produktion_pro_h") or {}
    capacity = float(resources.get("kapazitaet", 0) or 0)
    out = {}
    for r in config.RESOURCE_KEYS:
        rate = float(prod.get(r, 0) or 0)
        grown = float(resources.get(r, 0) or 0) + rate * seconds_ahead / 3600.0
        out[r] = min(capacity, grown)
    return out


def _eta_seconds(cost: dict, resources: dict, usable_now: dict) -> tuple[float | None, str]:
    """(Sekunden bis die Kosten zusammengespart sind, Hinderungsgrund).
    Sekunden = None heisst: nie erreichbar - entweder passt der Preis nicht ins
    Lager ("lager") oder der Rohstoff wird gar nicht produziert ("produktion")."""
    prod = resources.get("produktion_pro_h") or {}
    capacity = float(resources.get("kapazitaet", 0) or 0)
    worst = 0.0
    for r in config.RESOURCE_KEYS:
        need = float(cost.get(r, 0) or 0) - usable_now.get(r, 0.0)
        if need <= 0:
            continue
        if float(cost.get(r, 0) or 0) > capacity:
            return None, "lager"
        rate = float(prod.get(r, 0) or 0)
        if rate <= 0:
            return None, "produktion"
        worst = max(worst, need / rate * 3600.0)
    return worst, ""


def _forecast(candidates: dict, resources: dict, capacity: float, store, key: str,
              queue_empty: bool, now: datetime, now_ts: float):
    """Wann erlauben die Regeln den naechsten Ausbau?

    Prueft die Zeitpunkte, an denen sich etwas aendern kann (ein Kandidat wird
    bezahlbar bzw. die Steinmauer/Wachturm-Sperre faellt) und laesst dort die
    echte Prioritaets-Kaskade entscheiden.
    Rueckgabe: (Sekunden, gewaehlter Kandidat, unerreichbare Kandidaten je Grund)."""
    usable_now = _usable_resources(resources)
    points = {0.0}
    unreachable: dict[str, list[str]] = {"lager": [], "produktion": []}
    for name, candidate in candidates.items():
        eta, cause = _eta_seconds(candidate["cost"], resources, usable_now)
        if eta is None:
            unreachable[cause].append(name)
        else:
            points.add(eta + 1.0)  # 1s Puffer gegen Rundungsfehler
    release = _low_priority_release_in(key, queue_empty, now_ts)
    if release is not None:
        points.add(release + 1.0)

    for seconds in sorted(points):
        projected = _projected_usable(resources, seconds)
        allow_low = _allow_low_at(key, queue_empty, now_ts + seconds)
        chosen = choose_upgrade(candidates, projected, capacity, store, allow_low)
        if chosen is not None:
            return seconds, chosen, unreachable
    return None, None, unreachable


def _skip_reason(candidates: dict, resources: dict, capacity: float, store, key: str,
                 queue_empty: bool, allow_low: bool, now: datetime, now_ts: float) -> str:
    """Warum wurde kein (weiterer) Ausbau gestartet - und wann geht es weiter?"""
    if not candidates:
        return "keine ausbaubaren Gebaeude verfuegbar (Voraussetzungen fehlen)"

    usable_now = _usable_resources(resources)
    payable = [n for n, c in candidates.items() if affordable(c["cost"], usable_now)]

    parts = []
    if payable:
        parts.append("bezahlbar, aber nicht dran: " + ", ".join(
            f"{n} ({rejection_reason(n, candidates, capacity, allow_low)})" for n in sorted(payable)
        ))
    else:
        parts.append("kein Ausbau bezahlbar")

    seconds, chosen, unreachable = _forecast(
        candidates, resources, capacity, store, key, queue_empty, now, now_ts
    )
    if chosen is not None:
        missing = missing_resources(chosen["cost"], usable_now)
        fehlt = (" - es fehlen " + ", ".join(f"{v:.0f} {r.capitalize()}" for r, v in missing.items())) if missing else ""
        ready_at = (now + timedelta(seconds=seconds)).strftime("%H:%M")
        parts.append(
            f"naechster Ausbau: {chosen['building']} → {chosen['target_level']} "
            f"in {_fmt_duration(seconds)} (ca. {ready_at}){fehlt}"
        )
    else:
        parts.append("aktuell ist kein Ausbau in Sicht")

    low_payable = [n for n in payable if n in config.LOW_PRIORITY_BUILDINGS]
    if low_payable and not allow_low:
        low_names = "/".join(config.LOW_PRIORITY_BUILDINGS)
        release = _low_priority_release_in(key, queue_empty, now_ts)
        if release is not None:
            parts.append(f"{low_names} freigegeben in {_fmt_duration(release)}")
        else:
            parts.append(
                f"{low_names} erst, wenn die Warteschlange leer ist und danach "
                f"{_fmt_duration(config.LOW_PRIORITY_DELAY_SECONDS)} nichts Wichtigeres gebaut wird"
            )

    if unreachable["lager"]:
        parts.append("passt nicht ins Lager, Lagerhaus noetig: " + ", ".join(sorted(unreachable["lager"])))
    if unreachable["produktion"]:
        parts.append("wartet auf Rohstoffe ohne Produktion: " + ", ".join(sorted(unreachable["produktion"])))

    return "; ".join(parts)


# ----------------------------------------------------------------------
# Steuerung aus dem Seekampf-Hub: Priorisierung auf ein einzelnes Gebaeude
# ----------------------------------------------------------------------

def _zielgebaeude(einstellung: dict, levels: dict, buildings_by_typ: dict) -> tuple[str | None, str | None]:
    """Welches Gebaeude die Priorisierung gerade vorgibt - None heisst: die
    normale Kaskade entscheidet. Der zweite Wert erklaert, warum eine gesetzte
    Priorisierung nicht (mehr) greift."""
    modus = einstellung.get("modus") or "auto"
    if modus == "auto":
        return None, None
    b = buildings_by_typ.get(modus)
    if b is None:
        return None, f"Priorisierung {modus}: Gebaeude unbekannt - baut automatisch"
    bis = einstellung.get("bis_stufe")
    if bis and levels.get(modus, 0) >= int(bis):
        return None, f"Ziel {modus} Stufe {int(bis)} erreicht - baut wieder automatisch"
    if b.get("naechste_stufe") is None and b.get("verfuegbar", True):
        return None, f"{modus} hat die Hoechststufe erreicht - baut wieder automatisch"
    return modus, None


def _waehlen(candidates: dict, current: dict, capacity: float, store, allow_low: bool, ziel: str | None):
    """Mit Priorisierung nur das Zielgebaeude, und nur wenn bezahlbar - sonst
    wird gewartet, statt etwas anderes zu bauen."""
    if ziel is not None:
        c = candidates.get(ziel)
        return c if c is not None and affordable(c["cost"], current) else None
    return choose_upgrade(candidates, current, capacity, store, allow_low)


def _ziel_grund(ziel: str, candidates: dict, buildings_by_typ: dict, resources: dict, now: datetime) -> str:
    """Warum das priorisierte Gebaeude gerade nicht gebaut wird."""
    c = candidates.get(ziel)
    if c is None:
        b = buildings_by_typ.get(ziel) or {}
        if not b.get("verfuegbar", True):
            return f"Priorisierung {ziel}: noch nicht verfuegbar (Haupthaus Stufe {b.get('haupthaus_ab')} noetig)"
        return f"Priorisierung {ziel}: derzeit nicht ausbaubar"
    usable = _usable_resources(resources)
    missing = missing_resources(c["cost"], usable)
    eta, cause = _eta_seconds(c["cost"], resources, usable)
    fehlt = ", ".join(f"{v:.0f} {r.capitalize()}" for r, v in missing.items())
    if cause == "lager":
        return f"Priorisierung {ziel} → {c['target_level']}: passt nicht ins Lager, Lagerhaus noetig"
    if cause == "produktion":
        return f"Priorisierung {ziel} → {c['target_level']}: es fehlen {fehlt}, ohne Produktion"
    ready_at = (now + timedelta(seconds=eta or 0)).strftime("%H:%M")
    return (f"Priorisierung {ziel} → {c['target_level']}: wartet auf Rohstoffe, es fehlen {fehlt}, "
            f"bezahlbar in {_fmt_duration(eta or 0)} (ca. {ready_at})")


def _plan(candidates: dict, resources: dict, capacity: float, store, key: str, queue_empty: bool,
          active_orders: list, now: datetime, now_ts: float, ziel: str | None) -> dict | None:
    """Was der Bot als Naechstes bauen wuerde und fruehestens wann - fuer den
    Seekampf-Hub. Beruecksichtigt Rohstoffe und, bei voller Warteschlange, den
    naechsten freien Platz."""
    usable = _usable_resources(resources)
    if ziel is not None:
        chosen = candidates.get(ziel)
        if chosen is None:
            return None
        seconds, cause = _eta_seconds(chosen["cost"], resources, usable)
    else:
        seconds, chosen, _ = _forecast(candidates, resources, capacity, store, key, queue_empty, now, now_ts)
        cause = ""
        if chosen is None:
            return None
    if seconds is not None and len(active_orders) >= config.MAX_QUEUE_SLOTS:
        slot_seconds, _ = _next_slot_free(active_orders, now)
        if slot_seconds is not None:
            seconds = max(seconds, slot_seconds)
    return {
        "gebaeude": chosen["building"],
        "ziel_stufe": chosen["target_level"],
        "in_s": None if seconds is None else round(seconds),
        "fehlt": {r: round(v) for r, v in missing_resources(chosen["cost"], usable).items()},
        "unerreichbar": cause or None,
    }


def _log_status(logger, island_id, resources, active_orders, started, reason, now):
    prod = resources.get("produktion_pro_h") or {}
    capacity = float(resources.get("kapazitaet", 0) or 0)

    fields = [
        f"Insel {island_id}",
        f"Warteschlange {len(active_orders)}/{config.MAX_QUEUE_SLOTS}",
        f"Lager {_fmt_res(resources)} von {capacity:.0f}",
        f"Produktion {_fmt_res(prod, '+')} pro h",
    ]
    slot_seconds, slot_order = _next_slot_free(active_orders, now)
    if slot_order is not None and not (reason and "naechster Platz frei" in reason):
        fields.append(
            f"naechster Slot frei in {_fmt_duration(slot_seconds)} "
            f"({slot_order.get('building_typ')} → {slot_order.get('ziel_stufe')})"
        )

    if started:
        fields.append("gestartet: " + ", ".join(started))
    if reason:
        fields.append(f"kein Ausbau: {reason}")

    logger.info(" | ".join(fields))


def _auftraege_ausfuehren(client, island_id, auftraege: list, logger) -> None:
    """Ausbauten, die im Seekampf-Hub von Hand angestossen wurden. Sie laufen auch
    auf ausgeschalteten Inseln und unabhaengig von Priorisierung und Sperren."""
    for befehl in auftraege:
        gebaeude = befehl.get("gebaeude")
        try:
            client.start_upgrade(island_id, gebaeude)
        except ApiError as exc:
            logger.error("Insel %s: Ausbau '%s' von Hand fehlgeschlagen: %s", island_id, gebaeude, exc)
            _befehl_ergebnis(befehl, False, f"{gebaeude}: {exc.message}")
            continue
        logger.info("Insel %s: Ausbau '%s' von Hand gestartet (Seekampf-Hub)", island_id, gebaeude)
        _befehl_ergebnis(befehl, True, f"{gebaeude} in die Warteschlange gestellt")


def _befehl_ergebnis(befehl: dict, ok: bool, text: str) -> None:
    BEFEHL_ERGEBNISSE.appendleft({
        "id": befehl.get("id"), "typ": befehl.get("typ"), "insel_id": befehl.get("insel_id"),
        "ok": ok, "text": text, "zeit": time.time(),
    })


def process_island(client, island_id, store, logger, einstellung: dict | None = None,
                   auftraege: list | None = None):
    einstellung = einstellung or dict(steuerung.STANDARD)
    key = str(island_id)
    if auftraege:
        _auftraege_ausfuehren(client, island_id, auftraege, logger)
    orders = client.get_construction_queue(island_id)
    resources = client.get_island_resources(island_id)
    buildings = client.get_island_buildings(island_id)

    store.observe(buildings, resources, storage_key=_lager_key(buildings), island_key=key)

    active_orders = [
        o for o in orders if o.get("status", "").lower() not in config.FINISHED_CONSTRUCTION_STATUSES
    ]
    free_slots = config.MAX_QUEUE_SLOTS - len(active_orders)
    gesperrt = set(einstellung.get("gesperrt") or [])

    acted = 0
    started: list[str] = []
    next_hint = None
    ziel_hinweis = None
    if not einstellung.get("aktiv", True):
        next_hint = "Insel im Seekampf-Hub ausgeschaltet"
        free_slots = 0  # nichts Neues starten, laufende Auftraege laufen weiter
    while free_slots > 0 and acted < config.MAX_UPGRADES_PER_TICK:
        now_dt = datetime.now(TZ)
        current = _usable_resources(resources)
        capacity = float(resources.get("kapazitaet", 0) or 0)
        levels = projected_levels(buildings, active_orders)
        candidates = {n: c for n, c in collect_candidates(buildings, levels).items() if n not in gesperrt}
        by_typ = {b["typ"]: b for b in buildings}
        ziel, ziel_hinweis = _zielgebaeude(einstellung, levels, by_typ)

        now = time.time()
        queue_empty = len(active_orders) == 0
        if not queue_empty:
            _stop_low_priority(store, key)
        allow_low = (
            queue_empty and key in LOW_PRIORITY_SINCE
            and now - LOW_PRIORITY_SINCE[key] >= config.LOW_PRIORITY_DELAY_SECONDS
        )

        chosen = _waehlen(candidates, current, capacity, store, allow_low, ziel)

        if chosen is None:
            if ziel is not None:
                # Priorisierung: warten, bis das Zielgebaeude bezahlbar ist. Die
                # Leerlauf-Uhr fuer Steinmauer/Wachturm bleibt dabei unberuehrt.
                next_hint = _ziel_grund(ziel, candidates, by_typ, resources, now_dt)
                break
            # Die Leerlauf-Uhr fuer Steinmauer/Wachturm laeuft, sobald die
            # Warteschlange leer ist und die Prioritaets-Kaskade nichts
            # Wichtigeres baut. Entscheidend ist, was die Regeln TATSAECHLICH
            # bauen wuerden - nicht, was theoretisch bezahlbar waere: ein
            # Lagerhaus, das noch nicht noetig ist (oder ein zu teures
            # Haupthaus), blockiert die Uhr also nicht mehr.
            if queue_empty:
                _start_low_priority(store, key, now)
            else:
                _stop_low_priority(store, key)
            next_hint = _skip_reason(
                candidates, resources, capacity, store, key, queue_empty, allow_low, now_dt, now
            )
            break

        _stop_low_priority(store, key)
        try:
            client.start_upgrade(island_id, chosen["building"])
        except ApiError as exc:
            if exc.code in ("queue_full", "construction_queue_full"):
                logger.info("Insel %s: Warteschlange laut API voll, breche ab.", island_id)
                next_hint = "Warteschlange laut API voll."
                break
            logger.error("Insel %s: Ausbau '%s' fehlgeschlagen: %s", island_id, chosen["building"], exc)
            next_hint = f"{chosen['building']} fehlgeschlagen: {exc}"
            break

        logger.info(
            "Insel %s: Ausbau '%s' gestartet (Kosten: %s)", island_id, chosen["building"], chosen["cost"]
        )
        label = f"{chosen['building']} → {chosen['target_level']}"
        if chosen["building"] in config.RESOURCE_BUILDINGS:
            gain, quelle = resource_gain(chosen["building"], chosen, store)
            if gain:
                label += f" (+{gain:.0f}/h, {quelle})"
        started.append(label)
        acted += 1
        free_slots -= 1

        resources = client.get_island_resources(island_id)
        buildings = client.get_island_buildings(island_id)
        orders = client.get_construction_queue(island_id)
        active_orders = [
            o for o in orders if o.get("status", "").lower() not in config.FINISHED_CONSTRUCTION_STATUSES
        ]

    now_dt = datetime.now(TZ)
    if free_slots <= 0 and not next_hint:
        slot_seconds, slot_order = _next_slot_free(active_orders, now_dt)
        if slot_order is not None:
            next_hint = (
                f"Warteschlange voll ({len(active_orders)}/{config.MAX_QUEUE_SLOTS}), naechster Platz "
                f"frei in {_fmt_duration(slot_seconds)}"
            )
        else:
            next_hint = f"Warteschlange voll ({len(active_orders)}/{config.MAX_QUEUE_SLOTS})"
    elif acted >= config.MAX_UPGRADES_PER_TICK and not next_hint:
        next_hint = f"Limit von {config.MAX_UPGRADES_PER_TICK} Ausbauten pro Durchlauf erreicht"
    if ziel_hinweis:
        next_hint = f"{ziel_hinweis}; {next_hint}" if next_hint else ziel_hinweis

    _log_status(logger, island_id, resources, active_orders, started, next_hint, now_dt)

    # Fuer den Seekampf-Hub: was als Naechstes kaeme, mit dem Stand nach diesem Durchlauf.
    levels = projected_levels(buildings, active_orders)
    candidates = {n: c for n, c in collect_candidates(buildings, levels).items() if n not in gesperrt}
    by_typ = {b["typ"]: b for b in buildings}
    ziel, ziel_hinweis = _zielgebaeude(einstellung, levels, by_typ)
    plan = None
    if einstellung.get("aktiv", True):
        plan = _plan(candidates, resources, float(resources.get("kapazitaet", 0) or 0), store, key,
                     len(active_orders) == 0, active_orders, now_dt, time.time(), ziel)
    info = {
        "rohstoffe": resources,
        "warteschlange": [
            {"gebaeude": o.get("building_typ"), "ziel_stufe": o.get("ziel_stufe"), "status": o.get("status"),
             "start_at": o.get("start_at"), "finish_at": o.get("finish_at")}
            for o in sorted(active_orders, key=lambda o: o.get("queue_position") or 0)
        ],
        "gebaeude": [
            {k: b.get(k) for k in ("typ", "name", "stufe", "naechste_stufe", "verfuegbar",
                                   "haupthaus_ab", "kosten", "bauzeit_s")}
            for b in buildings
        ],
        "plan": plan,
        "priorisierung_aktiv": ziel,
        "priorisierung_hinweis": ziel_hinweis,
        "hinweis": next_hint,
        "gestartet": started,
    }
    return resources, orders, next_hint, info


def _notifier():
    """Telegram-Notifier, oder None wenn nicht konfiguriert."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return None
    return build_notifier({
        "service": "telegram",
        "bot_token": config.TELEGRAM_BOT_TOKEN,
        "chat_id": config.TELEGRAM_CHAT_ID,
    })


def _alert(logger, title: str, text: str) -> bool:
    """Sofort-Alarm aufs Handy (unabhaengig vom Morgenreport)."""
    notifier = _notifier()
    if notifier is None:
        logger.error("Alarm nicht zustellbar, Telegram nicht konfiguriert: %s", text)
        return False
    if notifier.send(title, text):
        logger.info("Alarm gesendet: %s", title)
        return True
    logger.error("Alarm konnte nicht gesendet werden: %s", title)
    return False


def _track_health(ok: bool, logger) -> None:
    """Alarm, wenn die Durchlaeufe laenger als ALERT_AFTER_FAILED_MINUTES am
    Stueck scheitern - und Entwarnung, sobald es wieder laeuft. Pro Stoerung
    jeweils genau eine Nachricht. Gemessen wird Zeit, nicht die Zahl der
    Durchlaeufe: deren Abstand haengt jetzt von der Bauplanung ab."""
    if ok:
        if HEALTH["alerted"]:
            dauer = _fmt_duration(time.time() - (HEALTH["since"] or time.time()))
            _alert(logger, "Upgrade-Bot laeuft wieder",
                   f"Nach {dauer} Stoerung ({HEALTH['failures']} fehlgeschlagene Durchlaeufe) war der "
                   f"letzte Durchlauf wieder erfolgreich.")
            HEALTH["alerted"] = False
        HEALTH["failures"] = 0
        HEALTH["since"] = None
        return

    HEALTH["failures"] += 1
    if HEALTH["since"] is None:
        HEALTH["since"] = time.time()
    seit = time.time() - HEALTH["since"]
    if seit >= config.ALERT_AFTER_FAILED_MINUTES * 60 and not HEALTH["alerted"]:
        _alert(logger, "Upgrade-Bot: Störung",
               f"Seit {_fmt_duration(seit)} schlagen die Durchlaeufe fehl ({HEALTH['failures']} in Folge) - "
               f"so lange wird nichts ausgebaut. Details: Seekampf-Hub, Bereich Gesundheit, "
               f"oder logs/bot-JJJJ-MM-TT.log")
        HEALTH["alerted"] = True


def _report_window_start(store, now: datetime) -> datetime:
    """Beginn des Berichtszeitraums: der letzte gesendete Report, sonst das
    Standardfenster (24h)."""
    raw = store.data.get("last_report_at")
    if raw:
        try:
            last = datetime.fromisoformat(raw)
            return last.replace(tzinfo=TZ) if last.tzinfo is None else last.astimezone(TZ)
        except ValueError:
            pass
    return now - timedelta(hours=config.REPORT_FALLBACK_WINDOW_HOURS)


def _maybe_send_report(island_reports, store, logger):
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    now = datetime.now(TZ)
    if now.hour < config.REPORT_HOUR:
        return
    today_str = now.date().isoformat()
    if store.data.get("last_report_date") == today_str or not island_reports:
        return

    since = _report_window_start(store, now)
    text = report.build_summary(island_reports, since=since, until=now, log_dir=config.LOG_DIR)
    notifier = _notifier()
    if notifier is None:
        return
    ok = notifier.send("Seekampf Morgenreport", text)
    if ok:
        store.data["last_report_date"] = today_str
        store.data["last_report_at"] = now.isoformat()
        store.save()
        logger.info(
            "Morgenreport gesendet (Zeitraum %s bis %s, %s).",
            since.strftime("%d.%m. %H:%M"), now.strftime("%d.%m. %H:%M"),
            _fmt_duration((now - since).total_seconds()),
        )
    else:
        logger.error("Morgenreport konnte nicht gesendet werden.")


def _status_schreiben(logger, inseln: dict, fehler: str | None) -> None:
    try:
        steuerung.status_schreiben({
            "zeit": time.time(),
            "letzter_tick": time.time(),
            "naechster_tick": NAECHSTER_TICK["ts"],
            "poll_s": config.POLL_INTERVAL_SECONDS,
            "max_warteschlange": config.MAX_QUEUE_SLOTS,
            "fehler": fehler,
            "inseln": inseln,
            "befehle": list(BEFEHL_ERGEBNISSE),
        })
    except OSError as exc:
        logger.error("status.json nicht schreibbar: %s", exc)


def tick(client, store, logger, befehle: list | None = None) -> bool:
    """Ein Durchlauf ueber alle Inseln. True, wenn mindestens eine Insel
    fehlerfrei bearbeitet werden konnte (siehe _track_health)."""
    befehle = befehle or []
    auftraege: dict[str, list] = {}
    for befehl in befehle:
        if befehl.get("typ") == "auftrag" and befehl.get("gebaeude"):
            auftraege.setdefault(str(befehl.get("insel_id")), []).append(befehl)
        elif befehl.get("typ") == "pruefen":
            _befehl_ergebnis(befehl, True, "Durchlauf ausgeloest")

    try:
        islands = client.get_my_islands()
    except (ApiError, requests.exceptions.RequestException) as exc:
        logger.error("Konnte Inselliste nicht laden: %s", exc)
        for liste in auftraege.values():
            for befehl in liste:
                _befehl_ergebnis(befehl, False, f"Inselliste nicht abrufbar: {exc}")
        _status_schreiben(logger, {}, f"Inselliste nicht abrufbar: {exc}")
        return False

    if not islands:
        logger.error("Die API meldet keine einzige Insel fuer diesen Account.")
        _status_schreiben(logger, {}, "Die API meldet keine Insel")
        return False

    try:
        for neu, vorlage in steuerung.neue_inseln_uebernehmen(islands):
            logger.info("Insel %s ist neu: Steuerung von Insel %s uebernommen (%s)", neu, vorlage,
                        steuerung.insel(steuerung.lesen(), neu))
    except OSError as exc:
        logger.error("Steuerung fuer neue Insel nicht schreibbar: %s", exc)
    einstellungen = steuerung.lesen()
    island_reports = {}
    status_inseln = {}
    ereignisse: list[float] = []
    for position, island in enumerate(islands):
        island_id, name = island["id"], island["name"]
        einstellung = steuerung.insel(einstellungen, island_id)
        eintrag = {"id": island_id, "name": name, "koordinaten": island.get("koordinaten", ""),
                   "position": position, "einstellung": einstellung, "zeit": time.time(), "fehler": None}
        try:
            resources, orders, hint, info = process_island(
                client, island_id, store, logger, einstellung, auftraege.pop(str(island_id), None))
            island_reports[island_id] = (name, resources, orders, hint)
            in_s = (info.get("plan") or {}).get("in_s")
            if in_s is not None:
                ereignisse.append(time.time() + in_s)
            eintrag.update(info)
        except (ApiError, requests.exceptions.RequestException) as exc:
            logger.error("API-Fehler auf Insel %s: %s", island_id, exc)
            eintrag["fehler"] = str(exc)
        except Exception as exc:
            logger.exception("Unerwarteter Fehler auf Insel %s", island_id)
            eintrag["fehler"] = f"{type(exc).__name__}: {exc}"
        status_inseln[str(island_id)] = eintrag

    for liste in auftraege.values():
        for befehl in liste:
            _befehl_ergebnis(befehl, False, f"Insel {befehl.get('insel_id')} gehoert nicht zum Konto")

    NAECHSTES_EREIGNIS["ts"] = min(ereignisse) if ereignisse else None
    _maybe_send_report(island_reports, store, logger)
    _status_schreiben(logger, status_inseln, None if island_reports else "keine Insel fehlerfrei bearbeitet")
    return bool(island_reports)


def main():
    logger = get_logger()
    logger.info("Upgrade-Bot gestartet (Poll-Intervall: %ss, Planer: Prioritaets-Kaskade, "
                "Ertragsbewertung aus %s)", config.POLL_INTERVAL_SECONDS,
                "den Regeltabellen" if gamedata.available() else "gelernten Beobachtungen")

    if not config.API_KEY:
        logger.error("SEEKAMPF_API_KEY ist nicht gesetzt (.env pruefen). Beende.")
        _alert(logger, "Upgrade-Bot startet nicht",
               "SEEKAMPF_API_KEY fehlt oder ist leer - bitte .env auf dem Pi pruefen. "
               "Der Bot baut bis dahin nichts aus.")
        # Fehlercode statt stiller Rueckkehr: systemd sieht den Fehlstart, gibt
        # nach StartLimitBurst auf und loest OnFailure (Telegram-Alarm) aus,
        # statt den Bot alle 10 Sekunden endlos neu zu starten.
        raise SystemExit(1)

    os.makedirs(os.path.dirname(config.STORE_PATH), exist_ok=True)
    store = Store(config.STORE_PATH)
    _load_low_priority(store)
    client = SeekampfClient()
    steuerung.geaendert()  # Ausgangsstand merken

    # Planmaessig alle POLL_INTERVAL_SECONDS; Befehle und geaenderte Steuerung
    # aus dem Seekampf-Hub loesen sofort einen Durchlauf aus (hoechstens alle
    # HUB_MIN_ABSTAND_S, damit schnelles Klicken die API nicht flutet).
    letzter = 0.0
    wartend: list = []
    angestossen = False
    while True:
        wartend += steuerung.befehle_holen()
        angestossen = angestossen or bool(wartend) or steuerung.geaendert()
        jetzt = time.time()
        faellig = jetzt >= NAECHSTER_TICK["ts"]
        if faellig or (angestossen and jetzt - letzter >= config.HUB_MIN_ABSTAND_S):
            befehle, wartend, angestossen = wartend, [], False
            NAECHSTER_TICK["ts"] = jetzt + config.POLL_INTERVAL_SECONDS
            NAECHSTES_EREIGNIS["ts"] = None
            try:
                ok = tick(client, store, logger, befehle)
            except Exception:
                # Letztes Sicherheitsnetz: der Dauerprozess soll nie durch einen
                # einzelnen fehlerhaften Tick sterben.
                logger.exception("Unerwarteter Fehler im Tick, laeuft beim naechsten Intervall weiter")
                ok = False
            letzter = time.time()
            _track_health(ok, logger)
            # Wird vor dem naechsten planmaessigen Durchlauf ein Bauplatz frei
            # oder ein Ausbau bezahlbar, genau dann nachsehen (kurz danach,
            # damit Server und Produktion sicher durch sind) - statt bis zu
            # POLL_INTERVAL_SECONDS ungenutzt zu warten.
            ereignis = NAECHSTES_EREIGNIS["ts"]
            if ok and ereignis is not None:
                frueh = max(ereignis + config.EREIGNIS_PUFFER_S, letzter + config.EREIGNIS_MIN_ABSTAND_S)
                NAECHSTER_TICK["ts"] = min(NAECHSTER_TICK["ts"], frueh)
        time.sleep(config.HUB_ABFRAGE_S)


if __name__ == "__main__":
    main()
