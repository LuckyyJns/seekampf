"""Ablauftests fuer den Ausbildungs-Bot - gegen eine nachgebaute Spiel-API.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import logging
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

import config  # noqa: E402
from ausbildung import Ausbilder  # noqa: E402
from state import State  # noqa: E402

KASERNE = [
    {"typ": "steinewerfer", "ab_stufe": 1, "kosten": {"gold": 35, "stein": 28, "holz": 14}},
    {"typ": "speerkaempfer", "ab_stufe": 5, "kosten": {"gold": 80, "stein": 45, "holz": 55}},
    {"typ": "bogenschuetze", "ab_stufe": 10, "kosten": {"gold": 165, "stein": 30, "holz": 90}},
]
HAFEN = [{"typ": "kleines_kriegsschiff", "kaempfer_kap": 8}, {"typ": "grosses_kriegsschiff", "kaempfer_kap": 30}]


def insel(iid, koord, kaserne=20, res=(100000, 100000, 100000), truppen=None, schiffe=None, bedroht=False):
    return {"id": iid, "name": f"I{iid}", "koordinaten": koord, "gebaeude": {"kaserne": kaserne},
            "rohstoffe": {"gold": res[0], "stein": res[1], "holz": res[2], "kapazitaet": 200000},
            "truppen": dict(truppen or {}), "schiffe": dict(schiffe or {}), "bedrohung_im_anflug": bedroht}


class FakeApi:
    def __init__(self, inseln):
        self.ov = inseln
        self.fleets, self.trainings, self.created = [], [], []
        self.auftraege = {}

    def get_islands_overview(self):
        return json.loads(json.dumps(self.ov))

    def get_fleets(self):
        return json.loads(json.dumps(self.fleets))

    def get_training(self, iid):
        i = next(i for i in self.ov if i["id"] == iid)
        stufe = i["gebaeude"]["kaserne"]
        return {"auftraege": self.auftraege.get(iid, []),
                "katalog": {"kaserne": [dict(k, baubar=stufe >= k["ab_stufe"]) for k in KASERNE],
                            "hafen": HAFEN}}

    def start_training(self, iid, facility, item, count):
        self.trainings.append((iid, item, count))
        self.auftraege.setdefault(iid, []).append({"item_typ": item, "anzahl": count, "abgeschlossen": 0,
                                                   "status": "aktiv"})
        return {"id": len(self.trainings)}

    def create_fleet(self, p):
        self.created.append(p)
        x, y, z = p["target"]["x"], p["target"]["y"], p["target"]["z"]
        von = next(i for i in self.ov if i["id"] == p["origin_island_id"])
        for t, n in p["units"].items():
            von["truppen"][t] -= n
        for t, n in p["ships"].items():
            von["schiffe"][t] -= n
        self.fleets.append({"id": len(self.created), "mission": "handel", "state": "outbound",
                            "origin_island_id": von["id"], "target": {"x": x, "y": y, "z": z},
                            "units": p["units"], "ships": p["ships"]})
        return self.fleets[-1]


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._pfade = (config.RESERVE_PATH, config.KOLO_RESERVE_PATH)
        config.RESERVE_PATH = os.path.join(self.tmp, "reserve.json")
        config.KOLO_RESERVE_PATH = os.path.join(self.tmp, "kolo.json")

    def tearDown(self):
        config.RESERVE_PATH, config.KOLO_RESERVE_PATH = self._pfade

    def bot(self, api, soll: dict):
        st = State(os.path.join(self.tmp, "state.json"))
        for iid, werte in soll.items():
            st.soll_setzen(iid, werte)
        return Ausbilder(api, st), st


class VorOrt(Basis):
    def test_fehlende_einheiten_werden_ausgebildet(self):
        api = FakeApi([insel(1, "40:40:1", truppen={"speerkaempfer": 3})])
        b, _ = self.bot(api, {1: {"speerkaempfer": 10}})
        b.tick()
        self.assertEqual(api.trainings, [(1, "speerkaempfer", 7)])
        b.tick()
        self.assertEqual(len(api.trainings), 1)        # in Ausbildung zaehlt mit

    def test_raids_unterwegs_zaehlen_mit(self):
        api = FakeApi([insel(1, "40:40:1", truppen={"steinewerfer": 2})])
        api.fleets.append({"mission": "attack", "origin_island_id": 1, "units": {"steinewerfer": 3}})
        b, _ = self.bot(api, {1: {"steinewerfer": 5}})
        b.tick()
        self.assertEqual(api.trainings, [])

    def test_ohne_soll_passiert_nichts(self):
        api = FakeApi([insel(1, "40:40:1")])
        b, _ = self.bot(api, {})
        b.tick()
        self.assertEqual(api.trainings, [])

    def test_nur_so_viel_wie_bezahlbar_und_kolo_reserve_bleibt(self):
        api = FakeApi([insel(1, "40:40:1", res=(20400, 50000, 50000))])
        with open(config.KOLO_RESERVE_PATH, "w") as f:
            json.dump({"zeit": time.time(), "inseln": {"1": {"gold": 19525}}}, f)
        b, _ = self.bot(api, {1: {"speerkaempfer": 100}})
        b.tick()
        self.assertEqual(api.trainings, [(1, "speerkaempfer", 10)])   # (20400 - 19525) // 80

    def test_kein_mini_auftrag(self):
        api = FakeApi([insel(1, "40:40:1", res=(200, 50000, 50000))])
        b, _ = self.bot(api, {1: {"speerkaempfer": 100}})
        b.tick()
        self.assertEqual(api.trainings, [])
        self.assertIn("wartet auf Rohstoffe", b.bericht["1"]["hinweise"][0])


class PerHandel(Basis):
    def test_ueberschuss_der_naechsten_insel_kommt_per_handel(self):
        api = FakeApi([
            insel(1, "40:40:1", truppen={"speerkaempfer": 103}, schiffe={"kleines_kriegsschiff": 2}),
            insel(2, "10:10:1", truppen={"speerkaempfer": 50}, schiffe={"kleines_kriegsschiff": 5}),
            insel(7, "41:40:1", kaserne=0),
        ])
        b, _ = self.bot(api, {1: {"speerkaempfer": 100}, 2: {"speerkaempfer": 0}, 7: {"speerkaempfer": 3}})
        b.tick()
        self.assertEqual(len(api.created), 1)
        p = api.created[0]
        self.assertEqual((p["origin_island_id"], p["mission_type"], p["units"]), (1, "handel", {"speerkaempfer": 3}))
        self.assertEqual(p["ships"], {"kleines_kriegsschiff": 1})
        b.tick()
        self.assertEqual(len(api.created), 1)          # im Anflug zaehlt mit

    def test_ohne_soll_gibt_eine_insel_nichts_ab(self):
        api = FakeApi([insel(1, "40:40:1", kaserne=0, truppen={"speerkaempfer": 103},
                             schiffe={"kleines_kriegsschiff": 2}),
                       insel(7, "41:40:1", kaserne=0)])
        b, _ = self.bot(api, {7: {"speerkaempfer": 3}})
        b.tick()
        self.assertEqual(api.created, [])
        self.assertIn("keine Insel", b.bericht["7"]["hinweise"][0])

    def test_kein_ueberschuss_dann_bildet_die_naechste_kaserne_mit_aus(self):
        api = FakeApi([insel(1, "40:40:1", truppen={"speerkaempfer": 100}, schiffe={"kleines_kriegsschiff": 2}),
                       insel(7, "41:40:1", kaserne=0)])
        b, _ = self.bot(api, {1: {"speerkaempfer": 100}, 7: {"speerkaempfer": 5}})
        b.tick()
        self.assertEqual(api.trainings, [(1, "speerkaempfer", 5)])
        self.assertEqual(api.created, [])
        # Naechster Takt: die fuer 7 ausgebildeten zaehlen auf 1 als Ueberschuss -
        # verlegt wird sofort aus dem Bestand daheim.
        b.tick()
        self.assertEqual(api.created[0]["units"], {"speerkaempfer": 5})
        self.assertEqual(len(api.trainings), 1)

    def test_ohne_kriegsschiffe_werden_sie_reserviert(self):
        api = FakeApi([insel(1, "40:40:1", truppen={"speerkaempfer": 120}),
                       insel(7, "41:40:1", kaserne=0)])
        b, _ = self.bot(api, {1: {"speerkaempfer": 100}, 7: {"speerkaempfer": 10}})
        b.tick()
        self.assertEqual(api.created, [])
        with open(config.RESERVE_PATH) as f:
            reserve = json.load(f)["kriegsschiffe"]
        self.assertEqual(reserve, {"1": {"grosses_kriegsschiff": 1, "kleines_kriegsschiff": 2}})

    def test_bedrohte_insel_gibt_nichts_ab(self):
        api = FakeApi([insel(1, "40:40:1", kaserne=0, truppen={"speerkaempfer": 120},
                             schiffe={"kleines_kriegsschiff": 2}, bedroht=True),
                       insel(7, "41:40:1", kaserne=0)])
        b, _ = self.bot(api, {1: {"speerkaempfer": 100}, 7: {"speerkaempfer": 10}})
        b.tick()
        self.assertEqual(api.created, [])


class Soll(Basis):
    def test_leer_heisst_keine_vorgabe(self):
        _, st = self.bot(FakeApi([]), {1: {"speerkaempfer": "20", "steinewerfer": ""}})
        self.assertEqual(st.soll(1), {"steinewerfer": None, "speerkaempfer": 20, "bogenschuetze": None})
        with self.assertRaises(ValueError):
            st.soll_setzen(1, {"speerkaempfer": -1})


if __name__ == "__main__":
    unittest.main()
