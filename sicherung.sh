#!/usr/bin/env bash
# Naechtliche Sicherung aller Seekampf-Bots.
#
# Packt ~/Seekampf ohne .venv, Logs und Caches nach
# ~/Seekampf-Sicherungen/seekampf-JJJJ-MM-TT.tar.gz: Code, Einstellungen,
# Zustaende (data/) und die .env-Dateien - genug, um nach einem
# Speicherkarten-Tausch mit `uv sync` je Bot wieder loszulegen. Die letzten
# BEHALTEN Sicherungen bleiben, aeltere werden geloescht.
#
# Die Archive enthalten die API-Schluessel: Ordner 700, Dateien 600.
# Faellt die Sicherung aus, kommt eine Telegram-Nachricht (Token aus der
# .env des Seekampf-Hubs). Ergebnis des letzten Laufs: letzter-lauf.json
# (zeigt der Seekampf-Hub unter "Gesundheit").
#
# Eingerichtet per crontab (crontab -l):
#   30 3 * * * /home/jannishoy/Seekampf/sicherung.sh
set -uo pipefail

QUELLE="$HOME/Seekampf"
ZIEL="$HOME/Seekampf-Sicherungen"
BEHALTEN=7
DATEI="$ZIEL/seekampf-$(date +%F).tar.gz"
STATUS="$ZIEL/letzter-lauf.json"

umask 077
mkdir -p "$ZIEL"
chmod 700 "$ZIEL"

melden() {  # $1 = ok (true/false), $2 = Meldung
  printf '{"zeit": %s, "ok": %s, "meldung": "%s"}\n' "$(date +%s)" "$1" "${2//\"/\'}" > "$STATUS"
}

alarm() {
  local token chat
  token=$(grep -s '^TELEGRAM_BOT_TOKEN=' "$QUELLE/Seekampf-Hub/.env" | cut -d= -f2-)
  chat=$(grep -s '^TELEGRAM_CHAT_ID=' "$QUELLE/Seekampf-Hub/.env" | cut -d= -f2-)
  [ -n "$token" ] && [ -n "$chat" ] || return 0
  curl -s -m 20 "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=Seekampf-Sicherung fehlgeschlagen: $1" > /dev/null
}

tmp="$DATEI.tmp"
# Die Bots schreiben waehrend der Sicherung weiter; ihre Zustaende werden
# atomar ersetzt, eine halbe Datei gibt es also nicht. tar meldet so etwas mit
# Exit 1 ("Datei hat sich geaendert") - das ist in Ordnung, erst 2 ist fatal.
fehler=$(tar -czf "$tmp" -C "$HOME" \
    --exclude='Seekampf/*/.venv' --exclude='Seekampf/*/logs' \
    --exclude='__pycache__' --exclude='*.pyc' \
    --warning=no-file-changed --warning=no-file-removed \
    Seekampf 2>&1)
rc=$?
if [ "$rc" -gt 1 ] || [ ! -s "$tmp" ] || ! tar -tzf "$tmp" > /dev/null 2>&1; then
  rm -f "$tmp"
  melden false "tar (Exit $rc): ${fehler:0:200}"
  alarm "tar (Exit $rc): ${fehler:0:300}"
  exit 1
fi
mv -f "$tmp" "$DATEI"

# Alte Sicherungen aufraeumen (nach Name = Datum sortiert).
ls -1 "$ZIEL"/seekampf-*.tar.gz 2>/dev/null | sort | head -n -"$BEHALTEN" | xargs -r rm -f

groesse=$(du -h "$DATEI" | cut -f1)
melden true "$(basename "$DATEI") ($groesse)"
echo "Sicherung $DATEI ($groesse)"
