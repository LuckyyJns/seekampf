import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config
import gamedata
import report
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
HEALTH = {"failures": 0, "alerted": False}


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


def process_island(client, island_id, store, logger):
    key = str(island_id)
    orders = client.get_construction_queue(island_id)
    resources = client.get_island_resources(island_id)
    buildings = client.get_island_buildings(island_id)

    store.observe(buildings, resources, storage_key=_lager_key(buildings))

    active_orders = [
        o for o in orders if o.get("status", "").lower() not in config.FINISHED_CONSTRUCTION_STATUSES
    ]
    free_slots = config.MAX_QUEUE_SLOTS - len(active_orders)

    acted = 0
    started: list[str] = []
    next_hint = None
    while free_slots > 0 and acted < config.MAX_UPGRADES_PER_TICK:
        now_dt = datetime.now(TZ)
        current = _usable_resources(resources)
        capacity = float(resources.get("kapazitaet", 0) or 0)
        levels = projected_levels(buildings, active_orders)
        candidates = collect_candidates(buildings, levels)

        now = time.time()
        queue_empty = len(active_orders) == 0
        if not queue_empty:
            _stop_low_priority(store, key)
        allow_low = (
            queue_empty and key in LOW_PRIORITY_SINCE
            and now - LOW_PRIORITY_SINCE[key] >= config.LOW_PRIORITY_DELAY_SECONDS
        )

        chosen = choose_upgrade(candidates, current, capacity, store, allow_low)

        if chosen is None:
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

    _log_status(logger, island_id, resources, active_orders, started, next_hint, now_dt)

    return resources, orders, next_hint


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
    """Alarm, wenn mehrere Durchlaeufe hintereinander scheitern - und Entwarnung,
    sobald es wieder laeuft. Pro Stoerung jeweils genau eine Nachricht."""
    if ok:
        if HEALTH["alerted"]:
            _alert(logger, "Seekampf-Bot laeuft wieder",
                   f"Nach {HEALTH['failures']} fehlgeschlagenen Durchlaeufen war der letzte "
                   f"Durchlauf wieder erfolgreich.")
            HEALTH["alerted"] = False
        HEALTH["failures"] = 0
        return

    HEALTH["failures"] += 1
    if HEALTH["failures"] >= config.ALERT_AFTER_FAILED_TICKS and not HEALTH["alerted"]:
        minutes = HEALTH["failures"] * config.POLL_INTERVAL_SECONDS // 60
        _alert(logger, "Seekampf-Bot: Störung",
               f"{HEALTH['failures']} Durchlaeufe in Folge fehlgeschlagen (seit ca. {minutes} Minuten "
               f"kein Ausbau moeglich). Details: journalctl -u seekampf-ressourcen-bot -n 50")
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
    bodies = [
        report.build_report(
            island_id=island_id, island_name=name, resources=r, queue=o, next_hint=hint,
            since=since, until=now, log_dir=config.LOG_DIR,
        )
        for island_id, (name, r, o, hint) in island_reports.items()
    ]
    notifier = _notifier()
    if notifier is None:
        return
    ok = notifier.send("Seekampf Morgenreport", "\n\n---\n\n".join(bodies))
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


def tick(client, store, logger) -> bool:
    """Ein Durchlauf ueber alle Inseln. True, wenn mindestens eine Insel
    fehlerfrei bearbeitet werden konnte (siehe _track_health)."""
    try:
        islands = client.get_my_islands()
    except (ApiError, requests.exceptions.RequestException) as exc:
        logger.error("Konnte Inselliste nicht laden: %s", exc)
        return False

    if not islands:
        logger.error("Die API meldet keine einzige Insel fuer diesen Account.")
        return False

    island_reports = {}
    for island in islands:
        island_id, name = island["id"], island["name"]
        try:
            resources, orders, hint = process_island(client, island_id, store, logger)
            island_reports[island_id] = (name, resources, orders, hint)
        except (ApiError, requests.exceptions.RequestException) as exc:
            logger.error("API-Fehler auf Insel %s: %s", island_id, exc)
        except Exception:
            logger.exception("Unerwarteter Fehler auf Insel %s", island_id)

    _maybe_send_report(island_reports, store, logger)
    return bool(island_reports)


def main():
    logger = get_logger()
    logger.info("Seekampf-Bot gestartet (Poll-Intervall: %ss, Planer: Prioritaets-Kaskade, "
                "Ertragsbewertung aus %s)", config.POLL_INTERVAL_SECONDS,
                "den Regeltabellen" if gamedata.available() else "gelernten Beobachtungen")

    if not config.API_KEY:
        logger.error("SEEKAMPF_API_KEY ist nicht gesetzt (.env pruefen). Beende.")
        _alert(logger, "Seekampf-Bot startet nicht",
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

    while True:
        try:
            ok = tick(client, store, logger)
        except Exception:
            # Letztes Sicherheitsnetz: der Dauerprozess soll nie durch einen
            # einzelnen fehlerhaften Tick sterben.
            logger.exception("Unerwarteter Fehler im Tick, laeuft beim naechsten Intervall weiter")
            ok = False
        _track_health(ok, logger)
        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
