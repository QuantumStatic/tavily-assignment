from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.config import TTL


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteCache:
    """Per-(vendor_key, section_type) cache. TTL is applied on read from config.TTL."""

    def __init__(self, path: str | Path, clock: Callable[[], datetime] = _utcnow):
        self._clock = clock
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS report_cache (
                 vendor_key TEXT NOT NULL,
                 section_type TEXT NOT NULL,
                 content TEXT NOT NULL,
                 fetched_at TEXT NOT NULL,
                 PRIMARY KEY (vendor_key, section_type)
               )"""
        )
        self._conn.commit()

    def put(self, vendor_key: str, section: Dimension, content: dict[str, Any]) -> None:
        self._conn.execute(
            "REPLACE INTO report_cache VALUES (?,?,?,?)",
            (vendor_key, section.value, json.dumps(content), self._clock().isoformat()),
        )
        self._conn.commit()

    def _row(self, vendor_key: str, section: Dimension) -> tuple[str, datetime] | None:
        cur = self._conn.execute(
            "SELECT content, fetched_at FROM report_cache WHERE vendor_key=? AND section_type=?",
            (vendor_key, section.value),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return row[0], datetime.fromisoformat(row[1])

    def get(self, vendor_key: str, section: Dimension) -> dict[str, Any] | None:
        row = self._row(vendor_key, section)
        if row is None:
            return None
        content, fetched_at = row
        if self._clock() - fetched_at >= TTL[section]:
            return None  # stale
        return json.loads(content)

    def fetched_at(self, vendor_key: str, section: Dimension) -> datetime | None:
        row = self._row(vendor_key, section)
        return row[1] if row else None
