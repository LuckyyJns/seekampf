"""Zusammenspiel mehrerer Bots in EINER nachgebauten Spielwelt
(seekampf_gemeinsam.testwelt): Flotten-Manager, Ausbildungs- und
Kolonisations-Bot mit ihren echten API-Clients, ihrem echten Code und einem
gemeinsamen Reservierungs-Verzeichnis.

Hier liegen die Fehler, die einzelne Bot-Tests nicht finden: zwei Bots wollen
dasselbe Schiff, dieselben Rohstoffe, oder eine Antwort geht genau im
falschen Moment verloren.

    .venv/bin/python -m unittest discover -s tests
"""
import importlib
import logging
import os
import shutil
import sys
import tempfile
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
SEEKAMPF = os.path.dirname(os.path.dirname(HIER))
logging.disable(logging.CRITICAL)

from seekampf_gemeinsam import reservierungen  # noqa: E402
from seekampf_gemeinsam.testwelt import Welt  # noqa: E402


def bot_laden(ordner: str, *module: str) -> dict:
    """Module eines Bots laden, ohne dass sie mit gleichnamigen anderer Bots
    (config, state, api_client ...) kollidieren: waehrend des Ladens stehen nur
    die eigenen in sys.modules, danach wieder die vorherigen."""
    pfad = os.path.join(SEEKAMPF, ordner)
    eigene = {n[:-3] for n in os.listdir(pfad) if n.endswith(".py")}
    vorher = {n: sys.modules.pop(n) for n in list(sys.modules) if n in eigene}
    sys.path.insert(0, pfad)
    try:
        return {m: importlib.import_module(m) for m in module}
    finally:
        sys.path.remove(pfad)
        for n in eigene:
            sys.modules.pop(n, None)
        sys.modules.update(vorher)


FM = bot_laden("Flotten-Manager", "config", "manager", "state", "api_client")
AB = bot_laden("Ausbildungs-Bot", "config", "ausbildung", "state", "api_client")
KO = bot_laden("Kolonisations-Bot", "config", "kolonisation", "state", "api_client")


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zusammenspiel-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reserv = os.path.join(self.tmp, "reservierungen")
        for bot in (FM, AB, KO):
            cfg = bot["config"]
            alt = {k: getattr(cfg, k) for k in ("RESERVIERUNGEN_ORDNER", "BILANZ_ORDNER") if hasattr(cfg, k)}
            cfg.RESERVIERUNGEN_ORDNER = self.reserv
            cfg.BILANZ_ORDNER = os.path.join(self.tmp, "bilanz")
            self.addCleanup(lambda c=cfg, a=alt: [setattr(c, k, v) for k, v in a.items()])
        alt_status = FM["config"].UPGRADE_STATUS_PATH
        FM["config"].UPGRADE_STATUS_PATH = os.path.join(self.tmp, "upgrade-status.json")
        self.addCleanup(setattr, FM["config"], "UPGRADE_STATUS_PATH", alt_status)
        self.welt = Welt()

    def client(self, bot):
        c = bot["api_client"].SeekampfClient()
        c.session = self.welt.sitzung()
        return c

    def flotten_manager(self, *aktive_inseln):
        st = FM["state"].State(os.path.join(self.tmp, "flotte.json"))
        m = FM["manager"].FlottenManager(self.client(FM), st)
        m.start()
        for iid in aktive_inseln:
            st.insel(iid)["aktiv"] = True
        return m

    def ausbilder(self, soll: dict):
        st = AB["state"].State(os.path.join(self.tmp, "ausbildung.json"))
        for iid, werte in soll.items():
            st.insel_setzen(iid, werte)
        return AB["ausbildung"].Ausbilder(self.client(AB), st)

    def kolonisierer(self, *ziele):
        st = KO["state"].State(os.path.join(self.tmp, "kolonie.json"))
        for koord in ziele:
            x, y, z = (int(v) for v in koord.split(":"))
            st.neuer_eintrag(x, y, z, "Ziel")
        return KO["kolonisation"].Kolonisierer(self.client(KO), st), st

    def posts(self, pfad, mission=None):
        return [b for m, p, b in self.welt.anfragen
                if m == "POST" and p == pfad and (mission is None or (b or {}).get("mission_type") == mission)]


class KriegsschiffWirdGeradeGebraucht(Basis):
    """Der Fehler vom 20.10.: der Ausbildungs-Bot will 1 Steinewerfer verlegen,
    der Flotten-Manager schickt das einzige Kriegsschiff genau dazwischen auf Raid.
    Frueher scheiterte das jeden Takt neu; jetzt reserviert der Ausbildungs-Bot
    das Schiff, der Flotten-Manager haelt es nach der Heimkehr zurueck."""

    def test_reservieren_statt_endlos_scheitern(self):
        w = self.welt
        w.insel(1, "10:10:1", schiffe={"kleines_kriegsschiff": 1, "kleines_handelsschiff": 1},
                truppen={"steinewerfer": 20})
        w.insel(2, "10:10:2", kaserne=0, hafen=0)
        w.freie_insel("10:10:5")
        fm = self.flotten_manager(1)
        ab = self.ausbilder({1: {"steinewerfer": 10}, 2: {"steinewerfer": 1}})

        w.vor("POST", r"^/fleets$", fm.tick)          # FM raidet genau vor dem Verlegen
        ab.tick()
        self.assertEqual(len(self.posts("/fleets", "attack")), 1)
        self.assertEqual(len(self.posts("/fleets", "handel")), 1)            # gescheitert
        self.assertEqual(reservierungen.von("ausbildung", self.reserv)["1"]["einheiten"],
                         {"kleines_kriegsschiff": 1})

        w.ankommen()
        w.heimkehren()                                   # Kriegsschiff wieder daheim
        fm.tick()
        self.assertEqual(len(self.posts("/fleets", "attack")), 1)            # haelt es zurueck
        ab.tick()
        handel = self.posts("/fleets", "handel")
        self.assertEqual(len(handel), 2)
        self.assertEqual((handel[-1]["units"], handel[-1]["ships"]), ({"steinewerfer": 1}, {"kleines_kriegsschiff": 1}))
        w.ankommen()
        self.assertEqual(w.inseln[2]["truppen"], {"steinewerfer": 1})


class KolonisationsReserve(Basis):
    def test_ausbildung_verbaut_nicht_was_die_kolonisation_anspart(self):
        w = self.welt
        w.insel(1, "20:20:1", rohstoffe=(30000, 30000, 20000))           # Holz reicht nicht fuers Schiff
        w.freie_insel("20:20:7")
        ko, _ = self.kolonisierer("20:20:7")
        ko.tick()
        self.assertEqual(reservierungen.von("kolonisation", self.reserv)["1"]["rohstoffe"]["holz"], 35500)
        ab = self.ausbilder({1: {"speerkaempfer": 200}})
        ab.tick()
        self.assertEqual(self.posts("/islands/1/training"), [])
        self.assertIn("wartet auf Rohstoffe", " ".join(ab.bericht["1"]["hinweise"]))
        self.assertEqual(w.inseln[1]["rohstoffe"]["holz"], 20000)


class AntwortVerloren(Basis):
    def test_kein_zweites_kolonisationsschiff(self):
        w = self.welt
        w.insel(1, "30:30:1", schiffe={"kolonisationsschiff": 1}, rohstoffe=(50000, 50000, 50000))
        w.freie_insel("30:30:9")
        ko, st = self.kolonisierer("30:30:9")
        w.antwort_verlieren("POST", r"^/fleets$")
        ko.tick()
        ko.tick()
        ko.tick()
        self.assertEqual(len(self.posts("/fleets")), 1)
        self.assertEqual(self.posts("/islands/1/training"), [])           # kein zweites Schiff (59.000 Rohstoffe)
        self.assertEqual(st.warteschlange[0]["status"], "unterwegs")


if __name__ == "__main__":
    unittest.main()
