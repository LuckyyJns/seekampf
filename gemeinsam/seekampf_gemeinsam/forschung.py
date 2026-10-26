"""Forschungsstand je Insel (Labor) und was er fuer die Bots aendert.

Forschung gilt nur auf der Insel, auf der sie erforscht wurde. Jede Stufe
bringt 6 % (https://seekampf.de/legal/regeln, Referenztabellen "Labor"):
- Segel: +6 % Knoten je Stufe. Nachgemessen am 26.10.2026: Flotten von einer
  Insel mit Segel 6 meldeten speed_knots 9,52 (grosses Handelsschiff, 7 Knoten)
  und 13,6 (kleines Handelsschiff, 10 Knoten) - also Knoten * (1 + 0,36).
- Engineering: -6 % Bauzeit je Stufe, zusaetzlich zum Haupthaus-Bonus. Wie
  beide zusammenwirken, sagen die Regeln nicht; angenommen ist, dass sie
  sich multiplizieren (noch nicht nachgemessen, keine Insel hatte Engineering).

Die Bots forschen nicht selbst - sie lesen den Stand nur, um Fahr- und
Bauzeiten richtig zu rechnen. Die Inseluebersicht enthaelt ihn nicht, also
fragt `Forschungsstand` GET /islands/{id}/research je Insel ab und haelt das
Ergebnis eine Weile vor.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

EFFEKT_JE_STUFE = 0.06
NEU_LADEN_S = 3600
# Nach einem Fehler nicht bei jedem Aufruf neu fragen.
FEHLER_PAUSE_S = 300


def knoten_faktor(segel: int) -> float:
    return 1.0 + EFFEKT_JE_STUFE * max(0, int(segel or 0))


def bauzeit_faktor(engineering: int) -> float:
    return max(0.0, 1.0 - EFFEKT_JE_STUFE * max(0, int(engineering or 0)))


def stufen_aus(antwort) -> dict[str, int]:
    """ResearchOverviewOut -> {tech: stufe}."""
    if not isinstance(antwort, dict):
        return {}
    return {f["tech"]: int(f.get("stufe") or 0) for f in antwort.get("forschungen") or []
            if isinstance(f, dict) and f.get("tech")}


class Forschungsstand:
    """Zwischenspeicher {insel_id: {tech: stufe}}.

    `client` ist ein ApiBasis-Client (jeder Bot hat einen). Schlaegt die
    Abfrage fehl, gilt der zuletzt bekannte Stand - oder Stufe 0, dann rechnet
    der Bot wie vor dem Labor."""

    def __init__(self, client, neu_laden_s: float = NEU_LADEN_S):
        self.client = client
        self.neu_laden_s = neu_laden_s
        self._stand: dict[int, tuple[float, dict[str, int]]] = {}

    def stufen(self, insel_id) -> dict[str, int]:
        iid = int(insel_id)
        jetzt = time.time()
        eintrag = self._stand.get(iid)
        if eintrag is not None and jetzt < eintrag[0]:
            return eintrag[1]
        alt = eintrag[1] if eintrag else {}
        try:
            neu = stufen_aus(self.client._request("GET", f"/islands/{iid}/research"))
        except Exception as e:  # noqa: BLE001 - Forschung ist nur Feinschliff, nie ein Grund abzubrechen
            log.warning("Forschungsstand von Insel %s nicht lesbar (%s) - rechne mit %s", iid, e, alt or "Stufe 0")
            self._stand[iid] = (jetzt + FEHLER_PAUSE_S, alt)
            return alt
        self._stand[iid] = (jetzt + self.neu_laden_s, neu)
        return neu

    def stufe(self, insel_id, tech: str) -> int:
        return int(self.stufen(insel_id).get(tech, 0))

    def knoten_faktor(self, insel_id) -> float:
        return knoten_faktor(self.stufe(insel_id, "segel"))

    def bauzeit_faktor(self, insel_id) -> float:
        return bauzeit_faktor(self.stufe(insel_id, "engineering"))

    def vergessen(self, insel_id=None) -> None:
        if insel_id is None:
            self._stand.clear()
        else:
            self._stand.pop(int(insel_id), None)
