"""Baut den taeglichen Morgenreport.

Der Zeitraum ist nicht mehr "seit 0 Uhr", sondern "seit dem letzten
gesendeten Report" (bot.py uebergibt since/until). Weil der Bot pro Tag eine
eigene Logdatei schreibt, werden dafuer alle Dateien im Zeitraum gelesen.
"""
from __future__ import annotations

import ast
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.TIMEZONE_NAME)

WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _fmt_day(dt: datetime, with_time: bool = False) -> str:
    stamp = dt.strftime("%d.%m. %H:%M") if with_time else dt.strftime("%d.%m.")
    return f"{WOCHENTAGE[dt.weekday()]} {stamp}"


LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (.*)$")
BUILT_RE = re.compile(r"^Insel (\S+): Ausbau '(\w+)' gestartet \(Kosten: (\{.*\})\)$")


def _log_files(log_dir: str, since: datetime, until: datetime) -> list[str]:
    """Alle Logdateien, die Zeilen aus dem Zeitraum enthalten koennen."""
    files = []
    day = since.date()
    while day <= until.date():
        path = os.path.join(log_dir, f"{config.LOG_FILE_PREFIX}-{day.isoformat()}.log")
        if os.path.exists(path):
            files.append(path)
        day += timedelta(days=1)
    return files


def _events(log_dir: str, since: datetime, until: datetime, island_id) -> tuple[list[tuple[str, dict]], list[str]]:
    """(gebaute Ausbauten dieser Insel, Fehlermeldungen) im Zeitraum."""
    built: list[tuple[str, dict]] = []
    problems: list[str] = []
    island = str(island_id)
    for path in _log_files(log_dir, since, until):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    m = LINE_RE.match(line.rstrip("\n"))
                    if not m:
                        continue  # z. B. Traceback-Zeilen
                    try:
                        stamp = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
                    except ValueError:
                        continue
                    if not (since <= stamp <= until):
                        continue
                    level, message = m.group(2), m.group(3)
                    hit = BUILT_RE.match(message)
                    if hit and hit.group(1) == island:
                        try:
                            kosten = ast.literal_eval(hit.group(3))
                        except (ValueError, SyntaxError):
                            kosten = {}
                        built.append((hit.group(2), kosten))
                    elif level == "ERROR":
                        problems.append(message[:200])
        except OSError:
            continue
    return built, problems


def _fmt_hours(seconds: float) -> str:
    if seconds <= 0:
        return "0min"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}min" if h else f"{m}min"


def build_report(*, island_id, island_name: str, resources: dict, queue: list,
                 next_hint: str | None, since: datetime, until: datetime, log_dir: str) -> str:
    built, problems = _events(log_dir, since, until, island_id)
    window = _fmt_hours((until - since).total_seconds())

    lines = [
        f"Seekampf – {_fmt_day(until)} · {len(built)} Ausbauten",
        f"Zeitraum: seit {_fmt_day(since, with_time=True)} ({window})",
        "",
    ]

    if built:
        lines.append(f"Gebaut ({len(built)})")
        for typ, kosten in built:
            lines.append(
                f"- {typ}   ({kosten.get('gold', 0):.0f} G / {kosten.get('stein', 0):.0f} S / "
                f"{kosten.get('holz', 0):.0f} H)"
            )
        lines.append("")

    prod = resources.get("produktion_pro_h", {})
    cap = resources.get("kapazitaet", 0) or 1
    lines.append("Wirtschaft")
    lines.append(
        f"- {island_name}: {prod.get('gold', 0):.0f}/{prod.get('stein', 0):.0f}/{prod.get('holz', 0):.0f} G/S/H pro h"
    )
    lines.append(
        f"  Lager {resources.get('gold', 0):.0f}/{resources.get('stein', 0):.0f}/{resources.get('holz', 0):.0f} "
        f"von {cap:.0f}"
    )
    for name, key in (("Gold", "gold"), ("Stein", "stein"), ("Holz", "holz")):
        amount, rate = resources.get(key, 0), prod.get(key, 0)
        if rate > 0 and cap > 0:
            room = cap - amount
            if room / rate * 3600 < 24 * 3600:
                lines.append(f"  ⚠️ {name}-Lager voll in {_fmt_hours(room / rate * 3600)}")
    lines.append("")

    active = [o for o in queue if o.get("status") == "aktiv"]
    waiting = [o for o in queue if o.get("status") == "wartend"]
    if active:
        lines.append("Läuft gerade")
        for o in active:
            lines.append(f"- {island_name}: {o.get('building_typ')} → {o.get('ziel_stufe')}")
        lines.append("")
    if waiting:
        lines.append(f"In der Warteschlange ({len(waiting)})")
        for o in waiting:
            lines.append(f"- {island_name}: {o.get('building_typ')} → {o.get('ziel_stufe')}")
        lines.append("")

    if next_hint:
        lines.append("Priorisierung")
        lines.append(f"- {next_hint}")
        lines.append("")

    if problems:
        lines.append(f"Probleme ({len(problems)})")
        for p in problems[:10]:
            lines.append(f"- {p}")

    return "\n".join(lines).strip()
