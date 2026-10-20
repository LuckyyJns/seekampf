"""Spieler-Tracker: andere Spieler Tag fuer Tag beobachten.

Einmal taeglich um STAND_STUNDE Uhr (und beim allerersten Start sofort, als
Ausgangsstand) haelt der Hub fest:

  - die Rangliste (GET /rankings/players): Rang, Punkte, Inselzahl, Allianz,
  - je Spieler das Profil (GET /users/{id}/profile): jede Insel mit Koordinate,
    Name und Punkten, dazu die erfuellten Quests,
  - die Allianz-Rangliste (GET /rankings/alliances),
  - die Server-Statistik (GET /stats/overview, im Spiel "Statistiken"):
    Schiffe und Truppen weltweit (Haefen/Garnisonen + auf See), Spieler,
    Inseln, Flotten unterwegs.

Aus zwei aufeinanderfolgenden Staenden entsteht ein "Tag": wer welche Inseln
dazubekommen oder verloren hat (bei einem Besitzerwechsel unter Spielern:
von wem / an wen), Punkte, Rang, Allianzwechsel, neue Quests; weltweit, wie
viele Schiffe und Truppen dazugekommen sind. Schiffe und Truppen gibt die
API nur als Summe aller Spieler heraus, nicht je Spieler.

Die Staende liegen in data/spieler.json (BEHALTEN_TAGE Tage).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from karte import API, DATA_DIR, _api_key

log = logging.getLogger("seekampf_hub")

PFAD = os.path.join(DATA_DIR, "spieler.json")
TZ = ZoneInfo("Europe/Berlin")
STAND_STUNDE = 1        # taeglich um 1:00 Uhr
BEHALTEN_TAGE = 365
PAUSE_S = 0.1           # Abstand zwischen zwei Profilabfragen


def _lesen(pfad: str = PFAD) -> dict:
    try:
        with open(pfad, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("staende"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"staende": []}


def _schreiben(daten: dict, pfad: str = PFAD) -> None:
    ordner = os.path.dirname(pfad)
    os.makedirs(ordner, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ordner, prefix=".spieler-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, pfad)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _summe(*teile: dict) -> dict[str, int]:
    aus: dict[str, int] = {}
    for t in teile:
        for k, n in (t or {}).items():
            aus[k] = aus.get(k, 0) + int(n or 0)
    return aus


def _allianz(a: dict | None) -> dict | None:
    return {"id": a.get("id"), "tag": a.get("tag"), "name": a.get("name")} if a else None


class SpielerTracker:
    def __init__(self, pfad: str = PFAD):
        self.pfad = pfad
        self.client = httpx.Client(base_url=API, headers={"X-API-Key": _api_key()}, timeout=20.0)
        self.lock = threading.Lock()
        self.laeuft = False
        self.letzter_fehler: str | None = None
        self._cache: tuple = (None, None)        # (mtime der Datei, gelesene Daten)
        self._auswertung: tuple = (None, None)   # ((mtime, tage), Ergebnis)

    def staende(self) -> list[dict]:
        """Die gespeicherten Staende - nur neu gelesen, wenn die Datei sich geaendert hat."""
        try:
            mtime = os.stat(self.pfad).st_mtime_ns
        except OSError:
            return []
        if self._cache[0] != mtime:
            self._cache = (mtime, _lesen(self.pfad))
        return self._cache[1]["staende"]

    # ------------------------------------------------------------ Abholen
    def _get(self, pfad: str, **params):
        r = self.client.get(pfad, params=params or None)
        r.raise_for_status()
        return r.json()

    def stand_holen(self) -> dict:
        """Einen vollstaendigen Stand von der API holen (wirft bei Fehlern)."""
        rangliste = self._get("/rankings/players", limit=200)
        spieler = {}
        for eintrag in rangliste:
            uid = str(eintrag["user_id"])
            try:
                profil = self._get(f"/users/{uid}/profile")
            except (httpx.HTTPError, ValueError) as e:
                log.warning("Spieler-Tracker: Profil von %s nicht abrufbar: %s", eintrag.get("name"), e)
                profil = None
            spieler[uid] = {
                "name": eintrag.get("name"), "rang": eintrag.get("rang"), "punkte": eintrag.get("punkte"),
                "inseln_anzahl": eintrag.get("inseln"), "allianz": _allianz(eintrag.get("allianz")),
                # None = Profil fehlt in diesem Stand (dann keine Inselvergleiche fuer ihn)
                "inseln": None if profil is None else {
                    i["koordinaten"]: {"name": i.get("name"), "punkte": i.get("punkte")}
                    for i in profil.get("inseln") or [] if i.get("koordinaten")},
                "quests": None if profil is None else sorted(
                    q.get("name") or q.get("id") for q in profil.get("quests_erfuellt") or []),
            }
            time.sleep(PAUSE_S)
        welt = self._get("/stats/overview")
        allianzen = self._get("/rankings/alliances", limit=200)
        return {
            "zeit": time.time(),
            "welt": {k: welt.get(k) for k in ("spieler_anzahl", "inseln_anzahl", "allianzen_anzahl",
                                               "flotten_unterwegs", "schiffe_gesamt", "schiffe_auf_see",
                                               "einheiten_gesamt", "einheiten_auf_see")},
            "allianzen": [{"id": a.get("allianz_id"), "rang": a.get("rang"), "tag": a.get("tag"),
                           "name": a.get("name"), "mitglieder": a.get("mitglieder"), "punkte": a.get("punkte")}
                          for a in allianzen],
            "spieler": spieler,
        }

    def stand_starten(self, grund: str) -> bool:
        with self.lock:
            if self.laeuft:
                return False
            self.laeuft = True
        threading.Thread(target=self._stand, args=(grund,), name="spieler-stand", daemon=True).start()
        return True

    def _stand(self, grund: str) -> None:
        start = time.time()
        try:
            stand = self.stand_holen()
            stand["grund"] = grund
            daten = _lesen(self.pfad)
            daten["staende"].append(stand)
            grenze = time.time() - BEHALTEN_TAGE * 86400
            daten["staende"] = [s for s in daten["staende"] if s.get("zeit", 0) >= grenze]
            _schreiben(daten, self.pfad)
            self.letzter_fehler = None
            log.info("Spieler-Tracker: Stand von %d Spielern gespeichert (%s, %.0f s)",
                     len(stand["spieler"]), grund, time.time() - start)
        except Exception as e:  # noqa: BLE001 - der Hub darf daran nicht sterben
            self.letzter_fehler = str(e)
            log.exception("Spieler-Tracker: Stand fehlgeschlagen")
        finally:
            self.laeuft = False

    def planer(self, stop: threading.Event) -> None:
        """Taeglich um STAND_STUNDE; ohne gespeicherten Stand sofort (Ausgangsstand).
        War der Hub um 1:00 aus, wird der Stand beim naechsten Mal nachgeholt."""
        if not self.staende():
            self.stand_starten("erster Start")
        while not stop.wait(60):
            jetzt = datetime.now(TZ)
            staende = self.staende()
            letzter = datetime.fromtimestamp(staende[-1]["zeit"] if staende else 0, TZ)
            faellig = jetzt.replace(hour=STAND_STUNDE, minute=0, second=0, microsecond=0)
            if jetzt >= faellig and letzter < faellig:
                self.stand_starten(f"taeglich {STAND_STUNDE}:00 Uhr")

    # ---------------------------------------------------------- Auswerten
    def daten(self, tage: int = 30) -> dict:
        staende = self.staende()
        schluessel = (self._cache[0], tage)
        if self._auswertung[0] != schluessel:
            self._auswertung = (schluessel, auswerten(staende, tage))
        return {
            **self._auswertung[1],
            "laeuft": self.laeuft,
            "letzter_fehler": self.letzter_fehler,
            "stunde": STAND_STUNDE,
        }


def _tag(a: dict, b: dict) -> dict:
    """Vergleich zweier Staende: was zwischen a (vorher) und b (nachher) passiert ist."""
    sa, sb = a.get("spieler") or {}, b.get("spieler") or {}
    # Wem gehoerte eine Insel vorher/nachher (nur aus vollstaendigen Profilen)?
    besitz_a = {k: uid for uid, s in sa.items() for k in (s.get("inseln") or {})}
    besitz_b = {k: uid for uid, s in sb.items() for k in (s.get("inseln") or {})}
    name = lambda uid: (sb.get(uid) or sa.get(uid) or {}).get("name")  # noqa: E731

    spieler = {}
    for uid in set(sa) | set(sb):
        x, y = sa.get(uid), sb.get(uid)
        if y is None:
            spieler[uid] = {"weg": True}
            continue
        e: dict = {}
        if x is None:
            e["neu"] = True
            x = {}
        for feld in ("punkte", "inseln_anzahl"):
            if x.get(feld) is not None and y.get(feld) is not None:
                e[f"{feld}_diff"] = int(y[feld]) - int(x[feld])
        if x.get("rang") is not None and y.get("rang") is not None:
            e["rang_diff"] = int(x["rang"]) - int(y["rang"])     # positiv = aufgestiegen
        if x and (x.get("allianz") or {}).get("id") != (y.get("allianz") or {}).get("id"):
            e["allianz_vorher"] = x.get("allianz")
        ia, ib = x.get("inseln"), y.get("inseln")
        if ia is not None and ib is not None:
            dazu = []
            for k in sorted(set(ib) - set(ia)):
                von = besitz_a.get(k)
                dazu.append({"k": k, "name": ib[k].get("name"),
                             "von": name(von) if von and von != uid else None})
            weg = []
            for k in sorted(set(ia) - set(ib)):
                an = besitz_b.get(k)
                weg.append({"k": k, "name": ia[k].get("name"), "an": name(an) if an and an != uid else None})
            gewachsen = sorted(
                ({"k": k, "name": ib[k].get("name"), "diff": int(ib[k].get("punkte") or 0) - int(ia[k].get("punkte") or 0)}
                 for k in set(ia) & set(ib)
                 if int(ib[k].get("punkte") or 0) != int(ia[k].get("punkte") or 0)),
                key=lambda g: -abs(g["diff"]))
            if dazu:
                e["inseln_dazu"] = dazu
            if weg:
                e["inseln_weg"] = weg
            if gewachsen:
                e["inseln_punkte"] = gewachsen[:10]
                e["inseln_gewachsen_anzahl"] = len(gewachsen)
        qa, qb = x.get("quests"), y.get("quests")
        if qa is not None and qb is not None and set(qb) - set(qa):
            e["quests_neu"] = sorted(set(qb) - set(qa))
        spieler[uid] = e

    wa, wb = a.get("welt") or {}, b.get("welt") or {}
    schiffe_a = _summe(wa.get("schiffe_gesamt"), wa.get("schiffe_auf_see"))
    schiffe_b = _summe(wb.get("schiffe_gesamt"), wb.get("schiffe_auf_see"))
    truppen_a = _summe(wa.get("einheiten_gesamt"), wa.get("einheiten_auf_see"))
    truppen_b = _summe(wb.get("einheiten_gesamt"), wb.get("einheiten_auf_see"))
    welt = {
        "schiffe": {t: schiffe_b.get(t, 0) - schiffe_a.get(t, 0) for t in sorted(set(schiffe_a) | set(schiffe_b))},
        "truppen": {t: truppen_b.get(t, 0) - truppen_a.get(t, 0) for t in sorted(set(truppen_a) | set(truppen_b))},
    }
    for k in ("spieler_anzahl", "inseln_anzahl", "allianzen_anzahl", "flotten_unterwegs"):
        if wa.get(k) is not None and wb.get(k) is not None:
            welt[k] = int(wb[k]) - int(wa[k])
    pa = {al["id"]: al for al in a.get("allianzen") or []}
    allianzen = {}
    for al in b.get("allianzen") or []:
        v = pa.get(al["id"])
        allianzen[str(al["id"])] = {
            "punkte_diff": int(al.get("punkte") or 0) - int(v.get("punkte") or 0) if v else None,
            "mitglieder_diff": int(al.get("mitglieder") or 0) - int(v.get("mitglieder") or 0) if v else None,
            "rang_diff": int(v["rang"]) - int(al["rang"]) if v and v.get("rang") and al.get("rang") else None,
        }
    return {"von": a.get("zeit"), "bis": b.get("zeit"), "welt": welt, "spieler": spieler, "allianzen": allianzen}


def auswerten(staende: list[dict], tage: int = 30) -> dict:
    """Aktueller Stand (Rangliste, Welt, Allianzen) und die letzten `tage`
    Vergleiche, neuester zuerst."""
    staende = sorted((s for s in staende if s.get("zeit")), key=lambda s: s["zeit"])
    if not staende:
        return {"stand": None, "tage": [], "staende": 0}
    letzter = staende[-1]
    spieler = []
    for uid, s in (letzter.get("spieler") or {}).items():
        spieler.append({"id": uid, "name": s.get("name"), "rang": s.get("rang"), "punkte": s.get("punkte"),
                        "inseln_anzahl": s.get("inseln_anzahl"), "allianz": s.get("allianz"),
                        "quests": s.get("quests")})
    spieler.sort(key=lambda s: (s["rang"] is None, s["rang"] or 0))
    vergleiche = [_tag(a, b) for a, b in zip(staende[:-1], staende[1:])][-tage:]
    vergleiche.reverse()
    return {
        "stand": {"zeit": letzter["zeit"], "welt": letzter.get("welt"), "allianzen": letzter.get("allianzen"),
                  "spieler": spieler},
        "tage": vergleiche,
        "staende": len(staende),
        "erster": staende[0]["zeit"],
    }
