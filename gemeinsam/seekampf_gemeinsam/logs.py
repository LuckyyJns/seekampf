"""Logging: eine Logdatei pro Kalendertag, gemeinsam fuer alle Bots.

Jeder Bot schreibt nach logs/<praefix>-YYYY-MM-DD.log und wechselt die Datei
selbstaendig um Mitternacht (in der angegebenen Zeitzone, unabhaengig davon,
wie der Rechner eingestellt ist - der Hub und die Reports lesen die
Zeitstempel so zurueck). Alte Dateien werden nach `aufbewahren_tage` geloescht.

Ins Journal (stdout) gehen nur Warnungen und Fehler: alles andere steht schon
in der Tages-Logdatei, doppelt geschrieben kostete es nur Schreibzugriffe auf
die SD-Karte. Abstuerze (stderr) landen weiterhin vollstaendig im Journal.
"""
from __future__ import annotations

import glob
import logging
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


class DailyFileHandler(logging.FileHandler):
    """Wie FileHandler, wechselt aber beim ersten Eintrag nach Mitternacht
    automatisch in die Datei des neuen Tages."""

    def __init__(self, ordner: str, praefix: str, tz: ZoneInfo,
                 retention_days: int = 0, encoding: str = "utf-8"):
        self.ordner = ordner
        self.praefix = praefix
        self.tz = tz
        self.retention_days = retention_days
        self._current_day = datetime.now(tz).date()
        super().__init__(self.pfad(self._current_day), encoding=encoding)

    def pfad(self, day: date) -> str:
        return os.path.join(self.ordner, f"{self.praefix}-{day.isoformat()}.log")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = datetime.fromtimestamp(record.created, self.tz).date()
            if day != self._current_day:
                self._switch_day(day)
        except Exception:  # Logging darf den Bot niemals stoppen
            self.handleError(record)
        super().emit(record)

    def _switch_day(self, day: date) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        self._current_day = day
        self.baseFilename = os.path.abspath(self.pfad(day))
        self.stream = self._open()
        self._cleanup(day)

    def _cleanup(self, today: date) -> None:
        if self.retention_days <= 0:
            return
        cutoff = today - timedelta(days=self.retention_days)
        for path in glob.glob(os.path.join(self.ordner, f"{self.praefix}-*.log")):
            stamp = os.path.basename(path)[len(self.praefix) + 1:-4]
            try:
                if date.fromisoformat(stamp) < cutoff:
                    os.remove(path)
            except (ValueError, OSError):
                continue


def logger_einrichten(name: str, ordner: str, praefix: str, zeitzone: str,
                      aufbewahren_tage: int = 60, journal: bool = True) -> logging.Logger:
    """Logger `name` mit Tages-Logdatei (und Journal fuer Warnungen/Fehler).
    Ein zweiter Aufruf gibt denselben Logger unveraendert zurueck."""
    os.makedirs(ordner, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    tz = ZoneInfo(zeitzone)
    formatter = logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(message)s",
                                  datefmt="%Y-%m-%d %H:%M:%S")
    formatter.converter = lambda ts: datetime.fromtimestamp(ts, tz).timetuple()

    file_handler = DailyFileHandler(ordner, praefix, tz, retention_days=aufbewahren_tage)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if journal:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(logging.WARNING)
        logger.addHandler(stream_handler)
    return logger
