"""Tests fuer den Seekampf-Hub: CSRF-Schutz, Gesundheit, Verlauf.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import gesundheit  # noqa: E402
import hub  # noqa: E402

# Ohne "with": der Lifespan (Kartenplaner-Thread) startet im Test nicht.
client = TestClient(hub.app)


class Csrf(unittest.TestCase):
    def test_aendernde_anfrage_ohne_kopf_wird_abgelehnt(self):
        for pfad in ("/api/dienste/flotte/stop", "/api/karte/scan", "/api/flotte/settings"):
            self.assertEqual(client.post(pfad, json={}).status_code, 403, pfad)
        self.assertEqual(client.put("/api/upgrade/inseln/1", json={}).status_code, 403)

    def test_lesen_geht_ohne_kopf(self):
        self.assertEqual(client.get("/").status_code, 200)

    def test_mit_kopf_kommt_die_anfrage_durch(self):
        # unbekannte Aktion -> 400 vom Endpunkt selbst, also nicht von der Sperre
        antwort = client.post("/api/dienste/flotte/loeschen", headers={"X-Seekampf-Hub": "1"})
        self.assertEqual(antwort.status_code, 400)


class Gesundheit(unittest.TestCase):
    def test_leerlauf_zeitgewichtet(self):
        ordner = tempfile.mkdtemp()
        os.makedirs(os.path.join(ordner, "logs"))
        t0 = datetime.now() - timedelta(hours=1)
        zeilen = [
            f"{t0:%Y-%m-%d %H:%M:%S} [INFO] Insel 20 | Warteschlange 0/3 | x\n",
            f"{t0 + timedelta(minutes=10):%Y-%m-%d %H:%M:%S} [INFO] Insel 20 | Warteschlange 2/3 | x\n",
            f"{t0 + timedelta(minutes=40):%Y-%m-%d %H:%M:%S} [ERROR] Insel 20: Ausbau 'x' fehlgeschlagen: 500\n",
        ]
        heute = datetime.now().date().isoformat()
        with open(os.path.join(ordner, "logs", f"bot-{heute}.log"), "w") as f:
            f.writelines(zeilen)
        erg = gesundheit._log_auswerten(ordner, "bot", time.time() - 86400, leerlauf=True)
        self.assertEqual(erg["fehler"], 1)
        self.assertEqual(erg["leerlauf"]["20"]["leer_s"], 600)
        self.assertEqual(len(erg["haeufig"]), 1)


class VerlaufSchnittstelle(unittest.TestCase):
    def test_lueckenlose_zeitachse_in_inselreihenfolge(self):
        ordner = tempfile.mkdtemp()
        os.makedirs(os.path.join(ordner, "data"))
        heute = datetime.now().date().isoformat()
        with open(os.path.join(ordner, "data", "verlauf.json"), "w") as f:
            json.dump({"inseln": {"20": {heute: {"punkte": 5}}, "447": {heute: {"punkte": 9}}}}, f)
        with open(os.path.join(ordner, "data", "status.json"), "w") as f:
            json.dump({"inseln": {"20": {"name": "GiG 2", "position": 1}, "447": {"name": "GiG", "position": 0}}}, f)
        alt = hub.BOTS["upgrade"]["ordner"]
        hub.BOTS["upgrade"]["ordner"] = ordner
        try:
            d = client.get("/api/upgrade/verlauf?tage=7").json()
        finally:
            hub.BOTS["upgrade"]["ordner"] = alt
        self.assertEqual(len(d["daten"]), 7)
        self.assertEqual([i["name"] for i in d["inseln"]], ["GiG", "GiG 2"])
        self.assertEqual(d["reihen"]["447"][-1], {"punkte": 9})
        self.assertIsNone(d["reihen"]["447"][0])


if __name__ == "__main__":
    unittest.main()
