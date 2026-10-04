#!/usr/bin/env bash
# Alle Tests aller Seekampf-Bots, jeweils mit der eigenen .venv.
#
#   ./tests.sh            alles
#   ./tests.sh Upgrade-Bot  nur einen Bot
#
# Laeuft automatisch vor jedem git commit (githooks/pre-commit; eingerichtet
# mit `git config core.hooksPath githooks`). Schlaegt ein Test fehl, wird
# nicht committet - und ein Dienst sollte dann auch nicht neu gestartet werden.
set -u
cd "$(dirname "$0")"
bots=("${@:-Flotten-Manager Upgrade-Bot Allianz-Bot Kolonisations-Bot Ausbildungs-Bot Seekampf-Hub}")
fehler=0
for bot in ${bots[@]}; do
  if [ ! -d "$bot/tests" ]; then
    continue
  fi
  if ausgabe=$(cd "$bot" && PYTHONWARNINGS=ignore::DeprecationWarning \
        timeout 300 .venv/bin/python -m unittest discover -s tests 2>&1); then
    echo "ok      $bot: $(echo "$ausgabe" | grep -o 'Ran [0-9]* tests*')"
  else
    echo "FEHLER  $bot"
    echo "$ausgabe" | tail -30 | sed 's/^/        /'
    fehler=1
  fi
done
exit $fehler
