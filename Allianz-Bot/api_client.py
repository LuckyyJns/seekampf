"""Zugriff auf die Seekampf-API fuer den Allianz-Bot.

Session, Wiederholungen und Serverzeit aus `seekampf_gemeinsam.api`; Backoff nur fuer GET. POST und DELETE
sind nicht idempotent und werden nie automatisch wiederholt.

Loeschen von Forenbeitraegen gibt es hier bewusst NUR als private Methode
`_beitrag_loeschen`. Aufrufen darf sie allein `forum.eigenen_beitrag_loeschen`,
die vorher prueft, dass der Beitrag vom Bot selbst stammt. Nachrichten (PN)
loescht der Bot nie - dafuer gibt es gar keine Methode.
"""
from __future__ import annotations

import config
from seekampf_gemeinsam.api import MAX_429_VERSUCHE, ApiBasis, ApiError  # noqa: F401 - ApiError/MAX_429_VERSUCHE fuer die Aufrufer


class SeekampfClient(ApiBasis):
    def __init__(self):
        super().__init__(config.API_KEY, config.API_BASE_URL)

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
