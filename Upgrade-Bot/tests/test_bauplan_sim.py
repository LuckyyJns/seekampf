"""Bauplan-Simulator (bauplan_sim.py): Modell, Kaskaden-Nachbau, Suche.

    .venv/bin/python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bauplan_sim as sim  # noqa: E402
import gamedata  # noqa: E402
import planner  # noqa: E402

STUFEN = {"haupthaus": 8, "goldmine": 8, "steingrube": 8, "saegewerk": 8, "lagerhaus": 8,
          "steinmauer": 1, "wachturm": 1, "kaserne": 5, "hafen": 5}


@unittest.skipUnless(gamedata.available(), "data/gamedata.json fehlt")
class Modell(unittest.TestCase):
    def insel(self, lager=(0, 0, 0), **stufen):
        return sim.Insel({**STUFEN, **stufen}, dict(zip(sim.RES, lager)))

    def test_lager_kappt_und_zaehlt_verfallen(self):
        i = self.insel(lagerhaus=0)        # 500 ohne Lagerhaus
        i.warten(100 * 3600)
        self.assertEqual(set(i.lager.values()), {500.0})
        self.assertGreater(i.verfallen, 0)
        self.assertAlmostEqual(i.erzeugt, 1500.0)

    def test_bauen_bucht_beim_start_und_stuft_bei_fertigstellung(self):
        kosten = gamedata.cost("goldmine", 9)
        i = self.insel(lager=(kosten["gold"], kosten["stein"], kosten["holz"]))
        self.assertTrue(i.bauen("goldmine", 10 * 86400))
        dauer = gamedata.build_time_s("goldmine", 9, 8)
        self.assertAlmostEqual(i.t, dauer)
        self.assertEqual(i.stufen["goldmine"], 9)
        self.assertEqual(i.plan, [(0.0, dauer, "goldmine", 9)])
        self.assertAlmostEqual(i.verbaut, sum(kosten.values()))

    def test_bau_ueber_den_horizont_zaehlt_nicht(self):
        i = self.insel(lager=(5000, 5000, 5000))
        self.assertFalse(i.bauen("goldmine", 60))
        self.assertEqual((i.t, i.stufen["goldmine"], i.laeuft), (60, 8, "goldmine"))

    def test_voraussetzungen_und_sperren(self):
        i = self.insel(haupthaus=6)
        kand = i.kandidaten(gesperrt={"steinmauer"})
        self.assertNotIn("hafen", kand)         # Hafen erst ab Haupthaus 7
        self.assertIn("kaserne", kand)
        self.assertNotIn("steinmauer", kand)

    def test_externe_einnahmen(self):
        i = sim.Insel({**STUFEN, "goldmine": 0}, {}, extern={"gold": 100})
        i.warten(3600)
        self.assertAlmostEqual(i.lager["gold"], 100.0)


@unittest.skipUnless(gamedata.available(), "data/gamedata.json fehlt")
class Planer(unittest.TestCase):
    def start(self):
        return sim.Insel(STUFEN, {"gold": 900, "stein": 900, "holz": 900})

    def test_kette_entscheidet_wie_der_planer(self):
        start = self.start()
        erwartet = planner.choose_upgrade(start.kandidaten(), start.lager, start.kapazitaet(), False)
        ergebnis = sim.kette(start, 7 * 86400)
        self.assertEqual(ergebnis.plan[0][2], erwartet["building"])
        self.assertEqual(ergebnis.t, 7 * 86400)

    def test_suche_nie_schlechter_als_kette(self):
        start = self.start()
        h = 3 * 86400
        for ziel in ("wirtschaft", "punkte"):
            self.assertGreaterEqual(sim.wert(sim.suchen(start, h, ziel, breite=5), ziel),
                                    sim.wert(sim.kette(start, h), ziel))


if __name__ == "__main__":
    unittest.main()
