"""Parser und Normalisierung fuer das Allianz-Protokoll V1 (agenten.md, Abschnitte 3-5).

Reine Funktionen ohne API-Zugriff. `parse()` bekommt eine Nachricht so, wie
sie der Server liefert (Text, Betreff, Kanal, Absender-ID, Nachrichten-ID,
Server-Zeitpunkt) und gibt entweder die Normalform oder None zurueck - None
heisst: ignorieren. Der Grund steht dann in `Ergebnis.grund`.

Geprueft wird hier alles, was ohne Weltwissen geht (Schritte 2, 3 und 5 der
Pruefreihenfolge). Mitgliedschaft, Idempotenz, Obergrenzen und Whitelist
prueft der Bot selbst.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
HAUPTVERSION = 1
ANGEBOT_GUELTIG = timedelta(minutes=20)

KANAL_TYPEN = {
    "forum:notrufe": {"NOTRUF", "ENTWARNUNG"},
    "forum:rohstoffe": {"ANFRAGE", "ERLEDIGT"},
    "forum:teilnehmer": {"PRAESENZ"},
    "pn": {"NOTRUF", "ANGEBOT", "ZUSAGE", "ABSAGE", "VERSANDT", "RUECKGABE"},
}
BEKANNTE_TYPEN = set().union(*KANAL_TYPEN.values())

EINHEITEN = ("steinewerfer", "speerkaempfer", "bogenschuetze")
ROHSTOFFE = ("gold", "stein", "holz")
ERGEBNISSE = ("gehalten", "verloren")
ABSAGE_GRUENDE = ("abgelaufen", "ungueltig", "nicht_verfuegbar", "zurueckgezogen", "abgelehnt")

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "AE", "Ö": "OE", "Ü": "UE"})
_WORT = r"[A-Za-zÄÖÜäöü_]+"
KOPF_RE = re.compile(rf"^[ \t]*\[({_WORT})(?:/(\d+))?\]")
FELD_RE = re.compile(rf"^[ \t]*({_WORT})[ \t]*:[ \t]*(.*)$")

# Linksperre des Servers: ein Punkt zwischen zwei Buchstaben/Ziffern (Abschnitt 11).
LINKSPERRE_RE = re.compile(r"[A-Za-z0-9ÄÖÜäöüß]\.[A-Za-z0-9ÄÖÜäöüß]")


class Ungueltig(Exception):
    """Ein Feldwert entspricht nicht seinem Format."""


@dataclass
class Ergebnis:
    nachricht: dict | None
    grund: str | None = None


# ------------------------------------------------------------------ Werte
def zeit_normal(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def zeit_lesen(wert: str) -> datetime:
    """Normalform-String (oder beliebiges ISO mit Offset) -> aware datetime."""
    return datetime.fromisoformat(wert.replace("Z", "+00:00"))


_ZEIT_VOLL_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:\d{2})?$")
_ZEIT_KURZ_RE = re.compile(r"^(\d{2}):(\d{2})$")


def _zeit(wert: str, bezug: datetime) -> str:
    m = _ZEIT_KURZ_RE.match(wert)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            raise Ungueltig(wert)
        lokal = bezug.astimezone(TZ)
        kandidat = datetime(lokal.year, lokal.month, lokal.day, h, mi, tzinfo=TZ)
        while kandidat <= bezug:
            tag = kandidat.date() + timedelta(days=1)
            kandidat = datetime(tag.year, tag.month, tag.day, h, mi, tzinfo=TZ)
        return zeit_normal(kandidat)
    m = _ZEIT_VOLL_RE.match(wert)
    if not m:
        raise Ungueltig(wert)
    jahr, mon, tag, h, mi = (int(m.group(i)) for i in range(1, 6))
    sek = int(m.group(6) or 0)
    offset = m.group(7)
    try:
        if offset is None:
            dt = datetime(jahr, mon, tag, h, mi, sek, tzinfo=TZ)
        else:
            iso = f"{jahr:04d}-{mon:02d}-{tag:02d}T{h:02d}:{mi:02d}:{sek:02d}"
            dt = datetime.fromisoformat(iso + ("+00:00" if offset == "Z" else offset))
    except ValueError as e:
        raise Ungueltig(wert) from e
    return zeit_normal(dt)


def _koordinate(wert: str) -> str:
    if not re.fullmatch(r"\d+:\d+:\d+", wert):
        raise Ungueltig(wert)
    return ":".join(str(int(t)) for t in wert.split(":"))


def _zahl(wert: str) -> int:
    if not re.fullmatch(r"\d+", wert):
        raise Ungueltig(wert)
    return int(wert)


def _tokens(wert: str) -> list[str]:
    return [t for t in re.split(r"[\s,]+", wert) if t]


def _mengen(wert: str, namen: tuple[str, ...], keine_erlaubt: bool = True) -> dict:
    tokens = _tokens(wert)
    if keine_erlaubt and tokens == ["keine"]:
        return {}
    out: dict[str, int] = {}
    for t in tokens:
        m = re.fullmatch(r"([a-z_]+)=(\d+)", t)
        if not m or m.group(1) not in namen or m.group(1) in out:
            raise Ungueltig(wert)
        out[m.group(1)] = int(m.group(2))
    if not out:
        raise Ungueltig(wert)
    return out


def _einheiten(wert: str) -> dict:
    return _mengen(wert, EINHEITEN)


def _rohstoffe(wert: str) -> dict:
    # Rohstoffe kennen laut Tabelle kein "keine".
    return _mengen(wert, ROHSTOFFE, keine_erlaubt=False)


def _liste(wert: str) -> list[str]:
    tokens = _tokens(wert)
    if tokens == ["keine"]:
        return []
    if not tokens or any(not re.fullmatch(r"[a-z_]+", t) for t in tokens):
        raise Ungueltig(wert)
    return tokens


def _vorgang(wert: str) -> str:
    if not re.fullmatch(r"\d+-f?\d+", wert):
        raise Ungueltig(wert)
    return wert


def _text(wert: str) -> str:
    return wert


def _enum(erlaubt):
    def pruefe(wert: str) -> str:
        if wert not in erlaubt:
            raise Ungueltig(wert)
        return wert
    return pruefe


def eigentuemer(vorgang: str) -> int:
    return int(vorgang.split("-", 1)[0])


# ------------------------------------------------------------ Typ-Tabelle
P, K = True, False
FORMATE = {
    "NOTRUF": {"insel": (_koordinate, P), "vorgang": (_vorgang, K), "ankunft": (_zeit, K),
               "angreifer": (_text, K), "staerke": (_zahl, K), "bedarf": (_einheiten, K)},
    "ENTWARNUNG": {"vorgang": (_vorgang, P), "ergebnis": (_enum(ERGEBNISSE), K)},
    "ANFRAGE": {"insel": (_koordinate, P), "rohstoffe": (_rohstoffe, P), "vorgang": (_vorgang, K),
                "bis": (_zeit, K)},
    "ERLEDIGT": {"vorgang": (_vorgang, P)},
    "ANGEBOT": {"vorgang": (_vorgang, P), "von": (_koordinate, P), "ankunft": (_zeit, P),
                "einheiten": (_einheiten, K), "rohstoffe": (_rohstoffe, K), "leihe": (_liste, K),
                "gueltig_bis": (_zeit, K)},
    "ZUSAGE": {"vorgang": (_vorgang, P), "einheiten": (_einheiten, K), "rohstoffe": (_rohstoffe, K)},
    "ABSAGE": {"vorgang": (_vorgang, P), "grund": (_enum(ABSAGE_GRUENDE), K)},
    "VERSANDT": {"vorgang": (_vorgang, P), "von": (_koordinate, P), "ankunft": (_zeit, P),
                 "einheiten": (_einheiten, K), "rohstoffe": (_rohstoffe, K), "leihe": (_liste, K)},
    "RUECKGABE": {"vorgang": (_vorgang, P), "einheiten": (_einheiten, P), "ankunft": (_zeit, K)},
    "PRAESENZ": {"version": (_zahl, P), "nebenversion": (_zahl, P), "faehigkeiten": (_liste, P),
                 "bot": (_text, K)},
}
# Wer absenden darf: "eigentuemer", "nicht_eigentuemer" oder None (beliebig).
ABSENDER_REGEL = {
    "NOTRUF": "eigentuemer", "ENTWARNUNG": "eigentuemer", "ANFRAGE": "eigentuemer",
    "ERLEDIGT": "eigentuemer", "ANGEBOT": "nicht_eigentuemer", "ZUSAGE": "eigentuemer",
    "ABSAGE": None, "VERSANDT": "nicht_eigentuemer", "RUECKGABE": "eigentuemer", "PRAESENZ": None,
}
# Bei diesen Typen muss genau eines von einheiten/rohstoffe gesetzt sein.
GENAU_EINE_LADUNG = {"ANGEBOT", "VERSANDT"}


# ------------------------------------------------------------------ Parsen
def _normal_typ(roh: str) -> str:
    return roh.translate(_UMLAUTE).upper()


def _normal_schluessel(roh: str) -> str:
    return roh.translate(_UMLAUTE).lower()


def _zerlegen(zeilen: list[str]) -> tuple[dict[str, str], str]:
    """Feldblock + Freitext. Erstes Vorkommen eines Schluessels gilt."""
    felder: dict[str, str] = {}
    i = 0
    while i < len(zeilen):
        zeile = zeilen[i]
        if not zeile.strip():
            break
        m = FELD_RE.match(zeile)
        if not m:
            break
        schluessel = _normal_schluessel(m.group(1))
        felder.setdefault(schluessel, m.group(2).strip())
        i += 1
    rest = zeilen[i:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    while rest and not rest[-1].strip():
        rest = rest[:-1]
    return felder, "\n".join(rest)


def parse(text: str, kanal: str, absender_id: int, nachricht_id: int,
          zeitpunkt: str | datetime, betreff: str | None = None) -> Ergebnis:
    bezug = zeitpunkt if isinstance(zeitpunkt, datetime) else zeit_lesen(zeitpunkt)
    zeilen = (text or "").replace("\r\n", "\n").split("\n")
    while zeilen and not zeilen[0].strip():
        zeilen = zeilen[1:]

    kopf = KOPF_RE.match(zeilen[0]) if zeilen else None
    if kopf:
        rumpf = zeilen[1:]
    elif kanal == "pn" and betreff and KOPF_RE.match(betreff):
        kopf = KOPF_RE.match(betreff)
        rumpf = zeilen
    else:
        return Ergebnis(None, "kein_kopf")

    typ = _normal_typ(kopf.group(1))
    version = int(kopf.group(2)) if kopf.group(2) else 1
    if typ not in BEKANNTE_TYPEN:
        return Ergebnis(None, "unbekannter_typ")
    if version != HAUPTVERSION:
        return Ergebnis(None, "andere_version")
    if typ not in KANAL_TYPEN.get(kanal, set()):
        return Ergebnis(None, "falscher_kanal")

    roh, freitext = _zerlegen(rumpf)
    felder: dict = {}
    for name, (fmt, pflicht) in FORMATE[typ].items():
        wert = roh.get(name, "")
        if wert == "":
            if pflicht:
                return Ergebnis(None, "pflichtfeld_fehlt")
            continue
        try:
            felder[name] = fmt(wert, bezug) if fmt is _zeit else fmt(wert)
        except Ungueltig:
            if pflicht:
                return Ergebnis(None, "ungueltiger_wert")

    # --- Zusatzregeln je Typ
    if typ == "NOTRUF" and "vorgang" not in felder:
        if kanal == "pn":
            return Ergebnis(None, "pflichtfeld_fehlt")
        felder["vorgang"] = f"{absender_id}-f{nachricht_id}"
    if typ == "ANFRAGE" and "vorgang" not in felder:
        felder["vorgang"] = f"{absender_id}-f{nachricht_id}"

    if typ in GENAU_EINE_LADUNG:
        hat_e, hat_r = "einheiten" in roh and roh["einheiten"] != "", "rohstoffe" in roh and roh["rohstoffe"] != ""
        if hat_e and hat_r:
            return Ergebnis(None, "ungueltiger_wert")
        if not hat_e and not hat_r:
            return Ergebnis(None, "pflichtfeld_fehlt")
        # einheiten/rohstoffe sind hier Pflicht: ungueltig heisst Nachricht weg.
        if hat_e and "einheiten" not in felder or hat_r and "rohstoffe" not in felder:
            return Ergebnis(None, "ungueltiger_wert")
        if hat_e:
            if roh.get("leihe", "") == "":
                return Ergebnis(None, "pflichtfeld_fehlt")
            if "leihe" not in felder or not set(felder["leihe"]) <= set(felder["einheiten"]):
                return Ergebnis(None, "ungueltiger_wert")
        else:
            felder.pop("leihe", None)

    if typ == "ZUSAGE" and "einheiten" in felder and "rohstoffe" in felder:
        return Ergebnis(None, "ungueltiger_wert")

    if typ == "ANGEBOT" and "gueltig_bis" not in felder:
        felder["gueltig_bis"] = zeit_normal(bezug + ANGEBOT_GUELTIG)

    regel = ABSENDER_REGEL[typ]
    if regel is not None:
        ist_eigentuemer = eigentuemer(felder["vorgang"]) == absender_id
        if (regel == "eigentuemer") != ist_eigentuemer:
            return Ergebnis(None, "falscher_absender")

    return Ergebnis({"typ": typ, "version": version, "felder": felder, "freitext": freitext})


# --------------------------------------------------------------- Schreiben
def _wert_text(wert) -> str:
    if isinstance(wert, dict):
        return " ".join(f"{k}={int(v)}" for k, v in wert.items()) if wert else "keine"
    if isinstance(wert, (list, tuple)):
        return " ".join(wert) if wert else "keine"
    if isinstance(wert, datetime):
        return zeit_normal(wert)
    return str(wert)


def bauen(typ: str, felder: dict, freitext: str = "") -> str:
    """Protokoll-Nachricht als Text. Felder mit None werden weggelassen."""
    zeilen = [f"[{typ}/{HAUPTVERSION}]"]
    zeilen += [f"{k}: {_wert_text(v)}" for k, v in felder.items() if v is not None]
    text = "\n".join(zeilen)
    if freitext:
        text += "\n\n" + freitext
    return text


def verstoesst_gegen_linksperre(text: str) -> bool:
    return bool(LINKSPERRE_RE.search(text))
