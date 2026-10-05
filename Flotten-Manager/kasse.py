"""Ueberlauf in die Allianzkasse.

Laeuft im Takt direkt nach dem Rohstoff-Ausgleich - was der verteilen
konnte, ist also schon weg. Steht danach auf irgendeiner eigenen Insel ein
Rohstoff bei `kasse_ab` (Anteil der Lagerkapazitaet) oder darueber, wird er
bis auf `kasse_bis` in die Allianzkasse eingezahlt - egal warum er liegen
geblieben ist (keine Insel braucht ihn, keine Handelsschiffe, Ausgleich aus,
Insel gibt nicht ab ...). So verpufft keine Produktion an einem vollen Lager.

Nie eingezahlt wird, was der Kolonisations-Bot auf der Insel fuer ein Schiff
anspart. Einzahlungen unter `kasse_min_menge` unterbleiben.

Technisch POST /alliances/{id}/kasse/deposit: das Spiel zieht die Rohstoffe
sofort von der Insel ab, in der Kasse kommen sie nach der Transferzeit an.
Schiffe braucht es dafuer nicht.
"""
from __future__ import annotations

import logging
import math

import config
from api_client import ApiError
from ausgleich import reserve_lesen

log = logging.getLogger("seekampf_flotten_manager")

EINZAHLUNGEN_MERKEN = 40
# Nach einem Fehler diese Insel so lange in Ruhe lassen.
FEHLER_PAUSE_S = 15 * 60
# Allianz-ID so lange merken, bevor sie neu gelesen wird.
ALLIANZ_MAX_ALTER_S = 3600


def leerer_stand() -> dict:
    return {"eingezahlt": {r: 0.0 for r in config.RESOURCE_KEYS}, "einzahlungen": []}


class Kasse:
    def __init__(self, manager):
        self.m = manager
        self.hinweis: str | None = None
        self._allianz: tuple[int | None, float] = (None, 0.0)
        self._pause_bis: dict[int, float] = {}

    def stand(self) -> dict:
        stand = self.m.state.data.get("kasse")
        if not isinstance(stand, dict):
            stand = self.m.state.data["kasse"] = leerer_stand()
        return stand

    def allianz_id(self, jetzt: float) -> int | None:
        aid, zeit = self._allianz
        if aid is None or jetzt - zeit > ALLIANZ_MAX_ALTER_S:
            aid = ((self.m.client.get_me() or {}).get("allianz") or {}).get("id")
            self._allianz = (aid, jetzt)
        return aid

    @staticmethod
    def einzahlung(res: dict, ab: float, bis: float, kolo: dict) -> dict:
        """Was von diesem Lagerstand eingezahlt wird (nur Rohstoffe ueber `ab`)."""
        kap = float(res.get("kapazitaet") or 0)
        if kap <= 0:
            return {}
        aus = {}
        for r in config.RESOURCE_KEYS:
            bestand = float(res.get(r, 0) or 0)
            if bestand < kap * ab:
                continue
            behalten = max(kap * bis, float(kolo.get(r, 0) or 0))
            menge = math.floor(bestand - behalten)
            if menge > 0:
                aus[r] = menge
        return aus

    def tick(self, alles: dict[int, dict], jetzt: float) -> None:
        s = self.m.settings
        if not s["kasse_aktiv"]:
            self.hinweis = "ausgeschaltet"
            return
        ab, mindestens = float(s["kasse_ab"]), float(s["kasse_min_menge"])
        bis = min(float(s["kasse_bis"]), ab)
        kolo = reserve_lesen(config.KOLO_RESERVE_PATH, "inseln")
        faellig = [iid for iid, ov in alles.items()
                   if self._pause_bis.get(iid, 0) <= jetzt
                   and sum(self.einzahlung(ov.get("rohstoffe") or {}, ab, bis,
                                           kolo.get(str(iid)) or {}).values()) >= mindestens]
        if not faellig:
            self.hinweis = None
            return
        aid = self.allianz_id(jetzt)
        if aid is None:
            self.hinweis = "keine Allianz - nichts eingezahlt"
            return
        for iid in faellig:
            # Frisch lesen: der Ausgleich hat in diesem Takt vielleicht schon geliefert.
            ov = self.m.insel_ov(iid, max_alter=0)
            menge = self.einzahlung(ov.get("rohstoffe") or {}, ab, bis, kolo.get(str(iid)) or {})
            if sum(menge.values()) < mindestens:
                continue
            try:
                antwort = self.m.client.kasse_einzahlen(aid, iid, menge) or {}
            except ApiError as e:
                self._pause_bis[iid] = jetzt + FEHLER_PAUSE_S
                self.hinweis = f"Einzahlung von {ov.get('name')} fehlgeschlagen: {e.message}"
                log.error("KASSE      Einzahlung von %s fehlgeschlagen: %s", ov.get("name"), e)
                continue
            stand = self.stand()
            for r, n in menge.items():
                stand["eingezahlt"][r] = stand["eingezahlt"].get(r, 0.0) + n
            stand["einzahlungen"].insert(0, {"zeit": jetzt, "insel": iid, "name": ov.get("name"),
                                             "rohstoffe": menge, "fertig_at": antwort.get("fertig_at")})
            del stand["einzahlungen"][EINZAHLUNGEN_MERKEN:]
            self.m._dirty = True
            self.hinweis = None
            log.info("KASSE      %s: %s in die Allianzkasse eingezahlt (Lager ueber %.0f %%)",
                     ov.get("name"), ", ".join(f"{n} {r}" for r, n in menge.items()), ab * 100)
        self.m._overview_zeit = 0  # Lagerstand hat sich geaendert
