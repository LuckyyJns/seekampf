"""Feste Einstellungen des Ausbildungs-Bots.

Der Standard und die Soll-Werte je Insel (Truppen und Schiffe) stehen in
data/state.json und werden im Seekampf-Hub gepflegt; hier nur Konstanten.
"""
import os

from dotenv import load_dotenv
from seekampf_gemeinsam import reservierungen

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_BASE_URL = "https://seekampf.de/api/v1"
API_KEY = os.environ.get("SEEKAMPF_API_KEY", "")

TIMEZONE_NAME = "Europe/Berlin"

# JSON-Schnittstelle fuer den Seekampf-Hub (reicht /api/ausbildung/... hierher durch).
WEB_HOST = os.environ.get("AUSBILDUNGS_BOT_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("AUSBILDUNGS_BOT_PORT", "8083"))

STATE_PATH = os.path.join(BASE_DIR, "data", "state.json")
# Gemeinsames Reservierungs-Verzeichnis (seekampf_gemeinsam.reservierungen):
# hier stehen die Kriegsschiffe fuer Truppentransporte (der Flotten-Manager
# raidet nicht damit); was andere Bots an Rohstoffen reservieren (z. B. der
# Kolonisations-Bot fuer ein Schiff), wird nicht fuer Ausbildung ausgegeben.
RESERVIERUNGEN_ORDNER = reservierungen.ORDNER

LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE_PREFIX = "ausbildung"
LOG_RETENTION_DAYS = 60
LOG_TO_STDOUT = True

RESOURCE_KEYS = ("gold", "stein", "holz")

# Einheiten, fuer die sich ein Soll setzen laesst (Kaserne).
EINHEITEN = ("steinewerfer", "speerkaempfer", "bogenschuetze")
# Schiffe mit Soll (Hafen). Kolonisationsschiffe fehlen mit Absicht: die baut
# der Kolonisations-Bot.
SCHIFFE = ("spaehschiff", "kleines_handelsschiff", "grosses_handelsschiff",
           "kleines_kriegsschiff", "grosses_kriegsschiff")
ITEMS = EINHEITEN + SCHIFFE
# In welcher Anlage ein Posten ausgebildet bzw. gebaut wird (Feld "facility" der API).
ANLAGE = {**{e: "kaserne" for e in EINHEITEN}, **{s: "hafen" for s in SCHIFFE}}
# Hoechstzahl gleichzeitiger Ausbildungsauftraege je Anlage und Insel (Kaserne, Hafen);
# danach antwortet das Spiel mit 409 queue_full.
MAX_AUFTRAEGE = 5
# Truppentransport per Handel geht nur auf Kriegsschiffen; Platz je Schiff
# (kaempfer_kap aus dem Ausbildungs-Katalog, das hier ist der Fallback).
KRIEGSSCHIFF_PLATZ = {"grosses_kriegsschiff": 30, "kleines_kriegsschiff": 8}

# --- Spielkonstanten (seekampf_gemeinsam/spiel.py, gemeinsam fuer alle Bots) ---
from seekampf_gemeinsam.spiel import (  # noqa: F401 - feste Spielwerte, gemeinsam fuer alle Bots
    MIN_FAHRZEIT_S, MIN_FAHRZEIT_SELBER_SEKTOR_S, SCHIFF_KNOTEN, SCHIFF_LADEVOLUMEN, SEKTOR_KANTE,
)

TICK_S = 60
# Ausbildung/Katalog von Inseln ohne Soll nur so selten neu lesen (nur fuer die Anzeige im Hub).
BEOBACHTUNG_ALTER_S = 120
# So viele Eintraege bleiben in der Liste "Zuletzt".
VERLAUF_MERKEN = 40

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
