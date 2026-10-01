# Allianz-Protokoll V1: Spezifikation für Bot-Implementierungen

Du implementierst einen Bot für das Browserspiel Seekampf, der am Allianz-Protokoll teilnimmt. Dieses Dokument ist normativ. MUSS, SOLL und DARF haben die übliche Bedeutung (RFC 2119). Den Hintergrund für Menschen erklärt [`README.md`](README.md). [`testfaelle.json`](testfaelle.json) ist die Konformitätsprüfung für deinen Parser.

Seekampf-API: `https://seekampf.de/api/v1`, Spec unter `https://seekampf.de/openapi.json` und `https://seekampf.de/legal/api-spec`.

## 1. Begriffe

- **Teilnehmer**: ein Allianzmitglied, dessen Bot eine gültige `[PRAESENZ]` gepostet hat, die höchstens 8 Tage alt ist.
- **Vorgang**: ein Notruf oder eine Anfrage mit allen Nachrichten dazu, erkennbar an der Vorgangs-ID im Feld `vorgang`.
- **Eigentümer** eines Vorgangs: der Spieler, dessen ID vor dem `-` der Vorgangs-ID steht. Der Eigentümer ist Verteidiger (Notruf) oder Anfragender (Anfrage).
- **Helfer**: ein Spieler, der einem Vorgang etwas anbietet.
- **Leihe**: eine Einheitenart, die der Eigentümer nach der Entwarnung zurückgeben muss. Alles Übrige, was ein Helfer schickt, ist **Geschenk**.
- **Entwarnung**: das Ende eines Notrufs, durch `[ENTWARNUNG]` oder spätestens durch die Obergrenze (Abschnitt 8).
- **Absender-ID, Zeitpunkt, Nachrichten-ID**: die Werte, die der Server zu einem Forenbeitrag oder einer PN liefert. Verwende nie deine lokale Uhr und nie einen Namen aus dem Nachrichtentext.

## 2. Kanäle

| Kanal | Ort | Erlaubte Typen |
|---|---|---|
| `forum:notrufe` | Allianz-Thread `Allianz-Protokoll: Notrufe` | `NOTRUF`, `ENTWARNUNG` |
| `forum:rohstoffe` | Allianz-Thread `Allianz-Protokoll: Rohstoffe` | `ANFRAGE`, `ERLEDIGT` |
| `forum:teilnehmer` | Allianz-Thread `Allianz-Protokoll: Teilnehmer` | `PRAESENZ` |
| `pn` | Ingame-PN (`POST /messages`) | `NOTRUF` (nur als Weck-Kopie), `ANGEBOT`, `ZUSAGE`, `ABSAGE`, `VERSANDT`, `RUECKGABE` |

- Finde die Threads über `GET /forum/threads?sichtbarkeit=allianz` am **exakten** Titel. Blättere mit `limit`/`offset`, bis eine Seite weniger Einträge als `limit` liefert. Gibt es mehrere Threads mit demselben Titel, gilt der mit der kleinsten ID.
- Fehlt ein Thread, MUSST du das deinem Operator melden. Du DARFST dann keinen Thread anlegen und nichts in diesen Kanal posten.
- Der erste Beitrag jedes Threads ist ein Kopfbeitrag für Menschen. Er ist keine Protokoll-Nachricht. Du DARFST ihn nie löschen.

## 3. Nachrichtenformat

```abnf
nachricht   = *leerzeile kopfzeile *( feldzeile ) [ freitext ]
kopfzeile   = *WSP "[" typ [ "/" version ] "]" *zeichen NL   ; Text nach "]" wird ignoriert
typ         = 1*( ALPHA / "Ä" / "Ö" / "Ü" / "ä" / "ö" / "ü" / "_" )
version     = 1*DIGIT                                     ; Hauptversion
feldzeile   = *WSP schluessel *WSP ":" *WSP wert NL
schluessel  = 1*( ALPHA / "Ä" / "Ö" / "Ü" / "ä" / "ö" / "ü" / "_" )
wert        = *zeichen                                    ; bis Zeilenende, getrimmt
freitext    = *zeichen-oder-NL                            ; alles ab der ersten Nicht-Feldzeile
```

Parse-Regeln:

1. Ersetze `\r\n` durch `\n`. Überspringe führende Leerzeilen.
2. Die erste Zeile MUSS eine Kopfzeile sein, sonst ist der Text keine Protokoll-Nachricht. Ausnahme bei PN: Beginnt der Text nicht mit einer Kopfzeile, der Betreff aber schon, dann nimm Typ und Version aus dem Betreff und lies den ganzen Text als Feldblock. Ein Betreff wie `Re: [ANGEBOT] …` beginnt nicht mit einer Kopfzeile.
3. Normalisiere Typ und Schlüssel: ersetze `ä ö ü Ä Ö Ü` durch `ae oe ue AE OE UE`. Den Typ schreibst du danach in Großbuchstaben, Schlüssel in Kleinbuchstaben. `[präsenz]` wird zu `PRAESENZ`, `Gültig_Bis` zu `gueltig_bis`.
4. Fehlt die Version, gilt `1`.
5. Der Feldblock endet an der ersten Leerzeile oder an der ersten Zeile, die nicht dem Muster `feldzeile` entspricht. Diese Zeile und alles danach ist Freitext. Entferne führende und abschließende Leerzeilen aus dem Freitext.
6. Kommt ein Schlüssel mehrfach vor, gilt das erste Vorkommen. Ein leerer Wert zählt als fehlendes Feld.
7. Unbekannte Schlüssel MUSST du ignorieren. Sie machen die Nachricht nicht ungültig.
8. Ist ein Kann-Feld ungültig, lass nur dieses Feld weg. Ist ein Pflichtfeld ungültig oder fehlt es, ignoriere die ganze Nachricht.

## 4. Werteformate

| Format | Syntax | Normalform |
|---|---|---|
| `koordinate` | `x:y:z`, drei nicht negative Ganzzahlen | String `"x:y:z"` ohne führende Nullen |
| `zahl` | nur Ziffern, ohne Vorzeichen und Tausenderpunkt | Ganzzahl |
| `zeit` | ISO 8601 `JJJJ-MM-TTTHH:MM[:SS]` mit `Z`, mit `±HH:MM` oder ohne Offset. Statt `T` ist ein Leerzeichen erlaubt. Kurzform `HH:MM` | String `JJJJ-MM-TTTHH:MM:SS±HH:MM` in Europe/Berlin |
| `einheiten` | `name=zahl`, getrennt durch Leerzeichen oder Komma, Namen aus `steinewerfer`, `speerkaempfer`, `bogenschuetze`, oder das Wort `keine` | Objekt `{name: zahl}`, `keine` = `{}` |
| `rohstoffe` | wie `einheiten`, Namen aus `gold`, `stein`, `holz` | Objekt `{name: zahl}` |
| `liste` | Wörter aus `a-z` und `_`, getrennt durch Leerzeichen oder Komma, oder das Wort `keine` | Array in Reihenfolge, `keine` = `[]` |
| `vorgang` | `<spieler_id>-<nr>` oder `<spieler_id>-f<beitrags_id>`, nur Ziffern außer dem `f` | String |
| `text` | beliebiger Rest der Zeile | String |

- `zeit` ohne Offset meint Europe/Berlin. Die Kurzform `HH:MM` meint den nächsten solchen Zeitpunkt in Europe/Berlin, der **nach** dem Zeitpunkt der Nachricht liegt.
- `einheiten` und `rohstoffe` sind ungültig, wenn ein Name unbekannt ist oder doppelt vorkommt.

## 5. Nachrichtentypen

P = Pflicht, K = Kann. Die letzte Spalte legt fest, wer der Absender sein muss. Andernfalls ist die Nachricht ungültig.

| Typ | Kanal | Felder | Absender |
|---|---|---|---|
| `NOTRUF` | `forum:notrufe`, `pn` | `insel` koordinate P · `vorgang` P in `pn`, sonst K · `ankunft` zeit K · `angreifer` text K · `staerke` zahl K · `bedarf` einheiten K | Eigentümer |
| `ENTWARNUNG` | `forum:notrufe` | `vorgang` P · `ergebnis` `gehalten`\|`verloren` K | Eigentümer |
| `ANFRAGE` | `forum:rohstoffe` | `insel` koordinate P · `rohstoffe` P · `vorgang` K · `bis` zeit K | Eigentümer |
| `ERLEDIGT` | `forum:rohstoffe` | `vorgang` P | Eigentümer |
| `ANGEBOT` | `pn` | `vorgang` P · `von` koordinate P · `ankunft` zeit P · genau eines von `einheiten` P / `rohstoffe` P · `leihe` liste P, wenn `einheiten` gesetzt ist · `gueltig_bis` zeit K | nicht Eigentümer |
| `ZUSAGE` | `pn` | `vorgang` P · höchstens eines von `einheiten` K / `rohstoffe` K | Eigentümer |
| `ABSAGE` | `pn` | `vorgang` P · `grund` `abgelaufen`\|`ungueltig`\|`nicht_verfuegbar`\|`zurueckgezogen`\|`abgelehnt` K | beliebig |
| `VERSANDT` | `pn` | `vorgang` P · `von` koordinate P · `ankunft` zeit P · genau eines von `einheiten` P / `rohstoffe` P · `leihe` liste P, wenn `einheiten` gesetzt ist | nicht Eigentümer |
| `RUECKGABE` | `pn` | `vorgang` P · `einheiten` P · `ankunft` zeit K | Eigentümer |
| `PRAESENZ` | `forum:teilnehmer` | `version` zahl P · `nebenversion` zahl P · `faehigkeiten` liste P · `bot` text K | beliebig |

Zusatzregeln beim Normalisieren:

- Setzt ein `NOTRUF` oder eine `ANFRAGE` im Forum keinen `vorgang`, lautet die Vorgangs-ID `<absender_id>-f<nachrichten_id>`.
- Fehlt im `ANGEBOT` das Feld `gueltig_bis`, gilt Zeitpunkt der Nachricht plus `ANGEBOT_GUELTIG`.
- `leihe` MUSS eine Teilmenge der Namen in `einheiten` sein. `leihe: keine` heißt: alles ist Geschenk. Bei `rohstoffe` wird ein `leihe`-Feld ignoriert.
- Die Normalform einer Nachricht enthält nur die Felder aus dieser Tabelle, dazu `vorgang` und `gueltig_bis`, falls sie nach den Regeln oben abgeleitet wurden.

## 6. Prüfreihenfolge für jede eingehende Nachricht

Führe die Schritte in dieser Reihenfolge aus. Scheitert ein Schritt, ignoriere die Nachricht ohne Antwort. Die einzige Ausnahme steht in Abschnitt 7.2.

1. Hast du die Nachrichten-ID schon verarbeitet? Dann ignorieren.
2. Parsen (Abschnitt 3). Ist der Text keine Protokoll-Nachricht, ist der Typ unbekannt oder weicht die Hauptversion von `1` ab, ignorieren.
3. Kanal: Der Typ muss in diesem Kanal erlaubt sein (Abschnitt 2).
4. Absender: Die Absender-ID muss laut `GET /alliances/{id}` ein aktuelles Mitglied deiner Allianz sein. Deine Mitgliederliste darf höchstens 1 h alt sein.
5. Felder und Absender-Regel (Abschnitte 4 und 5).
6. Vorgang: Ignoriere Nachrichten zu einem Vorgang, dessen Obergrenze abgelaufen ist. `VERSANDT` und `RUECKGABE` sind davon ausgenommen.
7. Vertrauen: Automatisch versenden (Truppen oder Rohstoffe) DARFST du nur auf eine `ZUSAGE` von einem Spieler auf deiner lokalen **Whitelist**. Bei allen anderen legst du den Fall deinem Operator zur Entscheidung vor.

Die Konformitätsprüfung in `testfaelle.json` deckt die Schritte 2, 3 und 5 ab.

## 7. Abläufe

### 7.1 Beistand, Rolle Verteidiger (Fähigkeit `notruf`)

1. Bei einem Angriff postest du `[NOTRUF]` in `forum:notrufe` mit eigener Vorgangs-ID `<deine_id>-<laufende_nr>`. Die laufende Nummer ist je Spieler eindeutig und wird persistent gespeichert.
2. Du DARFST den identischen Text zusätzlich als Weck-PN an jeden aktiven Teilnehmer mit Fähigkeit `beistand` schicken, mit Betreff `[NOTRUF] <vorgang>`.
3. Ändern sich Ankunft, Stärke oder Bedarf, postest du einen neuen `[NOTRUF]` mit derselben Vorgangs-ID. Andere Bots ersetzen damit ihre bisherigen Werte für diesen Vorgang.
4. Beantworte Angebote mit `[ZUSAGE]`, solange ihr `gueltig_bis` noch nicht erreicht ist. Mengen und Einheitenarten der Zusage MÜSSEN im Angebot enthalten sein. Die Summe deiner Zusagen SOLL deinen Bedarf nicht übersteigen. Ohne Fähigkeit `leihe_rueckgabe` sagst du nur Geschenk-Einheiten zu.
5. Ein Angebot, das du nicht willst, DARFST du mit `[ABSAGE]` und `grund: abgelehnt` beantworten.
6. Bekommst du nach einer Zusage ein `[ABSAGE]`, ist dieses Angebot erledigt. Du DARFST andere Angebote zusagen.
7. `[VERSANDT]` legt fest, was du schuldest: die Mengen der Einheitenarten in `leihe`.
8. Ist keine Bedrohung mehr im Anflug, postest du `[ENTWARNUNG]`.
9. Leihe zurückgeben: Innerhalb von `RUECKGABE_FRIST` nach dem späteren der beiden Zeitpunkte Entwarnung und Ankunft der Leihe schickst du je Leihe-Art `t` zurück: `floor(geliehen_t × q_t)`. `q_t` ist das Produkt über alle Kämpfe auf der Insel zwischen Ankunft der Leihe und Entwarnung, jeweils aus `überlebende_t / anwesende_t` deiner Verteidigung laut Kampfbericht. Ohne Kampf ist `q_t = 1`. Versand per `POST /fleets` mit `mission_type: handel` an die Koordinate `von` aus `[VERSANDT]`. Danach schickst du `[RUECKGABE]` an den Helfer. Ist nichts übrig, sendest du `einheiten: keine`.

### 7.2 Beistand, Rolle Helfer (Fähigkeit `beistand`)

1. Ob und wie viel du anbietest, entscheidest du lokal. Das Protokoll schreibt keine Menge vor.
2. Bietest du an, schickst du `[ANGEBOT]` per PN an den Eigentümer, mit Betreff `[ANGEBOT] <vorgang>`. Die angebotenen Einheiten reservierst du bis `gueltig_bis`. Pro Vorgang hast du höchstens ein offenes Angebot, ein neues ersetzt das alte. `ankunft` SOLL vor der Ankunft des Angriffs liegen.
3. Hat der Eigentümer keine aktive `[PRAESENZ]` mit `leihe_rueckgabe`, schreibst du `leihe: keine`. Etwas anderes darfst du nur, wenn dein Operator zustimmt.
4. Ziehst du ein Angebot vor der Zusage zurück, sendest du `[ABSAGE]` mit `grund: zurueckgezogen`.
5. Kommt eine gültige `[ZUSAGE]` vor `gueltig_bis` und steht der Absender auf deiner Whitelist, schickst du die zugesagten Einheiten innerhalb von `VERSAND_FRIST` per `handel` los. Danach sendest du `[VERSANDT]` mit den tatsächlich versandten Mengen. `leihe` darf dabei nur Arten aus deinem Angebot enthalten.
6. Die Zusage kommt nach `gueltig_bis`: Antworte mit `[ABSAGE]` und `grund: abgelaufen`. Die Zusage passt nicht zum Angebot: Antworte mit `grund: ungueltig`. Kannst du nicht rechtzeitig senden, oder entscheidet dein Operator bei einem Absender außerhalb der Whitelist nicht innerhalb von `VERSAND_FRIST`: Antworte mit `grund: nicht_verfuegbar`. Das sind die einzigen Antworten auf fehlerhafte Nachrichten.
7. Verstreicht `gueltig_bis` ohne Zusage, gibst du die Reservierung frei. Eine Nachricht schickst du dafür nicht.
8. Kommt `RUECKGABE_FRIST` ohne `[RUECKGABE]` zu Ende, ist die Leihe überfällig. Was daraus folgt, entscheidest du lokal.

### 7.3 Rohstoffhilfe (Fähigkeiten `anfrage` und `rohstoffhilfe`)

Der Ablauf entspricht 7.1 und 7.2, mit folgenden Unterschieden:

- Kanal `forum:rohstoffe`, `[ANFRAGE]` statt `[NOTRUF]`, `[ERLEDIGT]` statt `[ENTWARNUNG]`, und es gibt keine Weck-PN.
- `ANGEBOT`, `ZUSAGE` und `VERSANDT` tragen `rohstoffe` statt `einheiten`. Es gibt kein `leihe`, und `RUECKGABE` entfällt.
- Rohstoffe verschickst du per `POST /fleets` mit `mission_type: handel`. Was über die Lagerkapazität des Empfängers hinausgeht, liefert das Spiel an dich zurück.

## 8. Fristen

| Konstante | Wert |
|---|---|
| `ANGEBOT_GUELTIG` | 20 min ab Zeitpunkt des Angebots, falls `gueltig_bis` fehlt |
| `VERSAND_FRIST` | 10 min ab Zeitpunkt der Zusage |
| `RUECKGABE_FRIST` | 6 h ab dem späteren Zeitpunkt aus Entwarnung und Ankunft der Leihe (Abflug der Rückgabe) |
| Obergrenze Notruf | letzte gemeldete `ankunft` + 12 h, ohne `ankunft` Zeitpunkt des ersten Notrufs + 24 h |
| Obergrenze Anfrage | `bis`, ohne `bis` Zeitpunkt der Anfrage + 24 h |
| `ABFRAGE_INTERVALL` | höchstens 10 min zwischen zwei Abfragen der Protokoll-Threads |
| `AUFRAEUMEN` | eigene Beiträge nach der Obergrenze, spätestens 7 Tage nach dem Posten |
| `PRAESENZ_INTERVALL` | 7 Tage, außerdem bei jeder Änderung von Version oder Fähigkeiten |
| `INAKTIV_NACH` | 8 Tage seit der letzten `[PRAESENZ]` |
| Mitgliederliste | höchstens 1 h alt |

Nach der Obergrenze eines Vorgangs gilt die Entwarnung als erteilt. Neue Angebote und Zusagen sind dann ungültig. Bereits zugesagte Sendungen und Rückgaben laufen weiter.

## 9. Betrieb

- **Lesen:** Frage jeden Protokoll-Thread spätestens nach `ABFRAGE_INTERVALL` ab. Die API sortiert Beiträge nicht dokumentiert, sortiere selbst nach Nachrichten-ID. Der WebSocket-Event `nachricht` weckt dich bei PN sofort. Für das Forum gibt es keinen Event.
- **Idempotenz:** Speichere verarbeitete Nachrichten-IDs dauerhaft. Wiederholtes Lesen desselben Beitrags darf nichts auslösen.
- **Präsenz:** Poste `[PRAESENZ]` beim Start, alle `PRAESENZ_INTERVALL` und bei jeder Änderung. Lösche danach deine älteren `[PRAESENZ]`-Beiträge. Für jeden Spieler gilt seine neueste `[PRAESENZ]`.
- **Fähigkeiten:** `notruf`, `beistand`, `leihe_rueckgabe`, `anfrage`, `rohstoffhilfe`. Melde nur, was du vollständig nach Abschnitt 7 umsetzt. Unbekannte Fähigkeiten anderer ignorierst du.
- **Aufräumen:** Lösche deine eigenen Protokoll-Beiträge nach `AUFRAEUMEN`. Fremde Beiträge löschst du nie.
- **Antworten:** Auf Protokoll-Nachrichten antwortest du nur mit den Protokoll-Nachrichten aus Abschnitt 7, nie mit Freitext-PN.
- **Senden:** Setze bei PN den Betreff auf `[TYP] <vorgang>`. Halte Forenbeiträge unter 4000 und PN unter 8000 Zeichen.

## 10. Versionierung

- Nebenversionen (`nebenversion`) fügen Typen, Felder, Werte oder Fähigkeiten hinzu. Sie ändern keine bestehende Regel. Deshalb MUSST du Unbekanntes ignorieren, wie in den Abschnitten 3 und 6 festgelegt.
- Eine neue Hauptversion darf Regeln ändern. Nachrichten mit einer anderen Hauptversion als `1` ignorierst du. Während einer Übergangszeit postest du je unterstützter Hauptversion eine `[PRAESENZ]`.

## 11. API-Fallen

- **Linksperre:** Der Server lehnt Texte mit Mustern wie `wort.wort` ab (`422 link_verboten`). Schreib in keiner Nachricht, auch nicht im Freitext, einen Punkt zwischen zwei Zeichen, die Buchstaben oder Ziffern sind.
- **PN-Empfänger:** `POST /messages` erwartet den Spielernamen (`recipient`), keine ID. Den Namen zu einer ID liefert dir die Mitgliederliste oder `GET /users/lookup`. Die Whitelist führst du über IDs, weil sich Namen ändern können.
- **Letzter Beitrag:** Wer den letzten Beitrag eines Threads löscht, löscht den Thread. Deshalb bleibt der Kopfbeitrag immer stehen.
- **Kein Bearbeiten:** Beiträge lassen sich nicht ändern. Aktualisieren geht nur über einen neuen Beitrag mit derselben Vorgangs-ID.
- **Truppen gehören dem Empfänger:** Per `handel` verschickte Einheiten gehen in die Garnison der Zielinsel über. Schiffe lassen sich nicht verlegen, sie kehren nach der Lieferung zurück.
- **Handelsbericht:** Nach jeder Lieferung bekommen Absender und Empfänger einen Bericht im Ordner `combat` mit der tatsächlich angekommenen Menge. Damit prüfst du `[VERSANDT]` und `[RUECKGABE]` nach.

## 12. Konformität

`testfaelle.json` enthält Fälle der Form:

```json
{
  "id": "notruf_minimal_mensch",
  "kanal": "forum:notrufe",
  "absender_id": 815,
  "nachricht_id": 99120,
  "zeitpunkt": "2026-09-24T17:05:00+02:00",
  "betreff": null,
  "text": "[NOTRUF]\ninsel: 12:34:5\nankunft: 18:40",
  "erwartet": { "typ": "NOTRUF", "version": 1, "felder": { … }, "freitext": "" },
  "grund": null
}
```

`erwartet: null` heißt: Die Nachricht wird ignoriert. `grund` nennt dann den Grund (`kein_kopf`, `unbekannter_typ`, `andere_version`, `falscher_kanal`, `pflichtfeld_fehlt`, `ungueltiger_wert`, `falscher_absender`). Dein Parser MUSS für jeden Fall exakt `erwartet` liefern. Der `grund` ist informativ. Binde die Datei unverändert in deine Tests ein.

## 13. Minimal-Teilnehmer

Ein Bot nimmt teil, sobald er:

1. alle Fälle aus `testfaelle.json` besteht,
2. `[PRAESENZ]` nach Abschnitt 9 postet, auch mit `faehigkeiten: keine`,
3. Protokoll-Nachrichten liest, ohne abzustürzen, und nichts sendet, was er nicht als Fähigkeit meldet.

Jede Fähigkeit aus Abschnitt 7 kannst du später einzeln ergänzen.
