"""Telegram-Morgenreport: wie viele Flotten liefen, wie viel kam rein.

Bewusst knapp gehalten - Details stehen im Log und auf der Weboberflaeche.
Der Report deckt den Zeitraum seit dem zuletzt gesendeten Report ab; dafuer
merkt sich state.json bei jedem Versand den Statistik-Stand ("report_basis").
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("seekampf_flotten_manager")
TZ = ZoneInfo(config.TIMEZONE_NAME)


def _zahl(wert: float) -> str:
    return f"{int(round(wert)):,}".replace(",", ".")


def _basis(state) -> dict:
    vorhanden = state.data.get("report_basis")
    if vorhanden:
        return vorhanden
    return {"raids": 0, "gold": 0.0, "stein": 0.0, "holz": 0.0,
            "inseln": 0, "zeit": state.data["stats"].get("seit") or time.time()}

def _schnappschuss(state) -> dict:
    stats = state.data["stats"]
    return {"raids": stats["raids"], "gold": stats["gold"], "stein": stats["stein"],
            "holz": stats["holz"], "inseln": len(stats["inseln_besucht"]), "zeit": time.time()}


def text(state) -> str:
    basis, jetzt = _basis(state), _schnappschuss(state)
    raids = jetzt["raids"] - basis["raids"]
    beute = {r: jetzt[r] - basis.get(r, 0.0) for r in config.RESOURCE_KEYS}
    summe = sum(beute.values())
    seit = datetime.fromtimestamp(basis["zeit"], TZ).strftime("%d.%m. %H:%M")

    zeilen = [f"Seit {seit} Uhr:",
              f"  Flotten losgeschickt: {raids}",
              f"  Beute: {_zahl(beute['gold'])} Gold, {_zahl(beute['stein'])} Stein, "
              f"{_zahl(beute['holz'])} Holz (zusammen {_zahl(summe)})"]

    stats = state.data["stats"]
    gesamt = stats["gold"] + stats["stein"] + stats["holz"]
    zeilen += ["", f"Gesamt: {stats['raids']} Raids auf {len(stats['inseln_besucht'])} Inseln, "
                   f"{_zahl(gesamt)} Rohstoffe"]
    return "\n".join(zeilen)


def wenn_faellig(state, notifier, jetzt: float) -> None:
    if notifier is None or not notifier.aktiv:
        return
    heute = datetime.fromtimestamp(jetzt, TZ)
    if state.data.get("letzter_report") is None:
        # Allererster Start: nur den Zeitpunkt merken. Sonst ginge sofort ein
        # Report ueber einen Zeitraum raus, in dem noch gar nichts passiert ist.
        state.data["letzter_report"] = heute.date().isoformat()
        state.data["report_basis"] = _schnappschuss(state)
        state.save()
        return
    if heute.date().isoformat() == state.data.get("letzter_report"):
        return
    if heute.hour < int(state.settings["report_stunde"]):
        return
    if notifier.send("Seekampf Flotten-Manager", text(state)):
        state.data["report_basis"] = _schnappschuss(state)
        state.data["letzter_report"] = heute.date().isoformat()
        state.save()
        log.info("Morgenreport an Telegram gesendet")
