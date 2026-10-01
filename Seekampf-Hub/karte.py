"""Ozeankarte: die ganze Welt scannen, speichern, mit dem vorigen Scan vergleichen.

Das Spiel zeigt nur 3x3 Sektoren um die eigene Insel. Die API liefert ueber
GET /map/region/{x}/{y} aber jeden 3x3-Ausschnitt der Welt; die Welt ist
100x100 Sektoren gross (1..100), also genuegen Mittelpunkte 2, 5, 8 ... 98
und 100 je Achse - 34x34 = 1156 Abfragen, gemessen rund eine Minute.

Gescannt wird einmal taeglich um SCAN_STUNDE Uhr und von Hand ueber den Hub.
Ergebnis in data/karte.json; der vorige Stand wandert nach
data/karte_vorher.json, daraus entstehen die "Aenderungen seit dem letzten
Scan" (neu besiedelt, Besitzerwechsel, neue Ruinen).
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

log = logging.getLogger("seekampf_hub")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
KARTE_PATH = os.path.join(DATA_DIR, "karte.json")
VORHER_PATH = os.path.join(DATA_DIR, "karte_vorher.json")
API = "https://seekampf.de/api/v1"
TZ = ZoneInfo("Europe/Berlin")

WELT_MIN, WELT_MAX = 1, 100     # Sektorkoordinaten der Welt (gemessen 30.09.2026)
SCAN_STUNDE = 1                 # taeglicher Scan um 1:00 Uhr
PAUSE_S = 0.03                  # Abstand zwischen zwei Abfragen - die API nicht hetzen


def _api_key() -> str:
    """SEEKAMPF_API_KEY aus der Umgebung oder der .env des Hubs."""
    if os.environ.get("SEEKAMPF_API_KEY"):
        return os.environ["SEEKAMPF_API_KEY"]
    try:
        with open(os.path.join(BASE_DIR, ".env"), encoding="utf-8") as f:
            for zeile in f:
                if zeile.startswith("SEEKAMPF_API_KEY="):
                    return zeile.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _lesen(pfad: str) -> dict | None:
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _schreiben(pfad: str, daten: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".karte-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        os.replace(tmp, pfad)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class Karte:
    def __init__(self):
        self.client = httpx.Client(base_url=API, headers={"X-API-Key": _api_key()}, timeout=20.0)
        self.lock = threading.Lock()
        self.laeuft = False
        self.fortschritt = 0.0
        self.letzter_fehler: str | None = None
        self._ich: dict | None = None
        self._ich_zeit = 0.0
        self._angriffe: list = []
        self._angriffe_zeit = 0.0

    # ------------------------------------------------------------------ Scan
    def scan_starten(self, grund: str) -> bool:
        """Scan im Hintergrund starten. False, wenn schon einer laeuft."""
        with self.lock:
            if self.laeuft:
                return False
            self.laeuft = True
            self.fortschritt = 0.0
        threading.Thread(target=self._scan, args=(grund,), name="karten-scan", daemon=True).start()
        return True

    def _scan(self, grund: str) -> None:
        start = time.time()
        log.info("Kartenscan gestartet (%s)", grund)
        inseln: dict[str, dict] = {}
        # Jeder Mittelpunkt deckt sich selbst +/- 1 Sektor ab; der letzte darf
        # nicht ueber den Rand ragen (sonst 422), deshalb WELT_MAX dazu.
        zentren = list(range(WELT_MIN + 1, WELT_MAX, 3))
        if zentren[-1] + 1 < WELT_MAX:
            zentren.append(WELT_MAX)
        gesamt, fertig, fehler = len(zentren) ** 2, 0, 0
        try:
            for cy in zentren:
                for cx in zentren:
                    try:
                        r = self.client.get(f"/map/region/{cx}/{cy}")
                        r.raise_for_status()
                        for zeile in r.json().get("sektoren") or []:
                            for sektor in zeile or []:
                                for insel in (sektor or {}).get("inseln") or []:
                                    if insel:
                                        inseln[insel["koordinaten"]] = insel
                    except (httpx.HTTPError, ValueError) as e:
                        fehler += 1
                        if fehler > 20:
                            raise RuntimeError(f"zu viele Fehler beim Scannen, zuletzt: {e}") from e
                    fertig += 1
                    self.fortschritt = fertig / gesamt
                    time.sleep(PAUSE_S)
            alt = _lesen(KARTE_PATH)
            neu = {"zeit": time.time(), "dauer_s": round(time.time() - start, 1), "grund": grund,
                   "fehler": fehler, "inseln": inseln}
            if alt and alt.get("inseln"):
                _schreiben(VORHER_PATH, alt)
            _schreiben(KARTE_PATH, neu)
            self.letzter_fehler = None
            log.info("Kartenscan fertig: %d Inseln in %.0f s (%d Fehler)", len(inseln), neu["dauer_s"], fehler)
        except Exception as e:  # noqa: BLE001 - der Hub darf daran nicht sterben
            self.letzter_fehler = str(e)
            log.exception("Kartenscan fehlgeschlagen")
        finally:
            self.laeuft = False
            self.fortschritt = 1.0

    def planer(self, stop: threading.Event) -> None:
        """Taeglich um SCAN_STUNDE scannen; ohne gespeicherte Karte sofort."""
        if _lesen(KARTE_PATH) is None:
            self.scan_starten("erster Start")
        while not stop.wait(60):
            jetzt = datetime.now(TZ)
            daten = _lesen(KARTE_PATH) or {}
            letzter = datetime.fromtimestamp(daten.get("zeit", 0), TZ)
            faellig = jetzt.replace(hour=SCAN_STUNDE, minute=0, second=0, microsecond=0)
            if jetzt >= faellig and letzter < faellig:
                self.scan_starten(f"taeglich {SCAN_STUNDE}:00 Uhr")

    # --------------------------------------------------------------- Abfrage
    def ich(self) -> dict:
        if self._ich is None or time.time() - self._ich_zeit > 3600:
            try:
                r = self.client.get("/me")
                r.raise_for_status()
                me = r.json()
                self._ich = {"id": me.get("id"), "name": me.get("name"), "allianz": me.get("allianz")}
                self._ich_zeit = time.time()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("GET /me fehlgeschlagen: %s", e)
        return self._ich or {}

    def daten(self) -> dict:
        """Karte fuer die Seite: Inseln kompakt, Spieler, Allianzen, Aenderungen."""
        stand = _lesen(KARTE_PATH) or {}
        vorher = _lesen(VORHER_PATH) or {}
        inseln = stand.get("inseln") or {}
        liste, spieler, allianzen = [], {}, {}
        for koord, i in inseln.items():
            a = i.get("allianz") or {}
            liste.append({"k": koord, "x": i["x"], "y": i["y"], "z": i["z"], "n": i.get("name") or "",
                          "b": i.get("besitzer"), "r": bool(i.get("ruine")), "p": i.get("punkte") or 0,
                          "a": a.get("id")})
            if i.get("besitzer"):
                s = spieler.setdefault(i["besitzer"], {"name": i["besitzer"], "inseln": 0, "punkte": 0, "a": a.get("id")})
                s["inseln"] += 1
                s["punkte"] += i.get("punkte") or 0
            if a.get("id"):
                eintrag = allianzen.setdefault(a["id"], {"id": a["id"], "name": a.get("name"), "tag": a.get("tag"),
                                                         "inseln": 0, "spieler": set()})
                eintrag["inseln"] += 1
                if i.get("besitzer"):
                    eintrag["spieler"].add(i["besitzer"])
        for a in allianzen.values():
            a["spieler"] = len(a["spieler"])
        return {
            "zeit": stand.get("zeit"), "dauer_s": stand.get("dauer_s"), "vorher_zeit": vorher.get("zeit"),
            "welt": {"min": WELT_MIN, "max": WELT_MAX},
            "inseln": liste,
            "spieler": sorted(spieler.values(), key=lambda s: s["name"].lower()),
            "allianzen": sorted(allianzen.values(), key=lambda a: -a["inseln"]),
            "aenderungen": self._aenderungen(vorher.get("inseln") or {}, inseln) if vorher else [],
            "ich": self.ich(),
            "scan": self.scan_status(),
        }

    @staticmethod
    def _aenderungen(alt: dict, neu: dict) -> list[dict]:
        aenderungen = []
        for koord, i in neu.items():
            a = alt.get(koord)
            if a is None:
                aenderungen.append({"k": koord, "art": "neu", "text": f"neue Insel ({i.get('besitzer') or 'frei'})"})
            elif (a.get("besitzer") or None) != (i.get("besitzer") or None):
                if i.get("ruine") and not a.get("ruine"):
                    aenderungen.append({"k": koord, "art": "ruine",
                                        "text": f"zur Ruine geworden (vorher {a.get('besitzer') or 'frei'})"})
                elif not a.get("besitzer"):
                    aenderungen.append({"k": koord, "art": "besiedelt", "text": f"besiedelt von {i.get('besitzer')}"})
                else:
                    aenderungen.append({"k": koord, "art": "wechsel",
                                        "text": f"{a.get('besitzer')} → {i.get('besitzer') or 'frei'}"})
            elif i.get("ruine") and not a.get("ruine"):
                aenderungen.append({"k": koord, "art": "ruine", "text": "zur Ruine geworden"})
        for koord, a in alt.items():
            if koord not in neu:
                aenderungen.append({"k": koord, "art": "weg", "text": f"verschwunden (vorher {a.get('besitzer') or 'frei'})"})
        return aenderungen

    def scan_status(self) -> dict:
        return {"laeuft": self.laeuft, "fortschritt": round(self.fortschritt, 3), "fehler": self.letzter_fehler}

    def angriffe(self) -> list[dict]:
        """Feindliche Flotten im Anflug auf eigene Inseln (kein Handel/Support/
        Spionage, nicht nur vorbeifahrend) - hoechstens alle 20 s abgefragt."""
        if time.time() - self._angriffe_zeit < 20:
            return self._angriffe
        try:
            r = self.client.get("/fleets/incoming")
            r.raise_for_status()
            harmlos = {"transport", "support", "spy", "handel"}
            self._angriffe = [f for f in r.json() or []
                              if not f.get("vorbeifahrend") and not f.get("eigene") and not f.get("abfahrend")
                              and f.get("mission") not in harmlos]
            self._angriffe_zeit = time.time()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("GET /fleets/incoming fehlgeschlagen: %s", e)
        return self._angriffe
