"""Zugriff auf die Seekampf-API, beschraenkt auf das, was der Ausbildungs-Bot
braucht.

Aufgebaut wie der Client des Upgrade-Bots: eine Session, automatischer
Backoff bei transienten Netzwerkfehlern - aber nur fuer GET. POST /fleets ist
NICHT idempotent: geht die Antwort auf dem Rueckweg verloren, waere die Flotte
trotzdem unterwegs, und ein zweiter Versuch schickte eine zweite los.
"""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config


class ApiError(Exception):
    def __init__(self, status_code, code, message):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"{status_code} {code}: {message}")


class SeekampfClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {"X-API-Key": config.API_KEY, "Content-Type": "application/json"}
        )
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=(502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _request(self, method, path, **kwargs):
        url = f"{config.API_BASE_URL}{path}"
        response = self.session.request(method, url, timeout=15, **kwargs)
        if response.status_code >= 400:
            body = {}
            try:
                body = response.json()
            except ValueError:
                pass
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise ApiError(
                response.status_code,
                error.get("code", "unknown"),
                error.get("message", response.text[:300]),
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ---------------------------------------------------------------- Konto
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
        """POST /fleets -> legt die Flotte an und gibt sie zurueck (201)."""
        return self._request("POST", "/fleets", json=payload)

    def recall_fleet(self, fleet_id):
        """POST /fleets/{id}/recall - geht nur bis recallable_until."""
        return self._request("POST", f"/fleets/{fleet_id}/recall")

    # ------------------------------------------------------------ Ausbildung
    def get_training(self, island_id):
        """GET /islands/{id}/training -> auftraege (item_typ, anzahl,
        abgeschlossen, status), garnison, schiffe und katalog je Gebaeude
        (kaserne/hafen: typ, kosten, ab_stufe, baubar)."""
        return self._request("GET", f"/islands/{island_id}/training")

    def start_training(self, island_id, facility, item, count):
        """POST /islands/{id}/training - kostet sofort; nicht wiederholen."""
        return self._request("POST", f"/islands/{island_id}/training",
                             json={"facility": facility, "item": item, "count": int(count)})
