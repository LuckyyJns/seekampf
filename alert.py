"""Telegram-Alarm fuer systemd.

Wird von seekampf-bot-alert.service aufgerufen, sobald systemd den Bot-Dienst
als fehlgeschlagen markiert (Absturzschleife, Fehlstart, `systemctl stop` nach
Fehler). Greift also auch dann, wenn der Bot selbst gar nicht mehr laeuft und
deshalb keinen eigenen Alarm mehr senden kann.

    python alert.py "Text der Meldung"
"""
import subprocess
import sys

import config
from notify import build_notifier


def _last_log_lines(count: int = 8) -> str:
    try:
        out = subprocess.run(
            ["journalctl", "-u", "seekampf-bot.service", "-n", str(count), "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return out


def main() -> int:
    text = " ".join(sys.argv[1:]) or "seekampf-bot.service wurde als fehlgeschlagen markiert."
    tail = _last_log_lines()
    if tail:
        text = f"{text}\n\nLetzte Logzeilen:\n{tail}"

    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("Telegram nicht konfiguriert - kein Alarm moeglich.", file=sys.stderr)
        return 1

    notifier = build_notifier({
        "service": "telegram",
        "bot_token": config.TELEGRAM_BOT_TOKEN,
        "chat_id": config.TELEGRAM_CHAT_ID,
    })
    return 0 if notifier.send("Seekampf-Bot ausgefallen", text) else 1


if __name__ == "__main__":
    sys.exit(main())
