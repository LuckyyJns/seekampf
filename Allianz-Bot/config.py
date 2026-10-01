"""Feste Einstellungen des Allianz-Bots.

Protokoll-Konstanten stammen aus agenten.md (Allianz-Protokoll V1), die
Spielwerte aus https://seekampf.de/legal/regeln (Referenztabellen). Die
Strategie-Werte weiter unten hat Jannis am 28.09.2026 so festgelegt.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_BASE_URL = "https://seekampf.de/api/v1"
API_KEY = os.environ.get("SEEKAMPF_API_KEY", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TIMEZONE_NAME = "Europe/Berlin"
STATE_PATH = os.path.join(BASE_DIR, "data", "state.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE_PREFIX = "allianz"
LOG_RETENTION_DAYS = 60
LOG_TO_STDOUT = True

# --- Protokoll (agenten.md) ----------------------------------------------
BOT_NAME = "Jannis-Bot"
NEBENVERSION = 0
# rohstoffhilfe bewusst NICHT: der Bot gibt keine Rohstoffe her.
FAEHIGKEITEN = ["notruf", "beistand", "leihe_rueckgabe", "anfrage"]

THREADS = {
    "forum:notrufe": "Allianz-Protokoll: Notrufe",
    "forum:rohstoffe": "Allianz-Protokoll: Rohstoffe",
    "forum:teilnehmer": "Allianz-Protokoll: Teilnehmer",
}

VERSAND_FRIST = timedelta(minutes=10)
RUECKGABE_FRIST = timedelta(hours=6)
NOTRUF_NACH_ANKUNFT = timedelta(hours=12)
NOTRUF_OHNE_ANKUNFT = timedelta(hours=24)
ANFRAGE_OHNE_BIS = timedelta(hours=24)
AUFRAEUMEN_SPAETESTENS = timedelta(days=7)
# [ERLEDIGT]/[ENTWARNUNG] so lange stehen lassen, dass jeder Bot sie beim
# naechsten Lesen (ABFRAGE_INTERVALL <= 10 min) sieht; danach wird der Vorgang geloescht.
ABSCHLUSS_STEHEN_LASSEN = timedelta(minutes=20)
PRAESENZ_INTERVALL = timedelta(days=7)
INAKTIV_NACH = timedelta(days=8)
MITGLIEDER_MAX_ALTER = timedelta(hours=1)

# --- Takt ---------------------------------------------------------------
TICK_S = 15
INCOMING_INTERVALL_S = 30       # eigene Insel auf Angriffe pruefen
PN_INTERVALL_S = 60             # Postfach
FORUM_INTERVALL_S = 300         # Protokoll-Threads (Pflicht: <= 10 min)
MITGLIEDER_INTERVALL_S = 1800   # Mitgliederliste (Pflicht: <= 1 h)
ANFRAGE_INTERVALL_S = 300       # Lagerstand fuer Rohstoff-Anfragen

# --- Strategie (Vorgaben von Jannis) --------------------------------------
# Helfer: nur Speerkaempfer, hoechstens die Haelfte der eigenen, immer als Leihe.
HILFE_EINHEIT = "speerkaempfer"
HILFE_MAX_ANTEIL = 0.5
ANGEBOT_GUELTIG = timedelta(minutes=15)
# Verteidiger: Bedarf so, dass mindestens so viele Speerkaempfer auf der Insel
# stehen - bei hoeherer geschaetzter Angriffsstaerke entsprechend mehr.
MIN_SPEERKAEMPFER = 120
# Staerke-Schaetzung: aus frueheren Kampfberichten gegen denselben Angreifer
# (hoechste Land-Angriffsstaerke mal Faktor), sonst aus seinen Punkten.
BERICHT_SICHERHEITSFAKTOR = 1.5
PUNKTE_ANGRIFFSFAKTOR = 1.0
# Leihen werden nur fuer diese Einheiten angenommen; Steinewerfer nur als
# Geschenk, weil der Flotten-Manager sie sonst zum Raiden mitnimmt.
LEIHE_ERLAUBT = ("speerkaempfer", "bogenschuetze")
# Leih-Angebote erst zusagen, wenn so lange keine Geschenke den Bedarf gedeckt
# haben - ausser der Angriff steht kurz bevor.
LEIHE_WARTEZEIT = timedelta(minutes=4)
LEIHE_SOFORT_WENN_ANGRIFF_IN = timedelta(minutes=30)
# Fehlen fuer Beistand oder Leihe-Rueckgabe Kriegsschiffe, duerfen Raid-Flotten
# (mission attack, noch in Rueckruf-Reichweite) zurueckgerufen werden. Waehrend
# der Bot auf sie wartet, tickt er schneller, damit er die heimkehrenden
# Schiffe vor dem Flotten-Manager (5-s-Takt) greift.
RUECKRUF_ERLAUBT = True
EIL_TICK_S = 2
# Rohstoffe: nie hergeben; anfragen unter 10 %, auffuellen bis 25 % der Lagerkapazitaet.
ANFRAGE_SCHWELLE = 0.10
ANFRAGE_ZIEL = 0.25

# --- Spielwerte (legal/regeln, Referenztabellen) -----------------------------
EINHEIT_ANGRIFF = {"steinewerfer": 5, "speerkaempfer": 6, "bogenschuetze": 20}
EINHEIT_VERTEIDIGUNG = {"steinewerfer": 3, "speerkaempfer": 22, "bogenschuetze": 8}
BASIS_INSELVERTEIDIGUNG = 50
MAUER_BONUS_JE_STUFE = 0.05
# Truppen fahren auf Kriegsschiffen (Kaempfer-Kapazitaet); Reihenfolge = Vorliebe.
TRUPPEN_SCHIFFE = {"kleines_kriegsschiff": 8, "grosses_kriegsschiff": 30}
SCHIFF_KNOTEN = {
    "spaehschiff": 20, "kleines_kriegsschiff": 12, "grosses_kriegsschiff": 8,
    "kleines_handelsschiff": 10, "grosses_handelsschiff": 7, "kolonisationsschiff": 3,
}
SEKTOR_KANTE = 5
MIN_FAHRZEIT_S = 300
MIN_FAHRZEIT_SELBER_SEKTOR_S = 120
