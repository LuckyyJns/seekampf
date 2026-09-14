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
        if response.status_code >= 400:
            body = {}
            try:
                body = response.json()
            except ValueError:
                pass
            error = body.get("error", {})
            raise ApiError(
                response.status_code,
                error.get("code", "unknown"),
                error.get("message", response.text),
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get_my_island_ids(self):
        """GET /me/islands -> Liste von IslandListItem ({id, name, koordinaten, punkte})."""
        if config.MANUAL_ISLAND_IDS:
            return config.MANUAL_ISLAND_IDS
        islands = self._request("GET", "/me/islands")
        return [item["id"] for item in islands]

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
