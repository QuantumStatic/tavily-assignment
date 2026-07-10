from __future__ import annotations

import contextvars
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from vendor_dd.logs.formatter import JsonLineFormatter

# The five channels. Each maps to logger name `vendor_dd.<name>` and file `<name>.log`.
_CHANNELS = ("general", "llm", "tavily", "http", "db")

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vendor_dd_correlation_id", default=None,
)


def set_correlation_id(value: str) -> contextvars.Token:
    return correlation_id_var.set(value)


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True


def get_logger(channel: str) -> logging.Logger:
    """Return the logger for a channel ('general'|'llm'|'tavily'|'http'|'db')."""
    return logging.getLogger(f"vendor_dd.{channel}")


_configured = False


def configure_logging(log_dir: str | Path | None = None, level: str | None = None) -> None:
    """Idempotent: attach a rotating JSON-line file handler per channel. Safe to call
    more than once (handlers are replaced, not duplicated)."""
    global _configured
    directory = Path(log_dir or os.getenv("VENDOR_DD_LOG_DIR", "logs"))
    directory.mkdir(parents=True, exist_ok=True)
    lvl = (level or os.getenv("VENDOR_DD_LOG_LEVEL", "INFO")).upper()

    fmt = JsonLineFormatter()
    corr = _CorrelationFilter()
    for channel in _CHANNELS:
        logger = get_logger(channel)
        logger.setLevel(lvl)
        logger.propagate = False
        for h in list(logger.handlers):
            logger.removeHandler(h)
        handler = RotatingFileHandler(
            directory / f"{channel}.log", maxBytes=10 * 1024 * 1024, backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(fmt)
        handler.addFilter(corr)
        logger.addHandler(handler)
    _configured = True
