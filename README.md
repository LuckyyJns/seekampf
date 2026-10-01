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
| [`Seekampf-Hub/`](Seekampf-Hub/) | `seekampf-hub.service` | Gemeinsame Weboberflaeche fuer alle Bots (http://<pi>:8080): Status, Steuerung, Logs, Dienste starten/stoppen, Rohstoff-Ausgleich, Gesundheit. |

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

## Sicherung

[`sicherung.sh`](sicherung.sh) packt jede Nacht um 3:30 (crontab) Code,
Einstellungen, Zustaende und `.env`-Dateien nach
`~/Seekampf-Sicherungen/seekampf-JJJJ-MM-TT.tar.gz` (ohne Logs und `.venv`,
Rechte 600, die letzten 7 bleiben). Schlaegt sie fehl, kommt eine
Telegram-Nachricht. Zurueckspielen:

```bash
sudo systemctl stop seekampf-flotten-manager seekampf-upgrade-bot seekampf-allianz-bot
tar -xzf ~/Seekampf-Sicherungen/seekampf-JJJJ-MM-TT.tar.gz -C ~      # ueberschreibt ~/Seekampf
(cd ~/Seekampf/<Bot> && uv sync)                                      # nur nach Kartentausch
sudo systemctl start seekampf-flotten-manager seekampf-upgrade-bot seekampf-allianz-bot
```
