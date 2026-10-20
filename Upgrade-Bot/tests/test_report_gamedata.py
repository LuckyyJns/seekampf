"""Morgenreport (report.py) und das Einlesen der Regeltabellen (fetch_gamedata.py).

    .venv/bin/python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import fetch_gamedata  # noqa: E402
import report  # noqa: E402

TZ = report.TZ


class Morgenreport(unittest.TestCase):
    def setUp(self):
        self.logs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.logs, True)

    def log(self, tag, zeilen):
        with open(os.path.join(self.logs, f"{config.LOG_FILE_PREFIX}-{tag}.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(zeilen) + "\n")

    def test_ausbauten_warteschlange_lager_und_fehler(self):
        self.log("2026-10-19", [
            "2026-10-19 06:00:00 [INFO] Insel 7 | Warteschlange 1/3 | gestartet: goldmine → 5 (+16/h, Tabelle) | kein Ausbau: x",
            "2026-10-19 12:00:00 [INFO] Insel 7 | Warteschlange 1/3 | gestartet: goldmine → 6 (Such-Plan), kaserne → 4",
            "2026-10-19 13:00:00 [INFO] Insel 7: Ausbau 'hafen' von Hand gestartet (Seekampf-Hub)",
            "2026-10-19 14:00:00 [ERROR] API-Fehler auf Insel 7: 500 unknown",
            "Traceback (most recent call last):",
        ])
        self.log("2026-10-20", [
            "2026-10-20 01:00:00 [ERROR] API-Fehler auf Insel 8: 500 unknown",
            "2026-10-20 09:00:00 [INFO] Insel 7 | Warteschlange 1/3 | gestartet: saegewerk → 9",  # nach until
        ])
        since = datetime(2026, 10, 19, 5, 0, tzinfo=TZ)
        until = datetime(2026, 10, 20, 8, 0, tzinfo=TZ)
        inseln = {
            7: ("GiG", {"gold": 900, "stein": 0, "holz": 0, "kapazitaet": 1000,
                        "produktion_pro_h": {"gold": 50, "stein": 10, "holz": 10}},
                [{"status": "aktiv", "building_typ": "lagerhaus", "ziel_stufe": 9,
                  "finish_at": "2026-10-20T07:30:00+00:00"}, {"status": "wartend"}], ""),
            8: ("GiG 2", {"kapazitaet": 1000, "produktion_pro_h": {}}, [], ""),
        }
        text = report.build_summary(inseln, since=since, until=until, log_dir=self.logs)
        self.assertIn("4 Ausbauten auf 2 Inseln", text)
        self.assertIn("Goldmine 5/6 · Kaserne 4 · Hafen Hand", text)
        self.assertIn("⏳ Lagerhaus 9 bis 09:30 (+1 danach)", text)
        self.assertIn("⚠️ Lager voll: Gold in 2h 0min", text)
        self.assertIn("⏳ Warteschlange leer", text)
        self.assertIn("⚠️ 2 Fehler:", text)
        self.assertIn("2× API-Fehler auf Insel 7: 500 unknown", text)
        self.assertNotIn("Saegewerk", text)


SEITE = """<html><body><script>var x = "<table>";</script>
<h2>Gebaeude</h2>
<h3>Goldmine</h3>
<table><tr><th>Stufe</th><th>Gold</th><th>Stein</th><th>Holz</th><th>Bauzeit</th><th>Produktion/h</th></tr>
<tr><td>1</td><td>65</td><td>100</td><td>40</td><td>00:16:00</td><td>55</td></tr>
<tr><td>2</td><td>1.087</td><td>134</td><td>53</td><td>01:19:51</td><td>65</td></tr></table>
<h3>Sägewerk</h3>
<table><tr><th>Stufe</th><th>Gold</th><th>Stein</th><th>Holz</th><th>Bauzeit</th><th>Produktion/h</th></tr>
<tr><td>1</td><td>60</td><td>80</td><td>50</td><td>00:16:00</td><td>65</td></tr></table>
<h3>Lagerhaus</h3>
<table><tr><th>Stufe</th><th>Gold</th><th>Stein</th><th>Holz</th><th>Bauzeit</th>
<th>Lagerkapazität je Rohstoff</th><th>Plünderschutz je Rohstoff</th></tr>
<tr><td>1</td><td>61</td><td>50</td><td>110</td><td>00:16:00</td><td>1.000</td><td>440</td></tr></table>
<h3>Einheiten</h3>
<table><tr><th>Name</th><th>Gold</th></tr><tr><td>Steinewerfer</td><td>35</td></tr></table>
</body></html>"""


class Regeltabellen(unittest.TestCase):
    def test_parse(self):
        d = fetch_gamedata.parse(SEITE)
        self.assertEqual(sorted(d), ["goldmine", "lagerhaus", "saegewerk"])
        self.assertEqual(d["goldmine"]["2"], {"gold": 1087.0, "stein": 134.0, "holz": 53.0,
                                              "produktion_pro_h": 65.0, "bauzeit_s": 4791})
        self.assertEqual((d["lagerhaus"]["1"]["kapazitaet"], d["lagerhaus"]["1"]["pluenderschutz"]), (1000.0, 440.0))
        self.assertEqual(d["saegewerk"]["1"]["bauzeit_s"], 960)

    def test_hilfen(self):
        self.assertIsNone(fetch_gamedata._seconds("16:00"))
        self.assertEqual(fetch_gamedata._number("1.234,5"), 1234.5)
        self.assertIsNone(fetch_gamedata._number("–"))


if __name__ == "__main__":
    unittest.main()
