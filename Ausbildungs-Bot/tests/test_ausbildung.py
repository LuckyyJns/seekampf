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

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

import config  # noqa: E402
from api_client import ApiError  # noqa: E402
from ausbildung import Ausbilder  # noqa: E402
from state import State  # noqa: E402

KASERNE = [
    {"typ": "steinewerfer", "ab_stufe": 1, "kosten": {"gold": 35, "stein": 28, "holz": 14}},
    {"typ": "speerkaempfer", "ab_stufe": 5, "kosten": {"gold": 80, "stein": 45, "holz": 55}},
    {"typ": "bogenschuetze", "ab_stufe": 10, "kosten": {"gold": 165, "stein": 30, "holz": 90}},
]
HAFEN = [
    {"typ": "spaehschiff", "name": "Spaehschiff", "ab_stufe": 1, "kaempfer_kap": 0, "dauer_s": 360,
     "kosten": {"gold": 165, "stein": 36, "holz": 300}},
    {"typ": "kleines_kriegsschiff", "name": "Kleines Kriegsschiff", "ab_stufe": 5, "kaempfer_kap": 8, "dauer_s": 1620,
     "kosten": {"gold": 715, "stein": 156, "holz": 1300}},
    {"typ": "grosses_kriegsschiff", "name": "Grosses Kriegsschiff", "ab_stufe": 12, "kaempfer_kap": 30, "dauer_s": 6480,
     "kosten": {"gold": 2475, "stein": 540, "holz": 4500}},
]


def insel(iid, koord, kaserne=20, res=(100000, 100000, 100000), truppen=None, schiffe=None, bedroht=False, hafen=0):
    return {"id": iid, "name": f"I{iid}", "koordinaten": koord, "gebaeude": {"kaserne": kaserne, "hafen": hafen},
            "rohstoffe": {"gold": res[0], "stein": res[1], "holz": res[2], "kapazitaet": 200000},
            "truppen": dict(truppen or {}), "schiffe": dict(schiffe or {}), "bedrohung_im_anflug": bedroht}


class FakeApi:
    def __init__(self, inseln):
        self.ov = inseln
        self.fleets, self.trainings, self.created, self.facilities = [], [], [], []
        self.auftraege = {}

    def get_islands_overview(self):
        return json.loads(json.dumps(self.ov))

    def get_fleets(self):
        return json.loads(json.dumps(self.fleets))

    def get_training(self, iid):
        i = next(i for i in self.ov if i["id"] == iid)
        stufe = i["gebaeude"]["kaserne"]
        hafen = i["gebaeude"].get("hafen", 0)
        return {"auftraege": self.auftraege.get(iid, []),
                "katalog": {"kaserne": [dict(k, baubar=stufe >= k["ab_stufe"]) for k in KASERNE],
                            "hafen": [dict(k, baubar=hafen >= k["ab_stufe"]) for k in HAFEN]}}

    def start_training(self, iid, facility, item, count):
        self.trainings.append((iid, item, count))
        self.facilities.append(facility)
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

    def bot(self, api, soll: dict, standard: dict | None = None):
        st = State(os.path.join(self.tmp, "state.json"))
        if standard:
            st.standard_setzen(standard)
        for iid, werte in soll.items():
            st.insel_setzen(iid, werte)
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
        erwartet = {p: None for p in config.ITEMS}
        erwartet["speerkaempfer"] = 20
        self.assertEqual(st.soll(1), erwartet)
        with self.assertRaises(ValueError):
            st.soll_setzen(1, {"speerkaempfer": -1})


class Standard(Basis):
    def test_standard_gilt_fuer_inseln_ohne_eigenen_wert(self):
        api = FakeApi([insel(1, "40:40:1"), insel(2, "50:50:1", truppen={"steinewerfer": 3})])
        b, _ = self.bot(api, {}, standard={"steinewerfer": 10})
        b.tick()
        self.assertEqual(sorted(api.trainings), [(1, "steinewerfer", 10), (2, "steinewerfer", 7)])

    def test_eigener_wert_geht_vor_dem_standard(self):
        api = FakeApi([insel(1, "40:40:1"), insel(2, "50:50:1")])
        b, _ = self.bot(api, {2: {"steinewerfer": 4}}, standard={"steinewerfer": 10})
        b.tick()
        self.assertEqual(sorted(api.trainings), [(1, "steinewerfer", 10), (2, "steinewerfer", 4)])

    def test_nicht_verwalten_nimmt_den_posten_heraus(self):
        api = FakeApi([insel(1, "40:40:1"), insel(2, "50:50:1")])
        b, _ = self.bot(api, {2: {"aus": ["steinewerfer"]}}, standard={"steinewerfer": 10})
        b.tick()
        self.assertEqual(api.trainings, [(1, "steinewerfer", 10)])

    def test_inaktive_insel_macht_nicht_mit(self):
        api = FakeApi([insel(1, "40:40:1"), insel(2, "50:50:1")])
        b, _ = self.bot(api, {1: {"aktiv": False}}, standard={"steinewerfer": 10})
        b.tick()
        self.assertEqual(api.trainings, [(2, "steinewerfer", 10)])

    def test_ohne_standard_und_ohne_soll_passiert_nichts(self):
        api = FakeApi([insel(1, "40:40:1", hafen=20)])
        b, _ = self.bot(api, {})
        b.tick()
        self.assertEqual(api.trainings, [])

    def test_standard_und_insel_ueberschuss_gleichen_sich_aus(self):
        # Standard 100 fuer alle: die Insel ohne Kaserne bekommt den Ueberschuss der anderen.
        api = FakeApi([insel(1, "40:40:1", truppen={"speerkaempfer": 103}, schiffe={"kleines_kriegsschiff": 2}),
                       insel(7, "41:40:1", kaserne=0)])
        b, _ = self.bot(api, {7: {"speerkaempfer": 3}}, standard={"speerkaempfer": 100})
        b.tick()
        self.assertEqual(api.created[0]["units"], {"speerkaempfer": 3})


class Schiffe(Basis):
    def test_schiffe_werden_im_hafen_gebaut(self):
        api = FakeApi([insel(1, "40:40:1", hafen=6, schiffe={"kleines_kriegsschiff": 1})])
        b, _ = self.bot(api, {1: {"kleines_kriegsschiff": 3}})
        b.tick()
        self.assertEqual(api.trainings, [(1, "kleines_kriegsschiff", 2)])
        self.assertEqual(api.facilities, ["hafen"])
        b.tick()
        self.assertEqual(len(api.trainings), 1)        # in Ausbildung zaehlt mit

    def test_schiffe_auf_fahrt_zaehlen_mit(self):
        api = FakeApi([insel(1, "40:40:1", hafen=6, schiffe={"kleines_kriegsschiff": 1})])
        api.fleets.append({"mission": "attack", "state": "outbound", "origin_island_id": 1,
                           "ships": {"kleines_kriegsschiff": 2}, "units": {}})
        b, _ = self.bot(api, {1: {"kleines_kriegsschiff": 3}})
        b.tick()
        self.assertEqual(api.trainings, [])

    def test_zu_niedriger_hafen_gibt_einen_hinweis(self):
        api = FakeApi([insel(1, "40:40:1", hafen=2)])
        b, _ = self.bot(api, {1: {"kleines_kriegsschiff": 2}})
        b.tick()
        self.assertEqual(api.trainings, [])
        self.assertIn("Hafen zu niedrig", b.bericht["1"]["hinweise"][0])
        self.assertIn("Stufe 5", b.bericht["1"]["hinweise"][0])

    def test_einzelnes_schiff_wird_bestellt_obwohl_wenig_geld(self):
        api = FakeApi([insel(1, "40:40:1", hafen=6, res=(800, 200, 1400))])
        b, _ = self.bot(api, {1: {"kleines_kriegsschiff": 5}})
        b.tick()
        self.assertEqual(api.trainings, [(1, "kleines_kriegsschiff", 1)])

    def test_kolonisationsschiffe_sind_kein_posten(self):
        self.assertNotIn("kolonisationsschiff", config.ITEMS)
        _, st = self.bot(FakeApi([]), {})
        with self.assertRaises(ValueError):
            st.insel_setzen(1, {"aus": ["kolonisationsschiff"]})


class VonHand(Basis):
    def test_ausbilden_von_hand_nutzt_die_richtige_anlage(self):
        api = FakeApi([insel(1, "40:40:1", hafen=6)])
        b, st = self.bot(api, {})
        b.tick()
        b.ausbilden(1, "speerkaempfer", 12)
        b.ausbilden(1, "kleines_kriegsschiff", 2)
        self.assertEqual(api.trainings, [(1, "speerkaempfer", 12), (1, "kleines_kriegsschiff", 2)])
        self.assertEqual(api.facilities, ["kaserne", "hafen"])
        self.assertIn("von Hand", st.snapshot()["verlauf"][0]["text"])

    def test_ungueltige_angaben_werden_abgelehnt(self):
        api = FakeApi([insel(1, "40:40:1")])
        b, _ = self.bot(api, {})
        b.tick()
        for posten, n, insel_id in (("kolonisationsschiff", 1, 1), ("steinewerfer", 0, 1), ("steinewerfer", 1.5, 1),
                                    ("steinewerfer", 99999, 1), ("steinewerfer", 1, 42)):
            with self.assertRaises(ValueError):
                b.ausbilden(insel_id, posten, n)
        self.assertEqual(api.trainings, [])


class Status(Basis):
    def test_status_zeigt_alle_inseln_auch_ohne_soll(self):
        api = FakeApi([insel(1, "40:40:1", hafen=6, truppen={"steinewerfer": 4}, schiffe={"spaehschiff": 2}),
                       insel(2, "50:50:1")])
        api.auftraege[2] = [{"item_typ": "steinewerfer", "anzahl": 7, "abgeschlossen": 2, "status": "aktiv",
                             "finish_at": "2026-01-01T00:00:00Z"}]
        b, _ = self.bot(api, {})
        b.tick()
        st = b.status()
        self.assertEqual([i["id"] for i in st["inseln"]], [1, 2])
        i1, i2 = st["inseln"]
        self.assertEqual((i1["bestand"]["steinewerfer"], i1["bestand"]["spaehschiff"]), (4, 2))
        self.assertIn("kleines_kriegsschiff", i1["baubar"])
        self.assertNotIn("kleines_kriegsschiff", i2["baubar"])
        self.assertEqual(i2["in_ausbildung"]["steinewerfer"], 5)
        self.assertEqual(i2["auftraege"][0]["item"], "steinewerfer")
        self.assertEqual(st["katalog"]["kleines_kriegsschiff"]["anlage"], "hafen")
        self.assertEqual(st["katalog"]["steinewerfer"]["anlage"], "kaserne")
        self.assertNotIn("kolonisationsschiff", st["katalog"])

    def test_status_auch_wenn_der_bot_aus_ist(self):
        api = FakeApi([insel(1, "40:40:1", truppen={"steinewerfer": 4})])
        b, st = self.bot(api, {}, standard={"steinewerfer": 10})
        with st.lock:
            st.data["laeuft"] = False
        b.tick()
        self.assertEqual(api.trainings, [])
        self.assertEqual(b.status()["inseln"][0]["soll_eff"]["steinewerfer"], 10)


class AlterZustand(Basis):
    def test_alte_state_datei_wird_gelesen(self):
        pfad = os.path.join(self.tmp, "state.json")
        with open(pfad, "w") as f:
            json.dump({"laeuft": False, "inseln": {"5": {"soll": {"speerkaempfer": 200, "steinewerfer": None}}},
                       "verlauf": [{"zeit": 1, "text": "alt"}]}, f)
        st = State(pfad)
        self.assertFalse(st.data["laeuft"])
        self.assertEqual(st.insel(5)["soll"]["speerkaempfer"], 200)
        self.assertEqual((st.insel(5)["aus"], st.insel(5)["aktiv"]), ([], True))
        self.assertEqual(st.standard(), {p: None for p in config.ITEMS})
        self.assertEqual(st.soll_eff(5)["speerkaempfer"], 200)

    def test_werte_werden_geprueft(self):
        _, st = self.bot(FakeApi([]), {})
        for schlecht in ({"steinewerfer": -1}, {"aktiv": "ja"}, {"aus": "steinewerfer"}):
            with self.assertRaises(ValueError):
                st.insel_setzen(1, schlecht)
        with self.assertRaises(ValueError):
            st.standard_setzen({"steinewerfer": "abc"})


class VolleWarteschlange(Basis):
    def voll(self, api, iid=1, n=5):
        api.auftraege[iid] = [{"item_typ": "steinewerfer", "anzahl": 10, "abgeschlossen": 0, "status": "aktiv"}
                              for _ in range(n)]

    def test_bei_voller_kaserne_wird_nicht_bestellt(self):
        api = FakeApi([insel(1, "40:40:1")])
        self.voll(api)
        b, _ = self.bot(api, {1: {"speerkaempfer": 10}})
        b.tick()
        self.assertEqual(api.trainings, [])
        self.assertIn("voll", " ".join(b.status()["inseln"][0]["hinweise"]))

    def test_vier_auftraege_lassen_noch_einen_zu(self):
        api = FakeApi([insel(1, "40:40:1")])
        self.voll(api, n=4)
        b, _ = self.bot(api, {1: {"speerkaempfer": 10}})
        b.tick()
        self.assertEqual(api.trainings, [(1, "speerkaempfer", 10)])

    def test_hafen_und_kaserne_haben_getrennte_warteschlangen(self):
        api = FakeApi([insel(1, "40:40:1", hafen=20)])
        self.voll(api)                                 # Kaserne voll
        b, _ = self.bot(api, {1: {"speerkaempfer": 10, "spaehschiff": 1}})
        b.tick()
        self.assertEqual([t[1] for t in api.trainings], ["spaehschiff"])

    def test_mehr_als_fuenf_auftraege_pro_tick_gibt_es_nicht(self):
        api = FakeApi([insel(1, "40:40:1", hafen=20)])
        self.voll(api, n=4)
        b, _ = self.bot(api, {1: {"steinewerfer": 5, "speerkaempfer": 5, "bogenschuetze": 5}})
        b.tick()
        self.assertEqual(len(api.trainings), 1)        # nur noch ein Platz in der Kaserne

    def test_veraltete_zaehlung_queue_full_wird_nicht_wiederholt(self):
        api = FakeApi([insel(1, "40:40:1")])
        aufrufe = []

        def voll(iid, facility, item, count):
            aufrufe.append(item)
            raise ApiError(409, "queue_full", "Ausbildungs-Warteschlange voll (max. 5).")
        api.start_training = voll
        b, _ = self.bot(api, {1: {"steinewerfer": 5, "speerkaempfer": 5, "bogenschuetze": 5}})
        b.tick()
        self.assertEqual(len(aufrufe), 1)              # danach ist die Kaserne dieser Insel gesperrt


class NetzfehlerBeiPost(Basis):
    def test_ausbildung_antwort_verloren_andere_inseln_kommen_trotzdem_dran(self):
        api = FakeApi([insel(1, "40:40:1"), insel(2, "10:10:1")])
        echt = api.start_training

        def erste_insel_netzfehler(iid, facility, item, count):
            echt(iid, facility, item, count)
            if iid == 1:
                raise requests.ReadTimeout("Antwort verloren")
            return {"id": 99}
        api.start_training = erste_insel_netzfehler
        b, _ = self.bot(api, {1: {"speerkaempfer": 5}, 2: {"speerkaempfer": 5}})
        b.tick()                                       # darf nicht mit Ausnahme enden
        self.assertEqual(sorted(t[0] for t in api.trainings), [1, 2])
        b.tick()                                       # Auftrag steht in der Ausbildung -> nicht doppelt
        self.assertEqual(len(api.trainings), 2)

    def test_verlegen_antwort_verloren_wirft_nicht(self):
        api = FakeApi([
            insel(1, "40:40:1", truppen={"speerkaempfer": 103}, schiffe={"kleines_kriegsschiff": 2}),
            insel(7, "41:40:1", kaserne=0),
        ])

        def netzfehler(p):
            raise requests.ConnectionError("weg")
        api.create_fleet = netzfehler
        b, _ = self.bot(api, {1: {"speerkaempfer": 100}, 7: {"speerkaempfer": 3}})
        b.tick()                                       # keine Ausnahme
        self.assertEqual(api.created, [])


if __name__ == "__main__":
    unittest.main()
