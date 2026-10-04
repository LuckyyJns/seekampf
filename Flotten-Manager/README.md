# Flotten-Manager

Schickt vollautomatisch Raid-Flotten auf die freien Inseln rund um jede
eingeschaltete eigene Insel. Bedient wird er ueber den Seekampf-Hub
(`~/Seekampf/Seekampf-Hub`, http://<ip-des-pi>:8080); der Manager selbst stellt
nur eine JSON-Schnittstelle auf `127.0.0.1:8081` bereit.

## Mehrere Inseln

Jede eigene Insel hat einen An/Aus-Schalter, eine eigene Zielliste mit
Rotation, eine eigene Statistik und einen eigenen Beute-Verlauf je Tag. Aus
heisst: keine neuen Flotten; was unterwegs ist, faehrt zu Ende. Neue Inseln
starten ausgeschaltet. Zwei eigene Inseln greifen nie gleichzeitig dasselbe
Ziel an, und ein Ziel mit neuem Besitzer fliegt aus allen Ziellisten.
Bedrohung und "Lager voll" gelten je Insel; beim Rueckruf werden nur die
Flotten der bedrohten Insel geholt.

**Einstellungen je Insel:** Flottenaufbau (Kriegs-/Handelsschiffe, grosse
Handelsschiffe, Steinewerfer, Einheitentyp), `max_flotten`, Radius und
Scan-Intervall, Beute-Priorisierung, Razzia-Ziel, Lager-voll-Schwelle,
Rueckruf bei Bedrohung und Blacklist lassen sich je Insel abweichend setzen
(im Hub in der Inselkarte unter "Einstellungen dieser Insel"; leer = globaler
Wert, `config.INSEL_EINSTELLUNGEN`, `POST /api/inseln/{id}/einstellungen`).
Aendert sich der Radius, wird beim naechsten Takt neu gescannt. Global bleiben
Takt, Pruefintervalle unterwegs, Postfach, Telegram und der Rohstoff-Ausgleich.

Kriegsschiffe, die der Ausbildungs-Bot fuer einen Truppentransport braucht
(`Ausbildungs-Bot/data/reserve.json`), fahren nicht zum Raiden raus.
Kolonisationsfahrten (Kolonisations-Bot) uebernimmt der Manager nicht.

Beim Umstieg (erster Start dieser Version) bekam die Heimatinsel die
bisherige Zielliste und Statistik; der Beute-Verlauf wurde aus den Logs
nachgetragen.

## Rohstoff-Ausgleich

Im Seekampf-Hub unter **Rohstoff-Ausgleich**. Jede Insel hat zwei Schalter,
*gibt ab* und *bekommt*; beides zugleich ist erlaubt, so gleichen sich alle
Inseln gegenseitig aus. Je Insel einstellbar (leer = Standard):

| Feld | Bedeutung | Standard |
| --- | --- | --- |
| Auffuellen bis | jeden Rohstoff bis zu diesem Anteil der Lagerkapazitaet auffuellen | `ausgleich_ziel` (50 %) |
| Behaelt min. | beim Abgeben bleibt mindestens so viel | `ausgleich_reserve` (25 %) |
| Max. je Lieferung | hoechstens so viele Rohstoffe je Fahrt | unbegrenzt |

Braucht der Upgrade-Bot fuer den naechsten Ausbau mehr als das Ziel, wird bis
zu dessen Kosten aufgefuellt (liest `Upgrade-Bot/data/status.json`). Eine
Insel, die selbst auch bekommt, gibt nur ab, was ueber ihrem eigenen
Auffuellziel liegt, und nie, was ihr fuer den naechsten Ausbau fehlt - ein
Hin- und Herschicken ist damit ausgeschlossen. Was der Kolonisations-Bot auf
einer Insel fuer ein Schiff anspart (`Kolonisations-Bot/data/reserve.json`),
gibt sie ebenfalls nicht ab. Je Empfaenger liefert die Insel,
die am meisten liefern kann (bei Gleichstand die naechste), per Handelsfahrt
(`mission_type: handel`) mit ihren eigenen Handelsschiffen, ab
`ausgleich_min_menge` Rohstoffen je Fahrt. Was schon unterwegs ist, zaehlt mit.

Reichen die Handelsschiffe im Hafen nicht, haelt der Manager sie fuer die
Lieferung zurueck (die Insel raidet so lange ohne Handelsschiffe), hoechstens
30 Minuten - dann faehrt, was da ist. Inseln ohne Handelsschiffe geben nichts
ab. Bedrohte Inseln liefern nicht und bekommen nichts. Neue Inseln uebernehmen
die Einstellung der bis dahin neuesten Insel. Hauptschalter: `ausgleich_aktiv`.
Code: `ausgleich.py`; Schnittstelle `GET /api/ausgleich`,
`POST /api/inseln/{id}/ausgleich`.

## Was er tut

1. **Scannen** – sucht im eingestellten Radius (in Sektoren) alle freien Inseln,
   also solche ohne Besitzer: frisch angelegte unbewohnte Inseln und Ruinen.
   Inseln mit Besitzernamen bleiben aussen vor, auch die "Unbemannt NNN", die
   trotz ihres Namens dem Spieler *Niemend* gehoeren.
2. **Losschicken** – so viele Flotten gleichzeitig, wie Schiffe und Steinewerfer
   hergeben (Standard je Flotte: 1 Kriegsschiff, 1 Handelsschiff,
   1 Steinewerfer). Welcher Schiffstyp mitfährt, ist egal – der Manager nimmt,
   was im Hafen liegt, große Handelsschiffe zuerst (mehr Beute je Fahrt),
   Kriegsschiffe nach Geschwindigkeit. Spähschiff und Kolonisationsschiff
   bleiben daheim. Die Ziele kommen stur reihum aus der
   Rotation.
3. **Gegenpruefen** – unmittelbar vor der Abfahrt wird ueber
   `GET /map/island/{x}/{y}/{z}` noch einmal geprueft, ob die Insel wirklich
   frei ist. Hat sie inzwischen einen Besitzer, fliegt sie aus der Rotation und
   die Flotte nimmt das naechste Ziel. **Auch unterwegs:** Solange die Flotte
   noch zurueckgerufen werden kann (`recallable_until`, haengt am Wachturm),
   wird das Ziel alle `pruef_intervall_s` (60 s) geprueft und
   `pruef_vorlauf_s` (30 s) vor Ende der Rueckrufmoeglichkeit ein letztes Mal.
   Ist es besiedelt, wird die Flotte zurueckgerufen. Wird die Insel erst danach
   besiedelt, erkennt der Manager das am Namen im Kampfbericht, laesst den
   Bericht stehen und meldet es per Telegram. Die Zielliste wird stuendlich neu
   gescannt.
   Steht ein Ziel unter Anfaengerschutz (`409 newbie_protection`), ist es fuer
   6 Stunden gesperrt, statt in jeder Runde erneut angefahren zu werden.
4. **Nachhalten** – Ankunft und Rueckkehr jeder Flotte stehen im Log; sobald
   eine Flotte daheim ist, faehrt sie im selben Takt zum naechsten Ziel weiter.
5. **Auswerten** – aus dem Kampfbericht kommen Sieg/Niederlage und die echte
   Beute in die Statistik. Gezaehlt werden nur Raids (`mission: attack`);
   Handelsfahrten (Ausgleich, Allianz-Bot, von Hand) tauchen im Log auf, aber
   nicht in der Statistik.
6. **Aufraeumen** – ist der Bericht verbucht, wird er im Postfach
   ausgeblendet, damit dort nur bleibt, was wirklich Aufmerksamkeit braucht.

## Bedienung

Im Seekampf-Hub: oben die Kennzahlen und das Beute-Diagramm aller Inseln,
darunter je Insel aufklappbar Schiffe im Hafen, fahrende Flotten, Statistik,
Diagramm und Zielrotation; dann Einstellungen und Log.

Alle Einstellungen greifen sofort, ohne Neustart. Die wichtigsten:

| Einstellung | Bedeutung |
| --- | --- |
| `scan_radius_sektoren` | 0 = eigener Sektor, 1 = 3×3 Sektoren, 2 = 5×5 … |
| `scan_intervall_stunden` | wie oft neu nach freien Inseln gesucht wird (24 = taeglich) |
| `flotte_*` | Anzahl je Flotte (Typ egal); mehr Handelsschiffe = mehr Beute (klein 75, groß 460) |
| `max_flotten` | 0 = so viele gleichzeitig, wie Schiffe da sind |
| `rohstoff_modus` | `knappster` pluendert gezielt den Rohstoff, von dem am wenigsten da ist |
| `lager_voll_schwelle` | ab diesem Fuellstand aller drei Rohstoffe wird pausiert |
| `niederlagen_bis_blacklist` / `blacklist_tage` | wann ein Ziel gesperrt wird und wie lange |
| `rueckruf_bei_bedrohung` | holt fahrende Raid-Flotten zurueck, wenn ein Angriff im Anflug ist (Handelsfahrten nie) |
| `berichte_archivieren` | blendet die Kampfberichte der eigenen Raids aus dem Postfach aus |

### Postfach aufraeumen

Bei zwoelf Raids am Tag ist der Kampfbericht-Ordner nach einer Woche
unbrauchbar. Steht `berichte_archivieren` an (Standard), blendet der Manager
jeden **gewonnenen** Bericht aus, **den er selbst ausgeloest hat** – aber erst,
nachdem die Beute verbucht ist. Faellt das Ausblenden aus, bleibt der Bericht stehen; die
Statistik stimmt trotzdem.

Unberuehrt bleiben:

* Niederlagen,
* Angriffe auf Inseln, die unterwegs besiedelt wurden (Name im Bericht weicht
  ab und die Insel hat live einen Besitzer),
* Berichte zu von Hand gestarteten Angriffen,
* Berichte zu Flotten, die der Manager nicht zuordnen kann – das passiert nur,
  wenn `data/state.json` verloren geht, waehrend eine Flotte faehrt; ein
  normaler Neustart genuegt nicht, die Datei merkt sich die Herkunft,
* eingehende Angriffe auf eigene Inseln (`rolle != angreifer`),
* Spionageberichte und alles ausserhalb des Kampfordners.

Technisch ist das `POST /messages/{id}/archive`. Die Nachricht verschwindet aus
`GET /messages?folder=combat`, bleibt aber einzeln unter `GET /messages/{id}`
lesbar, und die oeffentliche Inselseite zeigt die letzten zehn Berichte zu einer
Insel ohnehin weiter an. **Einen unarchive-Endpunkt gibt es nicht** – ueber die
API ist das Ausblenden nicht umkehrbar.

### Beute-Priorisierung

`rohstoff_modus = knappster` waehlt den Rohstoff mit dem kleinsten **erwarteten**
Bestand – was die schon fahrenden Flotten mitbringen, ist eingerechnet. Sind
vier Flotten mit Stein unterwegs, hebt das Stein rechnerisch so weit an, dass
die fuenfte von selbst etwas anderes holt. Liegen alle drei Bestaende naeher als
`ausgleich_schwelle` (Anteil der Lagerkapazitaet) beieinander, wird gleichmaessig
gepluendert – dann kommt die Ladung wenigstens voll zurueck.

## Warum nur ein Steinewerfer reicht – und wann nicht

Eine freie Insel ohne jede Restgarnison verliert automatisch, sobald der
Angreifer mindestens eine Einheit landet. Steht dort aber auch nur **eine**
Einheit, zaehlt zusaetzlich die Basis-Inselverteidigung von 50 – dagegen
verliert ein einzelner Steinewerfer (Angriff 5) sicher und geht verloren. Frisch
angelegte freie Inseln sind leer, Ruinen verlassener Spieler koennen eine
Restgarnison haben. Deshalb die Blacklist: nach `niederlagen_bis_blacklist`
Niederlagen in Folge ist das Ziel fuer `blacklist_tage` gesperrt.

## Einrichtung

```bash
cd ~/Seekampf/Flotten-Manager
cp .env.example .env          # SEEKAMPF_API_KEY eintragen
uv sync                       # .venv anlegen
.venv/bin/python web.py       # von Hand starten
```

Als Dauerdienst:

```bash
sudo cp seekampf-flotten-manager.service seekampf-flotten-manager-alert.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seekampf-flotten-manager
tail -f logs/flotte-$(date +%F).log     # ins Journal gehen nur Warnungen/Fehler
```

Ist `data/state.json` beim Start unlesbar, wird sie als
`state.json.defekt-<Zeit>` beiseitegelegt, der Manager startet **pausiert** und
meldet sich per Telegram - statt die Statistik still zu ueberschreiben. Dann den
Stand aus `~/Seekampf-Sicherungen` zurueckspielen oder im Hub neu starten.

Der Dienst startet nach einem Neustart von selbst und raidet sofort weiter;
fahrende Flotten werden aus `data/state.json` wieder uebernommen.

### Ausfall-Alarm

Gibt systemd den Dienst auf (fuenf Fehlstarts in zehn Minuten), zieht
`OnFailure=` die Unit `seekampf-flotten-manager-alert.service` hoch, die per
Telegram meldet, dass keine Flotte mehr faehrt - samt der letzten Logzeilen.
Das ist noetig, weil der Manager in genau diesem Moment selbst nichts mehr
senden kann. Von Hand pruefen:

```bash
sudo systemctl start seekampf-flotten-manager-alert
```

Der Flotten-Manager benutzt einen **eigenen API-Schluessel**, nicht den des
Upgrade-Bots - so laesst sich einer von beiden zurueckziehen, ohne den
anderen lahmzulegen.

## Dateien

| Datei | Aufgabe |
| --- | --- |
| `web.py` | Einstiegspunkt: uvicorn + Bot-Thread, JSON-Schnittstelle fuer den Seekampf-Hub |
| `manager.py` | der Takt: Flotten abgleichen, Berichte auswerten, losschicken |
| `scanner.py` | Bereich abklappern, Zielliste pflegen |
| `ausgleich.py` | Rohstoff-Ausgleich zwischen den eigenen Inseln |
| `geo.py` | Feldkoordinaten, Entfernung, Fahrzeit |
| `state.py` | `data/state.json`: Einstellungen, Inseln (Ziele, Statistik), Flotten, Beute-Verlauf |
| `api_client.py` | die genutzten Seekampf-Endpunkte |
| `report.py` / `notify.py` | Telegram-Morgenreport |
| `alert.py` | Telegram-Alarm, den systemd bei Ausfall des Dienstes startet |
| `logs/flotte-JJJJ-MM-TT.log` | eine Logdatei pro Tag, 60 Tage Aufbewahrung |
