"""State des Allianz-Bots: defekte Datei darf den Start nicht abbrechen.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from state import State  # noqa: E402


class DefekteDatei(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pfad = os.path.join(self.tmp, "state.json")

    def test_intakte_datei_wird_gelesen(self):
        with open(self.pfad, "w") as f:
            json.dump({"vorgang_nr": 12}, f)
        st = State(self.pfad)
        self.assertEqual(st.data["vorgang_nr"], 12)
        self.assertIsNone(st.defekt)

    def test_kaputtes_json_wird_beiseitegelegt(self):
        with open(self.pfad, "w") as f:
            f.write('{"vorgang_nr": 12, ')
        st = State(self.pfad)                       # kein Absturz
        self.assertEqual(st.data["vorgang_nr"], 0)
        self.assertTrue(st.defekt and os.path.exists(st.defekt))
        self.assertFalse(os.path.exists(self.pfad))  # kaputte Datei bleibt zur Rettung unter anderem Namen
        with open(st.defekt) as f:
            self.assertIn("12", f.read())

    def test_kein_json_objekt(self):
        with open(self.pfad, "w") as f:
            f.write("[1, 2]")
        st = State(self.pfad)
        self.assertTrue(st.defekt)
        self.assertEqual(st.data["vorgang_nr"], 0)


if __name__ == "__main__":
    unittest.main()
