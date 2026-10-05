"""Feste Einstellungen und Standardwerte des Flotten-Managers.

Alles, was sich im laufenden Betrieb ueber die Weboberflaeche aendern laesst,
steht in DEFAULT_SETTINGS und wird beim ersten Start nach data/state.json
kopiert - ab dann gilt der Wert aus der Datei. Was hier direkt als Konstante
steht, aendert sich nur durch einen Neustart.
"""
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_BASE_URL = "https://seekampf.de/api/v1"
API_KEY = os.environ.get("SEEKAMPF_API_KEY", "")

TIMEZONE_NAME = "Europe/Berlin"

# Schnittstelle fuer den Seekampf-Hub (~/Seekampf/Seekampf-Hub), der die
# Weboberflaeche im Heimnetz ausliefert und Anfragen hierher weiterreicht.
# Deshalb nur lokal erreichbar.
WEB_HOST = os.environ.get("FLOTTEN_MANAGER_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("FLOTTEN_MANAGER_PORT", "8081"))

STATE_PATH = os.path.join(BASE_DIR, "data", "state.json")
# Der Rohstoff-Ausgleich liest hier, was der Upgrade-Bot als Naechstes bauen
# will und was ihm dafuer fehlt.
UPGRADE_STATUS_PATH = os.path.join(os.path.dirname(BASE_DIR), "Upgrade-Bot", "data", "status.json")
# Rohstoffe, die der Kolonisations-Bot auf einer Insel fuer ein
# Kolonisationsschiff anspart: der Rohstoff-Ausgleich gibt dort nur ab, was
# darueber liegt.
KOLO_RESERVE_PATH = os.path.join(os.path.dirname(BASE_DIR), "Kolonisations-Bot", "data", "reserve.json")
# Kriegsschiffe, die der Ausbildungs-Bot fuer einen Truppentransport braucht:
# sie fahren nicht zum Raiden raus.
AUSBILDUNG_RESERVE_PATH = os.path.join(os.path.dirname(BASE_DIR), "Ausbildungs-Bot", "data", "reserve.json")
# Aeltere Reservierungen (Bot aus) zaehlen nicht.
RESERVE_MAX_ALTER_S = 10 * 60
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE_PREFIX = "flotte"
LOG_RETENTION_DAYS = 60
LOG_TO_STDOUT = True

RESOURCE_KEYS = ("gold", "stein", "holz")

# --- Spielkonstanten (aus https://seekampf.de/legal/regeln, Referenztabellen) ---
# Ein Sektor ist ein 5x5-Raster; die Inselnummer z (1..25) ist der Platz darin.
SEKTOR_KANTE = 5
# Fahrzeit = Distanz in Feldern / Knoten * 10 Minuten, aber nie unter dieser
# Untergrenze - 2 Minuten innerhalb desselben Sektors, sonst 5.
MIN_FAHRZEIT_S = 300
MIN_FAHRZEIT_SELBER_SEKTOR_S = 120
# Knoten je Schiffstyp. Eine Flotte faehrt so schnell wie ihr langsamstes Schiff.
SCHIFF_KNOTEN = {
    "spaehschiff": 20, "kleines_kriegsschiff": 12, "grosses_kriegsschiff": 8,
    "kleines_handelsschiff": 10, "grosses_handelsschiff": 7, "kolonisationsschiff": 3,
}
# Rohstoff-Kapazitaet je Schiff - begrenzt, wie viel Beute mitkommt.
SCHIFF_LADEVOLUMEN = {
    "kleines_handelsschiff": 75, "grosses_handelsschiff": 460,
}
# Welche Typen als Kriegs- bzw. Handelsschiff zaehlen. Die Einstellungen geben
# nur vor, WIE VIELE Schiffe je Klasse mitfahren - welcher Typ das konkret ist,
# entscheidet der Bestand daheim. Frueher stand der Typ fest, dann blieb ein
# kleines Kriegsschiff liegen, weil kein kleines Handelsschiff mehr da war,
# obwohl ein grosses im Hafen lag.
# Die Reihenfolge ist die Vorliebe: Handelsschiffe nach Ladevolumen (mehr Beute
# je Fahrt wiegt die geringere Geschwindigkeit auf), Kriegsschiffe nach
# Geschwindigkeit (sie tragen nichts, bremsen die Flotte aber).
# Bewusst nicht dabei: Spaehschiff (kaempft nicht) und Kolonisationsschiff.
KRIEGSSCHIFF_TYPEN = ("kleines_kriegsschiff", "grosses_kriegsschiff")
HANDELSSCHIFF_TYPEN = ("grosses_handelsschiff", "kleines_handelsschiff")

# Telegram-Morgenreport (Token/Chat-ID wie beim Upgrade-Bot in die .env)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Ueber die Weboberflaeche aenderbar; die Datei data/state.json gewinnt.
DEFAULT_SETTINGS = {
    # --- Suchbereich ---
    # 0 = nur der eigene Sektor, 1 = 3x3 Sektoren, 2 = 5x5 ...
    "scan_radius_sektoren": 1,
    # Ein Scan kostet bei Radius 3 nur 9 Abfragen - stuendlich haelt die
    # Zielliste aktuell, wenn ringsum gerade viel besiedelt wird.
    "scan_intervall_stunden": 1,

    # --- Flottenzusammensetzung (je losgeschickter Flotte) ---
    "flotte_kriegsschiffe": 1,
    "flotte_handelsschiffe": 1,
    # False = grosse Handelsschiffe bleiben im Hafen, es fahren nur kleine.
    "grosse_handelsschiffe": True,
    "flotte_einheiten": 1,
    # Der Schiffstyp steht bewusst NICHT hier: es faehrt, was da ist
    # (siehe KRIEGSSCHIFF_TYPEN / HANDELSSCHIFF_TYPEN).
    "flotte_einheit_typ": "steinewerfer",
    # Je Insel. 0 = keine Obergrenze: es fahren so viele Flotten, wie Schiffe da sind.
    "max_flotten": 0,

    # --- Angriff ---
    "razzia_ziel": "haupthaus",
    # "knappster" | "gleichmaessig" | "gold" | "stein" | "holz"
    "rohstoff_modus": "knappster",
    # Liegen alle drei Bestaende naeher als dieser Anteil der Lagerkapazitaet
    # beieinander, lohnt die Priorisierung nicht - dann gleichmaessig pluendern
    # und dafuer die volle Ladung mitnehmen.
    "ausgleich_schwelle": 0.10,

    # --- Pausen & Sicherheit ---
    "lager_voll_schwelle": 0.95,
    "rueckruf_bei_bedrohung": True,
    "niederlagen_bis_blacklist": 2,
    "blacklist_tage": 3,

    # --- Ziel unterwegs pruefen ---
    # Solange eine Raid-Flotte noch zurueckgerufen werden kann, wird ihr Ziel so
    # oft geprueft; hat es inzwischen einen Besitzer, wird sie zurueckgerufen.
    "pruef_intervall_s": 60,
    # So viele Sekunden vor dem Ende der Rueckrufmoeglichkeit wird ein letztes
    # Mal geprueft.
    "pruef_vorlauf_s": 30,

    # --- Postfach ---
    # Blendet den Kampfbericht einer selbst losgeschickten Flotte aus, sobald
    # er verbucht ist - nur Siege auf Inseln, die noch frei waren. Niederlagen,
    # Angriffe auf inzwischen besiedelte Inseln, Berichte zu von Hand
    # gestarteten Angriffen und alles andere im Postfach bleiben stehen.
    "berichte_archivieren": True,

    # --- Rohstoff-Ausgleich zwischen den eigenen Inseln (siehe ausgleich.py) ---
    "ausgleich_aktiv": False,
    # Empfaenger: jeden Rohstoff bis zu diesem Anteil der Lagerkapazitaet auffuellen.
    "ausgleich_ziel": 0.5,
    # Spender: so viel je Rohstoff behalten (Anteil der eigenen Lagerkapazitaet).
    "ausgleich_reserve": 0.25,
    # Kleinste Lieferung - verhindert Mini-Fahrten fuer ein paar Rohstoffe.
    "ausgleich_min_menge": 300,
    # Fehlt dem Upgrade-Bot fuer den naechsten Ausbau mehr, wird das mitgeliefert.
    "ausgleich_upgrade_bedarf": True,

    # --- Takt ---
    "tick_sekunden": 5,

    # --- Telegram ---
    "telegram_report": True,
    "report_stunde": 7,
}

# Diese Einstellungen lassen sich je Insel abweichend setzen (Seekampf-Hub,
# POST /api/inseln/{id}/einstellungen). Was eine Insel nicht selbst setzt,
# kommt aus DEFAULT_SETTINGS bzw. den globalen Einstellungen.
INSEL_EINSTELLUNGEN = (
    "scan_radius_sektoren", "scan_intervall_stunden",
    "flotte_kriegsschiffe", "flotte_handelsschiffe", "grosse_handelsschiffe",
    "flotte_einheiten", "flotte_einheit_typ", "max_flotten",
    "razzia_ziel", "rohstoff_modus", "ausgleich_schwelle",
    "lager_voll_schwelle", "rueckruf_bei_bedrohung",
    "niederlagen_bis_blacklist", "blacklist_tage",
)
