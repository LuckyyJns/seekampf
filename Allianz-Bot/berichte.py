"""Verteidigungs-Kampfberichte: Grundlage fuer die Staerke-Schaetzung eines
Angreifers und fuer die Leihe-Rueckgabe (agenten.md 7.1.9).

Ein Bericht im Ordner combat mit payload.rolle == "verteidiger" ist ein
Angriff auf uns. Die Einzelheiten (Name des Angreifers, Angriffsstaerke je
Phase) liefert GET /battles/{id}.
"""
from __future__ import annotations

import logging
import math

import config
from api_client import ApiError

log = logging.getLogger("seekampf_allianz_bot")

MAX_BERICHTE = 300


def aktualisieren(k) -> None:
    cache = k.state.data["berichte"]
    neu = False
    for m in k.client.list_messages("combat", limit=100):
        p = m.get("payload") or {}
        bid = p.get("battle_id")
        if p.get("rolle") != "verteidiger" or not bid or str(bid) in cache:
            continue
        try:
            b = k.client.get_battle(bid)
        except ApiError as e:
            log.error("Kampfbericht %s nicht lesbar: %s", bid, e)
            continue
        phasen = {t.get("phase"): t for t in b.get("timeline") or []}
        eingesetzt = ((b.get("eingesetzt") or p.get("eingesetzt") or {}).get("verteidiger") or {})
        verluste = ((b.get("verluste") or p.get("verluste") or {}).get("verteidiger") or {})
        cache[str(bid)] = {
            "zeit": b.get("start_at") or m.get("created_at"),
            "angreifer": b.get("angreifer_name"),
            "insel": p.get("insel_koordinaten"),
            "sieg": bool(p.get("sieg")),
            "land_angriff": float((phasen.get("land") or {}).get("angriffsstaerke") or 0),
            "see_angriff": float((phasen.get("see") or {}).get("angriffsstaerke") or 0),
            "eingesetzt": eingesetzt.get("units") or {},
            "verluste": verluste.get("units") or {},
        }
        neu = True
        log.info("Verteidigungsbericht %s: Angreifer %s, Land-Angriff %.0f, %s", bid,
                 cache[str(bid)]["angreifer"], cache[str(bid)]["land_angriff"],
                 "gehalten" if cache[str(bid)]["sieg"] else "verloren")
    if len(cache) > MAX_BERICHTE:
        for bid in sorted(cache, key=lambda b: cache[b]["zeit"] or "")[:len(cache) - MAX_BERICHTE]:
            del cache[bid]
        neu = True
    if neu:
        k.state.speichern()


def schaetze_angriff(k, angreifer: list[str]) -> tuple[int, str]:
    """Geschaetzte Land-Angriffsstaerke aller Angreifer zusammen.

    Erst aus frueheren Berichten gegen denselben Spieler (hoechster Wert mal
    Sicherheitsfaktor), sonst aus seinen Punkten.
    """
    gesamt, quellen = 0.0, []
    for name in angreifer:
        frueher = [b["land_angriff"] for b in k.state.data["berichte"].values()
                   if b.get("angreifer") == name]
        if frueher:
            gesamt += max(frueher) * config.BERICHT_SICHERHEITSFAKTOR
            quellen.append(f"{name}: Bericht")
            continue
        try:
            punkte = float((k.client.lookup_user(name) or {}).get("punkte") or 0)
        except ApiError:
            punkte = 0.0
        gesamt += punkte * config.PUNKTE_ANGRIFFSFAKTOR
        quellen.append(f"{name}: {int(punkte)} Punkte")
    return int(math.ceil(gesamt)), ", ".join(quellen)


def mauer_faktor(ov: dict) -> float:
    stufe = int((ov.get("gebaeude") or {}).get("steinmauer") or 0)
    return 1 + config.MAUER_BONUS_JE_STUFE * stufe


def verteidigung(ov: dict) -> float:
    truppen = ov.get("truppen") or {}
    einheiten = sum(int(truppen.get(t, 0) or 0) * w for t, w in config.EINHEIT_VERTEIDIGUNG.items())
    return (einheiten + config.BASIS_INSELVERTEIDIGUNG) * mauer_faktor(ov)


def ueberlebensquote(k, einheit: str, von, bis, insel: str) -> float:
    """q_t aus 7.1.9: Produkt ueber alle Kaempfe auf der Insel `insel` zwischen
    Ankunft der Leihe und Entwarnung, jeweils Ueberlebende / Anwesende."""
    from kontext import zeit
    q = 1.0
    for b in k.state.data["berichte"].values():
        t = zeit(b.get("zeit"))
        if t is None or not (von <= t <= bis) or (b.get("insel") and b["insel"] != insel):
            continue
        anwesend = int(b["eingesetzt"].get(einheit, 0) or 0)
        if anwesend <= 0:
            continue
        verloren = int(b["verluste"].get(einheit, 0) or 0)
        q *= max(0, anwesend - verloren) / anwesend
    return q
