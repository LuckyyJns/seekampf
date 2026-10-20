"""Was der Upgrade-Bot ueber einen Neustart hinweg behalten muss: die
Leerlauf-Uhren fuer Steinmauer/Wachturm (low_priority_since) und wann der
Morgenreport zuletzt ging (last_report_date/last_report_at).

Bis Oktober 2026 lag das in data/learned.json zusammen mit gelernten
Kosten/Ertraegen je Stufe. Das Lernen ist entfallen (die Regeltabellen in
gamedata.py liefern die Werte); beim ersten Start werden die drei Eintraege
uebernommen und learned.json geloescht.
"""
from __future__ import annotations

import json
import os

SCHLUESSEL = ("low_priority_since", "last_report_date", "last_report_at")


class Zustand:
    def __init__(self, path: str, alt_path: str | None = None):
        self.path = path
        self.data = self._load()
        if alt_path and os.path.exists(alt_path):
            self._uebernehmen(alt_path)

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (ValueError, OSError):
                pass
            try:
                os.replace(self.path, self.path + ".defekt")
            except OSError:
                pass
        return {}

    def _uebernehmen(self, alt_path: str) -> None:
        """Einmalig: Eintraege aus der alten learned.json holen, dann loeschen."""
        try:
            with open(alt_path, "r", encoding="utf-8") as f:
                alt = json.load(f)
        except (ValueError, OSError):
            alt = {}
        if isinstance(alt, dict):
            for k in SCHLUESSEL:
                if k in alt and k not in self.data:
                    self.data[k] = alt[k]
        self.save()
        os.remove(alt_path)

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
