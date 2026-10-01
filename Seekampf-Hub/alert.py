"""Telegram-Alarm fuer systemd, wenn der Seekampf-Hub ausfaellt.

Wird von seekampf-hub-alert.service aufgerufen (OnFailure= in
seekampf-hub.service). Dann ist die Weboberflaeche weg - die Bots laufen
weiter, lassen sich aber nicht mehr bedienen. Ohne Abhaengigkeiten ausser der
Standardbibliothek, damit der Alarm auch bei kaputter .venv rausgeht.

    python3 alert.py "Text der Meldung"
"""
import os
import subprocess
import sys
import urllib.parse
import urllib.request

UNIT = "seekampf-hub.service"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _env() -> dict:
    werte = {}
    try:
        with open(os.path.join(BASE_DIR, ".env"), encoding="utf-8") as f:
            for zeile in f:
                k, sep, v = zeile.strip().partition("=")
                if sep and not k.startswith("#"):
                    werte[k.strip()] = v.strip()
    except OSError:
        pass
    return werte


def _letzte_logzeilen(anzahl: int = 8) -> str:
    try:
        return subprocess.run(["journalctl", "-u", UNIT, "-n", str(anzahl), "--no-pager", "-o", "cat"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> int:
    env = _env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram nicht konfiguriert - kein Alarm moeglich.", file=sys.stderr)
        return 1
    text = "Seekampf-Hub ausgefallen\n\n" + (" ".join(sys.argv[1:]) or f"{UNIT} wurde als fehlgeschlagen markiert.")
    tail = _letzte_logzeilen()
    if tail:
        text += f"\n\nLetzte Logzeilen:\n{tail}"
    daten = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000]}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=daten, timeout=20)
    except OSError as e:
        print(f"Telegram nicht erreichbar: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
