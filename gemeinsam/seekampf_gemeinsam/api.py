"""Kern des Zugriffs auf die Seekampf-API, gemeinsam fuer alle Bots.

Jeder Bot hat weiter seine eigene `api_client.SeekampfClient`-Klasse mit genau
den Endpunkten, die er braucht, und seinem eigenen API-Schluessel; sie erbt
Session, Wiederholungen und Fehlerauswertung von `ApiBasis`.

Wiederholt wird:
- HTTP 429 bei jeder Methode (der Server hat dann nichts verarbeitet),
- GET bei Netzfehlern und bei 500/502/503/504 (mit Backoff). Der Spielserver
  liefert ab und zu einen einzelnen 500 - ein zweiter Versuch klappt fast
  immer, statt den ganzen Takt scheitern zu lassen.
POST/DELETE werden bei 5xx NIE wiederholt: sie sind nicht idempotent (eine
Flotte oder ein Ausbau koennte sonst doppelt losgehen, obwohl der erste
Versuch angekommen ist).

Jede schreibende Anfrage bekommt einen eigenen `Idempotency-Key` (laut
API-Spezifikation optional; derselbe Schluessel soll einen Auftrag nur einmal
ausfuehren). Geht die Antwort verloren (Timeout, Verbindung weg), wird sie mit
DEMSELBEN Schluessel einmal wiederholt - aber nur, wenn IDEMPOTENZ_WIEDERHOLEN
an ist. Das bleibt AUS: Test am 2026-10-20 (zweimal POST /islands/581/training
mit demselben Schluessel) ergab zwei Auftraege - der Server beachtet den
Schluessel nicht (in openapi.json ist er auch bei keinem Endpunkt eingetragen).
Eine Wiederholung wuerde Auftraege doppelt ausfuehren. Erst nach einem neuen,
erfolgreichen Test einschalten; mitgeschickt wird der Schluessel trotzdem.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_BASE_URL = "https://seekampf.de/api/v1"
MAX_429_VERSUCHE = 3
SCHREIBEND = ("POST", "PUT", "PATCH", "DELETE")
IDEMPOTENZ_WIEDERHOLEN = False


class ApiError(Exception):
    def __init__(self, status_code, code, message, anfrage: str | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.anfrage = anfrage          # z. B. "GET /fleets/incoming"
        text = f"{status_code} {code}: {message}"
        super().__init__(f"{text} [{anfrage}]" if anfrage else text)


def warten_bei_429(response, versuch):
    """Sekunden bis zum naechsten Versuch nach HTTP 429 (Retry-After, sonst 2, 4, 8 ... s, hoechstens 30)."""
    try:
        sek = float(response.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        sek = 2.0 ** (versuch + 1)
    return max(0.5, min(sek, 30.0))


class ApiBasis:
    def __init__(self, api_key: str, base_url: str = API_BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        # Abstand Serveruhr - lokale Uhr (aus dem Date-Header der letzten Antwort).
        self._uhr_versatz = 0.0

    def jetzt(self) -> datetime:
        """Serverzeit (aus dem Date-Header der letzten Antwort fortgeschrieben)."""
        return datetime.fromtimestamp(time.time() + self._uhr_versatz, timezone.utc)

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        if method in SCHREIBEND:
            kwargs["headers"] = {"Idempotency-Key": uuid.uuid4().hex, **(kwargs.get("headers") or {})}
        try:
            response = self.session.request(method, url, timeout=15, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if method not in SCHREIBEND or not IDEMPOTENZ_WIEDERHOLEN:
                raise
            # Antwort verloren: mit demselben Schluessel noch einmal - der Server
            # fuehrt den Auftrag hoechstens einmal aus und liefert die Antwort.
            time.sleep(2)
            response = self.session.request(method, url, timeout=15, **kwargs)
        # 429 heisst: nichts verarbeitet - Wiederholen ist auch bei POST sicher.
        for versuch in range(MAX_429_VERSUCHE):
            if response.status_code != 429:
                break
            time.sleep(warten_bei_429(response, versuch))
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
            if not isinstance(error, dict):
                error = {}
            raise ApiError(
                response.status_code,
                error.get("code", "unknown"),
                error.get("message", (response.text or "")[:300]),
                anfrage=f"{method} {path}",
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()
