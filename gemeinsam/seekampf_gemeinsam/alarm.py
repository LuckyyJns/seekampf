"""Telegram-Alarm fuer systemd, gemeinsam fuer alle Bots und den Hub.

Jede `seekampf-*-alert.service` ruft das `alert.py` ihres Bots auf, sobald
systemd den Dienst als fehlgeschlagen markiert (Absturzschleife, Fehlstart,
Prozess weggestorben). Genau dann kann der Bot selbst keinen Alarm mehr
senden; deshalb ein eigener Einzeiler-Dienst.

Nur Standardbibliothek: Der Alarm muss auch rausgehen, wenn die .venv des
Bots kaputt ist. Telegram-Zugang aus der .env des Bots.
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.parse
import urllib.request


def env_lesen(pfad: str) -> dict:
    werte = {}
    try:
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                k, sep, v = zeile.strip().partition("=")
                if sep and not k.startswith("#"):
                    werte[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return werte


def letzte_logzeilen(unit: str, anzahl: int = 8) -> str:
    try:
        return subprocess.run(["journalctl", "-u", unit, "-n", str(anzahl), "--no-pager", "-o", "cat"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def ausfall_melden(bot_ordner: str, unit: str, titel: str, argv: list[str]) -> int:
    """Schickt `titel` + Meldung (argv) + letzte Journalzeilen per Telegram.
    Exit-Code 0 bei Erfolg, sonst 1."""
    env = env_lesen(os.path.join(bot_ordner, ".env"))
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram nicht konfiguriert - kein Alarm moeglich.", file=sys.stderr)
        return 1
    text = f"{titel}\n\n" + (" ".join(argv) or f"{unit} wurde als fehlgeschlagen markiert.")
    tail = letzte_logzeilen(unit)
    if tail:
        text += f"\n\nLetzte Logzeilen:\n{tail}"
    daten = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000]}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=daten, timeout=20)
    except OSError as e:
        print(f"Telegram nicht erreichbar: {e}", file=sys.stderr)
        return 1
    return 0
