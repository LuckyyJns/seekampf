"""Ablauf-Tests gegen eine simulierte Seekampf-API (kein Netz, kein echter Zustand).

    .venv/bin/python -m unittest discover -s tests -v
"""
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import protokoll  # noqa: E402
from api_client import ApiError  # noqa: E402

config.TELEGRAM_BOT_TOKEN = ""
import bot as bot_modul  # noqa: E402
import steuerung  # noqa: E402
from state import State  # noqa: E402

# Nie die echte Hub-Steuerung lesen oder den echten Status ueberschreiben:
# sonst haengen die erwarteten Mengen davon ab, was gerade im Seekampf-Hub
# eingestellt ist (z. B. ANFRAGE_ZIEL).
_TEST_DATA = tempfile.mkdtemp(prefix="allianz-test-")
steuerung.DATA_DIR = _TEST_DATA
steuerung.STEUERUNG_PATH = os.path.join(_TEST_DATA, "steuerung.json")
steuerung.STATUS_PATH = os.path.join(_TEST_DATA, "status.json")
if hasattr(steuerung, "BEFEHLE_DIR"):
    steuerung.BEFEHLE_DIR = os.path.join(_TEST_DATA, "befehle")
steuerung._mtime = -1.0  # erzwingt Neuladen -> Standardwerte aus config.py
steuerung.laden()

# Tests schreiben nicht ins echte Tages-Log.
logging.getLogger("seekampf_allianz_bot").handlers.clear()
logging.getLogger("seekampf_allianz_bot").addHandler(logging.NullHandler())

ICH, ANDI, BENNI, FREMD = 104, 112, 100, 999
START = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def praesenz(faehigkeiten):
    return f"[PRAESENZ/1]\nversion: 1\nnebenversion: 0\nfaehigkeiten: {faehigkeiten}\nbot: X"


class FakeClient:
    def __init__(self):
        self.uhr = START
        self.mitglieder = {ICH: "Lucky_Jns", ANDI: "Andi", BENNI: "benni", 101: "SeppMoller"}
        self.threads = {49: "Allianz-Protokoll: Notrufe", 50: "Allianz-Protokoll: Rohstoffe",
                        51: "Allianz-Protokoll: Teilnehmer"}
        self.posts = {tid: [] for tid in self.threads}
        self._id = 1000
        for tid in self.threads:
            self._post(tid, "Andi", "Kopfbeitrag fuer Menschen")
        self._post(51, "Andi", praesenz("notruf beistand leihe_rueckgabe anfrage"))
        self._post(51, "benni", praesenz("notruf beistand leihe_rueckgabe"))
        self.inbox = []
        self.gesendet = []   # (empfaenger, betreff, text)
        self.geloescht = []
        self.flotten_gestartet = []
        self.truppen = {"speerkaempfer": 96, "steinewerfer": 11}
        self.schiffe = {"kleines_kriegsschiff": 6, "spaehschiff": 5}
        self.rohstoffe = {"gold": 13000, "stein": 32000, "holz": 23000, "kapazitaet": 43480}
        self.incoming = []
        self.combat = []
        self.battles = {}
        self.zweite = None     # optionale zweite Insel (id 20, 46:55:6)
        self.flotten = []      # eigene Flotten unterwegs (Raids des Flotten-Managers)
        self.gerufen = []

    def _post(self, tid, autor, body):
        self._id += 1
        self.posts[tid].append({"id": self._id, "autor": autor, "body": body,
                                "created_at": self.uhr.isoformat()})
        return self._id

    def jetzt(self):
        return self.uhr

    def get_me(self):
        return {"id": ICH, "name": "Lucky_Jns", "allianz": {"id": 9}, "current_island_id": 447}

    def get_islands_overview(self):
        inseln = [{"id": 447, "name": "GiG", "koordinaten": "53:49:8", "truppen": dict(self.truppen),
                   "schiffe": dict(self.schiffe), "rohstoffe": dict(self.rohstoffe),
                   "gebaeude": {"steinmauer": 5},
                   "bedrohung_im_anflug": any(f.get("ziel_insel_id") == 447 for f in self.incoming)}]
        if self.zweite is not None:
            z = self.zweite
            inseln.append({"id": 20, "name": "GiG 2", "koordinaten": "46:55:6", "truppen": dict(z["truppen"]),
                           "schiffe": dict(z["schiffe"]), "rohstoffe": dict(z["rohstoffe"]),
                           "gebaeude": {"steinmauer": 0},
                           "bedrohung_im_anflug": any(f.get("ziel_insel_id") == 20 for f in self.incoming)})
        return inseln

    def zweite_insel(self, speer=0, kriegsschiffe=0, rohstoffe=None):
        self.zweite = {"truppen": {"speerkaempfer": speer}, "schiffe": {"kleines_kriegsschiff": kriegsschiffe},
                       "rohstoffe": rohstoffe or {"gold": 500, "stein": 500, "holz": 500, "kapazitaet": 1749}}

    def get_alliance(self, _):
        return {"mitglieder": [{"user_id": u, "name": n} for u, n in self.mitglieder.items()]}

    def lookup_user(self, name):
        return {"name": name, "punkte": 1000}

    def list_threads(self):
        return [{"id": t, "titel": n} for t, n in self.threads.items()]

    def get_thread_posts(self, tid):
        return [dict(p) for p in self.posts[tid]]

    def create_post(self, tid, body):
        return {"id": self._post(tid, "Lucky_Jns", body)}

    def _beitrag_loeschen(self, pid):
        for tid, posts in self.posts.items():
            for p in posts:
                if p["id"] == pid:
                    if p["autor"] != "Lucky_Jns":
                        raise AssertionError("fremder Beitrag geloescht!")
                    posts.remove(p)
                    self.geloescht.append(pid)
                    return

    def list_messages(self, folder, limit=50, archiv=False):
        return list(self.inbox) if folder == "inbox" else list(self.combat)

    def send_message(self, empfaenger, betreff, text):
        self.gesendet.append((empfaenger, betreff, text))

    def pn_von(self, uid, text, betreff):
        self._id += 1
        self.inbox.append({"id": self._id, "sender_id": uid, "sender": self.mitglieder.get(uid),
                           "subject": betreff, "body": text, "created_at": self.uhr.isoformat()})

    def get_battle(self, bid):
        return self.battles[bid]

    def get_incoming(self):
        return list(self.incoming)

    def get_fleets(self):
        return [dict(f) for f in self.flotten]

    def recall_fleet(self, fid):
        f = next(f for f in self.flotten if f["id"] == fid)
        assert f["mission"] == "attack" and f["state"] == "outbound"
        abfahrt = datetime.fromisoformat(f["depart_at"])
        f["state"], f["return_at"] = "returning", (self.uhr + (self.uhr - abfahrt)).isoformat()
        self.gerufen.append(fid)

    def raid(self, fid, schiffe, vor_min=2, mission="attack", von=447):
        self.flotten.append({"id": fid, "mission": mission, "state": "outbound", "ships": schiffe,
                             "origin_island_id": von,
                             "target": {"x": 54, "y": 50, "z": 22},
                             "depart_at": (self.uhr - timedelta(minutes=vor_min)).isoformat(),
                             "recallable_until": (self.uhr + timedelta(minutes=5)).isoformat()})

    def heimkehr(self):
        for f in list(self.flotten):
            if f["state"] == "returning" and datetime.fromisoformat(f["return_at"]) <= self.uhr:
                for t, n in f["ships"].items():
                    self.schiffe[t] = self.schiffe.get(t, 0) + n
                self.flotten.remove(f)

    def create_fleet(self, payload):
        if payload["origin_island_id"] == 447:
            truppen, schiffe = self.truppen, self.schiffe
        else:
            truppen, schiffe = self.zweite["truppen"], self.zweite["schiffe"]
        for t, n in payload["units"].items():
            if truppen.get(t, 0) < n:
                raise ApiError(400, "zu_wenig", t)
            truppen[t] -= n
        for t, n in payload["ships"].items():
            if schiffe.get(t, 0) < n:
                raise ApiError(400, "zu_wenig", t)
            schiffe[t] -= n
        self.flotten_gestartet.append(payload)
        return {"id": len(self.flotten_gestartet), "arrive_at": (self.uhr + timedelta(minutes=20)).isoformat()}

    # Hilfen fuer die Tests
    def gesendet_typ(self, typ):
        return [g for g in self.gesendet if g[1].startswith(f"[{typ}]")]

    def posts_typ(self, tid, typ):
        return [p for p in self.posts[tid] if p["body"].startswith(f"[{typ}")]


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.api = FakeClient()
        self.bot = bot_modul.AllianzBot.__new__(bot_modul.AllianzBot)
        bot_modul.AllianzBot.__init__(self.bot)
        self.bot.state = State(os.path.join(self.tmp.name, "state.json"))
        self.bot.client = self.api
        for obj in (self.bot.k,):
            obj.client, obj.state = self.api, self.bot.state
        self.bot.k.notifier = None
        self.bot.k.start()

    def tearDown(self):
        self.tmp.cleanup()

    def warte(self, **kw):
        self.api.uhr += timedelta(**kw)

    def lauf(self, n=1):
        for _ in range(n):
            self.bot._faellig.clear()
            self.bot.k._inseln_stand = self.bot.k._incoming = None  # die Test-Uhr steht zwischen Aenderungen still
            self.bot.tick()


class Praesenz(Basis):
    def test_praesenz_und_whitelist(self):
        self.lauf()
        eigene = self.api.posts_typ(51, "PRAESENZ")
        mein = [p for p in eigene if p["autor"] == "Lucky_Jns"]
        self.assertEqual(len(mein), 1)
        erg = protokoll.parse(mein[0]["body"], "forum:teilnehmer", ICH, 1, START)
        self.assertEqual(erg.nachricht["felder"]["faehigkeiten"],
                         ["notruf", "beistand", "leihe_rueckgabe", "anfrage"])
        self.assertEqual(erg.nachricht["felder"]["bot"], "Jannis-Bot")
        self.assertEqual(self.bot.k.whitelist(), {ANDI, BENNI})
        # Nach 7 Tagen neue Praesenz, die alte eigene wird geloescht - fremde nie.
        self.warte(days=7, minutes=1)
        self.lauf()
        mein2 = [p for p in self.api.posts_typ(51, "PRAESENZ") if p["autor"] == "Lucky_Jns"]
        self.assertEqual(len(mein2), 1)
        self.assertNotEqual(mein2[0]["id"], mein[0]["id"])
        self.assertEqual(self.api.geloescht, [mein[0]["id"]])

    def test_loeschen_nur_eigene(self):
        self.lauf()
        k = self.bot.k
        kopf = self.api.posts[51][0]["id"]
        fremd = self.api.posts[51][1]["id"]
        self.assertFalse(k.eigenen_beitrag_loeschen(kopf))
        self.assertFalse(k.eigenen_beitrag_loeschen(fremd))
        # Selbst wenn eine fremde ID faelschlich als eigen eingetragen waere: Autor-Pruefung.
        k.state.data["eigene_beitraege"][str(fremd)] = {"kanal": "forum:teilnehmer", "typ": "X",
                                                        "gepostet": "2026-09-28T12:00:00+00:00"}
        self.assertFalse(k.eigenen_beitrag_loeschen(fremd))
        k.state.data["eigene_beitraege"][str(kopf)] = {"kanal": "forum:teilnehmer", "typ": "X",
                                                       "gepostet": "2026-09-28T12:00:00+00:00"}
        self.assertFalse(k.eigenen_beitrag_loeschen(kopf))
        self.assertEqual(self.api.geloescht, [])


class Helfen(Basis):
    def notruf_andi(self, ankunft_min=60, bedarf=None):
        ankunft = protokoll.zeit_normal(self.api.uhr + timedelta(minutes=ankunft_min))
        text = f"[NOTRUF]\nvorgang: 112-5\ninsel: 50:53:3\nankunft: {ankunft}"
        if bedarf:
            text += f"\nbedarf: speerkaempfer={bedarf}"
        self.api._post(49, "Andi", text)

    def test_angebot_zusage_versand_rueckgabe(self):
        self.notruf_andi()
        self.lauf()
        angebote = self.api.gesendet_typ("ANGEBOT")
        self.assertEqual(len(angebote), 1)
        f = protokoll.parse(angebote[0][2], "pn", ICH, 1, self.api.uhr, angebote[0][1]).nachricht["felder"]
        self.assertEqual(angebote[0][0], "Andi")
        self.assertEqual(f["einheiten"], {"speerkaempfer": 48})  # 50 % von 96
        self.assertEqual(f["leihe"], ["speerkaempfer"])
        # kein zweites Angebot, solange eines offen ist
        self.lauf()
        self.assertEqual(len(self.api.gesendet_typ("ANGEBOT")), 1)
        # Zusage ueber 40
        self.warte(minutes=2)
        self.api.pn_von(ANDI, "[ZUSAGE]\nvorgang: 112-5\neinheiten: speerkaempfer=40", "[ZUSAGE] 112-5")
        self.lauf()
        self.assertEqual(len(self.api.flotten_gestartet), 1)
        fl = self.api.flotten_gestartet[0]
        self.assertEqual(fl["mission_type"], "handel")
        self.assertEqual(fl["units"], {"speerkaempfer": 40})
        self.assertEqual(fl["ships"], {"kleines_kriegsschiff": 5})
        self.assertEqual(fl["target"], {"x": 50, "y": 53, "z": 3})
        self.assertEqual(len(self.api.gesendet_typ("VERSANDT")), 1)
        # Rueckgabe wird verbucht
        self.api.pn_von(ANDI, "[RUECKGABE]\nvorgang: 112-5\neinheiten: speerkaempfer=30", "[RUECKGABE] 112-5")
        self.lauf()
        self.assertEqual(self.bot.state.data["verliehen"][0]["status"], "zurueck")

    def test_bedarf_begrenzt_angebot(self):
        self.notruf_andi(bedarf=20)
        self.lauf()
        f = protokoll.parse(self.api.gesendet_typ("ANGEBOT")[0][2], "pn", ICH, 1, self.api.uhr).nachricht
        self.assertEqual(f["felder"]["einheiten"], {"speerkaempfer": 20})

    def test_ohne_schiffe_kein_angebot(self):
        self.api.schiffe = {"spaehschiff": 5, "grosses_handelsschiff": 2}
        self.notruf_andi()
        self.lauf()
        self.assertEqual(self.api.gesendet_typ("ANGEBOT"), [])

    def test_zusage_ruft_raidflotte(self):
        self.api.schiffe = {"spaehschiff": 5}
        self.api.raid(501, {"kleines_kriegsschiff": 6})
        self.notruf_andi()
        self.lauf()
        f = protokoll.parse(self.api.gesendet_typ("ANGEBOT")[0][2], "pn", ICH, 1, self.api.uhr).nachricht
        self.assertEqual(f["felder"]["einheiten"], {"speerkaempfer": 48})
        self.assertEqual(self.api.gerufen, [])  # vor der Zusage wird nichts gerufen
        self.api.pn_von(ANDI, "[ZUSAGE]\nvorgang: 112-5", "[ZUSAGE] 112-5")
        self.lauf()
        self.assertEqual(self.api.gerufen, [501])
        self.assertEqual(self.api.flotten_gestartet, [])
        self.warte(minutes=3)
        self.api.heimkehr()
        self.lauf()
        self.assertEqual(self.api.flotten_gestartet[0]["units"], {"speerkaempfer": 48})
        self.assertEqual(len(self.api.gesendet_typ("VERSANDT")), 1)

    def test_rueckruf_nur_wenn_noetig(self):
        self.api.raid(501, {"kleines_kriegsschiff": 6})
        self.notruf_andi()
        self.lauf()
        self.api.pn_von(ANDI, "[ZUSAGE]\nvorgang: 112-5", "[ZUSAGE] 112-5")
        self.lauf()
        self.assertEqual(self.api.gerufen, [])  # 6 Schiffe lagen schon im Hafen
        self.assertEqual(len(self.api.flotten_gestartet), 1)

    def test_nicht_whitelist_kein_angebot(self):
        self.api._post(49, "SeppMoller", "[NOTRUF]\ninsel: 50:50:1")
        self.lauf()
        self.assertEqual(self.api.gesendet_typ("ANGEBOT"), [])

    def test_zusage_zu_spaet(self):
        self.notruf_andi()
        self.lauf()
        self.warte(minutes=16)
        self.api.pn_von(ANDI, "[ZUSAGE]\nvorgang: 112-5", "[ZUSAGE] 112-5")
        self.lauf()
        self.assertEqual(self.api.flotten_gestartet, [])
        absage = self.api.gesendet_typ("ABSAGE")
        self.assertTrue(absage and "abgelaufen" in absage[-1][2])

    def test_schiffe_weg_bis_zur_zusage(self):
        self.notruf_andi()
        self.lauf()
        self.api.schiffe = {"spaehschiff": 5}  # Flotten-Manager hat sie inzwischen losgeschickt
        self.api.pn_von(ANDI, "[ZUSAGE]\nvorgang: 112-5", "[ZUSAGE] 112-5")
        self.lauf()
        self.assertEqual(self.api.flotten_gestartet, [])
        self.assertIn("nicht_verfuegbar", self.api.gesendet_typ("ABSAGE")[-1][2])

    def test_eigene_bedrohung_zieht_zurueck(self):
        self.notruf_andi()
        self.lauf()
        self.api.incoming = [{"eigene": False, "ziel_insel_id": 447, "mission": "attack",
                              "absender": "Kraken", "arrive_at": (self.api.uhr + timedelta(hours=1)).isoformat()}]
        self.lauf()
        self.assertIn("zurueckgezogen", self.api.gesendet_typ("ABSAGE")[0][2])

    def test_fuenfzig_prozent_ueber_mehrere_vorgaenge(self):
        self.notruf_andi()
        self.lauf()
        self.api.pn_von(ANDI, "[ZUSAGE]\nvorgang: 112-5", "[ZUSAGE] 112-5")
        self.lauf()
        self.api.schiffe["kleines_kriegsschiff"] += 6
        self.api._post(49, "benni", "[NOTRUF]\nvorgang: 100-1\ninsel: 50:50:1")
        self.lauf()
        # 48 verliehen, 48 daheim -> Grenze erreicht, nichts mehr anzubieten
        self.assertEqual(len(self.api.gesendet_typ("ANGEBOT")), 1)


class Verteidigen(Basis):
    def angriff(self, minuten=60, name="Kraken"):
        self.api.incoming = [{"eigene": False, "vorbeifahrend": False, "ziel_insel_id": 447,
                              "mission": "attack", "absender": name,
                              "arrive_at": (self.api.uhr + timedelta(minutes=minuten)).isoformat()}]

    def angebot(self, uid, einheiten, leihe, ankunft_min=30):
        ankunft = protokoll.zeit_normal(self.api.uhr + timedelta(minutes=ankunft_min))
        self.api.pn_von(uid, f"[ANGEBOT]\nvorgang: 104-1\nvon: 50:53:3\neinheiten: {einheiten}\n"
                             f"leihe: {leihe}\nankunft: {ankunft}", "[ANGEBOT] 104-1")

    def test_notruf_bedarf_und_weckpn(self):
        self.angriff()
        self.lauf()
        notrufe = self.api.posts_typ(49, "NOTRUF")
        self.assertEqual(len(notrufe), 1)
        f = protokoll.parse(notrufe[0]["body"], "forum:notrufe", ICH, 1, self.api.uhr).nachricht["felder"]
        self.assertEqual(f["vorgang"], "104-1")
        self.assertEqual(f["bedarf"], {"speerkaempfer": 24})  # 120 - 96
        self.assertEqual(f["angreifer"], "Kraken")
        self.assertEqual(sorted(g[0] for g in self.api.gesendet_typ("NOTRUF")), ["Andi", "benni"])
        self.lauf()
        self.assertEqual(len(self.api.posts_typ(49, "NOTRUF")), 1)  # unveraendert -> kein neuer Beitrag

    def test_bedarf_aus_kampfbericht(self):
        self.api.combat = [{"id": 1, "created_at": START.isoformat(),
                            "payload": {"rolle": "verteidiger", "battle_id": 7, "sieg": True,
                                        "insel_koordinaten": "53:49:8"}}]
        self.api.battles[7] = {"start_at": START.isoformat(), "angreifer_name": "Kraken",
                               "timeline": [{"phase": "land", "angriffsstaerke": 8000}],
                               "eingesetzt": {"verteidiger": {"units": {}}},
                               "verluste": {"verteidiger": {"units": {}}}}
        self.warte(days=1)
        self.lauf()
        self.angriff()
        self.lauf()
        f = protokoll.parse(self.api.posts_typ(49, "NOTRUF")[0]["body"], "forum:notrufe", ICH, 1,
                            self.api.uhr).nachricht["felder"]
        # 8000 * 1,5 = 12000; Verteidigung (96*22 + 11*3 + 50) * 1,25 = 2745 -> fehlen 9255 / 27,5
        self.assertEqual(f["staerke"], 12000)
        self.assertEqual(f["bedarf"], {"speerkaempfer": 337})

    def test_geschenk_sofort_leihe_spaeter_steinewerfer_nie_geliehen(self):
        self.api.truppen["speerkaempfer"] = 0   # Bedarf = 120 (Mindestbestand)
        self.api.schiffe["kleines_kriegsschiff"] = 1  # traegt nur 8
        self.angriff(minuten=120)
        self.lauf()
        self.angebot(BENNI, "speerkaempfer=150 steinewerfer=50", "speerkaempfer steinewerfer")
        self.angebot(ANDI, "speerkaempfer=100", "keine")
        self.lauf()
        zusagen = self.api.gesendet_typ("ZUSAGE")
        self.assertEqual([z[0] for z in zusagen], ["Andi"])  # Geschenk sofort
        self.warte(minutes=5)
        self.lauf()
        zusagen = self.api.gesendet_typ("ZUSAGE")
        self.assertEqual(zusagen[1][0], "benni")
        f = protokoll.parse(zusagen[1][2], "pn", ICH, 1, self.api.uhr, zusagen[1][1]).nachricht["felder"]
        self.assertEqual(f["einheiten"], {"speerkaempfer": 20})  # 120 - 100, keine Steinewerfer
        ankunft = protokoll.zeit_normal(self.api.uhr + timedelta(minutes=20))
        self.api.pn_von(BENNI, f"[VERSANDT]\nvorgang: 104-1\nvon: 50:50:1\neinheiten: speerkaempfer=20\n"
                               f"leihe: speerkaempfer\nankunft: {ankunft}", "[VERSANDT] 104-1")
        self.lauf()
        self.assertEqual(len(self.bot.state.data["schulden"]), 1)
        # Kampf mit halben Verlusten -> halbe Leihe (10) zurueck
        self.warte(minutes=116)
        self.api.combat = [{"id": 2, "created_at": self.api.uhr.isoformat(),
                            "payload": {"rolle": "verteidiger", "battle_id": 8, "sieg": True,
                                        "insel_koordinaten": "53:49:8"}}]
        self.api.battles[8] = {"start_at": self.api.uhr.isoformat(), "angreifer_name": "Kraken",
                               "timeline": [{"phase": "land", "angriffsstaerke": 3000}],
                               "eingesetzt": {"verteidiger": {"units": {"speerkaempfer": 120}}},
                               "verluste": {"verteidiger": {"units": {"speerkaempfer": 60}}}}
        self.api.truppen["speerkaempfer"] = 60
        self.warte(minutes=2)
        self.api.incoming = []
        self.lauf()
        entw = self.api.posts_typ(49, "ENTWARNUNG")
        self.assertEqual(len(entw), 1)
        self.assertIn("ergebnis: gehalten", entw[0]["body"])
        # 1 kleines Kriegsschiff traegt 8 -> zwei Fahrten, eine RUECKGABE-PN am Ende
        self.lauf()
        self.assertEqual(self.api.flotten_gestartet[-1]["units"], {"speerkaempfer": 8})
        self.assertEqual(self.api.gesendet_typ("RUECKGABE"), [])
        self.api.schiffe["kleines_kriegsschiff"] = 1  # Schiff wieder daheim
        self.lauf()
        rueck = self.api.flotten_gestartet[-1]
        self.assertEqual(rueck["units"], {"speerkaempfer": 2})
        self.assertEqual(rueck["target"], {"x": 50, "y": 50, "z": 1})
        self.assertIn("einheiten: speerkaempfer=10", self.api.gesendet_typ("RUECKGABE")[0][2])
        self.assertEqual(self.bot.state.data["schulden"][0]["status"], "erledigt")

    def test_rueckgabe_ruft_raidflotte(self):
        self.api.schiffe = {"spaehschiff": 5}
        self.api.raid(501, {"kleines_kriegsschiff": 2, "kleines_handelsschiff": 1})
        self.api.raid(502, {"kleines_kriegsschiff": 1}, mission="handel")  # nie rufen
        self.bot.state.data["notrufe_alt"].append({"vorgang": "104-9", "entwarnt": self.api.uhr.isoformat(),
                                                   "angebote": {}})
        self.bot.state.data["schulden"].append({
            "vorgang": "104-9", "helfer_id": ANDI, "helfer_name": "Andi", "von": "50:53:3",
            "leihe": {"speerkaempfer": 10}, "ankunft": self.api.uhr.isoformat(), "status": "offen"})
        self.warte(seconds=1)
        self.lauf()
        self.assertEqual(self.api.gerufen, [501])
        self.assertEqual(self.api.flotten_gestartet, [])
        self.assertTrue(self.bot.k.eilig())
        self.warte(minutes=4)
        self.api.heimkehr()
        self.lauf()
        self.assertEqual(self.api.flotten_gestartet[-1]["units"], {"speerkaempfer": 10})
        self.assertEqual(self.api.flotten_gestartet[-1]["ships"], {"kleines_kriegsschiff": 2})
        self.assertEqual(self.api.gerufen, [501])

    def test_aufraeumen_nach_obergrenze(self):
        self.angriff(minuten=30)
        self.lauf()
        self.warte(minutes=32)
        self.api.incoming = []
        self.lauf()
        # Abgeschlossen: der Notruf ist sofort weg, die Entwarnung bleibt 20 min stehen.
        self.assertEqual(self.api.posts_typ(49, "NOTRUF"), [])
        self.assertEqual(len(self.api.posts_typ(49, "ENTWARNUNG")), 1)
        self.warte(minutes=19)
        self.lauf()
        self.assertEqual(len(self.api.posts_typ(49, "ENTWARNUNG")), 1)
        self.warte(minutes=2)
        self.lauf()
        self.assertEqual(self.api.posts_typ(49, "ENTWARNUNG"), [])
        self.assertEqual(len(self.api.posts[49]), 1)  # Kopfbeitrag steht noch


class MehrereInseln(Basis):
    def angriff_auf(self, insel_id, name="Kraken", minuten=60):
        self.api.incoming.append({"eigene": False, "vorbeifahrend": False, "ziel_insel_id": insel_id,
                                  "mission": "attack", "absender": name,
                                  "arrive_at": (self.api.uhr + timedelta(minutes=minuten)).isoformat()})

    def test_notruf_je_insel(self):
        self.api.zweite_insel()
        self.angriff_auf(20)
        self.lauf()
        notrufe = self.api.posts_typ(49, "NOTRUF")
        self.assertEqual(len(notrufe), 1)
        self.assertIn("insel: 46:55:6", notrufe[0]["body"])
        self.assertIn("bedarf: speerkaempfer=", notrufe[0]["body"])
        self.angriff_auf(447, name="Hai")
        self.lauf()
        notrufe = self.api.posts_typ(49, "NOTRUF")
        self.assertEqual(len(notrufe), 2)
        self.assertIn("insel: 53:49:8", notrufe[1]["body"])
        vorgaenge = {protokoll.parse(p["body"], "forum:notrufe", ICH, 1, self.api.uhr)
                     .nachricht["felder"]["vorgang"] for p in notrufe}
        self.assertEqual(len(vorgaenge), 2)

    def test_neue_insel_wird_ueberwacht(self):
        self.lauf()
        self.api.zweite_insel()   # kommt waehrend des Betriebs dazu
        self.angriff_auf(20)
        self.lauf()
        self.assertIn("insel: 46:55:6", self.api.posts_typ(49, "NOTRUF")[0]["body"])

    def test_helfen_von_der_besseren_insel(self):
        self.api.zweite_insel(speer=60, kriegsschiffe=6)
        self.angriff_auf(447, minuten=240)   # GiG selbst bedroht -> hilft nicht
        ankunft = protokoll.zeit_normal(self.api.uhr + timedelta(minutes=120))
        self.api._post(49, "Andi", f"[NOTRUF]\nvorgang: 112-5\ninsel: 50:53:3\nankunft: {ankunft}")
        self.lauf()
        f = protokoll.parse(self.api.gesendet_typ("ANGEBOT")[0][2], "pn", ICH, 1, self.api.uhr).nachricht["felder"]
        self.assertEqual(f["von"], "46:55:6")
        self.assertEqual(f["einheiten"], {"speerkaempfer": 30})  # 50 % von 60
        self.api.pn_von(ANDI, "[ZUSAGE]\nvorgang: 112-5", "[ZUSAGE] 112-5")
        self.lauf()
        self.assertEqual(self.api.flotten_gestartet[0]["origin_island_id"], 20)
        self.assertIn("von: 46:55:6", self.api.gesendet_typ("VERSANDT")[0][2])

    def test_rueckruf_nur_von_derselben_insel(self):
        self.api.zweite_insel(speer=60, kriegsschiffe=0)
        self.api.truppen["speerkaempfer"] = 0          # GiG kann nicht helfen
        self.api.raid(501, {"kleines_kriegsschiff": 6}, von=447)  # gehoert zu GiG, nuetzt GiG 2 nichts
        self.notruf_andi()
        self.lauf()
        self.assertEqual(self.api.gesendet_typ("ANGEBOT"), [])
        self.api.raid(502, {"kleines_kriegsschiff": 6}, von=20)
        self.bot.state.data["hilfe"]["112-5"]["angebote_n"] = 0
        self.lauf()
        self.assertIn("von: 46:55:6", self.api.gesendet_typ("ANGEBOT")[0][2])
        self.api.pn_von(ANDI, "[ZUSAGE]\nvorgang: 112-5", "[ZUSAGE] 112-5")
        self.lauf()
        self.assertEqual(self.api.gerufen, [502])

    def notruf_andi(self):
        ankunft = protokoll.zeit_normal(self.api.uhr + timedelta(minutes=60))
        self.api._post(49, "Andi", f"[NOTRUF]\nvorgang: 112-5\ninsel: 50:53:3\nankunft: {ankunft}")

    def test_anfrage_je_insel(self):
        self.api.zweite_insel(rohstoffe={"gold": 50, "stein": 900, "holz": 900, "kapazitaet": 1749})
        self.lauf()
        anfragen = self.api.posts_typ(50, "ANFRAGE")
        self.assertEqual(len(anfragen), 1)
        self.assertIn("insel: 46:55:6", anfragen[0]["body"])
        self.assertIn("rohstoffe: gold=387", anfragen[0]["body"])  # bis 25 % von 1749 = 437

    def test_altbestand_wird_heimatinsel(self):
        pfad = os.path.join(self.tmp.name, "alt.json")
        import json
        with open(pfad, "w") as f:
            json.dump({"anfrage": {"vorgang": "104-2", "start": START.isoformat(),
                                   "bis": (START + timedelta(hours=24)).isoformat(),
                                   "rohstoffe": {"gold": 9383}, "angebote": {}}}, f)
        st = State(pfad)
        self.bot.state = self.bot.k.state = st
        self.bot.k.start()
        a = st.data["anfragen"]["447"]
        self.assertEqual((a["vorgang"], a["insel"]), ("104-2", "53:49:8"))


class Rohstoffe(Basis):
    def test_anfrage_zusage_erledigt(self):
        self.api.rohstoffe["stein"] = 3000   # < 10 % von 43480
        self.lauf()
        anfragen = self.api.posts_typ(50, "ANFRAGE")
        self.assertEqual(len(anfragen), 1)
        self.assertIn("rohstoffe: stein=7870", anfragen[0]["body"])  # bis 25 % = 10870
        vorgang = protokoll.parse(anfragen[0]["body"], "forum:rohstoffe", ICH, 1,
                                  self.api.uhr).nachricht["felder"]["vorgang"]
        ankunft = protokoll.zeit_normal(self.api.uhr + timedelta(minutes=30))
        self.api.pn_von(ANDI, f"[ANGEBOT]\nvorgang: {vorgang}\nvon: 50:53:3\nrohstoffe: stein=9000\n"
                              f"ankunft: {ankunft}", f"[ANGEBOT] {vorgang}")
        self.lauf()
        z = self.api.gesendet_typ("ZUSAGE")[0]
        self.assertIn("rohstoffe: stein=7870", z[2])
        self.api.rohstoffe["stein"] = 10870
        self.lauf()
        self.assertEqual(len(self.api.posts_typ(50, "ERLEDIGT")), 1)
        self.assertEqual(self.api.posts_typ(50, "ANFRAGE"), [])  # abgeschlossen: Anfrage sofort weg
        self.warte(minutes=21)
        self.lauf()
        self.assertEqual(self.api.posts_typ(50, "ERLEDIGT"), [])
        self.assertEqual(len(self.api.posts[50]), 1)  # Kopfbeitrag steht noch

    def test_nur_eine_anfrage_je_insel(self):
        self.api.rohstoffe["stein"] = 3000
        self.lauf()
        self.bot.anfrage.manuell({"holz": 500})
        self.api.rohstoffe["gold"] = 100
        self.lauf()
        anfragen = self.api.posts_typ(50, "ANFRAGE")
        self.assertEqual(len(anfragen), 1)  # die alten sind geloescht
        body = anfragen[0]["body"]
        self.assertIn("vorgang: 104-1", body)
        for teil in ("stein=7870", "holz=500", "gold="):
            self.assertIn(teil, body)
        self.bot.anfrage.manuell({"holz": 250})
        anfragen = self.api.posts_typ(50, "ANFRAGE")
        self.assertEqual(len(anfragen), 1)
        self.assertIn("holz=750", anfragen[0]["body"])

    def test_alte_beitraege_werden_aufgeraeumt(self):
        """Altbestand vor dem Update: erledigte Vorgaenge und ueberholte Anfragen."""
        k, jetzt = self.bot.k, self.api.uhr
        eigene = self.bot.state.data["eigene_beitraege"]
        def alt(typ, vorgang, vor_min):
            pid = self.api._post(50, "Lucky_Jns", f"[{typ}]\nvorgang: {vorgang}")
            eigene[str(pid)] = {"kanal": "forum:rohstoffe", "typ": typ, "vorgang": vorgang,
                                "gepostet": protokoll.zeit_normal(jetzt - timedelta(minutes=vor_min))}
            self.bot.state.data["eigene_vorgaenge"][vorgang] = {
                "obergrenze": protokoll.zeit_normal(jetzt + timedelta(hours=20))}
            return pid
        alt("ANFRAGE", "104-7", 90)
        alt("ANFRAGE", "104-7", 60)
        alt("ERLEDIGT", "104-7", 30)
        alt("ANFRAGE", "104-8", 60)
        frisch = alt("ERLEDIGT", "104-8", 5)
        self.bot.aufraeumen()
        self.assertEqual([p["id"] for p in self.api.posts[50][1:]], [frisch])

    def test_manuelle_anfrage_bleibt_offen_bis_verschickt(self):
        self.bot.anfrage.manuell({"holz": 3000})   # Holz liegt weit ueber 25 %
        self.lauf()
        anfragen = self.api.posts_typ(50, "ANFRAGE")
        self.assertEqual(len(anfragen), 1)
        self.assertIn("rohstoffe: holz=3000", anfragen[0]["body"])
        self.assertEqual(self.api.posts_typ(50, "ERLEDIGT"), [])
        ankunft = protokoll.zeit_normal(self.api.uhr + timedelta(minutes=30))
        self.api.pn_von(ANDI, f"[ANGEBOT]\nvorgang: 104-1\nvon: 50:53:3\nrohstoffe: holz=5000\n"
                              f"ankunft: {ankunft}", "[ANGEBOT] 104-1")
        self.lauf()
        self.assertIn("rohstoffe: holz=3000", self.api.gesendet_typ("ZUSAGE")[0][2])
        self.assertEqual(self.api.posts_typ(50, "ERLEDIGT"), [])
        # Andi schickt weniger als zugesagt -> Anfrage bleibt offen, Rest wird wieder angenommen
        self.api.pn_von(ANDI, f"[VERSANDT]\nvorgang: 104-1\nvon: 50:53:3\nrohstoffe: holz=995\n"
                              f"ankunft: {ankunft}", "[VERSANDT] 104-1")
        self.lauf()
        self.assertEqual(self.api.posts_typ(50, "ERLEDIGT"), [])
        self.api.pn_von(BENNI, f"[ANGEBOT]\nvorgang: 104-1\nvon: 50:50:1\nrohstoffe: holz=4000\n"
                               f"ankunft: {ankunft}", "[ANGEBOT] 104-1")
        self.lauf()
        self.assertIn("rohstoffe: holz=2005", self.api.gesendet_typ("ZUSAGE")[-1][2])
        self.api.pn_von(BENNI, f"[VERSANDT]\nvorgang: 104-1\nvon: 50:50:1\nrohstoffe: holz=2005\n"
                               f"ankunft: {ankunft}", "[VERSANDT] 104-1")
        self.lauf()
        self.assertEqual(len(self.api.posts_typ(50, "ERLEDIGT")), 1)

    def test_fremde_anfrage_nie_bedient(self):
        self.api._post(50, "Andi", "[ANFRAGE]\ninsel: 50:53:3\nrohstoffe: stein=3000")
        self.lauf(3)
        self.assertEqual(self.api.gesendet, [])
        self.assertEqual(self.api.flotten_gestartet, [])


if __name__ == "__main__":
    unittest.main()
