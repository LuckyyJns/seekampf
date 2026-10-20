# Seekampf

Bots fuer [Seekampf.de](https://seekampf.de). Jeder Bot liegt in einem eigenen
Unterordner mit eigener `.env`, eigener virtueller Umgebung und eigener
systemd-Unit, damit sie sich gegenseitig nicht stoeren und einzeln gestartet
und gestoppt werden koennen.

| Ordner | Dienst | Aufgabe |
| --- | --- | --- |
| [`Upgrade-Bot/`](Upgrade-Bot/) | `seekampf-upgrade-bot.service` | Haelt die Bau-Warteschlange gefuellt und verplant die Rohstoffe nach Ertrag pro Kosten. |
| [`Flotten-Manager/`](Flotten-Manager/) | `seekampf-flotten-manager.service` | Raidet die freien Inseln im Umkreis jeder eingeschalteten Insel ab; gesteuert ueber den Seekampf-Hub. |
| [`Allianz-Bot/`](Allianz-Bot/) | `seekampf-allianz-bot.service` | Nimmt am Allianz-Protokoll teil: Notrufe, Beistand mit Speerkaempfern, Leihe-Rueckgabe, Rohstoff-Anfragen. |
| [`Kolonisations-Bot/`](Kolonisations-Bot/) | `seekampf-kolonisations-bot.service` | Kolonisiert freie Inseln aus einer Warteschlange im Hub; baut die Schiffe auf der nächstgelegenen geeigneten Insel. |
| [`Ausbildungs-Bot/`](Ausbildungs-Bot/) | `seekampf-ausbildungs-bot.service` | Hält die Truppen je Insel auf Soll: bildet aus oder verlegt per Handel. |
| [`Seekampf-Hub/`](Seekampf-Hub/) | `seekampf-hub.service` | Gemeinsame Weboberflaeche fuer alle Bots (https://seekampf-hub.com): Status, Steuerung, Logs, Dienste starten/stoppen, Rohstoff-Ausgleich, Gesundheit. |

## Gemeinsamer Code

[`gemeinsam/`](gemeinsam/) (Paket `seekampf_gemeinsam`) enthaelt, was alle
Bots gleich brauchen: API-Kern (Session, Wiederholungen bei 429 und - nur GET -
bei 500/502/503/504, Fehlermeldung mit Methode und Pfad), Telegram, Tages-Logs,
Spielwerte und Fahrzeiten (`geo`), das Reservierungs-Verzeichnis und den
Ausfall-Alarm. Jeder Bot bindet es ueber seine `pyproject.toml` als lokale,
editierbare Abhaengigkeit ein; nach einer Aenderung dort `./tests.sh` und die
betroffenen Dienste neu starten. Endpunkte, API-Schluessel und Einstellungen
bleiben je Bot getrennt.

Der Raspberry Pi laeuft als Dauerbetrieb-Host; die Einrichtung dort beschreibt
[`Upgrade-Bot/SETUP-PI.md`](Upgrade-Bot/SETUP-PI.md).

## Ueberblick

```bash
systemctl status 'seekampf-*'                         # Was laeuft gerade?
tail -f ~/Seekampf/Upgrade-Bot/logs/bot-$(date +%F).log  # Live-Log eines Bots
journalctl -u seekampf-upgrade-bot                    # nur Warnungen, Fehler, Abstuerze
```

Die Bots schreiben ihr vollstaendiges Log in `logs/<name>-JJJJ-MM-TT.log`; ins
Journal gehen nur Warnungen und Fehler (spart Schreibzugriffe auf die SD-Karte).
Ob alles rund laeuft, zeigt der Seekampf-Hub unter **Gesundheit**.

## Tests

```bash
./tests.sh                 # alle Bots (je mit eigener .venv)
./tests.sh Upgrade-Bot     # nur einer
```

Jeder Bot hat seine Tests in `tests/` (gegen eine nachgebaute Spiel-API, ohne
Netz und ohne echten Zustand). `tests.sh` laeuft automatisch vor jedem
`git commit` (Hook in `githooks/`, aktiviert mit
`git config core.hooksPath githooks`); schlaegt etwas fehl, wird nicht
committet. Nach Code-Aenderungen erst `./tests.sh`, dann den Dienst neu starten.

Zusammenspiel mehrerer Bots: `gemeinsam/seekampf_gemeinsam/testwelt.py` baut
eine kleine Spielwelt nach (Inseln, Schiffe, Truppen, Rohstoffe, Flotten,
Ausbildung, Karte), die sich mehrere Bots mit ihren echten API-Clients teilen.
`Flotten-Manager/tests/test_zusammenspiel.py` laesst darin Flotten-Manager,
Ausbildungs- und Kolonisations-Bot gegeneinander laufen - z. B. beide wollen
dasselbe Kriegsschiff, oder eine Antwort geht genau beim Losschicken verloren.
Neue Faelle dieser Art gehoeren dorthin.

## Sicherung

[`sicherung.sh`](sicherung.sh) packt jede Nacht um 3:30 (crontab) Code,
Einstellungen, Zustaende und `.env`-Dateien nach
`~/Seekampf-Sicherungen/seekampf-JJJJ-MM-TT.tar.gz` (ohne Logs und `.venv`,
Rechte 600, die letzten 7 bleiben). Schlaegt sie fehl, kommt eine
Telegram-Nachricht. Zurueckspielen:

```bash
sudo systemctl stop seekampf-flotten-manager seekampf-upgrade-bot seekampf-allianz-bot seekampf-kolonisations-bot seekampf-ausbildungs-bot
tar -xzf ~/Seekampf-Sicherungen/seekampf-JJJJ-MM-TT.tar.gz -C ~      # ueberschreibt ~/Seekampf
(cd ~/Seekampf/<Bot> && uv sync)                                      # nur nach Kartentausch
sudo systemctl start seekampf-flotten-manager seekampf-upgrade-bot seekampf-allianz-bot seekampf-kolonisations-bot seekampf-ausbildungs-bot
```
