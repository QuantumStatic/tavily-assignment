from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Callable

from vendor_dd.engine.schemas import Dimension
from vendor_dd.logs import get_logger

_LOG = get_logger("db")


def _today() -> date:
    # only used as a default; production passes the pipeline's injected date
    return date.today()


# Valid history dimensions: every scored report Dimension (i.e. all except SNAPSHOT,
# which is entity resolution not a score) PLUS the synthesized 'verdict'. Generated
# from the enum so the CHECK constraint has a single source of truth. VERDICT is
# deliberately NOT a Dimension member — code iterates that enum where verdict must
# not appear (EXPECTED_SECTIONS, _TAVILY_DIMS).
HISTORY_DIMENSIONS: tuple[str, ...] = tuple(
    d.value for d in Dimension if d is not Dimension.SNAPSHOT
) + ("verdict",)

_CHECK_LIST = ", ".join(f"'{d}'" for d in HISTORY_DIMENSIONS)


class ScoreHistory:
    """Append-only institutional memory of every score ever assigned, keyed by resolved
    domain (vendor_key). Survives vendor deletion and cache eviction deliberately.
    One row per (vendor_key, dimension, day); same-day re-runs REPLACE (last write wins)."""

    def __init__(self, path: str | Path, clock: Callable[[], date] = _today):
        self._clock = clock
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            f"""CREATE TABLE IF NOT EXISTS score_history (
                  vendor_key  TEXT NOT NULL,
                  dimension   TEXT NOT NULL CHECK (dimension IN ({_CHECK_LIST})),
                  score       INTEGER NOT NULL CHECK (score BETWEEN 0 AND 10),
                  recorded_on TEXT NOT NULL,
                  PRIMARY KEY (vendor_key, dimension, recorded_on)
                )"""
        )
        self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        _LOG.info("db.query", extra={"payload": {
            "sql": " ".join(sql.split()), "params": list(params),
            "rowcount": cur.rowcount, "lastrowid": cur.lastrowid,
        }})
        return cur

    def record(self, vendor_key: str, dimension: str, score: int) -> None:
        self._exec(
            """INSERT OR REPLACE INTO score_history (vendor_key, dimension, score, recorded_on)
               VALUES (?,?,?,?)""",
            (vendor_key, dimension, score, self._clock().isoformat()),
        )
        self._conn.commit()

    def scores_for(self, vendor_key: str) -> dict[str, tuple[int, str]]:
        """Latest (score, recorded_on) per dimension for this vendor_key."""
        cur = self._exec(
            """SELECT dimension, score, recorded_on FROM score_history
               WHERE vendor_key=? ORDER BY dimension, recorded_on""",
            (vendor_key,),
        )
        out: dict[str, tuple[int, str]] = {}
        for dim, score, recorded_on in cur.fetchall():
            out[dim] = (score, recorded_on)   # ORDER BY recorded_on => last wins = latest
        return out

    def previous(self, vendor_key: str, dimension: str) -> tuple[int, str] | None:
        """The score recorded on the second-most-recent distinct day, or None."""
        cur = self._exec(
            """SELECT score, recorded_on FROM score_history
               WHERE vendor_key=? AND dimension=?
               ORDER BY recorded_on DESC LIMIT 2""",
            (vendor_key, dimension),
        )
        rows = cur.fetchall()
        if len(rows) < 2:
            return None
        score, recorded_on = rows[1]
        return score, recorded_on

    def close(self) -> None:
        self._conn.close()
