#!/usr/bin/env python3
"""Rohstoff-Anfrage von Hand stellen - der laufende Allianz-Bot postet sie.

    .venv/bin/python anfrage_stellen.py holz=3000
    .venv/bin/python anfrage_stellen.py stein=2000 gold=500
    .venv/bin/python anfrage_stellen.py holz=500 insel=46:55:6   # fuer eine bestimmte Insel

Ohne insel= gilt die aktuelle Insel im Spiel.

Der Befehl landet in data/befehle/ und wird vom Bot beim naechsten Takt
(<= 15 s) ausgefuehrt. Angebote darauf sagt der Bot automatisch zu, hoechstens
bis zur angefragten Menge; [ERLEDIGT] folgt, sobald alles verschickt ist,
sonst endet die Anfrage nach 24 h.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import protokoll  # noqa: E402

ORDNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "befehle")


def main() -> int:
    insel = None
    teile = []
    for arg in sys.argv[1:]:
        if arg.startswith("insel="):
            insel = arg.split("=", 1)[1]
        else:
            teile.append(arg)
    try:
        rohstoffe = protokoll._rohstoffe(" ".join(teile))
        if insel is not None:
            insel = protokoll._koordinate(insel)
    except protokoll.Ungueltig:
        print(__doc__, file=sys.stderr)
        return 2
    os.makedirs(ORDNER, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ORDNER, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"typ": "anfrage", "rohstoffe": rohstoffe, "insel": insel}, f)
    os.replace(tmp, os.path.join(ORDNER, f"{time.time_ns()}.json"))
    print(f"Anfrage eingereiht: {rohstoffe}{f' fuer {insel}' if insel else ''} - der Bot postet sie innerhalb von 15 s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
