"""Die Schleife, die die Flotten fahren laesst.

Ablauf eines Ticks:
  1. Laufzeit fortschreiben (zaehlt nur, solange der Manager laeuft)
  2. GET /fleets - Ankunft und Rueckkehr der eigenen Flotten erkennen
  3. Kampfberichte zu angekommenen Flotten aufloesen (Sieg? Beute?)
  4. Pausen pruefen: Bedrohung im Anflug, Lager voll
  5. So viele Flotten losschicken, wie Schiffe und Steinewerfer hergeben

Eine Flotte verschwindet aus GET /fleets, sobald sie wieder daheim ist - genau
dann ist ihr Schiff wieder frei und Schritt 5 schickt sie sofort zum naechsten
Ziel der Rotation.
"""
from __future__ import annotations

import itertools
import logging
import time
from datetime import datetime, timezone

import config
import geo
import scanner
from api_client import ApiError

log = logging.getLogger("seekampf_flotten_manager")


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

        self.insel_id: int | None = None
        self.insel_name: str = ""
        self.heimat: tuple[int, int, int] | None = None

        self.letzter_fehler: str | None = None
        self.letzter_tick: float | None = None

        self._overview = None
        self._overview_zeit = 0.0
        self._letzter_tick_ts: float | None = None
        self._dirty = False
        self._letzte_speicherung = 0.0

    # ------------------------------------------------------------------ Basis
    @property
    def settings(self) -> dict:
        return self.state.settings

    def flotte_ships(self) -> dict:
        s = self.settings
        ships = {}
        if s["flotte_kriegsschiffe"] > 0:
            ships[s["flotte_kriegsschiff_typ"]] = int(s["flotte_kriegsschiffe"])
        if s["flotte_handelsschiffe"] > 0:
            ships[s["flotte_handelsschiff_typ"]] = int(s["flotte_handelsschiffe"])
        return ships

    def flotte_units(self) -> dict:
        s = self.settings
        return {s["flotte_einheit_typ"]: int(s["flotte_einheiten"])} if s["flotte_einheiten"] > 0 else {}

    def overview(self, max_alter: float = 15.0):
        """Uebersicht der eigenen Insel, kurz zwischengespeichert."""
        if self._overview is None or time.time() - self._overview_zeit > max_alter:
            inseln = self.client.get_islands_overview() or []
            if not inseln:
                raise ApiError(0, "keine_insel", "GET /me/islands/overview lieferte keine Insel")
            gewaehlt = next((i for i in inseln if i.get("id") == self.insel_id), inseln[0])
            self._overview = gewaehlt
            self._overview_zeit = time.time()
            self.insel_id = gewaehlt["id"]
            self.insel_name = gewaehlt.get("name") or f"Insel {self.insel_id}"
            self.heimat = geo.koord_parse(gewaehlt["koordinaten"])
        return self._overview

    def start(self) -> None:
        """Einmalig beim Hochfahren: Heimatinsel bestimmen, ggf. erster Scan."""
        try:
            me = self.client.get_me()
            self.insel_id = me.get("current_island_id")
        except ApiError as e:
            log.warning("GET /me fehlgeschlagen (%s) - nehme die erste Insel aus der Uebersicht", e)
        ov = self.overview(max_alter=0)
        log.info("Heimatinsel: %s (%s, id=%s)", self.insel_name,
                 geo.koord_str(*self.heimat), self.insel_id)
        if self.state.data["stats"]["seit"] is None:
            self.state.data["stats"]["seit"] = time.time()
            self._dirty = True
        if not self.state.data["ziele"]:
            self.scan()

    def scan(self) -> dict:
        if self.heimat is None:
            self.overview(max_alter=0)
        return scanner.scan(self.client, self.state, self.heimat, self.flotte_ships())

    # ------------------------------------------------------------------- Tick
    def tick(self) -> None:
        jetzt = time.time()
        self._laufzeit_fortschreiben(jetzt)

        flotten_api = self.client.get_fleets()
        self._flotten_abgleichen(flotten_api, jetzt)
        self._berichte_aufloesen()

        if not self.state.data["laeuft"]:
            self._pause("gestoppt")
            self._speichern_wenn_noetig(jetzt)
            self.letzter_tick = jetzt
            return

        ov = self.overview()
        self._scan_wenn_faellig(jetzt)
        self._report_wenn_faellig(jetzt)

        if self._bedrohung_behandeln(ov, flotten_api, jetzt):
            self._speichern_wenn_noetig(jetzt)
            self.letzter_tick = jetzt
            return
        if self._lager_voll(ov):
            self._pause("Lager voll - Beute waere verschenkt")
            self._speichern_wenn_noetig(jetzt)
            self.letzter_tick = jetzt
            return

        self._pause(None)
        self._losschicken(jetzt)
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

    def _pause(self, grund: str | None) -> None:
        if self.state.data["pause_grund"] != grund:
            if grund:
                log.info("Pausiert: %s", grund)
            self.state.data["pause_grund"] = grund
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
            "angekommen": False,
            "verbucht": False,
            "zurueckgerufen": False,
            "fremd": False,
        }

    def _flotten_abgleichen(self, flotten_api: list, jetzt: float) -> None:
        aktiv = self.state.data["flotten"]
        gesehen = set()

        for f in flotten_api:
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
            log.info("RUECKKEHR  Flotte #%s von %s (%s) - Beute laut Flotte: %s",
                     fid, rec["koordinaten"], rec["ziel_name"], fmt_beute(rec.get("loot")))

    def _ankunft(self, rec: dict, jetzt: float) -> None:
        rec["angekommen"] = True
        koord = rec["koordinaten"]
        log.info("ANKUNFT    Flotte #%s bei %s (%s)%s", rec["id"], koord, rec["ziel_name"],
                 f" - Rueckkehr in {fmt_dauer((rec['return_at'] or jetzt) - jetzt)}"
                 if rec.get("return_at") else "")

        if not rec["verbucht"]:
            rec["verbucht"] = True
            stats = self.state.data["stats"]
            stats["raids"] += 1
            if koord not in stats["inseln_besucht"]:
                stats["inseln_besucht"].append(koord)
            ziel = self.state.ziel(koord)
            if ziel is not None:
                ziel["raids"] += 1
                ziel["letzter_raid"] = jetzt
            self.state.data.setdefault("offene_berichte", []).append({
                "koordinaten": koord, "flotte": rec["id"],
                "arrive_at": rec["arrive_at"] or jetzt, "seit": jetzt,
            })
        self._dirty = True

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
                self._bericht_verbuchen(eintrag, treffer)
                verbucht.append(treffer["id"])
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
        ziel = self.state.ziel(koord)
        stats = self.state.data["stats"]
        sieg = bool(p.get("sieg"))
        beute = {r: float((p.get("loot") or {}).get(r, 0) or 0) for r in config.RESOURCE_KEYS}

        if sieg:
            for r in config.RESOURCE_KEYS:
                stats[r] += beute[r]
            if ziel is not None:
                ziel["niederlagen"] = 0
                ziel["letzte_beute"] = beute
                for r in config.RESOURCE_KEYS:
                    ziel["beute_gesamt"][r] = ziel["beute_gesamt"].get(r, 0.0) + beute[r]
            log.info("BEUTE      %s (%s): %s", koord, ziel["name"] if ziel else "?", fmt_beute(beute))
            return

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
        grenze = int(self.settings["niederlagen_bis_blacklist"])
        if ziel["niederlagen"] >= grenze:
            tage = float(self.settings["blacklist_tage"])
            ziel["blacklist_bis"] = time.time() + tage * 86400
            ziel["blacklist_grund"] = f"{ziel['niederlagen']} Niederlagen in Folge (Garnison?)"
            ziel["niederlagen"] = 0
            log.warning("BLACKLIST  %s fuer %.0f Tage gesperrt: %s",
                        koord, tage, ziel["blacklist_grund"])

    # ------------------------------------------------------------ Sicherheit
    def _bedrohung_behandeln(self, ov: dict, flotten_api: list, jetzt: float) -> bool:
        if not ov.get("bedrohung_im_anflug"):
            return False
        if not self.settings["rueckruf_bei_bedrohung"]:
            self._pause("Bedrohung im Anflug (Rueckruf abgeschaltet)")
            return True
        for f in flotten_api:
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
                log.warning("RUECKRUF   Flotte #%s von %s zurueckgerufen (Bedrohung im Anflug)",
                            f["id"], rec["koordinaten"])
            except ApiError as e:
                log.error("Rueckruf von Flotte #%s fehlgeschlagen: %s", f["id"], e)
        self._pause("Bedrohung im Anflug - Flotten bleiben daheim")
        return True

    def _lager_voll(self, ov: dict) -> bool:
        res = ov.get("rohstoffe") or {}
        kapazitaet = float(res.get("kapazitaet") or 0)
        if kapazitaet <= 0:
            return False
        grenze = kapazitaet * float(self.settings["lager_voll_schwelle"])
        return all(float(res.get(r, 0) or 0) >= grenze for r in config.RESOURCE_KEYS)

    # --------------------------------------------------------------- Scan/Report
    def _scan_wenn_faellig(self, jetzt: float) -> None:
        intervall = float(self.settings["scan_intervall_stunden"]) * 3600
        letzter = self.state.data.get("letzter_scan") or 0
        if intervall > 0 and jetzt - letzter >= intervall:
            try:
                self.scan()
            except ApiError as e:
                log.error("Scan fehlgeschlagen: %s", e)

    def _report_wenn_faellig(self, jetzt: float) -> None:
        if self.notifier is None or not self.settings["telegram_report"]:
            return
        import report  # spaet importiert: ohne Telegram-Token nie gebraucht
        report.wenn_faellig(self.state, self.notifier, jetzt)

    # ------------------------------------------------------------ Losschicken
    def _freie_flotten(self, ov: dict) -> int:
        """Wie viele vollstaendige Flotten stehen daheim bereit?

        truppen/schiffe in der Uebersicht zaehlen nur, was NICHT unterwegs ist -
        die Rechnung beruecksichtigt fahrende Flotten damit automatisch.
        """
        vorrat = dict(ov.get("schiffe") or {})
        vorrat.update({k: v for k, v in (ov.get("truppen") or {}).items()})
        bedarf = {**self.flotte_ships(), **self.flotte_units()}
        if not bedarf:
            return 0
        moeglich = min(int(vorrat.get(typ, 0)) // anzahl for typ, anzahl in bedarf.items())
        grenze = int(self.settings["max_flotten"])
        if grenze > 0:
            moeglich = min(moeglich, grenze - len(self.state.data["flotten"]))
        return max(0, moeglich)

    def _naechstes_ziel(self, belegt: set) -> dict | None:
        """Stur reihum: der Zeiger wandert bei jedem Griff eins weiter."""
        rotation = self.state.data["rotation"]
        if not rotation:
            return None
        jetzt = time.time()
        for _ in range(len(rotation)):
            index = self.state.data["rotation_index"] % len(rotation)
            koord = rotation[index]
            self.state.data["rotation_index"] = (index + 1) % len(rotation)
            ziel = self.state.ziel(koord)
            if ziel is None or koord in belegt or self.state.ist_gesperrt(ziel, jetzt):
                continue
            return ziel
        return None

    def _prioritaet(self, ov: dict) -> str | None:
        """Welcher Rohstoff soll gepluendert werden - oder gleichmaessig (None)?

        Grundregel: der Rohstoff, von dem am wenigsten da ist. Dabei zaehlt
        nicht der aktuelle Lagerstand, sondern der ERWARTETE: was die schon
        fahrenden Flotten mitbringen, ist eingerechnet. Vier Flotten mit Stein
        unterwegs heben Stein rechnerisch so weit an, dass die fuenfte von
        selbst etwas anderes holt.
        """
        modus = str(self.settings["rohstoff_modus"])
        if modus in config.RESOURCE_KEYS:
            return modus
        if modus == "gleichmaessig":
            return None

        res = ov.get("rohstoffe") or {}
        kapazitaet = float(res.get("kapazitaet") or 0)
        ladung = geo.ladevolumen(self.flotte_ships())
        erwartet = {r: float(res.get(r, 0) or 0) for r in config.RESOURCE_KEYS}
        for rec in self.state.data["flotten"].values():
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
            grenze = kapazitaet * float(self.settings["lager_voll_schwelle"])
            kandidaten = {r: v for r, v in erwartet.items() if v < grenze}
            if not kandidaten:
                return None
            # Liegt alles dicht beieinander, bringt Priorisieren nichts - dann
            # lieber gleichmaessig und dafuer die volle Ladung.
            if max(erwartet.values()) - min(erwartet.values()) < float(
                    self.settings["ausgleich_schwelle"]) * kapazitaet:
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
        koord = ziel["koordinaten"]
        self.state.data["ziele"].pop(koord, None)
        rotation = self.state.data["rotation"]
        if koord in rotation:
            index = rotation.index(koord)
            rotation.remove(koord)
            if index < self.state.data["rotation_index"]:
                self.state.data["rotation_index"] -= 1
        if rotation:
            self.state.data["rotation_index"] %= len(rotation)
        else:
            self.state.data["rotation_index"] = 0
        log.info("Ziel %s aus der Rotation genommen: %s", koord, grund)
        self._dirty = True

    def _losschicken(self, jetzt: float) -> None:
        # Erst mit dem zwischengespeicherten Stand grob pruefen; sieht es nach
        # einer freien Flotte aus, den Bestand frisch holen, bevor wirklich
        # losgeschickt wird - sonst faehrt eine Flotte los, deren Schiffe eine
        # Sekunde vorher schon vergeben wurden.
        if self._freie_flotten(self.overview()) <= 0:
            return
        ov = self.overview(max_alter=0)
        offen = self._freie_flotten(ov)
        if offen <= 0:
            return
        belegt = {rec["koordinaten"] for rec in self.state.data["flotten"].values()}
        ships, units = self.flotte_ships(), self.flotte_units()
        versuche = len(self.state.data["rotation"]) + offen

        for _ in itertools.repeat(None, versuche):
            if offen <= 0:
                return
            ziel = self._naechstes_ziel(belegt)
            if ziel is None:
                log.info("Kein freies Ziel in der Rotation (%d Ziele, %d gesperrt/belegt)",
                         len(self.state.data["rotation"]), len(belegt))
                return
            try:
                if not self._ziel_ist_frei(ziel):
                    self._ziel_verwerfen(ziel, "hat inzwischen einen Besitzer")
                    continue
            except ApiError as e:
                log.error("Freiheits-Check fuer %s fehlgeschlagen: %s", ziel["koordinaten"], e)
                continue

            prio = self._prioritaet(ov)
            payload = {
                "origin_island_id": self.insel_id,
                "mission_type": "attack",
                "target": {"x": ziel["x"], "y": ziel["y"], "z": ziel["z"]},
                "ships": ships,
                "units": units,
                "razzia_ziel": self.settings["razzia_ziel"] or None,
            }
            if prio:
                payload["razzia_prioritaet"] = prio
            try:
                antwort = self.client.create_fleet(payload)
            except ApiError as e:
                log.error("Flotte nach %s konnte nicht starten: %s", ziel["koordinaten"], e)
                return  # fehlt etwas (Schiffe, Einheiten), hilft der naechste Versuch auch nicht

            rec = self._record(antwort)
            rec["prioritaet"] = prio
            rec["fremd"] = False
            self.state.data["flotten"][str(rec["id"])] = rec
            belegt.add(rec["koordinaten"])
            offen -= 1
            self._dirty = True
            log.info("ABFAHRT    Flotte #%s -> %s (%s), %s, Beute: %s, Ankunft in %s, zurueck in %s",
                     rec["id"], ziel["koordinaten"], ziel["name"],
                     ", ".join(f"{n}x {t}" for t, n in {**ships, **units}.items()),
                     prio or "gleichmaessig",
                     fmt_dauer((rec["arrive_at"] or jetzt) - jetzt),
                     fmt_dauer((rec["return_at"] or 0) - jetzt) if rec["return_at"]
                     else fmt_dauer(2 * ((rec["arrive_at"] or jetzt) - jetzt)))
