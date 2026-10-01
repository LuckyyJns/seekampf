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
| [`Seekampf-Hub/`](Seekampf-Hub/) | `seekampf-hub.service` | Gemeinsame Weboberflaeche fuer alle Bots (http://<pi>:8080): Status, Steuerung, Logs, Dienste starten/stoppen. |

Der Raspberry Pi laeuft als Dauerbetrieb-Host; die Einrichtung dort beschreibt
[`Upgrade-Bot/SETUP-PI.md`](Upgrade-Bot/SETUP-PI.md).

## Ueberblick

```bash
systemctl status 'seekampf-*'            # Was laeuft gerade?
journalctl -u seekampf-upgrade-bot -f # Live-Log eines Bots
```
