"""Feste Spielwerte, die mehrere Bots brauchen (aus
https://seekampf.de/legal/regeln, Referenztabellen, bzw. an echten Flotten
nachgemessen). Die Bots lesen sie ueber ihre config.py (config.SCHIFF_KNOTEN ...)."""

# Ein Sektor ist ein 5x5-Raster von Inselplaetzen (z = 1..25).
SEKTOR_KANTE = 5

# Fahrzeit = Distanz in Feldern / Knoten * 10 Minuten, aber nie unter dieser
# Untergrenze - 2 Minuten innerhalb desselben Sektors, sonst 5.
MIN_FAHRZEIT_S = 300
MIN_FAHRZEIT_SELBER_SEKTOR_S = 120

# Knoten je Schiffstyp (Felder je 10 Minuten). Eine Flotte faehrt so schnell
# wie ihr langsamstes Schiff.
SCHIFF_KNOTEN = {
    "spaehschiff": 20, "kleines_kriegsschiff": 12, "grosses_kriegsschiff": 8,
    "kleines_handelsschiff": 10, "grosses_handelsschiff": 7, "kolonisationsschiff": 3,
}

# Rohstoff-Kapazitaet je Schiff - begrenzt, wie viel Beute mitkommt.
SCHIFF_LADEVOLUMEN = {
    "kleines_handelsschiff": 75, "grosses_handelsschiff": 460,
}

# Plaetze fuer Truppen je Kriegsschiff.
KRIEGSSCHIFF_PLATZ = {"kleines_kriegsschiff": 8, "grosses_kriegsschiff": 30}
