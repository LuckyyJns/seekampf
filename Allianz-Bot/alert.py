"""Telegram-Alarm fuer systemd, wenn seekampf-allianz-bot.service ausfaellt.

Wird von seekampf-allianz-bot-alert.service aufgerufen (OnFailure=). Ausfall heisst:
keine Notrufe, keine Beistands-Angebote und keine
Leihe-Rueckgaben mehr - und die anderen Bots halten uns nach 8 Tagen ohne
Praesenz fuer inaktiv.

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
    sys.exit(ausfall_melden(BOT_DIR, "seekampf-allianz-bot.service", "Allianz-Bot ausgefallen", sys.argv[1:]))
