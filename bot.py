import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import config
import report
from api_client import ApiError, SeekampfClient
from logger_setup import get_logger
from notify import build_notifier
from planner import affordable, choose_upgrade, collect_candidates, projected_levels
from store import Store

# In-memory: seit wann ist die Warteschlange einer Insel leer und nichts
# Wichtiges (nur noch Steinmauer/Wachturm) bezahlbar? Ueberlebt keinen Neustart -
# im schlimmsten Fall wartet der Bot nach einem Neustart erneut die volle
# Verzoegerung ab, bevor Steinmauer/Wachturm drankommen. Unkritisch.
LOW_PRIORITY_SINCE: dict[str, float] = {}


def _day_buffer_pct() -> float:
    now = datetime.now(ZoneInfo(config.TIMEZONE_NAME))
    if config.DAY_BUFFER_START_HOUR <= now.hour < config.DAY_BUFFER_END_HOUR:
        return config.DAY_BUFFER_PCT
    return 0.0


def _usable_resources(resources: dict, buffer_pct: float) -> dict:
    """Fuer Ausbau-Entscheidungen nutzbare Rohstoffe: echter Bestand minus
    Tagesreserve (buffer_pct * Kapazitaet). Nur fuer die Auswahl - der Report
    zeigt weiterhin den echten Lagerstand."""
    capacity = float(resources.get("kapazitaet", 0) or 0)
    reserve = buffer_pct * capacity
    return {r: max(0.0, resources.get(r, 0.0) - reserve) for r in config.RESOURCE_KEYS}


def _lager_key(buildings: list) -> str | None:
    for b in buildings:
        haystack = f"{b.get('typ', '')} {b.get('name', '')}".lower()
        if any(kw in haystack for kw in config.WAREHOUSE_BUILDING_KEYWORDS):
            return b["typ"]
    return None


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
    next_hint = None
    while free_slots > 0 and acted < config.MAX_UPGRADES_PER_TICK:
        current = _usable_resources(resources, _day_buffer_pct())
        capacity = float(resources.get("kapazitaet", 0) or 0)
        levels = projected_levels(buildings, active_orders)
        candidates = collect_candidates(buildings, levels)

        now = time.time()
        queue_empty = len(active_orders) == 0
        if not queue_empty:
            LOW_PRIORITY_SINCE.pop(key, None)
        allow_low = (
            queue_empty and key in LOW_PRIORITY_SINCE
            and now - LOW_PRIORITY_SINCE[key] >= config.LOW_PRIORITY_DELAY_SECONDS
        )

        chosen = choose_upgrade(candidates, current, capacity, store, allow_low)

        if chosen is None:
            important_payable = any(
                affordable(c["cost"], current)
                for n, c in candidates.items() if n not in config.LOW_PRIORITY_BUILDINGS
            )
            if queue_empty and not important_payable:
                LOW_PRIORITY_SINCE.setdefault(key, now)
                remaining = max(0, config.LOW_PRIORITY_DELAY_SECONDS - (now - LOW_PRIORITY_SINCE[key]))
                next_hint = f"Wichtige Ausbauten aktuell nicht passend/bezahlbar; Steinmauer/Wachturm erlaubt in {int(remaining // 60)} Min."
            else:
                LOW_PRIORITY_SINCE.pop(key, None)
                next_hint = "Aktuell kein Ausbau nach den Prioritätsregeln bezahlbar."
            break

        LOW_PRIORITY_SINCE.pop(key, None)
        try:
            client.start_upgrade(island_id, chosen["building"])
        except ApiError as exc:
            if exc.code in ("queue_full", "construction_queue_full"):
                logger.info("Insel %s: Warteschlange laut API voll, breche ab.", island_id)
                break
            logger.error("Insel %s: Ausbau '%s' fehlgeschlagen: %s", island_id, chosen["building"], exc)
            next_hint = f"{chosen['building']} fehlgeschlagen: {exc}"
            break

        logger.info(
            "Insel %s: Ausbau '%s' gestartet (Kosten: %s)", island_id, chosen["building"], chosen["cost"]
        )
        acted += 1
        free_slots -= 1

        resources = client.get_island_resources(island_id)
        buildings = client.get_island_buildings(island_id)
        orders = client.get_construction_queue(island_id)
        active_orders = [
            o for o in orders if o.get("status", "").lower() not in config.FINISHED_CONSTRUCTION_STATUSES
        ]

    return resources, buildings, orders, next_hint


def _maybe_send_report(island_reports, store, logger):
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    now = datetime.now(ZoneInfo(config.TIMEZONE_NAME))
    if now.hour < config.REPORT_HOUR:
        return
    today_str = now.date().isoformat()
    if store.data.get("last_report_date") == today_str or not island_reports:
        return

    bodies = [
        report.build_report(resources=r, buildings=b, queue=o, next_hint=hint, log_path=config.LOG_FILE)
        for (r, b, o, hint) in island_reports.values()
    ]
    notifier = build_notifier({
        "service": "telegram",
        "bot_token": config.TELEGRAM_BOT_TOKEN,
        "chat_id": config.TELEGRAM_CHAT_ID,
    })
    ok = notifier.send("Seekampf Morgenreport", "\n\n---\n\n".join(bodies))
    if ok:
        store.data["last_report_date"] = today_str
        store.save()
        logger.info("Morgenreport gesendet.")
    else:
        logger.error("Morgenreport konnte nicht gesendet werden.")


def tick(client, store, logger):
    try:
        island_ids = client.get_my_island_ids()
    except (ApiError, requests.exceptions.RequestException) as exc:
        logger.error("Konnte Inselliste nicht laden: %s", exc)
        return

    island_reports = {}
    for island_id in island_ids:
        try:
            island_reports[island_id] = process_island(client, island_id, store, logger)
        except ApiError as exc:
            logger.error("API-Fehler auf Insel %s: %s", island_id, exc)
        except Exception:
            logger.exception("Unerwarteter Fehler auf Insel %s", island_id)

    _maybe_send_report(island_reports, store, logger)


def main():
    logger = get_logger()
    logger.info("Seekampf-Bot gestartet (Poll-Intervall: %ss, Planer: Prioritaets-Kaskade + gelernte Produktion)",
                config.POLL_INTERVAL_SECONDS)

    if not config.API_KEY:
        logger.error("SEEKAMPF_API_KEY ist nicht gesetzt (.env pruefen). Beende.")
        return

    os.makedirs(os.path.dirname(config.STORE_PATH), exist_ok=True)
    store = Store(config.STORE_PATH)
    client = SeekampfClient()

    while True:
        try:
            tick(client, store, logger)
        except Exception:
            # Letztes Sicherheitsnetz: der Dauerprozess soll nie durch einen
            # einzelnen fehlerhaften Tick sterben (z. B. Autostart laeuft nur
            # beim Login neu, kein automatischer Neustart bei Absturz).
            logger.exception("Unerwarteter Fehler im Tick, laeuft beim naechsten Intervall weiter")
        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
