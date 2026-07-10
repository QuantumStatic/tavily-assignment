from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

_SECRET_ENV_VARS = ("NEBIUS_API_KEY", "TAVILY_API_KEY", "OPENAI_API_KEY")
_MASK = "***"


def _secret_values() -> list[str]:
    return [v for name in _SECRET_ENV_VARS if (v := os.getenv(name))]


def redact(value: Any) -> Any:
    """Recursively replace any known-secret env value with '***' (defense-in-depth;
    we already avoid logging auth, this catches accidental inclusion)."""
    secrets = _secret_values()
    if not secrets:
        return value
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, _MASK)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, correlation_id, event, payload."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "event": record.getMessage(),
            "payload": redact(getattr(record, "payload", {})),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)
