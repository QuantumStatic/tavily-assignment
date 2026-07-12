from __future__ import annotations

import sqlite3
from pathlib import Path

from vendor_dd.logs import get_logger

_LOG = get_logger("db")


class SqliteConn:
    """Shared SQLite setup + logged execution for the app's small stores (report cache,
    score history, project store). WAL plus a 5s busy timeout let them share one file
    across threads — each opens with check_same_thread=False and serializes its own
    writes. Subclasses call super().__init__(path), then create their tables via _exec."""

    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def _exec(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        _LOG.info("db.query", extra={"payload": {
            "sql": " ".join(sql.split()), "params": list(params),
            "rowcount": cur.rowcount, "lastrowid": cur.lastrowid,
        }})
        return cur

    def close(self) -> None:
        self._conn.close()
