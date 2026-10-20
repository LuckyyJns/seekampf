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

from seekampf_gemeinsam import api, bilanz, geo, reservierungen
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

    def test_schreibende_anfragen_tragen_idempotency_key(self):
        c = api.ApiBasis("k")
        c.session.request = mock.Mock(side_effect=[antwort(429), antwort(200, {"ok": 1}), antwort(200, {})])
        with mock.patch("seekampf_gemeinsam.api.time.sleep"):
            c._request("POST", "/fleets", json={})
        schluessel = {k.kwargs["headers"]["Idempotency-Key"] for k in c.session.request.call_args_list}
        self.assertEqual(len(schluessel), 1)            # 429-Wiederholung mit demselben Schluessel
        c._request("GET", "/fleets")
        self.assertNotIn("headers", c.session.request.call_args.kwargs)

    def test_post_nach_netzfehler_nur_mit_schalter_wiederholt(self):
        import requests
        c = api.ApiBasis("k")
        c.session.request = mock.Mock(side_effect=[requests.exceptions.Timeout(), antwort(200, {"id": 7})])
        with self.assertRaises(requests.exceptions.Timeout):
            c._request("POST", "/fleets", json={})
        c.session.request = mock.Mock(side_effect=[requests.exceptions.Timeout(), antwort(200, {"id": 7})])
        with mock.patch.object(api, "IDEMPOTENZ_WIEDERHOLEN", True), mock.patch("seekampf_gemeinsam.api.time.sleep"):
            self.assertEqual(c._request("POST", "/fleets", json={}), {"id": 7})
        k1, k2 = (k.kwargs["headers"]["Idempotency-Key"] for k in c.session.request.call_args_list)
        self.assertEqual(k1, k2)

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

    def test_eintrag_nur_mit_grund_bleibt(self):
        reservierungen.schreiben("ausgleich", {"1": {"einheiten": {"kleines_handelsschiff": 0}, "grund": "Lieferung"}},
                                 self.ordner)
        self.assertEqual(set(reservierungen.von("ausgleich", self.ordner)), {"1"})

    def test_unveraendert_wird_nicht_dauernd_neu_geschrieben(self):
        pfad = os.path.join(self.ordner, "ausgleich.json")
        reservierungen.schreiben("ausgleich", {"1": {"grund": "x"}}, self.ordner)
        erst = os.stat(pfad).st_mtime_ns
        os.utime(pfad, ns=(erst - 10**9, erst - 10**9))
        reservierungen.schreiben("ausgleich", {"1": {"grund": "x"}}, self.ordner)
        self.assertEqual(os.stat(pfad).st_mtime_ns, erst - 10**9)        # nicht neu geschrieben
        reservierungen.schreiben("ausgleich", {"2": {"grund": "y"}}, self.ordner)
        self.assertNotEqual(os.stat(pfad).st_mtime_ns, erst - 10**9)     # geaendert -> geschrieben
        reservierungen.schreiben("ausgleich", {"2": {"grund": "y"}}, self.ordner,
                                 jetzt=time.time() + reservierungen.AUFFRISCHEN_S + 1)
        with open(pfad) as f:
            self.assertGreater(json.load(f)["zeit"], time.time() + 60)  # aufgefrischt

    def test_fehlender_ordner(self):
        self.assertEqual(reservierungen.summe("rohstoffe", ordner=os.path.join(self.ordner, "gibtsnicht")), {})


class Bilanz(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.ordner)

    def test_buchen_zusammenfassen_und_bestand(self):
        heute = time.time()
        a = bilanz.Bilanz("flotte", self.ordner)
        a.buchen(447, "Raids", {"gold": 100, "stein": 50, "holz": 0})
        a.buchen(447, "Raids", {"gold": 20})
        a.buchen("447", "Ausgleich abgegeben", {"holz": 300}, -1)
        a.buchen(447, "Raids", {"gold": 0})                       # Nullbuchung faellt weg
        a.speichern()
        b = bilanz.Bilanz("upgrade", self.ordner)
        b.bestand(447, {"gold": 1, "stein": 2, "holz": 3})
        b.bestand(447, {"gold": 10, "stein": 20, "holz": 30})
        b.buchen(447, "Ausbau", {"gold": 500, "stein": 400, "holz": 300}, -1)
        b.speichern()
        z = bilanz.zusammenfassen(1, self.ordner)
        t = bilanz.tag(heute)
        self.assertEqual(z["buchungen"][t]["447"], {"Raids": {"gold": 120.0, "stein": 50.0},
                                                     "Ausgleich abgegeben": {"holz": -300.0},
                                                     "Ausbau": {"gold": -500.0, "stein": -400.0, "holz": -300.0}})
        self.assertEqual(z["bestand"][t]["447"]["erst"]["gold"], 1)
        self.assertEqual(z["bestand"][t]["447"]["letzt"]["gold"], 10)

    def test_neu_laden_und_alte_tage_fallen_weg(self):
        a = bilanz.Bilanz("kolonisation", self.ordner)
        a.buchen(1, "Kolonisation", {"holz": 35500}, -1, zeitpunkt=time.time() - (bilanz.BEHALTEN_TAGE + 2) * 86400)
        a.buchen(1, "Kolonisation", {"holz": 35500}, -1)
        a.speichern()
        b = bilanz.Bilanz("kolonisation", self.ordner)              # nach einem Neustart
        self.assertEqual(list(b.daten["tage"]), [bilanz.tag()])
        b.buchen(1, "Kolonisation", {"holz": 1}, -1)
        b.speichern()
        self.assertEqual(bilanz.zusammenfassen(1, self.ordner)["buchungen"][bilanz.tag()]["1"]["Kolonisation"]["holz"],
                         -35501.0)

    def test_seltenes_schreiben(self):
        a = bilanz.Bilanz("ausbildung", self.ordner)
        a.buchen(1, "Ausbildung", {"gold": 1}, -1)                 # erstes Buchen schreibt sofort
        pfad = os.path.join(self.ordner, "ausbildung.json")
        vorher = os.stat(pfad).st_mtime_ns
        a.buchen(1, "Ausbildung", {"gold": 1}, -1)                 # innerhalb von SPEICHERN_S: nur im Speicher
        self.assertEqual(os.stat(pfad).st_mtime_ns, vorher)
        a.speichern()
        with open(pfad) as f:
            self.assertEqual(json.load(f)["tage"][bilanz.tag()]["1"]["Ausbildung"]["gold"], -2.0)


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
