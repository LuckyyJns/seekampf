#!/usr/bin/env python3
"""Einmalig: die Kampfberichte vergangener Manager-Raids im Postfach ausblenden.

Der laufende Manager blendet neue Berichte selbst aus (Einstellung
"berichte_archivieren"). Dieses Skript holt den Altbestand nach, der vor dem
Einbau entstanden ist.

Zugeordnet wird ueber die Logs: jede "ANKUNFT Flotte #x bei KOORD"-Zeile ist
ein Angriff, den der Manager ausgeloest hat. Nur Berichte, die zeitlich (+-3
Minuten) und in den Koordinaten zu so einer Zeile passen, werden ausgeblendet.
Alles ohne Log-Treffer - also von Hand gestartete Angriffe - bleibt stehen,
ebenso Spionage- und Handelsberichte.

Ausblenden laesst sich ueber die API NICHT rueckgaengig machen. Deshalb:
  1. zuerst ohne Argument starten - das zeigt nur, was passieren wuerde
  2. erst dann mit --los

Die Berichte bleiben einzeln unter GET /messages/{id} lesbar, und eine
vollstaendige Kopie liegt vorher in data/postfach-sicherung-<datum>.json.
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from api_client import ApiError, SeekampfClient

TOLERANZ_S = 180
TZ = ZoneInfo(config.TIMEZONE_NAME)
ANKUNFT = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[INFO\] ANKUNFT\s+Flotte #(\d+) bei (\d+:\d+:\d+)"
)


def epoch(iso: str) -> float:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def lokal(iso: str) -> str:
    return datetime.datetime.fromtimestamp(epoch(iso), TZ).strftime("%d.%m.%Y %H:%M")


def manager_ankuenfte() -> dict[str, list[float]]:
    """Koordinate -> Zeitpunkte, zu denen eine Manager-Flotte dort ankam."""
    nach_koord: dict[str, list[float]] = {}
    muster = os.path.join(config.LOG_DIR, f"{config.LOG_FILE_PREFIX}-*.log")
    dateien = sorted(glob.glob(muster))
    if not dateien:
        sys.exit(f"Keine Logdateien unter {muster} - ohne Logs ist keine Zuordnung moeglich.")
    for pfad in dateien:
        with open(pfad, encoding="utf-8", errors="replace") as f:
            for zeile in f:
                m = ANKUNFT.match(zeile)
                if m:
                    dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    nach_koord.setdefault(m.group(3), []).append(
                        dt.replace(tzinfo=TZ).timestamp())
    print(f"{len(dateien)} Logdateien, "
          f"{sum(len(v) for v in nach_koord.values())} Ankuenfte auf {len(nach_koord)} Inseln")
    return nach_koord


def alle_kampfberichte(client: SeekampfClient) -> list[dict]:
    """Der Kampfordner, in Seiten zu 200 - GET /messages liefert nie mehr."""
    alle, offset = [], 0
    while True:
        teil = client._request("GET", "/messages", params={
            "folder": "combat", "limit": 200, "offset": offset})
        if not isinstance(teil, list) or not teil:
            break
        alle.extend(teil)
        if len(teil) < 200:
            break
        offset += 200
        time.sleep(0.2)
    return alle


def main() -> None:
    los = "--los" in sys.argv
    client = SeekampfClient()
    nach_koord = manager_ankuenfte()

    berichte = alle_kampfberichte(client)
    print(f"Kampfordner: {len(berichte)} sichtbare Nachrichten")

    sicherung = os.path.join(
        config.BASE_DIR, "data",
        f"postfach-sicherung-{datetime.date.today():%Y-%m-%d}.json")
    os.makedirs(os.path.dirname(sicherung), exist_ok=True)
    with open(sicherung, "w", encoding="utf-8") as f:
        json.dump(berichte, f, ensure_ascii=False)
    print(f"Sicherung: {sicherung}")

    kandidaten, bleibt = [], []
    for m in berichte:
        p = m.get("payload") or {}
        if p.get("rolle") != "angreifer" or p.get("typ") in ("spionage", "handel"):
            bleibt.append(m)
            continue
        t = epoch(m["created_at"])
        koord = p.get("insel_koordinaten")
        if any(abs(t - a) <= TOLERANZ_S for a in nach_koord.get(koord, ())):
            kandidaten.append(m)
        else:
            bleibt.append(m)

    print(f"\n  ausblenden : {len(kandidaten)} Berichte zu Manager-Raids")
    print(f"  behalten   : {len(bleibt)} (Handangriffe, Spionage, Handel)")

    hand = [m for m in bleibt if (m.get("payload") or {}).get("rolle") == "angreifer"]
    if hand:
        print(f"\n  Darunter {len(hand)} Angriffe ohne Log-Treffer - die bleiben stehen:")
        for m in sorted(hand, key=lambda x: x["created_at"]):
            p = m["payload"]
            print(f"    {lokal(m['created_at'])}  {p.get('insel_koordinaten'):<10} "
                  f"{(p.get('insel_name') or '')[:22]}")

    if not los:
        print("\nProbelauf - es wurde nichts veraendert.")
        print("Wenn die Aufteilung stimmt, noch einmal mit --los starten.")
        return

    print(f"\nBlende {len(kandidaten)} Berichte aus ...")
    ok, fehler = 0, []
    for n, m in enumerate(kandidaten, 1):
        try:
            client.archive_message(m["id"])
            ok += 1
        except ApiError as e:
            fehler.append((m["id"], str(e)))
        if n % 100 == 0 or n == len(kandidaten):
            print(f"  {n}/{len(kandidaten)} - {ok} ausgeblendet, {len(fehler)} Fehler", flush=True)
        time.sleep(0.08)

    print(f"\nFertig: {ok} ausgeblendet, {len(fehler)} Fehler")
    for mid, grund in fehler[:10]:
        print(f"  #{mid}: {grund}")


if __name__ == "__main__":
    main()
