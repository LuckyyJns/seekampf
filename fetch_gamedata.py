"""Holt die Gebaeudetabellen aus den offiziellen Spielregeln.

https://seekampf.de/legal/regeln listet fuer jedes Gebaeude alle Stufen mit
Kosten, Bauzeit und - bei den Rohstoffgebaeuden - der Produktion pro Stunde.
Die API liefert dagegen immer nur die naechste Stufe, weshalb der Planer den
Ertrag eines Ausbaus vorher nicht kannte.

Das Ergebnis landet in data/gamedata.json und wird von gamedata.py gelesen.
Erneut ausfuehren, wenn das Spiel die Werte aendert:

    .venv/bin/python fetch_gamedata.py
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.request

RULES_URL = "https://seekampf.de/legal/regeln"
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gamedata.json")

# Ueberschrift auf der Regelseite -> typ, wie ihn die API in "typ" liefert
BUILDING_NAMES = {
    "haupthaus": "haupthaus", "goldmine": "goldmine", "steingrube": "steingrube",
    "sägewerk": "saegewerk", "saegewerk": "saegewerk", "lagerhaus": "lagerhaus",
    "steinmauer": "steinmauer", "wachturm": "wachturm", "kaserne": "kaserne",
    "hafen": "hafen",
}

COLUMNS = {
    "gold": "gold", "stein": "stein", "holz": "holz",
    "bauzeit": "bauzeit", "produktion/h": "produktion_pro_h",
    "lagerkapazität je rohstoff": "kapazitaet",
    "plünderschutz je rohstoff": "pluenderschutz",
}


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _cells(row: str) -> list[str]:
    return [_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)]


def _seconds(value: str) -> int | None:
    parts = value.split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def _number(value: str) -> float | None:
    cleaned = value.replace(".", "").replace(",", ".").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse(page: str) -> dict:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    result: dict[str, dict] = {}

    for match in re.finditer(r"<table.*?</table>", page, flags=re.S):
        rows = [r for r in (_cells(r) for r in re.findall(r"<tr.*?</tr>", match.group(0), flags=re.S)) if r]
        if not rows or rows[0][0].lower() != "stufe":
            continue  # Einheiten-/Schiffstabellen haben keine Stufenspalte

        # Jede Gebaeudetabelle steht unter ihrer eigenen <h3>-Ueberschrift;
        # massgeblich ist die letzte Ueberschrift vor der Tabelle.
        headings = re.findall(r"<h[1-5][^>]*>(.*?)</h[1-5]>", page[:match.start()], flags=re.S)
        name = BUILDING_NAMES.get(_text(headings[-1]).lower()) if headings else None
        if name is None:
            continue

        header = [COLUMNS.get(h.lower()) for h in rows[0]]
        levels: dict[str, dict] = {}
        for row in rows[1:]:
            if not row or not row[0].isdigit():
                continue
            entry: dict[str, float | int] = {}
            for key, value in zip(header[1:], row[1:]):
                if key is None:
                    continue
                entry[key] = _seconds(value) if key == "bauzeit" else _number(value)
            if "bauzeit" in entry:
                entry["bauzeit_s"] = entry.pop("bauzeit")
            levels[row[0]] = entry
        if levels:
            result[name] = levels
    return result


def main() -> int:
    request = urllib.request.Request(RULES_URL, headers={"User-Agent": "seekampf-bot/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode("utf-8", "replace")

    data = parse(page)
    missing = sorted(set(BUILDING_NAMES.values()) - set(data))
    if missing:
        print(f"Nicht gefunden: {', '.join(missing)} - Seitenaufbau geaendert?", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"quelle": RULES_URL, "gebaeude": data}, f, ensure_ascii=False, indent=1)

    for name, levels in sorted(data.items()):
        print(f"  {name:11} Stufen {min(levels, key=int)}-{max(levels, key=int)}  "
              f"Felder: {', '.join(sorted(next(iter(levels.values()))))}")
    print(f"\nGeschrieben: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
