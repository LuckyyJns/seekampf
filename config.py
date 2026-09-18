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

# --- Priorisierung (uebernommen aus dem vom Nutzer bereitgestellten
# "bot test"-Skript, siehe planner.py fuer die Kaskade) ---
RESOURCE_BUILDINGS = ("goldmine", "steingrube", "saegewerk")
# Hafen und Kaserne teilen sich eine Prioritaetsstufe (nach Lagerhaus und
# Haupthaus, vor Steinmauer/Wachturm). Innerhalb der Stufe entscheidet die
# Gewichtung pro Kosten - frueher stand der Hafen eine Stufe hoeher und lief
# den Wirtschaftsgebaeuden davon (7 Ausbauten allein am 16.09.).
MID_PRIORITY_BUILDINGS = ("hafen", "kaserne")
LOW_PRIORITY_BUILDINGS = ("steinmauer", "wachturm")
TRACKED_BUILDINGS = (*RESOURCE_BUILDINGS, "haupthaus", "lagerhaus", *MID_PRIORITY_BUILDINGS, *LOW_PRIORITY_BUILDINGS)

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
# Alle Gebaeude verteuern sich pro Stufe um denselben Faktor (~1.337, aus
# data/learned.json gemessen), das Haupthaus ist pro Stufe aber rund doppelt so
# teuer wie ein Ressourcen-Gebaeude. Der Schwellwert legt damit fest, wie weit
# das Haupthaus dauerhaft zurueckfaellt: 0.55 hielt es ~3 Stufen zurueck (es kam
# nie dran), 1.0 haelt es ~2 Stufen hinter dem Schnitt - es waechst mit, ohne
# den Ausbau der Wirtschaft zu verdraengen.
HAUPTHAUS_MAX_COST_RATIO = 1.0
# Steinmauer/Wachturm erst, wenn die Warteschlange schon so lange leer ist
# UND die Prioritaets-Kaskade in dieser Zeit nichts Wichtigeres gebaut hat
# (2h Standard). Ein bezahlbares, aber noch nicht notwendiges Lagerhaus haelt
# die Uhr nicht auf - das Lagerhaus kommt dran, sobald STORAGE_TRIGGER_RATIO
# greift.
LOW_PRIORITY_DELAY_SECONDS = 7200

# Heuristik zur Erkennung des Lagerhaus-Gebaeudes anhand von typ/name aus
# GET /islands/{id}/buildings (typ ist ein freier String ohne Enum).
WAREHOUSE_BUILDING_KEYWORDS = ("lager", "speicher", "warehouse")

STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "learned.json")

# Nach so vielen fehlgeschlagenen Durchlaeufen in Folge geht ein Telegram-Alarm
# raus (bei 300s Intervall also nach ~15 Minuten ohne erfolgreichen Tick).
ALERT_AFTER_FAILED_TICKS = 3

# Telegram-Morgenreport
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
REPORT_HOUR = 7
# Der Morgenreport deckt den Zeitraum seit dem letzten gesendeten Report ab.
# Nur falls kein frueherer Report bekannt ist (erster Start), wird auf dieses
# Fenster zurueckgefallen.
REPORT_FALLBACK_WINDOW_HOURS = 24

_override = os.environ.get("SEEKAMPF_ISLAND_IDS_OVERRIDE", "").strip()
MANUAL_ISLAND_IDS = (
    [int(x) for x in _override.split(",") if x.strip()] if _override else []
)

# Falls /construction auch bereits abgeschlossene/abgebrochene Auftraege zurueckgibt
# (statt nur der aktiven Warteschlange), werden diese Status defensiv ausgefiltert.
FINISHED_CONSTRUCTION_STATUSES = {"fertig", "abgeschlossen", "completed", "done", "abgebrochen", "cancelled"}

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
# Pro Kalendertag eine eigene Datei: logs/bot-YYYY-MM-DD.log
LOG_FILE_PREFIX = "bot"
LOG_RETENTION_DAYS = 60
# Zusaetzlich nach stdout (bei systemd sichtbar via journalctl -u seekampf-bot)
LOG_TO_STDOUT = True
