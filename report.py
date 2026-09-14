"""Baut den taeglichen Morgenreport (Format angelehnt an die urspruengliche
README-Vorlage, aber auf unsere echte Insel/Ressourcen zugeschnitten)."""
from __future__ import annotations

import ast
import re
from datetime import date, datetime

BUILT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) [\d:]+ \[INFO\] Insel (\S+): Ausbau '(\w+)' gestartet \(Kosten: (\{.*\})\)$"
)
ERROR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) [\d:]+ \[(ERROR|WARNING)\] (.*)$")


def _today_events(log_path: str, today: date) -> tuple[list[tuple[str, dict]], list[str]]:
    built: list[tuple[str, dict]] = []
    problems: list[str] = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                m = BUILT_RE.match(line.rstrip("\n"))
                if m and date.fromisoformat(m.group(1)) == today:
                    try:
                        kosten = ast.literal_eval(m.group(4))
                    except (ValueError, SyntaxError):
                        kosten = {}
                    built.append((m.group(3), kosten))
                    continue
                m = ERROR_RE.match(line.rstrip("\n"))
                if m and date.fromisoformat(m.group(1)) == today and m.group(2) == "ERROR":
                    problems.append(m.group(3)[:200])
    except OSError:
        pass
    return built, problems


def _fmt_hours(seconds: float) -> str:
    if seconds <= 0:
        return "0min"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}min" if h else f"{m}min"


def build_report(*, resources: dict, buildings: list, queue: list, next_hint: str | None, log_path: str) -> str:
    today = datetime.now().date()
    built, problems = _today_events(log_path, today)

    lines = [f"Seekampf – {today.strftime('%a %d.%m.')} · {len(built)} Ausbauten", ""]

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
        f"- Heiminsel: {prod.get('gold', 0):.0f}/{prod.get('stein', 0):.0f}/{prod.get('holz', 0):.0f} G/S/H pro h"
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
            lines.append(f"- Heiminsel: {o.get('building_typ')} → {o.get('ziel_stufe')}")
        lines.append("")
    if waiting:
        lines.append(f"In der Warteschlange ({len(waiting)})")
        for o in waiting:
            lines.append(f"- Heiminsel: {o.get('building_typ')} → {o.get('ziel_stufe')}")
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
