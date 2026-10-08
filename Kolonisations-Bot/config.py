"""Feste Einstellungen des Kolonisations-Bots.

Was sich im laufenden Betrieb ueber den Seekampf-Hub aendern laesst (an/aus,
die Warteschlange), steht in data/state.json; hier nur Konstanten.
"""
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_BASE_URL = "https://seekampf.de/api/v1"
API_KEY = os.environ.get("SEEKAMPF_API_KEY", "")

TIMEZONE_NAME = "Europe/Berlin"

# JSON-Schnittstelle fuer den Seekampf-Hub (reicht /api/kolonie/... hierher durch).
WEB_HOST = os.environ.get("KOLONISATIONS_BOT_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("KOLONISATIONS_BOT_PORT", "8082"))

STATE_PATH = os.path.join(BASE_DIR, "data", "state.json")
# Rohstoffe, die eine Bau-Insel fuer ihr Kolonisationsschiff anspart. Upgrade-Bot,
# Rohstoff-Ausgleich (Flotten-Manager) und Ausbildungs-Bot geben dort nur aus,
# was darueber liegt. Ist die Datei aelter als RESERVE_MAX_ALTER_S (Bot aus),
# ignorieren die anderen sie.
RESERVE_PATH = os.path.join(BASE_DIR, "data", "reserve.json")
RESERVE_MAX_ALTER_S = 10 * 60
# Die Begleitung (weitere Schiffe/Truppen) eines bewohnten Ziels wird erst so lange
# vor Fertigstellung des Kolonisationsschiffs reserviert (dann bis zur Abfahrt).
BEGLEITUNG_VORLAUF_S = 10 * 60

LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE_PREFIX = "kolonie"
LOG_RETENTION_DAYS = 60
LOG_TO_STDOUT = True

RESOURCE_KEYS = ("gold", "stein", "holz")

SCHIFF = "kolonisationsschiff"
# Fallback, falls der Ausbildungs-Katalog nicht gelesen werden kann
# (Werte aus GET /islands/{id}/training, Stand 04.10.2026).
SCHIFF_AB_HAFEN = 20
SCHIFF_KOSTEN = {"gold": 19525, "stein": 4260, "holz": 35500}

# --- Spielkonstanten fuer geo.py (wie im Flotten-Manager) ---
SEKTOR_KANTE = 5
MIN_FAHRZEIT_S = 300
MIN_FAHRZEIT_SELBER_SEKTOR_S = 120
SCHIFF_KNOTEN = {"kolonisationsschiff": 3}
SCHIFF_LADEVOLUMEN: dict[str, int] = {}

# Takt der Bot-Schleife.
TICK_S = 30
# Wie oft die wartenden Ziele auf einen neuen Besitzer geprueft werden.
ZIEL_PRUEF_INTERVALL_S = 5 * 60
# Unterwegs: solange das Schiff zurueckgerufen werden kann, so oft pruefen ...
UNTERWEGS_PRUEF_INTERVALL_S = 60
# ... und so viele Sekunden vor Ende der Rueckrufmoeglichkeit ein letztes Mal.
UNTERWEGS_PRUEF_VORLAUF_S = 30
# So viele abgeschlossene Eintraege bleiben im Verlauf.
VERLAUF_MERKEN = 50

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
