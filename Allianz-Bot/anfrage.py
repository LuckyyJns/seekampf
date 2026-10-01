"""Rolle Anfragender: Rohstoffhilfe anfordern (agenten.md 7.3, Faehigkeit anfrage).

Vorgaben:
  - Faellt ein Rohstoff unter ANFRAGE_SCHWELLE der Lagerkapazitaet, geht eine
    Anfrage raus, die ihn bis ANFRAGE_ZIEL auffuellt.
  - Angebote werden automatisch zugesagt, hoechstens bis zur angefragten Menge.
  - Sind alle angefragten Rohstoffe wieder auf ANFRAGE_ZIEL: [ERLEDIGT].
  - Von Hand (anfrage_stellen.py) angefragte Mengen gelten als erledigt,
    sobald sie zugesagt und verschickt sind.
  - Selbst gibt der Bot nie Rohstoffe her (keine Faehigkeit rohstoffhilfe).
"""
from __future__ import annotations

import logging
import math

import config
import steuerung
from api_client import ApiError
from kontext import NichtGesendet, iso, zeit

log = logging.getLogger("seekampf_allianz_bot")

ROHSTOFFE = ("gold", "stein", "holz")


class Anfrage:
    def __init__(self, k):
        self.k = k

    @property
    def anfragen(self) -> dict:
        """Aktive eigene Anfragen, Schluessel = Insel-ID (als String)."""
        return self.k.state.data["anfragen"]

    def _aktiv(self, vorgang: str) -> dict | None:
        return next((a for a in self.anfragen.values() if a["vorgang"] == vorgang), None)

    def _vorgang(self, vorgang: str) -> dict | None:
        return self._aktiv(vorgang) or next(
            (x for x in self.k.state.data["anfragen_alt"] if x["vorgang"] == vorgang), None)

    def besitzt(self, vorgang: str) -> bool:
        return self._vorgang(vorgang) is not None

    # ------------------------------------------------------------------ Tick
    def tick(self) -> None:
        for insel_id in self.k.inseln():
            self._insel_pruefen(insel_id)

    def _insel_pruefen(self, insel_id: int) -> None:
        k = self.k
        jetzt = k.jetzt()
        res = k.overview(insel_id).get("rohstoffe") or {}
        kap = float(res.get("kapazitaet") or 0)
        if kap <= 0:
            return
        ziel = math.floor(kap * config.ANFRAGE_ZIEL)
        niedrig = {r: ziel - int(res.get(r, 0) or 0) for r in ROHSTOFFE
                   if float(res.get(r, 0) or 0) < kap * config.ANFRAGE_SCHWELLE}

        a = self.anfragen.get(str(insel_id))
        if a and jetzt > zeit(a["bis"]):
            self._abschliessen(a, erledigt_posten=False)
            a = None
        if a:
            if self._fertig(a, res, ziel):
                self._abschliessen(a, erledigt_posten=True)
                return
            neu = {r: n for r, n in niedrig.items() if r not in a["rohstoffe"]}
            if neu:
                a["rohstoffe"].update(neu)
                self._posten(a)
            return
        if not niedrig or not steuerung.will("anfrage"):
            return
        if k.overview(insel_id).get("handel_im_anflug"):
            # Eine Lieferung ist schon unterwegs - meist der Rohstoff-Ausgleich
            # des Flotten-Managers von der eigenen Hauptinsel. Erst abwarten,
            # statt die Allianz um etwas zu bitten, das gleich ankommt.
            return
        a = self._neu(insel_id, niedrig)
        self._posten(a)
        k.melden("Rohstoffe angefragt",
                 f"{a['insel_name']}: {self._text(niedrig)} (unter {int(config.ANFRAGE_SCHWELLE * 100)} % "
                 f"des Lagers). Vorgang {a['vorgang']}.")

    def _neu(self, insel_id: int, rohstoffe: dict, manuell: dict | None = None) -> dict:
        k = self.k
        jetzt = k.jetzt()
        nr = k.state.naechste_vorgangs_nr()
        a = {"vorgang": f"{k.ich_id}-{nr}", "insel_id": insel_id, "insel": k.koord(insel_id),
             "insel_name": k.name(insel_id), "start": iso(jetzt),
             "bis": iso(jetzt + config.ANFRAGE_OHNE_BIS), "rohstoffe": dict(rohstoffe), "angebote": {}}
        if manuell is not None:
            a["manuell"] = dict(manuell)
        self.anfragen[str(insel_id)] = a
        k.state.data["eigene_vorgaenge"][a["vorgang"]] = {"obergrenze": a["bis"]}
        return a

    def _fertig(self, a: dict, res: dict, ziel: int) -> bool:
        """Automatisch angefragte Rohstoffe: wieder auf ANFRAGE_ZIEL. Von Hand
        angefragte: die angefragte Menge ist zugesagt und verschickt."""
        manuell = a.get("manuell") or {}
        unterwegs = self._zugesagt(a, nur_versandt=True)
        return (all(float(res.get(r, 0) or 0) >= ziel for r in a["rohstoffe"] if r not in manuell)
                and all(unterwegs[r] >= n for r, n in manuell.items()))

    def _zugesagt(self, a: dict, nur_versandt: bool = False) -> dict:
        """Summe je Rohstoff: bei verschickten Angeboten die laut [VERSANDT]
        tatsaechlich verschickte Menge, bei noch offenen Zusagen die zugesagte."""
        summe = {r: 0 for r in ROHSTOFFE}
        for x in a["angebote"].values():
            if x["status"] == "versandt":
                mengen = x.get("geliefert", x["zusage"])
            elif x["status"] == "zugesagt" and not nur_versandt:
                mengen = x["zusage"]
            else:
                continue
            for r, n in mengen.items():
                summe[r] += n
        return summe

    def manuell(self, rohstoffe: dict, insel_id: int | None = None) -> None:
        """Anfrage von Hand (Befehl anfrage_stellen.py), ohne Inselangabe fuer
        die aktuelle Insel. Laeuft dort schon eine Anfrage, werden die Mengen
        aufaddiert und neu gepostet."""
        k = self.k
        insel_id = insel_id or k.heimat_id
        a = self.anfragen.get(str(insel_id))
        if a is not None and k.jetzt() > zeit(a["bis"]):
            self._abschliessen(a, erledigt_posten=False)
            a = None
        if a is None:
            a = self._neu(insel_id, {}, manuell={})
        a.setdefault("manuell", {})
        for r, n in rohstoffe.items():
            a["rohstoffe"][r] = a["rohstoffe"].get(r, 0) + n
            a["manuell"][r] = a["manuell"].get(r, 0) + n
        self._posten(a)
        k.melden("Rohstoffe angefragt (von Hand)",
                 f"{a['insel_name']}: {self._text(rohstoffe)}. Vorgang {a['vorgang']}.")

    def beenden(self, a: dict) -> None:
        """Von Hand beenden (Seekampf-Hub): [ERLEDIGT] posten. Bereits zugesagte
        Lieferungen laufen weiter (agenten.md 8)."""
        log.info("Anfrage %s von Hand beendet", a["vorgang"])
        self._abschliessen(a, erledigt_posten=True)

    def _posten(self, a: dict) -> None:
        try:
            self.k.posten("forum:rohstoffe", "ANFRAGE", {
                "vorgang": a["vorgang"], "insel": a["insel"], "rohstoffe": a["rohstoffe"], "bis": a["bis"]})
        except (NichtGesendet, ApiError) as e:
            log.error("Anfrage nicht gepostet: %s", e)
        self.k.state.speichern()

    def _abschliessen(self, a: dict, erledigt_posten: bool) -> None:
        k = self.k
        if erledigt_posten:
            try:
                k.posten("forum:rohstoffe", "ERLEDIGT", {"vorgang": a["vorgang"]})
            except (NichtGesendet, ApiError) as e:
                log.error("ERLEDIGT nicht gepostet: %s", e)
            k.melden("Rohstoff-Anfrage erledigt", f"{a['insel_name']}: Vorgang {a['vorgang']} erledigt.")
        a["ende"] = iso(k.jetzt())
        k.state.data["anfragen_alt"] = (k.state.data["anfragen_alt"] + [a])[-20:]
        del self.anfragen[str(a["insel_id"])]
        k.state.speichern()

    # ------------------------------------------------------------ Nachrichten
    def angebot(self, f: dict, absender: int, zeitpunkt, msg_id: int) -> None:
        k = self.k
        a = self._aktiv(f["vorgang"])
        if not a or zeitpunkt > zeit(a["bis"]):
            return
        if "rohstoffe" not in f or k.jetzt() >= zeit(f["gueltig_bis"]):
            self._absage(absender, a["vorgang"], "abgelehnt")
            return
        zugesagt = self._zugesagt(a)
        nimm = {r: min(n, a["rohstoffe"].get(r, 0) - zugesagt[r])
                for r, n in f["rohstoffe"].items()}
        nimm = {r: n for r, n in nimm.items() if n > 0}
        name = k.mitglieder.get(absender, str(absender))
        if not nimm:
            self._absage(absender, a["vorgang"], "abgelehnt")
            a["angebote"][str(msg_id)] = {"helfer_id": absender, "status": "abgelehnt", "zusage": {}}
            return
        felder = {"vorgang": a["vorgang"]}
        if nimm != f["rohstoffe"]:
            felder["rohstoffe"] = nimm
        try:
            k.pn(absender, "ZUSAGE", felder)
        except (NichtGesendet, ApiError) as e:
            log.error("Zusage an %s fehlgeschlagen: %s", name, e)
            return
        a["angebote"][str(msg_id)] = {"helfer_id": absender, "status": "zugesagt", "zusage": nimm}
        k.state.speichern()
        k.melden("Rohstoffe zugesagt", f"{name} liefert {self._text(nimm)}.")

    def absage(self, f: dict, absender: int) -> None:
        a = self._vorgang(f["vorgang"])
        if not a:
            return
        for x in a["angebote"].values():
            if x["helfer_id"] == absender and x["status"] == "zugesagt":
                x["status"] = "abgesagt"
        self.k.state.speichern()

    def versandt(self, f: dict, absender: int, zeitpunkt) -> None:
        a = self._vorgang(f["vorgang"])
        if not a or "rohstoffe" not in f:
            return
        for x in a["angebote"].values():
            if x["helfer_id"] == absender and x["status"] == "zugesagt":
                x["status"] = "versandt"
                x["geliefert"] = {r: n for r, n in f["rohstoffe"].items() if r in x["zusage"]}
                break
        self.k.state.speichern()
        log.info("Rohstoffe unterwegs von %s: %s, Ankunft %s", self.k.mitglieder.get(absender),
                 f["rohstoffe"], f["ankunft"])

    def _absage(self, uid: int, vorgang: str, grund: str) -> None:
        try:
            self.k.pn(uid, "ABSAGE", {"vorgang": vorgang, "grund": grund})
        except (NichtGesendet, ApiError) as e:
            log.error("Absage an #%s fehlgeschlagen: %s", uid, e)

    @staticmethod
    def _text(rohstoffe: dict) -> str:
        return ", ".join(f"{n} {r}" for r, n in rohstoffe.items())
