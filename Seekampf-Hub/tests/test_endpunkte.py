"""Tests fuer Dienststeuerung, Upgrade-/Allianz-Endpunkte und die Gesundheitsseite.

Alles in Temp-Ordnern; sudo/systemctl werden nie wirklich aufgerufen.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import gesundheit  # noqa: E402
import hub  # noqa: E402
from seekampf_gemeinsam import lebenszeichen  # noqa: E402

client = TestClient(hub.app)
KOPF = {"X-Seekampf-Hub": "1"}


class MitTempOrdnern(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bots = {name: dict(b, ordner=os.path.join(self.tmp, name)) for name, b in hub.BOTS.items()}
        p = mock.patch.dict(hub.BOTS, self.bots)
        p.start()
        self.addCleanup(p.stop)

    def datei(self, bot, *teile):
        with open(os.path.join(self.tmp, bot, "data", *teile), encoding="utf-8") as f:
            return json.load(f)

    def befehle(self, bot):
        ordner = os.path.join(self.tmp, bot, "data", "befehle")
        out = []
        for n in sorted(os.listdir(ordner)):
            with open(os.path.join(ordner, n), encoding="utf-8") as f:
                out.append(json.load(f))
        return out


class Dienststeuerung(unittest.TestCase):
    def test_nur_erlaubte_aktionen(self):
        for pfad in ("/api/dienste/hub/stop", "/api/dienste/flotte/loeschen", "/api/dienste/unbekannt/start"):
            self.assertEqual(client.post(pfad, headers=KOPF).status_code, 400, pfad)

    def test_genau_der_befehl_aus_sudoers(self):
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(hub.subprocess, "run", return_value=ok) as run, \
                mock.patch.object(hub, "_dienst_status", return_value={"zustand": "active"}):
            antwort = client.post("/api/dienste/kolonie/restart", headers=KOPF)
        self.assertEqual(antwort.status_code, 200)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0],
                         ["sudo", "-n", "/usr/bin/systemctl", "restart", "seekampf-kolonisations-bot.service"])

    def test_fehler_von_systemctl_kommt_an(self):
        nein = mock.Mock(returncode=1, stdout="", stderr="sudo: a password is required")
        with mock.patch.object(hub.subprocess, "run", return_value=nein):
            antwort = client.post("/api/dienste/upgrade/stop", headers=KOPF)
        self.assertEqual(antwort.status_code, 500)
        self.assertIn("password", antwort.json()["detail"])

    def test_hub_startet_sich_erst_nach_der_antwort_neu(self):
        with mock.patch.object(hub.subprocess, "Popen") as popen, mock.patch.object(hub.subprocess, "run") as run:
            self.assertEqual(client.post("/api/dienste/hub/restart", headers=KOPF).json(), {"ok": True})
        run.assert_not_called()
        befehl = popen.call_args.args[0]
        self.assertEqual(befehl[:2], ["sh", "-c"])
        self.assertIn("sleep 1; sudo -n /usr/bin/systemctl restart seekampf-hub.service", befehl[2])

    def test_status_aus_systemctl_show(self):
        aus = ("ActiveState=active\nSubState=running\nActiveEnterTimestamp=@1792460000\n"
               "NRestarts=2\nResult=success\nLoadState=loaded\n")
        with mock.patch.object(hub.subprocess, "run", return_value=mock.Mock(stdout=aus)):
            st = hub._dienst_status("x.service")
        self.assertEqual(st, {"zustand": "active", "detail": "running", "seit": 1792460000.0, "neustarts": 2,
                              "ergebnis": "success", "installiert": True})


class Upgrade(MitTempOrdnern):
    def put(self, payload, insel=5):
        return client.put(f"/api/upgrade/inseln/{insel}", json=payload, headers=KOPF)

    def test_einstellungen_werden_geprueft_und_gespeichert(self):
        self.assertEqual(self.put({"modus": "marktplatz"}).status_code, 400)
        self.assertEqual(self.put({"ausbauziele": [{"gebaeude": "labor", "stufe": 20}]}).status_code, 200)
        self.assertEqual(self.put({"gesperrt": ["marktplatz"]}).status_code, 400)
        self.assertEqual(self.put({"ausbauziele": [{"gebaeude": "hafen", "stufe": 21}]}).status_code, 400)
        self.assertEqual(self.put({"bis_stufe": "x"}).status_code, 400)
        a = self.put({"modus": "hafen", "bis_stufe": 25, "gesperrt": ["wachturm", "steinmauer", "wachturm"],
                      "ausbauziele": [{"gebaeude": "kaserne", "stufe": "10"}], "aktiv": 1})
        self.assertEqual(a.status_code, 200)
        insel = self.datei("upgrade", "steuerung.json")["inseln"]["5"]
        self.assertEqual(insel, {"modus": "hafen", "bis_stufe": 20, "gesperrt": ["steinmauer", "wachturm"],
                                 "ausbauziele": [{"gebaeude": "kaserne", "stufe": 10}], "aktiv": True})
        self.put({"bis_stufe": None}, insel=5)
        self.assertIsNone(self.datei("upgrade", "steuerung.json")["inseln"]["5"]["bis_stufe"])
        self.assertEqual(self.datei("upgrade", "steuerung.json")["inseln"]["5"]["modus"], "hafen")  # bleibt

    def test_befehle(self):
        self.assertEqual(client.post("/api/upgrade/befehl", json={"typ": "x"}, headers=KOPF).status_code, 400)
        self.assertEqual(client.post("/api/upgrade/befehl", json={"typ": "auftrag", "gebaeude": "marktplatz",
                                                                   "insel_id": 1}, headers=KOPF).status_code, 400)
        a = client.post("/api/upgrade/befehl", json={"typ": "auftrag", "gebaeude": "hafen", "insel_id": "7"},
                        headers=KOPF)
        self.assertEqual(a.status_code, 200)
        [b] = self.befehle("upgrade")
        self.assertEqual((b["typ"], b["insel_id"], b["gebaeude"], b["id"]), ("auftrag", 7, "hafen", a.json()["id"]))


class Allianz(MitTempOrdnern):
    def test_faehigkeiten(self):
        self.assertEqual(client.put("/api/allianz/faehigkeiten", json={"unsinn": True}, headers=KOPF).status_code, 400)
        client.put("/api/allianz/faehigkeiten", json={"notruf": 0, "beistand": 1, "x": 1}, headers=KOPF)
        self.assertEqual(self.datei("allianz", "steuerung.json")["faehigkeiten"], {"notruf": False, "beistand": True})

    def test_strategie_grenzen_und_nichts_gespeichert_bei_fehler(self):
        put = lambda p: client.put("/api/allianz/strategie", json=p, headers=KOPF)  # noqa: E731
        self.assertEqual(put({"HILFE_MAX_ANTEIL": 2}).status_code, 400)
        self.assertEqual(put({"MIN_SPEERKAEMPFER": "viele"}).status_code, 400)
        self.assertEqual(put({"ANFRAGE_SCHWELLE": 0.3, "ANFRAGE_ZIEL": 0.2}).status_code, 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "allianz", "data", "steuerung.json")))
        self.assertEqual(put({"ANFRAGE_SCHWELLE": 0.1, "ANFRAGE_ZIEL": 0.3, "RUECKRUF_ERLAUBT": 0}).status_code, 200)
        self.assertEqual(self.datei("allianz", "steuerung.json")["strategie"],
                         {"ANFRAGE_SCHWELLE": 0.1, "ANFRAGE_ZIEL": 0.3, "RUECKRUF_ERLAUBT": False})

    def test_kasse(self):
        post = lambda p: client.post("/api/allianz/kasse", json=p, headers=KOPF)  # noqa: E731
        self.assertEqual(post({"ab": 1.5}).status_code, 400)
        self.assertEqual(post({"ab": 0.8, "bis": 0.9}).status_code, 400)
        self.assertEqual(post({"aktiv": 1, "ab": 0.95, "bis": 0.8, "min_menge": -5}).status_code, 200)
        self.assertEqual(self.datei("allianz", "steuerung.json")["kasse"],
                         {"aktiv": True, "ab": 0.95, "bis": 0.8, "min_menge": 0})

    def test_anfrage(self):
        post = lambda p: client.post("/api/allianz/befehl", json=p, headers=KOPF)  # noqa: E731
        self.assertEqual(post({"typ": "anfrage", "rohstoffe": {}, "insel": "1:2:3"}).status_code, 400)
        self.assertEqual(post({"typ": "anfrage", "rohstoffe": {"holz": 5}, "insel": "1:2"}).status_code, 400)
        self.assertEqual(post({"typ": "anfrage", "rohstoffe": {"holz": "x"}, "insel": "1:2:3"}).status_code, 400)
        self.assertEqual(post({"typ": "anfrage", "rohstoffe": {"holz": 500, "gold": 0}, "insel": "1:2:3"}).status_code,
                         200)
        [b] = self.befehle("allianz")
        self.assertEqual((b["rohstoffe"], b["insel"]), ({"holz": 500}, "1:2:3"))


class GesundheitMitLebenszeichen(MitTempOrdnern):
    def setUp(self):
        super().setUp()
        self.lz_ordner = os.path.join(self.tmp, "lz")
        p = mock.patch.object(lebenszeichen, "ORDNER", self.lz_ordner)
        p.start()
        self.addCleanup(p.stop)
        gesundheit._cache.update(zeit=0.0, daten=None)
        self.addCleanup(gesundheit._cache.update, zeit=0.0, daten=None)

    def schreiben(self, bot, **werte):
        os.makedirs(self.lz_ordner, exist_ok=True)
        with open(os.path.join(self.lz_ordner, f"{bot}.json"), "w") as f:
            json.dump({"takt_s": 5, "letzter_takt": time.time(), "meldungen": [], **werte}, f)

    def test_haengt_fehlerzaehlung_und_ablehnungen(self):
        jetzt = time.time()
        self.schreiben("flotte", letzter_takt=jetzt - 30,
                       meldungen=[[jetzt - 60, "ERROR", "Tick fehlgeschlagen: 500"],
                                  [jetzt - 50, "WARNING", "Insel 4 bedroht"]])
        self.schreiben("ausbildung", takt_s=60, fehler_in_folge=3,
                       ablehnungen=[{"aktion": "POST /fleets", "code": "insufficient_ships", "anzahl": 7,
                                     "seit": jetzt - 3600, "zuletzt": jetzt, "text": "Nicht genug"}])
        g = gesundheit.berechnen(hub.BOTS, None, lambda unit: {"zustand": "active"})
        self.assertTrue(g["lebenszeichen"]["flotte"]["haengt"])            # 30 s > 5 x 5 s
        self.assertFalse(g["lebenszeichen"]["ausbildung"]["haengt"])
        self.assertEqual((g["logs"]["flotte"]["fehler"], g["logs"]["flotte"]["warnungen"]), (1, 1))
        self.assertEqual(g["lebenszeichen"]["ausbildung"]["fehler_in_folge"], 3)
        self.assertEqual(g["lebenszeichen"]["ausbildung"]["wiederholt_abgelehnt"][0]["anzahl"], 7)
        self.assertIsNone(g["lebenszeichen"]["kolonie"])                 # ohne Datei: Rueckfall auf die Logs
        self.assertIn("kolonie", g["logs"])

    def test_endpunkt_liefert_lebenszeichen(self):
        self.schreiben("upgrade", takt_s=300)
        with mock.patch.object(hub, "_dienst_status", return_value={"zustand": "active"}):
            g = client.get("/api/gesundheit").json()
        self.assertFalse(g["lebenszeichen"]["upgrade"]["haengt"])


if __name__ == "__main__":
    unittest.main()
