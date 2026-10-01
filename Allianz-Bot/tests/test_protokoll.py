"""Konformitaetspruefung gegen testfaelle.json (unveraendert eingebunden)
plus ein paar Eigenpruefungen fuer das Schreiben von Nachrichten.

    .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import protokoll  # noqa: E402

FAELLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testfaelle.json")


class Konformitaet(unittest.TestCase):
    def test_alle_faelle(self):
        with open(FAELLE, encoding="utf-8") as f:
            faelle = json.load(f)["faelle"]
        self.assertEqual(len(faelle), 46)
        for fall in faelle:
            with self.subTest(fall["id"]):
                erg = protokoll.parse(fall["text"], fall["kanal"], fall["absender_id"],
                                      fall["nachricht_id"], fall["zeitpunkt"], fall["betreff"])
                self.assertEqual(erg.nachricht, fall["erwartet"],
                                 f"grund={erg.grund}, erwartet grund={fall['grund']}")


class Schreiben(unittest.TestCase):
    def test_rundreise_angebot(self):
        text = protokoll.bauen("ANGEBOT", {
            "vorgang": "815-7", "von": "53:49:8", "einheiten": {"speerkaempfer": 40},
            "leihe": ["speerkaempfer"], "ankunft": "2026-09-24T18:10:00+02:00",
            "gueltig_bis": "2026-09-24T17:30:00+02:00"})
        erg = protokoll.parse(text, "pn", 104, 1, "2026-09-24T17:15:00+02:00", "[ANGEBOT] 815-7")
        self.assertIsNotNone(erg.nachricht, erg.grund)
        self.assertEqual(erg.nachricht["felder"]["einheiten"], {"speerkaempfer": 40})
        self.assertFalse(protokoll.verstoesst_gegen_linksperre(text))

    def test_keine(self):
        text = protokoll.bauen("RUECKGABE", {"vorgang": "815-7", "einheiten": {}})
        erg = protokoll.parse(text, "pn", 815, 1, "2026-09-24T17:15:00+02:00")
        self.assertEqual(erg.nachricht["felder"]["einheiten"], {})

    def test_linksperre(self):
        self.assertTrue(protokoll.verstoesst_gegen_linksperre("Jannis-Bot v1.0"))
        self.assertFalse(protokoll.verstoesst_gegen_linksperre("ende. Neuer Satz"))

    def test_kurzform_sommerzeitende(self):
        # 25.10.2026: Uhren springen von 03:00 auf 02:00 zurueck.
        erg = protokoll.parse("[NOTRUF]\ninsel: 1:2:3\nankunft: 04:00", "forum:notrufe", 1, 2,
                              "2026-10-25T01:30:00+02:00")
        self.assertEqual(erg.nachricht["felder"]["ankunft"], "2026-10-25T04:00:00+01:00")


if __name__ == "__main__":
    unittest.main()
