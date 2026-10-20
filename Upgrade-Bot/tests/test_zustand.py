"""Zustand ueber Neustarts (zustand.py) und die Uebernahme aus learned.json.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zustand import Zustand  # noqa: E402


class Uebernahme(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pfad = os.path.join(self.tmp.name, "zustand.json")
        self.alt = os.path.join(self.tmp.name, "learned.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_uebernimmt_uhren_und_report_und_loescht_alt(self):
        with open(self.alt, "w") as f:
            json.dump({"cost_points": {"goldmine": {"5": {}}}, "prod_deltas": {}, "last_snapshots": {"1": {}},
                       "low_priority_since": {"7": 123.0}, "last_report_date": "2026-10-19",
                       "last_report_at": "2026-10-19T08:00:00+02:00"}, f)
        z = Zustand(self.pfad, alt_path=self.alt)
        self.assertEqual(z.data, {"low_priority_since": {"7": 123.0}, "last_report_date": "2026-10-19",
                                  "last_report_at": "2026-10-19T08:00:00+02:00"})
        self.assertFalse(os.path.exists(self.alt))
        with open(self.pfad) as f:
            self.assertEqual(json.load(f), z.data)

    def test_ohne_alte_datei_leer_und_speichern(self):
        z = Zustand(self.pfad, alt_path=self.alt)
        self.assertEqual(z.data, {})
        z.data["last_report_date"] = "2026-10-20"
        z.save()
        self.assertEqual(Zustand(self.pfad).data, {"last_report_date": "2026-10-20"})

    def test_defekte_datei_beiseite(self):
        with open(self.pfad, "w") as f:
            f.write("{kaputt")
        self.assertEqual(Zustand(self.pfad).data, {})
        self.assertTrue(os.path.exists(self.pfad + ".defekt"))


if __name__ == "__main__":
    unittest.main()
