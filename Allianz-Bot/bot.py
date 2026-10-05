"""Einstiegspunkt des Allianz-Bots: Takt, Lesen, Verteilen, Praesenz, Aufraeumen.

Pruefreihenfolge je eingehender Nachricht (agenten.md, Abschnitt 6):
  1. schon verarbeitet?          -> state["verarbeitet"]
  2./3./5. Parsen, Kanal, Felder -> protokoll.parse
  4. Absender aktuelles Mitglied -> Mitgliederliste, hoechstens 1 h alt
  6. Obergrenze des Vorgangs     -> in den Rollen
  7. Whitelist vor Versand       -> in der Helfer-Rolle

Start von Hand:   .venv/bin/python bot.py
Als Dienst:       systemctl start seekampf-allianz-bot
"""
from __future__ import annotations

import glob
import json
import os
import signal
import threading
import time
from collections import deque
from datetime import timedelta

import requests

import berichte
import config
import logger_setup
import protokoll
import steuerung
from anfrage import Anfrage
from kasse import Kasse
from api_client import ApiError, SeekampfClient
from helfer import Helfer
from kontext import ABSCHLUSS, ERSETZBAR, Kontext, NichtGesendet, iso, zeit
from notify import TelegramNotifier
from state import State
from verteidiger import Verteidiger

log = logger_setup.get_logger()

VERARBEITET_BEHALTEN = timedelta(days=14)
PN_WEITERLEITEN_MAX_ALTER = timedelta(days=1)


class AllianzBot:
    def __init__(self):
        self.state = State()
        self.client = SeekampfClient()
        notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        self.k = Kontext(self.client, self.state, notifier)
        self.verteidiger = Verteidiger(self.k)
        self.helfer = Helfer(self.k)
        self.anfrage = Anfrage(self.k)
        self.kasse = Kasse(self.k)
        self._faellig: dict[str, float] = {}
        self._frisch_gestartet = True
        self._stop = threading.Event()
        # Fuer den Seekampf-Hub: Ergebnisse der letzten Befehle und der letzte Fehler.
        self.befehl_ergebnisse: deque = deque(maxlen=20)
        self.letzter_fehler: str | None = None
        self._status_zuletzt: tuple[str, float] = ("", 0.0)

    # ------------------------------------------------------------------ Takt
    def _ist_faellig(self, name: str, intervall: float) -> bool:
        jetzt = self.k.jetzt().timestamp()
        if jetzt >= self._faellig.get(name, 0):
            self._faellig[name] = jetzt + intervall
            return True
        return False

    def _schritt(self, name: str, fn) -> None:
        """Ein Teilschritt; scheitert er, laufen die anderen trotzdem."""
        try:
            fn()
        except (ApiError, requests.RequestException, NichtGesendet) as e:
            log.error("%s fehlgeschlagen: %s", name, e)
            self.letzter_fehler = f"{name}: {e}"
        except Exception as e:  # noqa: BLE001 - der Bot darf an einem Fehler nie sterben
            log.exception("Unerwarteter Fehler in %s", name)
            self.letzter_fehler = f"{name}: {type(e).__name__}: {e}"

    def tick(self) -> None:
        k = self.k
        steuerung.laden()
        # Was in der Praesenz steht: gewuenscht plus das, was laufende Vorgaenge
        # noch brauchen. Aendert es sich, postet praesenz_pflegen neu.
        config.FAEHIGKEITEN = steuerung.wirksam(k.state.data)
        self._schritt("Befehle", self.befehle_lesen)
        if self._ist_faellig("mitglieder", config.MITGLIEDER_INTERVALL_S):
            self._schritt("Mitgliederliste", lambda: k.mitglieder_aktualisieren(erzwingen=True))
        if self._ist_faellig("forum", config.FORUM_INTERVALL_S):
            self._schritt("Forum", self.forum_lesen)
            self._schritt("Praesenz", self.praesenz_pflegen)
            self._schritt("Aufraeumen", self.aufraeumen)
            self._schritt("Kampfberichte", lambda: berichte.aktualisieren(k))
        if self._ist_faellig("pn", config.PN_INTERVALL_S):
            self._schritt("Postfach", self.postfach_lesen)
        if self._ist_faellig("verteidiger", config.INCOMING_INTERVALL_S):
            self._schritt("Verteidiger", self.verteidiger.tick)
        elif k.eilig():
            # Zurueckgerufene Schiffe sofort nutzen, bevor der Flotten-Manager sie greift.
            self._schritt("Rueckgabe", self.verteidiger._rueckgaben)
        self._schritt("Helfer", self.helfer.tick)
        if self._ist_faellig("anfrage", config.ANFRAGE_INTERVALL_S):
            self._schritt("Anfrage", self.anfrage.tick)
        if self._ist_faellig("kasse", config.KASSE_INTERVALL_S):
            self._schritt("Kasse", self.kasse.tick)
        # Faehigkeit im Seekampf-Hub umgeschaltet oder ein Vorgang ist zu Ende:
        # die Praesenz sofort anpassen, nicht erst beim naechsten Forum-Takt.
        config.FAEHIGKEITEN = steuerung.wirksam(k.state.data)
        eigene = k.praesenzen.get(k.ich_id)
        if eigene is not None and eigene["faehigkeiten"] != config.FAEHIGKEITEN:
            self._schritt("Praesenz", self.praesenz_pflegen)
        self._schritt("Status", self.status_schreiben)

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        while not self._stop.is_set():
            try:
                self.k.start()
                break
            except Exception as e:  # noqa: BLE001
                log.error("Start fehlgeschlagen, neuer Versuch in 60 s: %s", e)
                self._stop.wait(60)
        if self._stop.is_set():
            return
        self.k.melden("Allianz-Bot gestartet",
                      f"{config.BOT_NAME} fuer {self.k.ich_name}, Faehigkeiten: "
                      f"{' '.join(config.FAEHIGKEITEN)}.")
        while not self._stop.is_set():
            self.tick()
            # In kleinen Schritten warten: ein Befehl aus dem Seekampf-Hub weckt
            # den Bot sofort, statt bis zum naechsten Takt zu liegen.
            ende = time.time() + (config.EIL_TICK_S if self.k.eilig() else config.TICK_S)
            while not self._stop.is_set() and time.time() < ende and not self._befehl_wartet():
                self._stop.wait(1)
        self.state.speichern()
        log.info("Allianz-Bot beendet")

    @staticmethod
    def _befehle_ordner() -> str:
        return os.path.join(config.BASE_DIR, "data", "befehle")

    def _befehl_wartet(self) -> bool:
        return bool(glob.glob(os.path.join(self._befehle_ordner(), "*.json")))

    def _ergebnis(self, befehl: dict, ok: bool, text: str) -> None:
        self.befehl_ergebnisse.appendleft({"id": befehl.get("id"), "typ": befehl.get("typ"),
                                           "ok": ok, "text": text, "zeit": time.time()})

    def befehle_lesen(self) -> None:
        """Auftraege von Hand aus data/befehle/ (Seekampf-Hub oder anfrage_stellen.py)."""
        for pfad in sorted(glob.glob(os.path.join(self._befehle_ordner(), "*.json"))):
            try:
                with open(pfad, encoding="utf-8") as f:
                    befehl = json.load(f)
            except (OSError, ValueError):
                befehl = {}
            finally:
                os.remove(pfad)  # nie zweimal ausfuehren, auch wenn er scheitert
            log.info("BEFEHL %s", befehl)
            try:
                self._befehl_ausfuehren(befehl)
            except (ApiError, requests.RequestException, NichtGesendet, KeyError, ValueError) as e:
                log.error("Befehl %s fehlgeschlagen: %s", befehl, e)
                self._ergebnis(befehl, False, str(e))

    def _befehl_ausfuehren(self, befehl: dict) -> None:
        typ = befehl.get("typ")
        if typ == "anfrage":
            if not steuerung.will("anfrage"):
                self._ergebnis(befehl, False, "Faehigkeit Rohstoff-Anfrage ist abgeschaltet")
                return
            insel_id = self.k.insel_zu_koord(befehl["insel"]) if befehl.get("insel") else None
            if befehl.get("insel") and insel_id is None:
                self.k.melden("Befehl verworfen", f"Keine eigene Insel bei {befehl['insel']}.")
                self._ergebnis(befehl, False, f"Keine eigene Insel bei {befehl['insel']}")
                return
            rohstoffe = {r: int(n) for r, n in befehl["rohstoffe"].items() if int(n) > 0}
            if not rohstoffe:
                self._ergebnis(befehl, False, "Keine Menge angegeben")
                return
            self.anfrage.manuell(rohstoffe, insel_id)
            ziel = self.k.name(insel_id or self.k.heimat_id)
            self._ergebnis(befehl, True, f"Anfrage fuer {ziel} gepostet: "
                                         + ", ".join(f"{n} {r}" for r, n in rohstoffe.items()))
        elif typ == "anfrage_beenden":
            a = self.anfrage.anfragen.get(str(befehl.get("insel_id")))
            if a is None:
                self._ergebnis(befehl, False, "Auf dieser Insel laeuft keine Anfrage")
                return
            self.anfrage.beenden(a)
            self._ergebnis(befehl, True, f"Anfrage {a['vorgang']} beendet ([ERLEDIGT] gepostet)")
        else:
            self._ergebnis(befehl, False, f"Unbekannter Befehl: {typ}")

    # ------------------------------------------------------------- Seekampf-Hub
    def status_schreiben(self) -> None:
        """data/status.json fuer den Seekampf-Hub. Geschrieben wird nur, wenn sich
        etwas geaendert hat, spaetestens aber jede Minute (Lebenszeichen) -
        das schont die SD-Karte."""
        k, d = self.k, self.state.data
        inseln = []
        for iid, ov in (k._inseln or {}).items():
            inseln.append({"id": iid, "name": ov.get("name"), "koordinaten": ov.get("koordinaten"),
                           "rohstoffe": ov.get("rohstoffe") or {},
                           "truppen": ov.get("truppen") or {},
                           "bedrohung": bool(ov.get("bedrohung_im_anflug"))})
        mitglieder = []
        for uid, name in sorted(k.mitglieder.items(), key=lambda x: x[1].lower()):
            p = k.praesenzen.get(uid)
            mitglieder.append({"id": uid, "name": name, "ich": uid == k.ich_id,
                               "aktiv": k.aktiv(uid) if p else False,
                               "praesenz": {"faehigkeiten": p["faehigkeiten"], "bot": p.get("bot"),
                                            "zeit": p["zeit"]} if p else None})
        inhalt = {
            "ich": {"id": k.ich_id, "name": k.ich_name}, "bot_name": config.BOT_NAME,
            "heimat_id": k.heimat_id,
            "faehigkeiten": {"gewuenscht": steuerung.gewuenscht(), "wirksam": list(config.FAEHIGKEITEN),
                             "gebunden": steuerung.gebunden(d),
                             "gemeldet": (k.praesenzen.get(k.ich_id) or {}).get("faehigkeiten")},
            "strategie": {name: getattr(config, name) for name in steuerung.STRATEGIE},
            "strategie_standard": steuerung.STANDARD,
            "inseln": inseln,
            "notrufe": list(d["notrufe"].values()), "notrufe_alt": d["notrufe_alt"][-10:],
            "anfragen": list(d["anfragen"].values()), "anfragen_alt": d["anfragen_alt"][-10:],
            "schulden": d["schulden"][-30:], "verliehen": d["verliehen"][-30:],
            "hilfe": [{"vorgang": v, "eigentuemer_name": k.mitglieder.get(h["eigentuemer"]), **h}
                      for v, h in d["hilfe"].items()],
            "mitglieder": mitglieder,
            "befehle": list(self.befehl_ergebnisse),
            "kasse": {"einstellung": steuerung.kasse_einstellung(), "hinweis": self.kasse.hinweis,
                      "stand": d.get("kasse") or {}},
            "fehler": self.letzter_fehler,
        }
        text = json.dumps(inhalt, ensure_ascii=False, sort_keys=True, default=str)
        jetzt = time.time()
        if text == self._status_zuletzt[0] and jetzt - self._status_zuletzt[1] < 60:
            return
        inhalt["zeit"] = jetzt
        steuerung.status_schreiben(json.loads(json.dumps(inhalt, default=str)))
        self._status_zuletzt = (text, jetzt)

    # ----------------------------------------------------------------- Lesen
    def _neu(self, schluessel: str) -> bool:
        return schluessel not in self.state.data["verarbeitet"]

    def _erledigt(self, schluessel: str) -> None:
        self.state.data["verarbeitet"][schluessel] = iso(self.k.jetzt())

    def forum_lesen(self) -> None:
        k = self.k
        if not k.mitglieder_frisch():
            return
        if not all(k.threads.get(kanal) for kanal in config.THREADS):
            k.threads_finden()
        for kanal in config.THREADS:
            thread_id = k.threads.get(kanal)
            if not thread_id:
                continue
            beitraege = sorted(k.client.get_thread_posts(thread_id), key=lambda b: int(b["id"]))
            if kanal == "forum:teilnehmer":
                self._praesenzen_aus(beitraege)
            for b in beitraege:
                schluessel = f"f:{b['id']}"
                if not self._neu(schluessel):
                    continue
                absender = k.id_zu_name(b.get("autor") or "")
                erg = protokoll.parse(b.get("body") or "", kanal, absender or 0, int(b["id"]),
                                      b["created_at"])
                self._erledigt(schluessel)
                if erg.nachricht is None or absender is None or absender == k.ich_id:
                    continue
                self._verteilen(erg.nachricht, kanal, absender, zeit(b["created_at"]), int(b["id"]))
        self._verarbeitet_kuerzen()
        self.state.speichern()

    def _praesenzen_aus(self, beitraege: list) -> None:
        k = self.k
        praesenzen: dict[int, dict] = {}
        for b in beitraege:
            uid = k.id_zu_name(b.get("autor") or "")
            if uid is None:
                continue
            erg = protokoll.parse(b.get("body") or "", "forum:teilnehmer", uid, int(b["id"]),
                                  b["created_at"])
            if erg.nachricht is None:
                continue
            f = erg.nachricht["felder"]
            alt = praesenzen.get(uid)
            if alt is None or int(b["id"]) > alt["post_id"]:
                praesenzen[uid] = {"post_id": int(b["id"]), "zeit": b["created_at"],
                                   "faehigkeiten": f["faehigkeiten"], "bot": f.get("bot"),
                                   "nebenversion": f["nebenversion"]}
        k.praesenzen_setzen(praesenzen)

    def postfach_lesen(self) -> None:
        k = self.k
        if not k.mitglieder_frisch():
            return
        for m in sorted(k.client.list_messages("inbox", limit=50), key=lambda m: int(m["id"])):
            if not m.get("sender_id"):
                continue  # Systemnachricht
            schluessel = f"p:{m['id']}"
            if not self._neu(schluessel):
                continue
            absender = int(m["sender_id"])
            erg = protokoll.parse(m.get("body") or "", "pn", absender, int(m["id"]),
                                  m["created_at"], m.get("subject"))
            self._erledigt(schluessel)
            self._pn_weiterleiten(m, erg, absender)
            if erg.nachricht is None or absender not in k.mitglieder or absender == k.ich_id:
                continue
            self._verteilen(erg.nachricht, "pn", absender, zeit(m["created_at"]), int(m["id"]))
        self.state.speichern()

    def _pn_weiterleiten(self, m: dict, erg, absender: int) -> None:
        """PN eines Mitspielers per Telegram weiterleiten - nur, was ein Mensch
        geschrieben hat: keine Protokoll-Nachrichten (auch keine ungueltigen
        mit Protokoll-Kopf), keine Systemnachrichten, nichts von uns selbst.
        Aeltere PNs (z. B. nach dem Kuerzen der verarbeiteten IDs wieder als
        neu erkannt) werden nicht nachgeschickt."""
        if not config.PN_WEITERLEITEN or erg.nachricht is not None or absender == self.k.ich_id:
            return
        if erg.grund not in ("kein_kopf", "unbekannter_typ"):
            return  # Protokoll-Kopf, nur ungueltig/andere Version - kein Mensch
        if self.k.jetzt() - zeit(m["created_at"]) > PN_WEITERLEITEN_MAX_ALTER:
            return
        betreff = (m.get("subject") or "").strip() or "(ohne Betreff)"
        text = (m.get("body") or "").strip()
        if len(text) > 3000:
            text = text[:3000] + " …"
        self.k.melden(f"PN von {m.get('sender') or absender}: {betreff}", text or "(leer)")

    def _verteilen(self, n: dict, kanal: str, absender: int, zeitpunkt, msg_id: int) -> None:
        typ, f = n["typ"], n["felder"]
        vorgang = f.get("vorgang")
        mein_vorgang = bool(vorgang) and protokoll.eigentuemer(vorgang) == self.k.ich_id
        log.info("EINGANG %s von %s via %s: %s", typ, self.k.mitglieder.get(absender), kanal, f)
        if typ == "NOTRUF" and not mein_vorgang:
            self.helfer.notruf(f, absender, zeitpunkt)
        elif typ == "ENTWARNUNG" and not mein_vorgang:
            self.helfer.entwarnung(f, absender, zeitpunkt)
        elif typ == "ZUSAGE" and not mein_vorgang:
            self.helfer.zusage(f, absender, zeitpunkt)
        elif typ == "RUECKGABE" and not mein_vorgang:
            self.helfer.rueckgabe(f, absender, zeitpunkt)
        elif typ == "ABSAGE" and not mein_vorgang:
            self.helfer.absage(f, absender)
        elif mein_vorgang and typ in ("ANGEBOT", "ABSAGE", "VERSANDT"):
            rolle = (self.verteidiger if self.verteidiger.besitzt(vorgang)
                     else self.anfrage if self.anfrage.besitzt(vorgang) else None)
            if rolle is None:
                return
            if typ == "ANGEBOT":
                rolle.angebot(f, absender, zeitpunkt, msg_id)
            elif typ == "ABSAGE":
                rolle.absage(f, absender)
            else:
                rolle.versandt(f, absender, zeitpunkt)
        # ANFRAGE/ERLEDIGT anderer: keine Faehigkeit rohstoffhilfe -> nur mitlesen.

    def _verarbeitet_kuerzen(self) -> None:
        grenze = self.k.jetzt() - VERARBEITET_BEHALTEN
        v = self.state.data["verarbeitet"]
        for schluessel in [s for s, t in v.items() if zeit(t) < grenze]:
            del v[schluessel]

    # -------------------------------------------------------------- Praesenz
    def praesenz_pflegen(self) -> None:
        k = self.k
        if not k.threads.get("forum:teilnehmer"):
            return
        jetzt = k.jetzt()
        eigene = k.praesenzen.get(k.ich_id)
        aktuell = (eigene is not None and eigene["faehigkeiten"] == config.FAEHIGKEITEN
                   and eigene["bot"] == config.BOT_NAME and eigene["nebenversion"] == config.NEBENVERSION
                   and str(eigene["post_id"]) in k.state.data["eigene_beitraege"])
        faellig = eigene is None or jetzt - zeit(eigene["zeit"]) >= config.PRAESENZ_INTERVALL
        # "Beim Start" posten - aber nicht bei jedem Neustart binnen einer Stunde erneut.
        start = self._frisch_gestartet and (eigene is None or jetzt - zeit(eigene["zeit"]) > timedelta(hours=1))
        self._frisch_gestartet = False
        if aktuell and not faellig and not start:
            return
        neu = k.posten("forum:teilnehmer", "PRAESENZ", {
            "version": protokoll.HAUPTVERSION, "nebenversion": config.NEBENVERSION,
            "faehigkeiten": config.FAEHIGKEITEN, "bot": config.BOT_NAME})
        if neu is None:
            return
        k.praesenzen[k.ich_id] = {"post_id": neu, "zeit": iso(jetzt), "faehigkeiten": config.FAEHIGKEITEN,
                                  "bot": config.BOT_NAME, "nebenversion": config.NEBENVERSION}
        for post_id, e in list(k.state.data["eigene_beitraege"].items()):
            if e["typ"] == "PRAESENZ" and int(post_id) != int(neu):
                self._schritt("Alte Praesenz loeschen", lambda p=post_id: k.eigenen_beitrag_loeschen(int(p)))

    # ------------------------------------------------------------ Aufraeumen
    def aufraeumen(self) -> None:
        """Eigene Protokoll-Beitraege loeschen, die nicht mehr gelten:
          - ueberholte [ANFRAGE]/[NOTRUF]: ein neuerer desselben Vorgangs steht,
          - abgeschlossene Vorgaenge: alles sofort, die Abschlussmeldung
            ([ERLEDIGT]/[ENTWARNUNG]) nach ABSCHLUSS_STEHEN_LASSEN,
          - sonst nach der Obergrenze, spaetestens nach 7 Tagen."""
        k = self.k
        jetzt = k.jetzt()
        aktiv = {x["vorgang"] for x in [*k.state.data["notrufe"].values(), *k.state.data["anfragen"].values()]}
        eigene = k.state.data["eigene_beitraege"]
        neueste: dict[str, int] = {}
        abgeschlossen: dict[str, object] = {}
        for post_id, e in eigene.items():
            vorgang = e.get("vorgang")
            if e["typ"] in ERSETZBAR:
                neueste[vorgang] = max(neueste.get(vorgang, 0), int(post_id))
            elif e["typ"] in ABSCHLUSS and vorgang not in aktiv:
                gepostet = zeit(e["gepostet"])
                abgeschlossen[vorgang] = min(abgeschlossen.get(vorgang, gepostet), gepostet)
        for post_id, e in list(eigene.items()):
            if e["typ"] == "PRAESENZ":
                continue
            vorgang = e.get("vorgang")
            obergrenze = (k.state.data["eigene_vorgaenge"].get(vorgang) or {}).get("obergrenze")
            zu_alt = jetzt >= zeit(e["gepostet"]) + config.AUFRAEUMEN_SPAETESTENS
            abgelaufen = obergrenze is not None and jetzt >= zeit(obergrenze) and vorgang not in aktiv
            ueberholt = e["typ"] in ERSETZBAR and int(post_id) < neueste[vorgang]
            erledigt = vorgang in abgeschlossen and (
                e["typ"] not in ABSCHLUSS or jetzt >= abgeschlossen[vorgang] + config.ABSCHLUSS_STEHEN_LASSEN)
            if zu_alt or abgelaufen or ueberholt or erledigt:
                self._schritt("Beitrag loeschen", lambda p=post_id: k.eigenen_beitrag_loeschen(int(p)))
        benutzt = {e.get("vorgang") for e in k.state.data["eigene_beitraege"].values()} | aktiv
        for vorgang in list(k.state.data["eigene_vorgaenge"]):
            if vorgang not in benutzt:
                del k.state.data["eigene_vorgaenge"][vorgang]
        k.state.speichern()


if __name__ == "__main__":
    AllianzBot().run()
