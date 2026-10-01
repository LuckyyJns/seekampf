"""Logging-Setup: eine Log-Datei pro Kalendertag.

Wie beim Upgrade-Bot: der Manager schreibt nach
logs/flotte-YYYY-MM-DD.log und wechselt die Datei selbstaendig um Mitternacht
(Zeitzone aus config.TIMEZONE_NAME). Alte Dateien werden nach
config.LOG_RETENTION_DAYS Tagen geloescht. Die Weboberflaeche liest die Datei
des laufenden Tages fuer die Log-Ansicht zurueck.
"""
import glob
import logging
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.TIMEZONE_NAME)


def log_path_for(day: date) -> str:
    return os.path.join(config.LOG_DIR, f"{config.LOG_FILE_PREFIX}-{day.isoformat()}.log")


class DailyFileHandler(logging.FileHandler):
    """Wie FileHandler, wechselt aber beim ersten Eintrag nach Mitternacht
    automatisch in die Datei des neuen Tages."""

    def __init__(self, retention_days: int = 0, encoding: str = "utf-8"):
        self.retention_days = retention_days
        self._current_day = datetime.now(TZ).date()
        super().__init__(log_path_for(self._current_day), encoding=encoding)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = datetime.fromtimestamp(record.created, TZ).date()
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
        self.baseFilename = os.path.abspath(log_path_for(day))
        self.stream = self._open()
        self._cleanup(day)

    def _cleanup(self, today: date) -> None:
        if self.retention_days <= 0:
            return
        cutoff = today - timedelta(days=self.retention_days)
        for path in glob.glob(os.path.join(config.LOG_DIR, f"{config.LOG_FILE_PREFIX}-*.log")):
            stamp = os.path.basename(path)[len(config.LOG_FILE_PREFIX) + 1:-4]
            try:
                if date.fromisoformat(stamp) < cutoff:
                    os.remove(path)
            except (ValueError, OSError):
                continue


def get_logger() -> logging.Logger:
    os.makedirs(config.LOG_DIR, exist_ok=True)

    logger = logging.getLogger("seekampf_flotten_manager")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Zeitstempel immer in der konfigurierten Zeitzone, unabhaengig davon,
    # wie der Rechner/Dienst eingestellt ist - report.py liest sie so zurueck.
    formatter.converter = lambda ts: datetime.fromtimestamp(ts, TZ).timetuple()

    file_handler = DailyFileHandler(retention_days=config.LOG_RETENTION_DAYS)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if config.LOG_TO_STDOUT:
        # Landet bei systemd im journal (journalctl -u seekampf-flotten-manager -f).
        # Ins journal nur Warnungen und Fehler: alles andere steht schon in der
        # Tages-Logdatei, doppelt geschrieben kostete es nur Schreibzugriffe auf
        # die SD-Karte. Abstuerze (stderr) landen weiterhin vollstaendig im journal.
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(logging.WARNING)
        logger.addHandler(stream_handler)

    return logger
