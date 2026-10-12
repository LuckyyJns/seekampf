"""Telegram-Alarm fuer systemd, wenn seekampf-upgrade-bot.service ausfaellt.

Wird von seekampf-upgrade-bot-alert.service aufgerufen (OnFailure=). Ausfall heisst:
der Bot baut nichts mehr aus.

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
    sys.exit(ausfall_melden(BOT_DIR, "seekampf-upgrade-bot.service", "Upgrade-Bot ausgefallen", sys.argv[1:]))
