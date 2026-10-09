"""Der Takt des Ausbildungs-Bots: Truppen und Schiffe je Insel auf Soll halten.

Je Insel und Posten (Steinewerfer, Speerkaempfer, Bogenschuetze und die
Schiffe) gilt ein Mindestbestand: der eigene Wert der Insel, sonst der
Standard fuer alle Inseln. Ohne beides gibt es keine Vorgabe, die Insel wird
fuer diesen Posten weder aufgefuellt noch gibt sie welche ab. Schiffe werden
nur vor Ort im Hafen gebaut (kein Transport, kein Ueberschuss-Ausgleich).

Als vorhanden zaehlt, was der Insel gehoert oder gleich gehoert:
  daheim + mit eigenen Raids unterwegs + in Ausbildung + per Handel im Anflug.
Was an Mitspieler verliehen ist, zaehlt nicht - es wird nachgebildet.

Fehlt etwas:
  1. Kann die Kaserne der Insel die Einheit ausbilden, bildet sie selbst aus -
     so viel, wie die Rohstoffe hergeben (ohne die Reserve des
     Kolonisations-Bots).
  2. Sonst kommt der Ueberschuss (ueber dem eigenen Soll) der naechstgelegenen
     Insel per Handel auf Kriegsschiffen. Sind keine Kriegsschiffe daheim,
     haelt der Flotten-Manager sie zurueck (data/reserve.json), bis sie da sind.
  3. Hat keine Insel Ueberschuss, bildet die naechstgelegene Insel mit
     passender Kaserne (und eigenem Soll fuer die Einheit) die fehlende Menge
     zusaetzlich aus; ist sie daheim, geht sie per 2. los.
Bedrohte Inseln geben keine Truppen ab.
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time

import requests

import config
import geo
from api_client import ApiError

log = logging.getLogger("seekampf_ausbildung")

AUSBILDUNG_LAEUFT = ("aktiv", "wartend", "queued", "laufend")
# Kleinere Auftraege nur, wenn insgesamt weniger fehlt - sonst wuerde bei
# knappen Rohstoffen jede Minute ein Auftrag ueber eine einzelne Einheit
# eingestellt.
MIN_AUFTRAG = 5
MIN_AUFTRAG_SCHIFF = 1    # Schiffe sind teuer: auch ein einzelnes wird bestellt


def _leer() -> dict:
    return {e: 0 for e in config.ITEMS}


def reserve_lesen(pfad: str, feld: str) -> dict:
    try:
        with open(pfad, encoding="utf-8") as f:
            daten = json.load(f)
        if time.time() - float(daten.get("zeit") or 0) > config.RESERVE_MAX_ALTER_S:
            return {}
        return {str(k): v for k, v in (daten.get(feld) or {}).items() if isinstance(v, dict)}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


class Ausbilder:
    def __init__(self, client, state, notifier=None):
        self.client = client
        self.state = state
        self.notifier = notifier
        self.lock = threading.RLock()            # Takt und Ausbildung von Hand nicht gleichzeitig
        self.ov: dict[int, dict] = {}
        self.katalog: dict[int, dict] = {}       # Insel -> {posten: Katalogeintrag}, nur baubare
        self.katalog_voll: dict[str, dict] = {}  # Posten -> Name, Kosten, Dauer, ab_stufe (alle)
        self.in_ausbildung: dict[int, dict] = {}
        self.auftraege: dict[int, list] = {}
        self._gelesen: dict[int, tuple] = {}     # Insel -> (Zeit, in Ausbildung) fuer die Anzeige
        self.platz = dict(config.KRIEGSSCHIFF_PLATZ)
        self.bericht: dict[str, dict] = {}
        self.reserve: dict[str, dict] = {}
        self.letzter_tick: float | None = None
        self.letzter_fehler: str | None = None

    # ------------------------------------------------------------ Hilfen
    def _name(self, iid) -> str:
        return (self.ov.get(int(iid)) or {}).get("name") or f"Insel {iid}"

    def _entfernung(self, a: int, b: int) -> float:
        return geo.distanz_felder(geo.koord_parse(self.ov[a]["koordinaten"]),
                                  geo.koord_parse(self.ov[b]["koordinaten"]))

    def _ausbildung_lesen(self, iid: int, max_alter: float = 0) -> dict:
        """Laufende Ausbildung je Posten; merkt sich nebenbei den Katalog.
        Mit max_alter > 0 darf ein frisch gelesener Stand wiederverwendet werden."""
        gemerkt = self._gelesen.get(iid)
        if max_alter and gemerkt and time.time() - gemerkt[0] < max_alter:
            return dict(gemerkt[1])
        daten = self.client.get_training(iid) or {}
        katalog = daten.get("katalog") or {}
        self.katalog[iid] = {}
        for anlage in ("kaserne", "hafen"):
            for k in katalog.get(anlage) or []:
                typ = k.get("typ")
                if typ not in config.ITEMS or config.ANLAGE[typ] != anlage:
                    continue
                if k.get("kosten"):
                    self.katalog_voll[typ] = {
                        "name": k.get("name") or typ, "art": "truppe" if anlage == "kaserne" else "schiff",
                        "anlage": anlage, "kosten": k["kosten"], "dauer_s": k.get("dauer_s"),
                        "ab_stufe": k.get("ab_stufe")}
                if k.get("baubar"):
                    self.katalog[iid][typ] = k
        for k in katalog.get("hafen") or []:
            if k.get("typ") in self.platz and k.get("kaempfer_kap"):
                self.platz[k["typ"]] = int(k["kaempfer_kap"])
        laeuft = _leer()
        self.auftraege[iid] = []
        for a in daten.get("auftraege") or []:
            if a.get("item_typ") in laeuft and str(a.get("status", "")).lower() in AUSBILDUNG_LAEUFT:
                laeuft[a["item_typ"]] += max(0, int(a.get("anzahl") or 0) - int(a.get("abgeschlossen") or 0))
                self.auftraege[iid].append({"item": a["item_typ"], "anzahl": int(a.get("anzahl") or 0),
                                            "abgeschlossen": int(a.get("abgeschlossen") or 0),
                                            "finish_at": a.get("finish_at")})
        self._gelesen[iid] = (time.time(), dict(laeuft))
        return laeuft

    # --------------------------------------------------------------- Tick
    def tick(self) -> None:
        with self.lock:
            self._tick()

    def _tick(self) -> None:
        jetzt = time.time()
        self.reserve = {}
        self.ov = {int(i["id"]): i for i in (self.client.get_islands_overview() or [])}
        sollwerte = {iid: self.state.soll_eff(iid) for iid in self.ov}
        relevant = [iid for iid, s in sollwerte.items() if any(v is not None for v in s.values())]
        laeuft = bool(self.state.data["laeuft"]) and bool(relevant)

        # Ausbildung und Katalog aller Inseln lesen (fuer die Anzeige im Hub);
        # Inseln mit Soll immer frisch, die anderen nur ab und zu.
        in_ausbildung = {}
        for iid in self.ov:
            frisch = laeuft and iid in relevant
            try:
                in_ausbildung[iid] = self._ausbildung_lesen(iid, 0 if frisch else config.BEOBACHTUNG_ALTER_S)
            except (ApiError, requests.RequestException) as err:
                if frisch:
                    raise
                log.warning("%s: Ausbildung nicht lesbar: %s", self._name(iid), str(err)[:120])
                in_ausbildung[iid] = self.in_ausbildung.get(iid) or _leer()
        self.in_ausbildung = in_ausbildung

        if not laeuft:
            self.bericht = {}
            self._reserve_schreiben(jetzt)
            self.letzter_tick = jetzt
            return

        flotten = self.client.get_fleets() or []
        koord_id = {ov.get("koordinaten"): iid for iid, ov in self.ov.items()}
        daheim = {iid: {**{e: int((self.ov[iid].get("truppen") or {}).get(e) or 0) for e in config.EINHEITEN},
                        **{s: int((self.ov[iid].get("schiffe") or {}).get(s) or 0) for s in config.SCHIFFE}}
                  for iid in self.ov}
        unterwegs = {iid: _leer() for iid in self.ov}
        im_anflug = {iid: _leer() for iid in self.ov}
        for f in flotten:
            units = f.get("units") or {}
            herkunft = f.get("origin_island_id")
            if herkunft in unterwegs:
                # Schiffe auf Fahrt (Raid, Handel, Rueckkehr) gehoeren weiter der Herkunftsinsel.
                for s in config.SCHIFFE:
                    unterwegs[herkunft][s] += int((f.get("ships") or {}).get(s) or 0)
            if f.get("mission") == "attack" and herkunft in unterwegs:
                for e in config.EINHEITEN:
                    unterwegs[herkunft][e] += int(units.get(e) or 0)
            elif f.get("mission") in ("handel", "transport") and f.get("state") == "outbound":
                t = f.get("target") or {}
                ziel = koord_id.get(geo.koord_str(t.get("x", 0), t.get("y", 0), t.get("z", 0)))
                if ziel is not None:
                    for e in config.EINHEITEN:
                        im_anflug[ziel][e] += int(units.get(e) or 0)

        ist = {iid: {e: daheim[iid][e] + unterwegs[iid][e] + im_anflug[iid][e]
                     + in_ausbildung.get(iid, _leer())[e] for e in config.ITEMS} for iid in relevant}
        # Soll inklusive dessen, was eine Insel fuer andere mit ausbildet.
        soll_eff = {iid: dict(sollwerte[iid]) for iid in relevant}
        hinweise: dict[int, list[str]] = {iid: [] for iid in relevant}
        transporte = []   # (von, nach, einheit, anzahl)

        def ueberschuss(j: int, e: str) -> int:
            if soll_eff[j][e] is None or self.ov[j].get("bedrohung_im_anflug"):
                return 0
            return max(0, min(daheim[j][e], ist[j][e] - soll_eff[j][e]))

        # 1. Inseln, die eine Einheit nicht selbst ausbilden koennen: Versorger suchen.
        for iid in relevant:
            for e in config.EINHEITEN:
                soll = sollwerte[iid][e]
                if soll is None or ist[iid][e] >= soll or e in self.katalog.get(iid, {}):
                    continue
                fehlt = soll - ist[iid][e]
                geber = [j for j in relevant if j != iid and ueberschuss(j, e) > 0]
                if geber:
                    j = min(geber, key=lambda g: self._entfernung(g, iid))
                    n = min(fehlt, ueberschuss(j, e))
                    transporte.append((j, iid, e, n))
                    daheim[j][e] -= n
                    ist[j][e] -= n
                    fehlt -= n
                if fehlt <= 0:
                    continue
                bilder = [j for j in relevant if j != iid and soll_eff[j][e] is not None
                          and e in self.katalog.get(j, {})]
                if not bilder:
                    hinweise[iid].append(f"{fehlt} {e} fehlen - keine Insel mit Soll fuer {e} kann sie ausbilden")
                    continue
                j = min(bilder, key=lambda g: self._entfernung(g, iid))
                soll_eff[j][e] += fehlt
                hinweise[iid].append(f"{fehlt} {e} werden auf {self._name(j)} ausgebildet und dann gebracht")
                hinweise[j].append(f"bildet {fehlt} {e} fuer {self._name(iid)} mit aus")

        # 2. Transporte
        for von, nach, e, n in transporte:
            self._verlegen(von, nach, e, n, hinweise)

        # 3. Ausbildung vor Ort
        kolo = reserve_lesen(config.KOLO_RESERVE_PATH, "inseln")
        for iid in relevant:
            budget = {r: float((self.ov[iid].get("rohstoffe") or {}).get(r, 0) or 0)
                      - float((kolo.get(str(iid)) or {}).get(r, 0) or 0) for r in config.RESOURCE_KEYS}
            for e in config.ITEMS:
                soll = soll_eff[iid][e]
                if soll is None or ist[iid][e] >= soll:
                    continue
                if e not in self.katalog.get(iid, {}):
                    if e in config.SCHIFFE:
                        k = self.katalog_voll.get(e) or {}
                        hinweise[iid].append(f"{soll - ist[iid][e]} {e} fehlen - Hafen zu niedrig"
                                             f"{f' (braucht Stufe {k['ab_stufe']})' if k.get('ab_stufe') else ''}")
                    continue
                fehlt = soll - ist[iid][e]
                kosten = self.katalog[iid][e].get("kosten") or {}
                leistbar = min((math.floor(max(0.0, budget[r]) / float(kosten[r]))
                                for r in config.RESOURCE_KEYS if float(kosten.get(r, 0) or 0) > 0), default=0)
                n = min(fehlt, leistbar)
                mindest = MIN_AUFTRAG if e in config.EINHEITEN else MIN_AUFTRAG_SCHIFF
                if n <= 0 or (n < fehlt and n < mindest):
                    hinweise[iid].append(f"{fehlt} {e} fehlen - wartet auf Rohstoffe")
                    continue
                try:
                    self.client.start_training(iid, config.ANLAGE[e], e, n)
                except ApiError as err:
                    hinweise[iid].append(f"Ausbildung von {e} fehlgeschlagen: {err.message}")
                    log.error("%s: Ausbildung von %d %s fehlgeschlagen: %s", self._name(iid), n, e, err)
                    continue
                except requests.RequestException as err:
                    # Unklar, ob der Auftrag angenommen wurde: nicht wiederholen, die Rohstoffe
                    # als ausgegeben rechnen; der naechste Tick liest die Ausbildung frisch.
                    for r in config.RESOURCE_KEYS:
                        budget[r] -= n * float(kosten.get(r, 0) or 0)
                    hinweise[iid].append(f"Ausbildung von {e} unklar (Netzwerk) - wird im naechsten Tick geprueft")
                    log.warning("%s: Ausbildung von %d %s unklar: %s", self._name(iid), n, e, type(err).__name__)
                    self._gelesen.pop(iid, None)
                    continue
                for r in config.RESOURCE_KEYS:
                    budget[r] -= n * float(kosten.get(r, 0) or 0)
                ist[iid][e] += n
                in_ausbildung[iid][e] += n
                text = f"{self._name(iid)}: {n} {e} in Ausbildung gegeben (Soll {soll})"
                log.info(text)
                self.state.merken(text)

        self.bericht = {str(iid): {
            "soll": sollwerte[iid], "soll_eff": soll_eff[iid], "ist": ist[iid], "daheim": daheim[iid],
            "unterwegs": unterwegs[iid], "im_anflug": im_anflug[iid], "in_ausbildung": in_ausbildung[iid],
            "baubar": sorted(self.katalog.get(iid, {})), "hinweise": hinweise[iid],
        } for iid in relevant}
        self._reserve_schreiben(jetzt)
        self.state.save()
        self.letzter_tick = jetzt

    # ------------------------------------------------- Ausbilden von Hand
    def ausbilden(self, insel_id: int, posten: str, anzahl: int) -> str:
        """Sofort einen Auftrag geben (vom Hub). Die Kosten zieht das Spiel ab.
        ValueError bei ungueltigen Angaben, ApiError wenn das Spiel ablehnt."""
        if posten not in config.ITEMS:
            raise ValueError(f"unbekannter Posten: {posten}")
        if isinstance(anzahl, bool) or int(anzahl) != anzahl or not 1 <= int(anzahl) <= 10000:
            raise ValueError("anzahl muss eine ganze Zahl von 1 bis 10000 sein")
        anzahl = int(anzahl)
        with self.lock:
            if int(insel_id) not in self.ov:
                raise ValueError("unbekannte Insel")
            self.client.start_training(int(insel_id), config.ANLAGE[posten], posten, anzahl)
            self._gelesen.pop(int(insel_id), None)
            text = f"{self._name(insel_id)}: {anzahl} {posten} von Hand in Ausbildung gegeben"
            log.info(text)
            self.state.merken(text)
            self.state.save()
        return text

    # -------------------------------------------------------------- Status
    def status(self) -> dict:
        """Alles, was die Hub-Seite braucht: Katalog, Standard, je Insel Bestand,
        Soll und laufende Ausbildung."""
        inseln = []
        for iid, ov in self.ov.items():      # Reihenfolge wie im Spiel
            eintrag = self.state.insel(iid)
            truppen, schiffe = ov.get("truppen") or {}, ov.get("schiffe") or {}
            bestand = {e: int(truppen.get(e) or 0) for e in config.EINHEITEN}
            bestand.update({s: int(schiffe.get(s) or 0) for s in config.SCHIFFE})
            in_aus = self.in_ausbildung.get(iid) or _leer()
            bericht = self.bericht.get(str(iid))
            geb = ov.get("gebaeude") or {}
            inseln.append({
                "id": iid, "name": ov.get("name"), "koordinaten": ov.get("koordinaten"),
                "kaserne": int(geb.get("kaserne") or 0), "hafen": int(geb.get("hafen") or 0),
                "bedrohung": bool(ov.get("bedrohung_im_anflug")), "aktiv": eintrag["aktiv"],
                "rohstoffe": ov.get("rohstoffe") or {},
                "bestand": bestand, "in_ausbildung": in_aus,
                "ist": bericht["ist"] if bericht else {p: bestand[p] + in_aus[p] for p in config.ITEMS},
                "soll": eintrag["soll"], "aus": eintrag["aus"], "soll_eff": self.state.soll_eff(iid),
                "baubar": sorted(self.katalog.get(iid, {})), "auftraege": self.auftraege.get(iid, []),
                "hinweise": bericht["hinweise"] if bericht else [],
            })
        return {"truppen": list(config.EINHEITEN), "schiffe": list(config.SCHIFFE),
                "katalog": self.katalog_voll, "standard": self.state.standard(), "inseln": inseln}

    # ------------------------------------------------------------ Verlegen
    def _verlegen(self, von: int, nach: int, e: str, n: int, hinweise: dict) -> None:
        """n Einheiten per Handel auf Kriegsschiffen verlegen - so viele, wie
        die Kriegsschiffe im Hafen fassen. Fehlt Platz, haelt der
        Flotten-Manager heimkehrende Kriegsschiffe fuer den Rest zurueck."""
        hafen = {t: int((self.ov[von].get("schiffe") or {}).get(t) or 0) for t in self.platz}
        ships: dict[str, int] = {}
        rest = n
        for typ in sorted(self.platz, key=lambda t: -self.platz[t]):   # grosse zuerst
            if rest <= 0:
                break
            k = min(hafen[typ], math.ceil(rest / self.platz[typ]))
            if k > 0:
                ships[typ] = k
                rest -= k * self.platz[typ]
        mit = min(n, sum(k * self.platz[t] for t, k in ships.items()))
        if mit < n:
            fehlt = n - mit
            self.reserve.setdefault(str(von), {})
            for typ, platz in self.platz.items():
                self.reserve[str(von)][typ] = self.reserve[str(von)].get(typ, 0) + math.ceil(fehlt / platz)
            hinweise[nach].append(f"{fehlt} {e} warten auf {self._name(von)} auf Kriegsschiffe")
        if mit <= 0:
            return
        ziel = geo.koord_parse(self.ov[nach]["koordinaten"])
        try:
            self.client.create_fleet({"origin_island_id": von, "mission_type": "handel",
                                      "target": {"x": ziel[0], "y": ziel[1], "z": ziel[2]},
                                      "ships": ships, "units": {e: mit}, "resources": {}})
        except ApiError as err:
            hinweise[nach].append(f"Verlegung von {self._name(von)} fehlgeschlagen: {err.message}")
            log.error("Verlegung von %d %s %s -> %s fehlgeschlagen: %s", mit, e, self._name(von),
                      self._name(nach), err)
            return
        except requests.RequestException as err:
            # Unklar, ob die Flotte losgefahren ist (create_fleet hat schon in GET /fleets
            # nachgesehen); sie zaehlt im naechsten Tick als "im Anflug", falls sie faehrt.
            hinweise[nach].append(f"Verlegung von {self._name(von)} unklar (Netzwerk) - wird im naechsten Tick geprueft")
            log.warning("Verlegung von %d %s %s -> %s unklar: %s", mit, e, self._name(von), self._name(nach),
                        type(err).__name__)
            return
        text = f"{mit} {e} von {self._name(von)} nach {self._name(nach)} verlegt"
        log.info(text)
        self.state.merken(text)

    def _reserve_schreiben(self, jetzt: float) -> None:
        pfad = config.RESERVE_PATH
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(pfad), prefix=".reserve-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"zeit": jetzt, "kriegsschiffe": self.reserve}, f)
            os.replace(tmp, pfad)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
