"""Ablauftests fuer den Flotten-Manager - gegen eine nachgebaute Spiel-API.

    .venv/bin/python -m unittest discover -s tests
"""
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

import config  # noqa: E402
from api_client import ApiError  # noqa: E402
from manager import FlottenManager  # noqa: E402
from state import State  # noqa: E402

ZUKUNFT = "2030-01-01T00:00:00Z"
VERGANGEN = "2000-01-01T00:00:00Z"


def insel(iid, name, koord, res, schiffe=None, truppen=None, bedroht=False):
    gold, stein, holz, kap = res
    return {"id": iid, "name": name, "koordinaten": koord, "schiffe": dict(schiffe or {}),
            "truppen": dict(truppen or {}), "bedrohung_im_anflug": bedroht,
            "rohstoffe": {"gold": gold, "stein": stein, "holz": holz, "kapazitaet": kap}}


class FakeApi:
    """Gerade genug Seekampf-API: Inseln, Flotten, Karte, Berichte."""

    def __init__(self, inseln, ziele=None):
        self.ov = inseln
        self.fleets, self.created, self.recalled = [], [], []
        self.ziele = ziele or []          # [(koord, anfaengerschutz)]
        self.besitzer = {}                # koord -> Spielername (besiedelt)
        self.berichte, self.archiviert, self.abfragen = [], [], []
        self.nid = 1000

    def get_me(self):
        return {"current_island_id": self.ov[0]["id"], "allianz": {"id": 9}}

    def get_islands_overview(self):
        return json.loads(json.dumps(self.ov))

    def get_fleets(self):
        return json.loads(json.dumps(self.fleets))

    def get_region(self, x, y):
        return {"inseln": [{"koordinaten": k, "x": int(k.split(":")[0]), "y": int(k.split(":")[1]),
                            "z": int(k.split(":")[2]), "besitzer": None, "name": k} for k, _ in self.ziele]}

    def get_island_info(self, x, y, z):
        self.abfragen.append(f"{x}:{y}:{z}")
        return {"besitzer": self.besitzer.get(f"{x}:{y}:{z}")}

    def get_combat_messages(self, limit=50):
        return list(self.berichte)

    def archive_message(self, i):
        self.archiviert.append(i)

    def recall_fleet(self, i):
        self.recalled.append(i)

    def create_fleet(self, p):
        if p["mission_type"] == "attack":
            koord = f"{p['target']['x']}:{p['target']['y']}:{p['target']['z']}"
            if dict(self.ziele).get(koord):
                raise ApiError(409, "newbie_protection", "Anfaengerschutz")
        self.created.append(p)
        self.nid += 1
        herkunft = next(i for i in self.ov if i["id"] == p["origin_island_id"])
        for t, n in {**p.get("ships", {}), **p.get("units", {})}.items():
            topf = "schiffe" if t in herkunft["schiffe"] else "truppen"
            herkunft[topf][t] = herkunft[topf].get(t, 0) - n
        f = {"id": self.nid, "mission": "attack" if p["mission_type"] == "attack" else "transport",
             "origin_island_id": p["origin_island_id"], "target": dict(p["target"], name="Ziel"),
             "ships": p.get("ships", {}), "units": p.get("units", {}), "resources": p.get("resources", {}),
             "state": "outbound", "arrive_at": ZUKUNFT, "return_at": None, "recallable_until": ZUKUNFT}
        self.fleets.append(f)
        return f


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._alt = (config.UPGRADE_STATUS_PATH, config.KOLO_RESERVE_PATH, config.AUSBILDUNG_RESERVE_PATH)
        config.UPGRADE_STATUS_PATH = os.path.join(self.tmp, "upgrade-status.json")
        config.KOLO_RESERVE_PATH = os.path.join(self.tmp, "kolo-reserve.json")
        config.AUSBILDUNG_RESERVE_PATH = os.path.join(self.tmp, "ausbildung-reserve.json")

    def tearDown(self):
        config.UPGRADE_STATUS_PATH, config.KOLO_RESERVE_PATH, config.AUSBILDUNG_RESERVE_PATH = self._alt

    def reserve(self, pfad, feld, werte, alter_s=0):
        with open(pfad, "w") as f:
            json.dump({"zeit": time.time() - alter_s, feld: werte}, f)

    def manager(self, api, vorher: dict | None = None):
        pfad = os.path.join(self.tmp, "state.json")
        if vorher is not None:
            with open(pfad, "w") as f:
                json.dump(vorher, f)
        state = State(pfad)
        m = FlottenManager(api, state)
        m.start()
        return m, state

    def upgrade_plan(self, plaene: dict):
        with open(config.UPGRADE_STATUS_PATH, "w") as f:
            json.dump({"zeit": time.time(), "inseln": {k: {"plan": v} for k, v in plaene.items()}}, f)


class Raids(Basis):
    def test_handelsfahrt_ist_kein_raid(self):
        api = FakeApi([insel(447, "GiG", "53:49:8", (0, 0, 0, 1000))])
        api.fleets.append({"id": 1, "mission": "transport", "origin_island_id": 447,
                           "target": {"x": 50, "y": 50, "z": 1, "name": "Allianz"}, "ships": {},
                           "resources": {"holz": 100}, "state": "outbound", "arrive_at": VERGANGEN})
        m, st = self.manager(api)
        m.tick()
        self.assertEqual(st.data["stats"]["raids"], 0)
        self.assertFalse(st.data.get("offene_berichte"))

    def test_rueckruf_nur_fuer_raids(self):
        api = FakeApi([insel(447, "GiG", "53:49:8", (0, 0, 0, 1000), {"kleines_kriegsschiff": 1,
                       "kleines_handelsschiff": 1}, {"steinewerfer": 1})], ziele=[("53:48:1", False)])
        api.fleets.append({"id": 1, "mission": "transport", "origin_island_id": 447,
                           "target": {"x": 50, "y": 50, "z": 1}, "ships": {}, "state": "outbound",
                           "arrive_at": ZUKUNFT, "recallable_until": ZUKUNFT})
        m, _ = self.manager(api)
        m.tick()  # schickt einen Raid los
        raid = next(f["id"] for f in api.fleets if f["mission"] == "attack")
        api.ov[0]["bedrohung_im_anflug"] = True
        m._overview_zeit = 0
        m.tick()
        self.assertEqual(api.recalled, [raid])

    def test_anfaengerschutz_sperrt_ziel_und_nimmt_das_naechste(self):
        api = FakeApi([insel(447, "GiG", "53:49:8", (0, 0, 0, 1000), {"kleines_kriegsschiff": 1,
                       "kleines_handelsschiff": 1}, {"steinewerfer": 1})],
                      ziele=[("53:48:1", False), ("53:48:2", True)])  # :2 liegt naeher, kommt zuerst
        m, st = self.manager(api)
        m.tick()
        ziele = st.insel(447)["ziele"]
        self.assertEqual(ziele["53:48:2"]["blacklist_grund"], "Anfaengerschutz")
        self.assertEqual([p["target"]["z"] for p in api.created], [1])


class InselEinstellungen(Basis):
    def zwei_inseln(self):
        schiffe = {"kleines_kriegsschiff": 4, "kleines_handelsschiff": 4}
        return FakeApi([insel(447, "GiG", "53:49:8", (0, 0, 0, 1000), dict(schiffe), {"steinewerfer": 8}),
                        insel(20, "GiG 2", "46:55:6", (0, 0, 0, 1000), dict(schiffe), {"steinewerfer": 8})],
                       ziele=[("53:48:1", False), ("53:48:2", False), ("53:48:3", False), ("53:48:4", False)])

    def test_eigene_werte_ueberlagern_die_globalen(self):
        m, st = self.manager(self.zwei_inseln())
        st.update_settings({"flotte_einheiten": 1})
        st.update_insel_settings(20, {"flotte_einheiten": "3", "flotte_handelsschiffe": "2", "max_flotten": ""})
        self.assertEqual(m.flotte_units(20), {"steinewerfer": 3})
        self.assertEqual(m.flotte_units(447), {"steinewerfer": 1})
        self.assertEqual(st.insel(20)["einstellungen"], {"flotte_einheiten": 3, "flotte_handelsschiffe": 2})
        # leer = wieder global
        st.update_insel_settings(20, {"flotte_einheiten": ""})
        self.assertEqual(m.flotte_units(20), {"steinewerfer": 1})

    def test_unbekannte_und_globale_schluessel_werden_ignoriert(self):
        m, st = self.manager(self.zwei_inseln())
        st.update_insel_settings(447, {"tick_sekunden": 1, "quatsch": 5, "max_flotten": "x"})
        self.assertEqual(st.insel(447)["einstellungen"], {})

    def test_max_flotten_je_insel(self):
        api = self.zwei_inseln()
        m, st = self.manager(api)
        with st.lock:
            st.insel(447)["aktiv"] = True
        st.update_insel_settings(447, {"max_flotten": 2})
        m.tick()
        self.assertEqual(len([p for p in api.created if p["origin_island_id"] == 447]), 2)

    def test_kriegsschiffe_fuer_truppentransport_bleiben_daheim(self):
        api = self.zwei_inseln()
        self.reserve(config.AUSBILDUNG_RESERVE_PATH, "kriegsschiffe", {"447": {"kleines_kriegsschiff": 3}})
        m, st = self.manager(api)
        self.assertEqual(m._freie_flotten(447, api.ov[0]), 1)
        self.assertEqual(m._freie_flotten(20, api.ov[1]), 4)

    def test_einstellungen_ueberstehen_neustart(self):
        m, st = self.manager(self.zwei_inseln())
        st.update_insel_settings(447, {"scan_radius_sektoren": 3})
        st2 = State(st.path)
        self.assertEqual(st2.insel_settings(447)["scan_radius_sektoren"], 3)


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class Unterwegs(Basis):
    """Ziel wird waehrend der Fahrt besiedelt."""

    def setUp(self):
        super().setUp()
        self.api = FakeApi([insel(447, "GiG", "53:49:8", (0, 0, 0, 1000))], ziele=[("53:48:1", False)])
        self.m, self.st = self.manager(self.api)
        self.st.data["laeuft"] = False      # nur pruefen, keine neuen Raids
        self.jetzt = time.time()

    def raid(self, rueckruf_in: float, id_=7):
        f = {"id": id_, "mission": "attack", "origin_island_id": 447, "state": "outbound",
             "target": {"x": 53, "y": 48, "z": 1, "name": "53:48:1"}, "ships": {}, "units": {},
             "depart_at": iso(self.jetzt - 60), "arrive_at": iso(self.jetzt + 600),
             "recallable_until": iso(self.jetzt + rueckruf_in)}
        self.api.fleets.append(f)
        rec = self.m._record(f)
        rec["fremd"] = False
        self.st.data["flotten"][str(id_)] = rec
        return rec

    def test_besiedelt_unterwegs_wird_zurueckgerufen(self):
        self.raid(300)
        self.api.besitzer["53:48:1"] = "Puffhausen"
        self.m.tick()
        self.assertEqual(self.api.recalled, [7])
        self.assertNotIn("53:48:1", self.st.insel(447)["ziele"])

    def test_frei_bleibt_unterwegs_und_wird_nicht_dauernd_abgefragt(self):
        self.raid(300)
        self.m.tick()
        self.m.tick()
        self.assertEqual(self.api.recalled, [])
        self.assertEqual(self.api.abfragen, ["53:48:1"])   # einmal je pruef_intervall_s

    def test_letzte_pruefung_kurz_vor_ende_der_rueckrufzeit(self):
        rec = self.raid(20)                                 # nur noch 20 s rueckrufbar
        rec["geprueft_um"] = self.jetzt                     # Intervall-Pruefung gerade erst gelaufen
        self.api.besitzer["53:48:1"] = "Puffhausen"
        self.m.tick()
        self.assertEqual(self.api.recalled, [7])

    def test_nach_ende_der_rueckrufzeit_nichts_mehr(self):
        self.raid(-5)
        self.api.besitzer["53:48:1"] = "Puffhausen"
        self.m.tick()
        self.assertEqual(self.api.recalled, [])


class Postfach(Basis):
    def bericht(self, api, mid, sieg, name):
        api.berichte.append({"id": mid, "created_at": iso(time.time()), "payload": {
            "rolle": "angreifer", "sieg": sieg, "insel_koordinaten": "53:48:1", "insel_name": name,
            "loot": {"gold": 5}, "verluste": {"angreifer": {}}}})

    def lauf(self, sieg, name, besitzer=None):
        api = FakeApi([insel(447, "GiG", "53:49:8", (0, 0, 0, 1000))], ziele=[("53:48:1", False)])
        m, st = self.manager(api)
        st.data["laeuft"] = False
        if besitzer:
            api.besitzer["53:48:1"] = besitzer
        st.data["offene_berichte"] = [{"koordinaten": "53:48:1", "flotte": 9, "insel_id": 447,
                                       "arrive_at": time.time() - 30, "seit": time.time(), "fremd": False}]
        self.bericht(api, 500, sieg, name)
        m.tick()
        return api, st

    def test_sieg_auf_freier_insel_wird_ausgeblendet(self):
        api, _ = self.lauf(True, "53:48:1")
        self.assertEqual(api.archiviert, [500])

    def test_niederlage_bleibt_stehen(self):
        api, _ = self.lauf(False, "53:48:1")
        self.assertEqual(api.archiviert, [])

    def test_besiedelte_insel_bleibt_stehen_und_fliegt_raus(self):
        api, st = self.lauf(True, "Puffhausen 94", besitzer="Puffhausen")
        self.assertEqual(api.archiviert, [])
        self.assertNotIn("53:48:1", st.insel(447)["ziele"])

    def test_nur_neuer_name_aber_frei_ist_kein_alarm(self):
        api, st = self.lauf(True, "Neuer Name")
        self.assertEqual(api.archiviert, [500])
        self.assertEqual(st.insel(447)["ziele"]["53:48:1"]["name"], "Neuer Name")


class Zustand(Basis):
    def test_kaputte_datei_wird_beiseitegelegt_und_pausiert(self):
        pfad = os.path.join(self.tmp, "state.json")
        with open(pfad, "w") as f:
            f.write("{halb")
        st = State(pfad)
        self.assertIsNotNone(st.defekt)
        self.assertTrue(os.path.exists(st.defekt))
        self.assertFalse(st.data["laeuft"])

    def test_gleichzeitiges_speichern(self):
        st = State(os.path.join(self.tmp, "state.json"))
        fehler = []

        def los():
            for _ in range(25):
                try:
                    st.save()
                except Exception as e:  # noqa: BLE001
                    fehler.append(e)
        threads = [threading.Thread(target=los) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(fehler, [])
        self.assertEqual(os.listdir(self.tmp), ["state.json"])

    def test_alte_rollen_werden_uebernommen(self):
        alt = {"inseln": {"447": {"name": "GiG", "koordinaten": "53:49:8", "ziele": {}, "rotation": [],
                                  "ausgleich_rolle": "spender"},
                          "20": {"name": "GiG 2", "koordinaten": "46:55:6", "ziele": {}, "rotation": [],
                                 "ausgleich_rolle": "empfaenger"}}}
        with open(os.path.join(self.tmp, "state.json"), "w") as f:
            json.dump(alt, f)
        st = State(os.path.join(self.tmp, "state.json"))
        self.assertTrue(st.insel(447)["ausgleich"]["gibt"])
        self.assertFalse(st.insel(447)["ausgleich"]["bekommt"])
        self.assertTrue(st.insel(20)["ausgleich"]["bekommt"])
        self.assertNotIn("ausgleich_rolle", st.insel(20))


class Ausgleich(Basis):
    def drei_inseln(self):
        return FakeApi([
            insel(447, "GiG", "53:49:8", (12000, 30000, 4000, 50000),
                  {"kleines_handelsschiff": 10, "grosses_handelsschiff": 2}),
            insel(20, "GiG 2", "46:55:6", (900, 100, 1700, 1749), {"kleines_handelsschiff": 10}),
            insel(581, "GiG 3", "46:55:9", (900, 1000, 50, 1749)),
        ])

    def einstellen(self, st, **inseln):
        st.settings.update(ausgleich_aktiv=True, ausgleich_ziel=0.5, ausgleich_reserve=0.25,
                           ausgleich_min_menge=200)
        for iid, werte in inseln.items():
            st.insel(iid.lstrip("i"))["ausgleich"].update(werte)

    def lieferungen(self, api):
        return {(p["origin_island_id"], p["target"]["z"]): p["resources"] for p in api.created
                if p["mission_type"] == "handel"}

    def test_gegenseitig_und_ohne_hin_und_her(self):
        api = self.drei_inseln()
        self.upgrade_plan({"447": {"gebaeude": "kaserne", "fehlt": {"holz": 500}}})
        m, st = self.manager(api)
        self.einstellen(st, i447={"gibt": True}, i20={"gibt": True, "bekommt": True}, i581={"bekommt": True})
        m.tick()
        lief = self.lieferungen(api)
        self.assertIn("stein", lief[(447, 6)])            # GiG -> GiG 2: Stein
        self.assertEqual(set(lief[(20, 9)]), {"holz"})     # GiG 2 -> GiG 3: Holz (gibt UND bekommt)
        b = m.ausgleich.bericht["inseln"]
        self.assertEqual(b["447"]["ueberschuss"]["holz"], 0)    # fehlt GiG selbst fuer den Ausbau
        self.assertAlmostEqual(b["20"]["grenze"]["holz"], 1749 * 0.5)  # nie unter das eigene Soll
        vorher = len(api.created)
        m.tick()
        self.assertEqual(len(api.created), vorher)          # Unterwegs zaehlt mit: nichts doppelt

    def test_max_abgabe_je_lieferung(self):
        api = self.drei_inseln()
        m, st = self.manager(api)
        self.einstellen(st, i447={"gibt": True, "max_abgabe": 300}, i20={"bekommt": True})
        m.tick()
        self.assertLessEqual(sum(self.lieferungen(api)[(447, 6)].values()), 300)

    def test_eigene_grenzen_je_insel(self):
        api = self.drei_inseln()
        m, st = self.manager(api)
        self.einstellen(st, i447={"gibt": True, "reserve": 0.9}, i20={"bekommt": True, "ziel": 0.8})
        m.tick()
        b = m.ausgleich.bericht["inseln"]
        self.assertEqual(b["447"]["ueberschuss"]["stein"], 0)          # 30000 < 90 % von 50000
        self.assertEqual(b["20"]["bedarf"]["stein"], int(1749 * 0.8) - 100)

    def test_kolonisations_reserve_bleibt_beim_geber(self):
        api = self.drei_inseln()
        self.reserve(config.KOLO_RESERVE_PATH, "inseln", {"447": {"gold": 19525, "stein": 4260, "holz": 35500}})
        m, st = self.manager(api)
        self.einstellen(st, i447={"gibt": True}, i20={"bekommt": True})
        m.tick()
        b = m.ausgleich.bericht["inseln"]
        self.assertEqual(b["447"]["grenze"]["gold"], 19525)
        self.assertEqual(b["447"]["ueberschuss"]["gold"], 0)            # 12000 < 19525
        self.assertEqual(b["447"]["grenze"]["stein"], 12500)             # Reserve 25 % > 4260

    def test_veraltete_reserve_zaehlt_nicht(self):
        api = self.drei_inseln()
        self.reserve(config.KOLO_RESERVE_PATH, "inseln", {"447": {"gold": 19525}}, alter_s=3600)
        m, st = self.manager(api)
        self.einstellen(st, i447={"gibt": True}, i20={"bekommt": True})
        m.tick()
        self.assertEqual(m.ausgleich.bericht["inseln"]["447"]["grenze"]["gold"], 12500)

    def test_bedrohte_insel_bekommt_nichts(self):
        api = self.drei_inseln()
        api.ov[1]["bedrohung_im_anflug"] = True
        m, st = self.manager(api)
        self.einstellen(st, i447={"gibt": True}, i20={"bekommt": True})
        m.tick()
        self.assertEqual(self.lieferungen(api), {})

    def test_lieferung_von_hand(self):
        api = self.drei_inseln()
        m, st = self.manager(api)
        r = m.ausgleich.liefern_von_hand(447, 20, {"stein": 500, "holz": 100})
        p = api.created[-1]
        self.assertEqual((p["origin_island_id"], p["mission_type"]), (447, "handel"))
        self.assertEqual((p["target"]["x"], p["target"]["y"], p["target"]["z"]), (46, 55, 6))
        self.assertEqual(p["resources"], {"stein": 500, "holz": 100})
        self.assertEqual(p["ships"], {"kleines_handelsschiff": 8})      # 600 Ladung / 75
        self.assertEqual(r["rohstoffe"], {"stein": 500, "holz": 100})
        eintrag = m.ausgleich.stand()["lieferungen"][0]
        self.assertTrue(eintrag["von_hand"])
        self.assertEqual((eintrag["von"], eintrag["nach"]), (447, 20))
        self.assertEqual(m.ausgleich.stand()["geliefert"]["stein"], 500)

    def test_lieferung_von_hand_nimmt_grosse_schiffe_wenn_noetig(self):
        api = self.drei_inseln()
        m, _ = self.manager(api)
        m.ausgleich.liefern_von_hand(447, 20, {"stein": 1600})
        self.assertEqual(api.created[-1]["ships"], {"kleines_handelsschiff": 10, "grosses_handelsschiff": 2})

    def test_lieferung_von_hand_lehnt_unsinn_ab(self):
        api = self.drei_inseln()
        api.ov[2]["bedrohung_im_anflug"] = True
        m, _ = self.manager(api)
        for von, nach, menge in ((447, 447, {"stein": 10}), (447, 20, {}), (447, 20, {"stein": -5}),
                                 (447, 20, {"stein": 1.5}), (447, 20, {"gold": 99999}),   # mehr als im Lager
                                 (447, 20, {"stein": 5000}),                               # mehr als die Schiffe tragen
                                 (447, 9999, {"stein": 10}), (581, 20, {"gold": 1}),       # Ziel unbekannt / Start bedroht
                                 ("abc", 20, {"stein": 10})):
            with self.assertRaises(ValueError, msg=(von, nach, menge)):
                m.ausgleich.liefern_von_hand(von, nach, menge)
        self.assertEqual([p for p in api.created if p["mission_type"] == "handel"], [])

    def test_wartet_auf_schiffe_und_haelt_sie_zurueck(self):
        api = self.drei_inseln()
        api.ov[0]["schiffe"] = {"kleines_handelsschiff": 1}
        api.fleets.append({"id": 5, "mission": "attack", "origin_island_id": 447,
                           "target": {"x": 1, "y": 1, "z": 1}, "ships": {"grosses_handelsschiff": 3},
                           "state": "returning", "arrive_at": VERGANGEN})
        m, st = self.manager(api)
        self.einstellen(st, i447={"gibt": True}, i20={"bekommt": True})
        m.tick()
        self.assertEqual(self.lieferungen(api), {})
        self.assertIn(447, m.ausgleich.reserviert)
        self.assertEqual(m.vorrat_fuer_raids(447, api.ov[0]).get("kleines_handelsschiff"), 0)

    def test_neue_insel_erbt_einstellung(self):
        api = self.drei_inseln()
        m, st = self.manager(api)
        st.insel(581)["ausgleich"].update(gibt=True, bekommt=True, ziel=0.6)
        api.ov.append(insel(184, "GiG 5", "48:54:12", (0, 0, 0, 1000)))
        m._overview_zeit = 0
        m.overview()
        self.assertEqual(st.insel(184)["ausgleich"]["ziel"], 0.6)
        self.assertTrue(st.insel(184)["ausgleich"]["bekommt"])


if __name__ == "__main__":
    unittest.main()
