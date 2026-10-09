import time

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
        # Transiente Netzwerkfehler (z. B. "Remote end closed connection without
        # response") und kurzzeitige Server-Probleme automatisch mit Backoff erneut
        # versuchen, statt den Tick sofort scheitern zu lassen.
        # Nur GET wird automatisch wiederholt - POST/DELETE sind nicht idempotent
        # (ein Upgrade-Auftrag darf nicht versehentlich doppelt ausgeloest werden,
        # falls die Antwort verloren geht, obwohl der Request den Server erreicht hat).
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
        # 429 heisst: nichts verarbeitet - Wiederholen ist auch bei POST sicher.
        for versuch in range(MAX_429_VERSUCHE):
            if response.status_code != 429:
                break
            time.sleep(_warten_bei_429(response, versuch))
            response = self.session.request(method, url, timeout=15, **kwargs)
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
                error.get("message", response.text),
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get_my_islands(self):
        """GET /me/islands -> Liste von IslandListItem ({id, name, koordinaten, punkte}).
        Zurueck kommen id, name, koordinaten und punkte; Name und Koordinaten
        zeigen Telegram-Report und Seekampf-Hub, die Punkte landen im Verlauf."""
        if config.MANUAL_ISLAND_IDS:
            return [{"id": i, "name": f"Insel {i}", "koordinaten": ""} for i in config.MANUAL_ISLAND_IDS]
        islands = self._request("GET", "/me/islands")
        return [
            {"id": item["id"], "name": item.get("name") or f"Insel {item['id']}",
             "koordinaten": item.get("koordinaten") or "", "punkte": item.get("punkte")}
            for item in islands
        ]

    def get_island_resources(self, island_id):
        """GET /islands/{id}/resources -> ResourcesOut
        {gold, stein, holz, produktion_pro_h, kapazitaet, pluenderschutz}."""
        return self._request("GET", f"/islands/{island_id}/resources")

    def get_island_buildings(self, island_id):
        """GET /islands/{id}/buildings -> Liste von BuildingOut
        {typ, name, stufe, haupthaus_ab, verfuegbar, naechste_stufe, kosten, bauzeit_s}.
        Enthaelt die Kosten der naechsten Stufe bereits direkt - kein
        separater Detail-Call notwendig."""
        return self._request("GET", f"/islands/{island_id}/buildings")

    def get_construction_queue(self, island_id):
        """GET /islands/{id}/construction -> Liste von ConstructionOrderOut."""
        return self._request("GET", f"/islands/{island_id}/construction")

    def start_upgrade(self, island_id, building_type):
        """POST /islands/{id}/buildings/{typ}/upgrade."""
        return self._request(
            "POST", f"/islands/{island_id}/buildings/{building_type}/upgrade"
        )
