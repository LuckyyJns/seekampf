"""Logger des Bots: Tages-Logdatei logs/<LOG_FILE_PREFIX>-YYYY-MM-DD.log, ins
Journal nur Warnungen und Fehler (seekampf_gemeinsam.logs)."""
import logging

import config
from seekampf_gemeinsam.logs import logger_einrichten


def get_logger() -> logging.Logger:
    return logger_einrichten("seekampf_bot", config.LOG_DIR, config.LOG_FILE_PREFIX, config.TIMEZONE_NAME,
                             config.LOG_RETENTION_DAYS, config.LOG_TO_STDOUT)
