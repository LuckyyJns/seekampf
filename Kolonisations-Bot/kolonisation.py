"""Der Takt des Kolonisations-Bots.

Die Ziele kommen ausschliesslich aus der Warteschlange, die im Seekampf-Hub
gepflegt wird - der Bot waehlt selbst keine Inseln aus. Je Eintrag:

  wartet     noch keine Insel, die das Schiff bauen kann (oder alle bauen gerade)
  spart      die naechstgelegene geeignete Insel spart auf das Schiff; die
             Kosten stehen in data/reserve.json, damit Upgrade-Bot,
             Rohstoff-Ausgleich und Ausbildungs-Bot sie nicht ausgeben
  baut       das Schiff ist in Ausbildung
  unterwegs  das Schiff faehrt; wird das Ziel vorher von jemand anderem
             besiedelt, wird es zurueckgerufen

Geeignet ist eine Insel, deren Hafen das Schiff bauen kann (Stufe 20) und
deren Lager die Schiffskosten fasst. Jede Insel baut hoechstens ein Schiff
zur Zeit, mehrere Eintraege laufen so parallel auf verschiedenen Inseln.
Liegt irgendwo schon ein freies Kolonisationsschiff im Hafen (z. B. nach
einem Rueckruf), nimmt der naechste Eintrag das naechstgelegene davon, statt
ein neues zu bauen.

Ein Ziel, das einen Besitzer bekommt, fliegt aus der Warteschlange - ausser
es wurde bewusst als bewohnte Insel eingetragen (Truppen und Hauptgebaeude
zerstoert, das Schiff soll sie einnehmen); dann bleibt es stehen.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone

import requests

import config
import geo
from api_client import ApiError

log = logging.getLogger("seekampf_kolonisation")

AUSBILDUNG_LAEUFT = ("aktiv", "wartend", "queued", "laufend")


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


def naechster_name(muster: str, namen: list[str]) -> str:
    """Name nach Muster mit {n} = hoechste schon vergebene Nummer + 1.
    "GiG {n}" bei GiG, GiG 2 ... GiG 7 -> "GiG 8"."""
    if "{n}" not in muster:
        return muster
    vor, nach = muster.split("{n}", 1)
    regel = re.compile("^" + re.escape(vor) + r"(\d+)" + re.escape(nach) + "$")
    nummern = [int(m.group(1)) for n in namen if (m := regel.match(n or ""))]
    # Die erste Insel heisst oft nur "GiG" (ohne Nummer) - sie zaehlt als 1.
    if any((n or "").strip() == (vor + nach).strip() for n in namen):
        nummern.append(1)
    return muster.replace("{n}", str(max(nummern, default=0) + 1))


def fmt_res(werte: dict) -> str:
    return ", ".join(f"{float(werte.get(r, 0)):.0f} {r.capitalize()}" for r in config.RESOURCE_KEYS
                     if float(werte.get(r, 0) or 0) > 0) or "nichts"


def mengen_text(m: dict) -> str:
    return ", ".join(f"{n}x {typ}" for typ, n in m.items())


def fmt_dauer(sek: float) -> str:
    sek = max(0, int(sek))
    h, rest = divmod(sek, 3600)
    return f"{h}h {rest // 60:02d}min" if h else f"{rest // 60}min"


def ziel_status(info: dict) -> tuple[bool, str | None]:
    """(ist eine Insel, Besitzer) aus GET /map/island. Leeres Wasser hat kein
    Feld "besitzer"; eine freie Insel oder Ruine hat besitzer = null."""
    if "besitzer" not in (info or {}):
        return False, None
    return True, info.get("besitzer")


def begleitung_bereinigen(roh) -> dict:
    """{"schiffe": {typ: n}, "truppen": {typ: n}} mit ganzen Zahlen > 0; alles andere faellt weg."""
    aus: dict[str, dict[str, int]] = {"schiffe": {}, "truppen": {}}
    for art in aus:
        for typ, n in ((roh or {}).get(art) or {}).items():
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if n > 0 and isinstance(typ, str) and typ.strip() and typ != config.SCHIFF:
                aus[art][typ.strip()] = n
    return aus


class Kolonisierer:
    def __init__(self, client, state, notifier=None):
        self.client = client
        self.state = state
        self.notifier = notifier
        self.ov: dict[int, dict] = {}
        self.ab_hafen = config.SCHIFF_AB_HAFEN
        self.kosten = dict(config.SCHIFF_KOSTEN)
        self.inseln: dict[int, dict] = {}   # Eignung je Insel, fuer den Seekampf-Hub
        self.reserve: dict[str, dict] = {}
        self.einheiten_reserve: dict[str, dict] = {}   # Insel -> {Typ: Anzahl} der Begleitung
        self.spieler: str | None = None
        self.letzter_tick: float | None = None
        self.letzter_fehler: str | None = None
        self._training: dict[int, dict] = {}

    # ------------------------------------------------------------ Hilfen
    def _melden(self, titel: str, text: str) -> None:
        if self.notifier is not None and getattr(self.notifier, "aktiv", False):
            self.notifier.send(titel, text)

    def _name(self, iid) -> str:
        ov = self.ov.get(int(iid)) if iid is not None else None
        return (ov or {}).get("name") or f"Insel {iid}"

    def _koord(self, iid) -> tuple[int, int, int]:
        return geo.koord_parse(self.ov[int(iid)]["koordinaten"])

    def entfernung(self, iid, e: dict) -> float:
        return geo.distanz_felder(self._koord(iid), (e["x"], e["y"], e["z"]))

    def fahrzeit(self, iid, e: dict) -> float:
        return geo.fahrzeit_s(self._koord(iid), (e["x"], e["y"], e["z"]),
                              config.SCHIFF_KNOTEN[config.SCHIFF])

    def _ausbildung(self, iid: int) -> dict:
        """Ausbildungsstand einer Insel (einmal je Tick abgefragt). Liest dabei
        Hafenstufe und Kosten des Schiffs aus dem Katalog."""
        if iid not in self._training:
            daten = self.client.get_training(iid) or {}
            self._training[iid] = daten
            for k in (daten.get("katalog") or {}).get("hafen") or []:
                if k.get("typ") == config.SCHIFF:
                    if k.get("ab_stufe"):
                        self.ab_hafen = int(k["ab_stufe"])
                    if k.get("kosten"):
                        self.kosten = {r: float(k["kosten"].get(r, 0) or 0) for r in config.RESOURCE_KEYS}
        return self._training[iid]

    def _in_ausbildung(self, iid: int) -> int:
        """Wie viele Kolonisationsschiffe auf dieser Insel gerade ausgebildet werden."""
        anzahl = 0
        for a in self._ausbildung(iid).get("auftraege") or []:
            if a.get("item_typ") == config.SCHIFF and str(a.get("status", "")).lower() in AUSBILDUNG_LAEUFT:
                anzahl += int(a.get("anzahl") or 0) - int(a.get("abgeschlossen") or 0)
        return max(0, anzahl)

    def eignung(self, iid: int) -> str | None:
        """None = diese Insel kann ein Kolonisationsschiff bauen, sonst der Grund."""
        ov = self.ov[iid]
        hafen = int((ov.get("gebaeude") or {}).get("hafen") or 0)
        if hafen < self.ab_hafen:
            return f"Hafen Stufe {hafen} (braucht {self.ab_hafen})"
        kap = float((ov.get("rohstoffe") or {}).get("kapazitaet") or 0)
        groesster = max(self.kosten.values())
        if kap < groesster:
            return f"Lager {kap:.0f} zu klein (braucht {groesster:.0f})"
        return None

    def ziel_pruefen(self, e: dict) -> str | None:
        """None = Ziel ist weiterhin frei, sonst der neue Besitzer."""
        ist_insel, besitzer = ziel_status(self.client.get_island_info(e["x"], e["y"], e["z"]))
        e["letzte_pruefung"] = time.time()
        if not ist_insel:
            return "(keine Insel)"
        return besitzer

    # --------------------------------------------------------------- Tick
    def tick(self) -> None:
        jetzt = time.time()
        self._training = {}
        if self.spieler is None:
            self.spieler = (self.client.get_me() or {}).get("name")
        self.ov = {int(i["id"]): i for i in (self.client.get_islands_overview() or [])}
        flotten = {str(f.get("id")): f for f in (self.client.get_fleets() or [])}
        eigene = {i.get("koordinaten"): iid for iid, i in self.ov.items()}
        self._umbenennen()
        self._verlorene_abfahrten_uebernehmen(flotten, jetzt)

        for e in [e for e in self.state.warteschlange if e["status"] == "unterwegs"]:
            self._unterwegs(e, flotten, eigene, jetzt)

        if not self.state.data["laeuft"]:
            self.reserve = {}
            self._reserve_schreiben(jetzt)
            self._inseln_bewerten()
            self.state.save()
            self.letzter_tick = jetzt
            return

        for e in [e for e in self.state.warteschlange if e["status"] != "unterwegs"]:
            if e["koordinaten"] in eigene:
                self.state.abschliessen(e, "kolonisiert", "gehoert schon dir")
                continue
            if jetzt - float(e.get("letzte_pruefung") or 0) >= config.ZIEL_PRUEF_INTERVALL_S:
                try:
                    besitzer = self.ziel_pruefen(e)
                except ApiError as err:
                    log.error("Pruefung von %s fehlgeschlagen: %s", e["koordinaten"], err)
                    continue
                if besitzer and not e.get("bewohnt"):
                    self._besiedelt(e, besitzer)

        self._zuordnen()
        self.reserve = {}
        for e in list(self.state.warteschlange):
            if e["status"] == "spart":
                self._sparen(e)
            elif e["status"] == "bereit":
                self._losschicken(e, jetzt)
        self._reserve_schreiben(jetzt)
        self._inseln_bewerten()
        self.state.save()
        self.letzter_tick = jetzt

    def _verlorene_abfahrten_uebernehmen(self, flotten: dict, jetzt: float) -> None:
        """Eintraege, deren Schiff laut Hafen weg ist, obwohl die Abfahrt nicht
        verbucht wurde (Antwort auf POST /fleets ging verloren): die Flotte
        in GET /fleets suchen und uebernehmen. Sonst wuerde _zuordnen den Eintrag
        auf "wartet" setzen und ein zweites Schiff (~59.000 Rohstoffe) bauen."""
        vergeben = {str(e.get("flotte_id")) for e in self.state.warteschlange if e["status"] == "unterwegs"}
        for e in self.state.warteschlange:
            if e["status"] != "bereit":
                continue
            for fid, f in flotten.items():
                t = f.get("target") or {}
                if (fid in vergeben or (t.get("x"), t.get("y"), t.get("z")) != (e["x"], e["y"], e["z"])
                        or not int((f.get("ships") or {}).get(config.SCHIFF) or 0)):
                    continue
                vergeben.add(fid)
                e.update(status="unterwegs", flotte_id=f.get("id"), abfahrt=jetzt,
                         ankunft=iso_zu_epoch(f.get("arrive_at")),
                         rueckruf_bis=iso_zu_epoch(f.get("recallable_until")), hinweis=None)
                log.warning("ABFAHRT    %s: Flotte #%s war schon unterwegs (Antwort ging verloren) - uebernommen",
                            e["koordinaten"], f.get("id"))
                break

    # --------------------------------------------------------- Umbenennen
    def _umbenennen(self) -> None:
        """Neu aufgetauchte Inseln nach dem Muster umbenennen. Beim allerersten
        Mal werden die vorhandenen Inseln nur erfasst, nie umbenannt."""
        bekannt = self.state.data.get("bekannte_inseln")
        if bekannt is None:
            self.state.data["bekannte_inseln"] = sorted(self.ov)
            self.state.save()
            return
        einst = self.state.data.get("umbenennen") or {}
        for iid in sorted(set(self.ov) - set(bekannt)):
            bekannt.append(iid)
            alt = self.ov[iid].get("name") or ""
            if not einst.get("aktiv") or not (einst.get("muster") or "").strip():
                continue
            neu = naechster_name(einst["muster"].strip(), [i.get("name") for i in self.ov.values()])[:40]
            if neu == alt:
                continue
            try:
                self.client.rename_island(iid, neu)
            except ApiError as err:
                log.error("Neue Insel %s (%s) liess sich nicht umbenennen: %s",
                          alt, self.ov[iid].get("koordinaten"), err)
                continue
            self.ov[iid]["name"] = neu
            log.info("UMBENANNT  neue Insel %s: '%s' -> '%s'", self.ov[iid].get("koordinaten"), alt, neu)
        self.state.save()

    def _fehlt_begleitung(self, iid: int, e: dict) -> dict[str, int]:
        """Was von der Begleitung im Hafen von Insel iid noch fehlt (leer = alles da)."""
        beg = e.get("begleitung") or {}
        ov = self.ov.get(iid) or {}
        fehlt = {}
        for art, quelle in (("schiffe", "schiffe"), ("truppen", "truppen")):
            for typ, n in (beg.get(art) or {}).items():
                luecke = int(n) - int((ov.get(quelle) or {}).get(typ) or 0)
                if luecke > 0:
                    fehlt[typ] = luecke
        return fehlt

    def _mit_begleitung(self, ids, e: dict) -> list[int]:
        """Inseln, die die Begleitung des Eintrags schon im Hafen haben; hat keine
        sie, bleibt die Auswahl unveraendert."""
        ids = list(ids)
        passend = [i for i in ids if not self._fehlt_begleitung(i, e)]
        return passend or ids

    # ----------------------------------------------------------- Zuordnung
    def _zuordnen(self) -> None:
        """Jedem Eintrag (in Reihenfolge der Warteschlange) ein Schiff bzw.
        eine Bau-Insel geben."""
        geeignet = [iid for iid in self.ov if self.eignung(iid) is None]
        im_hafen = {iid: int((ov.get("schiffe") or {}).get(config.SCHIFF) or 0) for iid, ov in self.ov.items()}
        in_bau = {iid: self._in_ausbildung(iid) for iid in geeignet}
        belegt: set[int] = set()   # Inseln, die gerade fuer einen Eintrag bauen oder sparen

        offen = [e for e in self.state.warteschlange if e["status"] not in ("unterwegs",)]
        # Zuerst behalten Eintraege, deren Schiff schon gebaut wird, ihre Insel.
        for e in offen:
            iid = e.get("bau_insel")
            if e["status"] not in ("baut", "bereit") or iid not in self.ov:
                continue
            if im_hafen.get(iid, 0) > 0:
                im_hafen[iid] -= 1
                e["status"] = "bereit"
            elif e["status"] == "baut" and in_bau.get(iid, 0) > 0:
                in_bau[iid] -= 1
                belegt.add(iid)
            else:
                log.warning("Schiff fuer %s auf %s ist weder in Ausbildung noch im Hafen - neu zuordnen",
                            e["koordinaten"], self._name(iid))
                e.update(status="wartet", bau_insel=None, auftrag_id=None)

        for e in offen:
            if e["status"] in ("baut", "bereit"):
                continue
            # 1. Ein Schiff liegt schon irgendwo im Hafen: das naechstgelegene nehmen.
            frei = self._mit_begleitung([iid for iid, n in im_hafen.items() if n > 0], e)
            if frei:
                iid = min(frei, key=lambda i: self.entfernung(i, e))
                im_hafen[iid] -= 1
                e.update(status="bereit", bau_insel=iid, hinweis=None)
                continue
            # 2. Ein Schiff ist schon in Ausbildung, ohne dass ein Eintrag es fuer sich hat.
            frei = self._mit_begleitung([iid for iid, n in in_bau.items() if n > 0], e)
            if frei:
                iid = min(frei, key=lambda i: self.entfernung(i, e))
                in_bau[iid] -= 1
                belegt.add(iid)
                e.update(status="baut", bau_insel=iid, hinweis="Schiff in Ausbildung")
                continue
            # 3. Die naechstgelegene geeignete Insel, die gerade nichts anderes baut.
            kandidaten = self._mit_begleitung([iid for iid in geeignet if iid not in belegt], e)
            if not kandidaten:
                e.update(status="wartet", bau_insel=None,
                         hinweis=("alle geeigneten Inseln bauen gerade ein Schiff" if geeignet else
                                  f"keine Insel kann das Schiff bauen (Hafen {self.ab_hafen}, "
                                  f"Lager ab {max(self.kosten.values()):.0f})"))
                continue
            iid = min(kandidaten, key=lambda i: self.entfernung(i, e))
            if e.get("bau_insel") != iid:
                log.info("%s: Schiff wird auf %s gebaut (%.1f Felder, Fahrzeit %s)", e["koordinaten"],
                         self._name(iid), self.entfernung(iid, e), fmt_dauer(self.fahrzeit(iid, e)))
            belegt.add(iid)
            e.update(status="spart", bau_insel=iid)

    # ----------------------------------------------------- Sparen und Bauen
    def _sparen(self, e: dict) -> None:
        iid = e["bau_insel"]
        res = self.ov[iid].get("rohstoffe") or {}
        fehlt = {r: self.kosten[r] - float(res.get(r, 0) or 0) for r in config.RESOURCE_KEYS}
        fehlt = {r: n for r, n in fehlt.items() if n > 0}
        if fehlt:
            self.reserve[str(iid)] = dict(self.kosten)
            e["hinweis"] = f"spart auf {self._name(iid)}, es fehlen {fmt_res(fehlt)}"
            return
        try:
            antwort = self.client.start_training(iid, "hafen", config.SCHIFF, 1) or {}
        except ApiError as err:
            self.reserve[str(iid)] = dict(self.kosten)
            e["hinweis"] = f"Ausbildung auf {self._name(iid)} fehlgeschlagen: {err.message}"
            log.error("%s: Ausbildung des Schiffs auf %s fehlgeschlagen: %s", e["koordinaten"],
                      self._name(iid), err)
            return
        except requests.RequestException as err:
            # Unklar, ob die Ausbildung angenommen wurde. Nicht wiederholen: _zuordnen
            # erkennt ein Schiff in Ausbildung im naechsten Tick selbst (GET /training).
            self.reserve[str(iid)] = dict(self.kosten)
            e["hinweis"] = f"Ausbildung auf {self._name(iid)} unklar (Netzwerk) - wird im naechsten Tick geprueft"
            log.warning("%s: Ausbildung des Schiffs auf %s unklar: %s", e["koordinaten"], self._name(iid),
                        type(err).__name__)
            return
        e.update(status="baut", auftrag_id=antwort.get("id"), hinweis="Schiff in Ausbildung")
        log.info("%s: Kolonisationsschiff auf %s in Ausbildung gegeben (%s)", e["koordinaten"],
                 self._name(iid), fmt_res(self.kosten))

    def _schiff_fertig_um(self, iid: int) -> float | None:
        """Wann das Kolonisationsschiff auf dieser Insel fertig ausgebildet ist (None = unbekannt)."""
        enden = []
        try:
            auftraege = self._ausbildung(iid).get("auftraege") or []
        except ApiError:
            return None
        for a in auftraege:
            if a.get("item_typ") == config.SCHIFF and str(a.get("status", "")).lower() in AUSBILDUNG_LAEUFT:
                ende = iso_zu_epoch(a.get("finish_at"))
                if ende:
                    enden.append(ende)
        return min(enden) if enden else None

    def _begleitung_reservieren(self, jetzt: float) -> None:
        """Begleitung bewohnter Ziele zurueckhalten (der Flotten-Manager nimmt sie
        sonst fuer Raids): wenn das Schiff bereitliegt oder in hoechstens
        BEGLEITUNG_VORLAUF_S fertig wird."""
        self.einheiten_reserve = {}
        if not self.state.data["laeuft"]:
            return
        for e in self.state.warteschlange:
            iid, beg = e.get("bau_insel"), e.get("begleitung") or {}
            if not e.get("bewohnt") or iid not in self.ov or not (beg.get("schiffe") or beg.get("truppen")):
                continue
            if e["status"] == "baut":
                ende = self._schiff_fertig_um(iid)
                if ende is None or ende - jetzt > config.BEGLEITUNG_VORLAUF_S:
                    continue
            elif e["status"] != "bereit":
                continue
            ziel = self.einheiten_reserve.setdefault(str(iid), {})
            for art in ("schiffe", "truppen"):
                for typ, n in (beg.get(art) or {}).items():
                    ziel[typ] = ziel.get(typ, 0) + int(n)

    def _reserve_schreiben(self, jetzt: float) -> None:
        self._begleitung_reservieren(jetzt)
        pfad = config.RESERVE_PATH
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(pfad), prefix=".reserve-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"zeit": jetzt, "inseln": self.reserve, "einheiten": self.einheiten_reserve}, f)
            os.replace(tmp, pfad)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # --------------------------------------------------------- Losschicken
    def _losschicken(self, e: dict, jetzt: float) -> None:
        iid = e["bau_insel"]
        if self.ov[iid].get("bedrohung_im_anflug"):
            e["hinweis"] = f"Schiff liegt auf {self._name(iid)} bereit, wartet: Angriff im Anflug"
            return
        try:
            besitzer = self.ziel_pruefen(e)
        except ApiError as err:
            e["hinweis"] = f"Ziel nicht pruefbar: {err.message}"
            return
        if besitzer and not e.get("bewohnt"):
            self._besiedelt(e, besitzer)
            return
        beg = e.get("begleitung") or {}
        fehlt = self._fehlt_begleitung(iid, e)
        if fehlt:
            e["hinweis"] = f"Schiff liegt auf {self._name(iid)} bereit, wartet auf Begleitung: es fehlen {mengen_text(fehlt)}"
            return
        try:
            f = self.client.create_fleet({
                "origin_island_id": iid, "mission_type": "attack",
                "target": {"x": e["x"], "y": e["y"], "z": e["z"]},
                "ships": {config.SCHIFF: 1, **(beg.get("schiffe") or {})}, "units": dict(beg.get("truppen") or {}),
            }) or {}
        except ApiError as err:
            e["hinweis"] = f"Abfahrt fehlgeschlagen: {err.message}"
            log.error("%s: Abfahrt von %s fehlgeschlagen: %s", e["koordinaten"], self._name(iid), err)
            return
        except requests.RequestException as err:
            # Unklar, ob die Flotte losgefahren ist (create_fleet hat schon in GET /fleets
            # nachgesehen). Der naechste Tick uebernimmt sie, falls sie doch faehrt.
            e["hinweis"] = "Abfahrt unklar (Netzwerk) - wird im naechsten Tick geprueft"
            log.warning("%s: Abfahrt von %s unklar: %s", e["koordinaten"], self._name(iid), type(err).__name__)
            return
        e.update(status="unterwegs", flotte_id=f.get("id"), abfahrt=jetzt,
                 ankunft=iso_zu_epoch(f.get("arrive_at")), rueckruf_bis=iso_zu_epoch(f.get("recallable_until")),
                 hinweis=None)
        log.info("ABFAHRT    %s: Kolonisationsschiff von %s, Ankunft in %s (Flotte #%s)", e["koordinaten"],
                 self._name(iid), fmt_dauer((e["ankunft"] or jetzt) - jetzt), e["flotte_id"])

    # ------------------------------------------------------------ Unterwegs
    def _unterwegs(self, e: dict, flotten: dict, eigene: dict, jetzt: float) -> None:
        if e["koordinaten"] in eigene:
            log.info("KOLONIE    %s ist jetzt deine Insel", e["koordinaten"])
            self.state.abschliessen(e, "kolonisiert", f"kolonisiert von {self._name(e.get('bau_insel'))} aus")
            self._melden("Kolonisation gelungen", f"{e['koordinaten']} ({e.get('name') or 'Insel'}) gehoert jetzt dir.")
            return
        f = flotten.get(str(e.get("flotte_id")))
        if f is None:
            if jetzt < float(e.get("ankunft") or 0) + 120:
                return  # Uebergang an der Ankunft - die neue Insel taucht gleich auf
            text = "Schiff ist zurueck, die Insel gehoert nicht dir"
            log.warning("GESCHEITERT %s: %s", e["koordinaten"], text)
            self.state.abschliessen(e, "gescheitert", text)
            self._melden("Kolonisation gescheitert", f"{e['koordinaten']}: {text}.")
            return
        # Rueckfahrt (Kolonisation gescheitert oder zurueckgerufen) fuer die Karte im Hub
        if f.get("return_at"):
            e["rueckkehr"] = iso_zu_epoch(f.get("return_at"))
        bis = float(e.get("rueckruf_bis") or 0)
        if e.get("zurueckgerufen") or jetzt >= bis:
            return
        faellig = jetzt - float(e.get("letzte_pruefung") or 0) >= config.UNTERWEGS_PRUEF_INTERVALL_S
        letzte = bis - jetzt <= config.UNTERWEGS_PRUEF_VORLAUF_S and not e.get("letzte_vor_ende")
        if not (faellig or letzte):
            return
        if letzte:
            e["letzte_vor_ende"] = True
        try:
            besitzer = self.ziel_pruefen(e)
        except ApiError as err:
            log.error("Pruefung von %s unterwegs fehlgeschlagen: %s", e["koordinaten"], err)
            return
        if not besitzer or e.get("bewohnt"):
            return
        try:
            self.client.recall_fleet(e["flotte_id"])
            e["zurueckgerufen"] = True
            log.warning("RUECKRUF   %s wurde von %s besiedelt - Schiff zurueckgerufen", e["koordinaten"], besitzer)
        except ApiError as err:
            log.error("Rueckruf des Schiffs nach %s fehlgeschlagen: %s", e["koordinaten"], err)
        self._besiedelt(e, besitzer, zurueckgerufen=e["zurueckgerufen"])

    def _besiedelt(self, e: dict, besitzer: str, zurueckgerufen: bool = False) -> None:
        if besitzer == self.spieler:
            self.state.abschliessen(e, "kolonisiert", "gehoert schon dir")
            return
        text = f"wurde von {besitzer} besiedelt" + (" - Schiff zurueckgerufen" if zurueckgerufen else "")
        log.warning("ENTFERNT   %s %s", e["koordinaten"], text)
        self.state.abschliessen(e, "besiedelt", text)
        self._melden("Kolonisation: Ziel entfernt", f"{e['koordinaten']} {text}. "
                     "Ein schon gebautes Schiff nimmt das naechste Ziel.")

    # ---------------------------------------------------------- Uebersicht
    def _inseln_bewerten(self) -> None:
        bau = {e.get("bau_insel"): e for e in self.state.warteschlange if e["status"] in ("spart", "baut")}
        self.inseln = {}
        for iid, ov in self.ov.items():
            e = bau.get(iid)
            self.inseln[iid] = {
                "id": iid, "name": ov.get("name"), "koordinaten": ov.get("koordinaten"),
                "hafen": int((ov.get("gebaeude") or {}).get("hafen") or 0),
                "kapazitaet": float((ov.get("rohstoffe") or {}).get("kapazitaet") or 0),
                "rohstoffe": {r: float((ov.get("rohstoffe") or {}).get(r, 0) or 0) for r in config.RESOURCE_KEYS},
                "schiffe_im_hafen": int((ov.get("schiffe") or {}).get(config.SCHIFF) or 0),
                "schiffe": dict(ov.get("schiffe") or {}), "truppen": dict(ov.get("truppen") or {}),
                "ungeeignet": self.eignung(iid),
                "baut_fuer": e["koordinaten"] if e else None,
                "reserviert": self.reserve.get(str(iid)),
            }
