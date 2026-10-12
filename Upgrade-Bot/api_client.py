"""Zugriff auf die Seekampf-API fuer den Upgrade-Bot.

Session, Wiederholungen und Fehlerauswertung kommen aus
`seekampf_gemeinsam.api`; automatisch wiederholt wird nur GET und HTTP 429 -
ein Ausbau-Auftrag darf nie doppelt ausgeloest werden.
"""
import config
from seekampf_gemeinsam.api import MAX_429_VERSUCHE, ApiBasis, ApiError  # noqa: F401 - ApiError/MAX_429_VERSUCHE fuer die Aufrufer


class SeekampfClient(ApiBasis):
    def __init__(self):
        super().__init__(config.API_KEY, config.API_BASE_URL)

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
