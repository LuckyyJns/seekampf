"""Lebenszeichen: jeder Bot meldet selbst, dass und wie er laeuft.

Nach jedem Takt schreibt der Bot eine kleine Datei `<ORDNER>/<bot>.json`:

    {"bot": "flotte", "pid": 1234, "takt_s": 5, "letzter_takt": 1792..., "letzter_erfolg": 1792...,
     "fehler_in_folge": 0, "letzter_fehler": null,
     "meldungen": [[zeit, "WARNING", "text"], ...]}      # Warnungen/Fehler der letzten 24 h

Die Meldungen sammelt ein Logging-Handler direkt beim Loggen - der Hub muss
dafuer keine Logdateien mehr lesen und auswerten (das brach still, wenn sich
das Format einer Zeile aenderte). Der Ordner liegt im Arbeitsspeicher
(/dev/shm): geschrieben wird bis zu alle paar Sekunden, die SD-Karte bleibt
geschont; nach einem Neustart des Pi schreibt jeder Bot ihn mit dem ersten
Takt neu.

Der Hub nennt einen Bot "haengt", wenn sein letzter Takt laenger als
HAENGT_FAKTOR x takt_s zurueckliegt, obwohl der Dienst laeuft.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import deque

ORDNER = "/dev/shm/seekampf-lebenszeichen" if os.path.isdir("/dev/shm") else \
    os.path.join(tempfile.gettempdir(), "seekampf-lebenszeichen")
FENSTER_S = 24 * 3600
MAX_MELDUNGEN = 500
HAENGT_FAKTOR = 5


class _Sammler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.meldungen: deque = deque(maxlen=MAX_MELDUNGEN)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.meldungen.append((record.created, record.levelname, record.getMessage()[:240]))
        except Exception:  # Logging darf den Bot niemals stoppen
            self.handleError(record)


class Lebenszeichen:
    def __init__(self, bot: str, takt_s: float, logger: logging.Logger | None = None,
                 ordner: str | None = None):
        self.bot = bot
        self.takt_s = float(takt_s)
        self.ordner = ordner or ORDNER
        self.letzter_erfolg: float | None = None
        self.fehler_in_folge = 0
        self.letzter_fehler: str | None = None
        self._sammler = _Sammler()
        # Meldungen ueber einen Neustart des Bots hinweg behalten.
        alt = lesen(bot, self.ordner) or {}
        grenze = time.time() - FENSTER_S
        for m in alt.get("meldungen") or []:
            if isinstance(m, list) and len(m) == 3 and m[0] >= grenze:
                self._sammler.meldungen.append(tuple(m))
        self._lock = threading.Lock()
        if logger is not None:
            logger.addHandler(self._sammler)

    def takt(self, fehler: str | None = None, takt_s: float | None = None) -> None:
        """Nach jedem Takt aufrufen; fehler = None heisst: fehlerfrei."""
        jetzt = time.time()
        with self._lock:
            if takt_s is not None:
                self.takt_s = float(takt_s)
            if fehler is None:
                self.letzter_erfolg, self.fehler_in_folge = jetzt, 0
            else:
                self.fehler_in_folge += 1
                self.letzter_fehler = str(fehler)[:300]
            grenze = jetzt - FENSTER_S
            daten = {"bot": self.bot, "pid": os.getpid(), "takt_s": self.takt_s, "letzter_takt": jetzt,
                     "letzter_erfolg": self.letzter_erfolg, "fehler_in_folge": self.fehler_in_folge,
                     "letzter_fehler": self.letzter_fehler,
                     "meldungen": [list(m) for m in self._sammler.meldungen if m[0] >= grenze]}
        try:
            os.makedirs(self.ordner, exist_ok=True)
            pfad = os.path.join(self.ordner, f"{self.bot}.json")
            tmp = f"{pfad}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(daten, f, ensure_ascii=False)
            os.replace(tmp, pfad)
        except OSError:
            pass   # ein fehlendes Lebenszeichen zeigt der Hub ohnehin an


def lesen(bot: str, ordner: str | None = None) -> dict | None:
    try:
        with open(os.path.join(ordner or ORDNER, f"{bot}.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def auswerten(d: dict | None, jetzt: float | None = None) -> dict | None:
    """Fuer den Hub: haengt? Zaehler und haeufigste Meldungen der letzten 24 h."""
    if not d:
        return None
    jetzt = jetzt or time.time()
    takt_s = float(d.get("takt_s") or 60)
    alter = jetzt - float(d.get("letzter_takt") or 0)
    meldungen = [m for m in d.get("meldungen") or [] if m[0] >= jetzt - FENSTER_S]
    gruppen: dict[str, int] = {}
    for _, level, text in meldungen:
        schluessel = "".join("#" if c.isdigit() else c for c in text)[:80]
        gruppen[schluessel] = gruppen.get(schluessel, 0) + 1
    haeufig = sorted(gruppen.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "letzter_takt": d.get("letzter_takt"), "letzter_erfolg": d.get("letzter_erfolg"), "takt_s": takt_s,
        "haengt": alter > HAENGT_FAKTOR * takt_s, "grenze_s": HAENGT_FAKTOR * takt_s,
        "fehler_in_folge": d.get("fehler_in_folge") or 0, "letzter_fehler": d.get("letzter_fehler"),
        "fehler": sum(1 for m in meldungen if m[1] in ("ERROR", "CRITICAL")),
        "warnungen": sum(1 for m in meldungen if m[1] == "WARNING"),
        "letzte": [{"zeit": t, "level": lv, "text": tx} for t, lv, tx in meldungen[-8:][::-1]],
        "haeufig": [{"anzahl": n, "text": t} for t, n in haeufig],
    }
