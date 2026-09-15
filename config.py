import os

from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = "https://seekampf.de/api/v1"
API_KEY = os.environ.get("SEEKAMPF_API_KEY", "")

TIMEZONE_NAME = "Europe/Berlin"

POLL_INTERVAL_SECONDS = 300
MAX_QUEUE_SLOTS = 3
MAX_UPGRADES_PER_TICK = 3

# Die drei Rohstoffe, wie sie ResourcesOut (verifiziert gegen die echte
# OpenAPI-Spezifikation von seekampf.de) liefert. Sie teilen sich EINE
# gemeinsame Lagerkapazitaet ("kapazitaet"), es gibt keine getrennten
# Kapazitaeten pro Rohstoff.
RESOURCE_KEYS = ("gold", "stein", "holz")

# Tagsueber (07:00-16:00 Europe/Berlin) wird ein Teil der Kapazitaet als Reserve
# zurueckgehalten (nicht fuer Ausbauten verplant); nachts 0%, alles verbaubar.
# ACHTUNG: bezieht sich auf die Kapazitaet, nicht den aktuellen Bestand - bei
# grosser Kapazitaet kann das knappe Rohstoffe (z.B. Gold) auf 0 nutzbar kappen
# und dann tagsueber alles blockieren (siehe Obsidian-Projektdoku, 2026-09-15).
# Deshalb bewusst niedrig gehalten statt strukturell auf "% vom Bestand" geaendert.
DAY_BUFFER_START_HOUR = 7
DAY_BUFFER_END_HOUR = 16
DAY_BUFFER_PCT = 0.01

# --- Priorisierung (uebernommen aus dem vom Nutzer bereitgestellten
# "bot test"-Skript, siehe planner.py fuer die Kaskade) ---
RESOURCE_BUILDINGS = ("goldmine", "steingrube", "saegewerk")
LOW_PRIORITY_BUILDINGS = ("steinmauer", "wachturm")
TRACKED_BUILDINGS = (*RESOURCE_BUILDINGS, "haupthaus", "lagerhaus", "hafen", "kaserne", *LOW_PRIORITY_BUILDINGS)

# Startgewichte fuer die Kandidaten-Bewertung, bis store.py genug echte
# Produktions-Beobachtungen gelernt hat (siehe planner._resource_score).
PRIORITY_WEIGHTS = {
    "goldmine": 100, "steingrube": 100, "saegewerk": 100,
    "haupthaus": 65, "lagerhaus": 60, "hafen": 55, "kaserne": 30,
    "steinmauer": 5, "wachturm": 5,
}

# Lagerhaus bekommt Vorrang, sobald irgendein anderer Ausbau schon so viel von
# einem Rohstoff kostet, dass er diesen Anteil der Kapazitaet aufbraucht.
STORAGE_TRIGGER_RATIO = 0.60
# Hauptgebaeude wird nur priorisiert, wenn es hoechstens diesen Anteil der
# durchschnittlichen Ressourcen-Gebaeude-Kosten kostet.
HAUPTHAUS_MAX_COST_RATIO = 0.55
# Steinmauer/Wachturm erst, wenn die Warteschlange schon so lange leer ist
# UND nichts Wichtigeres bezahlbar war (2h Standard).
LOW_PRIORITY_DELAY_SECONDS = 7200

# Heuristik zur Erkennung des Lagerhaus-Gebaeudes anhand von typ/name aus
# GET /islands/{id}/buildings (typ ist ein freier String ohne Enum).
WAREHOUSE_BUILDING_KEYWORDS = ("lager", "speicher", "warehouse")

STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "learned.json")

# Telegram-Morgenreport
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
REPORT_HOUR = 7

_override = os.environ.get("SEEKAMPF_ISLAND_IDS_OVERRIDE", "").strip()
MANUAL_ISLAND_IDS = (
    [int(x) for x in _override.split(",") if x.strip()] if _override else []
)

# Falls /construction auch bereits abgeschlossene/abgebrochene Auftraege zurueckgibt
# (statt nur der aktiven Warteschlange), werden diese Status defensiv ausgefiltert.
FINISHED_CONSTRUCTION_STATUSES = {"fertig", "abgeschlossen", "completed", "done", "abgebrochen", "cancelled"}

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
