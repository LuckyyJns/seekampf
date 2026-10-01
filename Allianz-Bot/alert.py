"""Telegram-Alarm fuer systemd.

Wird von seekampf-allianz-bot-alert.service aufgerufen, sobald systemd den
Dienst als fehlgeschlagen markiert - also bei einer Absturzschleife, einem
Fehlstart oder wenn der Prozess wegstirbt. Genau dann kann der Bot selbst
keinen Alarm mehr senden, weil er nicht mehr laeuft; deshalb ein eigener
Einzeiler-Dienst, den systemd anstoesst.

Ausfall heisst hier: keine Notrufe, keine Beistands-Angebote und keine
Leihe-Rueckgaben mehr - und die anderen Bots halten uns nach 8 Tagen ohne
Praesenz fuer inaktiv.

    python alert.py "Text der Meldung"
"""
import subprocess
import sys

import config
from notify import TelegramNotifier

UNIT = "seekampf-allianz-bot.service"


def _letzte_logzeilen(anzahl: int = 8) -> str:
    try:
        return subprocess.run(
            ["journalctl", "-u", UNIT, "-n", str(anzahl), "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> int:
    text = " ".join(sys.argv[1:]) or f"{UNIT} wurde als fehlgeschlagen markiert."
    tail = _letzte_logzeilen()
    if tail:
        text = f"{text}\n\nLetzte Logzeilen:\n{tail}"

    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    if not notifier.aktiv:
        print("Telegram nicht konfiguriert - kein Alarm moeglich.", file=sys.stderr)
        return 1
    return 0 if notifier.send("Allianz-Bot ausgefallen", text) else 1


if __name__ == "__main__":
    sys.exit(main())
