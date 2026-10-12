"""Tests fuer den gemeinsamen Code aller Bots.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from seekampf_gemeinsam import api, geo, reservierungen
from seekampf_gemeinsam.logs import logger_einrichten


def antwort(status, body=None, headers=None):
    r = mock.Mock(status_code=status, headers=headers or {}, content=b"x", text="roh")
    r.json.return_value = body if body is not None else {}
    return r


class Api(unittest.TestCase):
    def test_fehler_nennt_methode_und_pfad(self):
        c = api.ApiBasis("k")
        c.session.request = mock.Mock(return_value=antwort(500, {"error": {"code": "unknown", "message": "Internal Server Error"}}))
        with self.assertRaises(api.ApiError) as ctx:
            c._request("GET", "/fleets/incoming")
        e = ctx.exception
        self.assertEqual((e.status_code, e.code, e.message), (500, "unknown", "Internal Server Error"))
        self.assertEqual(e.anfrage, "GET /fleets/incoming")
        self.assertIn("[GET /fleets/incoming]", str(e))

    def test_get_wird_bei_500_wiederholt_post_nicht(self):
        retry = api.ApiBasis("k").session.get_adapter("https://seekampf.de").max_retries
        self.assertIn(500, retry.status_forcelist)
        self.assertTrue(retry.is_retry("GET", 500))
        self.assertFalse(retry.is_retry("POST", 500))

    def test_429_wird_wiederholt(self):
        c = api.ApiBasis("k")
        ok = antwort(200, {"ok": 1})
        c.session.request = mock.Mock(side_effect=[antwort(429, headers={"Retry-After": "2"}), ok])
        with mock.patch("seekampf_gemeinsam.api.time.sleep") as schlaf:
            self.assertEqual(c._request("POST", "/x"), {"ok": 1})
        schlaf.assert_called_once_with(2.0)

    def test_serverzeit_aus_date_header(self):
        c = api.ApiBasis("k")
        c.session.request = mock.Mock(return_value=antwort(200, {}, {"Date": "Mon, 12 Oct 2026 10:00:00 GMT"}))
        c._request("GET", "/x")
        self.assertEqual(c.jetzt().strftime("%Y-%m-%d"), "2026-10-12")


class Geo(unittest.TestCase):
    def test_nachgerechnete_flotte(self):
        # 53:49:8 -> 53:48:6: die API meldete distanz_felder = sqrt(29)
        self.assertAlmostEqual(geo.distanz_felder((53, 49, 8), (53, 48, 6)), 29 ** 0.5)

    def test_langsamstes_schiff_und_untergrenze(self):
        self.assertEqual(geo.flotten_knoten({"kleines_kriegsschiff": 2, "kolonisationsschiff": 1}), 3.0)
        self.assertEqual(geo.fahrzeit_s((1, 1, 1), (1, 1, 2), 12), 120)


class Reservierungen(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.ordner)

    def test_summe_ueber_bots_ohne_eigene(self):
        reservierungen.schreiben("kolonisation", {"447": {"rohstoffe": {"holz": 100}, "einheiten": {"kleines_kriegsschiff": 2},
                                                          "grund": "Kolonisationsschiff"}}, self.ordner)
        reservierungen.schreiben("ausbildung", {447: {"einheiten": {"kleines_kriegsschiff": 3}}}, self.ordner)
        self.assertEqual(reservierungen.summe("einheiten", ordner=self.ordner), {"447": {"kleines_kriegsschiff": 5}})
        self.assertEqual(reservierungen.summe("einheiten", ausser="ausbildung", ordner=self.ordner),
                         {"447": {"kleines_kriegsschiff": 2}})
        self.assertEqual(reservierungen.summe("rohstoffe", ordner=self.ordner), {"447": {"holz": 100}})
        self.assertEqual(reservierungen.von("kolonisation", self.ordner)["447"]["grund"], "Kolonisationsschiff")

    def test_veraltete_datei_zaehlt_nicht(self):
        reservierungen.schreiben("kolonisation", {"447": {"rohstoffe": {"holz": 100}}}, self.ordner,
                                 jetzt=time.time() - reservierungen.MAX_ALTER_S - 60)
        self.assertEqual(reservierungen.summe("rohstoffe", ordner=self.ordner), {})
        self.assertFalse(reservierungen.alle(self.ordner)["kolonisation"]["aktuell"])
        self.assertEqual(reservierungen.von("kolonisation", self.ordner), {})

    def test_leere_eintraege_fallen_weg_und_kaputte_datei_stoert_nicht(self):
        reservierungen.schreiben("ausgleich", {"1": {"einheiten": {"kleines_handelsschiff": 0}}}, self.ordner)
        with open(os.path.join(self.ordner, "kaputt.json"), "w") as f:
            f.write("{nicht json")
        self.assertEqual(reservierungen.alle(self.ordner)["ausgleich"]["inseln"], {})
        self.assertNotIn("kaputt", reservierungen.alle(self.ordner))

    def test_fehlender_ordner(self):
        self.assertEqual(reservierungen.summe("rohstoffe", ordner=os.path.join(self.ordner, "gibtsnicht")), {})


class Logs(unittest.TestCase):
    def test_tagesdatei(self):
        ordner = tempfile.mkdtemp()
        try:
            log = logger_einrichten("test_gemeinsam", ordner, "probe", "Europe/Berlin", journal=False)
            log.info("hallo")
            for h in log.handlers:
                h.flush()
            dateien = os.listdir(ordner)
            self.assertEqual(len(dateien), 1)
            self.assertTrue(dateien[0].startswith("probe-"))
            with open(os.path.join(ordner, dateien[0])) as f:
                self.assertIn("[INFO] hallo", f.read())
            self.assertIs(logger_einrichten("test_gemeinsam", ordner, "probe", "Europe/Berlin"), log)
        finally:
            for h in list(log.handlers):
                h.close()
                log.removeHandler(h)
            shutil.rmtree(ordner)


if __name__ == "__main__":
    unittest.main()
