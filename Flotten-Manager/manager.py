"""Die Schleife, die die Flotten fahren laesst.

Ablauf eines Ticks:
  1. Laufzeit fortschreiben (zaehlt nur, solange der Manager laeuft)
  2. GET /fleets - Ankunft und Rueckkehr der eigenen Flotten erkennen
  3. Kampfberichte zu angekommenen Flotten aufloesen (Sieg? Beute?) und,
     wenn gewuenscht, die eigenen davon im Postfach ausblenden
  4. Je eingeschalteter Insel: Pausen pruefen (Bedrohung im Anflug, Lager
     voll) und so viele Flotten losschicken, wie ihre Schiffe und
     Steinewerfer hergeben

Eine Flotte verschwindet aus GET /fleets, sobald sie wieder daheim ist - genau
dann ist ihr Schiff wieder frei und Schritt 5 schickt sie sofort zum naechsten
Ziel der Rotation.
"""
from __future__ import annotations

import copy
import itertools
import logging
import time
from datetime import datetime, timezone

import requests

import config
import geo
import scanner
from api_client import ApiError
from ausgleich import Ausgleich, reserve_lesen
from state import neue_insel

log = logging.getLogger("seekampf_flotten_manager")

# Steht ein Ziel unter Anfaengerschutz, wird es so lange uebersprungen, statt
# es in jeder Runde erneut anzufahren.
ANFAENGERSCHUTZ_SPERRE_S = 6 * 3600


def iso_zu_epoch(wert: str | None) -> float | None:
    if not wert:
        return None
    try:
        dt = datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def fmt_dauer(sekunden: float | None) -> str:
    if sekunden is None:
        return "-"
    sekunden = max(0, int(sekunden))
    if sekunden < 60:
        return f"{sekunden}s"
    h, rest = divmod(sekunden, 3600)
    m, s = divmod(rest, 60)
    if h >= 24:
        t, h = divmod(h, 24)
        return f"{t}d {h}h"
    return f"{h}h {m:02d}min" if h else f"{m}min {s:02d}s"


def waehle_schiffe(typen: tuple[str, ...], anzahl: int, vorrat: dict | None) -> dict:
    """`anzahl` Schiffe der Klasse `typen` zusammenstellen.

    Der Typ ist egal - es zaehlt nur, dass genug Schiffe der Klasse da sind.
    Gefuellt wird in der Reihenfolge von `typen` (die Vorliebe aus config),
    notfalls gemischt: 2 Handelsschiffe koennen auch 1 grosses + 1 kleines sein.
    Ohne `vorrat` (None) wird der bevorzugte Typ angenommen - das braucht der
    Scan, der nur die Geschwindigkeit abschaetzen will. Reicht der Bestand
    nicht fuer die volle Anzahl, kommt {} zurueck.
    """
    if anzahl <= 0:
        return {}
    if vorrat is None:
        return {typen[0]: anzahl}
    gewaehlt: dict[str, int] = {}
    offen = anzahl
    for typ in typen:
        if offen <= 0:
            break
        n = min(offen, int(vorrat.get(typ, 0) or 0))
        if n > 0:
            gewaehlt[typ] = n
            offen -= n
    return gewaehlt if offen <= 0 else {}


def fmt_beute(beute: dict | None) -> str:
    if not beute:
        return "nichts"
    teile = [f"{int(beute.get(r, 0) or 0)} {r.capitalize()}" for r in config.RESOURCE_KEYS
             if (beute.get(r) or 0) > 0]
    return ", ".join(teile) if teile else "nichts"


class FlottenManager:
    def __init__(self, client, state, notifier=None):
        self.client = client
        self.state = state
        self.notifier = notifier

        # Insel, die im Spiel gerade ausgewaehlt ist - bekommt beim Umstieg
        # auf mehrere Inseln den Altbestand und ist als einzige sofort aktiv.
        self.heimat_id: int | None = None
        self.gestartet = False

        self.letzter_fehler: str | None = None
        self.letzter_tick: float | None = None

        self._overview: dict[int, dict] = {}
        self._overview_zeit = 0.0
        self._letzter_tick_ts: float | None = None
        self._dirty = False
        self._letzte_speicherung = 0.0
        self.ausgleich = Ausgleich(self)

    # ------------------------------------------------------------------ Basis
    @property
    def settings(self) -> dict:
        return self.state.settings

    def s(self, insel_id) -> dict:
        """Die fuer diese Insel wirksamen Einstellungen (eigene vor globalen)."""
        return self.state.insel_settings(insel_id)

    def flotte_bedarf(self, insel_id) -> list[tuple[tuple[str, ...], int]]:
        """Was eine Flotte dieser Insel an Schiffen braucht: je Klasse eine Anzahl."""
        s = self.s(insel_id)
        handel = config.HANDELSSCHIFF_TYPEN
        if not s["grosse_handelsschiffe"]:
            handel = tuple(t for t in handel if t != "grosses_handelsschiff")
        return [(config.KRIEGSSCHIFF_TYPEN, int(s["flotte_kriegsschiffe"])),
                (handel, int(s["flotte_handelsschiffe"]))]

    @staticmethod
    def vorrat(ov: dict) -> dict:
        """Was daheim liegt - Schiffe und Truppen in einem Topf.

        truppen/schiffe in der Uebersicht zaehlen nur, was NICHT unterwegs ist.
        """
        bestand = dict(ov.get("schiffe") or {})
        bestand.update(ov.get("truppen") or {})
        return bestand

    def vorrat_fuer_raids(self, insel_id: int, ov: dict) -> dict:
        """Wie vorrat(), aber ohne die Handelsschiffe, die der Rohstoff-Ausgleich
        gerade fuer eine Lieferung zurueckhaelt."""
        bestand = self.vorrat(ov)
        if insel_id in self.ausgleich.reserviert:
            for typ in config.HANDELSSCHIFF_TYPEN:
                bestand[typ] = 0
        # Kriegsschiffe, die der Ausbildungs-Bot gerade fuer einen Truppentransport braucht.
        transport = reserve_lesen(config.AUSBILDUNG_RESERVE_PATH, "kriegsschiffe").get(str(insel_id))
        for typ, n in (transport or {}).items():
            if typ in bestand:
                bestand[typ] = max(0, int(bestand[typ] or 0) - int(n or 0))
        # Begleitung, die der Kolonisations-Bot fuer ein bewohntes Ziel zurueckhaelt
        # (erst kurz vor Fertigstellung des Kolonisationsschiffs).
        kolo = reserve_lesen(config.KOLO_RESERVE_PATH, "einheiten").get(str(insel_id))
        for typ, n in (kolo or {}).items():
            if typ in bestand:
                bestand[typ] = max(0, int(bestand[typ] or 0) - int(n or 0))
        return bestand

    def flotte_ships(self, insel_id, vorrat: dict | None = None) -> dict:
        """Die Schiffe EINER Flotte, mit konkreten Typen.

        Die Einstellung gibt nur vor, wie viele Kriegs- und Handelsschiffe
        mitfahren; welcher Typ das ist, sucht sich der Manager aus `vorrat`.
        Ohne `vorrat` kommt die Wunschbesetzung (bevorzugter Typ je Klasse),
        reicht der Bestand nicht fuer eine vollstaendige Flotte, kommt {}.
        """
        ships: dict[str, int] = {}
        for typen, anzahl in self.flotte_bedarf(insel_id):
            if anzahl <= 0:
                continue
            gewaehlt = waehle_schiffe(typen, anzahl, vorrat)
            if not gewaehlt:
                return {}
            ships.update(gewaehlt)
        return ships

    def flotte_units(self, insel_id) -> dict:
        s = self.s(insel_id)
        return {s["flotte_einheit_typ"]: int(s["flotte_einheiten"])} if s["flotte_einheiten"] > 0 else {}

    def overview(self, max_alter: float = 15.0) -> dict[int, dict]:
        """Uebersicht aller eigenen Inseln, kurz zwischengespeichert. Neue Inseln
        bekommen dabei einen (ausgeschalteten) Eintrag im State."""
        if not self._overview or time.time() - self._overview_zeit > max_alter:
            inseln = self.client.get_islands_overview() or []
            if not inseln:
                raise ApiError(0, "keine_insel", "GET /me/islands/overview lieferte keine Insel")
            self._overview = {int(i["id"]): i for i in inseln}
            self._overview_zeit = time.time()
            with self.state.lock:
                for iid, ov in self._overview.items():
                    eintrag = self.state.insel(iid)
                    if eintrag is None:
                        eintrag = neue_insel()
                        # Einstellung im Rohstoff-Ausgleich von der bisher
                        # neuesten Insel uebernehmen (wie beim Upgrade-Bot).
                        bisher = list(self.state.data["inseln"].values())
                        if bisher and isinstance(bisher[-1].get("ausgleich"), dict):
                            eintrag["ausgleich"] = copy.deepcopy(bisher[-1]["ausgleich"])
                        self.state.data["inseln"][str(iid)] = eintrag
                        if self.gestartet:
                            log.info("Neue Insel %s (%s) - im Seekampf-Hub einschalten, um von dort zu raiden",
                                     ov.get("name"), ov.get("koordinaten"))
                        self._dirty = True
                    eintrag["name"] = ov.get("name") or f"Insel {iid}"
                    eintrag["koordinaten"] = ov.get("koordinaten") or ""
        return self._overview

    def insel_ov(self, insel_id: int, max_alter: float = 15.0) -> dict:
        return self.overview(max_alter).get(int(insel_id)) or {}

    def heimat(self, insel_id: int) -> tuple[int, int, int] | None:
        koord = (self.insel_ov(insel_id).get("koordinaten")
                 or (self.state.insel(insel_id) or {}).get("koordinaten"))
        return geo.koord_parse(koord) if koord else None

    def aktive_inseln(self) -> list[int]:
        return [int(iid) for iid, insel in self.state.data["inseln"].items()
                if insel.get("aktiv") and int(iid) in self._overview]

    def start(self) -> None:
        """Einmalig beim Hochfahren: Inseln laden, ggf. Altbestand zuordnen, erster Scan."""
        try:
            me = self.client.get_me()
            self.heimat_id = me.get("current_island_id")
        except ApiError as e:
            log.warning("GET /me fehlgeschlagen (%s) - nehme die erste Insel aus der Uebersicht", e)
        erster_start = not self.state.data["inseln"] and "altbestand" not in self.state.data
        alles = self.overview(max_alter=0)
        if self.heimat_id not in alles:
            self.heimat_id = next(iter(alles))
        ov = alles[self.heimat_id]
        if self.state.auf_inseln_umstellen(self.heimat_id, ov.get("name") or "", ov.get("koordinaten") or ""):
            log.info("Auf mehrere Inseln umgestellt: bisherige Ziele und Statistik gehoeren %s (%s)",
                     ov.get("name"), ov.get("koordinaten"))
        elif erster_start:
            # Allererster Start: die Insel aus dem Spiel raidet, die anderen erst nach Freigabe.
            self.state.insel(self.heimat_id)["aktiv"] = True
        self.gestartet = True
        log.info("Inseln: %s", ", ".join(
            f"{i.get('name')} ({i.get('koordinaten')}, id={iid}, {'an' if i.get('aktiv') else 'aus'})"
            for iid, i in self.state.data["inseln"].items()))
        if self.state.data["stats"]["seit"] is None:
            self.state.data["stats"]["seit"] = time.time()
        self._dirty = True
        for iid in self.aktive_inseln():
            if not self.state.insel(iid)["ziele"]:
                self.scan(iid)

    def scan(self, insel_id: int) -> dict:
        heimat = self.heimat(insel_id)
        if heimat is None:
            raise ApiError(0, "insel_unbekannt", f"Insel {insel_id} gehoert nicht zum Konto")
        # Fuer die Fahrzeiten zaehlt, was gerade wirklich faehrt; ist nichts
        # daheim, tut es die Wunschbesetzung als Schaetzung.
        ships = (self.flotte_ships(insel_id, self.vorrat(self.insel_ov(insel_id)))
                 or self.flotte_ships(insel_id))
        return scanner.scan(self.client, self.state, insel_id, heimat, ships)

    # ------------------------------------------------------------------- Tick
    def tick(self) -> None:
        jetzt = time.time()
        self._laufzeit_fortschreiben(jetzt)

        flotten_api = self.client.get_fleets()
        self._flotten_abgleichen(flotten_api, jetzt)
        # Sicherheit vor allem anderen - auch wenn das Raiden gestoppt ist,
        # fahren ja noch Flotten.
        self._unterwegs_pruefen(flotten_api, jetzt)
        self._berichte_aufloesen()

        if not self.state.data["laeuft"]:
            self.ausgleich.reserviert = set()
            self._pause("gestoppt")
            self._speichern_wenn_noetig(jetzt)
            self.letzter_tick = jetzt
            return

        alles = self.overview()
        self._pause(None)
        self._report_wenn_faellig(jetzt)
        # Vor den Raids: eine faellige Lieferung bekommt die Handelsschiffe zuerst.
        self.ausgleich.tick(alles, flotten_api, jetzt)
        aktiv = set(self.aktive_inseln())
        for iid_text, insel in self.state.data["inseln"].items():
            iid = int(iid_text)
            if iid not in alles:
                self._insel_pause(insel, "gehoert nicht mehr zum Konto")
                continue
            if iid not in aktiv:
                self._insel_pause(insel, "ausgeschaltet")
                continue
            self._scan_wenn_faellig(iid, insel, jetzt)
            ov = alles[iid]
            if self._bedrohung_behandeln(iid, insel, ov, flotten_api, jetzt):
                continue
            if self._lager_voll(iid, ov):
                self._insel_pause(insel, "Lager voll - Beute waere verschenkt")
                continue
            self._losschicken(iid, insel, jetzt)

        self._speichern_wenn_noetig(jetzt)
        self.letzter_tick = jetzt

    def _laufzeit_fortschreiben(self, jetzt: float) -> None:
        vorher, self._letzter_tick_ts = self._letzter_tick_ts, jetzt
        if vorher is None or not self.state.data["laeuft"]:
            return
        # Eine Luecke groesser als ein paar Ticks war Stillstand (Dienst weg,
        # Rechner aus) - die zaehlt nicht als Laufzeit.
        delta = jetzt - vorher
        if 0 < delta <= max(30.0, self.settings["tick_sekunden"] * 4):
            self.state.data["stats"]["laufzeit_s"] += delta
            for insel in self.state.data["inseln"].values():
                if insel.get("aktiv"):
                    insel["stats"]["laufzeit_s"] += delta

    def _pause(self, grund: str | None) -> None:
        if self.state.data["pause_grund"] != grund:
            if grund:
                log.info("Pausiert: %s", grund)
            self.state.data["pause_grund"] = grund
            self._dirty = True

    def _insel_pause(self, insel: dict, grund: str | None) -> None:
        if insel.get("pause_grund") != grund:
            if grund and grund != "ausgeschaltet":
                log.info("%s pausiert: %s", insel.get("name"), grund)
            insel["pause_grund"] = grund
            self._dirty = True

    def _speichern_wenn_noetig(self, jetzt: float) -> None:
        if self._dirty or jetzt - self._letzte_speicherung > 60:
            self.state.save()
            self._dirty = False
            self._letzte_speicherung = jetzt

    # -------------------------------------------------------- Flotten-Abgleich
    def _record(self, f: dict) -> dict:
        ziel = f.get("target") or {}
        koord = geo.koord_str(ziel.get("x", 0), ziel.get("y", 0), ziel.get("z", 0))
        return {
            "id": f.get("id"),
            "insel_id": f.get("origin_island_id"),
            "koordinaten": koord,
            "ziel_name": ziel.get("name") or "",
            "mission": f.get("mission"),
            "ships": f.get("ships") or {},
            "units": f.get("units") or {},
            "prioritaet": None,
            "ladevolumen": geo.ladevolumen(f.get("ships") or {}),
            "depart_at": iso_zu_epoch(f.get("depart_at")),
            "arrive_at": iso_zu_epoch(f.get("arrive_at")),
            "return_at": iso_zu_epoch(f.get("return_at")),
            "recallable_until": iso_zu_epoch(f.get("recallable_until")),
            "state": f.get("state"),
            "loot": f.get("loot") or {},
            "resources": f.get("resources") or {},
            "angekommen": False,
            "verbucht": False,
            "zurueckgerufen": False,
            "fremd": False,
        }

    def _flotten_abgleichen(self, flotten_api: list, jetzt: float) -> None:
        aktiv = self.state.data["flotten"]
        gesehen = set()

        for f in flotten_api:
            if (f.get("ships") or {}).get("kolonisationsschiff"):
                continue  # Sache des Kolonisations-Bots, kein Raid
            fid = str(f.get("id"))
            gesehen.add(fid)
            rec = aktiv.get(fid)
            if rec is None:
                rec = self._record(f)
                # Nicht von uns losgeschickt: entweder von Hand gestartet oder
                # der Manager wurde neu gestartet, waehrend sie unterwegs war.
                rec["fremd"] = True
                aktiv[fid] = rec
                log.info("Fremde Flotte uebernommen: #%s -> %s (%s)",
                         fid, rec["koordinaten"], rec["state"])
                self._dirty = True
            if f.get("origin_island_id") is not None:
                rec["insel_id"] = f["origin_island_id"]
            for feld, wert in (("state", f.get("state")),
                               ("loot", f.get("loot") or rec.get("loot") or {}),
                               ("arrive_at", iso_zu_epoch(f.get("arrive_at"))),
                               ("return_at", iso_zu_epoch(f.get("return_at"))),
                               ("recallable_until", iso_zu_epoch(f.get("recallable_until")))):
                if wert not in (None, {}) and rec.get(feld) != wert:
                    rec[feld] = wert
            if f.get("target", {}).get("name"):
                rec["ziel_name"] = f["target"]["name"]

            if not rec["angekommen"] and rec["arrive_at"] and jetzt >= rec["arrive_at"]:
                self._ankunft(rec, jetzt)

        for fid in list(aktiv):
            if fid in gesehen:
                continue
            rec = aktiv.pop(fid)
            self._dirty = True
            if rec["zurueckgerufen"] and not rec["angekommen"]:
                log.info("Flotte #%s wurde zurueckgerufen und ist wieder daheim", fid)
                continue
            if not rec["angekommen"] and rec["arrive_at"] and jetzt >= rec["arrive_at"]:
                # Ankunft waehrend eines Ausfalls verpasst - jetzt nachtragen.
                self._ankunft(rec, jetzt)
            if rec.get("mission") != "attack":
                log.info("RUECKKEHR  Flotte #%s (%s) von %s (%s)",
                         fid, rec.get("mission"), rec["koordinaten"], rec["ziel_name"])
                continue
            log.info("RUECKKEHR  Flotte #%s von %s (%s) - Beute laut Flotte: %s",
                     fid, rec["koordinaten"], rec["ziel_name"], fmt_beute(rec.get("loot")))

    def _ankunft(self, rec: dict, jetzt: float) -> None:
        rec["angekommen"] = True
        koord = rec["koordinaten"]
        log.info("ANKUNFT    Flotte #%s bei %s (%s)%s", rec["id"], koord, rec["ziel_name"],
                 f" - Rueckkehr in {fmt_dauer((rec['return_at'] or jetzt) - jetzt)}"
                 if rec.get("return_at") else "")

        if rec.get("mission") != "attack":
            # Handel/Transport (Rohstoff-Ausgleich, Allianz-Bot, von Hand):
            # kein Raid, kein Kampfbericht - nichts zu verbuchen.
            rec["verbucht"] = True
            self._dirty = True
            return

        if not rec["verbucht"]:
            rec["verbucht"] = True
            iid = rec.get("insel_id")
            insel = self.state.insel(iid)
            for stats in (self.state.data["stats"], insel["stats"] if insel else None):
                if stats is None:
                    continue
                stats["raids"] += 1
                if koord not in stats["inseln_besucht"]:
                    stats["inseln_besucht"].append(koord)
            ziel = self.state.ziel(iid, koord)
            if ziel is not None:
                ziel["raids"] += 1
                ziel["letzter_raid"] = jetzt
            if iid is not None:
                self.state.verlauf_buchen(iid, jetzt, raids=1)
            self.state.data.setdefault("offene_berichte", []).append({
                "koordinaten": koord, "flotte": rec["id"], "insel_id": iid,
                "arrive_at": rec["arrive_at"] or jetzt, "seit": jetzt,
                # Nur Berichte zu selbst losgeschickten Flotten duerfen spaeter
                # ausgeblendet werden - siehe _bericht_archivieren.
                "fremd": bool(rec.get("fremd")),
            })
        self._dirty = True

    # ------------------------------------------------- Ziel unterwegs pruefen
    def _unterwegs_pruefen(self, flotten_api: list, jetzt: float) -> None:
        """Ist das Ziel einer fahrenden Raid-Flotte noch frei?

        Zwischen Abfahrt und Ankunft kann jemand die Insel besiedeln - dann
        wuerde aus dem Pluenderzug ein Angriff auf einen Mitspieler. Solange
        die Flotte noch zurueckgerufen werden kann (recallable_until, haengt am
        Wachturm), wird das Ziel alle `pruef_intervall_s` geprueft und
        `pruef_vorlauf_s` vor Ende der Rueckrufmoeglichkeit ein letztes Mal.
        Hat es einen Besitzer: Flotte zurueckrufen, Ziel aus allen Listen.
        Wird die Insel erst danach besiedelt, laesst sich nichts mehr tun.
        """
        intervall = float(self.settings["pruef_intervall_s"])
        vorlauf = float(self.settings["pruef_vorlauf_s"])
        frisch: dict[str, bool] = {}  # je Koordinate hoechstens eine Abfrage pro Tick
        for f in flotten_api:
            if f.get("mission") != "attack" or f.get("state") != "outbound":
                continue
            rec = self.state.data["flotten"].get(str(f.get("id")))
            if rec is None or rec.get("fremd") or rec.get("zurueckgerufen") or rec.get("angekommen"):
                continue
            ende = rec.get("recallable_until")
            if not ende or jetzt >= ende:
                continue
            rest = ende - jetzt
            letzte = rest <= vorlauf
            if letzte and rec.get("letzte_pruefung"):
                continue
            if not letzte and jetzt - float(rec.get("geprueft_um") or rec.get("depart_at") or 0) < intervall:
                continue
            koord = rec["koordinaten"]
            if koord not in frisch:
                x, y, z = geo.koord_parse(koord)
                try:
                    frisch[koord] = scanner.ist_frei(self.client.get_island_info(x, y, z) or {})
                except (ApiError, requests.RequestException) as e:
                    log.warning("Pruefung von %s (Flotte #%s) fehlgeschlagen: %s", koord, rec["id"], e)
                    continue  # naechster Tick versucht es wieder
            rec["geprueft_um"] = jetzt
            if letzte:
                rec["letzte_pruefung"] = True
            self._dirty = True
            if frisch[koord]:
                continue
            try:
                self.client.recall_fleet(f["id"])
            except ApiError as e:
                log.error("RUECKRUF   Flotte #%s nach %s fehlgeschlagen: %s - Ziel ist besiedelt!",
                          rec["id"], koord, e)
            else:
                rec["zurueckgerufen"] = True
                log.warning("RUECKRUF   Flotte #%s zurueckgerufen: %s (%s) wurde unterwegs besiedelt "
                            "(%s vor Ende der Rueckrufmoeglichkeit)", rec["id"], koord, rec.get("ziel_name"),
                            fmt_dauer(rest))
            ziel = self._irgendein_ziel(koord)
            if ziel is not None:
                self._ziel_verwerfen(ziel, "wurde besiedelt, waehrend eine Flotte unterwegs war")

    def _irgendein_ziel(self, koord: str) -> dict | None:
        for insel in self.state.data["inseln"].values():
            if koord in insel["ziele"]:
                return insel["ziele"][koord]
        return None

    # -------------------------------------------------------------- Berichte
    def _berichte_aufloesen(self) -> None:
        """Zu jeder angekommenen Flotte den Kampfbericht suchen.

        Nur der Bericht sagt, ob der Angriff gewonnen wurde und wie viel Beute
        wirklich mitkam - die Flotte selbst fuehrt zwar ein loot-Feld, aber
        kein Sieg/Niederlage-Kennzeichen, und genau daran haengt die Blacklist.
        """
        offen = self.state.data.get("offene_berichte") or []
        if not offen:
            return
        try:
            nachrichten = self.client.get_combat_messages(limit=50)
        except ApiError as e:
            log.warning("Kampfberichte nicht abrufbar: %s", e)
            return

        verbucht = self.state.data["verbuchte_berichte"]
        jetzt = time.time()
        uebrig = []
        for eintrag in offen:
            treffer = self._passender_bericht(nachrichten, eintrag, verbucht)
            if treffer is not None:
                self._besiedelt_pruefen(eintrag, treffer)
                self._bericht_verbuchen(eintrag, treffer)
                verbucht.append(treffer["id"])
                self._bericht_archivieren(eintrag, treffer)
                self._dirty = True
                continue
            if jetzt - eintrag["seit"] > 900:
                log.warning("Kein Kampfbericht zu %s (Flotte #%s) gefunden - Beute nicht verbucht",
                            eintrag["koordinaten"], eintrag["flotte"])
                self._dirty = True
                continue
            uebrig.append(eintrag)

        self.state.data["offene_berichte"] = uebrig
        del verbucht[:-500]  # nur die juengsten IDs behalten

    def _besiedelt_pruefen(self, eintrag: dict, nachricht: dict) -> None:
        """Traf die Flotte auf eine inzwischen besiedelte Insel? Der Bericht
        traegt den Inselnamen zum Zeitpunkt des Kampfes; weicht er vom Namen
        der freien Insel ab, hat sie jemand uebernommen. Dann: Bericht stehen
        lassen, Ziel aus allen Listen, Telegram."""
        p = nachricht.get("payload") or {}
        koord = eintrag["koordinaten"]
        ziel = self.state.ziel(eintrag.get("insel_id"), koord)
        name = p.get("insel_name")
        if eintrag.get("fremd") or not name or ziel is None or name == ziel.get("name"):
            return
        # Nur ein anderer Name? Live nachsehen, bevor Alarm geschlagen wird.
        try:
            frei = scanner.ist_frei(self.client.get_island_info(ziel["x"], ziel["y"], ziel["z"]) or {})
        except (ApiError, requests.RequestException):
            frei = False  # im Zweifel: Bericht stehen lassen und melden
        if frei:
            ziel["name"] = name
            return
        eintrag["besiedelt"] = True
        log.warning("BESIEDELT  Flotte #%s hat %s angegriffen, die unterwegs besiedelt wurde: '%s' -> '%s' "
                    "(Bericht #%s bleibt im Postfach)", eintrag["flotte"], koord, ziel.get("name"), name,
                    nachricht.get("id"))
        if self.notifier is not None and getattr(self.notifier, "aktiv", False):
            self.notifier.send("Flotten-Manager: Angriff auf besiedelte Insel",
                               f"Flotte #{eintrag['flotte']} hat {koord} angegriffen. Die Insel wurde unterwegs "
                               f"besiedelt ('{ziel.get('name')}' -> '{name}'), erst nachdem sie nicht mehr "
                               f"zurueckgerufen werden konnte. Der Kampfbericht bleibt im Postfach.")
        self._ziel_verwerfen(ziel, f"wurde besiedelt ('{name}')")

    @staticmethod
    def _passender_bericht(nachrichten: list, eintrag: dict, verbucht: list) -> dict | None:
        for m in nachrichten:
            p = m.get("payload") or {}
            if p.get("typ") == "spionage" or "sieg" not in p:
                continue
            if p.get("insel_koordinaten") != eintrag["koordinaten"]:
                continue
            if m.get("id") in verbucht:
                continue
            erstellt = iso_zu_epoch(m.get("created_at"))
            # Der Bericht entsteht im Moment der Ankunft; zwei Minuten Spiel
            # reichen fuer Uhrenversatz, ohne einen aelteren Angriff auf
            # dieselbe Insel einzufangen.
            if erstellt is None or erstellt < eintrag["arrive_at"] - 120:
                continue
            return m
        return None

    def _bericht_verbuchen(self, eintrag: dict, nachricht: dict) -> None:
        p = nachricht.get("payload") or {}
        koord = eintrag["koordinaten"]
        iid = eintrag.get("insel_id")
        insel = self.state.insel(iid)
        ziel = self.state.ziel(iid, koord)
        alle_stats = [self.state.data["stats"]] + ([insel["stats"]] if insel else [])
        sieg = bool(p.get("sieg"))
        beute = {r: float((p.get("loot") or {}).get(r, 0) or 0) for r in config.RESOURCE_KEYS}

        if sieg:
            for stats in alle_stats:
                for r in config.RESOURCE_KEYS:
                    stats[r] += beute[r]
            if iid is not None:
                self.state.verlauf_buchen(iid, iso_zu_epoch(nachricht.get("created_at")) or time.time(),
                                          beute=beute)
            if ziel is not None:
                ziel["niederlagen"] = 0
                ziel["letzte_beute"] = beute
                for r in config.RESOURCE_KEYS:
                    ziel["beute_gesamt"][r] = ziel["beute_gesamt"].get(r, 0.0) + beute[r]
            log.info("BEUTE      %s (%s): %s", koord, ziel["name"] if ziel else "?", fmt_beute(beute))
            return

        for stats in alle_stats:
            stats["niederlagen"] += 1
        verluste = (p.get("verluste") or {}).get("angreifer") or {}
        log.warning("NIEDERLAGE %s - Verluste: %s %s", koord,
                    verluste.get("units") or "keine Einheiten",
                    verluste.get("ships") or "keine Schiffe")
        if ziel is None:
            return
        ziel["niederlagen"] += 1
        ziel["niederlagen_gesamt"] += 1
        ziel["letzte_beute"] = beute
        s = self.s(iid)
        grenze = int(s["niederlagen_bis_blacklist"])
        if ziel["niederlagen"] >= grenze:
            tage = float(s["blacklist_tage"])
            ziel["blacklist_bis"] = time.time() + tage * 86400
            ziel["blacklist_grund"] = f"{ziel['niederlagen']} Niederlagen in Folge (Garnison?)"
            ziel["niederlagen"] = 0
            log.warning("BLACKLIST  %s fuer %.0f Tage gesperrt: %s",
                        koord, tage, ziel["blacklist_grund"])

    def _bericht_archivieren(self, eintrag: dict, nachricht: dict) -> None:
        """Den eben verbuchten Bericht im Postfach ausblenden.

        Erst verbuchen, dann ausblenden: waere die Reihenfolge umgekehrt und
        das Verbuchen schluege fehl, waere der Bericht aus der Liste
        verschwunden, bevor die Beute gezaehlt ist - zurueckholen kann ihn die
        API nicht.

        Ausgeblendet wird nur, was der Manager selbst ausgeloest hat. Die
        Pruefungen sind bewusst eng; im Zweifel bleibt der Bericht liegen:
          - "fremd" fehlt (Eintrag aus einer aelteren Version) -> behalten
          - "fremd" ist True: von Hand gestartet - oder state.json ist
            verlorengegangen, waehrend die Flotte fuhr -> behalten
          - rolle != "angreifer" (eingehender Angriff auf eine eigene Insel)
            -> behalten
          - Niederlage -> behalten
          - Insel war beim Kampf schon besiedelt (anderer Name im Bericht)
            -> behalten
        """
        if not self.settings["berichte_archivieren"]:
            return
        if eintrag.get("fremd", True):
            return
        p = nachricht.get("payload") or {}
        if p.get("rolle") != "angreifer":
            return
        if not p.get("sieg"):
            return  # Niederlagen bleiben zum Nachlesen im Postfach
        if eintrag.get("besiedelt"):
            return  # Angriff auf eine inzwischen bewohnte Insel - bleibt sichtbar
        try:
            self.client.archive_message(nachricht["id"])
        except ApiError as e:
            # Nicht schlimm: die Beute ist verbucht, nur das Postfach bleibt
            # voller. Beim naechsten Bericht wird es wieder versucht.
            log.warning("Bericht #%s zu %s nicht archivierbar: %s",
                        nachricht["id"], eintrag["koordinaten"], e)
            return
        log.info("ARCHIV     Bericht #%s zu %s ausgeblendet",
                 nachricht["id"], eintrag["koordinaten"])

    # ------------------------------------------------------------ Sicherheit
    def _bedrohung_behandeln(self, insel_id: int, insel: dict, ov: dict, flotten_api: list,
                             jetzt: float) -> bool:
        """Angriff auf DIESE Insel im Anflug: ihre Flotten zurueckrufen (Schiffe
        kehren immer zu ihrer Ausgangsinsel zurueck) und keine neuen losschicken."""
        if not ov.get("bedrohung_im_anflug"):
            return False
        if not self.s(insel_id)["rueckruf_bei_bedrohung"]:
            self._insel_pause(insel, "Bedrohung im Anflug (Rueckruf abgeschaltet)")
            return True
        for f in flotten_api:
            # Nur Raids: Handelsfahrten (Ausgleich, Beistand und Leihe-Rueckgabe
            # des Allianz-Bots) haben ein Versprechen dahinter und fahren weiter.
            if f.get("origin_island_id") != insel_id or f.get("mission") != "attack":
                continue
            rec = self.state.data["flotten"].get(str(f.get("id")))
            if rec is None or rec["zurueckgerufen"] or rec["angekommen"]:
                continue
            grenze = rec.get("recallable_until") or 0
            if jetzt >= grenze:
                continue  # ausser Sichtweite, muss ihr Ziel erreichen
            try:
                self.client.recall_fleet(f["id"])
                rec["zurueckgerufen"] = True
                self._dirty = True
                log.warning("RUECKRUF   Flotte #%s von %s zurueckgerufen (Bedrohung auf %s)",
                            f["id"], rec["koordinaten"], insel.get("name"))
            except ApiError as e:
                log.error("Rueckruf von Flotte #%s fehlgeschlagen: %s", f["id"], e)
        self._insel_pause(insel, "Bedrohung im Anflug - Flotten bleiben daheim")
        return True

    def _lager_voll(self, insel_id, ov: dict) -> bool:
        res = ov.get("rohstoffe") or {}
        kapazitaet = float(res.get("kapazitaet") or 0)
        if kapazitaet <= 0:
            return False
        grenze = kapazitaet * float(self.s(insel_id)["lager_voll_schwelle"])
        return all(float(res.get(r, 0) or 0) >= grenze for r in config.RESOURCE_KEYS)

    # --------------------------------------------------------------- Scan/Report
    def _scan_wenn_faellig(self, insel_id: int, insel: dict, jetzt: float) -> None:
        intervall = float(self.s(insel_id)["scan_intervall_stunden"]) * 3600
        letzter = insel.get("letzter_scan") or 0
        if (intervall > 0 and jetzt - letzter >= intervall) or (not insel["ziele"] and not letzter):
            try:
                self.scan(insel_id)
            except ApiError as e:
                log.error("Scan fuer %s fehlgeschlagen: %s", insel.get("name"), e)

    def _report_wenn_faellig(self, jetzt: float) -> None:
        if self.notifier is None or not self.settings["telegram_report"]:
            return
        import report  # spaet importiert: ohne Telegram-Token nie gebraucht
        report.wenn_faellig(self.state, self.notifier, jetzt)

    # ------------------------------------------------------------ Losschicken
    def _flotten_von(self, insel_id: int) -> list[dict]:
        return [rec for rec in self.state.data["flotten"].values() if rec.get("insel_id") == insel_id]

    def _freie_flotten(self, insel_id: int, ov: dict) -> int:
        """Wie viele vollstaendige Flotten stehen auf dieser Insel bereit?

        truppen/schiffe in der Uebersicht zaehlen nur, was NICHT unterwegs ist -
        die Rechnung beruecksichtigt fahrende Flotten damit automatisch.
        """
        vorrat = self.vorrat_fuer_raids(insel_id, ov)
        # Je Klasse zaehlt die Summe ueber alle Typen: ein kleines und ein
        # grosses Handelsschiff sind zwei Handelsschiffe.
        reicht_fuer = [sum(int(vorrat.get(t, 0) or 0) for t in typen) // anzahl
                       for typen, anzahl in self.flotte_bedarf(insel_id) if anzahl > 0]
        reicht_fuer += [int(vorrat.get(typ, 0) or 0) // anzahl
                        for typ, anzahl in self.flotte_units(insel_id).items()]
        if not reicht_fuer:
            return 0
        moeglich = min(reicht_fuer)
        grenze = int(self.s(insel_id)["max_flotten"])
        if grenze > 0:
            moeglich = min(moeglich, grenze - len(self._flotten_von(insel_id)))
        return max(0, moeglich)

    def naechstes_ziel_vorschau(self, insel: dict) -> str | None:
        rotation = insel["rotation"]
        return rotation[insel["rotation_index"] % len(rotation)] if rotation else None

    def _naechstes_ziel(self, insel: dict, belegt: set) -> dict | None:
        """Stur reihum: der Zeiger wandert bei jedem Griff eins weiter."""
        rotation = insel["rotation"]
        if not rotation:
            return None
        jetzt = time.time()
        for _ in range(len(rotation)):
            index = insel["rotation_index"] % len(rotation)
            koord = rotation[index]
            insel["rotation_index"] = (index + 1) % len(rotation)
            ziel = insel["ziele"].get(koord)
            if ziel is None or koord in belegt or self.state.ist_gesperrt(ziel, jetzt):
                continue
            return ziel
        return None

    def _prioritaet(self, insel_id: int, ov: dict, ships: dict | None = None) -> str | None:
        """Welcher Rohstoff soll gepluendert werden - oder gleichmaessig (None)?

        Grundregel: der Rohstoff, von dem auf dieser Insel am wenigsten da ist.
        Dabei zaehlt nicht der aktuelle Lagerstand, sondern der ERWARTETE: was
        die schon fahrenden Flotten dieser Insel mitbringen, ist eingerechnet.
        Vier Flotten mit Stein unterwegs heben Stein rechnerisch so weit an,
        dass die fuenfte von selbst etwas anderes holt.
        """
        s = self.s(insel_id)
        modus = str(s["rohstoff_modus"])
        if modus in config.RESOURCE_KEYS:
            return modus
        if modus == "gleichmaessig":
            return None

        res = ov.get("rohstoffe") or {}
        kapazitaet = float(res.get("kapazitaet") or 0)
        ladung = geo.ladevolumen(ships if ships is not None else self.flotte_ships(insel_id))
        erwartet = {r: float(res.get(r, 0) or 0) for r in config.RESOURCE_KEYS}
        for rec in self._flotten_von(insel_id):
            if rec.get("angekommen") and rec.get("loot"):
                continue  # Beute steht schon fest und ist gleich im Lager
            fracht = float(rec.get("ladevolumen") or ladung)
            prio = rec.get("prioritaet")
            if prio in erwartet:
                erwartet[prio] += fracht
            else:
                for r in config.RESOURCE_KEYS:
                    erwartet[r] += fracht / len(config.RESOURCE_KEYS)

        if kapazitaet > 0:
            grenze = kapazitaet * float(s["lager_voll_schwelle"])
            kandidaten = {r: v for r, v in erwartet.items() if v < grenze}
            if not kandidaten:
                return None
            # Liegt alles dicht beieinander, bringt Priorisieren nichts - dann
            # lieber gleichmaessig und dafuer die volle Ladung.
            if max(erwartet.values()) - min(erwartet.values()) < float(
                    s["ausgleich_schwelle"]) * kapazitaet:
                return None
        else:
            kandidaten = erwartet
        return min(kandidaten, key=kandidaten.get)

    def _ziel_ist_frei(self, ziel: dict) -> bool:
        """Sicherheitsabfrage unmittelbar vor dem Losschicken.

        Zwischen Scan und Abfahrt kann jemand die Insel kolonisiert haben -
        dann waere aus dem Pluenderzug ein Angriff auf einen Mitspieler
        geworden.
        """
        info = self.client.get_island_info(ziel["x"], ziel["y"], ziel["z"])
        return scanner.ist_frei(info or {})

    def _ziel_verwerfen(self, ziel: dict, grund: str) -> None:
        """Aus den Ziellisten ALLER Inseln nehmen - hat die Insel einen
        Besitzer, ist sie fuer jede eigene Insel tabu."""
        koord = ziel["koordinaten"]
        for insel in self.state.data["inseln"].values():
            insel["ziele"].pop(koord, None)
            rotation = insel["rotation"]
            if koord in rotation:
                index = rotation.index(koord)
                rotation.remove(koord)
                if index < insel["rotation_index"]:
                    insel["rotation_index"] -= 1
            if rotation:
                insel["rotation_index"] %= len(rotation)
            else:
                insel["rotation_index"] = 0
        log.info("Ziel %s aus der Rotation genommen: %s", koord, grund)
        self._dirty = True

    def _ziel_sperren(self, koord: str, sekunden: float, grund: str) -> None:
        """Ein Ziel fuer ALLE eigenen Inseln voruebergehend sperren."""
        bis = time.time() + sekunden
        for insel in self.state.data["inseln"].values():
            ziel = insel["ziele"].get(koord)
            if ziel is not None and ziel.get("blacklist_bis") != "dauerhaft":
                ziel["blacklist_bis"] = bis
                ziel["blacklist_grund"] = grund
        log.info("SPERRE     %s fuer %s gesperrt: %s", koord, fmt_dauer(sekunden), grund)
        self._dirty = True

    def _losschicken(self, insel_id: int, insel: dict, jetzt: float) -> None:
        # Erst mit dem zwischengespeicherten Stand grob pruefen; sieht es nach
        # einer freien Flotte aus, den Bestand frisch holen, bevor wirklich
        # losgeschickt wird - sonst faehrt eine Flotte los, deren Schiffe eine
        # Sekunde vorher schon vergeben wurden.
        if self._freie_flotten(insel_id, self.insel_ov(insel_id)) <= 0:
            self._insel_pause(insel, None)
            return
        ov = self.insel_ov(insel_id, max_alter=0)
        offen = self._freie_flotten(insel_id, ov)
        if offen <= 0:
            self._insel_pause(insel, None)
            return
        # Belegt sind Ziele, die IRGENDEINE eigene Flotte gerade anfaehrt -
        # zwei eigene Inseln greifen nie gleichzeitig dasselbe Ziel an.
        belegt = {rec["koordinaten"] for rec in self.state.data["flotten"].values()}
        units = self.flotte_units(insel_id)
        # Was noch im Hafen liegt. Jede losgeschickte Flotte wird abgezogen,
        # damit die naechste nicht dieselben Schiffe einplant.
        rest = self.vorrat_fuer_raids(insel_id, ov)
        versuche = len(insel["rotation"]) + offen

        for _ in itertools.repeat(None, versuche):
            if offen <= 0:
                break
            ships = self.flotte_ships(insel_id, rest)
            if not ships:
                break  # Bestand reicht nicht mehr fuer eine volle Flotte
            ziel = self._naechstes_ziel(insel, belegt)
            if ziel is None:
                self._insel_pause(insel, "kein freies Ziel in der Rotation")
                return
            try:
                if not self._ziel_ist_frei(ziel):
                    self._ziel_verwerfen(ziel, "hat inzwischen einen Besitzer")
                    continue
            except ApiError as e:
                log.error("Freiheits-Check fuer %s fehlgeschlagen: %s", ziel["koordinaten"], e)
                continue

            prio = self._prioritaet(insel_id, ov, ships)
            payload = {
                "origin_island_id": insel_id,
                "mission_type": "attack",
                "target": {"x": ziel["x"], "y": ziel["y"], "z": ziel["z"]},
                "ships": ships,
                "units": units,
                "razzia_ziel": self.s(insel_id)["razzia_ziel"] or None,
            }
            if prio:
                payload["razzia_prioritaet"] = prio
            try:
                antwort = self.client.create_fleet(payload)
            except ApiError as e:
                if e.code == "newbie_protection":
                    self._ziel_sperren(ziel["koordinaten"], ANFAENGERSCHUTZ_SPERRE_S, "Anfaengerschutz")
                    continue
                log.error("Flotte von %s nach %s konnte nicht starten: %s",
                          insel.get("name"), ziel["koordinaten"], e)
                return  # fehlt etwas (Schiffe, Einheiten), hilft der naechste Versuch auch nicht

            rec = self._record(antwort)
            rec["insel_id"] = insel_id
            rec["prioritaet"] = prio
            rec["fremd"] = False
            self.state.data["flotten"][str(rec["id"])] = rec
            belegt.add(rec["koordinaten"])
            for typ, n in {**ships, **units}.items():
                rest[typ] = int(rest.get(typ, 0) or 0) - n
            offen -= 1
            self._dirty = True
            log.info("ABFAHRT    Flotte #%s %s -> %s (%s), %s, Beute: %s, Ankunft in %s, zurueck in %s",
                     rec["id"], insel.get("name"), ziel["koordinaten"], ziel["name"],
                     ", ".join(f"{n}x {t}" for t, n in {**ships, **units}.items()),
                     prio or "gleichmaessig",
                     fmt_dauer((rec["arrive_at"] or jetzt) - jetzt),
                     fmt_dauer((rec["return_at"] or 0) - jetzt) if rec["return_at"]
                     else fmt_dauer(2 * ((rec["arrive_at"] or jetzt) - jetzt)))
        self._insel_pause(insel, None)
