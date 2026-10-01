"""Gemeinsame Grundlage aller Rollen: wer bin ich, wer ist in der Allianz,
wer nimmt am Protokoll teil, und wie kommt eine Nachricht sicher hinaus.

Alles, was nach aussen wirkt (Beitrag, PN, Flotte, Loeschen), laeuft durch
diese Klasse. Ausgehende Protokoll-Nachrichten werden vor dem Senden mit dem
eigenen Parser gegengelesen und auf die Linksperre geprueft - was der eigene
Parser nicht so versteht wie gemeint, geht nicht raus.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config
import protokoll
from api_client import ApiError

log = logging.getLogger("seekampf_allianz_bot")


# Beitragstypen, die ein Vorgang neu postet, wenn sich etwas aendert - es gilt nur der neueste.
ERSETZBAR = ("ANFRAGE", "NOTRUF")
# Beitragstypen, die einen Vorgang abschliessen.
ABSCHLUSS = ("ERLEDIGT", "ENTWARNUNG")


class NichtGesendet(Exception):
    """Eine ausgehende Nachricht wurde vor dem Senden verworfen."""


def iso(dt: datetime) -> str:
    return protokoll.zeit_normal(dt)


def zeit(wert: str | None) -> datetime | None:
    return protokoll.zeit_lesen(wert) if wert else None


def koord_tupel(text: str) -> tuple[int, int, int]:
    x, y, z = (int(t) for t in text.split(":"))
    return x, y, z


def fahrzeit(von: str, nach: str, knoten: float) -> timedelta:
    """Fahrzeit nach legal/regeln: Felder / Knoten * 10 min, mit Untergrenze."""
    def feld(k):
        x, y, z = koord_tupel(k)
        kante = config.SEKTOR_KANTE
        return x * kante + (z - 1) % kante, y * kante + (z - 1) // kante
    a, b = feld(von), feld(nach)
    sek = math.dist(a, b) / knoten * 600.0 if knoten > 0 else 0.0
    gleich = koord_tupel(von)[:2] == koord_tupel(nach)[:2]
    return timedelta(seconds=max(config.MIN_FAHRZEIT_SELBER_SEKTOR_S if gleich
                                 else config.MIN_FAHRZEIT_S, sek))


def truppen_schiffe(menge: int, hafen: dict) -> dict:
    """Kriegsschiffe aus `hafen`, die `menge` Kaempfer tragen. {} wenn es nicht reicht."""
    gewaehlt, rest = {}, menge
    for typ, kap in config.TRUPPEN_SCHIFFE.items():
        if rest <= 0:
            break
        n = min(int(hafen.get(typ, 0) or 0), math.ceil(rest / kap))
        if n > 0:
            gewaehlt[typ] = n
            rest -= n * kap
    return gewaehlt if rest <= 0 else {}


def truppen_kapazitaet(hafen: dict) -> int:
    return sum(int(hafen.get(t, 0) or 0) * k for t, k in config.TRUPPEN_SCHIFFE.items())


def knoten(schiffe: dict) -> float:
    werte = [config.SCHIFF_KNOTEN[t] for t, n in schiffe.items() if n and t in config.SCHIFF_KNOTEN]
    return float(min(werte)) if werte else 0.0


class Kontext:
    def __init__(self, client, state, notifier):
        self.client = client
        self.state = state
        self.notifier = notifier
        self.tz = ZoneInfo(config.TIMEZONE_NAME)

        self.ich_id: int | None = None
        self.ich_name: str = ""
        self.allianz_id: int | None = None
        # Aktuelle Insel im Spiel - Ziel fuer Anfragen von Hand ohne Inselangabe.
        self.heimat_id: int | None = None

        self.mitglieder: dict[int, str] = {}
        self._mitglieder_stand: datetime | None = None
        self.threads: dict[str, int | None] = {}
        self.praesenzen: dict[int, dict] = {}

        self._inseln: dict[int, dict] = {}
        self._inseln_stand: datetime | None = None
        self._incoming = None
        self._incoming_stand: datetime | None = None

    # ---------------------------------------------------------------- Zeit
    def jetzt(self) -> datetime:
        return self.client.jetzt()

    # ------------------------------------------------------------ Identitaet
    def start(self) -> None:
        me = self.client.get_me()
        self.ich_id = int(me["id"])
        self.ich_name = me["name"]
        allianz = me.get("allianz") or {}
        self.allianz_id = allianz.get("id")
        if not self.allianz_id:
            raise RuntimeError("Konto ist in keiner Allianz - das Protokoll braucht eine.")
        inseln = self.inseln(max_alter=0)
        self.heimat_id = int(me.get("current_island_id") or next(iter(inseln)))
        self.altbestand_zuordnen()
        self.mitglieder_aktualisieren(erzwingen=True)
        self.threads_finden()
        log.info("Start als %s (#%s), Allianz %s, Inseln: %s", self.ich_name, self.ich_id, self.allianz_id,
                 ", ".join(f"{i.get('name')} {self.koord(iid)} (id={iid})" for iid, i in inseln.items()))

    def altbestand_zuordnen(self) -> None:
        """Notruf/Anfrage aus der Ein-Insel-Zeit gehoerten zur Heimatinsel."""
        for schluessel in ("notrufe", "anfragen"):
            alt = self.state.data[schluessel].pop("?", None)
            if alt is None:
                continue
            alt.setdefault("insel_id", self.heimat_id)
            alt.setdefault("insel", self.koord(self.heimat_id))
            alt.setdefault("insel_name", self.name(self.heimat_id))
            self.state.data[schluessel][str(self.heimat_id)] = alt
            log.info("Altbestand %s %s der Insel %s zugeordnet", schluessel, alt["vorgang"], alt["insel"])
        self.state.speichern()

    # ---------------------------------------------------------------- Inseln
    def inseln(self, max_alter: float = 15.0) -> dict[int, dict]:
        """Alle eigenen Inseln (Uebersicht je Insel). Neue Inseln tauchen hier
        automatisch auf und werden ab dann mit ueberwacht."""
        jetzt = self.jetzt()
        if (not self._inseln or self._inseln_stand is None
                or (jetzt - self._inseln_stand).total_seconds() > max_alter):
            liste = self.client.get_islands_overview()
            if not liste:
                raise ApiError(0, "keine_insel", "GET /me/islands/overview lieferte keine Insel")
            neu = {int(i["id"]): i for i in liste}
            if self._inseln:
                for iid in neu.keys() - self._inseln.keys():
                    self.melden("Neue Insel", f"{neu[iid].get('name')} ({neu[iid].get('koordinaten')}) "
                                              f"wird ab jetzt vom Allianz-Bot mit ueberwacht.")
            self._inseln = neu
            self._inseln_stand = jetzt
        return self._inseln

    def overview(self, insel_id: int, max_alter: float = 15.0) -> dict:
        inseln = self.inseln(max_alter)
        if insel_id not in inseln:
            raise ApiError(0, "insel_weg", f"Insel {insel_id} gehoert uns nicht (mehr)")
        return inseln[insel_id]

    def koord(self, insel_id: int) -> str:
        return protokoll._koordinate(self.overview(insel_id)["koordinaten"])

    def name(self, insel_id: int) -> str:
        return self.overview(insel_id).get("name") or str(insel_id)

    def insel_zu_koord(self, koord: str) -> int | None:
        return next((iid for iid in self.inseln() if self.koord(iid) == koord), None)

    def incoming(self, max_alter: float = 15.0) -> list:
        jetzt = self.jetzt()
        if (self._incoming is None or self._incoming_stand is None
                or (jetzt - self._incoming_stand).total_seconds() > max_alter):
            self._incoming = self.client.get_incoming()
            self._incoming_stand = jetzt
        return self._incoming

    def feindliche_flotten(self, insel_id: int, max_alter: float = 15.0) -> list:
        """Fremde Flotten mit Ziel auf dieser Insel, die kein Handel/Support/Spion sind."""
        harmlos = {"transport", "support", "spy"}
        return [f for f in self.incoming(max_alter)
                if not f.get("vorbeifahrend") and not f.get("eigene")
                and f.get("ziel_insel_id") == insel_id
                and f.get("mission") not in harmlos]

    def bedroht(self, insel_id: int) -> bool:
        return bool(self.feindliche_flotten(insel_id) or self.overview(insel_id).get("bedrohung_im_anflug"))

    # ------------------------------------------------------------ Mitglieder
    def mitglieder_aktualisieren(self, erzwingen: bool = False) -> None:
        jetzt = self.jetzt()
        if (not erzwingen and self._mitglieder_stand
                and (jetzt - self._mitglieder_stand).total_seconds() < config.MITGLIEDER_INTERVALL_S):
            return
        allianz = self.client.get_alliance(self.allianz_id)
        self.mitglieder = {int(m["user_id"]): m["name"] for m in allianz.get("mitglieder") or []}
        self._mitglieder_stand = jetzt

    def mitglieder_frisch(self) -> bool:
        """Pruefschritt 4 verlangt eine hoechstens 1 h alte Mitgliederliste."""
        if self._mitglieder_stand is None:
            return False
        if self.jetzt() - self._mitglieder_stand > config.MITGLIEDER_MAX_ALTER:
            try:
                self.mitglieder_aktualisieren(erzwingen=True)
            except (ApiError, requests.RequestException) as e:
                log.error("Mitgliederliste nicht aktualisierbar: %s", e)
                return False
        return True

    def id_zu_name(self, name: str) -> int | None:
        for uid, n in self.mitglieder.items():
            if n == name:
                return uid
        return None

    # ---------------------------------------------------------------- Threads
    def threads_finden(self) -> None:
        alle = self.client.list_threads()
        for kanal, titel in config.THREADS.items():
            treffer = sorted(int(t["id"]) for t in alle if t.get("titel") == titel)
            self.threads[kanal] = treffer[0] if treffer else None
            if not treffer:
                self.einmal_melden(f"thread_fehlt:{kanal}", "Allianz-Thread fehlt",
                                   f"Der Thread „{titel}“ existiert nicht. Der Bot legt ihn laut "
                                   f"Protokoll nicht selbst an und postet nichts in diesen Kanal.")

    # --------------------------------------------------------------- Praesenz
    def praesenzen_setzen(self, praesenzen: dict[int, dict]) -> None:
        self.praesenzen = praesenzen

    def aktiv(self, uid: int) -> bool:
        p = self.praesenzen.get(uid)
        return bool(p) and self.jetzt() - zeit(p["zeit"]) <= config.INAKTIV_NACH

    def whitelist(self) -> set[int]:
        """Allianzmitglieder mit aktiver Bot-Praesenz, ausser uns selbst."""
        return {uid for uid in self.praesenzen
                if uid != self.ich_id and uid in self.mitglieder and self.aktiv(uid)}

    def kann(self, uid: int, faehigkeit: str) -> bool:
        return self.aktiv(uid) and faehigkeit in self.praesenzen[uid]["faehigkeiten"]

    # ---------------------------------------------------------------- Senden
    def _pruefen(self, text: str, kanal: str, betreff: str | None, limit: int) -> None:
        if len(text) >= limit:
            raise NichtGesendet(f"Text zu lang ({len(text)} Zeichen)")
        if protokoll.verstoesst_gegen_linksperre(text) or (betreff and protokoll.verstoesst_gegen_linksperre(betreff)):
            raise NichtGesendet("Text verstoesst gegen die Linksperre")
        erg = protokoll.parse(text, kanal, self.ich_id, 0, self.jetzt(), betreff)
        if erg.nachricht is None:
            raise NichtGesendet(f"eigener Parser lehnt ab: {erg.grund}")

    def posten(self, kanal: str, typ: str, felder: dict, vorgang: str | None = None) -> int | None:
        thread_id = self.threads.get(kanal)
        if not thread_id:
            raise NichtGesendet(f"kein Thread fuer {kanal}")
        text = protokoll.bauen(typ, felder)
        self._pruefen(text, kanal, None, 4000)
        antwort = self.client.create_post(thread_id, text) or {}
        post_id = antwort.get("id") if isinstance(antwort, dict) else None
        if post_id is None:
            # Antwort ohne ID: den eigenen Beitrag im Thread wiederfinden.
            eigene = [b for b in self.client.get_thread_posts(thread_id)
                      if b.get("autor") == self.ich_name and b.get("body") == text]
            post_id = max((int(b["id"]) for b in eigene), default=None)
        if post_id is not None:
            self.state.data["eigene_beitraege"][str(post_id)] = {
                "kanal": kanal, "typ": typ, "vorgang": vorgang or felder.get("vorgang"),
                "gepostet": iso(self.jetzt())}
            # Den eigenen Beitrag nicht spaeter noch einmal als fremde Nachricht verarbeiten.
            self.state.data["verarbeitet"][f"f:{post_id}"] = iso(self.jetzt())
        else:
            log.error("Eigener Beitrag in %s ohne ID - wird nie automatisch geloescht", kanal)
        self.state.speichern()
        log.info("POST   %s in %s (#%s): %s", typ, kanal, post_id, text.replace("\n", " | "))
        if post_id is not None and typ in (*ERSETZBAR, *ABSCHLUSS):
            self._ueberholte_loeschen(vorgang or felder.get("vorgang"), post_id)
        return post_id

    def _ueberholte_loeschen(self, vorgang: str | None, neu_id: int) -> None:
        """Nach einer neuen [ANFRAGE]/[NOTRUF] die aelteren desselben Vorgangs
        loeschen; nach [ERLEDIGT]/[ENTWARNUNG] alle. Die Abschlussmeldung selbst
        raeumt AllianzBot.aufraeumen nach ABSCHLUSS_STEHEN_LASSEN weg."""
        if not vorgang:
            return
        for post_id, e in list(self.state.data["eigene_beitraege"].items()):
            if e.get("vorgang") == vorgang and e["typ"] in ERSETZBAR and int(post_id) != int(neu_id):
                try:
                    self.eigenen_beitrag_loeschen(int(post_id))
                except (ApiError, requests.RequestException) as ex:
                    log.error("Ueberholten Beitrag #%s nicht geloescht: %s", post_id, ex)

    def pn(self, empfaenger_id: int, typ: str, felder: dict) -> None:
        name = self.mitglieder.get(empfaenger_id)
        if not name:
            raise NichtGesendet(f"Empfaenger #{empfaenger_id} ist kein Allianzmitglied")
        betreff = f"[{typ}] {felder['vorgang']}"
        text = protokoll.bauen(typ, felder)
        self._pruefen(text, "pn", betreff, 8000)
        self.client.send_message(name, betreff, text)
        log.info("PN     %s an %s: %s", typ, name, text.replace("\n", " | "))

    # ------------------------------------------------------------ Loeschen
    def eigenen_beitrag_loeschen(self, post_id: int) -> bool:
        """Loescht einen Forenbeitrag - aber NUR, wenn er nachweislich vom Bot stammt.

        Drei Pruefungen, jede fuer sich ausreichend, um abzubrechen:
          1. Die ID steht in der Liste der Beitraege, die der Bot selbst gepostet hat.
          2. Der Beitrag ist live noch da und sein Autor ist unser Konto.
          3. Er ist nicht der erste Beitrag des Threads und nicht der einzige
             (sonst verschwaende der ganze Thread).
        """
        eintrag = self.state.data["eigene_beitraege"].get(str(post_id))
        if eintrag is None:
            log.error("LOESCHEN VERWEIGERT #%s: nicht vom Bot gepostet", post_id)
            self.einmal_melden(f"loeschen_verweigert:{post_id}", "Loeschen verweigert",
                               f"Beitrag #{post_id} stammt nicht vom Bot und bleibt stehen.")
            return False
        thread_id = self.threads.get(eintrag["kanal"])
        if not thread_id:
            return False
        beitraege = self.client.get_thread_posts(thread_id)
        ids = sorted(int(b["id"]) for b in beitraege)
        beitrag = next((b for b in beitraege if int(b["id"]) == int(post_id)), None)
        if beitrag is None:
            del self.state.data["eigene_beitraege"][str(post_id)]  # schon weg
            self.state.speichern()
            return True
        if beitrag.get("autor") != self.ich_name:
            log.error("LOESCHEN VERWEIGERT #%s: Autor ist %s", post_id, beitrag.get("autor"))
            self.einmal_melden(f"loeschen_verweigert:{post_id}", "Loeschen verweigert",
                               f"Beitrag #{post_id} ist von {beitrag.get('autor')} und bleibt stehen.")
            return False
        if len(ids) < 2 or int(post_id) == ids[0]:
            log.error("LOESCHEN VERWEIGERT #%s: erster/einziger Beitrag des Threads", post_id)
            return False
        self.client._beitrag_loeschen(int(post_id))
        del self.state.data["eigene_beitraege"][str(post_id)]
        self.state.speichern()
        log.info("GELOESCHT eigener Beitrag #%s (%s %s)", post_id, eintrag["typ"], eintrag.get("vorgang") or "")
        return True

    # ------------------------------------------------------------- Flotten
    def handel_schicken(self, von_insel_id: int, ziel: str, schiffe: dict, einheiten: dict) -> dict:
        """Truppen per Handel losschicken. Gibt die Flotte zurueck.

        POST /fleets ist nicht idempotent. Reisst die Verbindung ab, schauen
        wir in GET /fleets nach, ob die Flotte trotzdem losgefahren ist, statt
        es noch einmal zu versuchen.
        """
        x, y, z = koord_tupel(ziel)
        payload = {"origin_island_id": von_insel_id, "mission_type": "handel",
                   "target": {"x": x, "y": y, "z": z}, "ships": schiffe, "units": einheiten}
        vorher = {f.get("id") for f in self.client.get_fleets()}
        try:
            flotte = self.client.create_fleet(payload)
        except requests.RequestException as e:
            for f in self.client.get_fleets():
                t = f.get("target") or {}
                if (f.get("id") not in vorher and f.get("mission") in ("handel", "transport")
                        and (t.get("x"), t.get("y"), t.get("z")) == (x, y, z)):
                    log.warning("Antwort verloren, Flotte #%s ist aber unterwegs", f.get("id"))
                    return f
            raise NichtGesendet(f"Versand unklar/fehlgeschlagen: {e}") from e
        self._inseln_stand = None  # Bestand hat sich geaendert
        log.info("FLOTTE handel %s -> %s: %s, Schiffe %s, Ankunft %s", self.koord(von_insel_id), ziel,
                 einheiten, schiffe,
                 (flotte or {}).get("arrive_at"))
        return flotte or {}

    # --------------------------------------------------- Raid-Flotten rufen
    def transport_quellen(self, insel_id: int) -> list[dict]:
        """Flotten dieser Insel mit Kriegsschiffen, die bald wieder im Hafen sein koennen.

        Schiffe kehren immer zu ihrer Ausgangsinsel zurueck - deshalb zaehlen
        nur Flotten, die von `insel_id` losgefahren sind.

        - Raid-Flotte (mission attack) auf dem Hinweg, noch in Rueckruf-Reichweite:
          zurueckgerufen ist sie nach der bisher gefahrenen Zeit wieder daheim.
        - Flotte auf dem Rueckweg: kommt zu return_at ohnehin heim.
        Sortiert nach Heimkehr. Andere Missionen (Handel, Spionage) bleiben unberuehrt.
        """
        jetzt = self.jetzt()
        quellen = []
        for f in self.client.get_fleets():
            if f.get("origin_island_id") != insel_id:
                continue
            kap = truppen_kapazitaet(f.get("ships") or {})
            if kap <= 0:
                continue
            rueckruf_bis = zeit(f.get("recallable_until"))
            if (config.RUECKRUF_ERLAUBT and f.get("state") == "outbound" and f.get("mission") == "attack"
                    and rueckruf_bis and rueckruf_bis > jetzt + timedelta(seconds=10) and f.get("depart_at")):
                rueckkehr = jetzt + (jetzt - zeit(f["depart_at"]))
                rufen = True
            elif f.get("state") == "returning" and f.get("return_at"):
                rueckkehr, rufen = zeit(f["return_at"]), False
            else:
                continue
            quellen.append({"id": f.get("id"), "kap": kap, "rueckkehr": rueckkehr, "rufen": rufen,
                            "ziel": f.get("target") or {}})
        return sorted(quellen, key=lambda q: q["rueckkehr"])

    def schiffe_beschaffen(self, insel_id: int, benoetigt: int, bis: datetime, zweck: str,
                           ausfuehren: bool) -> tuple[int, datetime]:
        """Truppen-Kapazitaet auf `insel_id` fuer `benoetigt` Kaempfer bis spaetestens `bis`.

        Zaehlt erst den Hafen, dann Flotten nach Heimkehr. Mit `ausfuehren`
        werden die dafuer noetigen Raid-Flotten wirklich zurueckgerufen.
        Gibt (erreichbare Kapazitaet, Zeitpunkt, ab dem sie daheim ist) zurueck.
        """
        jetzt = self.jetzt()
        kap = truppen_kapazitaet(self.overview(insel_id, max_alter=0).get("schiffe") or {})
        bereit = jetzt
        if kap >= benoetigt:
            return benoetigt, bereit
        gewaehlt = []
        for q in self.transport_quellen(insel_id):
            if q["rueckkehr"] > bis:
                break
            gewaehlt.append(q)
            kap += q["kap"]
            bereit = max(bereit, q["rueckkehr"])
            if kap >= benoetigt:
                break
        if ausfuehren:
            for q in gewaehlt:
                if not q["rufen"]:
                    continue
                try:
                    self.client.recall_fleet(q["id"])
                except ApiError as e:
                    log.error("Rueckruf von Flotte #%s fehlgeschlagen: %s", q["id"], e)
                    kap -= q["kap"]
                    continue
                z = q["ziel"]
                log.warning("RUECKRUF  Raid-Flotte #%s (-> %s:%s:%s) fuer %s, daheim ca. %s", q["id"],
                            z.get("x"), z.get("y"), z.get("z"), zweck,
                            q["rueckkehr"].astimezone(self.tz).strftime("%H:%M:%S"))
            if gewaehlt:
                self.eilen_bis(bereit + timedelta(minutes=2))
        return min(kap, benoetigt), bereit

    def eilen_bis(self, bis: datetime) -> None:
        alt = zeit(self.state.data.get("eile_bis"))
        if alt is None or bis > alt:
            self.state.data["eile_bis"] = iso(bis)
            self.state.speichern()

    def eilig(self) -> bool:
        """Wartet der Bot gerade auf heimkehrende Schiffe? Dann kurzer Takt."""
        bis = zeit(self.state.data.get("eile_bis"))
        return bis is not None and self.jetzt() < bis

    # ---------------------------------------------------------- Meldungen
    def melden(self, titel: str, text: str) -> None:
        log.info("MELDUNG %s: %s", titel, text.replace("\n", " | "))
        if self.notifier is not None and self.notifier.aktiv:
            self.notifier.send(titel, text)

    def einmal_melden(self, schluessel: str, titel: str, text: str) -> None:
        gemeldet = self.state.data["gemeldet"]
        if schluessel in gemeldet:
            return
        gemeldet[schluessel] = iso(self.jetzt())
        self.state.speichern()
        self.melden(titel, text)
