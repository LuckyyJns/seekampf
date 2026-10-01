"""Rohstoff-Ausgleich zwischen den eigenen Inseln.

Jede Insel hat eine Rolle: "spender" (gibt Ueberschuss ab), "empfaenger"
(wird aufgefuellt) oder "aus". Je Tick:

  1. Bedarf je Empfaenger: jeder Rohstoff bis `ausgleich_ziel` der
     Lagerkapazitaet - mehr, wenn der Upgrade-Bot fuer seinen naechsten Ausbau
     mehr braucht (data/status.json des Upgrade-Bots, `plan.fehlt`). Was schon
     per Handel unterwegs ist, zaehlt mit, sonst ginge dieselbe Lieferung
     zweimal los.
  2. Ueberschuss je Spender: alles ueber `ausgleich_reserve` der eigenen
     Lagerkapazitaet.
  3. Liefern per Handelsfahrt (mission_type "handel"), sobald es mindestens
     `ausgleich_min_menge` sind.

Handelsschiffe sind auch das, womit geraidet wird. Reichen die im Hafen nicht
fuer die Lieferung, haelt der Manager die Handelsschiffe des Spenders zurueck
(`reserviert`), bis genug heimgekehrt sind - nach WARTEN_HOECHSTENS faehrt,
was da ist. Bedrohte Inseln liefern nicht und bekommen nichts (die Ladung
waere Beute fuer den Angreifer).
"""
from __future__ import annotations

import json
import logging
import math
import time

import config
import geo
from api_client import ApiError

log = logging.getLogger("seekampf_flotten_manager")

ROLLEN = ("aus", "spender", "empfaenger")
WARTEN_HOECHSTENS_S = 30 * 60
FEHLER_PAUSE_S = 10 * 60
UPGRADE_STATUS_MAX_ALTER_S = 15 * 60
LIEFERUNGEN_MERKEN = 40


def leerer_stand() -> dict:
    return {"fahrten": 0, "geliefert": {r: 0.0 for r in config.RESOURCE_KEYS},
            "lieferungen": [], "warten_seit": {}}


def _summe(werte: dict) -> float:
    return sum(float(werte.get(r, 0) or 0) for r in config.RESOURCE_KEYS)


def schiffe_fuer(ladung: float, hafen: dict) -> dict:
    """Handelsschiffe fuer `ladung` aus dem Hafen: erst kleine (schneller),
    dann grosse. Reicht der Hafen nicht, alle vorhandenen."""
    gewaehlt: dict[str, int] = {}
    rest = ladung
    for typ in ("kleines_handelsschiff", "grosses_handelsschiff"):
        if rest <= 0:
            break
        volumen = config.SCHIFF_LADEVOLUMEN[typ]
        n = min(int(hafen.get(typ, 0) or 0), math.ceil(rest / volumen))
        if n > 0:
            gewaehlt[typ] = n
            rest -= n * volumen
    # Grosse Schiffe machen kleine ueberfluessig? Ueberzaehlige kleine wieder abgeben.
    while gewaehlt.get("kleines_handelsschiff") and \
            geo.ladevolumen(gewaehlt) - config.SCHIFF_LADEVOLUMEN["kleines_handelsschiff"] >= ladung:
        gewaehlt["kleines_handelsschiff"] -= 1
    return {t: n for t, n in gewaehlt.items() if n > 0}


class Ausgleich:
    def __init__(self, manager):
        self.m = manager
        self.reserviert: set[int] = set()
        self.bericht: dict = {"inseln": {}, "hinweis": None, "zeit": None}
        self._pause_bis: dict[str, float] = {}

    # ------------------------------------------------------------ Zugriff
    @property
    def s(self) -> dict:
        return self.m.settings

    def stand(self) -> dict:
        return self.m.state.data.setdefault("ausgleich", leerer_stand())

    @staticmethod
    def rolle(insel: dict) -> str:
        r = insel.get("ausgleich_rolle")
        return r if r in ROLLEN else "aus"

    # ------------------------------------------------------- Eingangsdaten
    @staticmethod
    def unterwegs(flotten_api: list) -> dict[str, dict]:
        """Rohstoffe, die per Handel zu einer Koordinate unterwegs sind."""
        aus: dict[str, dict] = {}
        for f in flotten_api:
            if f.get("mission") not in ("transport", "handel") or f.get("state") != "outbound":
                continue
            t = f.get("target") or {}
            koord = geo.koord_str(t.get("x", 0), t.get("y", 0), t.get("z", 0))
            ziel = aus.setdefault(koord, {r: 0.0 for r in config.RESOURCE_KEYS})
            for r in config.RESOURCE_KEYS:
                ziel[r] += float((f.get("resources") or {}).get(r, 0) or 0)
        return aus

    @staticmethod
    def upgrade_plaene() -> dict[str, dict]:
        """Naechster geplanter Ausbau je Insel laut Upgrade-Bot (nur frische Daten)."""
        try:
            with open(config.UPGRADE_STATUS_PATH, encoding="utf-8") as f:
                status = json.load(f)
        except (OSError, ValueError):
            return {}
        if time.time() - float(status.get("zeit") or 0) > UPGRADE_STATUS_MAX_ALTER_S:
            return {}
        return {iid: i["plan"] for iid, i in (status.get("inseln") or {}).items()
                if isinstance(i, dict) and i.get("plan")}

    # --------------------------------------------------------------- Tick
    def tick(self, alles: dict[int, dict], flotten_api: list, jetzt: float) -> None:
        self.reserviert = set()
        s = self.s
        inseln = self.m.state.data["inseln"]
        unterwegs = self.unterwegs(flotten_api)
        plaene = self.upgrade_plaene() if s["ausgleich_upgrade_bedarf"] else {}

        bericht: dict[str, dict] = {}
        empfaenger, spender = [], []
        for iid_text, insel in inseln.items():
            iid = int(iid_text)
            ov = alles.get(iid)
            rolle = self.rolle(insel)
            eintrag = {"rolle": rolle, "bedarf": {}, "ueberschuss": {}, "hinweis": None,
                       "unterwegs": unterwegs.get(insel.get("koordinaten") or "", {}),
                       "upgrade": plaene.get(iid_text)}
            bericht[iid_text] = eintrag
            if ov is None:
                eintrag["hinweis"] = "gehoert nicht mehr zum Konto"
                continue
            res = ov.get("rohstoffe") or {}
            kap = float(res.get("kapazitaet") or 0)
            if kap <= 0:
                continue
            if rolle == "empfaenger":
                eintrag["bedarf"] = self._bedarf(res, kap, eintrag["unterwegs"], plaene.get(iid_text))
                empfaenger.append(iid)
            elif rolle == "spender":
                grenze = kap * float(s["ausgleich_reserve"])
                eintrag["ueberschuss"] = {r: max(0.0, math.floor(float(res.get(r, 0) or 0) - grenze))
                                          for r in config.RESOURCE_KEYS}
                spender.append(iid)

        self.bericht = {"inseln": bericht, "zeit": jetzt,
                        "hinweis": None if s["ausgleich_aktiv"] else "ausgeschaltet"}
        if not s["ausgleich_aktiv"]:
            self.stand()["warten_seit"] = {}
            return
        if not spender:
            self.bericht["hinweis"] = "keine Spender-Insel eingestellt"
            return

        min_menge = float(s["ausgleich_min_menge"])
        # Dringendste zuerst: wer gemessen an seinem Lager am meisten braucht.
        def dringlichkeit(iid):
            kap = float((alles[iid].get("rohstoffe") or {}).get("kapazitaet") or 1)
            return _summe(bericht[str(iid)]["bedarf"]) / kap
        for iid in sorted(empfaenger, key=dringlichkeit, reverse=True):
            e = bericht[str(iid)]
            if _summe(e["bedarf"]) < min_menge:
                continue
            if alles[iid].get("bedrohung_im_anflug"):
                e["hinweis"] = "Bedrohung im Anflug - keine Lieferung"
                continue
            if self._pause_bis.get(str(iid), 0) > jetzt:
                e["hinweis"] = "nach einem Fehler kurz pausiert"
                continue
            moeglich = [sp for sp in spender if sp not in self.reserviert
                        and not alles[sp].get("bedrohung_im_anflug")]
            if not moeglich:
                e["hinweis"] = "Spender bedroht oder wartet auf Schiffe"
                continue
            sp = max(moeglich, key=lambda x: _summe(self._lieferung(e["bedarf"], bericht[str(x)]["ueberschuss"])))
            lieferung = self._lieferung(e["bedarf"], bericht[str(sp)]["ueberschuss"])
            if _summe(lieferung) < min_menge:
                e["hinweis"] = "Spender hat gerade keinen passenden Ueberschuss"
                continue
            gesendet = self._senden(sp, iid, lieferung, flotten_api, jetzt, e)
            if gesendet:
                ueb = bericht[str(sp)]["ueberschuss"]
                for r, n in gesendet.items():
                    ueb[r] = max(0.0, ueb[r] - n)
                    e["unterwegs"][r] = e["unterwegs"].get(r, 0.0) + n
                    e["bedarf"][r] = max(0.0, e["bedarf"].get(r, 0.0) - n)

    def _bedarf(self, res: dict, kap: float, unterwegs: dict, plan: dict | None) -> dict:
        ziel = kap * float(self.s["ausgleich_ziel"])
        decke = kap * 0.95
        fehlt = (plan or {}).get("fehlt") or {}
        bedarf = {}
        for r in config.RESOURCE_KEYS:
            bestand = float(res.get(r, 0) or 0)
            soll = min(decke, max(ziel, bestand + float(fehlt.get(r, 0) or 0)))
            bedarf[r] = max(0.0, math.floor(soll - bestand - float(unterwegs.get(r, 0) or 0)))
        return bedarf

    @staticmethod
    def _lieferung(bedarf: dict, ueberschuss: dict) -> dict:
        return {r: min(bedarf.get(r, 0.0), ueberschuss.get(r, 0.0)) for r in config.RESOURCE_KEYS
                if min(bedarf.get(r, 0.0), ueberschuss.get(r, 0.0)) > 0}

    # -------------------------------------------------------------- Senden
    def _senden(self, sp: int, ziel_id: int, lieferung: dict, flotten_api: list,
                jetzt: float, eintrag: dict) -> dict | None:
        """Lieferung losschicken. Gibt zurueck, was wirklich faehrt - oder None,
        wenn auf Schiffe gewartet wird bzw. es scheiterte."""
        m = self.m
        ov = m.insel_ov(sp, max_alter=0)
        hafen = {t: int((ov.get("schiffe") or {}).get(t, 0) or 0) for t in config.HANDELSSCHIFF_TYPEN}
        kap_hafen = geo.ladevolumen(hafen)
        draussen = sum(geo.ladevolumen({t: n for t, n in (f.get("ships") or {}).items()
                                        if t in config.HANDELSSCHIFF_TYPEN})
                       for f in flotten_api if f.get("origin_island_id") == sp)
        gesamt = kap_hafen + draussen
        if gesamt <= 0:
            eintrag["hinweis"] = "der Spender hat keine Handelsschiffe"
            return None

        fracht = _summe(lieferung)
        noetig = min(fracht, gesamt)
        warten = self.stand()["warten_seit"]
        seit = warten.get(str(sp))
        mindestens = min(float(self.s["ausgleich_min_menge"]), gesamt)
        lange_genug = seit is not None and jetzt - seit >= WARTEN_HOECHSTENS_S
        if kap_hafen < noetig and not (lange_genug and kap_hafen >= mindestens):
            warten.setdefault(str(sp), jetzt)
            self.reserviert.add(sp)
            eintrag["hinweis"] = (f"wartet auf Handelsschiffe von {m.state.insel(sp).get('name')}: "
                                  f"{kap_hafen:.0f} von {noetig:.0f} Ladung im Hafen")
            return None

        ladung = min(fracht, kap_hafen)
        if ladung < fracht:  # anteilig kuerzen
            faktor = ladung / fracht
            lieferung = {r: math.floor(n * faktor) for r, n in lieferung.items()}
            lieferung = {r: n for r, n in lieferung.items() if n > 0}
        schiffe = schiffe_fuer(_summe(lieferung), hafen)
        ziel = m.state.insel(ziel_id)
        x, y, z = geo.koord_parse(ziel["koordinaten"])
        payload = {"origin_island_id": sp, "mission_type": "handel", "target": {"x": x, "y": y, "z": z},
                   "ships": schiffe, "units": {}, "resources": lieferung}
        try:
            antwort = m.client.create_fleet(payload)
        except ApiError as e:
            log.error("AUSGLEICH  Lieferung %s -> %s fehlgeschlagen: %s",
                      m.state.insel(sp).get("name"), ziel.get("name"), e)
            self._pause_bis[str(ziel_id)] = jetzt + FEHLER_PAUSE_S
            eintrag["hinweis"] = f"Fehler: {e.message}"
            return None
        # Bei einem Netzwerkfehler (requests) bricht der Tick ab, ohne zu
        # wiederholen: POST /fleets ist nicht idempotent. Ist die Flotte doch
        # losgefahren, taucht sie im naechsten Tick in GET /fleets auf und
        # zaehlt als unterwegs.
        warten.pop(str(sp), None)
        rec = m._record(antwort or {})
        rec.update(insel_id=sp, fremd=False, art="ausgleich")
        if rec.get("id") is not None:
            m.state.data["flotten"][str(rec["id"])] = rec
        stand = self.stand()
        stand["fahrten"] += 1
        for r, n in lieferung.items():
            stand["geliefert"][r] = stand["geliefert"].get(r, 0.0) + n
        stand["lieferungen"].insert(0, {
            "zeit": jetzt, "von": sp, "nach": ziel_id, "von_name": m.state.insel(sp).get("name"),
            "nach_name": ziel.get("name"), "rohstoffe": lieferung, "schiffe": schiffe,
            "ankunft": rec.get("arrive_at"), "flotte": rec.get("id")})
        del stand["lieferungen"][LIEFERUNGEN_MERKEN:]
        m._dirty = True
        log.info("AUSGLEICH  Flotte #%s %s -> %s (%s): %s, %s, Ankunft in %s",
                 rec.get("id"), m.state.insel(sp).get("name"), ziel.get("name"), ziel.get("koordinaten"),
                 ", ".join(f"{n:.0f} {r.capitalize()}" for r, n in lieferung.items()),
                 ", ".join(f"{n}x {t}" for t, n in schiffe.items()),
                 _dauer((rec.get("arrive_at") or jetzt) - jetzt))
        eintrag["hinweis"] = "Lieferung unterwegs"
        return lieferung


def _dauer(sekunden: float) -> str:
    sekunden = max(0, int(sekunden))
    h, rest = divmod(sekunden, 3600)
    return f"{h}h {rest // 60:02d}min" if h else f"{rest // 60}min"

