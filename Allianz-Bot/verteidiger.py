"""Rolle Verteidiger: Notruf, Zusagen, Entwarnung, Leihe-Rueckgabe
(agenten.md 7.1, Faehigkeiten notruf und leihe_rueckgabe).

Vorgaben:
  - Bei jedem Angriff auf eine eigene Insel geht ein Notruf raus - je Insel ein
    eigener Vorgang. Neue Inseln werden automatisch mit ueberwacht.
  - Bedarf an Speerkaempfern: so viele, dass mindestens MIN_SPEERKAEMPFER auf
    der Insel stehen, und so viele, dass die geschaetzte Angriffsstaerke
    gedeckt ist - der groessere Wert gilt.
  - Geschenke und Leihen werden angenommen, Geschenke bevorzugt: Leih-Anteile
    erst nach einer kurzen Wartezeit, in der noch Geschenke eintreffen koennen.
  - Steinewerfer nur als Geschenk, nie als Leihe.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta

import berichte
import config
import steuerung
from api_client import ApiError
from kontext import NichtGesendet, iso, truppen_kapazitaet, truppen_schiffe, zeit

log = logging.getLogger("seekampf_allianz_bot")

DEF = config.EINHEIT_VERTEIDIGUNG


def _verteidigungswert(einheiten: dict) -> float:
    return sum(DEF.get(t, 0) * int(n) for t, n in (einheiten or {}).items())


def _auffuellen(nimm: dict, vorrat: dict, bedarf: float) -> float:
    """Aus `vorrat` so viel in `nimm` legen, bis `bedarf` Verteidigungspunkte gedeckt sind."""
    for typ in sorted(vorrat, key=lambda t: -DEF.get(t, 0)):
        if bedarf <= 0:
            break
        wert = DEF.get(typ, 0)
        if wert <= 0:
            continue
        n = min(int(vorrat[typ]), math.ceil(bedarf / wert))
        if n > 0:
            nimm[typ] = nimm.get(typ, 0) + n
            bedarf -= n * wert
    return bedarf


class Verteidiger:
    def __init__(self, k):
        self.k = k

    @property
    def notrufe(self) -> dict:
        """Aktive eigene Notrufe, Schluessel = Insel-ID (als String)."""
        return self.k.state.data["notrufe"]

    def _aktiv(self, vorgang: str) -> dict | None:
        return next((n for n in self.notrufe.values() if n["vorgang"] == vorgang), None)

    def _vorgang(self, vorgang: str) -> dict | None:
        n = self._aktiv(vorgang)
        if n:
            return n
        return next((a for a in self.k.state.data["notrufe_alt"] if a["vorgang"] == vorgang), None)

    def besitzt(self, vorgang: str) -> bool:
        return self._vorgang(vorgang) is not None

    @staticmethod
    def obergrenze(n: dict):
        if n.get("letzte_ankunft"):
            return zeit(n["letzte_ankunft"]) + config.NOTRUF_NACH_ANKUNFT
        return zeit(n["start"]) + config.NOTRUF_OHNE_ANKUNFT

    # ------------------------------------------------------------------ Tick
    def tick(self) -> None:
        for insel_id in self.k.inseln():
            self._angriff_pruefen(insel_id)
        for n in list(self.notrufe.values()):
            self._angebote_entscheiden(n)
            self._entwarnung_pruefen(n)
        self._rueckgaben()

    # ---------------------------------------------------------------- Notruf
    def _angriff_pruefen(self, insel_id: int) -> None:
        k = self.k
        feinde = k.feindliche_flotten(insel_id, max_alter=config.INCOMING_INTERVALL_S)
        if not feinde:
            return
        jetzt = k.jetzt()
        ankuenfte = [zeit(f["arrive_at"]) for f in feinde if f.get("arrive_at")]
        kommend = [a for a in ankuenfte if a > jetzt]
        angreifer = sorted({(f.get("absender") or "unbekannt") for f in feinde})

        n = self.notrufe.get(str(insel_id))
        neu = n is None
        if neu and not steuerung.will("notruf"):
            erste = min(ankuenfte).isoformat() if ankuenfte else "?"
            k.einmal_melden(f"angriff_ohne_notruf:{insel_id}:{erste}", "Angriff ohne Notruf",
                            f"{k.name(insel_id)} ({k.koord(insel_id)}) wird angegriffen von "
                            f"{', '.join(angreifer)}. Notruf ist im Seekampf-Hub abgeschaltet - "
                            f"es geht kein [NOTRUF] raus.")
            return
        if neu:
            nr = k.state.naechste_vorgangs_nr()
            n = {"vorgang": f"{k.ich_id}-{nr}", "insel_id": insel_id, "insel": k.koord(insel_id),
                 "insel_name": k.name(insel_id), "start": iso(jetzt), "ankunft": None,
                 "letzte_ankunft": None, "angreifer": [], "staerke": 0, "quelle": "",
                 "bedarf": 0, "signatur": None, "angebote": {}, "entwarnt": None,
                 "ergebnis": None}
            self.notrufe[str(insel_id)] = n

        if kommend:
            n["ankunft"] = iso(min(kommend))
        if ankuenfte:
            spaeteste = max(ankuenfte)
            if not n["letzte_ankunft"] or spaeteste > zeit(n["letzte_ankunft"]):
                n["letzte_ankunft"] = iso(spaeteste)

        if angreifer != n["angreifer"]:
            bekannte = [a for a in angreifer if a != "unbekannt"]
            staerke, quelle = berichte.schaetze_angriff(k, bekannte)
            n["angreifer"], n["staerke"], n["quelle"] = angreifer, max(n["staerke"], staerke), quelle
            ov = k.overview(insel_id, max_alter=0)
            speer = int((ov.get("truppen") or {}).get("speerkaempfer", 0) or 0)
            fehlt = max(0.0, n["staerke"] - berichte.verteidigung(ov))
            fuer_staerke = math.ceil(fehlt / (DEF["speerkaempfer"] * berichte.mauer_faktor(ov)))
            n["bedarf"] = max(n["bedarf"], config.MIN_SPEERKAEMPFER - speer, fuer_staerke, 0)

        k.state.data["eigene_vorgaenge"][n["vorgang"]] = {"obergrenze": iso(self.obergrenze(n))}
        signatur = [n["ankunft"], n["angreifer"], n["bedarf"], n["staerke"]]
        if signatur == n["signatur"]:
            return
        felder = {
            "vorgang": n["vorgang"], "insel": n["insel"], "ankunft": n["ankunft"],
            "angreifer": ", ".join(a.replace(".", " ") for a in n["angreifer"]),
            "staerke": n["staerke"] or None,
            "bedarf": {"speerkaempfer": n["bedarf"]} if n["bedarf"] > 0 else None,
        }
        try:
            k.posten("forum:notrufe", "NOTRUF", felder)
        except (NichtGesendet, ApiError) as e:
            log.error("Notruf nicht gepostet: %s", e)
        n["signatur"] = signatur
        if neu:
            for uid in sorted(k.whitelist()):
                if k.kann(uid, "beistand"):
                    try:
                        k.pn(uid, "NOTRUF", felder)
                    except (NichtGesendet, ApiError) as e:
                        log.error("Weck-PN an #%s fehlgeschlagen: %s", uid, e)
        k.state.speichern()
        ankunft = zeit(n["ankunft"]).astimezone(k.tz).strftime("%H:%M") if n["ankunft"] else "?"
        k.melden("Notruf" if neu else "Notruf aktualisiert",
                 f"Angriff auf {n['insel_name']} ({n['insel']}) von {', '.join(n['angreifer'])}, "
                 f"Ankunft {ankunft}.\n"
                 f"Geschaetzte Staerke {n['staerke']} ({n['quelle'] or 'keine Daten'}), "
                 f"Bedarf {n['bedarf']} Speerkaempfer. Vorgang {n['vorgang']}.")

    # -------------------------------------------------------- Nachrichten
    def angebot(self, f: dict, absender: int, zeitpunkt, msg_id: int) -> None:
        k = self.k
        n = self._aktiv(f["vorgang"])
        if not n or n["entwarnt"]:
            return
        if k.jetzt() > self.obergrenze(n):
            return
        if "einheiten" not in f:
            self._absage(absender, n["vorgang"], "abgelehnt")
            return
        for a in n["angebote"].values():
            if a["helfer_id"] == absender and a["status"] == "offen":
                a["status"] = "ersetzt"
        n["angebote"][str(msg_id)] = {
            "helfer_id": absender, "helfer_name": k.mitglieder.get(absender, str(absender)),
            "von": f["von"], "ankunft": f["ankunft"], "einheiten": f["einheiten"],
            "leihe": f.get("leihe") or [], "gueltig_bis": f["gueltig_bis"],
            "empfangen": iso(zeitpunkt), "status": "offen", "zusage": None,
        }
        k.state.speichern()
        log.info("ANGEBOT von %s fuer %s: %s (Leihe: %s)", k.mitglieder.get(absender), n["vorgang"],
                 f["einheiten"], f.get("leihe") or "keine")

    def absage(self, f: dict, absender: int) -> None:
        n = self._vorgang(f["vorgang"])
        if not n:
            return
        for a in n["angebote"].values():
            if a["helfer_id"] == absender and a["status"] in ("offen", "zugesagt"):
                a["status"] = "abgesagt"
                log.info("ABSAGE von %s fuer %s (%s)", a["helfer_name"], f["vorgang"], f.get("grund"))
        self.k.state.speichern()

    def versandt(self, f: dict, absender: int, zeitpunkt) -> None:
        k = self.k
        n = self._vorgang(f["vorgang"])
        if not n or "einheiten" not in f:
            return
        for a in n["angebote"].values():
            if a["helfer_id"] == absender and a["status"] == "zugesagt":
                a["status"] = "versandt"
        leihe = {t: int(f["einheiten"][t]) for t in f.get("leihe") or [] if f["einheiten"].get(t)}
        name = k.mitglieder.get(absender, str(absender))
        if leihe:
            k.state.data["schulden"].append({
                "vorgang": f["vorgang"], "insel_id": n.get("insel_id"), "helfer_id": absender,
                "helfer_name": name, "von": f["von"], "leihe": leihe, "ankunft": f["ankunft"],
                "erhalten": iso(zeitpunkt), "status": "offen"})
        k.state.speichern()
        k.melden("Hilfe unterwegs",
                 f"{name} schickt {self._text(f['einheiten'])}, Ankunft "
                 f"{zeit(f['ankunft']).astimezone(k.tz).strftime('%H:%M')}.\n"
                 f"Leihe (spaeter zurueck): {self._text(leihe) if leihe else 'nichts, alles geschenkt'}.")

    # ------------------------------------------------------ Angebote waehlen
    def _angebote_entscheiden(self, n: dict) -> None:
        k = self.k
        if n["entwarnt"] or not n["angebote"]:
            return
        jetzt = k.jetzt()
        kommend = [zeit(f["arrive_at"]) for f in k.feindliche_flotten(n["insel_id"], max_alter=config.INCOMING_INTERVALL_S)
                   if f.get("arrive_at") and zeit(f["arrive_at"]) > jetzt]
        angriff = min(kommend) if kommend else None

        gedeckt = sum(_verteidigungswert(a["zusage"]) for a in n["angebote"].values()
                      if a["status"] in ("zugesagt", "versandt"))
        rest = n["bedarf"] * DEF["speerkaempfer"] - gedeckt

        def reihenfolge(item):
            a = item[1]
            geschenk = sum(v for t, v in a["einheiten"].items() if t not in a["leihe"])
            gesamt = sum(a["einheiten"].values()) or 1
            return (-geschenk / gesamt, a["ankunft"])

        for msg_id, a in sorted(n["angebote"].items(), key=reihenfolge):
            if a["status"] != "offen":
                continue
            if jetzt >= zeit(a["gueltig_bis"]):
                a["status"] = "abgelaufen"
                continue
            if angriff is None or zeit(a["ankunft"]) >= angriff or rest <= 0:
                self._absage(a["helfer_id"], n["vorgang"], "abgelehnt")
                a["status"] = "abgelehnt"
                continue
            geschenk = {t: v for t, v in a["einheiten"].items() if t not in a["leihe"]}
            # Ohne gewuenschte Faehigkeit leihe_rueckgabe nur Geschenke (agenten.md 7.1, 4.).
            leihe = {t: v for t, v in a["einheiten"].items()
                     if t in a["leihe"] and t in config.LEIHE_ERLAUBT and steuerung.will("leihe_rueckgabe")}
            leihe_ok = (jetzt - zeit(a["empfangen"]) >= config.LEIHE_WARTEZEIT
                        or angriff - jetzt <= config.LEIHE_SOFORT_WENN_ANGRIFF_IN
                        or zeit(a["gueltig_bis"]) - jetzt <= timedelta(minutes=3))
            nimm: dict[str, int] = {}
            nach_geschenk = _auffuellen(nimm, geschenk, rest)
            if leihe and nach_geschenk > 0:
                if not leihe_ok:
                    continue  # auf moegliche Geschenke warten, dann neu entscheiden
                nach_geschenk = _auffuellen(nimm, leihe, nach_geschenk)
            if not nimm:
                self._absage(a["helfer_id"], n["vorgang"], "abgelehnt")
                a["status"] = "abgelehnt"
                continue
            felder = {"vorgang": n["vorgang"]}
            if nimm != a["einheiten"]:
                felder["einheiten"] = nimm
            try:
                k.pn(a["helfer_id"], "ZUSAGE", felder)
            except (NichtGesendet, ApiError) as e:
                log.error("Zusage an %s fehlgeschlagen: %s", a["helfer_name"], e)
                continue
            a["status"], a["zusage"] = "zugesagt", nimm
            rest = nach_geschenk
            geliehen = {t: v for t, v in nimm.items() if t in a["leihe"]}
            k.melden("Hilfe zugesagt",
                     f"{a['helfer_name']}: {self._text(nimm)}"
                     + (f" (davon Leihe: {self._text(geliehen)})" if geliehen else " (Geschenk)"))
        k.state.speichern()

    def _absage(self, uid: int, vorgang: str, grund: str) -> None:
        try:
            self.k.pn(uid, "ABSAGE", {"vorgang": vorgang, "grund": grund})
        except (NichtGesendet, ApiError) as e:
            log.error("Absage an #%s fehlgeschlagen: %s", uid, e)

    # ------------------------------------------------------------ Entwarnung
    def _entwarnung_pruefen(self, n: dict) -> None:
        k = self.k
        jetzt = k.jetzt()
        vorbei = jetzt > self.obergrenze(n)
        feinde = k.feindliche_flotten(n["insel_id"], max_alter=config.INCOMING_INTERVALL_S)
        gelandet = not n["letzte_ankunft"] or jetzt > zeit(n["letzte_ankunft"]) + timedelta(minutes=1)
        if not vorbei and (feinde or not gelandet):
            return
        if not vorbei:
            berichte.aktualisieren(k)
            start = zeit(n["start"]) - timedelta(minutes=5)
            kaempfe = [b for b in k.state.data["berichte"].values()
                       if zeit(b["zeit"]) and zeit(b["zeit"]) >= start and b.get("insel") in (None, n["insel"])]
            n["ergebnis"] = None if not kaempfe else ("verloren" if any(not b["sieg"] for b in kaempfe) else "gehalten")
            try:
                k.posten("forum:notrufe", "ENTWARNUNG", {"vorgang": n["vorgang"], "ergebnis": n["ergebnis"]})
            except (NichtGesendet, ApiError) as e:
                log.error("Entwarnung nicht gepostet: %s", e)
            n["entwarnt"] = iso(jetzt)
        else:
            n["entwarnt"] = iso(self.obergrenze(n))
        for a in n["angebote"].values():
            if a["status"] == "offen":
                a["status"] = "beendet"
        k.state.data["notrufe_alt"] = (k.state.data["notrufe_alt"] + [n])[-20:]
        del self.notrufe[str(n["insel_id"])]
        k.state.speichern()
        k.melden("Entwarnung", f"{n['insel_name']}: Vorgang {n['vorgang']} beendet"
                 + (f", Insel {n['ergebnis']}." if n["ergebnis"] else "."))

    # ------------------------------------------------------------- Rueckgabe
    def _rueckgaben(self) -> None:
        k = self.k
        offen = [s for s in k.state.data["schulden"] if s["status"] == "offen"]
        if not offen:
            return
        jetzt = k.jetzt()
        berichte_frisch = False
        for s in offen:
            n = self._vorgang(s["vorgang"])
            if n is None or not n.get("entwarnt"):
                continue
            entwarnt, ankunft = zeit(n["entwarnt"]), zeit(s["ankunft"])
            beginn = max(entwarnt, ankunft)
            if jetzt < beginn:
                continue
            faellig = beginn + config.RUECKGABE_FRIST
            insel_id = s.get("insel_id") or k.heimat_id
            if k.bedroht(insel_id):
                k.einmal_melden(f"rueckgabe_wartet_angriff:{s['vorgang']}:{s['helfer_id']}",
                                "Rueckgabe wartet",
                                f"Leihe an {s['helfer_name']} wartet, weil gerade ein Angriff anfliegt.")
                continue
            if "soll" not in s:
                # Einmal festlegen, was wir schulden - spaetere Kaempfe aendern das nicht mehr.
                if not berichte_frisch:
                    berichte.aktualisieren(k)
                    berichte_frisch = True
                soll = {t: math.floor(v * berichte.ueberlebensquote(k, t, ankunft, entwarnt, k.koord(insel_id)))
                        for t, v in s["leihe"].items()}
                s["soll"] = {t: v for t, v in soll.items() if v > 0}
                s["gesendet"] = {}
                s["letzte_ankunft"] = None
            rest = {t: v - s["gesendet"].get(t, 0) for t, v in s["soll"].items()}
            rest = {t: v for t, v in rest.items() if v > 0}
            if not rest:
                self._rueckgabe_melden(s)
                continue
            ov = k.overview(insel_id, max_alter=0)
            daheim = ov.get("truppen") or {}
            hafen = ov.get("schiffe") or {}
            frist_knapp = jetzt >= faellig - timedelta(minutes=30)
            benoetigt = sum(min(v, int(daheim.get(t, 0) or 0)) for t, v in rest.items())
            hafen_kap = truppen_kapazitaet(hafen)
            if 0 < hafen_kap < benoetigt or (hafen_kap == 0 and benoetigt > 0):
                # Zu wenig Kriegsschiffe: einmal Raid-Flotten zurueckrufen und auf sie warten.
                if not s.get("warten_bis"):
                    kap, bereit = k.schiffe_beschaffen(
                        insel_id, benoetigt, bis=faellig, zweck=f"Leihe-Rueckgabe an {s['helfer_name']}", ausfuehren=True)
                    s["warten_bis"] = iso(bereit + timedelta(seconds=90) if kap > hafen_kap else jetzt)
                    k.state.speichern()
                    if kap > hafen_kap:
                        k.melden("Raid-Flotten zurueckgerufen",
                                 f"Fuer die Leihe-Rueckgabe an {s['helfer_name']} fehlten Kriegsschiffe. "
                                 f"Zurueck ca. {bereit.astimezone(k.tz).strftime('%H:%M:%S')}.")
                if jetzt < zeit(s["warten_bis"]):
                    continue
            s["warten_bis"] = None
            # Was nicht daheim ist (z. B. Steinewerfer beim Raiden), wartet bis kurz
            # vor Fristende; danach gilt das bis dahin Zurueckgeschickte.
            ladung, frei = {}, truppen_kapazitaet(hafen)
            for t, v in rest.items():
                n = min(v, int(daheim.get(t, 0) or 0), frei)
                if n > 0:
                    ladung[t] = n
                    frei -= n
            if not ladung:
                if frist_knapp and not any(int(daheim.get(t, 0) or 0) for t in rest):
                    self._rueckgabe_melden(s)
                elif jetzt > faellig - timedelta(hours=1):
                    k.einmal_melden(f"rueckgabe_keine_schiffe:{s['vorgang']}:{s['helfer_id']}",
                                    "Rueckgabe: keine Schiffe",
                                    f"Leihe an {s['helfer_name']} ({self._text(rest)}) ist bis "
                                    f"{faellig.astimezone(k.tz).strftime('%d.%m. %H:%M')} faellig, "
                                    f"aber es liegen keine Kriegsschiffe im Hafen.")
                continue
            try:
                flotte = k.handel_schicken(insel_id, s["von"], truppen_schiffe(sum(ladung.values()), hafen), ladung)
            except ApiError as e:
                log.error("Rueckgabe an %s nicht gestartet: %s", s["helfer_name"], e)
                continue
            except NichtGesendet as e:
                s["status"] = "unklar"
                k.melden("Rueckgabe unklar", f"Rueckgabe an {s['helfer_name']}: {e}. Bitte selbst pruefen.")
                continue
            for t, n in ladung.items():
                s["gesendet"][t] = s["gesendet"].get(t, 0) + n
            s["letzte_ankunft"] = flotte.get("arrive_at") or s["letzte_ankunft"]
            k.state.speichern()
            if all(s["gesendet"].get(t, 0) >= v for t, v in s["soll"].items()):
                self._rueckgabe_melden(s)
        k.state.speichern()

    def _rueckgabe_melden(self, s: dict) -> None:
        k = self.k
        einheiten = dict(s.get("gesendet") or {})
        ankunft = s.get("letzte_ankunft")
        felder = {"vorgang": s["vorgang"], "einheiten": einheiten,
                  "ankunft": iso(zeit(ankunft)) if ankunft else None}
        try:
            k.pn(s["helfer_id"], "RUECKGABE", felder)
        except (NichtGesendet, ApiError) as e:
            log.error("RUECKGABE-PN an %s fehlgeschlagen: %s", s["helfer_name"], e)
        s["status"] = "erledigt"
        s["zurueck"] = einheiten
        s["zurueck_am"] = iso(k.jetzt())
        k.melden("Leihe zurueckgegeben",
                 f"An {s['helfer_name']}: {self._text(einheiten) if einheiten else 'nichts uebrig'} "
                 f"(geliehen: {self._text(s['leihe'])}).")

    @staticmethod
    def _text(einheiten: dict) -> str:
        return ", ".join(f"{n} {t}" for t, n in einheiten.items()) or "nichts"
