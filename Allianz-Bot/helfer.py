"""Rolle Helfer: Beistand fuer andere Allianzmitglieder (agenten.md 7.2,
Faehigkeit beistand).

Vorgaben:
  - Nur fuer Mitglieder, deren Bot eine aktive Praesenz hat (Whitelist) -
    bei ihnen wird eine Zusage automatisch erfuellt.
  - Nur Speerkaempfer, hoechstens die Haelfte der eigenen (verliehene
    mitgerechnet, fremde Leihen bei uns nicht), und immer als Leihe. Das
    Protokoll erlaubt eine Leihe nur, wenn der Empfaenger leihe_rueckgabe
    meldet - anderen bieten wir deshalb nichts an.
  - Transportiert wird auf Kriegsschiffen. Fehlen sie im Hafen, duerfen
    Raid-Flotten in Rueckruf-Reichweite zurueckgerufen werden (erst nach der
    Zusage). Reicht es bis zum Ende der Versandfrist nicht, geht los, was
    passt - oder es gibt eine Absage.
  - Angeboten wird von der eigenen Insel, die am meisten bereitstellen kann -
    nie von einer Insel, die gerade selbst bedroht ist. Die 50-%-Grenze und der
    Schiffs-Rueckruf gelten je Insel.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta

import config
import steuerung
from api_client import ApiError
from kontext import (NichtGesendet, fahrzeit, iso, knoten, truppen_kapazitaet,
                     truppen_schiffe, zeit)

log = logging.getLogger("seekampf_allianz_bot")

EINHEIT = config.HILFE_EINHEIT
MAX_ANGEBOTE_JE_VORGANG = 3
VERSAND_PUFFER = timedelta(minutes=2)


class Helfer:
    def __init__(self, k):
        self.k = k

    @property
    def hilfe(self) -> dict:
        return self.k.state.data["hilfe"]

    @staticmethod
    def obergrenze(h: dict):
        if h.get("letzte_ankunft"):
            return zeit(h["letzte_ankunft"]) + config.NOTRUF_NACH_ANKUNFT
        return zeit(h["erste"]) + config.NOTRUF_OHNE_ANKUNFT

    # ------------------------------------------------------------ Nachrichten
    def notruf(self, f: dict, absender: int, zeitpunkt) -> None:
        k = self.k
        vorgang = f["vorgang"]
        h = self.hilfe.get(vorgang)
        if h is not None and k.jetzt() > self.obergrenze(h):
            return
        if h is None:
            h = {"eigentuemer": absender, "erste": iso(zeitpunkt), "ankunft": None,
                 "letzte_ankunft": None, "entwarnt": None, "angebot": None,
                 "angebote_n": 0, "versandt": False}
            self.hilfe[vorgang] = h
        h["insel"] = f["insel"]
        h["bedarf"] = f.get("bedarf") or {}
        h["angreifer"] = f.get("angreifer")
        if f.get("ankunft"):
            h["ankunft"] = f["ankunft"]
            if not h["letzte_ankunft"] or zeit(f["ankunft"]) > zeit(h["letzte_ankunft"]):
                h["letzte_ankunft"] = f["ankunft"]
        k.state.speichern()
        log.info("NOTRUF von %s (%s): Insel %s, Ankunft %s, Bedarf %s", k.mitglieder.get(absender),
                 vorgang, h["insel"], h["ankunft"] or "?", h["bedarf"] or "-")

    def entwarnung(self, f: dict, absender: int, zeitpunkt) -> None:
        h = self.hilfe.get(f["vorgang"])
        if h is None or h["entwarnt"]:
            return
        h["entwarnt"] = iso(zeitpunkt)
        if h["angebot"] and h["angebot"]["status"] == "offen":
            h["angebot"]["status"] = "beendet"
        self.k.state.speichern()
        log.info("ENTWARNUNG %s (%s)", f["vorgang"], f.get("ergebnis") or "ohne Ergebnis")

    def absage(self, f: dict, absender: int) -> None:
        h = self.hilfe.get(f["vorgang"])
        if h and h["angebot"] and h["angebot"]["status"] == "offen":
            h["angebot"]["status"] = "abgelehnt"
            self.k.state.speichern()
            log.info("Angebot fuer %s abgelehnt (%s)", f["vorgang"], f.get("grund"))

    def rueckgabe(self, f: dict, absender: int, zeitpunkt) -> None:
        k = self.k
        for l in k.state.data["verliehen"]:
            if l["vorgang"] == f["vorgang"] and l["empfaenger_id"] == absender and l["status"] == "offen":
                l["status"] = "zurueck"
                l["zurueck"] = f["einheiten"]
                l["zurueck_ankunft"] = f.get("ankunft")
                k.state.speichern()
                k.melden("Leihe kommt zurueck",
                         f"{l['empfaenger_name']} gibt {self._text(f['einheiten']) or 'nichts'} zurueck "
                         f"(verliehen: {self._text(l['einheiten'])}).")
                return

    def zusage(self, f: dict, absender: int, zeitpunkt) -> None:
        k = self.k
        vorgang = f["vorgang"]
        h = self.hilfe.get(vorgang)
        a = h and h["angebot"]
        if not a:
            return  # nie angeboten - keine Antwort
        if a["status"] != "offen":
            if a["status"] in ("abgelaufen", "beendet"):
                self._absage(absender, vorgang, "abgelaufen")
            return
        if zeitpunkt > zeit(a["gueltig_bis"]) or k.jetzt() > self.obergrenze(h):
            a["status"] = "abgelaufen"
            self._absage(absender, vorgang, "abgelaufen")
            return
        wunsch = f.get("einheiten", {EINHEIT: a["menge"]})
        if ("rohstoffe" in f or set(wunsch) != {EINHEIT}
                or not 1 <= wunsch[EINHEIT] <= a["menge"]):
            self._absage(absender, vorgang, "ungueltig")
            return  # Angebot bleibt offen, eine korrigierte Zusage ist moeglich
        von_insel = a.get("von_insel_id") or k.heimat_id
        if (absender not in k.whitelist() or k.bedroht(von_insel)
                or k.jetzt() > zeitpunkt + config.VERSAND_FRIST):
            a["status"] = "nicht_verfuegbar"
            self._absage(absender, vorgang, "nicht_verfuegbar")
            return
        a["status"] = "wartet"
        a["zusage"] = {"menge": wunsch[EINHEIT], "zeit": iso(zeitpunkt)}
        self._versand_versuchen(vorgang, h, a, erster_versuch=True)

    def _versand_versuchen(self, vorgang: str, h: dict, a: dict, erster_versuch: bool = False) -> None:
        """Zugesagte Speerkaempfer von der Insel aus dem Angebot losschicken -
        notfalls erst Raid-Flotten dieser Insel zurueckrufen.

        Fehlen Kriegsschiffe, werden beim ersten Versuch Raid-Flotten in
        Rueckruf-Reichweite gerufen. Dann wird gewartet, bis genug Schiffe
        daheim sind - laengstens bis kurz vor Ende der VERSAND_FRIST. Kommt bis
        dahin zu wenig zurueck, geht los, was passt.
        """
        k = self.k
        absender = h["eigentuemer"]
        insel_id = a.get("von_insel_id") or k.heimat_id
        jetzt = k.jetzt()
        frist = zeit(a["zusage"]["zeit"]) + config.VERSAND_FRIST
        if k.bedroht(insel_id) or jetzt > frist:
            self._nicht_verfuegbar(vorgang, a, "eigene Insel bedroht" if jetzt <= frist else "Versandfrist verstrichen")
            return
        ov = k.overview(insel_id, max_alter=0)
        menge = min(a["zusage"]["menge"], self._verfuegbar(insel_id, ov, ohne_vorgang=vorgang))
        if menge < 1:
            self._nicht_verfuegbar(vorgang, a, "keine Speerkaempfer mehr frei")
            return
        hafen = ov.get("schiffe") or {}
        hafen_kap = truppen_kapazitaet(hafen)
        if erster_versuch and hafen_kap < menge:
            kap, bereit = k.schiffe_beschaffen(insel_id, menge, bis=frist - timedelta(minutes=1),
                                               zweck=f"Beistand {vorgang}", ausfuehren=True)
            if kap > hafen_kap:
                a["warten_bis"] = iso(min(frist - timedelta(minutes=1), bereit + timedelta(seconds=90)))
                k.state.speichern()
                k.melden("Raid-Flotten zurueckgerufen",
                         f"Fuer den Beistand an {k.mitglieder.get(absender)} ({vorgang}) fehlten auf "
                         f"{k.name(insel_id)} Kriegsschiffe. Zurueck ca. "
                         f"{bereit.astimezone(k.tz).strftime('%H:%M:%S')}.")
        if hafen_kap < menge and a.get("warten_bis") and jetzt < zeit(a["warten_bis"]):
            return  # auf die zurueckgerufenen Schiffe warten
        menge = min(menge, hafen_kap)
        schiffe = truppen_schiffe(menge, hafen) if menge > 0 else {}
        if not schiffe:
            self._nicht_verfuegbar(vorgang, a, "keine Kriegsschiffe frei")
            return
        einheiten = {EINHEIT: menge}
        von = k.koord(insel_id)
        try:
            flotte = k.handel_schicken(insel_id, h["insel"], schiffe, einheiten)
        except ApiError as e:
            log.error("Beistand fuer %s nicht gestartet: %s", vorgang, e)
            self._nicht_verfuegbar(vorgang, a, str(e))
            return
        except NichtGesendet as e:
            a["status"] = "unklar"
            k.melden("Beistand unklar", f"Versand an {k.mitglieder.get(absender)}: {e}. Bitte pruefen.")
            return
        ankunft = zeit(flotte["arrive_at"]) if flotte.get("arrive_at") else k.jetzt() + fahrzeit(
            von, h["insel"], knoten(schiffe))
        try:
            k.pn(absender, "VERSANDT", {"vorgang": vorgang, "von": von, "einheiten": einheiten,
                                        "leihe": [EINHEIT], "ankunft": iso(ankunft)})
        except (NichtGesendet, ApiError) as e:
            log.error("VERSANDT-PN fehlgeschlagen: %s", e)
        a["status"] = "versandt"
        h["versandt"] = True
        name = k.mitglieder.get(absender, str(absender))
        k.state.data["verliehen"].append({
            "vorgang": vorgang, "von_insel_id": insel_id, "empfaenger_id": absender,
            "empfaenger_name": name, "einheiten": einheiten, "ankunft": iso(ankunft),
            "gesendet": iso(k.jetzt()), "status": "offen"})
        k.state.speichern()
        k.melden("Beistand verschickt",
                 f"{menge} Speerkaempfer als Leihe von {k.name(insel_id)} an {name} ({h['insel']}), "
                 f"Ankunft {ankunft.astimezone(k.tz).strftime('%H:%M')}.")

    def _nicht_verfuegbar(self, vorgang: str, a: dict, grund: str) -> None:
        h = self.hilfe[vorgang]
        a["status"] = "nicht_verfuegbar"
        self._absage(h["eigentuemer"], vorgang, "nicht_verfuegbar")
        self.k.melden("Hilfe nicht moeglich",
                      f"Zusage von {self.k.mitglieder.get(h['eigentuemer'])} ({vorgang}) nicht erfuellt: {grund}.")

    # ------------------------------------------------------------------ Tick
    def tick(self) -> None:
        k = self.k
        jetzt = k.jetzt()
        for vorgang, h in list(self.hilfe.items()):
            a = h["angebot"]
            if a and a["status"] == "wartet":
                self._versand_versuchen(vorgang, h, a)
            if a and a["status"] == "offen":
                if jetzt > zeit(a["gueltig_bis"]):
                    a["status"] = "abgelaufen"  # Reservierung frei, ohne Nachricht
                elif k.bedroht(a.get("von_insel_id") or k.heimat_id):
                    a["status"] = "zurueckgezogen"
                    self._absage(h["eigentuemer"], vorgang, "zurueckgezogen")
            if self._soll_anbieten(h, jetzt):
                self._anbieten(vorgang, h, jetzt)
        self._ueberfaellige(jetzt)
        self._aufraeumen(jetzt)
        k.state.speichern()

    def _soll_anbieten(self, h: dict, jetzt) -> bool:
        k = self.k
        a = h["angebot"]
        return (steuerung.will("beistand")
                and not h["entwarnt"] and not h["versandt"]
                and jetzt <= self.obergrenze(h)
                and (a is None or a["status"] in ("abgelaufen", "zurueckgezogen"))
                and h["angebote_n"] < MAX_ANGEBOTE_JE_VORGANG
                and (not h["ankunft"] or zeit(h["ankunft"]) > jetzt)
                and h["eigentuemer"] in k.whitelist()
                and k.kann(h["eigentuemer"], "leihe_rueckgabe"))

    def _verfuegbar(self, insel_id: int, ov: dict, ohne_vorgang: str | None = None) -> int:
        """Wie viele Speerkaempfer diese Insel noch hergeben darf (50-%-Regel je Insel)."""
        k = self.k
        heimat = k.heimat_id
        daheim = int((ov.get("truppen") or {}).get(EINHEIT, 0) or 0)
        # Fremde Leihen bei uns gehoeren nicht uns.
        geschuldet = sum(int(s["leihe"].get(EINHEIT, 0)) for s in k.state.data["schulden"]
                         if s["status"] == "offen" and (s.get("insel_id") or heimat) == insel_id)
        verliehen = sum(int(l["einheiten"].get(EINHEIT, 0)) for l in k.state.data["verliehen"]
                        if l["status"] == "offen" and (l.get("von_insel_id") or heimat) == insel_id)
        reserviert = sum(h["angebot"]["menge"] for v, h in self.hilfe.items()
                         if v != ohne_vorgang and h["angebot"]
                         and h["angebot"]["status"] in ("offen", "wartet")
                         and (h["angebot"].get("von_insel_id") or heimat) == insel_id)
        eigene = max(0, daheim - geschuldet)
        grenze = math.floor((eigene + verliehen) * config.HILFE_MAX_ANTEIL)
        return max(0, min(eigene, grenze - verliehen - reserviert))

    def _moeglichkeit(self, insel_id: int, vorgang: str, h: dict, jetzt) -> dict | None:
        """Was diese Insel anbieten koennte: Menge, Ankunft, Gueltigkeit - oder None."""
        k = self.k
        if k.bedroht(insel_id):
            return None
        ov = k.overview(insel_id)
        hafen = ov.get("schiffe") or {}
        menge = self._verfuegbar(insel_id, ov)
        if h["bedarf"].get(EINHEIT):
            menge = min(menge, int(h["bedarf"][EINHEIT]))
        if menge < 1:
            return None
        verzug = timedelta(0)
        if truppen_kapazitaet(hafen) >= menge:
            kn = knoten(truppen_schiffe(menge, hafen))
        else:
            # Raid-Flotten mitzaehlen, die nach einer Zusage zurueckgerufen werden koennten.
            kap, bereit = k.schiffe_beschaffen(insel_id, menge, bis=jetzt + config.VERSAND_FRIST,
                                               zweck=f"Angebot {vorgang}", ausfuehren=False)
            menge = kap
            verzug = bereit - jetzt
            kn = min(config.SCHIFF_KNOTEN[t] for t in config.TRUPPEN_SCHIFFE)
        if menge < 1:
            return None
        von = k.koord(insel_id)
        weg = fahrzeit(von, h["insel"], kn)
        ankunft = jetzt + VERSAND_PUFFER + verzug + weg
        gueltig = jetzt + config.ANGEBOT_GUELTIG
        if h["ankunft"]:
            angriff = zeit(h["ankunft"])
            if ankunft >= angriff:
                return None  # kaeme zu spaet
            gueltig = min(gueltig, angriff - weg - verzug - VERSAND_PUFFER)
        if gueltig - jetzt < timedelta(minutes=3):
            return None
        return {"insel_id": insel_id, "von": von, "menge": menge, "ankunft": ankunft, "gueltig": gueltig}

    def _anbieten(self, vorgang: str, h: dict, jetzt) -> None:
        """Von der Insel anbieten, die am meisten bereitstellen kann (bei
        Gleichstand die, deren Truppen frueher ankommen)."""
        k = self.k
        optionen = [o for o in (self._moeglichkeit(iid, vorgang, h, jetzt) for iid in k.inseln()) if o]
        if not optionen:
            return
        o = max(optionen, key=lambda o: (o["menge"], -o["ankunft"].timestamp()))
        felder = {"vorgang": vorgang, "von": o["von"], "einheiten": {EINHEIT: o["menge"]},
                  "leihe": [EINHEIT], "ankunft": iso(o["ankunft"]), "gueltig_bis": iso(o["gueltig"])}
        try:
            k.pn(h["eigentuemer"], "ANGEBOT", felder)
        except (NichtGesendet, ApiError) as e:
            log.error("Angebot fuer %s nicht gesendet: %s", vorgang, e)
            h["angebote_n"] += 1
            return
        h["angebot"] = {"menge": o["menge"], "von_insel_id": o["insel_id"], "gesendet": iso(jetzt),
                        "gueltig_bis": iso(o["gueltig"]), "ankunft": iso(o["ankunft"]), "status": "offen"}
        h["angebote_n"] += 1
        k.melden("Beistand angeboten",
                 f"{o['menge']} Speerkaempfer (Leihe) von {k.name(o['insel_id'])} an "
                 f"{k.mitglieder.get(h['eigentuemer'])} ({h['insel']}), gueltig bis "
                 f"{o['gueltig'].astimezone(k.tz).strftime('%H:%M')}.")

    def _ueberfaellige(self, jetzt) -> None:
        k = self.k
        for l in k.state.data["verliehen"]:
            if l["status"] != "offen":
                continue
            h = self.hilfe.get(l["vorgang"])
            if h is None:
                continue
            ende = zeit(h["entwarnt"]) if h["entwarnt"] else self.obergrenze(h)
            if jetzt < ende:
                continue
            faellig = max(ende, zeit(l["ankunft"])) + config.RUECKGABE_FRIST
            if jetzt > faellig:
                k.einmal_melden(f"leihe_ueberfaellig:{l['vorgang']}:{l['empfaenger_id']}",
                                "Leihe ueberfaellig",
                                f"{l['empfaenger_name']} hat {self._text(l['einheiten'])} aus Vorgang "
                                f"{l['vorgang']} nicht bis {faellig.astimezone(k.tz).strftime('%d.%m. %H:%M')} "
                                f"zurueckgegeben.")

    def _aufraeumen(self, jetzt) -> None:
        offen = {l["vorgang"] for l in self.k.state.data["verliehen"] if l["status"] == "offen"}
        for vorgang, h in list(self.hilfe.items()):
            if vorgang not in offen and jetzt > self.obergrenze(h) + timedelta(days=2):
                del self.hilfe[vorgang]
        grenze = jetzt - timedelta(days=30)
        self.k.state.data["verliehen"] = [
            l for l in self.k.state.data["verliehen"]
            if l["status"] == "offen" or zeit(l["gesendet"]) > grenze]

    def _absage(self, uid: int, vorgang: str, grund: str) -> None:
        try:
            self.k.pn(uid, "ABSAGE", {"vorgang": vorgang, "grund": grund})
        except (NichtGesendet, ApiError) as e:
            log.error("Absage an #%s fehlgeschlagen: %s", uid, e)

    @staticmethod
    def _text(einheiten: dict) -> str:
        return ", ".join(f"{n} {t}" for t, n in (einheiten or {}).items())

