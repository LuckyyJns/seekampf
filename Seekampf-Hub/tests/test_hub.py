"""Tests fuer den Seekampf-Hub: CSRF-Schutz, Gesundheit, Verlauf.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import gesundheit  # noqa: E402
import hub  # noqa: E402
from seekampf_gemeinsam import bilanz, reservierungen  # noqa: E402

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


class Bilanz(unittest.TestCase):
    def test_nicht_zugeordnet_ist_lagerveraenderung_minus_buchungen(self):
        ordner = tempfile.mkdtemp()
        alt = bilanz.ORDNER
        bilanz.ORDNER = ordner
        try:
            f = bilanz.Bilanz("flotte", ordner)
            f.buchen(447, "Raids", {"gold": 1000})
            f.speichern()
            u = bilanz.Bilanz("upgrade", ordner)
            u.bestand(447, {"gold": 5000, "stein": 100, "holz": 0})
            u.buchen(447, "Ausbau", {"stein": 50}, -1)
            u.bestand(447, {"gold": 6500, "stein": 50, "holz": 0})
            u.speichern()
            d = client.get("/api/bilanz?tage=1").json()
        finally:
            bilanz.ORDNER = alt
        insel = d["tage"][bilanz.tag()]["447"]
        self.assertEqual(insel["Raids"], {"gold": 1000.0})
        # Gold +1500 im Lager, davon 1000 gebucht -> 500 nicht zugeordnet; Stein passt genau.
        self.assertEqual(insel[d["rest"]], {"gold": 500, "stein": 0, "holz": 0})

    def test_reservierungen_mit_inselnamen(self):
        ordner = tempfile.mkdtemp()
        alt = reservierungen.ORDNER
        reservierungen.ORDNER = ordner
        try:
            reservierungen.schreiben("ausbildung", {"447": {"einheiten": {"kleines_kriegsschiff": 2}, "grund": "Truppentransport"}})
            d = client.get("/api/reservierungen").json()
        finally:
            reservierungen.ORDNER = alt
        self.assertEqual([(e["bot_titel"], e["insel_id"], e["grund"], e["aktuell"]) for e in d["eintraege"]],
                         [("Ausbildungs-Bot", "447", "Truppentransport", True)])


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


class SpielerTracker(unittest.TestCase):
    @staticmethod
    def stand(zeit, spieler, schiffe=None, allianzen=None):
        return {"zeit": zeit, "spieler": spieler,
                "welt": {"spieler_anzahl": len(spieler), "inseln_anzahl": 10, "flotten_unterwegs": 5,
                         "schiffe_gesamt": schiffe or {"kleines_kriegsschiff": 100}, "schiffe_auf_see": {"kleines_kriegsschiff": 10},
                         "einheiten_gesamt": {"speerkaempfer": 50}, "einheiten_auf_see": {}},
                "allianzen": allianzen or [{"id": 1, "rang": 1, "tag": "MTG", "punkte": 1000, "mitglieder": 5}]}

    @staticmethod
    def sp(name, rang, punkte, inseln, allianz=1, quests=()):
        return {"name": name, "rang": rang, "punkte": punkte, "inseln_anzahl": len(inseln),
                "allianz": {"id": allianz, "tag": "X"} if allianz else None,
                "inseln": {k: {"name": k, "punkte": p} for k, p in inseln.items()}, "quests": list(quests)}

    def test_tagesvergleich(self):
        import spieler
        a = self.stand(1000, {"1": self.sp("anna", 1, 500, {"1:1:1": 100, "1:1:2": 50}),
                              "2": self.sp("bert", 2, 300, {"2:2:2": 80, "2:2:3": 20})})
        b = self.stand(90000, {"1": self.sp("anna", 2, 520, {"1:1:1": 120, "2:2:3": 20, "3:3:3": 1}, quests=["Eroberer"]),
                               "2": self.sp("bert", 1, 600, {"2:2:2": 80, "1:1:2": 50}, allianz=None),
                               "3": self.sp("neu", 3, 1, {"4:4:4": 1})},
                       schiffe={"kleines_kriegsschiff": 130, "spaehschiff": 2})
        aus = spieler.auswerten([b, a])          # Reihenfolge egal, sortiert nach Zeit
        self.assertEqual([s["name"] for s in aus["stand"]["spieler"]], ["bert", "anna", "neu"])
        tag = aus["tage"][0]
        anna, bert = tag["spieler"]["1"], tag["spieler"]["2"]
        self.assertEqual(anna["punkte_diff"], 20)
        self.assertEqual(anna["rang_diff"], -1)
        self.assertEqual(anna["inseln_anzahl_diff"], 1)
        self.assertEqual(anna["inseln_dazu"], [{"k": "2:2:3", "name": "2:2:3", "von": "bert"},
                                               {"k": "3:3:3", "name": "3:3:3", "von": None}])
        self.assertEqual(anna["inseln_weg"], [{"k": "1:1:2", "name": "1:1:2", "an": "bert"}])
        self.assertEqual(anna["inseln_punkte"], [{"k": "1:1:1", "name": "1:1:1", "diff": 20}])
        self.assertEqual(anna["quests_neu"], ["Eroberer"])
        self.assertEqual(bert["rang_diff"], 1)
        self.assertEqual(bert["allianz_vorher"], {"id": 1, "tag": "X"})
        self.assertTrue(tag["spieler"]["3"]["neu"])
        self.assertEqual(tag["welt"]["schiffe"], {"kleines_kriegsschiff": 30, "spaehschiff": 2})
        self.assertEqual(tag["welt"]["truppen"], {"speerkaempfer": 0})
        self.assertEqual(tag["welt"]["spieler_anzahl"], 1)

    def test_fehlendes_profil_und_leerer_stand(self):
        import spieler
        self.assertEqual(spieler.auswerten([])["tage"], [])
        a = self.stand(1000, {"1": self.sp("anna", 1, 500, {"1:1:1": 1})})
        b = self.stand(2000, {"1": dict(self.sp("anna", 1, 510, {}), inseln=None, quests=None)})
        e = spieler.auswerten([a, b])["tage"][0]["spieler"]["1"]
        self.assertNotIn("inseln_weg", e)                 # Profil fehlte: keine falschen Verluste
        self.assertEqual(e["punkte_diff"], 10)

    def test_planer_holt_stand_nach(self):
        import spieler
        if datetime.now(spieler.TZ).hour < spieler.STAND_STUNDE:
            self.skipTest("zwischen 0 und 1 Uhr ist der heutige Stand noch nicht faellig")
        ordner = tempfile.mkdtemp()
        t = spieler.SpielerTracker(os.path.join(ordner, "s.json"))
        gestern = time.time() - 2 * 86400
        spieler._schreiben({"staende": [self.stand(gestern, {})]}, t.pfad)
        self.assertEqual(len(t.staende()), 1)
        gestartet = []
        t.stand_starten = lambda grund: gestartet.append(grund)
        stop = mock.Mock()
        stop.wait.side_effect = [False, True]
        t.planer(stop)
        self.assertEqual(gestartet, ["taeglich 1:00 Uhr"])

    def test_endpunkt(self):
        with mock.patch.object(hub.spieler_tracker, "daten", return_value={"stand": None, "tage": []}), \
             mock.patch.object(hub.karte, "ich", return_value={"id": 7}):
            r = client.get("/api/spieler")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ich"], 7)


if __name__ == "__main__":
    unittest.main()


class Drosselung(unittest.TestCase):
    """Seit 25.10.2026 antwortet der Server bei zu vielen Abfragen mit 429."""

    @staticmethod
    def antwort(status, retry="1"):
        import httpx
        return httpx.Response(status, headers={"Retry-After": retry}, json={},
                              request=httpx.Request("GET", "https://x/map"))

    def test_429_wird_abgewartet_und_wiederholt(self):
        import karte
        d = karte.Drossel(pause=0.1)
        c = mock.Mock()
        c.get.side_effect = [self.antwort(429), self.antwort(429), self.antwort(200)]
        with mock.patch.object(karte.time, "sleep") as schlaf:
            r = d.get(c, "/map/region/2/2")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get.call_count, 3)
        self.assertEqual(d.gedrosselt, 2)
        self.assertAlmostEqual(d.pause, 0.225)                 # Abstand ist gewachsen
        wartezeiten = [a.args[0] for a in schlaf.call_args_list]
        self.assertEqual(wartezeiten[:2], [1.0, 1.0])            # Retry-After
        self.assertAlmostEqual(wartezeiten[2], 0.225)

    def test_dauerhaft_429_wird_zum_fehler(self):
        import httpx
        import karte
        c = mock.Mock()
        c.get.return_value = self.antwort(429)
        with mock.patch.object(karte.time, "sleep"), self.assertRaises(httpx.HTTPStatusError):
            karte.Drossel().get(c, "/x")
        self.assertEqual(c.get.call_count, karte.MAX_429_VERSUCHE)

    def test_fehlgeschlagener_scan_startet_nicht_jede_minute_neu(self):
        import karte
        if datetime.now(karte.TZ).hour < karte.SCAN_STUNDE:
            self.skipTest("zwischen 0 und 1 Uhr ist der heutige Scan noch nicht faellig")
        k = karte.Karte()
        k.client = mock.Mock()
        k.client.get.side_effect = RuntimeError("Server weg")
        with mock.patch.object(karte, "_lesen", return_value={"zeit": time.time() - 2 * 86400, "inseln": {}}):
            k._scan("test")
            self.assertGreater(k._naechster_versuch, time.time() + 60)
            gestartet = []
            k.scan_starten = lambda grund: gestartet.append(grund)
            stop = mock.Mock()
            stop.wait.side_effect = [False, True]
            k.planer(stop)
        self.assertEqual(gestartet, [])
