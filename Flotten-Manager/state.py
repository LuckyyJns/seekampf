"""Der gesamte Zustand des Flotten-Managers in einer JSON-Datei.

Enthaelt Einstellungen, je Insel die Zielliste und Statistik, die gerade
fahrenden Flotten, die Gesamtstatistik und den Beute-Verlauf je Tag. Die Weboberflaeche und die Bot-Schleife laufen in einem Prozess,
aber in verschiedenen Threads - jeder Zugriff geht deshalb ueber das Lock.

Geschrieben wird ueber eine temporaere Datei mit os.replace: ein Stromausfall
mitten im Speichern hinterlaesst so entweder den alten oder den neuen Stand,
nie eine halbe Datei.
"""
from __future__ import annotations

import copy
import glob
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.TIMEZONE_NAME)
VERLAUF_TAGE = 120


def _leere_stats() -> dict:
    return {
        # Wie viele Flotten haben ein Ziel tatsaechlich erreicht (Mehrfach-
        # angriffe auf dieselbe Insel zaehlen jedes Mal).
        "raids": 0,
        # Verschiedene Inseln, die mindestens einmal angegriffen wurden.
        "inseln_besucht": [],
        "gold": 0.0,
        "stein": 0.0,
        "holz": 0.0,
        "niederlagen": 0,
        # Kumulierte Laufzeit ueber alle Laeufe hinweg, in Sekunden. Es zaehlt
        # nur Zeit, in der der Manager wirklich lief (gestoppt = Uhr steht).
        "laufzeit_s": 0.0,
        "seit": None,
    }


def neue_insel(name: str = "", koordinaten: str = "", aktiv: bool = False) -> dict:
    """Je eigene Insel: an/aus, eigene Zielliste samt Rotation, eigene Statistik."""
    return {
        "aktiv": aktiv, "name": name, "koordinaten": koordinaten,
        "ziele": {},             # "x:y:z" -> Ziel-Datensatz
        "rotation": [],          # Reihenfolge der Koordinaten (stur reihum)
        "rotation_index": 0,
        "letzter_scan": None,
        "pause_grund": None,
        # Rohstoff-Ausgleich: gibt/bekommt + eigene Grenzen (siehe ausgleich.einstellung)
        "ausgleich": {"gibt": False, "bekommt": False, "ziel": None, "reserve": None, "max_abgabe": 0},
        # Eigene Werte fuer config.INSEL_EINSTELLUNGEN; was fehlt, gilt global.
        "einstellungen": {},
        "stats": _leere_stats(),
    }


class State:
    def __init__(self, path: str = config.STATE_PATH):
        self.path = path
        self.lock = threading.RLock()
        self._speicher_lock = threading.Lock()
        # Pfad der beiseitegelegten Datei, falls state.json unlesbar war -
        # web.py meldet das dann und laesst den Manager pausiert anlaufen.
        self.defekt: str | None = None
        self.data = self._load()

    # ------------------------------------------------------------ Laden/Speichern
    def _load(self) -> dict:
        roh = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    roh = json.load(f)
                if not isinstance(roh, dict):
                    raise ValueError("kein JSON-Objekt")
            except (ValueError, OSError):
                # Nie stillschweigend mit leerem Stand weitermachen: das naechste
                # Speichern wuerde Statistik, Ziellisten und die Herkunft der
                # fahrenden Flotten endgueltig ueberschreiben. Die kaputte Datei
                # bleibt zur Rettung liegen.
                self.defekt = f"{self.path}.defekt-{time.strftime('%Y%m%d-%H%M%S')}"
                os.replace(self.path, self.defekt)
                roh = {"laeuft": False}

        daten = {
            "laeuft": True,          # nach einem Neustart sofort weiterraiden
            "settings": {},
            "inseln": {},            # Insel-ID (str) -> neue_insel()
            "flotten": {},           # fleet_id (str) -> laufende Flotte
            "stats": _leere_stats(),  # Gesamt ueber alle Inseln
            "verlauf": {},           # Insel-ID (str) -> {"JJJJ-MM-TT": {gold, stein, holz, raids}}
            "letzter_report": None,  # ISO-Datum des letzten Telegram-Reports
            "report_basis": None,    # Statistik-Stand beim letzten Report
            "verbuchte_berichte": [],  # message_ids, damit nichts doppelt zaehlt
            "pause_grund": None,
            "ausgleich": None,       # Rohstoff-Ausgleich: Fahrten, Summen, letzte Lieferungen
            "kasse": None,           # Ueberlauf in die Allianzkasse: Summen, letzte Einzahlungen
        }
        daten.update({k: v for k, v in roh.items() if k in daten})
        # Aus der Ein-Insel-Zeit: Zielliste und Rotation lagen oben. Sie gehoeren
        # der Heimatinsel - welche das ist, weiss erst der Manager (auf_inseln_umstellen).
        if not daten["inseln"] and "ziele" in roh:
            daten["altbestand"] = {k: roh.get(k) for k in
                                   ("ziele", "rotation", "rotation_index", "letzter_scan")}
        import ausgleich  # spaet: ausgleich importiert geo/config, nicht state
        if not isinstance(daten["ausgleich"], dict):
            daten["ausgleich"] = ausgleich.leerer_stand()
        for insel in daten["inseln"].values():
            ausgleich.einstellung(insel)  # alte Rolle -> gibt/bekommt
            stats = _leere_stats()
            stats.update(insel.get("stats") or {})
            insel["stats"] = stats
            eigene = insel.get("einstellungen") or {}
            insel["einstellungen"] = {k: v for k, v in eigene.items() if k in config.INSEL_EINSTELLUNGEN}

        # Neue Einstellungen aus config ergaenzen, bestehende nicht ueberschreiben.
        # Abgeschaffte Schluessel fallen dabei raus (z. B. die frueher fest
        # eingestellten Schiffstypen), damit die Datei nicht verwahrlost.
        settings = dict(config.DEFAULT_SETTINGS)
        settings.update({k: v for k, v in (daten.get("settings") or {}).items()
                         if k in config.DEFAULT_SETTINGS})
        daten["settings"] = settings

        stats = _leere_stats()
        stats.update(daten.get("stats") or {})
        daten["stats"] = stats
        return daten

    def _kopie(self) -> dict:
        """Tiefe Kopie des Zustands.

        Der Bot-Thread aendert data ohne Lock (er haelt es nicht ueber seine
        API-Aufrufe hinweg, sonst haenge die Weboberflaeche). Trifft die Kopie
        genau auf so eine Aenderung, wirft deepcopy "changed size during
        iteration" - dann einfach noch einmal.
        """
        with self.lock:
            for _ in range(4):
                try:
                    return copy.deepcopy(self.data)
                except RuntimeError:
                    time.sleep(0.01)
            return copy.deepcopy(self.data)

    def save(self) -> None:
        # Bot-Thread und Weboberflaeche speichern unabhaengig voneinander. Ohne
        # eigene Sperre und mit festem Temp-Namen nahm der eine dem anderen die
        # Temp-Datei unter den Fuessen weg (FileNotFoundError beim os.replace).
        with self._speicher_lock:
            schnappschuss = self._kopie()
            ordner = os.path.dirname(self.path)
            os.makedirs(ordner, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=ordner, prefix=".state-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(schnappschuss, f, ensure_ascii=False, indent=1)
                    f.flush()
                    os.fsync(f.fileno())  # erst auf der Karte, dann umbenennen
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise

    def snapshot(self) -> dict:
        return self._kopie()

    # ----------------------------------------------------------- Einstellungen
    @property
    def settings(self) -> dict:
        return self.data["settings"]

    @staticmethod
    def _umwandeln(key: str, wert):
        """Wert in den Typ des Standardwerts bringen - die Weboberflaeche
        schickt alles als Text. ValueError, wenn das nicht geht."""
        standard = config.DEFAULT_SETTINGS[key]
        if isinstance(standard, bool):
            return wert if isinstance(wert, bool) else str(wert).lower() in ("1", "true", "ja", "on")
        try:
            if isinstance(standard, int):
                return int(wert)
            if isinstance(standard, float):
                return float(wert)
        except (TypeError, ValueError) as e:
            raise ValueError(str(e)) from e
        return str(wert)

    def update_settings(self, neu: dict) -> dict:
        """Uebernimmt nur bekannte Schluessel und erzwingt den Typ des
        Standardwerts."""
        geaendert = {}
        with self.lock:
            for key, wert in (neu or {}).items():
                if key not in config.DEFAULT_SETTINGS:
                    continue
                try:
                    wert = self._umwandeln(key, wert)
                except ValueError:
                    continue
                if self.settings.get(key) != wert:
                    self.settings[key] = wert
                    geaendert[key] = wert
        if geaendert:
            self.save()
        return geaendert

    def insel_settings(self, insel_id) -> dict:
        """Die fuer diese Insel wirksamen Einstellungen: global, ueberlagert
        von dem, was die Insel selbst setzt."""
        wirksam = dict(self.settings)
        insel = self.insel(insel_id)
        if insel is not None:
            wirksam.update(insel.get("einstellungen") or {})
        return wirksam

    def update_insel_settings(self, insel_id, neu: dict) -> dict:
        """Eigene Werte einer Insel setzen. Leer (None/"") heisst: wieder den
        globalen Wert nehmen. Rueckgabe: die eigenen Werte danach."""
        with self.lock:
            insel = self.insel(insel_id)
            if insel is None:
                raise KeyError(insel_id)
            eigene = insel.setdefault("einstellungen", {})
            for key, wert in (neu or {}).items():
                if key not in config.INSEL_EINSTELLUNGEN:
                    continue
                if wert is None or (isinstance(wert, str) and not wert.strip()):
                    eigene.pop(key, None)
                    continue
                try:
                    eigene[key] = self._umwandeln(key, wert)
                except ValueError:
                    continue
            ergebnis = dict(eigene)
        self.save()
        return ergebnis

    # ------------------------------------------------------------------ Inseln
    def insel(self, insel_id) -> dict | None:
        return self.data["inseln"].get(str(insel_id))

    def auf_inseln_umstellen(self, heimat_id, name: str, koordinaten: str) -> bool:
        """Einmalig: den Altbestand der Heimatinsel zuordnen. Die bisherige
        Gesamtstatistik ist ihre Statistik (alle Raids kamen von dort), der
        Beute-Verlauf wird aus den Logs nachgetragen. True, wenn umgestellt."""
        alt = self.data.pop("altbestand", None)
        if alt is None:
            return False
        with self.lock:
            insel = neue_insel(name, koordinaten, aktiv=True)
            insel["ziele"] = alt.get("ziele") or {}
            insel["rotation"] = alt.get("rotation") or []
            insel["rotation_index"] = int(alt.get("rotation_index") or 0)
            insel["letzter_scan"] = alt.get("letzter_scan")
            insel["stats"] = copy.deepcopy(self.data["stats"])
            self.data["inseln"][str(heimat_id)] = insel
            for rec in self.data["flotten"].values():
                rec.setdefault("insel_id", heimat_id)
            for eintrag in self.data.get("offene_berichte") or []:
                eintrag.setdefault("insel_id", heimat_id)
            if not self.data["verlauf"]:
                self.data["verlauf"][str(heimat_id)] = verlauf_aus_logs()
        self.save()
        return True

    # ------------------------------------------------------------------ Ziele
    def ziel(self, insel_id, koord: str) -> dict | None:
        insel = self.insel(insel_id)
        return None if insel is None else insel["ziele"].get(koord)

    def neues_ziel(self, koord: str, x: int, y: int, z: int, name: str,
                   distanz: float, fahrzeit_s: float) -> dict:
        return {
            "koordinaten": koord, "x": x, "y": y, "z": z, "name": name,
            "distanz": distanz, "fahrzeit_s": fahrzeit_s,
            "raids": 0, "niederlagen": 0, "niederlagen_gesamt": 0,
            "letzter_raid": None, "letzte_beute": None,
            "beute_gesamt": {"gold": 0.0, "stein": 0.0, "holz": 0.0},
            "blacklist_bis": None, "blacklist_grund": None,
        }

    def ist_gesperrt(self, ziel: dict, jetzt: float | None = None) -> bool:
        bis = ziel.get("blacklist_bis")
        if not bis:
            return False
        if bis == "dauerhaft":
            return True
        return (jetzt or time.time()) < float(bis)

    # -------------------------------------------------------------- Statistik
    def stats_zuruecksetzen(self) -> None:
        """Gesamt- und Inselstatistik auf null. Der Beute-Verlauf bleibt - er
        ist das Archiv, aus dem das Diagramm zeichnet."""
        with self.lock:
            self.data["stats"] = _leere_stats()
            for insel in self.data["inseln"].values():
                insel["stats"] = _leere_stats()
            self.data["report_basis"] = None
        self.save()

    # --------------------------------------------------------------- Verlauf
    def verlauf_buchen(self, insel_id, zeitpunkt: float, raids: int = 0, beute: dict | None = None) -> None:
        tage = self.data["verlauf"].setdefault(str(insel_id), {})
        datum = datetime.fromtimestamp(zeitpunkt, TZ).date().isoformat()
        tag = tage.setdefault(datum, {"gold": 0.0, "stein": 0.0, "holz": 0.0, "raids": 0})
        tag["raids"] += raids
        for r, n in (beute or {}).items():
            tag[r] = tag.get(r, 0.0) + float(n or 0)
        grenze = (datetime.now(TZ).date() - timedelta(days=VERLAUF_TAGE)).isoformat()
        for alt in [d for d in tage if d < grenze]:
            del tage[alt]


_BEUTE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \S+ \[\w+\] BEUTE\s+\S+ \(.*\): (.*)$")
_ANKUNFT = re.compile(r"^(\d{4}-\d{2}-\d{2}) \S+ \[\w+\] ANKUNFT\s")
_MENGE = re.compile(r"(\d+) (Gold|Stein|Holz)")


def verlauf_aus_logs() -> dict:
    """Beute und Raids je Tag aus den Logdateien - fuer den Verlauf vor der
    Umstellung auf mehrere Inseln."""
    tage: dict[str, dict] = {}
    for pfad in sorted(glob.glob(os.path.join(config.LOG_DIR, f"{config.LOG_FILE_PREFIX}-*.log"))):
        with open(pfad, encoding="utf-8", errors="replace") as f:
            for zeile in f:
                if (m := _ANKUNFT.match(zeile)):
                    tage.setdefault(m.group(1), {"gold": 0.0, "stein": 0.0, "holz": 0.0, "raids": 0})["raids"] += 1
                elif (m := _BEUTE.match(zeile)):
                    tag = tage.setdefault(m.group(1), {"gold": 0.0, "stein": 0.0, "holz": 0.0, "raids": 0})
                    for n, r in _MENGE.findall(m.group(2)):
                        tag[r.lower()] += float(n)
    return tage
