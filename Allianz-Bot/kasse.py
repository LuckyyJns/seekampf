"""Ueberlauf in die Allianzkasse - eigenstaendige Option, im Seekampf-Hub
unter Allianz-Bot an- und abschaltbar (steuerung.json, Abschnitt "kasse").

Steht auf irgendeiner eigenen Insel ein Rohstoff bei `KASSE_AB` (Anteil der
Lagerkapazitaet) oder darueber, wird er bis auf `KASSE_BIS` in die
Allianzkasse eingezahlt - egal warum er liegen geblieben ist (keine Insel
braucht ihn, keine Handelsschiffe, Ausgleich aus ...). So verpufft keine
Produktion an einem vollen Lager.

Vorrang haben die anderen Bots:
  - Was der Kolonisations-Bot auf der Insel fuer ein Schiff anspart, bleibt.
  - Haelt der Rohstoff-Ausgleich des Flotten-Managers gerade Handelsschiffe
    einer Insel fuer eine Lieferung zurueck, wird dort nichts eingezahlt -
    erst liefern, dann einzahlen.
Einzahlungen unter `KASSE_MIN_MENGE` unterbleiben.

POST /alliances/{id}/kasse/deposit zieht die Rohstoffe sofort von der Insel
ab, in der Kasse kommen sie nach der Transferzeit an. Schiffe braucht es nicht.
"""
from __future__ import annotations

import logging
import math
import time

from seekampf_gemeinsam import reservierungen

import config
from api_client import ApiError

log = logging.getLogger("seekampf_allianz_bot")

ROHSTOFFE = ("gold", "stein", "holz")

EINZAHLUNGEN_MERKEN = 40
FEHLER_PAUSE_S = 15 * 60


def leerer_stand() -> dict:
    return {"eingezahlt": {r: 0.0 for r in ROHSTOFFE}, "einzahlungen": []}


def kolo_reserve() -> dict:
    """Was andere Bots je Insel an Rohstoffen reservieren (gemeinsames
    Reservierungs-Verzeichnis, z. B. Kolonisationsschiff; Bot aus = nichts)."""
    return reservierungen.summe("rohstoffe", ordner=config.RESERVIERUNGEN_ORDNER)


def ausgleich_wartet() -> set[int]:
    """Inseln, deren Handelsschiffe der Rohstoff-Ausgleich gerade fuer eine
    Lieferung zurueckhaelt (Flotten-Manager aus = keine)."""
    return {int(iid) for iid in reservierungen.von("ausgleich", config.RESERVIERUNGEN_ORDNER)}


def einzahlung(res: dict, ab: float, bis: float, reserve: dict) -> dict:
    """Was von diesem Lagerstand eingezahlt wird (nur Rohstoffe ab `ab`)."""
    kap = float(res.get("kapazitaet") or 0)
    if kap <= 0:
        return {}
    aus = {}
    for r in ROHSTOFFE:
        bestand = float(res.get(r, 0) or 0)
        if bestand < kap * ab:
            continue
        menge = math.floor(bestand - max(kap * bis, float(reserve.get(r, 0) or 0)))
        if menge > 0:
            aus[r] = menge
    return aus


class Kasse:
    def __init__(self, k):
        self.k = k
        self.hinweis: str | None = None
        self._pause_bis: dict[int, float] = {}

    def stand(self) -> dict:
        stand = self.k.state.data.get("kasse")
        if not isinstance(stand, dict):
            stand = self.k.state.data["kasse"] = leerer_stand()
        return stand

    def tick(self) -> None:
        if not config.KASSE_AKTIV:
            self.hinweis = "ausgeschaltet"
            return
        k, jetzt = self.k, time.time()
        if not k.allianz_id:
            self.hinweis = "keine Allianz"
            return
        ab, mindestens = float(config.KASSE_AB), float(config.KASSE_MIN_MENGE)
        bis = min(float(config.KASSE_BIS), ab)
        reserve = kolo_reserve()
        kandidaten = [iid for iid, ov in k.inseln().items()
                      if self._pause_bis.get(iid, 0) <= jetzt
                      and sum(einzahlung(ov.get("rohstoffe") or {}, ab, bis,
                                         reserve.get(str(iid)) or {}).values()) >= mindestens]
        self.hinweis = None
        if not kandidaten:
            return
        wartet = ausgleich_wartet()
        for iid in kandidaten:
            if iid in wartet:
                self.hinweis = f"{k.name(iid)}: wartet erst auf die Lieferung des Rohstoff-Ausgleichs"
                continue
            ov = k.overview(iid, max_alter=0)   # frisch: vielleicht wurde gerade geliefert
            menge = einzahlung(ov.get("rohstoffe") or {}, ab, bis, reserve.get(str(iid)) or {})
            if sum(menge.values()) < mindestens:
                continue
            try:
                antwort = k.client.kasse_einzahlen(k.allianz_id, iid, menge) or {}
            except ApiError as e:
                self._pause_bis[iid] = jetzt + FEHLER_PAUSE_S
                self.hinweis = f"Einzahlung von {k.name(iid)} fehlgeschlagen: {e.message}"
                log.error("KASSE Einzahlung von %s fehlgeschlagen: %s", k.name(iid), e)
                continue
            k.bilanz.buchen(iid, "Allianzkasse", menge, -1)
            stand = self.stand()
            for r, n in menge.items():
                stand["eingezahlt"][r] = stand["eingezahlt"].get(r, 0.0) + n
            stand["einzahlungen"].insert(0, {"zeit": jetzt, "insel": iid, "name": k.name(iid),
                                             "rohstoffe": menge, "fertig_at": antwort.get("fertig_at")})
            del stand["einzahlungen"][EINZAHLUNGEN_MERKEN:]
            log.info("KASSE %s: %s in die Allianzkasse eingezahlt (Lager ab %.0f %%)", k.name(iid),
                     ", ".join(f"{n} {r}" for r, n in menge.items()), ab * 100)
        k.state.speichern()
