"""Tests fuer den Upgrade-Bot: Lager-Ueberlauf, Verlauf, Steuerung neuer
Inseln, Morgenreport.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import logging
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

import bot  # noqa: E402
import config  # noqa: E402
import planner  # noqa: E402
import report  # noqa: E402
import steuerung  # noqa: E402
import verlauf as verlauf_modul  # noqa: E402

TZ = ZoneInfo(config.TIMEZONE_NAME)


class KeinStore:
    def production_delta(self, *a):
        return None


def kandidat(name, stufe, kosten):
    return {"building": name, "level": stufe, "target_level": stufe + 1, "cost": kosten}


def statuszeile(t: datetime, insel, lager, kap=1749, prod=(100, 100, 100), gestartet=""):
    g = f" | gestartet: {gestartet}" if gestartet else ""
    return (f"{t:%Y-%m-%d %H:%M:%S} [INFO] Insel {insel} | Warteschlange 1/3 | Lager {lager[0]}/{lager[1]}/{lager[2]} "
            f"von {kap} | Produktion +{prod[0]}/+{prod[1]}/+{prod[2]} pro h{g} | kein Ausbau: x\n")


class NurNochMauerUndTurm(unittest.TestCase):
    """Sind alle anderen Gebaeude auf Hoechststufe oder gesperrt, kommen
    Steinmauer/Wachturm ohne die Leerlauf-Wartezeit dran."""
    genug = {"gold": 5000, "stein": 5000, "holz": 5000}

    def test_nur_niedrige_uebrig_baut_sofort(self):
        kandidaten = {"steinmauer": kandidat("steinmauer", 6, {"gold": 429, "stein": 1431, "holz": 215})}
        wahl = planner.choose_upgrade(kandidaten, self.genug, 50000, KeinStore(), False)
        self.assertEqual(wahl["building"], "steinmauer")
        self.assertNotEqual(planner.rejection_reason("steinmauer", kandidaten, 50000, False), "niedrige Prioritaet")

    def test_anderes_gebaeude_noch_ausbaubar_haelt_die_wartezeit(self):
        kandidaten = {
            "steinmauer": kandidat("steinmauer", 6, {"gold": 429, "stein": 1431, "holz": 215}),
            "haupthaus": kandidat("haupthaus", 14, {"gold": 9000, "stein": 9000, "holz": 9000}),
        }
        self.assertIsNone(planner.choose_upgrade(kandidaten, self.genug, 50000, KeinStore(), False))
        self.assertEqual(planner.rejection_reason("steinmauer", kandidaten, 50000, False), "niedrige Prioritaet")

    def test_keine_kandidaten(self):
        self.assertFalse(planner.nur_niedrige({}))


class KoloReserve(unittest.TestCase):
    def setUp(self):
        self._pfad = config.KOLO_RESERVE_PATH
        config.KOLO_RESERVE_PATH = os.path.join(tempfile.mkdtemp(), "reserve.json")

    def tearDown(self):
        config.KOLO_RESERVE_PATH = self._pfad

    def schreiben(self, alter_s):
        with open(config.KOLO_RESERVE_PATH, "w") as f:
            json.dump({"zeit": time.time() - alter_s, "inseln": {"447": {"gold": 19525, "stein": 4260, "holz": 35500}}}, f)

    def test_reserve_wird_abgezogen(self):
        self.schreiben(0)
        reserve = bot._kolo_reserve(447)
        planbar = bot._ohne_reserve({"gold": 20000, "stein": 3000, "holz": 40000, "kapazitaet": 50000}, reserve)
        self.assertEqual((planbar["gold"], planbar["stein"], planbar["holz"]), (475, 0, 4500))
        self.assertEqual(planbar["kapazitaet"], 50000)
        self.assertEqual(bot._kolo_reserve(20), {"gold": 0.0, "stein": 0.0, "holz": 0.0})

    def test_veraltete_oder_fehlende_datei_zaehlt_nicht(self):
        self.assertEqual(bot._kolo_reserve(447), {})
        self.schreiben(config.KOLO_RESERVE_MAX_ALTER_S + 60)
        self.assertEqual(bot._kolo_reserve(447), {})


class Ueberlauf(unittest.TestCase):
    def setUp(self):
        self.kandidaten = {
            "goldmine": kandidat("goldmine", 5, {"gold": 100, "stein": 100, "holz": 100}),
            "lagerhaus": kandidat("lagerhaus", 4, {"gold": 200, "stein": 200, "holz": 200}),
        }
        self.genug = {"gold": 1749, "stein": 1000, "holz": 1000}

    def test_ohne_ueberlauf_gewinnt_das_ressourcengebaeude(self):
        wahl = planner.choose_upgrade(self.kandidaten, self.genug, 1749, KeinStore(), False)
        self.assertEqual(wahl["building"], "goldmine")

    def test_bei_ueberlauf_kommt_das_lagerhaus_zuerst(self):
        wahl = planner.choose_upgrade(self.kandidaten, self.genug, 1749, KeinStore(), False, ueberlauf=True)
        self.assertEqual(wahl["building"], "lagerhaus")

    def _verlauf(self, voll_s):
        v = verlauf_modul.Verlauf(os.path.join(tempfile.mkdtemp(), "v.json"), log_dir=tempfile.mkdtemp())
        if voll_s:
            v.data["fenster"]["20"] = [[time.time() - 60, voll_s]]
        return v

    def test_ueberlauf_braucht_volles_lager_und_dauer(self):
        voll = {"gold": 1749, "stein": 10, "holz": 10, "kapazitaet": 1749}
        halb = {"gold": 900, "stein": 10, "holz": 10, "kapazitaet": 1749}
        jetzt = time.time()
        self.assertEqual(bot._ueberlauf(voll, self._verlauf(3 * 3600), 20, jetzt), (True, ["gold"]))
        self.assertFalse(bot._ueberlauf(voll, self._verlauf(1800), 20, jetzt)[0])     # erst kurz voll
        self.assertFalse(bot._ueberlauf(halb, self._verlauf(3 * 3600), 20, jetzt)[0])  # jetzt nicht voll

    def test_kein_zweites_lagerhaus_solange_eins_im_bau_ist(self):
        voll = {"gold": 1749, "stein": 10, "holz": 10, "kapazitaet": 1749}
        im_bau = [{"building_typ": "lagerhaus"}]
        self.assertFalse(bot._ueberlauf(voll, self._verlauf(3 * 3600), 20, time.time(), im_bau)[0])


class Verlauf(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.logs = os.path.join(self.ordner, "logs")
        os.makedirs(self.logs)

    def test_nachtragen_aus_logs_und_rueckrechnen(self):
        tag = datetime(2026, 9, 20, 10, 0, tzinfo=TZ)
        zeilen = [
            statuszeile(tag, 20, (1749, 5, 5), gestartet="goldmine → 6 (+16/h, Tabelle)"),
            statuszeile(tag + timedelta(minutes=5), 20, (1749, 5, 5)),
            statuszeile(tag + timedelta(minutes=10), 20, (1000, 5, 5)),
        ]
        with open(os.path.join(self.logs, f"{config.LOG_FILE_PREFIX}-2026-09-20.log"), "w") as f:
            f.writelines(zeilen)
        v = verlauf_modul.Verlauf(os.path.join(self.ordner, "v.json"), log_dir=self.logs)
        t = v.data["inseln"]["20"]["2026-09-20"]
        self.assertEqual(t["voll_s"]["gold"], 600)                 # zwei mal fuenf Minuten voll
        self.assertAlmostEqual(t["verlust"]["gold"], 100 * 600 / 3600)
        self.assertEqual(t["ausbauten"], 1)
        # erster echter Durchlauf: Goldmine heute 7, Saegewerk 3 -> am 20.09. Goldmine 6, Saegewerk 3
        v.erfassen(20, {"gold": 1, "stein": 1, "holz": 1, "kapazitaet": 1749, "produktion_pro_h": {}},
                   [{"typ": "goldmine", "stufe": 7}, {"typ": "saegewerk", "stufe": 3}], 120, 0, time.time())
        t = v.data["inseln"]["20"]["2026-09-20"]
        self.assertEqual(t["stufen"], {"goldmine": 7, "saegewerk": 3})  # danach kein Start mehr
        self.assertEqual(v.data["rueckrechnen"], [])

    def test_nachtragen_fuellt_das_24h_fenster(self):
        jetzt = datetime.now(TZ).replace(microsecond=0)
        zeilen = [statuszeile(jetzt - timedelta(hours=3, minutes=m), 20, (1749, 5, 5)) for m in (30, 20, 10, 0)]
        with open(os.path.join(self.logs, f"{config.LOG_FILE_PREFIX}-{jetzt.date().isoformat()}.log"), "w") as f:
            f.writelines(zeilen)
        v = verlauf_modul.Verlauf(os.path.join(self.ordner, "v.json"), log_dir=self.logs)
        self.assertEqual(v.voll_letzte_24h(20, time.time()), 1800)

    def test_erfassen_zeitgewichtet_und_fenster(self):
        v = verlauf_modul.Verlauf(os.path.join(self.ordner, "v.json"), log_dir=self.logs)
        t0 = time.time() - 600
        voll = {"gold": 1749, "stein": 0, "holz": 0, "kapazitaet": 1749,
                "produktion_pro_h": {"gold": 360, "stein": 0, "holz": 0}}
        v.erfassen(20, voll, [], 50, 0, t0)
        v.erfassen(20, voll, [], 50, 0, t0 + 600)
        self.assertEqual(v.voll_letzte_24h(20, time.time()), 600)
        v.speichern()
        self.assertTrue(os.path.exists(os.path.join(self.ordner, "v.json")))


class NeueInseln(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self._alt = (steuerung.DATA_DIR, steuerung.STEUERUNG_PATH, steuerung.SPERR_PATH)
        steuerung.DATA_DIR = d
        steuerung.STEUERUNG_PATH = os.path.join(d, "steuerung.json")
        steuerung.SPERR_PATH = os.path.join(d, "steuerung.lock")

    def tearDown(self):
        steuerung.DATA_DIR, steuerung.STEUERUNG_PATH, steuerung.SPERR_PATH = self._alt

    def test_uebernimmt_die_neueste_insel(self):
        with open(steuerung.STEUERUNG_PATH, "w") as f:
            json.dump({"inseln": {"447": {"gesperrt": []},
                                  "665": {"gesperrt": ["steinmauer", "wachturm"]}}}, f)
        inseln = [{"id": i} for i in (447, 665, 184)]
        self.assertEqual(steuerung.neue_inseln_uebernehmen(inseln), [("184", "665")])
        self.assertEqual(steuerung.insel(steuerung.lesen(), 184)["gesperrt"], ["steinmauer", "wachturm"])
        self.assertEqual(steuerung.neue_inseln_uebernehmen(inseln), [])


class Morgenreport(unittest.TestCase):
    def test_alle_ausbauten_kompakt(self):
        logs = tempfile.mkdtemp()
        jetzt = datetime.now(TZ)
        zeilen = [statuszeile(jetzt - timedelta(hours=2), 20, (1, 1, 1),
                              gestartet="goldmine → 5 (+16/h, Tabelle), kaserne → 4"),
                  statuszeile(jetzt - timedelta(hours=1), 20, (1, 1, 1), gestartet="goldmine → 6")]
        with open(os.path.join(logs, f"{config.LOG_FILE_PREFIX}-{jetzt.date().isoformat()}.log"), "w") as f:
            f.writelines(zeilen)
        text = report.build_summary({20: ("GiG 2", {"kapazitaet": 1749}, [], None)},
                                    since=jetzt - timedelta(hours=3), until=jetzt, log_dir=logs)
        self.assertIn("Goldmine 5/6 · Kaserne 4", text)
        self.assertIn("3 Ausbauten auf 1 Inseln", text)
        self.assertLess(len(text), 400)


if __name__ == "__main__":
    unittest.main()
