#!/usr/bin/env bash
# Sucht in dem, was gleich ins (oeffentliche) Repo geht, nach Geheimnissen.
#
#   ./pruefe_geheimnisse.sh               vorgemerkte Aenderungen (git diff --cached)
#   ./pruefe_geheimnisse.sh origin/master noch nicht gepushte Commits ab dort
#
# Geprueft werden nur hinzugefuegte Zeilen und Dateinamen:
#   - .env-Dateien, data/ und logs/ duerfen nie ins Repo,
#   - die echten Werte aus allen .env-Dateien (API-Schluessel, Telegram-Token,
#     Chat-ID) duerfen nirgends auftauchen,
#   - typische Schluesselmuster (sk_live_..., Telegram-Bot-Token, private Schluessel).
# Exit 1 und eine Liste der Funde (ohne die Werte selbst), sonst Exit 0.
# Laeuft im pre-commit-Hook und in sicherung.sh vor Commit und Push.
set -uo pipefail
cd "$(dirname "$0")"

if [ $# -gt 0 ]; then
  bereich="$1..HEAD"
  dateien=$(git diff --name-only --diff-filter=AM "$bereich")
  zeilen=$(git diff -U0 --no-color "$bereich" | grep '^+' | grep -v '^+++')
else
  dateien=$(git diff --cached --name-only --diff-filter=AM)
  zeilen=$(git diff --cached -U0 --no-color | grep '^+' | grep -v '^+++')
fi

funde=()

while IFS= read -r datei; do
  [ -n "$datei" ] || continue
  case "$datei" in
    *.env.example) ;;
    .env|*/.env|.env.*|*/.env.*) funde+=("Datei $datei (Zugangsdaten)") ;;
    Upgrade-Bot/data/gamedata.json) ;;
    */data/*|*/logs/*) funde+=("Datei $datei (Laufzeitdaten/Logs)") ;;
  esac
done <<< "$dateien"

# Echte Werte aus allen .env-Dateien (nur ausreichend lange, damit nichts
# Zufaelliges anschlaegt).
werte=$(mktemp)
trap 'rm -f "$werte"' EXIT
for env in */.env; do
  [ -f "$env" ] || continue
  sed -n 's/^[A-Z_][A-Z0-9_]*=//p' "$env" | sed 's/^["'\'']//; s/["'\'']$//; s/[[:space:]]*$//' \
    | awk 'length($0) >= 8' >> "$werte"
done
if [ -s "$werte" ] && printf '%s\n' "$zeilen" | grep -qF -f "$werte"; then
  funde+=("ein echter Wert aus einer .env-Datei")
fi

if printf '%s\n' "$zeilen" | grep -qE 'sk_live_[A-Za-z0-9]{8,}'; then
  funde+=("ein Seekampf-API-Schluessel (sk_live_...)")
fi
if printf '%s\n' "$zeilen" | grep -qE '[0-9]{8,10}:[A-Za-z0-9_-]{35}'; then
  funde+=("ein Telegram-Bot-Token")
fi
if printf '%s\n' "$zeilen" | grep -q -- '-----BEGIN [A-Z ]*PRIVATE KEY-----'; then
  funde+=("ein privater Schluessel")
fi

if [ ${#funde[@]} -gt 0 ]; then
  echo "GEHEIMNIS-PRUEFUNG: nicht ins Repo - gefunden:"
  printf '  - %s\n' "${funde[@]}"
  exit 1
fi
exit 0
