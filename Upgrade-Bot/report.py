"""Baut den taeglichen Morgenreport - eine kurze Nachricht fuer alle Inseln.

Je Insel: alle Ausbauten seit dem letzten Report in einer Zeile (Gebaeude
mit den erreichten Stufen), was gerade baut, und Warnungen nur, wenn wirklich
etwas anliegt. Der Zeitraum ist "seit dem letzten gesendeten Report" (bot.py
uebergibt since/until); dafuer werden alle Tageslogs im Zeitraum gelesen.
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
# Statuszeile je Insel und Durchlauf: "... | gestartet: goldmine → 5 (+16/h, Tabelle), kaserne → 4 | ..."
STATUS_RE = re.compile(r"^Insel (\S+) \|.*?\| gestartet: (.*?)(?: \| kein Ausbau:.*)?$")
STUFE_RE = re.compile(r"(\w+) → (\d+)")
HAND_RE = re.compile(r"^Insel (\S+): Ausbau '(\w+)' von Hand gestartet")
NAMEN = {"haupthaus": "Haupthaus", "goldmine": "Goldmine", "steingrube": "Steingrube",
         "saegewerk": "Sägewerk", "lagerhaus": "Lagerhaus", "hafen": "Hafen", "kaserne": "Kaserne",
         "steinmauer": "Steinmauer", "wachturm": "Wachturm"}
ROHSTOFF_NAMEN = {"gold": "Gold", "stein": "Stein", "holz": "Holz"}
# Warnung "Lager bald voll" nur, wenn es wirklich bald soweit ist.
LAGER_WARNUNG_H = 6


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


def _events(log_dir: str, since: datetime, until: datetime):
    """Gebaute Ausbauten je Insel [(gebaeude, stufe|None)] und Fehler im Zeitraum."""
    built: dict[str, list[tuple[str, int | None]]] = {}
    problems: list[str] = []
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
                    if (hit := STATUS_RE.match(message)):
                        built.setdefault(hit.group(1), []).extend(
                            (typ, int(stufe)) for typ, stufe in STUFE_RE.findall(hit.group(2)))
                    elif (hit := HAND_RE.match(message)):
                        built.setdefault(hit.group(1), []).append((hit.group(2), None))
                    elif level == "ERROR":
                        problems.append(message)
        except OSError:
            continue
    return built, problems


def _fmt_hours(seconds: float) -> str:
    if seconds <= 0:
        return "0min"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}min" if h else f"{m}min"


def _ausbauten_zeile(liste: list[tuple[str, int | None]]) -> str:
    """goldmine 5, kaserne 4, goldmine 6 -> "Goldmine 5/6 · Kaserne 4"."""
    stufen: dict[str, list[str]] = {}
    for typ, stufe in liste:
        stufen.setdefault(typ, []).append(str(stufe) if stufe is not None else "Hand")
    return " · ".join(f"{NAMEN.get(t, t)} {'/'.join(s)}" for t, s in stufen.items())


def _uhr(value: str | None, now: datetime) -> str:
    if not value:
        return "?"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return "?"
    return dt.strftime("%H:%M") if dt.date() == now.date() else dt.strftime("%d.%m. %H:%M")


def _fehler_kurz(problems: list[str]) -> list[str]:
    """Gleiche Fehler zusammenfassen (Zahlen/IDs zaehlen nicht), die haeufigsten zuerst."""
    gruppen: dict[str, list[str]] = {}
    for p in problems:
        gruppen.setdefault(re.sub(r"\d+", "#", p)[:70], []).append(p)
    return [f"{len(v)}× {v[0][:90]}" for v in sorted(gruppen.values(), key=len, reverse=True)[:3]]


def build_summary(island_reports: dict, *, since: datetime, until: datetime, log_dir: str) -> str:
    """island_reports: {insel_id: (name, rohstoffe, warteschlange, hinweis)}."""
    built, problems = _events(log_dir, since, until)
    gesamt = sum(len(built.get(str(i), [])) for i in island_reports)
    lines = [
        f"**Seekampf · {_fmt_day(until)}** – seit {_fmt_day(since, with_time=True)}",
        f"{gesamt} Ausbauten auf {len(island_reports)} Inseln",
    ]
    for island_id, (name, resources, queue, _hint) in island_reports.items():
        liste = built.get(str(island_id), [])
        lines.append("")
        lines.append(f"**{name}** · {len(liste)}")
        if liste:
            lines.append(_ausbauten_zeile(liste))

        aktiv = sorted((o for o in queue if o.get("status") == "aktiv"), key=lambda o: o.get("finish_at") or "")
        wartend = [o for o in queue if o.get("status") == "wartend"]
        if aktiv:
            o = aktiv[0]
            extra = f" (+{len(aktiv) - 1 + len(wartend)} danach)" if len(aktiv) - 1 + len(wartend) else ""
            lines.append(f"⏳ {NAMEN.get(o.get('building_typ'), o.get('building_typ'))} {o.get('ziel_stufe')} "
                         f"bis {_uhr(o.get('finish_at'), until)}{extra}")
        elif not wartend:
            lines.append("⏳ Warteschlange leer")

        prod = resources.get("produktion_pro_h") or {}
        cap = float(resources.get("kapazitaet", 0) or 0)
        voll = []
        for key, rname in ROHSTOFF_NAMEN.items():
            rate = float(prod.get(key, 0) or 0)
            if rate > 0 and cap > 0:
                rest_h = (cap - float(resources.get(key, 0) or 0)) / rate
                if rest_h < LAGER_WARNUNG_H:
                    voll.append(f"{rname} in {_fmt_hours(max(0.0, rest_h) * 3600)}")
        if voll:
            lines.append("⚠️ Lager voll: " + ", ".join(voll))

    if problems:
        lines.append("")
        lines.append(f"⚠️ {len(problems)} Fehler:")
        lines.extend(f"- {p}" for p in _fehler_kurz(problems))
    return "\n".join(lines).strip()
