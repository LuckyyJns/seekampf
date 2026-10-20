"""Bauplan-Simulator: spielt Bau-Reihenfolgen einer Insel im Rechner durch.

Nur zum Messen (Stufe a), steuert nichts. Zwei Planer auf demselben Modell:
  kette(...)  - die echte Prioritaets-Kaskade (planner.choose_upgrade), so wie
                der Bot sie im Betrieb anwendet
  suchen(...) - Beam-Search ueber Bau-Reihenfolgen (Idee aus ~/SeekampfBot,
                sim/search.py), bewertet am Horizont

Modell (Regeln, Werte aus gamedata.py):
- Es baut immer nur ein Auftrag; der naechste startet fruehestens, wenn der
  vorige fertig ist UND die Rohstoffe da sind. Kosten werden beim Start
  abgebucht, die neue Stufe gilt ab Fertigstellung.
- Produktion aus den Minenstufen, gekappt an der Lagerkapazitaet (Lagerhaus;
  ohne Lagerhaus 500). Was darueber hinaus produziert wird, zaehlt als verfallen.
- Bauzeit mit Haupthaus-Abzug, Voraussetzungen: Kaserne ab Haupthaus 5,
  Hafen ab Haupthaus 7 (nur fertige Stufen).
- Einnahmen/Ausgaben von aussen (Raid-Beute, Ausgleich, Allianz, Ausbildung,
  Kolonisation) als feste Rate je Stunde (`extern`, z. B. aus der Rohstoff-Bilanz).
Nicht im Modell: Allianzkasse (nimmt nur den Ueberlauf), Reservierungen des
Kolonisations-Bots, Ausbauziele/Priorisierung aus dem Hub (Sperren schon).
Beide Planer sehen dieselbe Vereinfachung - der Vergleich bleibt fair.
"""
from __future__ import annotations

import config
import gamedata
import planner
from seekampf_gemeinsam import bilanz

RES = config.RESOURCE_KEYS
GEBAEUDE = config.TRACKED_BUILDINGS
MINE = {"goldmine": "gold", "steingrube": "stein", "saegewerk": "holz"}
HAUPTHAUS_AB = {"kaserne": 5, "hafen": 7}
MAX_STUFE = 20
LAGER_OHNE_LAGERHAUS = 500.0
LEERLAUF_S = config.LOW_PRIORITY_DELAY_SECONDS
UEBERLAUF_S = config.UEBERLAUF_MIN_STUNDEN_24H * 3600

# Punkte je Stufe, aus 255 echten Tageswerten (data/verlauf.json) per kleinster
# Quadrate bestimmt (Abweichung hoechstens 0,6 Punkte); Summe 50 = 1000 Punkte
# bei allen Gebaeuden auf Stufe 20.
PUNKTE_JE_STUFE = {
    "haupthaus": 8.56, "hafen": 9.04, "kaserne": 7.27, "goldmine": 4.30, "steingrube": 4.35,
    "saegewerk": 4.21, "lagerhaus": 4.63, "steinmauer": 4.24, "wachturm": 3.39,
}

# Fuer das Kolonisationsschiff: Hafen 20 und ein Lager, in das 35.500 Holz passen.
KOLO_HAFEN, KOLO_LAGERHAUS = 20, 18

# Bewertung "wirtschaft": Produktion am Horizont zaehlt so viele Stunden weiter.
REST_H = 72.0


class Insel:
    __slots__ = ("t", "stufen", "lager", "verbaut", "erzeugt", "verfallen", "plan", "voll_seit", "laeuft",
                 "extern")

    def __init__(self, stufen: dict, lager: dict, extern: dict | None = None):
        self.t = 0.0
        self.stufen = {g: int(stufen.get(g, 0) or 0) for g in GEBAEUDE}
        self.lager = {r: float(lager.get(r, 0) or 0) for r in RES}
        self.verbaut = 0.0
        self.erzeugt = 0.0
        self.verfallen = 0.0
        self.plan: list[tuple[float, float, str, int]] = []   # (start, fertig, gebaeude, stufe)
        self.voll_seit: dict[str, float | None] = {r: None for r in RES}
        self.laeuft: str | None = None    # am Horizont noch im Bau
        self.extern = {r: float((extern or {}).get(r, 0) or 0) for r in RES}   # je Stunde, auch negativ
        kap = self.kapazitaet()
        for r in RES:
            if self.lager[r] >= kap:
                self.lager[r] = kap
                self.voll_seit[r] = 0.0

    def kopie(self) -> "Insel":
        k = Insel.__new__(Insel)
        k.t, k.verbaut, k.erzeugt, k.verfallen, k.laeuft = self.t, self.verbaut, self.erzeugt, self.verfallen, self.laeuft
        k.stufen, k.lager, k.voll_seit = dict(self.stufen), dict(self.lager), dict(self.voll_seit)
        k.plan = list(self.plan)
        k.extern = self.extern
        return k

    # ------------------------------------------------------------- Modell
    def produktion_h(self) -> dict:
        out = {r: 0.0 for r in RES}
        for g, r in MINE.items():
            out[r] = float(gamedata.production_per_h(g, self.stufen[g]) or 0.0)
        return out

    def rate_h(self) -> dict:
        """Zugang je Stunde: Minen plus Einnahmen von aussen."""
        prod = self.produktion_h()
        return {r: prod[r] + self.extern[r] for r in RES}

    def kapazitaet(self) -> float:
        lvl = self.stufen["lagerhaus"]
        if lvl <= 0:
            return LAGER_OHNE_LAGERHAUS
        return float(gamedata.entry("lagerhaus", lvl)["kapazitaet"])

    def punkte(self) -> float:
        return sum(PUNKTE_JE_STUFE[g] * s for g, s in self.stufen.items())

    def lager_nach(self, s: float) -> dict:
        kap, rate = self.kapazitaet(), self.rate_h()
        return {r: max(0.0, min(kap, self.lager[r] + rate[r] * s / 3600)) for r in RES}

    def warten(self, s: float) -> None:
        if s <= 0:
            return
        kap, prod = self.kapazitaet(), self.rate_h()
        for r in RES:
            roh = self.lager[r] + prod[r] * s / 3600
            neu = max(0.0, min(kap, roh))
            self.erzeugt += neu - self.lager[r]
            self.verfallen += max(0.0, roh - neu)
            if neu >= kap and self.voll_seit[r] is None:
                bis_voll = (kap - self.lager[r]) / prod[r] * 3600 if prod[r] > 0 else 0.0
                self.voll_seit[r] = self.t + max(0.0, bis_voll)
            self.lager[r] = neu
        self.t += s

    def bezahlbar_in(self, kosten: dict) -> float | None:
        kap, prod = self.kapazitaet(), self.rate_h()
        warte = 0.0
        for r in RES:
            fehlt = float(kosten.get(r, 0) or 0) - self.lager[r]
            if fehlt <= 0:
                continue
            if float(kosten.get(r, 0) or 0) > kap or prod[r] <= 0:
                return None
            warte = max(warte, fehlt / prod[r] * 3600)
        return warte

    def ueberlauf_in(self, s: float) -> bool:
        """Wie bot._ueberlauf: ein Rohstoff steht am Limit und das schon laenger
        (vereinfacht: ununterbrochen voll statt 'in den letzten 24 h')."""
        kap, prod = self.kapazitaet(), self.rate_h()
        for r in RES:
            seit = self.voll_seit[r]
            if seit is None:
                if prod[r] <= 0:
                    continue
                seit = self.t + (kap - self.lager[r]) / prod[r] * 3600
            if self.t + s - seit >= UEBERLAUF_S:
                return True
        return False

    def kandidaten(self, gesperrt=()) -> dict:
        out = {}
        for g in GEBAEUDE:
            lvl = self.stufen[g]
            if g in gesperrt or lvl >= MAX_STUFE or self.stufen["haupthaus"] < HAUPTHAUS_AB.get(g, 0):
                continue
            kosten = gamedata.cost(g, lvl + 1)
            if kosten:
                out[g] = {"building": g, "level": lvl, "target_level": lvl + 1, "cost": kosten}
        return out

    def bauen(self, g: str, horizont: float) -> bool:
        """Naechste Stufe von g bauen: warten bis bezahlbar, abbuchen, bauen.
        False, wenn es vor dem Horizont nicht mehr fertig wird (die Insel steht
        dann am Horizont) oder nie bezahlbar ist (Insel unveraendert)."""
        kosten = gamedata.cost(g, self.stufen[g] + 1)
        warte = self.bezahlbar_in(kosten) if kosten else None
        if warte is None:
            return False
        if self.t + warte >= horizont:
            self.warten(horizont - self.t)
            return False
        self.warten(warte)
        for r in RES:
            self.lager[r] -= float(kosten.get(r, 0) or 0)
            if self.lager[r] < self.kapazitaet():
                self.voll_seit[r] = None
        self.verbaut += sum(float(kosten.get(r, 0) or 0) for r in RES)
        dauer = gamedata.build_time_s(g, self.stufen[g] + 1, self.stufen["haupthaus"]) or 0.0
        start = self.t
        if start + dauer > horizont:
            self.warten(horizont - self.t)
            self.laeuft = g
            return False
        self.warten(dauer)
        kap_alt = self.kapazitaet()
        self.stufen[g] += 1
        if g == "lagerhaus" and self.kapazitaet() > kap_alt:
            self.voll_seit = {r: None for r in RES}
        self.plan.append((start, self.t, g, self.stufen[g]))
        return True

    def bis_horizont(self, horizont: float) -> "Insel":
        k = self.kopie()
        k.warten(horizont - k.t)
        return k

    def kolo_bereit_ab(self) -> float | None:
        """Wann Hafen 20 und Lagerhaus 18 erreicht waren (None = nicht im Horizont)."""
        return self._kolo_zeit() if self._kolo_ok(self.stufen) else None

    def _kolo_zeit(self) -> float | None:
        stufen = dict(self.stufen)
        for start, fertig, g, s in reversed(self.plan):
            stufen[g] = s - 1
            if not self._kolo_ok(stufen):
                return fertig
        return 0.0

    @staticmethod
    def _kolo_ok(stufen: dict) -> bool:
        return stufen.get("hafen", 0) >= KOLO_HAFEN and stufen.get("lagerhaus", 0) >= KOLO_LAGERHAUS


# ---------------------------------------------------------------- Bewertung
def wert(insel: Insel, ziel: str) -> float:
    """Wert einer Insel AM HORIZONT (vorher bis_horizont aufrufen)."""
    wirtschaft = insel.verbaut + sum(insel.lager.values()) + sum(insel.produktion_h().values()) * REST_H
    if ziel == "punkte":
        return insel.punkte() + wirtschaft * 1e-7
    return wirtschaft


# --------------------------------------------------------- Planer: Kaskade
def kette(start: Insel, horizont: float, gesperrt=()) -> Insel:
    """Die Prioritaets-Kaskade des Bots, wie bot._forecast sie anwendet: an jedem
    Zeitpunkt, an dem sich etwas aendern kann, fragt sie planner.choose_upgrade."""
    insel = start.kopie()
    while insel.t < horizont:
        kand = insel.kandidaten(gesperrt)
        if not kand:
            break
        punkte = {0.0, LEERLAUF_S + 1.0}
        for c in kand.values():
            eta = insel.bezahlbar_in(c["cost"])
            if eta is not None:
                punkte.add(eta + 1.0)
        kap, prod = insel.kapazitaet(), insel.rate_h()
        for r in RES:
            if insel.voll_seit[r] is not None:
                punkte.add(max(0.0, insel.voll_seit[r] + UEBERLAUF_S - insel.t) + 1.0)
            elif prod[r] > 0:
                punkte.add((kap - insel.lager[r]) / prod[r] * 3600 + UEBERLAUF_S + 1.0)
        gewaehlt = None
        for s in sorted(punkte):
            if insel.t + s >= horizont:
                break
            c = planner.choose_upgrade(kand, insel.lager_nach(s), kap, s >= LEERLAUF_S, insel.ueberlauf_in(s))
            if c is not None:
                gewaehlt = (s, c["building"])
                break
        if gewaehlt is None:
            break
        insel.warten(gewaehlt[0])
        if not insel.bauen(gewaehlt[1], horizont):
            break
    return insel.bis_horizont(horizont)


# ------------------------------------------------------ Planer: Beam-Search
def suchen(start: Insel, horizont: float, ziel: str = "wirtschaft", breite: int = 30,
           gesperrt=()) -> Insel:
    """Beam-Search: von jedem Stand aus jeden moeglichen naechsten Ausbau
    probieren, je Runde die `breite` besten Staende (bewertet, als wuerde danach
    nichts mehr gebaut) weiterverfolgen. Zusaetzlich startet die Suche mit dem
    Ergebnis der Kaskade als Kandidat - sie kann also nie schlechter abschneiden."""
    bester = kette(start, horizont, gesperrt)
    bester_wert = wert(bester, ziel)
    front = [start.kopie()]
    while front:
        kinder: dict[tuple, tuple[float, Insel]] = {}
        for insel in front:
            ende = insel.bis_horizont(horizont)
            w = wert(ende, ziel)
            if w > bester_wert:
                bester, bester_wert = ende, w
            for g in insel.kandidaten(gesperrt):
                k = insel.kopie()
                if not k.bauen(g, horizont):
                    if k.t >= horizont:
                        w = wert(k, ziel)
                        if w > bester_wert:
                            bester, bester_wert = k, w
                    continue
                schluessel = tuple(k.stufen.values())
                w = wert(k.bis_horizont(horizont), ziel)
                alt = kinder.get(schluessel)
                if alt is None or w > alt[0] or (w == alt[0] and k.t < alt[1].t):
                    kinder[schluessel] = (w, k)
        front = [k for _, k in sorted(kinder.values(), key=lambda x: -x[0])[:breite]]
    return bester


def kennzahlen(insel: Insel) -> dict:
    prod = insel.produktion_h()
    return {
        "punkte": round(insel.punkte(), 1),
        "produktion_h": round(sum(prod.values())),
        "erzeugt": round(insel.erzeugt),
        "verfallen": round(insel.verfallen),
        "verbaut": round(insel.verbaut),
        "lager": round(sum(insel.lager.values())),
        "ausbauten": len(insel.plan),
        "kolo_bereit_h": None if insel.kolo_bereit_ab() is None else round(insel.kolo_bereit_ab() / 3600, 1),
        "stufen": dict(insel.stufen),
        "plan": [(round(s / 3600, 2), g, st) for s, _, g, st in insel.plan],
    }


# ------------------------------------------------- Start aus dem Live-Stand
def aus_auftraegen(stufen: dict, lager: dict, auftraege: list, extern: dict | None, horizont: float) -> Insel:
    """Insel ab jetzt (t = 0), die Warteschlange vorweg abgearbeitet.
    auftraege: [(gebaeude, ziel_stufe, status, fertig_in_s)] in Reihenfolge; ein
    'aktiv'er Auftrag ist schon bezahlt und laeuft noch fertig_in_s Sekunden,
    wartende werden wie normale Ausbauten bezahlt und gebaut."""
    insel = Insel(stufen, lager, extern)
    for g, ziel, status, fertig_in in auftraege:
        if g not in insel.stufen or insel.stufen[g] >= ziel:
            continue
        if status == "aktiv":
            insel.warten(max(0.0, float(fertig_in or 0) - insel.t))
            insel.stufen[g] = ziel
        elif insel.stufen[g] + 1 == ziel:
            insel.bauen(g, horizont)
    return insel


def naechster_schritt(start: Insel, horizont: float, ziel: str, breite: int, gesperrt=()) -> str | None:
    """Erstes Gebaeude des Such-Plans nach der schon gefuellten Warteschlange."""
    ergebnis = suchen(start, horizont, ziel, breite, gesperrt)
    vorweg = len(start.plan)
    return ergebnis.plan[vorweg][2] if len(ergebnis.plan) > vorweg else None


# Was ausser Produktion und Ausbau auf einer Insel ankommt bzw. abgeht. Die
# Allianzkasse fehlt bewusst: sie nimmt nur den Ueberlauf, den das Modell selbst kennt.
EXTERN_KATEGORIEN = ("Raids", "Ausgleich erhalten", "Allianz erhalten", "Ausgleich abgegeben",
                     "Ausbildung", "Kolonisation")


def extern_je_stunde(tage: int = 3, ordner: str | None = None) -> dict:
    """{insel_id: {r: je Stunde}} aus der Rohstoff-Bilanz - Durchschnitt der
    letzten `tage` vollen Tage (heute zaehlt nicht, er ist noch unvollstaendig)."""
    heute = bilanz.tag()
    buchungen = {t: v for t, v in bilanz.zusammenfassen(tage + 1, ordner)["buchungen"].items() if t != heute}
    out: dict = {}
    for inseln in buchungen.values():
        for iid, kat in inseln.items():
            summe = out.setdefault(str(iid), {r: 0.0 for r in RES})
            for k in EXTERN_KATEGORIEN:
                for r, n in (kat.get(k) or {}).items():
                    if r in summe:
                        summe[r] += float(n or 0)
    stunden = max(1, len(buchungen)) * 24
    return {iid: {r: v / stunden for r, v in werte.items()} for iid, werte in out.items()}
