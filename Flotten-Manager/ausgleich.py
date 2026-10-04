"""Rohstoff-Ausgleich zwischen den eigenen Inseln - in alle Richtungen.

Jede Insel hat zwei Schalter: "gibt" (darf Ueberschuss abgeben) und "bekommt"
(wird aufgefuellt); beides zugleich ist erlaubt, so gleichen sich alle Inseln
gegenseitig aus. Je Insel einstellbar (leer = Standard aus den Einstellungen):
  ziel        auffuellen bis zu diesem Anteil der Lagerkapazitaet
  reserve     so viel behaelt sie mindestens, wenn sie abgibt
  max_abgabe  hoechstens so viele Rohstoffe je Lieferung (0 = unbegrenzt)

Je Tick:
  1. Soll je Rohstoff einer "bekommt"-Insel: bis `ziel` - mehr, wenn der
     Upgrade-Bot fuer den naechsten Ausbau mehr braucht (data/status.json,
     `plan.fehlt`). Bedarf = Soll - Bestand - schon per Handel unterwegs.
  2. Ueberschuss einer "gibt"-Insel: alles ueber ihrer Grenze. Die Grenze ist
     die Reserve - bekommt die Insel selbst auch, mindestens ihr eigenes Soll.
     So gibt keine Insel etwas ab, das sie im naechsten Moment selbst wieder
     anfordern wuerde; ein Hin- und Herschicken ist ausgeschlossen. Was ihr
     fuer den eigenen naechsten Ausbau fehlt, gibt sie gar nicht ab.
  3. Je Empfaenger (dringendster zuerst) liefert die Insel, die am meisten
     liefern kann - bei Gleichstand die naechstgelegene -, ab
     `ausgleich_min_menge` Rohstoffen je Fahrt, per Handel (mission "handel").

Handelsschiffe sind auch das, womit geraidet wird. Reichen die im Hafen nicht
fuer die Lieferung, haelt der Manager die Handelsschiffe des Gebers zurueck
(`reserviert`), bis genug heimgekehrt sind - nach WARTEN_HOECHSTENS faehrt,
was da ist. Inseln ganz ohne Handelsschiffe geben nichts ab. Bedrohte Inseln
liefern nicht und bekommen nichts (die Ladung waere Beute fuer den Angreifer).
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

WARTEN_HOECHSTENS_S = 30 * 60
FEHLER_PAUSE_S = 10 * 60
UPGRADE_STATUS_MAX_ALTER_S = 15 * 60
LIEFERUNGEN_MERKEN = 40
LAGER_DECKE = 0.95  # nie ueber diesen Anteil auffuellen - der Rest waere verschenkt


def reserve_lesen(pfad: str, feld: str) -> dict[str, dict]:
    """Reservierung eines anderen Bots (Insel-ID -> {Name: Menge}); leer, wenn
    die Datei fehlt oder veraltet ist (Bot aus)."""
    try:
        with open(pfad, encoding="utf-8") as f:
            daten = json.load(f)
        if time.time() - float(daten.get("zeit") or 0) > config.RESERVE_MAX_ALTER_S:
            return {}
        return {str(k): v for k, v in (daten.get(feld) or {}).items() if isinstance(v, dict)}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def leerer_stand() -> dict:
    return {"fahrten": 0, "geliefert": {r: 0.0 for r in config.RESOURCE_KEYS},
            "lieferungen": [], "warten_seit": {}}


def standard_einstellung() -> dict:
    return {"gibt": False, "bekommt": False, "ziel": None, "reserve": None, "max_abgabe": 0}


def einstellung(insel: dict) -> dict:
    """Ausgleich-Einstellung einer Insel; uebernimmt die alte Rolle
    ("spender"/"empfaenger") beim ersten Lesen."""
    e = insel.get("ausgleich")
    if not isinstance(e, dict):
        alt = insel.pop("ausgleich_rolle", "aus")
        e = standard_einstellung()
        e["gibt"] = alt == "spender"
        e["bekommt"] = alt == "empfaenger"
        insel["ausgleich"] = e
    for k, v in standard_einstellung().items():
        e.setdefault(k, v)
    return e


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

    def wirksam(self, e: dict) -> tuple[float, float, int]:
        """(ziel, reserve, max_abgabe) einer Insel, leere Felder = Standard."""
        ziel = e.get("ziel")
        reserve = e.get("reserve")
        return (float(self.s["ausgleich_ziel"] if ziel is None else ziel),
                float(self.s["ausgleich_reserve"] if reserve is None else reserve),
                int(e.get("max_abgabe") or 0))

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

    @staticmethod
    def handels_kapazitaet(insel_id: int, ov: dict, flotten_api: list) -> tuple[float, float]:
        """(Ladung der Handelsschiffe im Hafen, Ladung aller Handelsschiffe der Insel)."""
        hafen = geo.ladevolumen({t: int((ov.get("schiffe") or {}).get(t, 0) or 0)
                                 for t in config.HANDELSSCHIFF_TYPEN})
        draussen = sum(geo.ladevolumen({t: n for t, n in (f.get("ships") or {}).items()
                                        if t in config.HANDELSSCHIFF_TYPEN})
                       for f in flotten_api if f.get("origin_island_id") == insel_id)
        return hafen, hafen + draussen

    # --------------------------------------------------------------- Tick
    def tick(self, alles: dict[int, dict], flotten_api: list, jetzt: float) -> None:
        self.reserviert = set()
        s = self.s
        inseln = self.m.state.data["inseln"]
        unterwegs = self.unterwegs(flotten_api)
        plaene = self.upgrade_plaene() if s["ausgleich_upgrade_bedarf"] else {}
        kolo = reserve_lesen(config.KOLO_RESERVE_PATH, "inseln")

        bericht: dict[str, dict] = {}
        nehmer, geber = [], []
        for iid_text, insel in inseln.items():
            iid = int(iid_text)
            ov = alles.get(iid)
            e = einstellung(insel)
            ziel, reserve, max_abgabe = self.wirksam(e)
            eintrag = {"gibt": e["gibt"], "bekommt": e["bekommt"], "ziel": ziel, "reserve": reserve,
                       "max_abgabe": max_abgabe, "bedarf": {}, "ueberschuss": {}, "soll": {}, "grenze": {},
                       "hinweis": None, "upgrade": plaene.get(iid_text),
                       "unterwegs": unterwegs.get(insel.get("koordinaten") or "", {}),
                       "schiffe_kap": 0.0}
            bericht[iid_text] = eintrag
            if ov is None:
                eintrag["hinweis"] = "gehoert nicht mehr zum Konto"
                continue
            res = ov.get("rohstoffe") or {}
            kap = float(res.get("kapazitaet") or 0)
            if kap <= 0:
                continue
            fehlt = (plaene.get(iid_text) or {}).get("fehlt") or {}
            for r in config.RESOURCE_KEYS:
                bestand = float(res.get(r, 0) or 0)
                soll = kap * ziel
                if float(fehlt.get(r, 0) or 0) > 0:  # der naechste Ausbau braucht mehr als das Ziel
                    soll = max(soll, bestand + float(fehlt[r]))
                soll = min(kap * LAGER_DECKE, soll)
                eintrag["soll"][r] = soll
                if e["bekommt"]:
                    eintrag["bedarf"][r] = max(0.0, math.floor(
                        soll - bestand - float(eintrag["unterwegs"].get(r, 0) or 0)))
                grenze = kap * reserve
                if e["bekommt"]:
                    grenze = max(grenze, soll)  # nie abgeben, was sie selbst haben will
                # Was der Kolonisations-Bot hier fuer ein Schiff anspart, bleibt da.
                grenze = max(grenze, float((kolo.get(iid_text) or {}).get(r, 0) or 0))
                eintrag["grenze"][r] = grenze
                if e["gibt"]:
                    # Was dem eigenen naechsten Ausbau fehlt, bleibt ganz da.
                    frei = 0.0 if float(fehlt.get(r, 0) or 0) > 0 else max(0.0, math.floor(bestand - grenze))
                    eintrag["ueberschuss"][r] = frei
            _, eintrag["schiffe_kap"] = self.handels_kapazitaet(iid, ov, flotten_api)
            if e["bekommt"]:
                nehmer.append(iid)
            if e["gibt"]:
                if eintrag["schiffe_kap"] > 0:
                    geber.append(iid)
                elif _summe(eintrag["ueberschuss"]) > 0:
                    eintrag["hinweis"] = "hat Ueberschuss, aber keine Handelsschiffe"

        self.bericht = {"inseln": bericht, "zeit": jetzt,
                        "hinweis": None if s["ausgleich_aktiv"] else "ausgeschaltet"}
        if not s["ausgleich_aktiv"]:
            self.stand()["warten_seit"] = {}
            return
        if not geber:
            self.bericht["hinweis"] = "keine Insel mit Handelsschiffen gibt ab"
            return

        min_menge = float(s["ausgleich_min_menge"])
        # Dringendste zuerst: wer gemessen an seinem Lager am meisten braucht.
        def dringlichkeit(iid):
            kap = float((alles[iid].get("rohstoffe") or {}).get("kapazitaet") or 1)
            return _summe(bericht[str(iid)]["bedarf"]) / kap
        for iid in sorted(nehmer, key=dringlichkeit, reverse=True):
            e = bericht[str(iid)]
            if _summe(e["bedarf"]) < min_menge:
                continue
            if alles[iid].get("bedrohung_im_anflug"):
                e["hinweis"] = "Bedrohung im Anflug - keine Lieferung"
                continue
            if self._pause_bis.get(str(iid), 0) > jetzt:
                e["hinweis"] = "nach einem Fehler kurz pausiert"
                continue
            moeglich = [g for g in geber if g != iid and g not in self.reserviert
                        and not alles[g].get("bedrohung_im_anflug")]
            if not moeglich:
                e["hinweis"] = "kein Geber frei (bedroht oder wartet auf Schiffe)"
                continue
            ziel_koord = geo.koord_parse(inseln[str(iid)]["koordinaten"])

            def wertung(g):
                menge = _summe(self._lieferung(e["bedarf"], bericht[str(g)]))
                naehe = -geo.distanz_felder(geo.koord_parse(inseln[str(g)]["koordinaten"]), ziel_koord)
                return (menge, naehe)
            g = max(moeglich, key=wertung)
            lieferung = self._lieferung(e["bedarf"], bericht[str(g)])
            if _summe(lieferung) < min_menge:
                e["hinweis"] = "gerade hat keine Insel passenden Ueberschuss"
                continue
            gesendet = self._senden(g, iid, lieferung, flotten_api, jetzt, e)
            if gesendet:
                ueb = bericht[str(g)]["ueberschuss"]
                for r, n in gesendet.items():
                    ueb[r] = max(0.0, ueb[r] - n)
                    e["unterwegs"][r] = e["unterwegs"].get(r, 0.0) + n
                    e["bedarf"][r] = max(0.0, e["bedarf"].get(r, 0.0) - n)

    @staticmethod
    def _lieferung(bedarf: dict, geber: dict) -> dict:
        """Was `geber` von `bedarf` liefern kann, gedeckelt durch seine max_abgabe."""
        ueb = geber["ueberschuss"]
        lieferung = {r: min(bedarf.get(r, 0.0), ueb.get(r, 0.0)) for r in config.RESOURCE_KEYS}
        lieferung = {r: n for r, n in lieferung.items() if n > 0}
        grenze = geber.get("max_abgabe") or 0
        summe = _summe(lieferung)
        if grenze > 0 and summe > grenze:
            lieferung = {r: math.floor(n * grenze / summe) for r, n in lieferung.items()}
            lieferung = {r: n for r, n in lieferung.items() if n > 0}
        return lieferung

    # -------------------------------------------------------------- Senden
    def _senden(self, sp: int, ziel_id: int, lieferung: dict, flotten_api: list,
                jetzt: float, eintrag: dict) -> dict | None:
        """Lieferung losschicken. Gibt zurueck, was wirklich faehrt - oder None,
        wenn auf Schiffe gewartet wird bzw. es scheiterte."""
        m = self.m
        ov = m.insel_ov(sp, max_alter=0)
        hafen = {t: int((ov.get("schiffe") or {}).get(t, 0) or 0) for t in config.HANDELSSCHIFF_TYPEN}
        kap_hafen, gesamt = self.handels_kapazitaet(sp, ov, flotten_api)
        if gesamt <= 0:
            eintrag["hinweis"] = f"{m.state.insel(sp).get('name')} hat keine Handelsschiffe"
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

