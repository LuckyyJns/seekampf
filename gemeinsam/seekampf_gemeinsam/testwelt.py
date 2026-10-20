"""Nachgebautes Seekampf fuer Tests, in denen mehrere Bots zusammenspielen.

Ersetzt nur die Netzverbindung: `client.session = welt.sitzung()` - die echten
API-Clients der Bots (Fehlerauswertung, Nachpruefen, Ablehnungs-Zaehlung)
laufen unveraendert mit. Mehrere Bots teilen sich so EINE Welt und sehen
gegenseitig, was der andere gerade tut.

Nachgebaut ist, wo sich Bots in die Quere kommen koennen: Inseln mit
Schiffen, Truppen und Rohstoffen, Flotten (Raid auf freie Inseln, Handel
zwischen eigenen Inseln, Rueckruf), Ausbildung in Kaserne und Hafen, die
Karte (freie Inseln) und ein leeres Postfach. Kampf, Gebaeudebau, Allianz und
Forum fehlen bewusst.

Zeit laeuft nicht von selbst: `ankommen()` laesst alle Flotten ankommen,
`heimkehren()` alle zurueckkehren, `ausbildung_fertig()` beendet die
Ausbildung. So bleibt jeder Test deterministisch.

Eingriffe:
  welt.vor(methode, pfad_muster, funktion)   - laeuft einmal unmittelbar vor
      der passenden Anfrage (z. B. "ein anderer Bot handelt genau jetzt")
  welt.antwort_verlieren(methode, pfad_muster) - die naechste passende Anfrage
      wird ausgefuehrt, ihre Antwort geht aber verloren (requests.ReadTimeout)
  welt.anfragen                               - Protokoll [(methode, pfad, json)]
"""
from __future__ import annotations

import copy
import json
import re
import time
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.parse import urlparse

import requests

SCHIFF_NAMEN = {"spaehschiff": "Spaehschiff", "kleines_kriegsschiff": "Kleines Kriegsschiff",
                "grosses_kriegsschiff": "Großes Kriegsschiff", "kleines_handelsschiff": "Kleines Handelsschiff",
                "grosses_handelsschiff": "Großes Handelsschiff", "kolonisationsschiff": "Kolonisationsschiff"}
KASERNE = [
    {"typ": "steinewerfer", "ab_stufe": 1, "dauer_s": 420, "kosten": {"gold": 35, "stein": 28, "holz": 14}},
    {"typ": "speerkaempfer", "ab_stufe": 5, "dauer_s": 780, "kosten": {"gold": 80, "stein": 45, "holz": 55}},
    {"typ": "bogenschuetze", "ab_stufe": 10, "dauer_s": 1260, "kosten": {"gold": 165, "stein": 30, "holz": 90}},
]
HAFEN = [
    {"typ": "spaehschiff", "ab_stufe": 1, "kaempfer_kap": 0, "rohstoff_kap": 0, "dauer_s": 360,
     "kosten": {"gold": 165, "stein": 36, "holz": 300}},
    {"typ": "kleines_handelsschiff", "ab_stufe": 2, "kaempfer_kap": 0, "rohstoff_kap": 75, "dauer_s": 900,
     "kosten": {"gold": 385, "stein": 84, "holz": 700}},
    {"typ": "kleines_kriegsschiff", "ab_stufe": 5, "kaempfer_kap": 8, "rohstoff_kap": 0, "dauer_s": 1620,
     "kosten": {"gold": 715, "stein": 156, "holz": 1300}},
    {"typ": "grosses_handelsschiff", "ab_stufe": 10, "kaempfer_kap": 0, "rohstoff_kap": 460, "dauer_s": 3600,
     "kosten": {"gold": 1650, "stein": 360, "holz": 3000}},
    {"typ": "grosses_kriegsschiff", "ab_stufe": 12, "kaempfer_kap": 30, "rohstoff_kap": 0, "dauer_s": 6480,
     "kosten": {"gold": 2475, "stein": 540, "holz": 4500}},
    {"typ": "kolonisationsschiff", "ab_stufe": 20, "kaempfer_kap": 0, "rohstoff_kap": 0, "dauer_s": 21600,
     "kosten": {"gold": 19525, "stein": 4260, "holz": 35500}},
]
KATALOG = {k["typ"]: k for k in KASERNE + HAFEN}
ROHSTOFFE = ("gold", "stein", "holz")
FAHRZEIT_S = 1800
RUECKRUF_S = 600
BEUTE_JE_ROHSTOFF = 100


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class _Antwort:
    def __init__(self, status: int, daten=None):
        self.status_code = status
        self.headers = {"Date": formatdate(usegmt=True)}
        self._daten = daten
        self.content = b"" if daten is None else json.dumps(daten).encode()
        self.text = self.content.decode()

    def json(self):
        if self._daten is None:
            raise ValueError("kein Inhalt")
        return copy.deepcopy(self._daten)


class Ablehnung(Exception):
    def __init__(self, status: int, code: str, text: str):
        super().__init__(text)
        self.status, self.code, self.text = status, code, text


class Welt:
    def __init__(self, spieler: str = "Lucky_Jns"):
        self.spieler = spieler
        self.inseln: dict[int, dict] = {}
        self.freie: dict[str, dict] = {}            # koord -> {"name", "besitzer"}
        self.flotten: dict[int, dict] = {}
        self.ausbildung: dict[int, list] = {}
        self.anfragen: list[tuple] = []
        self._vor: list[list] = []                  # [methode, muster, funktion]
        self._verlieren: list[list] = []            # [methode, muster]
        self._nid = 100

    # ------------------------------------------------------------ Aufbau
    def insel(self, iid: int, koord: str, *, name: str | None = None, schiffe=None, truppen=None,
              rohstoffe=(10000, 10000, 10000), kapazitaet=50000, kaserne=20, hafen=20,
              produktion=(100, 100, 100)) -> dict:
        x, y, z = (int(v) for v in koord.split(":"))
        self.inseln[iid] = {
            "id": iid, "name": name or f"I{iid}", "koordinaten": koord, "x": x, "y": y, "z": z,
            "schiffe": dict(schiffe or {}), "truppen": dict(truppen or {}),
            "rohstoffe": {**dict(zip(ROHSTOFFE, map(float, rohstoffe))), "kapazitaet": float(kapazitaet)},
            "produktion": dict(zip(ROHSTOFFE, map(float, produktion))),
            "gebaeude": {"kaserne": kaserne, "hafen": hafen}, "bedrohung_im_anflug": False,
        }
        return self.inseln[iid]

    def freie_insel(self, koord: str, name: str = "Unbewohnte Insel") -> None:
        self.freie[koord] = {"name": name, "besitzer": None}

    def besiedeln(self, koord: str, besitzer: str) -> None:
        self.freie.setdefault(koord, {"name": "Insel"})["besitzer"] = besitzer

    def sitzung(self) -> "_Sitzung":
        return _Sitzung(self)

    # ------------------------------------------------------------ Eingriffe
    def vor(self, methode: str, muster: str, funktion) -> None:
        self._vor.append([methode, re.compile(muster), funktion])

    def antwort_verlieren(self, methode: str, muster: str) -> None:
        self._verlieren.append([methode, re.compile(muster)])

    # ---------------------------------------------------------------- Zeit
    def ankommen(self) -> None:
        """Alle Flotten auf dem Hinweg kommen an."""
        for f in list(self.flotten.values()):
            if f["state"] != "outbound":
                continue
            ziel = self._eigene_an(f["target"])
            if f["mission"] == "handel" and ziel is not None:
                for t, n in f["units"].items():
                    ziel["truppen"][t] = ziel["truppen"].get(t, 0) + n
                for r, n in f["resources"].items():
                    ziel["rohstoffe"][r] = min(ziel["rohstoffe"]["kapazitaet"], ziel["rohstoffe"][r] + n)
                f["units"], f["resources"] = {}, {}
            elif f["mission"] == "attack" and self._ist_frei(f["target"]):
                fracht = sum(n * KATALOG.get(t, {}).get("rohstoff_kap", 0) for t, n in f["ships"].items())
                je = min(BEUTE_JE_ROHSTOFF, fracht // 3)
                f["loot"] = {r: je for r in ROHSTOFFE}
            f.update(state="returning", return_at=_iso(time.time() + FAHRZEIT_S), recallable_until=None)

    def heimkehren(self) -> None:
        """Alle Flotten auf dem Rueckweg sind wieder daheim."""
        for fid, f in list(self.flotten.items()):
            if f["state"] != "returning":
                continue
            heim = self.inseln[f["origin_island_id"]]
            for t, n in f["ships"].items():
                heim["schiffe"][t] = heim["schiffe"].get(t, 0) + n
            for t, n in f["units"].items():
                heim["truppen"][t] = heim["truppen"].get(t, 0) + n
            for r, n in {**f["resources"], **(f.get("loot") or {})}.items():
                heim["rohstoffe"][r] = min(heim["rohstoffe"]["kapazitaet"], heim["rohstoffe"][r] + n)
            del self.flotten[fid]

    def ausbildung_fertig(self) -> None:
        for iid, auftraege in self.ausbildung.items():
            for a in auftraege:
                topf = "schiffe" if a["facility"] == "hafen" else "truppen"
                rest = a["anzahl"] - a["abgeschlossen"]
                self.inseln[iid][topf][a["item_typ"]] = self.inseln[iid][topf].get(a["item_typ"], 0) + rest
            auftraege.clear()

    # ---------------------------------------------------------- Abwicklung
    def anfrage(self, methode: str, url: str, params=None, json_body=None):
        pfad = urlparse(url).path
        pfad = pfad[pfad.index("/api/v1") + 7:] if "/api/v1" in pfad else pfad
        self.anfragen.append((methode, pfad, json_body))
        for eintrag in list(self._vor):
            if eintrag[0] == methode and eintrag[1].search(pfad):
                self._vor.remove(eintrag)
                eintrag[2]()
        verlieren = next((e for e in self._verlieren if e[0] == methode and e[1].search(pfad)), None)
        try:
            antwort = _Antwort(200, self._route(methode, pfad, params or {}, json_body or {}))
        except Ablehnung as a:
            antwort = _Antwort(a.status, {"error": {"code": a.code, "message": a.text}})
        if verlieren is not None:
            self._verlieren.remove(verlieren)
            raise requests.ReadTimeout(f"Antwort verloren ({methode} {pfad})")
        return antwort

    def _route(self, m: str, pfad: str, params: dict, body: dict):
        if m == "GET" and pfad == "/me":
            return {"name": self.spieler, "current_island_id": next(iter(self.inseln)), "allianz": {"id": 9}}
        if m == "GET" and pfad == "/me/islands/overview":
            return [self._uebersicht(i) for i in self.inseln.values()]
        if m == "GET" and pfad == "/fleets":
            return [copy.deepcopy(f) for f in self.flotten.values()]
        if m == "POST" and pfad == "/fleets":
            return self._flotte(body)
        if (t := re.fullmatch(r"/fleets/(\d+)/recall", pfad)) and m == "POST":
            return self._rueckruf(int(t.group(1)))
        if (t := re.fullmatch(r"/islands/(\d+)/training", pfad)):
            iid = self._eigene(int(t.group(1)))
            return self._ausbilden(iid, body) if m == "POST" else self._ausbildung_lesen(iid)
        if (t := re.fullmatch(r"/islands/(\d+)/resources", pfad)) and m == "GET":
            i = self.inseln[self._eigene(int(t.group(1)))]
            return {**i["rohstoffe"], "produktion_pro_h": dict(i["produktion"])}
        if (t := re.fullmatch(r"/islands/(\d+)/rename", pfad)) and m == "POST":
            self.inseln[self._eigene(int(t.group(1)))]["name"] = body.get("name")
            return {"ok": True}
        if (t := re.fullmatch(r"/map/region/(-?\d+)/(-?\d+)", pfad)) and m == "GET":
            return self._region(int(t.group(1)), int(t.group(2)))
        if (t := re.fullmatch(r"/map/island/(\d+)/(\d+)/(\d+)", pfad)) and m == "GET":
            return self._kartenfeld(f"{t.group(1)}:{t.group(2)}:{t.group(3)}")
        if m == "GET" and pfad == "/messages":
            return []
        if re.fullmatch(r"/messages/\d+/archive", pfad) and m == "POST":
            return {"ok": True}
        raise Ablehnung(404, "not_found", f"{m} {pfad} gibt es in der Testwelt nicht")

    # --------------------------------------------------------------- Teile
    def _uebersicht(self, i: dict) -> dict:
        aus = {k: copy.deepcopy(v) for k, v in i.items() if k not in ("x", "y", "z", "produktion")}
        aus["rohstoffe"]["produktion_pro_h"] = dict(i["produktion"])
        return aus

    def _eigene(self, iid: int) -> int:
        if iid not in self.inseln:
            raise Ablehnung(404, "not_found", "Insel nicht gefunden")
        return iid

    def _eigene_an(self, ziel: dict) -> dict | None:
        koord = f"{ziel.get('x')}:{ziel.get('y')}:{ziel.get('z')}"
        return next((i for i in self.inseln.values() if i["koordinaten"] == koord), None)

    def _ist_frei(self, ziel: dict) -> bool:
        feld = self.freie.get(f"{ziel.get('x')}:{ziel.get('y')}:{ziel.get('z')}")
        return feld is not None and feld.get("besitzer") is None

    def _flotte(self, p: dict) -> dict:
        iid = self._eigene(int(p.get("origin_island_id")))
        heim = self.inseln[iid]
        mission = p.get("mission_type")
        if mission not in ("attack", "handel"):
            raise Ablehnung(422, "invalid_mission", "Mission unbekannt")
        ships, units = dict(p.get("ships") or {}), dict(p.get("units") or {})
        for t, n in ships.items():
            if heim["schiffe"].get(t, 0) < n:
                raise Ablehnung(409, "insufficient_ships", f"Nicht genug {SCHIFF_NAMEN.get(t, t)} im Hafen.")
        for t, n in units.items():
            if heim["truppen"].get(t, 0) < n:
                raise Ablehnung(409, "insufficient_units", f"Nicht genug {t} auf der Insel.")
        platz = sum(n * KATALOG.get(t, {}).get("kaempfer_kap", 0) for t, n in ships.items())
        if mission == "handel" and sum(units.values()) > platz:
            raise Ablehnung(409, "capacity", "Die Einheiten passen nicht auf die Schiffe.")
        for t, n in ships.items():
            heim["schiffe"][t] -= n
        for t, n in units.items():
            heim["truppen"][t] -= n
        self._nid += 1
        jetzt = time.time()
        f = {"id": self._nid, "mission": mission, "origin_island_id": iid,
             "target": {**p["target"], "name": "Ziel"}, "ships": ships, "units": units,
             "resources": dict(p.get("resources") or {}), "state": "outbound",
             "arrive_at": _iso(jetzt + FAHRZEIT_S), "return_at": None,
             "recallable_until": _iso(jetzt + RUECKRUF_S)}
        self.flotten[f["id"]] = f
        return copy.deepcopy(f)

    def _rueckruf(self, fid: int) -> dict:
        f = self.flotten.get(fid)
        if f is None or f["state"] != "outbound":
            raise Ablehnung(409, "not_recallable", "Flotte nicht zurueckrufbar")
        f.update(state="returning", return_at=_iso(time.time() + 60), recallable_until=None)
        return {"ok": True}

    def _ausbildung_lesen(self, iid: int) -> dict:
        geb = self.inseln[iid]["gebaeude"]
        katalog = {"kaserne": [dict(k, baubar=geb["kaserne"] >= k["ab_stufe"]) for k in KASERNE],
                   "hafen": [dict(k, baubar=geb["hafen"] >= k["ab_stufe"]) for k in HAFEN]}
        return {"auftraege": copy.deepcopy(self.ausbildung.get(iid, [])), "katalog": katalog}

    def _ausbilden(self, iid: int, body: dict) -> dict:
        item, anzahl, anlage = body.get("item"), int(body.get("count") or 0), body.get("facility")
        k = KATALOG.get(item)
        if k is None or anzahl < 1 or (anlage == "hafen") != (k in HAFEN):
            raise Ablehnung(422, "invalid_item", "Unbekannter Posten")
        if self.inseln[iid]["gebaeude"][anlage] < k["ab_stufe"]:
            raise Ablehnung(409, "requirement", f"{anlage} zu niedrig")
        schlange = self.ausbildung.setdefault(iid, [])
        if sum(1 for a in schlange if a["facility"] == anlage) >= 5:
            raise Ablehnung(409, "queue_full", "Ausbildungs-Warteschlange voll (max. 5).")
        lager = self.inseln[iid]["rohstoffe"]
        if any(lager[r] < k["kosten"][r] * anzahl for r in ROHSTOFFE):
            raise Ablehnung(409, "insufficient_resources", "Nicht genug Rohstoffe")
        for r in ROHSTOFFE:
            lager[r] -= k["kosten"][r] * anzahl
        self._nid += 1
        jetzt = time.time()
        a = {"id": self._nid, "facility": anlage, "item_typ": item, "anzahl": anzahl, "abgeschlossen": 0,
             "status": "aktiv" if not any(x["facility"] == anlage for x in schlange) else "wartend",
             "start_at": _iso(jetzt), "finish_at": _iso(jetzt + k["dauer_s"] * anzahl),
             "dauer_je_s": k["dauer_s"], "queue_position": len(schlange)}
        schlange.append(a)
        return copy.deepcopy(a)

    def _region(self, cx: int, cy: int) -> dict:
        inseln = []
        for koord, feld in self.freie.items():
            x, y, z = (int(v) for v in koord.split(":"))
            if abs(x - cx) <= 1 and abs(y - cy) <= 1:
                inseln.append({"koordinaten": koord, "x": x, "y": y, "z": z, **feld})
        for i in self.inseln.values():
            if abs(i["x"] - cx) <= 1 and abs(i["y"] - cy) <= 1:
                inseln.append({"koordinaten": i["koordinaten"], "x": i["x"], "y": i["y"], "z": i["z"],
                               "name": i["name"], "besitzer": self.spieler})
        return {"sektoren": [{"inseln": inseln}]}

    def _kartenfeld(self, koord: str) -> dict:
        eigene = next((i for i in self.inseln.values() if i["koordinaten"] == koord), None)
        if eigene is not None:
            return {"name": eigene["name"], "besitzer": self.spieler, "eigene_berichte": []}
        feld = self.freie.get(koord)
        if feld is None:
            raise Ablehnung(404, "not_found", "Keine Insel")
        return {"name": feld["name"], "besitzer": feld.get("besitzer"), "eigene_berichte": []}


class _Sitzung:
    """Steht anstelle von requests.Session im API-Client eines Bots."""

    def __init__(self, welt: Welt):
        self.welt = welt
        self.headers: dict = {}

    def request(self, method, url, timeout=None, params=None, json=None, headers=None, **_):
        return self.welt.anfrage(method, url, params=params, json_body=json)

    def mount(self, *_):
        pass
