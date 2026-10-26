"""Gemeinsamer Code aller Seekampf-Bots.

Jeder Bot bindet das Paket ueber seine pyproject.toml als lokale, editierbare
Abhaengigkeit ein (`seekampf-gemeinsam = { path = "../gemeinsam", editable = true }`)
- eine Aenderung hier gilt nach dem Neustart des jeweiligen Dienstes sofort.

| Modul | Inhalt |
| --- | --- |
| `api` | Session, Wiederholungen (429, GET bei 5xx), ApiError, Serverzeit |
| `telegram` | TelegramNotifier (HTML, lange Texte in mehreren Nachrichten) |
| `logs` | eine Logdatei pro Tag, Journal nur Warnungen/Fehler |
| `spiel` | feste Spielwerte (Sektorkante, Knoten, Ladevolumen, Mindestfahrzeit) |
| `geo` | Feldkoordinaten, Entfernung, Fahrzeit |
| `forschung` | Forschungsstand je Insel (Labor): Segel -> Knoten, Engineering -> Bauzeit |
| `reservierungen` | gemeinsames Verzeichnis, was welcher Bot auf welcher Insel zurueckhaelt |
| `alarm` | Ausfall-Alarm fuer systemd (nur Standardbibliothek) |
"""
