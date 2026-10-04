"""Ablauftests fuer den Kolonisations-Bot - gegen eine nachgebaute Spiel-API.

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

import config  # noqa: E402
from api_client import ApiError  # noqa: E402
from kolonisation import Kolonisierer, naechster_name  # noqa: E402
from state import State  # noqa: E402

ZUKUNFT = "2030-01-01T00:00:00Z"
VERGANGEN = "2000-01-01T00:00:00Z"
KOSTEN = {"gold": 19525, "stein": 4260, "holz": 35500}


def insel(iid, koord, hafen=20, kap=50000, res=(0, 0, 0), schiffe=0, bedroht=False):
    return {"id": iid, "name": f"I{iid}", "koordinaten": koord, "gebaeude": {"hafen": hafen},
            "rohstoffe": {"gold": res[0], "stein": res[1], "holz": res[2], "kapazitaet": kap},
            "schiffe": {config.SCHIFF: schiffe} if schiffe else {}, "truppen": {},
            "bedrohung_im_anflug": bedroht}


class FakeApi:
    def __init__(self, inseln):
        self.ov = inseln
        self.besitzer = {}          # koord -> Spieler
        self.fleets, self.created, self.recalled, self.trainings = [], [], [], []
        self.training = {}          # iid -> [auftraege]
        self.nid = 500

    def get_me(self):
        return {"name": "Lucky_Jns"}

    def get_islands_overview(self):
        return json.loads(json.dumps(self.ov))

    def get_fleets(self):
        return json.loads(json.dumps(self.fleets))

    def get_island_info(self, x, y, z):
        return {"name": "Unbewohnt", "besitzer": self.besitzer.get(f"{x}:{y}:{z}")}

    def get_training(self, iid):
        return {"auftraege": self.training.get(iid, []),
                "katalog": {"hafen": [{"typ": config.SCHIFF, "ab_stufe": 20, "kosten": KOSTEN}]}}

    def start_training(self, iid, facility, item, count):
        i = next(i for i in self.ov if i["id"] == iid)
        for r, n in KOSTEN.items():
            if i["rohstoffe"][r] < n:
                raise ApiError(409, "zu_wenig", "zu wenig Rohstoffe")
            i["rohstoffe"][r] -= n
        self.trainings.append((iid, facility, item, count))
        self.nid += 1
        self.training.setdefault(iid, []).append({"id": self.nid, "item_typ": item, "anzahl": count,
                                                  "abgeschlossen": 0, "status": "aktiv"})
        return {"id": self.nid}

    def fertig(self, iid):
        """Ausbildung abgeschlossen: Schiff liegt im Hafen."""
        self.training[iid] = []
        i = next(i for i in self.ov if i["id"] == iid)
        i["schiffe"][config.SCHIFF] = i["schiffe"].get(config.SCHIFF, 0) + 1

    def create_fleet(self, p):
        i = next(i for i in self.ov if i["id"] == p["origin_island_id"])
        if i["schiffe"].get(config.SCHIFF, 0) < 1:
            raise ApiError(409, "keine_schiffe", "kein Schiff")
        i["schiffe"][config.SCHIFF] -= 1
        self.created.append(p)
        self.nid += 1
        f = {"id": self.nid, "mission": "attack", "origin_island_id": i["id"], "target": p["target"],
             "ships": p["ships"], "state": "outbound", "arrive_at": ZUKUNFT, "recallable_until": ZUKUNFT}
        self.fleets.append(f)
        return f

    def recall_fleet(self, fid):
        self.recalled.append(fid)

    def rename_island(self, iid, name):
        self.umbenannt = getattr(self, "umbenannt", []) + [(iid, name)]
        next(i for i in self.ov if i["id"] == iid)["name"] = name


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._reserve = config.RESERVE_PATH
        config.RESERVE_PATH = os.path.join(self.tmp, "reserve.json")

    def tearDown(self):
        config.RESERVE_PATH = self._reserve

    def bot(self, api):
        st = State(os.path.join(self.tmp, "state.json"))
        return Kolonisierer(api, st), st

    def reserve(self):
        with open(config.RESERVE_PATH) as f:
            return json.load(f)["inseln"]


class Zuordnung(Basis):
    def test_naechste_geeignete_insel_spart_und_reserviert(self):
        api = FakeApi([insel(1, "10:10:1"), insel(2, "40:40:1"), insel(3, "41:40:1", hafen=9)])
        bot, st = self.bot(api)
        e = st.neuer_eintrag(42, 40, 1, "Ziel")
        bot.tick()
        self.assertEqual((e["status"], e["bau_insel"]), ("spart", 2))   # 3 ist naeher, aber Hafen 9
        self.assertEqual(self.reserve(), {"2": KOSTEN})

    def test_zu_kleines_lager_ist_ungeeignet(self):
        api = FakeApi([insel(1, "40:40:1", kap=20000), insel(2, "10:10:1")])
        bot, st = self.bot(api)
        e = st.neuer_eintrag(41, 40, 1, "Ziel")
        bot.tick()
        self.assertEqual(e["bau_insel"], 2)
        self.assertIn("Lager", bot.inseln[1]["ungeeignet"])

    def test_parallel_auf_verschiedenen_inseln(self):
        api = FakeApi([insel(1, "10:10:1"), insel(2, "40:40:1")])
        bot, st = self.bot(api)
        a = st.neuer_eintrag(41, 40, 1, "A")
        b = st.neuer_eintrag(42, 40, 1, "B")
        bot.tick()
        self.assertEqual((a["bau_insel"], b["bau_insel"]), (2, 1))
        self.assertEqual(set(self.reserve()), {"1", "2"})

    def test_keine_geeignete_insel(self):
        api = FakeApi([insel(1, "10:10:1", hafen=9)])
        bot, st = self.bot(api)
        e = st.neuer_eintrag(41, 40, 1, "A")
        bot.tick()
        self.assertEqual(e["status"], "wartet")
        self.assertEqual(self.reserve(), {})


class Ablauf(Basis):
    def test_vom_sparen_bis_zur_kolonie(self):
        api = FakeApi([insel(1, "40:40:1")])
        bot, st = self.bot(api)
        e = st.neuer_eintrag(41, 40, 1, "Ziel")
        bot.tick()
        self.assertEqual(e["status"], "spart")
        api.ov[0]["rohstoffe"].update(gold=20000, stein=5000, holz=36000)
        bot.tick()
        self.assertEqual(e["status"], "baut")
        self.assertEqual(api.trainings, [(1, "hafen", config.SCHIFF, 1)])
        self.assertEqual(self.reserve(), {})          # bezahlt - nichts mehr zurueckzuhalten
        bot.tick()
        self.assertEqual(len(api.trainings), 1)        # nicht doppelt bauen
        api.fertig(1)
        bot.tick()
        self.assertEqual(e["status"], "unterwegs")
        self.assertEqual(api.created[0]["ships"], {config.SCHIFF: 1})
        self.assertEqual(api.created[0]["mission_type"], "attack")
        # Angekommen: die Insel gehoert jetzt uns.
        api.fleets = []
        api.ov.append(insel(9, "41:40:1", hafen=0, kap=1000))
        bot.tick()
        self.assertEqual(st.warteschlange, [])
        self.assertEqual(st.data["verlauf"][0]["ergebnis"], "kolonisiert")

    def test_freies_schiff_im_hafen_wird_zuerst_genommen(self):
        api = FakeApi([insel(1, "40:40:1"), insel(2, "10:10:1", schiffe=1)])
        bot, st = self.bot(api)
        e = st.neuer_eintrag(41, 40, 1, "Ziel")
        bot.tick()
        self.assertEqual(e["status"], "unterwegs")
        self.assertEqual(api.created[0]["origin_island_id"], 2)
        self.assertEqual(api.trainings, [])

    def test_bedrohte_insel_schickt_nicht_los(self):
        api = FakeApi([insel(1, "40:40:1", schiffe=1, bedroht=True)])
        bot, st = self.bot(api)
        e = st.neuer_eintrag(41, 40, 1, "Ziel")
        bot.tick()
        self.assertEqual(e["status"], "bereit")
        self.assertEqual(api.created, [])
        api.ov[0]["bedrohung_im_anflug"] = False
        bot.tick()
        self.assertEqual(e["status"], "unterwegs")


class Besiedelt(Basis):
    def test_wartendes_ziel_mit_besitzer_fliegt_raus(self):
        api = FakeApi([insel(1, "40:40:1")])
        bot, st = self.bot(api)
        st.neuer_eintrag(41, 40, 1, "Ziel")
        api.besitzer["41:40:1"] = "Fremder"
        bot.tick()
        self.assertEqual(st.warteschlange, [])
        self.assertEqual(st.data["verlauf"][0]["ergebnis"], "besiedelt")
        self.assertEqual(self.reserve(), {})

    def test_unterwegs_besiedelt_wird_zurueckgerufen(self):
        api = FakeApi([insel(1, "40:40:1", schiffe=1)])
        bot, st = self.bot(api)
        e = st.neuer_eintrag(41, 40, 1, "Ziel")
        bot.tick()
        self.assertEqual(e["status"], "unterwegs")
        api.besitzer["41:40:1"] = "Fremder"
        e["letzte_pruefung"] = 0
        bot.tick()
        self.assertEqual(api.recalled, [e["flotte_id"]])
        self.assertEqual(st.warteschlange, [])

    def test_schiff_nimmt_nach_rueckruf_das_naechste_ziel(self):
        api = FakeApi([insel(1, "40:40:1")])
        bot, st = self.bot(api)
        a = st.neuer_eintrag(41, 40, 1, "A")
        b = st.neuer_eintrag(42, 40, 1, "B")
        api.ov[0]["rohstoffe"].update(gold=20000, stein=5000, holz=36000)
        bot.tick()
        self.assertEqual((a["status"], b["status"]), ("baut", "wartet"))
        api.besitzer["41:40:1"] = "Fremder"
        a["letzte_pruefung"] = 0
        bot.tick()
        self.assertEqual(b["status"], "baut")       # uebernimmt das Schiff in Ausbildung
        self.assertEqual(len(api.trainings), 1)

    def test_eigene_insel_ist_kein_fremder(self):
        api = FakeApi([insel(1, "40:40:1")])
        bot, st = self.bot(api)
        st.neuer_eintrag(41, 40, 1, "Ziel")
        api.besitzer["41:40:1"] = "Lucky_Jns"
        bot.tick()
        self.assertEqual(st.data["verlauf"][0]["ergebnis"], "kolonisiert")


class Umbenennen(Basis):
    def test_muster(self):
        namen = ["GiG", "GiG 2", "GiG 7", "Fremd 9"]
        self.assertEqual(naechster_name("GiG {n}", namen), "GiG 8")
        self.assertEqual(naechster_name("GiG {n}", ["GiG"]), "GiG 2")
        self.assertEqual(naechster_name("Kolonie-{n}", namen), "Kolonie-1")

    def test_nur_neue_inseln_werden_umbenannt(self):
        api = FakeApi([insel(1, "40:40:1"), insel(2, "40:40:2")])
        api.ov[0]["name"], api.ov[1]["name"] = "GiG", "GiG 2"
        bot, st = self.bot(api)
        bot.tick()                                   # erster Lauf: nur erfassen
        self.assertEqual(getattr(api, "umbenannt", []), [])
        api.ov.append(insel(9, "41:40:1"))
        api.ov[2]["name"] = "Unbewohnte Insel"
        bot.tick()
        self.assertEqual(api.umbenannt, [(9, "GiG 3")])
        bot.tick()
        self.assertEqual(len(api.umbenannt), 1)

    def test_ausgeschaltet(self):
        api = FakeApi([insel(1, "40:40:1")])
        bot, st = self.bot(api)
        bot.tick()
        st.data["umbenennen"]["aktiv"] = False
        api.ov.append(insel(9, "41:40:1"))
        bot.tick()
        self.assertEqual(getattr(api, "umbenannt", []), [])


class Reihenfolge(Basis):
    def test_verschieben(self):
        api = FakeApi([insel(1, "40:40:1")])
        _, st = self.bot(api)
        a = st.neuer_eintrag(41, 40, 1, "A")
        b = st.neuer_eintrag(42, 40, 1, "B")
        st.verschieben(b["id"], -1)
        self.assertEqual([e["id"] for e in st.warteschlange], [b["id"], a["id"]])

    def test_angehalten_reserviert_nichts(self):
        api = FakeApi([insel(1, "40:40:1")])
        bot, st = self.bot(api)
        st.neuer_eintrag(41, 40, 1, "A")
        st.data["laeuft"] = False
        bot.tick()
        self.assertEqual(self.reserve(), {})
        self.assertEqual(api.trainings, [])


if __name__ == "__main__":
    unittest.main()
