"""Dauerhafter Zustand des Allianz-Bots in data/state.json.

Das Protokoll verlangt, dass verarbeitete Nachrichten-IDs, die laufende
Vorgangsnummer und die eigenen Beitraege einen Neustart ueberleben
(agenten.md, Abschnitt 9). Geschrieben wird atomar ueber eine Temp-Datei,
damit ein Absturz mitten im Schreiben nie eine halbe Datei hinterlaesst.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

import config

LEER = {
    # "f:<beitrag_id>" / "p:<pn_id>" -> Server-Zeitpunkt (ISO)
    "verarbeitet": {},
    # Beitrags-ID -> {kanal, typ, vorgang, gepostet}. NUR diese darf der Bot loeschen.
    "eigene_beitraege": {},
    "vorgang_nr": 0,
    # Eigene Vorgaenge (Notruf/Anfrage) -> Obergrenze, fuers Aufraeumen.
    "eigene_vorgaenge": {},
    "notrufe": {},           # aktive eigene Notrufe je Insel-ID (Rolle Verteidiger)
    "notrufe_alt": [],       # abgeschlossene eigene Notrufe (fuer spaete VERSANDT/ABSAGE)
    "schulden": [],          # Leihen, die wir zurueckgeben muessen
    "hilfe": {},             # fremde Notrufe (Rolle Helfer), Schluessel = Vorgangs-ID
    "verliehen": [],         # Leihen, die wir verschickt haben
    "anfragen": {},          # aktive eigene Rohstoff-Anfragen je Insel-ID
    "anfragen_alt": [],
    "berichte": {},          # battle_id -> ausgewerteter Verteidigungsbericht
    "gemeldet": {},          # Schluessel einmaliger Telegram-Meldungen -> Zeitpunkt
    "eile_bis": None,        # bis wann auf zurueckgerufene Schiffe gewartet wird
    "kasse": None,           # Ueberlauf in die Allianzkasse: Summen, letzte Einzahlungen
}


class State:
    def __init__(self, path: str = config.STATE_PATH):
        self.path = path
        self.data = json.loads(json.dumps(LEER))
        self._zuletzt = None  # zuletzt geschriebener Inhalt - spart Schreibzugriffe auf die SD-Karte
        # Pfad der beiseitegelegten Datei, falls state.json unlesbar war (Bot meldet das).
        self.defekt: str | None = None
        gespeichert = None
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    gespeichert = json.load(f)
                if not isinstance(gespeichert, dict):
                    raise ValueError("kein JSON-Objekt")
            except (ValueError, OSError):
                # Nicht abstuerzen und nicht ueberschreiben: die kaputte Datei bleibt
                # zur Rettung liegen (Vorgangsnummern!), der Bot startet mit leerem Stand.
                self.defekt = f"{path}.defekt-{time.strftime('%Y%m%d-%H%M%S')}"
                os.replace(path, self.defekt)
                gespeichert = None
        if gespeichert is not None:
            self.data.update({k: v for k, v in gespeichert.items() if k in LEER})
            # Aus der Ein-Insel-Zeit: einzelner Notruf/einzelne Anfrage. Die Insel
            # traegt Kontext.start() nach (siehe Kontext.altbestand_zuordnen).
            if gespeichert.get("notruf"):
                self.data["notrufe"]["?"] = gespeichert["notruf"]
            if gespeichert.get("anfrage"):
                self.data["anfragen"]["?"] = gespeichert["anfrage"]

    def speichern(self) -> None:
        inhalt = json.dumps(self.data, ensure_ascii=False, indent=1)
        if inhalt == self._zuletzt:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(inhalt)
            os.replace(tmp, self.path)
            self._zuletzt = inhalt
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def naechste_vorgangs_nr(self) -> int:
        self.data["vorgang_nr"] += 1
        self.speichern()  # sofort: die Nummer muss je Spieler eindeutig bleiben
        return self.data["vorgang_nr"]
