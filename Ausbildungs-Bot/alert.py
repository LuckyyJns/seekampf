"""Telegram-Alarm fuer systemd, wenn seekampf-ausbildungs-bot.service ausfaellt.

Wird von seekampf-ausbildungs-bot-alert.service aufgerufen (OnFailure=). Ausfall heisst:
es wird nichts mehr ausgebildet oder verlegt.

Die Arbeit macht seekampf_gemeinsam.alarm - nur Standardbibliothek, damit der
Alarm auch bei kaputter .venv rausgeht (deshalb Import ueber den Pfad, nicht
ueber das installierte Paket).

    python alert.py "Text der Meldung"
"""
import os
import sys

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(BOT_DIR), "gemeinsam"))

from seekampf_gemeinsam.alarm import ausfall_melden  # noqa: E402

if __name__ == "__main__":
    sys.exit(ausfall_melden(BOT_DIR, "seekampf-ausbildungs-bot.service", "Ausbildungs-Bot ausgefallen", sys.argv[1:]))
