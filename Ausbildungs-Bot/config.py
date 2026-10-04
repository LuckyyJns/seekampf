"""Feste Einstellungen des Ausbildungs-Bots.

Die Soll-Truppen je Insel stehen in data/state.json und werden im
Seekampf-Hub gepflegt; hier nur Konstanten.
"""
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_BASE_URL = "https://seekampf.de/api/v1"
API_KEY = os.environ.get("SEEKAMPF_API_KEY", "")

TIMEZONE_NAME = "Europe/Berlin"

# JSON-Schnittstelle fuer den Seekampf-Hub (reicht /api/ausbildung/... hierher durch).
WEB_HOST = os.environ.get("AUSBILDUNGS_BOT_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("AUSBILDUNGS_BOT_PORT", "8083"))

STATE_PATH = os.path.join(BASE_DIR, "data", "state.json")
# Kriegsschiffe, die fuer einen Truppentransport gebraucht werden - der
# Flotten-Manager laesst sie nicht zum Raiden rausfahren.
RESERVE_PATH = os.path.join(BASE_DIR, "data", "reserve.json")
# Rohstoffe, die der Kolonisations-Bot fuer ein Kolonisationsschiff anspart -
# davon wird nichts ausgebildet. Aeltere Dateien (Bot aus) zaehlen nicht.
KOLO_RESERVE_PATH = os.path.join(os.path.dirname(BASE_DIR), "Kolonisations-Bot", "data", "reserve.json")
RESERVE_MAX_ALTER_S = 10 * 60

LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE_PREFIX = "ausbildung"
LOG_RETENTION_DAYS = 60
LOG_TO_STDOUT = True

RESOURCE_KEYS = ("gold", "stein", "holz")

# Einheiten, fuer die sich ein Soll setzen laesst (Kaserne).
EINHEITEN = ("steinewerfer", "speerkaempfer", "bogenschuetze")
# Truppentransport per Handel geht nur auf Kriegsschiffen; Platz je Schiff
# (kaempfer_kap aus dem Ausbildungs-Katalog, das hier ist der Fallback).
KRIEGSSCHIFF_PLATZ = {"grosses_kriegsschiff": 30, "kleines_kriegsschiff": 8}

# --- Spielkonstanten fuer geo.py (wie im Flotten-Manager) ---
SEKTOR_KANTE = 5
MIN_FAHRZEIT_S = 300
MIN_FAHRZEIT_SELBER_SEKTOR_S = 120
SCHIFF_KNOTEN = {"kleines_kriegsschiff": 12, "grosses_kriegsschiff": 8}
SCHIFF_LADEVOLUMEN: dict[str, int] = {}

TICK_S = 60
# So viele Eintraege bleiben in der Liste "Zuletzt".
VERLAUF_MERKEN = 40

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
