# Allianz-Bot

Nimmt fuer Lucky_Jns am **Allianz-Protokoll V1** der Allianz CC-AZuBis teil
(Spezifikation: [`agenten.md`](agenten.md), Konformitaetsfaelle:
[`tests/testfaelle.json`](tests/testfaelle.json), beide unveraendert vom
Protokoll-Herausgeber). Eigener Dienst neben Upgrade-Bot und
Flotten-Manager; an denen aendert er nichts.

Praesenz: `bot: Jannis-Bot`, Faehigkeiten `notruf beistand leihe_rueckgabe anfrage`.

**Alle eigenen Inseln** werden ueberwacht - auch neue, sobald sie in
`GET /me/islands/overview` auftauchen (Telegram-Meldung „Neue Insel“). Notrufe
und Rohstoff-Anfragen laufen je Insel als eigener Vorgang; Beistand kommt von
der Insel, die am meisten bereitstellen kann.

## Was er tut

| Rolle | Verhalten |
| --- | --- |
| **Notruf** | Jeder feindliche Anflug auf eine eigene Insel (nicht Handel/Support/Spionage) -> `[NOTRUF]` im Forum + Weck-PN an alle Bots mit `beistand`. Bedarf an Speerkaempfern = groesserer Wert aus „auf mind. 120 auffuellen“ und „geschaetzte Angriffsstaerke decken“. Schaetzung: fruehere Kampfberichte gegen denselben Angreifer (hoechste Land-Angriffsstaerke x 1,5), sonst dessen Punkte x 1,0. |
| **Angebote annehmen** | Geschenke sofort, Leihen erst nach 4 min Wartezeit (ausser der Angriff kommt in < 30 min). Nur was vor dem Angriff ankommt, nur bis der Bedarf gedeckt ist. Steinewerfer nie als Leihe (der Flotten-Manager wuerde sie zum Raiden mitnehmen). |
| **Entwarnung** | Sobald nichts Feindliches mehr anfliegt und die letzte Ankunft vorbei ist, mit Ergebnis aus dem Kampfbericht. |
| **Leihe-Rueckgabe** | Nach Entwarnung: je Art `floor(geliehen x Ueberlebensquote)` laut Kampfberichten per Handel zurueck, notfalls in mehreren Fahrten, dann `[RUECKGABE]`. Fehlen Kriegsschiffe, ruft er Raid-Flotten in Rueckruf-Reichweite zurueck. Wartet, solange ein Angriff anfliegt (Telegram-Warnung 1 h vor Fristende, wenn es nicht klappt). |
| **Beistand** | Nur fuer Mitglieder mit aktiver Bot-Praesenz (Whitelist) und `leihe_rueckgabe`. Nur Speerkaempfer, immer als Leihe, hoechstens 50 % der eigenen je Insel (Verliehenes mitgerechnet, fremde Leihen bei uns nicht). Angeboten wird von der Insel mit der groessten moeglichen Menge, nie von einer bedrohten. Transport auf Kriegsschiffen: aus dem Hafen, sonst werden nach der Zusage Raid-Flotten in Rueckruf-Reichweite zurueckgerufen (im Angebot eingerechnet). Nie, solange die eigene Insel bedroht ist. Zusage -> Versand innerhalb der 10-min-Frist + `[VERSANDT]`; reicht es nicht: was passt, sonst `[ABSAGE] nicht_verfuegbar`. |
| **Rohstoffe** | Gibt nie etwas her. Faellt auf einer Insel ein Rohstoff unter 10 % ihrer Lagerkapazitaet: `[ANFRAGE]` bis 25 %; Angebote werden automatisch bis zur angefragten Menge zugesagt; `[ERLEDIGT]`, sobald alles wieder auf 25 % ist. Je Insel gibt es nur eine offene Anfrage: Neue Rohstoffe (automatisch oder von Hand) kommen dazu, die Anfrage wird mit derselben Vorgangs-ID neu gepostet und die alte geloescht. |

## Raid-Flotten zurueckrufen

Nur Flotten mit `mission: attack` auf dem Hinweg, die von der Insel stammen, auf
der die Schiffe gebraucht werden (Schiffe kehren immer zu ihrer Ausgangsinsel
zurueck), noch in Rueckruf-Reichweite sind und Kriegsschiffe tragen - und nur so viele, wie fuer die Aufgabe fehlen.
Handels- und andere Flotten bleiben unberuehrt. Solange der Bot auf die
heimkehrenden Schiffe wartet, tickt er alle 2 s, damit er sie vor dem
Flotten-Manager (5-s-Takt) greift; gewinnt trotzdem der Flotten-Manager, geht
los, was noch passt. Der Flotten-Manager selbst wird nicht veraendert: eine von
aussen zurueckgerufene Flotte faellt dort hoechstens als „Kein Kampfbericht“ im
Log auf, Ziele werden dadurch nicht gesperrt. Abschaltbar ueber
`RUECKRUF_ERLAUBT` in `config.py`.

## Forum sauber halten

Pro Vorgang steht immer nur der neueste `[ANFRAGE]`- bzw. `[NOTRUF]`-Beitrag;
aeltere desselben Vorgangs loescht der Bot direkt nach dem neuen Beitrag. Ist
ein Vorgang abgeschlossen, verschwindet er ganz: Anfrage/Notruf sofort, die
Abschlussmeldung (`[ERLEDIGT]`/`[ENTWARNUNG]`) nach 20 min
(`ABSCHLUSS_STEHEN_LASSEN` in `config.py`), damit jeder andere Bot sie beim
naechsten Lesen noch sieht. Sonst gilt weiter: nach der Obergrenze, spaetestens
nach 7 Tagen.

## Sicherheit beim Loeschen

Der Bot loescht **nur Forenbeitraege, die er selbst gepostet hat** - und prueft
das dreifach: die ID steht in seiner eigenen Liste (`data/state.json`,
`eigene_beitraege`), der Autor ist live das eigene Konto, und es ist weder der
erste noch der einzige Beitrag des Threads. Der API-Client kennt ausserdem
keine allgemeine Loeschfunktion. PNs loescht er nie.

## Betrieb

```bash
sudo cp seekampf-allianz-bot*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seekampf-allianz-bot

journalctl -u seekampf-allianz-bot -f     # Live-Log
.venv/bin/python -m unittest discover -s tests -v   # Tests (Konformitaet + Ablaeufe)
```

Rohstoffe von Hand anfragen (der laufende Bot postet die Anfrage binnen 15 s,
sagt Angebote automatisch zu und meldet `[ERLEDIGT]`, sobald alles verschickt ist):

```bash
.venv/bin/python anfrage_stellen.py holz=3000                 # aktuelle Insel im Spiel
.venv/bin/python anfrage_stellen.py gold=500 insel=46:55:6    # bestimmte Insel
```

Bequemer geht beides im Seekampf-Hub (http://<pi>:8080, Bereich Allianz-Bot):
Funktionen einzeln an/aus, Anfrage stellen oder beenden, Strategie-Werte.
Eine abgeschaltete Faehigkeit bleibt in der Praesenz, bis ihre laufenden
Vorgaenge fertig sind (das Protokoll verbietet, Ungemeldetes zu senden).

Einstellungen stehen in [`config.py`](config.py) (Abschnitt „Strategie“); was
im Seekampf-Hub gesetzt ist (`data/steuerung.json`), hat Vorrang.
Der Zustand (verarbeitete Nachrichten, eigene Beitraege, Vorgaenge, Leihen)
liegt in `data/state.json`; Logs in `logs/allianz-YYYY-MM-DD.log`.

## Dateien

| Datei | Inhalt |
| --- | --- |
| `protokoll.py` | Parser/Normalisierung nach agenten.md 3-5, Nachrichten bauen, Linksperre |
| `kontext.py` | Identitaet, Mitglieder, Threads, Praesenzen/Whitelist, Senden mit Selbstpruefung, Flottenversand, geschuetztes Loeschen |
| `verteidiger.py` | Notruf, Zusagen, Entwarnung, Leihe-Rueckgabe |
| `helfer.py` | Beistand fuer andere |
| `anfrage.py` | Rohstoff-Anfragen |
| `berichte.py` | Verteidigungs-Kampfberichte, Staerke-Schaetzung, Ueberlebensquote |
| `bot.py` | Takt, Lesen, Verteilen, Praesenz, Aufraeumen |
