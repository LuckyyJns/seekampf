# Seekampf

Bots fuer [Seekampf.de](https://seekampf.de). Jeder Bot liegt in einem eigenen
Unterordner mit eigener `.env`, eigener virtueller Umgebung und eigener
systemd-Unit, damit sie sich gegenseitig nicht stoeren und einzeln gestartet
und gestoppt werden koennen.

| Ordner | Dienst | Aufgabe |
| --- | --- | --- |
| [`Ressourcen-Bot/`](Ressourcen-Bot/) | `seekampf-ressourcen-bot.service` | Haelt die Bau-Warteschlange gefuellt und verplant die Rohstoffe nach Ertrag pro Kosten. |
| [`Flotten-Manager/`](Flotten-Manager/) | – | Noch nicht begonnen. |

Der Raspberry Pi laeuft als Dauerbetrieb-Host; die Einrichtung dort beschreibt
[`Ressourcen-Bot/SETUP-PI.md`](Ressourcen-Bot/SETUP-PI.md).

## Ueberblick

```bash
systemctl status 'seekampf-*'            # Was laeuft gerade?
journalctl -u seekampf-ressourcen-bot -f # Live-Log eines Bots
```
