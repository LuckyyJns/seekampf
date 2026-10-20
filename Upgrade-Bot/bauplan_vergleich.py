"""Vergleich: Prioritaets-Kaskade gegen Such-Plan, fuer alle eigenen Inseln.

Nur messen - steuert nichts, schreibt nichts ins Spiel. Liest den Stand der
Inseln aus data/status.json (schreibt der laufende Upgrade-Bot) und rechnet mit
bauplan_sim.py beide Planer bis zum Horizont durch.

    .venv/bin/python bauplan_vergleich.py                 # 7 Tage, beide Ziele
    .venv/bin/python bauplan_vergleich.py --tage 3 --breite 20
    .venv/bin/python bauplan_vergleich.py --insel 718 --plan   # Bau-Reihenfolge zeigen
    .venv/bin/python bauplan_vergleich.py --json data/bauplan_vergleich.json
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import bauplan_sim as sim

KURZ = {"haupthaus": "Haupt", "goldmine": "Gold", "steingrube": "Stein", "saegewerk": "Saege", "lagerhaus": "Lager",
        "steinmauer": "Mauer", "wachturm": "Turm", "kaserne": "Kas", "hafen": "Hafen"}

STATUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "status.json")


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def startzustand(eintrag: dict, jetzt: float, horizont: float, extern: dict | None = None) -> sim.Insel:
    """Insel aus status.json; laufende/wartende Auftraege werden vorweg abgearbeitet
    (fuer beide Planer gleich)."""
    stufen = {b["typ"]: int(b.get("stufe") or 0) for b in eintrag.get("gebaeude") or []}
    stand = float(eintrag.get("zeit") or jetzt)
    auftraege = [(o.get("gebaeude"), int(o.get("ziel_stufe") or 0), o.get("status"),
                  (_epoch(o.get("finish_at")) or stand) - stand) for o in eintrag.get("warteschlange") or []]
    return sim.aus_auftraegen(stufen, eintrag.get("rohstoffe") or {}, auftraege, extern, horizont)


def vergleichen(eintrag: dict, horizont: float, breite: int, jetzt: float, extern: dict | None = None) -> dict:
    gesperrt = set((eintrag.get("einstellung") or {}).get("gesperrt") or [])
    start = startzustand(eintrag, jetzt, horizont, extern)
    out = {"id": eintrag.get("id"), "name": eintrag.get("name"), "gesperrt": sorted(gesperrt),
           "extern_h": {r: round(v) for r, v in start.extern.items()}}
    t0 = time.process_time()
    out["kette"] = sim.kennzahlen(sim.kette(start, horizont, gesperrt))
    out["kette_s"] = round(time.process_time() - t0, 2)
    for ziel in ("wirtschaft", "punkte"):
        t0 = time.process_time()
        out[ziel] = sim.kennzahlen(sim.suchen(start, horizont, ziel, breite, gesperrt))
        out[ziel + "_s"] = round(time.process_time() - t0, 2)
    return out


def _zeile(name, k, ref=None) -> str:
    def d(key):
        if ref is None:
            return ""
        diff = k[key] - ref[key]
        return f" ({diff:+.0f})" if diff else ""
    kolo = "-" if k["kolo_bereit_h"] is None else f"{k['kolo_bereit_h']:.0f} h"
    return (f"  {name:<11} Punkte {k['punkte']:>6.1f}{d('punkte'):<8} Produktion {k['produktion_h']:>5}/h{d('produktion_h'):<8}"
            f" erzeugt {k['erzeugt']:>8}{d('erzeugt'):<9} verfallen {k['verfallen']:>7}  Ausbauten {k['ausbauten']:>3}"
            f"  Kolo-bereit {kolo}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tage", type=float, default=7.0)
    ap.add_argument("--breite", type=int, default=30)
    ap.add_argument("--insel", type=int, action="append")
    ap.add_argument("--plan", action="store_true", help="Bau-Reihenfolgen ausgeben")
    ap.add_argument("--json", help="Ergebnis zusaetzlich als JSON speichern")
    ap.add_argument("--ohne-extern", action="store_true",
                    help="nur Minenproduktion, ohne Raids/Ausgleich/Ausbildung aus der Rohstoff-Bilanz")
    args = ap.parse_args()

    with open(STATUS, encoding="utf-8") as f:
        inseln = json.load(f).get("inseln") or {}
    horizont = args.tage * 86400
    jetzt = time.time()
    extern = {} if args.ohne_extern else sim.extern_je_stunde()
    ergebnisse, summe = [], {"kette": {}, "wirtschaft": {}, "punkte": {}}
    gesamt_s = 0.0
    for eintrag in sorted(inseln.values(), key=lambda e: e.get("position") or 0):
        if args.insel and int(eintrag["id"]) not in args.insel:
            continue
        if not eintrag.get("gebaeude"):
            continue
        e = vergleichen(eintrag, horizont, args.breite, jetzt, extern.get(str(eintrag["id"])))
        if e["kette"]["ausbauten"] == 0 and e["wirtschaft"]["ausbauten"] == 0:
            continue   # nichts mehr zu bauen
        ergebnisse.append(e)
        gesamt_s += e["kette_s"] + e["wirtschaft_s"] + e["punkte_s"]
        print(f"\n{e['name']} (#{e['id']})" + (f" - gesperrt: {', '.join(e['gesperrt'])}" if e["gesperrt"] else "")
              + f"   extern/h {'/'.join(f'{v:+d}' for v in e['extern_h'].values())}"
              + f"   Rechenzeit Suche {e['wirtschaft_s']:.1f} s + {e['punkte_s']:.1f} s")
        print(_zeile("Kaskade", e["kette"]))
        print(_zeile("Suche Wirt.", e["wirtschaft"], e["kette"]))
        print(_zeile("Suche Pkt.", e["punkte"], e["kette"]))
        if args.plan:
            for art in ("kette", "wirtschaft", "punkte"):
                print(f"    {art}: " + ", ".join(f"{KURZ.get(g, g)}{st}@{h:.0f}h" for h, g, st in e[art]["plan"]))
        for art in summe:
            for k in ("punkte", "produktion_h", "erzeugt", "verfallen", "ausbauten"):
                summe[art][k] = summe[art].get(k, 0) + e[art][k]

    if not ergebnisse:
        print("Keine Insel mit offenen Ausbauten.")
        return
    print(f"\nSUMME ueber {len(ergebnisse)} Inseln, Horizont {args.tage:g} Tage, Breite {args.breite}, "
          f"Rechenzeit gesamt {gesamt_s:.0f} s")
    for art, titel in (("kette", "Kaskade"), ("wirtschaft", "Suche Wirt."), ("punkte", "Suche Pkt.")):
        s = summe[art]
        ref = summe["kette"]
        rel = lambda k: f" ({(s[k] / ref[k] - 1) * 100:+.1f} %)" if art != "kette" and ref[k] else ""
        print(f"  {titel:<11} Punkte {s['punkte']:>8.1f}{rel('punkte'):<11} Produktion {s['produktion_h']:>6}/h"
              f"{rel('produktion_h'):<11} erzeugt {s['erzeugt']:>9}{rel('erzeugt'):<11} verfallen {s['verfallen']:>8}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"zeit": jetzt, "tage": args.tage, "breite": args.breite, "inseln": ergebnisse}, f,
                      ensure_ascii=False)


if __name__ == "__main__":
    main()
