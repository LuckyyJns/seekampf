"""Verlauf je Insel und Tag - fuer die Diagramme im Seekampf-Hub und die
Lager-Ueberlauf-Regel des Planers.

data/verlauf.json:
  {"inseln": {insel_id: {"JJJJ-MM-TT": Tag}}, "fenster": {insel_id: [[t, voll_s], ...]}}

Tag = {
  "produktion": {gold, stein, holz}   Stundenproduktion (letzter Stand des Tages)
  "kapazitaet": n                      Lagerkapazitaet je Rohstoff
  "punkte": n | None                   Inselpunkte (vor dem 01.10.2026 unbekannt)
  "stufen": {gebaeude: stufe}          Ausbaustufen (rueckwirkend: begonnene Stufen)
  "stufen_summe": n
  "ausbauten": n                       gestartete Ausbauten an dem Tag
  "voll_s": {gold, stein, holz}        Sekunden, in denen das Lager voll war
  "verlust": {gold, stein, holz}       dabei verpuffte Produktion
}

Gezaehlt wird zeitgewichtet zwischen zwei Durchlaeufen (die kommen unregel-
maessig): war ein Rohstoff beim letzten Durchlauf voll, gilt die Zeit bis
jetzt als "voll" und die Produktion in der Zeit als verloren. Luecken ueber
LUECKE_MAX (Dienst aus) werden nicht hochgerechnet.

`fenster` haelt die Voll-Zeiten der letzten 24 Stunden je Insel fest - daraus
liest der Planer, ob ein Lager regelmaessig ueberlaeuft (voll_letzte_24h).
Beim allerersten Start wird der Verlauf aus den Tageslogs nachgetragen.
"""
from __future__ import annotations

import glob
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.TIMEZONE_NAME)
RES = config.RESOURCE_KEYS
VOLL_ANTEIL = 0.99      # ab diesem Fuellstand gilt ein Rohstoff als "voll"
LUECKE_MAX = 1800       # laengere Pausen zwischen zwei Durchlaeufen nicht hochrechnen
TAGE_BEHALTEN = 180

STATUS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) \[\w+\] Insel (\S+) \| Warteschlange \d+/\d+ \| "
    r"Lager (\d+)/(\d+)/(\d+) von (\d+) \| Produktion \+(\d+)/\+(\d+)/\+(\d+) pro h(.*)$")
GESTARTET_RE = re.compile(r"\| gestartet: (.*?)(?: \| kein Ausbau:.*)?$")
STUFE_RE = re.compile(r"(\w+) → (\d+)")


def _leerer_tag() -> dict:
    return {"produktion": {r: 0.0 for r in RES}, "kapazitaet": 0, "punkte": None, "stufen": {},
            "stufen_summe": 0, "ausbauten": 0, "voll_s": {r: 0.0 for r in RES},
            "verlust": {r: 0.0 for r in RES}}


class Verlauf:
    def __init__(self, path: str = config.VERLAUF_PATH, log_dir: str = config.LOG_DIR):
        self.path = path
        self.log_dir = log_dir
        self.data = self._laden()
        self._letzter: dict[str, tuple[float, dict]] = {}  # insel -> (zeit, rohstoffe) des letzten Durchlaufs

    # ---------------------------------------------------------- Datei
    def _laden(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                daten = json.load(f)
            if isinstance(daten, dict) and isinstance(daten.get("inseln"), dict):
                daten.setdefault("fenster", {})
                return daten
        except FileNotFoundError:
            fenster: dict[str, list] = {}
            inseln = aus_logs(self.log_dir, fenster)
            # Die Stufen der nachgetragenen Tage stimmen erst, wenn der erste
            # echte Durchlauf die aktuellen Stufen liefert (_stufen_rueckrechnen).
            return {"inseln": inseln, "fenster": fenster, "rueckrechnen": sorted(inseln)}
        except (OSError, ValueError):
            try:  # kaputt: beiseitelegen statt ueberschreiben
                os.replace(self.path, self.path + ".defekt")
            except OSError:
                pass
        return {"inseln": {}, "fenster": {}}

    def speichern(self) -> None:
        ordner = os.path.dirname(self.path)
        os.makedirs(ordner, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=ordner, prefix=".verlauf-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # ------------------------------------------------------- Erfassen
    def tag(self, insel_id, datum: str) -> dict:
        tage = self.data["inseln"].setdefault(str(insel_id), {})
        if datum not in tage:
            tage[datum] = _leerer_tag()
            # Stufen vom Vortag uebernehmen, bis der Durchlauf die echten liefert
            vorher = sorted(d for d in tage if d < datum)
            if vorher:
                tage[datum]["stufen"] = dict(tage[vorher[-1]].get("stufen") or {})
        return tage[datum]

    def erfassen(self, insel_id, rohstoffe: dict, gebaeude: list, punkte, gestartet: int,
                 jetzt: float) -> None:
        """Einen Durchlauf einer Insel verbuchen."""
        key = str(insel_id)
        datum = datetime.fromtimestamp(jetzt, TZ).date().isoformat()
        tag = self.tag(key, datum)
        prod = rohstoffe.get("produktion_pro_h") or {}
        kap = float(rohstoffe.get("kapazitaet") or 0)
        tag["produktion"] = {r: float(prod.get(r, 0) or 0) for r in RES}
        tag["kapazitaet"] = kap
        if punkte is not None:
            tag["punkte"] = punkte
        if gebaeude:
            tag["stufen"] = {b["typ"]: int(b.get("stufe") or 0) for b in gebaeude if b.get("typ")}
            tag["stufen_summe"] = sum(tag["stufen"].values())
            if key in self.data.get("rueckrechnen", []):
                self._stufen_rueckrechnen(key, datum, tag["stufen"])
        tag["ausbauten"] += gestartet

        vorher = self._letzter.get(key)
        self._letzter[key] = (jetzt, rohstoffe)
        if vorher is None:
            return
        t0, res0 = vorher
        dauer = jetzt - t0
        if not 0 < dauer <= LUECKE_MAX:
            return
        kap0 = float(res0.get("kapazitaet") or 0)
        prod0 = res0.get("produktion_pro_h") or {}
        voll_gesamt = 0.0
        for r in RES:
            if kap0 > 0 and float(res0.get(r, 0) or 0) >= kap0 * VOLL_ANTEIL:
                tag["voll_s"][r] += dauer
                tag["verlust"][r] += float(prod0.get(r, 0) or 0) * dauer / 3600
                voll_gesamt = dauer
        if voll_gesamt:
            fenster = self.data["fenster"].setdefault(key, [])
            fenster.append([jetzt, voll_gesamt])

    def _stufen_rueckrechnen(self, key: str, heute: str, aktuell: dict) -> None:
        """Stufen der aus den Logs nachgetragenen Tage: aktuelle Stufe minus
        alles, was danach begonnen wurde. So zaehlen auch Gebaeude mit, die
        im Log-Zeitraum nie ausgebaut wurden."""
        tage = self.data["inseln"].get(key, {})
        danach: dict[str, int] = {}
        for datum in sorted((d for d in tage if d < heute), reverse=True):
            tag = tage[datum]
            tag["stufen"] = {typ: max(0, n - danach.get(typ, 0)) for typ, n in aktuell.items()}
            tag["stufen_summe"] = sum(tag["stufen"].values())
            for typ, n in (tag.pop("_starts", None) or {}).items():
                danach[typ] = danach.get(typ, 0) + n
        self.data["rueckrechnen"].remove(key)

    def aufraeumen(self, jetzt: float) -> None:
        grenze = (datetime.fromtimestamp(jetzt, TZ).date() - timedelta(days=TAGE_BEHALTEN)).isoformat()
        for tage in self.data["inseln"].values():
            for d in [d for d in tage if d < grenze]:
                del tage[d]
        for key, fenster in self.data["fenster"].items():
            self.data["fenster"][key] = [e for e in fenster if e[0] >= jetzt - 86400]

    # -------------------------------------------------------- Abfragen
    def voll_letzte_24h(self, insel_id, jetzt: float) -> float:
        """Sekunden, in denen auf der Insel in den letzten 24 h mindestens ein
        Rohstoff am Lagerlimit stand."""
        return sum(d for t, d in self.data["fenster"].get(str(insel_id), []) if t >= jetzt - 86400)


def aus_logs(log_dir: str, fenster: dict | None = None) -> dict:
    """Verlauf rueckwirkend aus den Statuszeilen der Tageslogs. Mit `fenster`
    werden dort auch die Voll-Zeiten der letzten 24 h eingetragen, damit die
    Ueberlauf-Regel sofort greifen kann."""
    seit = datetime.now(TZ).timestamp() - 86400
    inseln: dict[str, dict] = {}
    letzter: dict[str, tuple[datetime, list[float], list[float], float]] = {}
    for pfad in sorted(glob.glob(os.path.join(log_dir, f"{config.LOG_FILE_PREFIX}-*.log"))):
        try:
            f = open(pfad, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for zeile in f:
                m = STATUS_RE.match(zeile.rstrip("\n"))
                if not m:
                    continue
                t = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
                insel = m.group(3)
                lager = [float(m.group(i)) for i in (4, 5, 6)]
                kap = float(m.group(7))
                prod = [float(m.group(i)) for i in (8, 9, 10)]
                tage = inseln.setdefault(insel, {})
                tag = tage.setdefault(m.group(1), _leerer_tag())
                if not tag["stufen"]:
                    vorher = sorted(d for d in tage if d < m.group(1))
                    if vorher:
                        tag["stufen"] = dict(tage[vorher[-1]]["stufen"])
                tag["produktion"] = dict(zip(RES, prod))
                tag["kapazitaet"] = kap
                if (g := GESTARTET_RE.search(m.group(11))):
                    for typ, stufe in STUFE_RE.findall(g.group(1)):
                        tag["stufen"][typ] = max(tag["stufen"].get(typ, 0), int(stufe))
                        tag["ausbauten"] += 1
                        starts = tag.setdefault("_starts", {})
                        starts[typ] = starts.get(typ, 0) + 1
                    tag["stufen_summe"] = sum(tag["stufen"].values())
                if insel in letzter:
                    t0, lager0, prod0, kap0 = letzter[insel]
                    dauer = (t - t0).total_seconds()
                    if 0 < dauer <= LUECKE_MAX:
                        voll = False
                        for i, r in enumerate(RES):
                            if kap0 > 0 and lager0[i] >= kap0 * VOLL_ANTEIL:
                                tag["voll_s"][r] += dauer
                                tag["verlust"][r] += prod0[i] * dauer / 3600
                                voll = True
                        if voll and fenster is not None and t.timestamp() >= seit:
                            fenster.setdefault(insel, []).append([t.timestamp(), dauer])
                letzter[insel] = (t, lager, prod, kap)
    for tage in inseln.values():  # Summen auch fuer Tage ohne gestarteten Ausbau
        for tag in tage.values():
            tag["stufen_summe"] = sum(tag["stufen"].values())
    return inseln
