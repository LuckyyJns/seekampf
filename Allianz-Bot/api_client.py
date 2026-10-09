"""Zugriff auf die Seekampf-API fuer den Allianz-Bot.

Wie bei den anderen Bots: eine Session, Backoff nur fuer GET. POST und DELETE
sind nicht idempotent und werden nie automatisch wiederholt.

Loeschen von Forenbeitraegen gibt es hier bewusst NUR als private Methode
`_beitrag_loeschen`. Aufrufen darf sie allein `forum.eigenen_beitrag_loeschen`,
die vorher prueft, dass der Beitrag vom Bot selbst stammt. Nachrichten (PN)
loescht der Bot nie - dafuer gibt es gar keine Methode.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

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


MAX_429_VERSUCHE = 3


def _warten_bei_429(response, versuch):
    """Sekunden bis zum naechsten Versuch nach HTTP 429 (Retry-After, sonst 2, 4, 8 ... s, hoechstens 30)."""
    try:
        sek = float(response.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        sek = 2.0 ** (versuch + 1)
    return max(0.5, min(sek, 30.0))


class SeekampfClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {"X-API-Key": config.API_KEY, "Content-Type": "application/json"}
        )
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(502, 503, 504),
                      allowed_methods=("GET",), raise_on_status=False)
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        # Abstand Serveruhr - lokale Uhr. Das Protokoll rechnet mit Serverzeit.
        self._uhr_versatz = 0.0

    def jetzt(self) -> datetime:
        """Serverzeit (aus dem Date-Header der letzten Antwort fortgeschrieben)."""
        return datetime.fromtimestamp(time.time() + self._uhr_versatz, timezone.utc)

    def _request(self, method, path, **kwargs):
        url = f"{config.API_BASE_URL}{path}"
        response = self.session.request(method, url, timeout=15, **kwargs)
        # 429 heisst: nichts verarbeitet - Wiederholen ist auch bei POST sicher.
        for versuch in range(MAX_429_VERSUCHE):
            if response.status_code != 429:
                break
            time.sleep(_warten_bei_429(response, versuch))
            response = self.session.request(method, url, timeout=15, **kwargs)
        datum = response.headers.get("Date")
        if datum:
            try:
                self._uhr_versatz = parsedate_to_datetime(datum).timestamp() + 0.5 - time.time()
            except (TypeError, ValueError):
                pass
        if response.status_code >= 400:
            body = {}
            try:
                body = response.json()
            except ValueError:
                pass
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise ApiError(response.status_code, error.get("code", "unknown"),
                           error.get("message", response.text[:300]))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ---------------------------------------------------------------- Konto
    def get_me(self):
        return self._request("GET", "/me")

    def get_islands_overview(self):
        return self._request("GET", "/me/islands/overview") or []

    def get_alliance(self, alliance_id):
        return self._request("GET", f"/alliances/{alliance_id}")

    def get_profile(self, user_id):
        return self._request("GET", f"/users/{user_id}/profile")

    def lookup_user(self, name):
        return self._request("GET", "/users/lookup", params={"name": name})

    # --------------------------------------------------------------- Forum
    def list_threads(self, limit=100):
        """Alle Allianz-Threads, seitenweise bis eine Seite kuerzer als limit ist."""
        alle, offset = [], 0
        while True:
            seite = self._request("GET", "/forum/threads",
                                  params={"sichtbarkeit": "allianz", "limit": limit, "offset": offset}) or []
            alle += seite
            if len(seite) < limit:
                return alle
            offset += limit

    def get_thread_posts(self, thread_id, limit=200):
        beitraege, offset = [], 0
        while True:
            d = self._request("GET", f"/forum/threads/{thread_id}",
                              params={"limit": limit, "offset": offset}) or {}
            seite = d.get("beitraege") or []
            beitraege += seite
            if len(seite) < limit:
                return beitraege
            offset += limit

    def create_post(self, thread_id, body):
        return self._request("POST", f"/forum/threads/{thread_id}/posts", json={"body": body})

    def _beitrag_loeschen(self, post_id):
        return self._request("DELETE", f"/forum/posts/{post_id}")

    # ------------------------------------------------------------ Postfach
    def list_messages(self, folder, limit=50, archiv=False):
        return self._request("GET", "/messages",
                             params={"folder": folder, "limit": limit, "archiv": archiv}) or []

    def send_message(self, recipient, subject, body):
        return self._request("POST", "/messages",
                             json={"recipient": recipient, "subject": subject, "body": body})

    def kasse_einzahlen(self, alliance_id, island_id, resources):
        """POST /alliances/{id}/kasse/deposit - zieht die Rohstoffe sofort von
        der Insel ab, in der Kasse kommen sie nach der Transferzeit an."""
        return self._request("POST", f"/alliances/{alliance_id}/kasse/deposit",
                             json={"island_id": island_id, "resources": {r: int(n) for r, n in resources.items()}})

    def get_battle(self, battle_id):
        return self._request("GET", f"/battles/{battle_id}")

    # -------------------------------------------------------------- Flotten
    def get_incoming(self):
        return self._request("GET", "/fleets/incoming") or []

    def get_fleets(self):
        return self._request("GET", "/fleets") or []

    def create_fleet(self, payload):
        return self._request("POST", "/fleets", json=payload)

    def recall_fleet(self, fleet_id):
        """POST /fleets/{id}/recall - geht nur bis recallable_until."""
        return self._request("POST", f"/fleets/{fleet_id}/recall")
