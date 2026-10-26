"""Kartenscan (scanner.py) und Morgenreport (report.py) des Flotten-Managers.

    .venv/bin/python -m unittest discover -s tests
"""
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

import config  # noqa: E402
import report  # noqa: E402
import scanner  # noqa: E402
from state import State, neue_insel  # noqa: E402


def insel_daten(x, y, z, besitzer=None, name=None):
    return {"koordinaten": f"{x}:{y}:{z}", "x": x, "y": y, "z": z, "besitzer": besitzer, "name": name or f"I{z}"}


class Region:
    def __init__(self, inseln):
        self.inseln, self.abfragen = inseln, []

    def get_region(self, x, y):
        self.abfragen.append((x, y))
        # verschachtelt wie im Spiel: Sektoren in Zeilen, darin Inseln
        return {"sektoren": [[{"inseln": [i for i in self.inseln if abs(i["x"] - x) <= 1 and abs(i["y"] - y) <= 1]}]]}


class Scanner(unittest.TestCase):
    def setUp(self):
        self.st = State(os.path.join(tempfile.mkdtemp(), "state.json"))
        self.st.data["inseln"]["1"] = neue_insel("Heimat", "50:50:1", aktiv=True)
        self.st.data["inseln"]["1"].setdefault("einstellungen", {})["scan_radius_sektoren"] = 1

    def test_region_zentren_decken_den_bereich_ab(self):
        for radius in range(0, 6):
            zentren = scanner._region_zentren(50, radius)
            for sektor in range(50 - radius, 50 + radius + 1):
                self.assertTrue(any(abs(sektor - c) <= 1 for c in zentren), (radius, sektor))
            self.assertEqual(len(zentren), -(-(2 * radius + 1) // 3))   # so wenige Abfragen wie moeglich
        self.assertEqual(scanner._region_zentren(50, 1), [50])          # Radius 1: genau eine Abfrage

    def test_inseln_aus_beliebiger_schachtelung_und_nur_freie(self):
        antwort = {"a": [{"b": insel_daten(1, 1, 1)}, insel_daten(1, 1, 2, besitzer="Niemend")]}
        inseln = scanner._inseln_aus_region(antwort)
        self.assertEqual(len(inseln), 2)
        self.assertEqual([scanner.ist_frei(i) for i in inseln], [True, False])
        self.assertFalse(scanner.ist_frei({"koordinaten": "1:1:3", "z": 3}))   # ohne Feld: nicht frei

    def test_scan_haelt_statistik_haengt_neues_an_und_wirft_besiedeltes_raus(self):
        welt = Region([insel_daten(50, 50, 5), insel_daten(51, 50, 7), insel_daten(49, 51, 9, besitzer="Max"),
                       insel_daten(53, 50, 3)])   # 53 liegt ausserhalb von Radius 1
        erg = scanner.scan(welt, self.st, 1, (50, 50, 1), {"kleines_kriegsschiff": 1})
        self.assertEqual(welt.abfragen, [(50, 50)])
        self.assertEqual((erg["gefunden"], sorted(erg["neu"])), (2, ["50:50:5", "51:50:7"]))
        ziele = self.st.insel(1)["ziele"]
        ziele["50:50:5"]["raids"] = 4
        self.st.insel(1)["rotation_index"] = 1
        # Naechster Scan: 51:50:7 ist besiedelt, 50:50:8 neu.
        welt.inseln = [insel_daten(50, 50, 5), insel_daten(51, 50, 7, besitzer="Max"), insel_daten(50, 50, 8)]
        erg = scanner.scan(welt, self.st, 1, (50, 50, 1), {"kleines_kriegsschiff": 1})
        self.assertEqual((erg["neu"], erg["entfernt"]), (["50:50:8"], ["51:50:7"]))
        self.assertEqual(ziele["50:50:5"]["raids"], 4)                    # Statistik bleibt
        self.assertEqual(self.st.insel(1)["rotation"], ["50:50:5", "50:50:8"])
        self.assertEqual(self.st.insel(1)["rotation_index"], 1)
        self.assertGreater(ziele["50:50:8"]["fahrzeit_s"], 0)

    def test_segel_verkuerzt_die_fahrzeit(self):
        welt = Region([insel_daten(51, 51, 25)])
        scanner.scan(welt, self.st, 1, (50, 50, 1), {"kleines_kriegsschiff": 1})
        ohne = self.st.insel(1)["ziele"]["51:51:25"]["fahrzeit_s"]
        scanner.scan(welt, self.st, 1, (50, 50, 1), {"kleines_kriegsschiff": 1}, segel=5)
        self.assertAlmostEqual(self.st.insel(1)["ziele"]["51:51:25"]["fahrzeit_s"], ohne / 1.3)


class Morgenreport(unittest.TestCase):
    def setUp(self):
        self.st = State(os.path.join(tempfile.mkdtemp(), "state.json"))
        self.st.settings["report_stunde"] = 7
        self.notifier = mock.Mock(aktiv=True)
        self.notifier.send.return_value = True

    def ts(self, tag, stunde):
        return datetime(2026, 10, tag, stunde, 0, tzinfo=report.TZ).timestamp()

    def test_erst_merken_dann_einmal_am_tag_ab_der_stunde(self):
        report.wenn_faellig(self.st, self.notifier, self.ts(19, 9))
        self.notifier.send.assert_not_called()                          # allererster Start
        self.st.data["stats"].update(raids=5, gold=1200.0, stein=800.0, holz=1000.0, inseln_besucht=["a", "b"])
        report.wenn_faellig(self.st, self.notifier, self.ts(19, 23))   # selber Tag
        report.wenn_faellig(self.st, self.notifier, self.ts(20, 6))    # vor der Stunde
        self.notifier.send.assert_not_called()
        report.wenn_faellig(self.st, self.notifier, self.ts(20, 7))
        self.notifier.send.assert_called_once()
        text = self.notifier.send.call_args.args[1]
        self.assertIn("Flotten losgeschickt: 5", text)
        self.assertIn("1.200 Gold, 800 Stein, 1.000 Holz (zusammen 3.000)", text)
        report.wenn_faellig(self.st, self.notifier, self.ts(20, 8))
        self.assertEqual(self.notifier.send.call_count, 1)
        self.assertIn("Flotten losgeschickt: 0", report.text(self.st))  # Basis nach Versand neu

    def test_fehlgeschlagener_versand_wird_wiederholt(self):
        report.wenn_faellig(self.st, self.notifier, self.ts(19, 9))
        self.notifier.send.return_value = False
        report.wenn_faellig(self.st, self.notifier, self.ts(20, 8))
        self.notifier.send.return_value = True
        report.wenn_faellig(self.st, self.notifier, self.ts(20, 9))
        self.assertEqual(self.notifier.send.call_count, 2)

    def test_ohne_telegram_nichts(self):
        report.wenn_faellig(self.st, None, self.ts(20, 9))
        report.wenn_faellig(self.st, mock.Mock(aktiv=False), self.ts(20, 9))
        self.assertIsNone(self.st.data.get("letzter_report"))


if __name__ == "__main__":
    unittest.main()
