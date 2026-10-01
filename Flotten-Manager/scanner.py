"""Sucht im eingestellten Bereich alle freien Inseln und schreibt sie in die
Zielliste.

Frei heisst: die Insel existiert, hat aber keinen Besitzer (besitzer == null).
Das sind die frisch angelegten unbewohnten Inseln und die Ruinen verlassener
oder eroberter Spieler. Alles mit einem Besitzernamen bleibt aussen vor -
auch die "Unbemannt NNN"-Inseln, die trotz ihres Namens dem Spieler Niemend
gehoeren und damit ein echter Spielerangriff waeren.

Gescannt wird ueber /map/region, das 3x3 Sektoren in EINER Antwort liefert -
ein Radius von 1 kostet also genau einen Request, Radius 2 vier.
"""
from __future__ import annotations

import logging
import time

import geo

log = logging.getLogger("seekampf_flotten_manager")


def _region_zentren(mitte: int, radius: int) -> list[int]:
    """Welche Region-Mittelpunkte decken mitte-radius .. mitte+radius ab?

    Jede Region deckt drei Sektoren ab (Mitte +/- 1), also reicht ein
    Mittelpunkt alle drei Sektoren.
    """
    lo, hi = mitte - radius, mitte + radius
    zentren, c = [], lo + 1
    while c - 1 <= hi:
        zentren.append(c)
        c += 3
    return zentren


def _inseln_aus_region(antwort) -> list[dict]:
    """Alle Insel-Datensaetze aus einer /map/region-Antwort einsammeln.

    Absichtlich rekursiv statt fest auf sektoren[zeile][spalte]["inseln"]:
    aendert das Spiel die Schachtelung, findet der Scanner die Inseln
    trotzdem, statt still eine leere Liste zu liefern.
    """
    gefunden: list[dict] = []

    def lauf(knoten):
        if isinstance(knoten, dict):
            if "koordinaten" in knoten and "z" in knoten:
                gefunden.append(knoten)
            else:
                for wert in knoten.values():
                    lauf(wert)
        elif isinstance(knoten, list):
            for wert in knoten:
                lauf(wert)

    lauf(antwort)
    return gefunden


def ist_frei(insel: dict) -> bool:
    return "besitzer" in insel and insel.get("besitzer") is None


def scan(client, state, insel_id, heimat: tuple[int, int, int], flotte_ships: dict) -> dict:
    """Bereich um eine eigene Insel abklappern, deren Zielliste aktualisieren,
    Zusammenfassung zurueckgeben.

    Bestehende Ziele behalten ihre Statistik und ihre Blacklist-Sperre; neu
    dazugekommene Inseln werden hinten an die Rotation angehaengt, damit die
    laufende Runde nicht durcheinandergeraet. Inseln, die inzwischen einen
    Besitzer haben oder aus dem Bereich gefallen sind, fliegen raus.
    """
    hx, hy, _ = heimat
    radius = max(0, int(state.settings["scan_radius_sektoren"]))
    knoten = geo.flotten_knoten(flotte_ships) or 10.0

    gefundene: dict[str, dict] = {}
    requests_gesamt = 0
    for cx in _region_zentren(hx, radius):
        for cy in _region_zentren(hy, radius):
            antwort = client.get_region(cx, cy)
            requests_gesamt += 1
            for insel in _inseln_aus_region(antwort):
                x, y, z = insel["x"], insel["y"], insel["z"]
                if abs(x - hx) > radius or abs(y - hy) > radius:
                    continue  # die Region ragt ueber den eingestellten Bereich hinaus
                if not ist_frei(insel):
                    continue
                gefundene[insel["koordinaten"]] = insel

    with state.lock:
        eigene = state.insel(insel_id)
        alt = eigene["ziele"]
        neu_dazu, entfernt = [], []

        for koord, insel in gefundene.items():
            if koord in alt:
                alt[koord]["name"] = insel.get("name") or alt[koord]["name"]
                continue
            x, y, z = insel["x"], insel["y"], insel["z"]
            distanz = geo.distanz_felder(heimat, (x, y, z))
            ziel = state.neues_ziel(
                koord, x, y, z, insel.get("name") or "Unbewohnte Insel",
                distanz, geo.fahrzeit_s(heimat, (x, y, z), knoten),
            )
            alt[koord] = ziel
            neu_dazu.append(koord)

        for koord in list(alt):
            if koord not in gefundene:
                del alt[koord]
                entfernt.append(koord)

        # Fahrzeiten neu rechnen: die Flottenzusammensetzung kann sich seit dem
        # letzten Scan geaendert haben und damit die Geschwindigkeit.
        for koord, ziel in alt.items():
            ziel["fahrzeit_s"] = geo.fahrzeit_s(heimat, (ziel["x"], ziel["y"], ziel["z"]), knoten)

        # Rotation: bekannte Reihenfolge behalten, Neues nach Entfernung
        # sortiert hinten anhaengen. So bleibt "stur reihum" stur.
        rotation = [k for k in eigene["rotation"] if k in alt]
        rotation += sorted(neu_dazu, key=lambda k: alt[k]["distanz"])
        if not eigene["rotation"]:
            rotation = sorted(alt, key=lambda k: alt[k]["distanz"])
        eigene["rotation"] = rotation
        if eigene["rotation_index"] >= len(rotation):
            eigene["rotation_index"] = 0
        eigene["letzter_scan"] = time.time()

    state.save()
    log.info(
        "Scan %s: Radius %d um %d:%d: %d freie Inseln (%d neu, %d weggefallen, %d Requests)",
        eigene.get("name") or insel_id, radius, hx, hy, len(gefundene), len(neu_dazu), len(entfernt),
        requests_gesamt,
    )
    return {"gefunden": len(gefundene), "neu": neu_dazu, "entfernt": entfernt,
            "requests": requests_gesamt, "radius": radius}
