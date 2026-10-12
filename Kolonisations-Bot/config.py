"""Feste Einstellungen des Kolonisations-Bots.

Was sich im laufenden Betrieb ueber den Seekampf-Hub aendern laesst (an/aus,
die Warteschlange), steht in data/state.json; hier nur Konstanten.
"""
import os

from dotenv import load_dotenv
from seekampf_gemeinsam import reservierungen

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_BASE_URL = "https://seekampf.de/api/v1"
API_KEY = os.environ.get("SEEKAMPF_API_KEY", "")

TIMEZONE_NAME = "Europe/Berlin"

# JSON-Schnittstelle fuer den Seekampf-Hub (reicht /api/kolonie/... hierher durch).
WEB_HOST = os.environ.get("KOLONISATIONS_BOT_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("KOLONISATIONS_BOT_PORT", "8082"))

STATE_PATH = os.path.join(BASE_DIR, "data", "state.json")
# Gemeinsames Reservierungs-Verzeichnis (seekampf_gemeinsam.reservierungen):
# Rohstoffe, die eine Bau-Insel fuer ihr Kolonisationsschiff anspart (Upgrade-Bot,
# Rohstoff-Ausgleich, Ausbildungs-Bot und Allianzkasse geben dort nur aus, was
# darueber liegt), und die Begleitung kurz vor der Abfahrt. Laeuft der Bot nicht
# (Datei aelter als 10 min), gilt nichts davon.
RESERVIERUNGEN_ORDNER = reservierungen.ORDNER
# Die Begleitung (weitere Schiffe/Truppen) eines bewohnten Ziels wird erst so lange
# vor Fertigstellung des Kolonisationsschiffs reserviert (dann bis zur Abfahrt).
BEGLEITUNG_VORLAUF_S = 10 * 60
# Bau-Insel erst wechseln, wenn eine andere so viel schneller am Ziel waere
WECHSEL_VORTEIL_S = 15 * 60
SCHIFF_DAUER_S = 6 * 3600   # Rueckfall, falls der Katalog keine Bauzeit nennt

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

# --- Spielkonstanten (seekampf_gemeinsam/spiel.py, gemeinsam fuer alle Bots) ---
from seekampf_gemeinsam.spiel import (  # noqa: F401 - feste Spielwerte, gemeinsam fuer alle Bots
    MIN_FAHRZEIT_S, MIN_FAHRZEIT_SELBER_SEKTOR_S, SCHIFF_KNOTEN, SCHIFF_LADEVOLUMEN, SEKTOR_KANTE,
)

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
