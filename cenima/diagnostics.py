"""Bounded, redacted application diagnostics."""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

from .config import CONFIG_DIR


LOG_NAME = 'cenima.log'
MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3

_FILTER = re.compile(
    r'(https?://)[^\s"\'<>]+',
    re.IGNORECASE,
)
_SECRET = re.compile(
    r'(?i)\b(token|access_token|cookie|authorization|api[_-]?key|secret|password)\b\s*[:=]\s*'
    r'([^\s,;]+)'
)
_HOME = re.compile(re.escape(str(Path.home())), re.IGNORECASE)
_WINDOWS_USERS = re.compile(r'(?i)([A-Z]:\\Users\\)[^\\\s]+', re.IGNORECASE)


def redact_diagnostic(value):
    text = str(value)
    text = _SECRET.sub(r'\1=<redacted>', text)

    def redact_url(match):
        url = match.group(0)
        try:
            parts = urlsplit(url)
            return f'{parts.scheme}://{parts.hostname or "<host>"}/<redacted>'
        except ValueError:
            return '<redacted-url>'

    text = _FILTER.sub(redact_url, text)
    text = _HOME.sub('<home>', text)
    text = _WINDOWS_USERS.sub(r'\1<user>', text)
    return ''.join(char for char in text if char.isprintable() or char in '\n\t')


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        safe_record = logging.LogRecord(
            record.name, record.levelno, record.pathname, record.lineno,
            redact_diagnostic(record.getMessage()), record.args if False else None,
            record.exc_info,
        )
        if record.exc_info:
            safe_record.exc_text = redact_diagnostic(logging.Formatter().formatException(record.exc_info))
        return super().format(safe_record)


def log_path():
    return CONFIG_DIR / 'logs' / LOG_NAME


def get_logger(name='cenima'):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=MAX_LOG_BYTES,
                                  backupCount=LOG_BACKUPS, encoding='utf-8')
    handler.setFormatter(RedactingFormatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(category, message, level='info', **details):
    logger = get_logger('cenima.' + category)
    extra = ' '.join(f'{key}={redact_diagnostic(value)}' for key, value in details.items())
    body = f'{message} {extra}'.strip()
    getattr(logger, level if level in ('debug', 'info', 'warning', 'error', 'critical') else 'info')(body)
