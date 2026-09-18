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

# Weboberflaeche: nur im LAN, ohne Login (so gewuenscht). 0.0.0.0 heisst
# "von jedem Geraet im Heimnetz erreichbar" - nach draussen kommt nur, was
# der Router durchreicht, und das tut er ohne Portfreigabe nicht.
WEB_HOST = os.environ.get("FLOTTEN_MANAGER_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("FLOTTEN_MANAGER_PORT", "8080"))

STATE_PATH = os.path.join(BASE_DIR, "data", "state.json")
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

# Telegram-Morgenreport (Token/Chat-ID wie beim Ressourcen-Bot in die .env)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Ueber die Weboberflaeche aenderbar; die Datei data/state.json gewinnt.
DEFAULT_SETTINGS = {
    # --- Suchbereich ---
    # 0 = nur der eigene Sektor, 1 = 3x3 Sektoren, 2 = 5x5 ...
    "scan_radius_sektoren": 1,
    "scan_intervall_stunden": 24,

    # --- Flottenzusammensetzung (je losgeschickter Flotte) ---
    "flotte_kriegsschiffe": 1,
    "flotte_handelsschiffe": 1,
    "flotte_einheiten": 1,
    "flotte_kriegsschiff_typ": "kleines_kriegsschiff",
    "flotte_handelsschiff_typ": "kleines_handelsschiff",
    "flotte_einheit_typ": "steinewerfer",
    # 0 = keine Obergrenze: es fahren so viele Flotten, wie Schiffe da sind.
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

    # --- Takt ---
    "tick_sekunden": 5,

    # --- Telegram ---
    "telegram_report": True,
    "report_stunde": 7,
}
