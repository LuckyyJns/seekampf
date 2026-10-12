"""Zugriff auf die Seekampf-API, beschraenkt auf das, was der Flotten-Manager
braucht.

Session, Wiederholungen und Fehlerauswertung kommen aus
`seekampf_gemeinsam.api` (gemeinsam fuer alle Bots); automatisch wiederholt
wird nur GET und HTTP 429. POST /fleets ist
NICHT idempotent: geht die Antwort auf dem Rueckweg verloren, waere die Flotte
trotzdem unterwegs, und ein zweiter Versuch schickte eine zweite los.
"""
from __future__ import annotations

import requests

import config
from seekampf_gemeinsam.api import MAX_429_VERSUCHE, ApiBasis, ApiError  # noqa: F401 - ApiError/MAX_429_VERSUCHE fuer die Aufrufer


class SeekampfClient(ApiBasis):
    def __init__(self):
        super().__init__(config.API_KEY, config.API_BASE_URL)

    def get_me(self):
        """GET /me -> u. a. current_island_id."""
        return self._request("GET", "/me")

    def get_islands_overview(self):
        """GET /me/islands/overview -> je Insel Rohstoffe, Truppen, Schiffe,
        Gebaeude und bedrohung_im_anflug.

        Wichtig: truppen/schiffe zaehlen nur, was gerade ZU HAUSE liegt -
        alles, was mit einer Flotte unterwegs ist, fehlt hier. Genau deshalb
        laesst sich daraus zaehlen, wie viele Flotten noch losfahren koennen.
        """
        return self._request("GET", "/me/islands/overview")

    # ---------------------------------------------------------------- Karte
    def get_region(self, x, y):
        """GET /map/region/{x}/{y} -> 3x3 Sektoren um (x, y) in einer Antwort."""
        return self._request("GET", f"/map/region/{x}/{y}")

    def get_island_info(self, x, y, z):
        """GET /map/island/{x}/{y}/{z} -> oeffentliche Infos einer Insel.

        Leeres Wasser antwortet nur mit {koordinaten, frei: true} - eine echte
        Insel hat immer ein Feld "besitzer" (null = unbewohnt/Ruine).
        """
        return self._request("GET", f"/map/island/{x}/{y}/{z}")

    # -------------------------------------------------------------- Flotten
    def get_fleets(self):
        """GET /fleets -> eigene Flotten, die gerade unterwegs sind.

        Felder je Flotte: id, mission, origin/target ({x,y,z,name}), ships,
        units, loot, state ("outbound" auf dem Hinweg), speed_knots,
        distanz_felder, depart_at, arrive_at, return_at, recallable_until.
        Eine Flotte verschwindet aus der Liste, sobald sie wieder daheim ist.
        """
        return self._request("GET", "/fleets") or []

    def create_fleet(self, payload):
        """POST /fleets -> legt die Flotte an und gibt sie zurueck (201).

        Reisst die Verbindung ab, bevor die Antwort da ist, kann die Flotte
        trotzdem losgefahren sein. Deshalb wird danach in GET /fleets nach ihr
        gesucht (neue Flotte, gleiches Ziel, gleiche Schiffe) und sie dann
        zurueckgegeben, statt einen Fehler zu melden. Gefunden wird nichts =
        der Netzfehler wird weitergereicht (Versand unklar).
        """
        vorher = {f.get("id") for f in self.get_fleets()}   # schlaegt das fehl, wurde noch nichts gesendet
        try:
            return self._request("POST", "/fleets", json=payload)
        except requests.RequestException:
            gefunden = self._neue_flotte_suchen(payload, vorher)
            if gefunden is None:
                raise
            return gefunden

    def _neue_flotte_suchen(self, payload, vorher):
        """Die Flotte aus `payload`, die seit `vorher` neu in GET /fleets steht (oder None)."""
        try:
            flotten = self.get_fleets()
        except (ApiError, requests.RequestException):
            return None
        ziel = payload.get("target") or {}
        ships = {t: int(n) for t, n in (payload.get("ships") or {}).items() if n}
        for f in flotten:
            t = f.get("target") or {}
            if (f.get("id") not in vorher
                    and (t.get("x"), t.get("y"), t.get("z")) == (ziel.get("x"), ziel.get("y"), ziel.get("z"))
                    and {k: int(n) for k, n in (f.get("ships") or {}).items() if n} == ships):
                return f
        return None

    def recall_fleet(self, fleet_id):
        """POST /fleets/{id}/recall - geht nur bis recallable_until."""
        return self._request("POST", f"/fleets/{fleet_id}/recall")

    # ------------------------------------------------------------- Berichte
    def get_combat_messages(self, limit=50):
        """GET /messages?folder=combat -> Kampf- und Spionageberichte.

        Der Kampfbericht traegt in payload: sieg, insel_koordinaten, loot
        ({gold, stein, holz}), verluste und battle_id.
        """
        return self._request("GET", "/messages", params={"folder": "combat", "limit": limit}) or []

    def archive_message(self, message_id):
        """POST /messages/{id}/archive - blendet die Nachricht im Postfach aus.

        Der Ordner der Nachricht bleibt "combat"; sie verschwindet lediglich
        aus GET /messages?folder=combat. Ueber die API einsehbar bleibt sie
        einzeln unter GET /messages/{id}, und die oeffentliche Inselseite zeigt
        sie weiter an. Zurueckholen laesst sie sich ueber die API nicht -
        einen unarchive-Endpunkt gibt es nicht.
        """
        return self._request("POST", f"/messages/{message_id}/archive")
